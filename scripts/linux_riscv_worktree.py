#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Isolated kernel worktrees for agents doing kernel-tree work in parallel.

- Worktrees come from the machine Linux store (tmp/linux-store, see kernel_store.py, #118):
  objects shared, each tree costs only its ~2 GB checkout. Never clone a kernel for this.
- Use one per kernel-touching task instead of editing the shared linux-riscv/ (two agents in one
  tree was a real collision, linux-rs#3).
- Branch per worktree: `awto/linux-rs/<name>-<base tag>`, from --base (default: linux-riscv/'s HEAD).
- The new tree has no build output: build it before boot-testing, and check Rust linked in
  (`llvm-nm vmlinux | grep <some *_rs symbol>`; a Rust-free build still says BUILD OK, linux-rs#33).

Usage:
  linux_riscv_worktree.py create <name> [--base REV]   -> linux-riscv-worktrees/<name>/
  linux_riscv_worktree.py list                         store worktrees; flags any outside the managed dir
  linux_riscv_worktree.py remove <name> [--delete-branch]
Store: --store PATH or env LINUX_RS_STORE, else tmp/linux-store.
Log: tmp/logs/linux_riscv_worktree.log
"""
import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import REPO, log_path  # noqa: E402
from kernel_store import MAINLINE, store_path  # noqa: E402

LOG = log_path("linux_riscv_worktree")
MAIN_TREE = REPO / "linux-riscv"
WORKTREES_DIR = REPO / "linux-riscv-worktrees"
SEED_CONFIG = MAIN_TREE / ".config"
# worktree add of a ~90k-file kernel checkout ~30-60 s on this disk; x1.25 rounded up.
# Other git calls are local metadata reads (<1 s). On expiry: logged, exit 1.
CHECKOUT_TIMEOUT, GIT_TIMEOUT = 90, 15


def sh(cmd, cwd=None, check=True, timeout=GIT_TIMEOUT):
    try:
        return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check, timeout=timeout)
    except subprocess.TimeoutExpired:
        logging.error("TIMEOUT %s: no result after %d s (limit)", " ".join(map(str, cmd)), timeout)
        sys.exit(1)


def create(store: Path, name: str, base: str | None) -> int:
    target = WORKTREES_DIR / name
    if target.exists():
        logging.error("worktree already exists: %s (other name, or `remove %s` first)", target, name)
        return 1
    rev = base or sh(["git", "rev-parse", "HEAD"], cwd=MAIN_TREE).stdout.strip()
    mb = sh(["git", "merge-base", rev, MAINLINE], cwd=store).stdout.strip()
    tag = sh(["git", "describe", "--tags", "--abbrev=0", mb], cwd=store).stdout.strip()
    branch = f"awto/linux-rs/{name}-{tag}"
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    r = sh(["git", "worktree", "add", "-b", branch, str(target), rev], cwd=store, check=False,
           timeout=CHECKOUT_TIMEOUT)
    if r.returncode != 0:
        logging.error("worktree add failed: %s", r.stderr.strip())
        return 1
    logging.info("created %s on branch %s (from %s)", target, branch, rev[:12])
    # No .config -> olddefconfig falls back to Kconfig defaults, CONFIG_RUST off (linux-rs#33)
    if SEED_CONFIG.exists():
        shutil.copy2(SEED_CONFIG, target / ".config")
        print(f"OK: {target}  (branch {branch}, .config seeded from linux-riscv/.config)")
    else:
        logging.warning("no seed .config at %s: copy a CONFIG_RUST=y .config in before "
                        "`dev.py config -e` (linux-rs#33)", SEED_CONFIG)
        print(f"OK: {target}  (branch {branch}, NO .config)")
    print("NOTE: no build output in a new worktree; build before boot-testing (linux-rs#33).")
    return 0


def list_worktrees(store: Path) -> int:
    out = sh(["git", "worktree", "list"], cwd=store).stdout
    print(out, end="")
    mine = [ln for ln in out.splitlines() if ln.startswith(str(REPO) + "/")]
    stray = [ln for ln in mine if Path(ln.split()[0]).parent != WORKTREES_DIR
             and Path(ln.split()[0]) not in (MAIN_TREE, REPO / "linux")]
    if stray:
        print("\nSTRAY (this repo's, but not linux/, linux-riscv/ or linux-riscv-worktrees/*):")
        for ln in stray:
            print(f"  {ln}")
    return 0


def remove(store: Path, name: str, delete_branch: bool) -> int:
    target = WORKTREES_DIR / name
    if not target.exists():
        logging.error("no such worktree: %s", target)
        return 1
    branch = sh(["git", "branch", "--show-current"], cwd=target).stdout.strip()
    r = sh(["git", "worktree", "remove", "--force", str(target)], cwd=store, check=False,
           timeout=CHECKOUT_TIMEOUT)
    if r.returncode != 0:
        logging.error("worktree remove failed: %s", r.stderr.strip())
        return 1
    logging.info("removed %s", target)
    if delete_branch and branch:
        r = sh(["git", "branch", "-D", branch], cwd=store, check=False)
        logging.info("branch %s: %s", branch, "deleted" if r.returncode == 0 else r.stderr.strip())
    print(f"OK: removed {target}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG, mode="a"), logging.StreamHandler(sys.stdout)])
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", help="Linux store (default: env LINUX_RS_STORE, else tmp/linux-store)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create")
    p.add_argument("name")
    p.add_argument("--base", help="start revision (default: linux-riscv/ HEAD)")
    sub.add_parser("list")
    p = sub.add_parser("remove")
    p.add_argument("name")
    p.add_argument("--delete-branch", action="store_true")
    a = ap.parse_args()
    store = store_path(a.store)
    if not (store / ".git").exists():
        logging.error("%s: no Linux store; run `kernel_store.py --store PATH setup` (#118)", store)
        return 1
    if a.cmd == "create":
        return create(store, a.name, a.base)
    if a.cmd == "list":
        return list_worktrees(store)
    return remove(store, a.name, a.delete_branch)


if __name__ == "__main__":
    sys.exit(main())
