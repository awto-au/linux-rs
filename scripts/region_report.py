#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Phase 1 v1: coverage curves + top families from tmp/region_census.pkl."""
import logging
import pickle
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IN = REPO / "tmp" / "region_census.pkl"
OUT = REPO / "tmp" / "region_report.md"
LOG = REPO / "tmp" / "region_report.log"


def coverage(counter, targets=(0.25, 0.5, 0.8, 0.95)):
    total = sum(counter.values())
    out, cum, n = [], 0, 0
    todo = list(targets)
    for _, size in counter.most_common():
        cum += size
        n += 1
        while todo and cum >= todo[0] * total:
            out.append((todo.pop(0), n))
    for t in todo:
        out.append((t, None))
    return out, total


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    d = pickle.load(open(IN, "rb"))
    stmts, bi, tri, ex = d["stmts"], d["bi"], d["tri"], d["exemplars"]

    lines = []
    w = lines.append
    w("# Phase 1 v1 — statement/region coverage (the gate)")
    w("")
    for name, c in (("statements", stmts), ("bigrams", bi), ("trigrams", tri)):
        cov, total = coverage(c)
        singles = sum(1 for v in c.values() if v == 1)
        w(f"## {name}")
        w("")
        w(f"- instances: **{total:,}**, families: **{len(c):,}** "
          f"(collapse {100*(total-len(c))/total:.1f}%)")
        w(f"- singleton families (seen once): {singles:,} "
          f"({100*singles/len(c):.1f}% of families, "
          f"{100*singles/total:.1f}% of instances)")
        w("")
        w("| corpus fraction | families needed |")
        w("|---|---|")
        for t, n in cov:
            w(f"| {int(t*100)}% | {n if n else 'n/a':,} |" if n else
              f"| {int(t*100)}% | n/a |")
        w("")
    w("## Top 40 statement families")
    w("")
    w("| count | exemplar (first occurrence) |")
    w("|---|---|")
    for fp, n in stmts.most_common(40):
        f, l, snip = ex.get(fp, ("?", 0, "?"))
        snip = snip.replace("|", "\\|")
        w(f"| {n:,} | `{snip}` — {f}:{l} |")
    OUT.write_text("\n".join(lines) + "\n")
    logging.info("wrote %s", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
