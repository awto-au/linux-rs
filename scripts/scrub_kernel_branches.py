#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Scrub check before publishing awto/linux-rs/* store branches to a public fork.

Per branch, over commits not in origin/master: flags host paths (/mnt/, /home/)
and sensitive terms in added lines and in commit messages. Exit 1 on any hit.

Usage: scrub_kernel_branches.py [--pattern GLOB] [--upstream REV]
Log: tmp/logs/scrub_kernel_branches.log
"""
import argparse
import re
import subprocess
import sys

from _repo import REPO, make_log

TREE = REPO / "linux-riscv"
BAD = re.compile(r"/mnt/|/home/|hantek|\bV07\b", re.IGNORECASE)
log = make_log("scrub_kernel_branches")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(TREE), *args],
                          capture_output=True, text=True, check=True).stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="awto/linux-rs/*")
    ap.add_argument("--upstream", default="origin/master")
    args = ap.parse_args()

    branches = git("for-each-ref", "--format=%(refname:short)", f"refs/heads/{args.pattern}").split()
    hits = 0
    for br in branches:
        rng = f"{args.upstream}..{br}"
        msgs = git("log", "--format=%h %B", rng)
        bad = [f"msg: {l.strip()}" for l in msgs.splitlines() if BAD.search(l)]
        for line in git("log", "-p", "--format=COMMIT %h", rng).splitlines():
            if line.startswith("COMMIT "):
                cur = line[7:]
            elif line.startswith("+") and not line.startswith("+++") and BAD.search(line):
                bad.append(f"{cur}: {line[:160]}")
        log(f"{'FAIL' if bad else 'ok  '} {br}: {len(bad)} hits")
        for b in bad:
            log(f"    {b}")
        hits += len(bad)
    log(f"SUMMARY: {len(branches)} branches, {hits} hits")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
