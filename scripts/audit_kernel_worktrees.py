#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Audit linux-riscv-worktrees/* before removal: what work exists nowhere else.

Per tree:
- unique commits: `git cherry <ref> HEAD` '+' lines (patch not in --ref, default linux-riscv HEAD)
- real edits: tracked changes, ignoring deletions under target/ dirs and tools/build/ (copy-step artefact)
- untracked files (excluding build output via the tree's .gitignore)

--retire: per tree, commit real edits + untracked as a snapshot on its own store branch
(branch kept), then move the dir to the machine wastebasket and `git worktree prune`.

Usage: audit_kernel_worktrees.py [--ref REV] [--json] [--retire [TREE ...] --wastebasket DIR]
Log: tmp/logs/audit_kernel_worktrees.log; JSON: tmp/kernel_worktree_audit.json
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from _repo import REPO, TMP, make_log

WT_DIR = REPO / "linux-riscv-worktrees"
MAIN = REPO / "linux-riscv"
log = make_log("audit_kernel_worktrees")


def git(tree, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args],
                          capture_output=True, text=True, check=True).stdout


def artefact(status: str, path: str) -> bool:
    return status == "D" and ("/target/" in f"/{path}" or path.startswith("tools/build/"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=None, help="compare against this rev (default linux-riscv HEAD)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--retire", nargs="*", metavar="TREE", help="no names = all trees")
    ap.add_argument("--wastebasket", type=Path)
    args = ap.parse_args()
    ref = args.ref or git(MAIN, "rev-parse", "HEAD").strip()

    out = []
    for tree in sorted(p for p in WT_DIR.iterdir() if (p / ".git").exists()):
        cherry = [l[2:] for l in git(tree, "cherry", ref, "HEAD").splitlines() if l.startswith("+")]
        commits = [git(tree, "log", "-1", "--format=%h %ad %s", "--date=short", c).strip() for c in cherry]
        edits = []
        for line in git(tree, "diff", "--name-status", "HEAD").splitlines():
            st, path = line.split("\t", 1)
            if not artefact(st[0], path):
                edits.append(f"{st} {path}")
        untracked = git(tree, "ls-files", "--others", "--exclude-standard").splitlines()
        row = {"tree": tree.name, "branch": git(tree, "branch", "--show-current").strip(),
               "unique_commits": commits, "edits": edits, "untracked": untracked}
        out.append(row)
        log(f"{tree.name}: {len(commits)} unique commits, {len(edits)} real edits, {len(untracked)} untracked")
        for c in commits:
            log(f"    commit  {c}")
        for e in edits + [f"?? {u}" for u in untracked]:
            log(f"    {e}")

    clean = [r["tree"] for r in out if not (r["unique_commits"] or r["edits"] or r["untracked"])]
    log(f"SUMMARY: {len(out)} trees, {len(clean)} hold nothing beyond {ref[:12]}")
    if args.json:
        TMP.mkdir(exist_ok=True)
        (TMP / "kernel_worktree_audit.json").write_text(json.dumps({"ref": ref, "trees": out}, indent=1))
    if args.retire is not None:
        return retire(out, args.retire, args.wastebasket)
    return 0


def retire(rows, names: list[str], wastebasket: Path | None) -> int:
    if not wastebasket or not wastebasket.is_dir():
        log("RETIRE FAIL: --wastebasket DIR (existing) required")
        return 1
    dest_root = wastebasket / f"linux-rs-kernel-worktrees-{datetime.now():%Y%m%d-%H%M%S}"
    dest_root.mkdir()
    for r in rows:
        if names and r["tree"] not in names:
            continue
        tree = WT_DIR / r["tree"]
        paths = [e.split(" ", 1)[1] for e in r["edits"]] + r["untracked"]
        if paths:
            subprocess.run(["git", "-C", str(tree), "add", "--", *paths], check=True)
            subprocess.run(["git", "-C", str(tree), "commit", "-q", "-m",
                            f"wip snapshot: {r['tree']} uncommitted work at retirement"], check=True)
        sha = git(tree, "rev-parse", "--short", "HEAD").strip()
        shutil.move(str(tree), str(dest_root / r["tree"]))
        log(f"RETIRED {r['tree']}: branch {r['branch']} @ {sha} ({len(paths)} paths snapshotted) -> {dest_root.name}")
    git(MAIN, "worktree", "prune")
    log(f"RETIRE DONE -> {dest_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
