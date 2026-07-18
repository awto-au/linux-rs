#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Sync rulesdb/patterns.db's work_items table — the single queryable
index of what needs doing across both tracks (hand-translation in
linux-riscv/, the awtoau/c2rust fork) — from real sources of truth:
c2rust_issues (crawled from GitHub via crawl_c2rust_upstream.py --repo
awtoau/c2rust) for the c2rust track, and hand-curated entries for
kernel-track work that has no GitHub issue tracker of its own.

work_items is an INDEX, not the handoff mechanism — actual work still
happens via real GitHub issues/PRs (an agent, Copilot, or a person
picks one up and opens a real commit). This just answers "what should
be worked on next" in one query instead of scanning multiple trackers.

Usage: sync_work_items.py
Inputs: rulesdb/patterns.db's c2rust_issues (run crawl_c2rust_upstream.py
        --repo awtoau/c2rust first if stale)
Output: rulesdb/patterns.db's work_items table
Log: tmp/sync_work_items.log
"""
import logging
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "rulesdb" / "patterns.db"
LOG = REPO / "tmp" / "sync_work_items.log"

# Kernel-track work items have no GitHub issue tracker (linux-riscv/ is
# a local working tree, not a repo we file issues against) — hand-
# curated here, matched by title so re-running this script updates
# rather than duplicates. Add a new entry whenever a real kernel-track
# bug/task is found; mark status='done' with fixed_by_commit once landed
# rather than deleting, so the history of what was found stays queryable.
KERNEL_WORK_ITEMS = [
    {
        "title": "CONFIG_RUST_KUNIT_TESTS gate missing after upstream kernel sync",
        "status": "done",
        "priority": "P0",
        "priority_rationale": "blocked all 6 Rust KUnit suites from running at all — boot oracle itself",
        "blocks_boot_path": 1,
        "fixed_by_commit": "linux-riscv (config enable, see dev.py config -e history)",
        "notes": "New upstream Kconfig menuconfig gate (rust/kernel/Kconfig.test) came back "
                 "unset from olddefconfig after the 15,717-commit rebase onto torvalds/linux "
                 "master. Re-enabled RUST_KUNIT_TESTS + 6 children.",
    },
    {
        "title": "memparse() overflow handling out of sync with upstream fix 9a4580db6e9f",
        "status": "done",
        "priority": "P1",
        "priority_rationale": "cmdline KUnit suite regression after kernel sync",
        "blocks_boot_path": 1,
        "fixed_by_commit": "linux-riscv 5c1e05432402",
        "notes": "Upstream fixed memparse() to saturate on overflow and reject bare suffixes "
                 "with no preceding digits; our translation (cmdline_rs.rs) still matched the "
                 "old buggy behavior. Ported the fix.",
    },
    {
        "title": "kstrtox _parse_integer_limit doesn't saturate to ULLONG_MAX on overflow",
        "status": "done",
        "priority": "P1",
        "priority_rationale": "independent pre-existing translation bug, found while fixing memparse()",
        "blocks_boot_path": 1,
        "fixed_by_commit": "linux-riscv ae53660e5b00",
        "notes": "TU 29's own translation set KSTRTOX_OVERFLOW but left the wrapped multiply/"
                 "add result instead of saturating — masked until memparse()'s new overflow "
                 "logic started relying on the saturation contract.",
    },
    {
        "title": "8250/16550 serial driver translation — first slice: register helpers",
        "status": "open",
        "priority": "P2",
        "priority_rationale": "Real device-driver TU (first of its kind — all 30 landed TUs are "
                 "lib/-style pure functions), and unusually high-stakes: 8250 is the live console "
                 "driver this project's ENTIRE verification methodology depends on reading "
                 "(dev.py check parses QEMU serial output for KUnit results). A subtly-wrong "
                 "translation could boot yet corrupt/drop console output, silently invalidating "
                 "the test harness itself. Not urgent (console works fine in C today, nothing is "
                 "broken) but high-value as the next ambitious hand-translation target once "
                 "picked up — hence P2, not P0/P1. Scoped narrowly on purpose: first slice is "
                 "serial8250_compute_lcr / fcr_get_rxtrig_bytes / bytes_to_fcr_rxtrig only (pure, "
                 "control-flow-simple, zero register I/O), verified via diff-oracle "
                 "(bench/diff_8250_helpers.{c,rs}, already landed and passing byte-identical over "
                 "7500 cases) and NOT wired into the live boot path in this first slice. See "
                 "docs/serial-8250-translation-scoping-2026-07-18.md for full driver-structure "
                 "analysis, risk assessment, and the staged plan for eventually reaching the live "
                 "console path.",
        "blocks_boot_path": 0,
        "notes": "drivers/tty/serial/8250/8250_port.c is 3472 lines (vs a few hundred for the "
                 "largest lib/ TU so far) — monolithic translation is not realistically scoped "
                 "for one pass. Scoping doc breaks it into: (1) pure register-bit helpers [this "
                 "item, oracle already passing], (2) serial_in/out register-access shims "
                 "[unsafe MMIO, testable only via KUnit not diff-oracle], (3) startup/shutdown/ "
                 "termios control flow [high complexity, high risk], (4) IRQ handling + tty core "
                 "integration [out of scope indefinitely — framework plumbing, not 16550-specific]. "
                 "No Rust-for-Linux prior art found for an in-kernel 8250/ns16550 driver using the "
                 "`kernel` crate abstractions (only standalone no_std bare-metal crates like "
                 "uart_16550/ns16550a exist, unrelated to this project's translation approach).",
    },
    {
        "title": "Interactive console milestone — minimal initramfs /init drops to a live sh instead of powering off",
        "status": "open",
        "priority": "P1",
        "priority_rationale": "Blocks the entire hybrid-boot-backwards stream (docs/streams.md #3): "
                 "working BACKWARDS from a known-good boot-to-console baseline, adding landed "
                 "translations back in one at a time with a full dev.py check after each, requires "
                 "having a real interactive console to work from in the first place. Today's "
                 "configs/initramfs-init.sh mounts devtmpfs/proc/sys, prints INIT REACHED, and "
                 "immediately calls `busybox poweroff -f` — there is no way to sit at a prompt and "
                 "run anything today. P1 (not P0) since nothing is currently broken by this gap, "
                 "but it's the single blocking prerequisite for a whole stream, not just one item.",
        "blocks_boot_path": 0,
        "notes": "Proposed verification once landed: re-run a KUnit suite FROM the live console "
                 "(debugfs, `/sys/kernel/debug/kunit/<suite>/run` if CONFIG_KUNIT_DEBUGFS is on) "
                 "rather than only the automatic boot-time run — reuses trusted infrastructure "
                 "(same suites dev.py check already parses) as real, load-bearing evidence the "
                 "console is genuinely interactive, not just an ad hoc echo test.",
    },
    {
        "title": "tmpfs-in-Rust — blocked on missing VFS abstractions, evaluate upstream RFC PR #1037 first",
        "status": "open",
        "priority": "P3",
        "priority_rationale": "Not a translation TU today — scoping confirmed this project's vendored "
                 "`rust/kernel/` has ZERO VFS filesystem-registration abstractions (no SuperBlock, "
                 "Inode, Dentry, address_space, file_system_type, register_filesystem; fs.rs is 11 "
                 "lines covering only already-open File/Kiocb). A Rust tmpfs has nowhere to attach "
                 "regardless of translation quality. CONFIG_SHMEM is also unset in .config. Below "
                 "8250 (P2, which has a real struct uart_ops attachment point today) and c2rust "
                 "fixes. Not P4 because the recommended next step (attempt rebasing upstream RFC "
                 "PR #1037 onto this project's HEAD in an isolated branch) is a concrete, boundable, "
                 "single-session evaluation, not indefinitely blocked — PR #1037's base commit "
                 "(43a393185e33) turned out to be a direct git ancestor of this project's current "
                 "linux-riscv HEAD (~1 month back), a much better starting position than assumed, "
                 "which makes this more tractable than a generic 'wait for upstream' P4.",
        "blocks_boot_path": 0,
        "notes": "Three options assessed in docs/tmpfs-rust-scoping-2026-07-18.md: (a) write VFS "
                 "abstractions (SuperBlock/Inode/Dentry/AddressSpace/file_system_type) into "
                 "rust/kernel/ from scratch — rejected as a first move, kernel-architecture-level "
                 "work that upstream's own RFC hasn't finished in ~3 years; (b) adapt unmerged "
                 "upstream Rust-for-Linux PR #1037 ('vfs abstractions and tarfs', open since "
                 "2023-09-29, still just a draft against rust-next, last rebased 2026-06-16) — "
                 "RECOMMENDED first step, scoped as 'attempt a mechanical rebase onto this "
                 "project's HEAD in an isolated branch, report conflict size' rather than a full "
                 "port; PR adds rust/kernel/fs.rs (+1290 lines), folio.rs (+214, new), "
                 "fs/buffer.rs (+60, new), mem_cache.rs (+62, new), plus a worked tarfs example "
                 "(fs/tarfs/, +426 lines) and a simpler rust_rofs sample (+154) — none of these "
                 "paths exist in this project's tree today; (c) standalone not-yet-integrated "
                 "translation of mm/shmem.c (5963 lines, ~197 top-level functions, 641 hits for "
                 "swap_/struct inode/struct address_space/folio — heavily entangled with core mm, "
                 "not a lib/-style pure-function file) — deferred, needs its own 8250-style "
                 "function-tiering scoping pass before being a real candidate, not pre-scoped by "
                 "this doc. No code written or linux-riscv/ changes made in this scoping pass.",
    },
    {
        "title": "target_compile_test.py — cross-compile+riscv64-execute oracle for candidate .rs files",
        "status": "open",
        "priority": "P3",
        "priority_rationale": "New verification capability (intermediate rung between stream 1's "
                 "host-side real-ABI compile-check and stream 2/3's full boot-and-KUnit gate), not "
                 "a fix for something broken. Scoping (docs/target-compile-test-scoping-2026-07-18.md) "
                 "confirmed the marginal signal over the existing host-native diff-oracle "
                 "(scripts/diff_oracle.py) is real but modest for the lib/-style code translated so "
                 "far (no architecture-dependent behavior in what's landed) — real value shows up "
                 "once register/MMIO/asm-shaped candidates (8250 Tier B and beyond) need evaluating, "
                 "which is not yet the case for anything currently in flight. P3: worth building "
                 "(cheap — reuses ~existing, already-verified toolchain/runtime, small extension of "
                 "diff_oracle.py's own ~80-line structure) but nothing is blocked on it today.",
        "blocks_boot_path": 0,
        "notes": "Scoping's key finding: 'rustc inside the QEMU guest' is NOT the right shape "
                 "(confirmed — no native riscv64 rustc exists or is practical to build, and the "
                 "256M guest has no room for one). Right shape verified end-to-end instead: cross-"
                 "compile on host with `rustc --target riscv64gc-unknown-linux-musl -C target-"
                 "feature=+crt-static -C link-self-contained=on -C linker-flavor=ld.lld` (self-"
                 "contained via rust-lld — sidesteps a real, reproduced ISA-version link "
                 "incompatibility between the project's cached musl.cc riscv64-linux-musl-cross "
                 "toolchain and rustc's own prebuilt riscv64gc-unknown-linux-musl static libs), then "
                 "execute directly via qemu-riscv64-static (already installed: qemu-user-static-riscv "
                 "package, confirmed via rpm -q) — usermode emulation, no qemu-system-riscv64/kernel "
                 "image/initramfs/boot involved at all, verified with a real cross-compiled binary "
                 "producing correct output and exit code. Proposed first step: implement against one "
                 "existing bench/diff_bcd or diff_win_minmax pair (smallest, fastest iteration) as a "
                 "second execution backend for scripts/diff_oracle.py's existing harness contract, "
                 "reporting host-native and riscv64-emulated verdicts side by side. No code written "
                 "in this scoping pass; linux-riscv/, linux/, and the c2rust fork untouched.",
    },
]


def sync_from_c2rust_issues(conn):
    """One work_item per open-or-recently-closed awtoau/c2rust issue,
    matched on (repo, issue_number) so re-running updates rather than
    duplicates. Priority comes from the real GitHub label (P0-P4) —
    this is the denormalized copy, kept in sync here, not the source
    of truth (the GitHub label is)."""
    rows = conn.execute(
        "SELECT number, title, state, labels, html_url FROM c2rust_issues "
        "WHERE repo='awtoau/c2rust' AND is_pr=0"
    ).fetchall()
    n = 0
    for number, title, state, labels, html_url in rows:
        priority = None
        for label in (labels or "").split(","):
            label = label.strip()
            if label in ("P0", "P1", "P2", "P3", "P4"):
                priority = label
        status = "done" if state == "closed" else "open"
        existing = conn.execute(
            "SELECT id FROM work_items WHERE repo=? AND issue_number=?",
            ("awtoau/c2rust", number),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE work_items SET title=?, priority=?, status=?, updated_at=datetime('now') "
                "WHERE id=?",
                (title, priority, status, existing[0]),
            )
        else:
            conn.execute(
                "INSERT INTO work_items (track, title, repo, issue_number, priority, status, "
                "created_at, updated_at) VALUES ('c2rust', ?, 'awtoau/c2rust', ?, ?, ?, "
                "datetime('now'), datetime('now'))",
                (title, number, priority, status),
            )
        n += 1
    return n


def sync_kernel_items(conn):
    n = 0
    for item in KERNEL_WORK_ITEMS:
        existing = conn.execute(
            "SELECT id FROM work_items WHERE track='kernel' AND title=?", (item["title"],)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE work_items SET status=?, priority=?, priority_rationale=?, "
                "blocks_boot_path=?, fixed_by_commit=?, notes=?, updated_at=datetime('now') "
                "WHERE id=?",
                (item["status"], item["priority"], item["priority_rationale"],
                 item["blocks_boot_path"], item.get("fixed_by_commit"), item.get("notes"),
                 existing[0]),
            )
        else:
            conn.execute(
                "INSERT INTO work_items (track, title, status, priority, priority_rationale, "
                "blocks_boot_path, fixed_by_commit, notes, created_at, updated_at) "
                "VALUES ('kernel', ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                (item["title"], item["status"], item["priority"], item["priority_rationale"],
                 item["blocks_boot_path"], item.get("fixed_by_commit"), item.get("notes")),
            )
        n += 1
    return n


def main():
    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.FileHandler(LOG, mode="a"), logging.StreamHandler(sys.stdout)],
    )

    if not DB.exists():
        logging.error("no %s — run scripts/build_db.py first", DB)
        return 1

    conn = sqlite3.connect(DB)
    n_c2rust = sync_from_c2rust_issues(conn)
    n_kernel = sync_kernel_items(conn)
    conn.commit()

    active = conn.execute(
        "SELECT track, priority, title FROM work_items_active LIMIT 10"
    ).fetchall()
    conn.close()

    logging.info("synced %d c2rust issues, %d kernel items", n_c2rust, n_kernel)
    logging.info("top of work_items_active:")
    for track, priority, title in active:
        logging.info("  [%s/%s] %s", track, priority, title[:80])
    print(f"SYNC OK: {n_c2rust} c2rust + {n_kernel} kernel work items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
