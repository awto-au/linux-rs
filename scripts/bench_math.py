#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build and run the host math benchmark: C originals (clang -O2) vs
faithful Rust vs optimised Rust (rustc -O).

Output lines from both programs: impl,func,ns_per_op,acc
Output: table on stdout + tmp/bench_math.log; raw CSV tmp/bench_math.csv.
Host-indicative only; target numbers come from FPGA/QEMU-icount later.
"""
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = REPO / "tmp"
LOG = TMP / "bench_math.log"
N = "10000000"


def run(cmd):
    logging.info("$ %s", " ".join(map(str, cmd)))
    return subprocess.run(cmd, check=True, text=True,
                          capture_output=True).stdout


def main() -> int:
    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    run(["clang", "-O2", "-o", str(TMP / "cref"), str(REPO / "bench/cref.c")])
    run(["rustc", "-O", "--edition=2021", "-o", str(TMP / "benchrs"),
         str(REPO / "bench/bench.rs")])

    csv_lines = []
    accs = {}
    funcs = {}
    for exe in (TMP / "cref", TMP / "benchrs"):
        for line in run([str(exe), N]).splitlines():
            csv_lines.append(line)
            parts = line.split(",")
            if parts[0] == "equivalence":
                logging.info("equivalence check: %s over %s inputs",
                             parts[1], parts[2])
                continue
            impl, func, ns, acc = parts[0], parts[1], float(parts[2]), parts[3]
            funcs.setdefault(func, {})[impl] = ns
            accs.setdefault((func, impl), acc)
    (TMP / "bench_math.csv").write_text("\n".join(csv_lines) + "\n")

    # Cross-implementation checksum agreement (same LCG stream => the
    # accumulated results must be identical for the same func).
    # Rule 0011 cites checksum agreement as mandatory methodology — a
    # MISMATCH means the implementations are not computing the same
    # function over the same input stream, so the timing numbers are
    # not comparable.  Fail the run rather than silently log and return 0.
    checksum_mismatches = []
    for func in funcs:
        vals = {accs[(func, impl)] for impl in funcs[func]}
        if len(vals) == 1:
            logging.info("checksum %-10s AGREE", func)
        else:
            logging.error("checksum %-10s MISMATCH %s", func, vals)
            checksum_mismatches.append(func)

    logging.info("%-10s %10s %14s %10s %8s %8s", "func", "C ns/op",
                 "faithful ns/op", "opt ns/op", "faith/C", "opt/C")
    for func, d in funcs.items():
        c = d.get("c")
        f = d.get("rust-faithful")
        o = d.get("rust-opt")
        logging.info("%-10s %10.2f %14.2f %10s %8s %8s", func,
                     c if c is not None else float("nan"),
                     f if f is not None else float("nan"),
                     f"{o:.2f}" if o is not None else "—",
                     f"{f / c:.2f}x" if c and f else "—",
                     f"{o / c:.2f}x" if c and o else "—")
    if checksum_mismatches:
        logging.error("FAIL: checksum mismatch for %d function(s): %s",
                      len(checksum_mismatches), checksum_mismatches)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
