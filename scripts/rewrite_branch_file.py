#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Replace a string in one file at a branch tip, re-committing the tip (no checkout).

For scrubbing a branch before publishing: rebuilds the tip commit with the same
parent, author and message, only PATH changed. Kernel trees are GB-scale, so this
uses a temp index instead of a worktree.

Usage: rewrite_branch_file.py BRANCH PATH OLD NEW [--tree DIR]
Log: tmp/logs/rewrite_branch_file.log
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from _repo import REPO, TMP, make_log

log = make_log("rewrite_branch_file")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("branch")
    ap.add_argument("path")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--tree", type=Path, default=REPO / "linux-riscv")
    a = ap.parse_args()
    TMP.mkdir(exist_ok=True)
    env = dict(os.environ, GIT_INDEX_FILE=str(TMP / "rewrite_branch_file.idx"))

    def git(*args, inp=None, e=None):
        return subprocess.run(["git", "-C", str(a.tree), *args], input=inp, env=e,
                              capture_output=True, text=True, check=True).stdout.strip()

    tip = git("rev-parse", a.branch)
    text = git("show", f"{tip}:{a.path}") + "\n"
    n = text.count(a.old)
    if n == 0:
        log(f"NO CHANGE: {a.old!r} not in {a.path} at {tip[:12]}")
        return 1
    blob = git("hash-object", "-w", "--stdin", inp=text.replace(a.old, a.new))
    git("read-tree", tip, e=env)
    git("update-index", "--cacheinfo", f"100644,{blob},{a.path}", e=env)
    tree = git("write-tree", e=env)
    meta = git("log", "-1", "--format=%an%x00%ae%x00%ad%x00%B", tip).split("\x00", 3)
    cenv = dict(os.environ, GIT_AUTHOR_NAME=meta[0], GIT_AUTHOR_EMAIL=meta[1], GIT_AUTHOR_DATE=meta[2])
    parents = git("log", "-1", "--format=%P", tip).split()
    pargs = [x for p in parents for x in ("-p", p)]
    new = git("commit-tree", tree, *pargs, inp=meta[3], e=cenv)
    git("update-ref", f"refs/heads/{a.branch}", new, tip)
    (TMP / "rewrite_branch_file.idx").unlink(missing_ok=True)
    log(f"REWROTE {a.branch}: {tip[:12]} -> {new[:12]} ({n} replacements in {a.path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
