#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Report which translated TUs' C originals changed upstream since the series base.

- Pairs from rulesdb translated_tus (c_file, rs_file); split TUs (8250) map to source_c_file.
- Base = merge-base(linux-riscv HEAD, origin/master); target = origin/master (or --target).
- DRIFTED = C file differs base..target → Rust translation must be re-checked/regenerated.

Usage: check_c_drift.py [--target REV] [--json]
Log: tmp/logs/check_c_drift.log; JSON: tmp/c_drift.json
"""
import argparse
import json
import sqlite3
import subprocess
import sys

from _repo import REPO, TMP, make_log

TREE = REPO / "linux-riscv"
DB = REPO / "rulesdb" / "patterns.db"
log = make_log("check_c_drift")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(TREE), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="origin/master")
    ap.add_argument("--json", action="store_true", help="also write tmp/c_drift.json")
    args = ap.parse_args()

    base = git("merge-base", "HEAD", args.target)
    log(f"base={git('describe', '--tags', base)} target={git('describe', '--tags', args.target)}")

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # split TUs: the series-created .c slice has no upstream; diff its real source instead
    pairs = conn.execute(
        "SELECT COALESCE(s.source_c_file, t.c_file), t.rs_file FROM translated_tus t "
        "LEFT JOIN split_tu_provenance s ON s.rs_file = t.rs_file ORDER BY 1").fetchall()

    rows = []
    for c_file, rs_file in pairs:
        stat = git("diff", "--numstat", base, args.target, "--", c_file)
        if not stat:
            state, added, removed = "same", 0, 0
        else:
            a, r, _ = stat.split("\t", 2)
            state, added, removed = "DRIFTED", int(a), int(r)
        if not git("ls-tree", args.target, "--", c_file):
            state = "GONE"
        rows.append({"c_file": c_file, "rs_file": rs_file, "state": state,
                     "added": added, "removed": removed})
        log(f"{state:8} +{added:<5} -{removed:<5} {c_file}  ({rs_file})")

    drifted = [r for r in rows if r["state"] != "same"]
    log(f"SUMMARY: {len(drifted)}/{len(rows)} translated TUs have C drift base..{args.target}")
    if args.json:
        TMP.mkdir(exist_ok=True)
        (TMP / "c_drift.json").write_text(json.dumps(
            {"base": base, "target": git("rev-parse", args.target), "rows": rows}, indent=1))
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
