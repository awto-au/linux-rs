#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Manage isolated git worktrees of the linux-riscv kernel tree for
agents doing kernel-tree work in parallel.

Why this exists (2026-07-18): linux-riscv/ is gitignored from this repo
and lives as its own local git worktree of linux/ (confirmed via `cat
linux-riscv/.git` -> gitdir: linux/.git/worktrees/linux-riscv), on
branch linux-rs/phase2-gcd. It is the single shared kernel tree every
boot-path/driver-translation agent builds and boots against. An agent
fixing awto-au/linux-rs#3 (wiring 8250 driver functions to Rust) hit
this directly: found concurrent uncommitted changes from another agent
sitting in the same tree, correctly stopped rather than guessing, and
had to wait for the coordinating session to confirm the tree was clear
before proceeding — real, but avoidable, lost time on a task explicitly
flagged as high-stakes (the live console driver the whole verification
methodology depends on).

Since linux-riscv/ is ALREADY a git worktree (not a plain clone), this
script is cheap: `git worktree add` shares the object store with
linux/, so a new isolated tree costs disk (the ~2.1G checked-out
working tree, uncompressed — not the .git objects) but no wasted
network/clone time. Every kernel-tree-touching agent that isn't doing a
quick read-only check should get its own worktree via this script
rather than editing the shared linux-riscv/ directly.

Usage:
  linux_riscv_worktree.py create <name> [--base linux-rs/phase2-gcd]
      Creates linux-riscv-worktrees/<name>/ (sibling to linux-riscv/,
      inside this repo's own tree but gitignored the same way) on a new
      branch "agent-<name>" branched from --base.
  linux_riscv_worktree.py list
      Shows every linux-riscv worktree and flags anything not under the
      managed dir as stray.
  linux_riscv_worktree.py remove <name> [--delete-branch]
      Removes the worktree directory (and its branch, optionally).

IMPORTANT: after creating a worktree, an agent must build its own
kernel image in it (`dev.py build` equivalent) before boot-testing —
worktrees share git history but NOT build artifacts (vmlinux, .config,
compiled objects aren't tracked by git). This costs real build time per
worktree; only worth it for genuinely-parallel kernel-tree work, not
quick single-file edits that can wait for the shared tree to free up.

Log: tmp/linux_riscv_worktree.log
"""
import argparse
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "linux_riscv_worktree.log"

LINUX_MAIN = REPO / "linux"
WORKTREES_DIR = REPO / "linux-riscv-worktrees"
SEED_CONFIG = REPO / "linux-riscv" / ".config"


def sh(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)


def create(name: str, base: str) -> int:
    if not LINUX_MAIN.exists():
        logging.error("no linux/ checkout at %s", LINUX_MAIN)
        return 1
    target = WORKTREES_DIR / name
    if target.exists():
        logging.error("worktree already exists: %s (use a different name, or "
                       "`remove %s` first if it's stale)", target, name)
        return 1
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    branch = f"agent-{name}"
    r = sh(["git", "worktree", "add", "-b", branch, str(target), base],
           cwd=LINUX_MAIN, check=False)
    if r.returncode != 0:
        logging.error("worktree add failed: %s", r.stderr.strip())
        return 1
    logging.info("created %s on branch %s (from %s)", target, branch, base)

    # awto-au/linux-rs#33: a bare `git worktree add` carries no .config
    # (git worktrees share history, never build artifacts). The first
    # `dev.py config -e ...` + olddefconfig against an empty .config
    # falls back to Kconfig defaults for EVERYTHING, including
    # CONFIG_RUST itself — `dev.py build` then reports "BUILD OK" for a
    # genuinely successful but silently Rust-free vmlinux. Seed a known
    # CONFIG_RUST=y config so that trap requires an explicit opt-out
    # (deleting the seeded .config) rather than being the default path.
    if SEED_CONFIG.exists():
        import shutil
        shutil.copy2(SEED_CONFIG, target / ".config")
        logging.info("seeded .config from %s", SEED_CONFIG)
        print(f"OK: {target}  (branch {branch}, .config seeded from linux-riscv/.config)")
    else:
        logging.warning("no seed .config at %s — new worktree has NONE; "
                         "the first `dev.py config -e` will fall back to Kconfig "
                         "defaults, which sets CONFIG_RUST off (see linux-rs#33)", SEED_CONFIG)
        print(f"OK: {target}  (branch {branch})")
        print("WARNING: no .config seeded (none found at linux-riscv/.config). "
              "Copy a known-good CONFIG_RUST=y .config in before the first "
              "`dev.py config -e` — see linux-rs#33.")

    print("NOTE: this worktree has git history only — no other build artifacts "
          "(vmlinux, compiled objects). Build a kernel image in it before "
          "boot-testing (see this project's dev.py build equivalent, pointed "
          "at this tree). After building, verify Rust actually linked in: "
          "`llvm-nm vmlinux | grep <an expected *_rs symbol>` — a Rust-free "
          "build still reports BUILD OK (linux-rs#33).")
    return 0


def list_worktrees() -> int:
    r = sh(["git", "worktree", "list"], cwd=LINUX_MAIN, check=False)
    if r.returncode != 0:
        logging.error("could not list worktrees: %s", r.stderr.strip())
        return 1
    print(r.stdout, end="")
    stray = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        path = Path(line.split()[0])
        if path == LINUX_MAIN or path == (REPO / "linux-riscv") or path.parent == WORKTREES_DIR:
            continue
        stray.append(line)
    if stray:
        print("\nSTRAY (not the main linux-riscv/ tree and not under the "
              "managed linux-riscv-worktrees/ dir — clean up by hand):")
        for line in stray:
            print(f"  {line}")
    return 0


def remove(name: str, delete_branch: bool) -> int:
    target = WORKTREES_DIR / name
    branch = f"agent-{name}"
    if not target.exists():
        logging.error("no such worktree: %s", target)
        return 1
    r = sh(["git", "worktree", "remove", "--force", str(target)], cwd=LINUX_MAIN, check=False)
    if r.returncode != 0:
        logging.error("worktree remove failed: %s", r.stderr.strip())
        return 1
    logging.info("removed %s", target)
    if delete_branch:
        r = sh(["git", "branch", "-D", branch], cwd=LINUX_MAIN, check=False)
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
    p_create.add_argument("--base", default="linux-rs/phase2-gcd")

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
