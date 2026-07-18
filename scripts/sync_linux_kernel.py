#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Rebase linux-riscv/'s own commits (translations, config, boot-path
fixes) onto the latest upstream torvalds/linux master, then rebuild and
re-run the full boot+KUnit oracle. This project deliberately tracks
upstream HEAD rather than a pinned release (test/research infra, not
production) — this is the repeatable version of that manual process.

Deliberately NOT auto-scheduled (same convention as
crawl_c2rust_upstream.py) — upstream moves fast and a rebase can
conflict or shift Kconfig surface (new gates, renamed options) in ways
that need a human/agent to actually look at, not a cron job silently
rewriting the tree. Re-run manually when you want to move forward.

On any failure (conflict, build, or boot oracle) the branch is LEFT AS
IS mid-rebase or at the new base — never force-reset or discard work
automatically. Resolve conflicts / fix the regression, then re-run
`dev.py check` yourself; this script's job is the rebase + one
verification pass, not unattended repair.

Usage: sync_linux_kernel.py [--dry-run]
Log: tmp/sync_linux_kernel.log
"""
import argparse
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
LOG = REPO / "tmp" / "sync_linux_kernel.log"


def sh(cmd, cwd=TREE, check=True):
    logging.info("+ %s (cwd=%s)", " ".join(map(str, cmd)), cwd)
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if p.stdout:
        logging.info(p.stdout)
    if p.stderr:
        logging.info(p.stderr)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed (rc={p.returncode}): {' '.join(map(str, cmd))}")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch + report only, no rebase")
    args = ap.parse_args()

    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="a"), logging.StreamHandler(sys.stdout)],
    )

    status = sh(["git", "status", "--short"])
    if status.stdout.strip():
        print("REFUSING: uncommitted changes in linux-riscv/ — commit or stash first")
        return 1

    sh(["git", "fetch", "upstream"])
    behind = sh(["git", "rev-list", "--count", "HEAD..upstream/master"]).stdout.strip()
    behind_rust = sh(["git", "log", "--oneline", "HEAD..upstream/master", "--", "rust/"]).stdout
    n_rust = len(behind_rust.splitlines())
    print(f"{behind} commits behind upstream/master ({n_rust} touching rust/)")

    if behind == "0":
        print("SYNC OK: already at upstream/master")
        return 0

    if args.dry_run:
        print("DRY RUN: not rebasing")
        return 0

    old_head = sh(["git", "rev-parse", "HEAD"]).stdout.strip()
    ours = sh(["git", "rev-list", "--count", "@{upstream}..HEAD"], check=False)
    if ours.returncode == 0:
        print(f"{ours.stdout.strip()} local commits not yet rebased")

    try:
        sh(["git", "rebase", "upstream/master"])
    except RuntimeError:
        print("REBASE CONFLICT — tree left mid-rebase. Resolve by hand:")
        print("  git -C linux-riscv status")
        print("  (fix conflicts, git add, git rebase --continue)")
        print("  or git -C linux-riscv rebase --abort to go back")
        return 1

    new_head = sh(["git", "rev-parse", "HEAD"]).stdout.strip()
    print(f"REBASED: {old_head[:12]} -> {new_head[:12]}")

    # Rebuild + full boot/KUnit oracle — the real verification. Let any
    # failure here propagate as a normal non-zero exit; the branch stays
    # at the new (possibly broken) HEAD for a human/agent to fix, same
    # as any other failed `dev.py check` — rebasing again or resetting
    # is a separate, deliberate decision, not this script's to make.
    sh(["python3", str(REPO / "scripts" / "dev.py"), "check"], cwd=REPO, check=True)

    # patterns.db's corpus-derived tables (functions, statement families,
    # translated-TU status) are keyed off linux-riscv's tree contents —
    # stale after any rebase (new/moved/removed files, changed line
    # numbers) even though the boot oracle above only checks runtime
    # behavior, not the DB. Rebuild so readiness/rule-conformance queries
    # reflect the tree this sync just moved to, not the pre-sync one.
    sh(["python3", str(REPO / "scripts" / "dev.py"), "db"], cwd=REPO, check=True)

    print("SYNC OK: rebased, rebuilt, boot+KUnit oracle passed, patterns.db refreshed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"SYNC FAILED: {e}")
        sys.exit(1)
