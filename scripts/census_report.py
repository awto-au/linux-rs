#!/usr/bin/env python3
"""Phase 1: aggregate tmp/functions.jsonl into the pattern census report.

Answers the go/no-go question: how far does the corpus collapse when
functions are grouped by normalised AST fingerprint?

  - fp_exact: kinds + callee names + ref categories (strictest — a cluster
    here is "same code modulo identifiers/literals": rule-ready)
  - fp_shape: kinds only (structural families)

Output: tmp/census_report.md (+ log tmp/census_report.log)
"""
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IN = REPO / "tmp" / "functions.jsonl"
OUT = REPO / "tmp" / "census_report.md"
LOG = REPO / "tmp" / "census_report.log"


def coverage(counter, total, targets=(0.5, 0.8, 0.95)):
    """How many clusters (largest first) cover each target fraction?"""
    out, cum, n = {}, 0, 0
    todo = list(targets)
    for _, size in counter.most_common():
        cum += size
        n += 1
        while todo and cum >= todo[0] * total:
            out[todo.pop(0)] = n
    for t in todo:
        out[t] = None
    return out


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    recs = [json.loads(l) for l in open(IN)]
    total = len(recs)
    exact, shape = Counter(), Counter()
    exemplar = {}
    feat = Counter()
    sizes = defaultdict(list)
    for r in recs:
        exact[r["fp_exact"]] += 1
        shape[r["fp_shape"]] += 1
        exemplar.setdefault(r["fp_exact"], r)
        for f in r["features"]:
            feat[f] += 1
        sizes[r["fp_exact"]].append(r["nodes"])

    lines = []
    w = lines.append
    w("# Phase 1 census (v0: exact-fingerprint lower bound)")
    w("")
    w(f"- functions: **{total}**")
    w(f"- distinct fp_exact: **{len(exact)}** "
      f"({total - len(exact)} functions are identifier-renamed duplicates of "
      f"another: {100*(total-len(exact))/total:.1f}%)")
    w(f"- distinct fp_shape: **{len(shape)}** "
      f"(structural collapse: {100*(total-len(shape))/total:.1f}%)")
    w("")
    for name, c in (("fp_exact", exact), ("fp_shape", shape)):
        cov = coverage(c, total)
        w(f"## Coverage curve — {name}")
        w("")
        w("| corpus fraction | clusters needed |")
        w("|---|---|")
        for t, n in cov.items():
            w(f"| {int(t*100)}% | {n if n else 'n/a'} |")
        w("")
    w("## Multi-member fp_exact clusters (top 25 by size)")
    w("")
    w("| size | median nodes | exemplar |")
    w("|---|---|---|")
    for fp, n in exact.most_common(25):
        ex = exemplar[fp]
        med = sorted(sizes[fp])[len(sizes[fp]) // 2]
        w(f"| {n} | {med} | `{ex['name']}` {ex['file']}:{ex['line']} |")
    w("")
    dup_fns = sum(n for _, n in exact.items() if n > 1)
    multi = sum(1 for _, n in exact.items() if n > 1)
    w(f"- functions living in multi-member exact clusters: {dup_fns} "
      f"({100*dup_fns/total:.1f}%) across {multi} clusters")
    w("")
    w("## Feature prevalence")
    w("")
    w("| feature | functions | % |")
    w("|---|---|---|")
    for f, n in feat.most_common():
        w(f"| {f} | {n} | {100*n/total:.1f}% |")
    w("")
    w("## Callee vocabulary concentration")
    w("")
    vocab = Counter()
    for r in recs:
        vocab.update(r["callees"])
    w(f"- distinct callee names (all): **{len(vocab)}**")
    defined = defaultdict(set)
    for r in recs:
        defined[r["file"]].add(r["name"])
    ext_vocab = Counter()
    ext_calls = {}
    for i, r in enumerate(recs):
        ext = [c for c in r["callees"] if c not in defined[r["file"]]]
        ext_calls[i] = ext
        ext_vocab.update(ext)
    w(f"- distinct *external* callee names (same-file callees excluded — "
      f"those translate with their file): **{len(ext_vocab)}**")
    w("")
    w("| vocabulary size (top-K external callees) | functions fully covered | % |")
    w("|---|---|---|")
    ranked = [name for name, _ in ext_vocab.most_common()]
    for k in (100, 250, 500, 1000, 2000, 5000, 10000):
        top = set(ranked[:k])
        n = sum(1 for i, r in enumerate(recs) if all(c in top for c in ext_calls[i]))
        w(f"| {k} | {n} | {100*n/total:.1f}% |")
    w("")
    w("(a function is 'fully covered' when every *external* function it calls"
      " is in the top-K vocabulary — i.e. K mapped APIs suffice for its"
      " cross-file call surface)")
    OUT.write_text("\n".join(lines) + "\n")
    logging.info("wrote %s", OUT)
    print("\n".join(lines[:14]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
