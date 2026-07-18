#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Sync rulesdb/patterns.db's work_items table — the single queryable
index of what needs doing across both tracks (hand-translation in
linux-riscv/, the awtoau/c2rust fork) — from real sources of truth:
c2rust_issues (crawled from GitHub via crawl_c2rust_upstream.py --repo
<repo>, for BOTH awtoau/c2rust and awto-au/linux-rs — the table name is
legacy, it holds issues from any crawled repo). KERNEL_WORK_ITEMS below
is now only for already-closed historical items that predate
awto-au/linux-rs issue tracking (added 2026-07-18) — do not add new
open items there; open a real GitHub issue instead so kernel-track work
gets the same durability/visibility as c2rust-track work.

work_items is an INDEX, not the handoff mechanism — actual work still
happens via real GitHub issues/PRs (an agent, Copilot, or a person
picks one up and opens a real commit). This just answers "what should
be worked on next" in one query instead of scanning multiple trackers.

Usage: sync_work_items.py
Inputs: rulesdb/patterns.db's c2rust_issues (run
        crawl_c2rust_upstream.py --repo awtoau/c2rust and --repo
        awto-au/linux-rs first if stale)
Output: rulesdb/patterns.db's work_items table
Log: tmp/sync_work_items.log
"""
import logging
import sqlite3
import subprocess
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
        "title": "Interactive console milestone — minimal initramfs /init drops to a live sh instead of powering off",
        "status": "done",
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
        "fixed_by_commit": "linux-rs 72bebc7",
        "notes": "/init now does a single bounded `read -t 15` on /dev/console after INIT_REACHED: "
                 "real input hands off to `exec /bin/sh -i` for a genuinely open-ended interactive "
                 "session (manually verified with a piped `busybox uname -a` that actually executed "
                 "and returned real output, not just an echoed prompt); no input within 15s falls "
                 "through to the existing SBI poweroff, so dev.py check still terminates on its own "
                 "and never hangs. 15s is sized off this project's own fresh-boot timings (well "
                 "under 1s of wall-clock QEMU time from decompress through all 16 KUnit suites "
                 "through INIT_REACHED) and stays two orders of magnitude below dev.py's outer 600s "
                 "subprocess timeout. Re-verified dev.py check after landing: 16/16 KUnit suites, "
                 "ORACLE PASS, INIT REACHED, plus a real '/ #' prompt now visible in "
                 "tmp/qemu-boot.log. Re-running a KUnit suite FROM the live console (debugfs, "
                 "CONFIG_KUNIT_DEBUGFS) remains explicitly deferred to a separate follow-up task, "
                 "as does generic stdin-piping support in boot_qemu.py itself (this task only "
                 "proved input reaches the shell via a manual subprocess.Popen test, not a durable "
                 "script feature) — see docs/streams.md stream 3 for the sequencing.",
    },
]


def sync_from_issues(conn, repo, track):
    """One work_item per open-or-recently-closed GitHub issue in `repo`,
    matched on (repo, issue_number) so re-running updates rather than
    duplicates. Priority comes from the real GitHub label (P0-P4) —
    this is the denormalized copy, kept in sync here, not the source
    of truth (the GitHub label is). Requires crawl_c2rust_upstream.py
    --repo <repo> to have populated c2rust_issues for this repo first
    (the table name is legacy — it holds issues from any crawled repo,
    not just awtoau/c2rust).

    Used for BOTH tracks: awtoau/c2rust issues (track='c2rust') and
    awto-au/linux-rs issues (track='kernel') — kernel-track work used to
    be hand-curated only in KERNEL_WORK_ITEMS below with no real issue
    tracker backing it, which meant it could silently drift out of sync
    with reality (found 2026-07-18: an item's rationale claimed a
    translation wasn't wired into the boot path when it actually already
    was). Real GitHub issues are the source of truth for both tracks now;
    KERNEL_WORK_ITEMS is kept only for already-closed historical items
    that predate awto-au/linux-rs issue tracking.

    Returns (count, newly_closed) — newly_closed is every issue whose
    status flipped open->done since the last sync (detected by comparing
    against work_items' own previous status, not by tracking a separate
    "last seen" file), for the caller to append to docs/HISTORY.md via
    scripts/append_history.py. This is the "automatically updated via gh
    issue closing" path — no hand-editing HISTORY.md for routine issue
    closures."""
    rows = conn.execute(
        "SELECT number, title, state, labels, html_url, closed_at FROM c2rust_issues "
        "WHERE repo=? AND is_pr=0",
        (repo,),
    ).fetchall()
    n = 0
    newly_closed = []
    for number, title, state, labels, html_url, closed_at in rows:
        priority = None
        for label in (labels or "").split(","):
            label = label.strip()
            if label in ("P0", "P1", "P2", "P3", "P4"):
                priority = label
        status = "done" if state == "closed" else "open"
        existing = conn.execute(
            "SELECT id, status FROM work_items WHERE repo=? AND issue_number=?",
            (repo, number),
        ).fetchone()
        if existing:
            prev_status = existing[1]
            conn.execute(
                "UPDATE work_items SET title=?, priority=?, status=?, updated_at=datetime('now') "
                "WHERE id=?",
                (title, priority, status, existing[0]),
            )
            if status == "done" and prev_status != "done":
                newly_closed.append((title, repo, number, closed_at))
        else:
            conn.execute(
                "INSERT INTO work_items (track, title, repo, issue_number, priority, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, "
                "datetime('now'), datetime('now'))",
                (track, title, repo, number, priority, status),
            )
        n += 1
    return n, newly_closed


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
    n_c2rust, closed_c2rust = sync_from_issues(conn, "awtoau/c2rust", "c2rust")
    n_kernel_issues, closed_kernel = sync_from_issues(conn, "awto-au/linux-rs", "kernel")
    n_kernel = sync_kernel_items(conn) + n_kernel_issues
    conn.commit()

    active = conn.execute(
        "SELECT track, priority, title FROM work_items_active LIMIT 10"
    ).fetchall()
    conn.close()

    logging.info("synced %d c2rust issues, %d kernel items", n_c2rust, n_kernel)
    logging.info("top of work_items_active:")
    for track, priority, title in active:
        logging.info("  [%s/%s] %s", track, priority, title[:80])

    for title, repo, number, closed_at in closed_c2rust + closed_kernel:
        date = (closed_at or "")[:10] or "unknown-date"
        milestone = f"Closed {repo}#{number}: {title}"
        subprocess.run([sys.executable, str(REPO / "scripts" / "append_history.py"),
                         date, milestone], check=False)
        logging.info("history: appended closure of %s#%s", repo, number)

    print(f"SYNC OK: {n_c2rust} c2rust + {n_kernel} kernel work items, "
          f"{len(closed_c2rust) + len(closed_kernel)} newly-closed issues logged to HISTORY.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
