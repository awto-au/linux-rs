#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""List every kernel::warn_on! site the fix classes rewrote: raw c2rust vs fixed.

- Raw: tmp/c2rust-baseline/<slug>/output/src/*.rs; fixed: the last sweep's
  tmp/c2rust-sweep-scratch/<slug>/*.rs (run sweep_c2rust_corpus.py first).
- Sites pair up by order (fix classes never add/remove a warn_on! call).
- For checking each rewritten condition against the C (issues #100, #121).

Usage: audit_warn_on_sites.py --c2rust-rev REV [--all] [C_FILE ...]
Log: tmp/logs/audit_warn_on_sites.log
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import REPO, make_log  # noqa: E402
from c2rust_output_check_common import find_clean_outputs  # noqa: E402

DB = REPO / "rulesdb" / "patterns.db"
BASELINE = REPO / "tmp" / "c2rust-baseline"
SCRATCH = REPO / "tmp" / "c2rust-sweep-scratch"
OPEN_RE = re.compile(r"kernel::warn_on!\(")
TAIL_RE = re.compile(r"\s*(?:(?:!=|==)\s*0(?![\w.])(?:\s+as\s+[\w:]+)?)?")

log = make_log("audit_warn_on_sites", stamped=False)


def sites(text: str) -> list[tuple[int, str]]:
    """(line, `[!]warn_on!(arg)<trailing ==/!= 0>`) for each call, whitespace-collapsed."""
    out = []
    for m in OPEN_RE.finditer(text):
        depth, i = 0, m.end() - 1
        while i < len(text):
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
            if depth == 0:
                break
        i = TAIL_RE.match(text, i).end()
        neg = "!" if text[: m.start()].rstrip().endswith("!") else ""
        out.append((text.count("\n", 0, m.start()) + 1, neg + " ".join(text[m.start() : i].split())))
    return out


def classify(raw: str, fixed: str) -> str:
    """same | drop-neq0 | negate-eq0 | OTHER (OTHER = arg changed: check against the C)."""
    if raw == fixed:
        return "same"
    base = re.sub(r"\s*(!=|==)\s*0$", "", raw)
    if raw.endswith("!= 0") and fixed == base:
        return "drop-neq0"
    if raw.endswith("== 0") and fixed == "!" + base:
        return "negate-eq0"
    return "OTHER"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--c2rust-rev", required=True)
    ap.add_argument("--all", action="store_true", help="also list unchanged sites")
    ap.add_argument("c_files", nargs="*")
    args = ap.parse_args()

    rc, kinds = 0, {}
    for rs, c_file in find_clean_outputs(DB, BASELINE, args.c2rust_rev):
        if args.c_files and c_file not in args.c_files:
            continue
        fixed = SCRATCH / c_file.replace("/", "_") / rs.name
        if not fixed.exists():
            log(f"{c_file}: no sweep scratch copy at {fixed.relative_to(REPO)}")
            rc = 1
            continue
        raw_s, fix_s = sites(rs.read_text()), sites(fixed.read_text())
        if len(raw_s) != len(fix_s):
            log(f"{c_file}: site count differs raw={len(raw_s)} fixed={len(fix_s)} -- pair by hand")
            rc = 1
            continue
        for (_rl, r), (fl, f) in zip(raw_s, fix_s):
            if r != f or args.all:
                kind = classify(r, f)
                kinds[kind] = kinds.get(kind, 0) + 1
                log(f"{c_file}:{fl} [{kind}]\n  raw:   {r}\n  fixed: {f}")
    log(f"DONE: {kinds}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
