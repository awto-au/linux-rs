#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Manage isolated git worktrees of the awtoau/c2rust fork for agents
fixing separate issues in parallel.

Why this exists (2026-07-18): the Agent tool's isolation="worktree"
option only isolates THIS repo (linux-rs) — an agent instructed to `cd`
into a separate repo (like awtoau/c2rust, which lives outside linux-rs
entirely) gets no isolation there at all. Two agents fixing c2rust
issues #11 and #12 in parallel both worked directly in the single
shared /mnt/2tb/git/github.com/awtoau/c2rust checkout, collided (one
agent's branch-switch left the other's staged-but-uncommitted changes
sitting on the wrong branch), and one agent improvised its own ad-hoc
clone (awtoau/c2rust-issue12) mid-task trying to self-remediate — a
real, working fix, but an unmanaged, undocumented one that would have
been easy to lose or leave orphaned.

This script is the deliberate replacement: every c2rust-track agent
gets its own script-created, predictably-named, script-cleaned-up
worktree under awtoau/c2rust-worktrees/<name>/ (sibling to the main
checkout, not mixed in with unrelated repos like awtoau/prjtrellis).
Never git worktree add by hand or ad-hoc clone for this purpose again —
use this script so `list` always shows the true state and `remove`
always cleans up both the worktree and its branch.

Usage:
  c2rust_worktree.py create <name> [--base master]
      Creates awtoau/c2rust-worktrees/<name>/ on a new branch
      "agent-<name>" branched from --base (default master).
  c2rust_worktree.py list
      Shows every c2rust worktree (script-managed and any stray ones
      found under the main checkout's own `git worktree list`).
  c2rust_worktree.py remove <name> [--delete-branch]
      Removes the worktree directory. --delete-branch also deletes the
      local agent-<name> branch (only after it's merged/pushed/no
      longer needed — this does NOT check that for you).

Log: tmp/c2rust_worktree.log
"""
import argparse
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "c2rust_worktree.log"

C2RUST_MAIN = Path("/mnt/2tb/git/github.com/awtoau/c2rust")
WORKTREES_DIR = Path("/mnt/2tb/git/github.com/awtoau/c2rust-worktrees")


def sh(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)


def create(name: str, base: str) -> int:
    if not C2RUST_MAIN.exists():
        logging.error("no c2rust checkout at %s", C2RUST_MAIN)
        return 1
    target = WORKTREES_DIR / name
    if target.exists():
        logging.error("worktree already exists: %s (use a different name, or "
                       "`remove %s` first if it's stale)", target, name)
        return 1
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    branch = f"agent-{name}"
    r = sh(["git", "fetch", "origin"], cwd=C2RUST_MAIN, check=False)
    if r.returncode != 0:
        logging.warning("fetch failed (continuing with local refs): %s", r.stderr.strip())
    r = sh(["git", "worktree", "add", "-b", branch, str(target), f"origin/{base}"],
           cwd=C2RUST_MAIN, check=False)
    if r.returncode != 0:
        logging.error("worktree add failed: %s", r.stderr.strip())
        return 1
    logging.info("created %s on branch %s (from origin/%s)", target, branch, base)
    print(f"OK: {target}  (branch {branch})")
    return 0


def list_worktrees() -> int:
    r = sh(["git", "worktree", "list"], cwd=C2RUST_MAIN, check=False)
    if r.returncode != 0:
        logging.error("could not list worktrees: %s", r.stderr.strip())
        return 1
    print(r.stdout, end="")
    stray = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        path = Path(line.split()[0])
        if path == C2RUST_MAIN or path.parent == WORKTREES_DIR:
            continue
        stray.append(line)
    if stray:
        print("\nSTRAY (not under the managed c2rust-worktrees/ dir — clean up by "
              "hand, script can't safely guess these):")
        for line in stray:
            print(f"  {line}")
    return 0


def remove(name: str, delete_branch: bool) -> int:
    target = WORKTREES_DIR / name
    branch = f"agent-{name}"
    if not target.exists():
        logging.error("no such worktree: %s", target)
        return 1
    r = sh(["git", "worktree", "remove", "--force", str(target)], cwd=C2RUST_MAIN, check=False)
    if r.returncode != 0:
        logging.error("worktree remove failed: %s", r.stderr.strip())
        return 1
    logging.info("removed %s", target)
    if delete_branch:
        r = sh(["git", "branch", "-D", branch], cwd=C2RUST_MAIN, check=False)
        if r.returncode == 0:
            logging.info("deleted branch %s", branch)
        else:
            logging.warning("branch delete failed (may already be gone): %s", r.stderr.strip())
    print(f"OK: removed {target}")
    return 0


def main() -> int:
    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.FileHandler(LOG, mode="a"), logging.StreamHandler(sys.stdout)],
    )
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create")
    p_create.add_argument("name")
    p_create.add_argument("--base", default="master")

    sub.add_parser("list")

    p_remove = sub.add_parser("remove")
    p_remove.add_argument("name")
    p_remove.add_argument("--delete-branch", action="store_true")

    args = ap.parse_args()
    if args.cmd == "create":
        return create(args.name, args.base)
    if args.cmd == "list":
        return list_worktrees()
    if args.cmd == "remove":
        return remove(args.name, args.delete_branch)
    return 1


if __name__ == "__main__":
    sys.exit(main())
