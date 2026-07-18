#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Tier-2.5 differential oracle: build the C and Rust sides, generate a
shared input corpus, run both, diff outputs byte-for-byte.

This is the PLAN's oracle addition between tier 1 (compiles) and tier 4
(exercised on a booted kernel) — for pure functions without a dedicated
KUnit suite. Cheaper than a kernel boot; catches wrong-on-some-input-class
bugs that a boot-smoke test cannot.

Usage: diff_oracle.py base64   (adds more targets as bench/diff_<name>.*
                                land)
Log: tmp/diff_oracle.log
"""
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = REPO / "tmp"
LOG = TMP / "diff_oracle.log"
N = "5000"
SEED = "424242"
TIMEOUT_S = 60


def sh(cmd):
    logging.info("$ %s", " ".join(map(str, cmd)))
    try:
        return subprocess.run(cmd, check=True, text=True, capture_output=True,
                               timeout=TIMEOUT_S).stdout
    except subprocess.TimeoutExpired:
        logging.error("ORACLE 2.5 FAIL: %s timed out after %ds", cmd[0], TIMEOUT_S)
        sys.exit(1)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: diff_oracle.py <target>  (e.g. base64)")
        return 1
    target = sys.argv[1]
    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    c_src = REPO / "bench" / f"diff_{target}.c"
    rs_src = REPO / "bench" / f"diff_{target}.rs"
    if not c_src.exists() or not rs_src.exists():
        logging.error("missing bench/diff_%s.{c,rs}", target)
        return 1

    sh(["clang", "-O1", "-o", str(TMP / f"diff_{target}_c"), str(c_src)])
    sh(["rustc", "-O", "--edition=2021", "-o", str(TMP / f"diff_{target}_rs"), str(rs_src)])

    c_out = sh([str(TMP / f"diff_{target}_c"), N, SEED])
    rs_out = sh([str(TMP / f"diff_{target}_rs"), N, SEED])

    c_lines = c_out.splitlines()
    rs_lines = rs_out.splitlines()
    logging.info("C: %d output lines, Rust: %d output lines", len(c_lines), len(rs_lines))

    mismatches = []
    for i, (c, r) in enumerate(zip(c_lines, rs_lines)):
        if c != r:
            mismatches.append((i, c, r))
    if len(c_lines) != len(rs_lines):
        mismatches.append(("length", len(c_lines), len(rs_lines)))

    if mismatches:
        logging.error("ORACLE 2.5 FAIL: %d mismatches (of %d cases)",
                      len(mismatches), len(c_lines) // 2)
        for i, c, r in mismatches[:10]:
            logging.error("  #%s\n    C:  %s\n    Rs: %s", i, c, r)
        return 1

    logging.info("ORACLE 2.5 PASS: %s — %d cases, %d output lines, byte-identical",
                 target, len(c_lines) // 2, len(c_lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
