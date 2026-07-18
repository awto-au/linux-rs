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
