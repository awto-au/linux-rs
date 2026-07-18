#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Rank untranslated TUs by translation readiness.

Vocabulary transfer measure: the already-translated TUs (see
translated_tus() below — derived from <name>_rs.rs files in the
worktree, so this always reflects current count, not a number frozen
at write time) define a covered token vocabulary (normalised
statement-fingerprint tokens: node kinds, callee names, macro names,
types). A candidate TU's readiness = fraction of its statements whose
every token is already in that vocabulary — i.e. nothing in the
statement is a construct we haven't already translated once.

Usage: readiness.py [--tree linux-riscv] [--glob 'lib/**/*.c']
Output: table on stdout, log tmp/readiness.log
"""
import argparse
import fnmatch
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from region_census import load_entries, process  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "readiness.log"

def translated_tus(tree: Path):
    """Derive the translated set from <name>_rs.rs files in the worktree."""
    return sorted(
        str(f.relative_to(tree)).replace("_rs.rs", ".c")
        for f in tree.glob("**/*_rs.rs")
    )


def toks(fp):
    return set(fp.split("\x00"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=os.environ.get("LINUXRS_TREE", "linux-riscv"))
    ap.add_argument("--glob", default="lib/*.c")
    args = ap.parse_args()

    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    entries = load_entries(REPO, tree=args.tree)
    by_rel = {}
    for e in entries:
        rel = e["file"].split(f"/{args.tree}/")[-1]
        by_rel[rel] = e

    translated = translated_tus(REPO / args.tree)
    vocab = set()
    for rel in translated:
        e = by_rel.get(rel)
        if e is None:
            logging.warning("translated TU not in corpus: %s", rel)
            continue
        _, res = process(e)
        if res:
            for fp in res[0]:
                vocab |= toks(fp)
    logging.info("covered vocabulary: %d tokens from %d translated TUs",
                 len(vocab), len(translated))

    rows = []
    for rel, e in sorted(by_rel.items()):
        if rel in translated or not fnmatch.fnmatch(rel, args.glob):
            continue
        _, res = process(e)
        if not res:
            continue
        stmts = res[0]
        total = sum(stmts.values())
        if total == 0:
            continue
        covered = sum(n for fp, n in stmts.items() if toks(fp) <= vocab)
        new_toks = set()
        for fp in stmts:
            new_toks |= toks(fp) - vocab
        rows.append((covered / total, total, len(new_toks), rel))

    rows.sort(reverse=True)
    logging.info("%-40s %9s %7s %9s", "TU", "readiness", "stmts", "new toks")
    for ready, total, newt, rel in rows[:25]:
        logging.info("%-40s %8.1f%% %7d %9d", rel, 100 * ready, total, newt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
