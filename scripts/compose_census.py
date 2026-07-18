#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Phase 1 v2.1: compositionality of the tail (corrected, matches census v1.1).

Hypothesis (confirmed in v2.0): a singleton statement family is a novel
*combination* of common sub-patterns, so tail statements cost only "glue".

Corrections from the 2026-07-16 review:
  - statement enumeration now mirrors region_census v1.1 (no macro-expansion
    internals, brace-normalised bodies, same fingerprints);
  - the root node of a singleton is glue BY CONSTRUCTION (its Merkle hash is
    the whole statement, which is unique) — report non-root glue as the
    substantive stat;
  - iterative postorder (no RecursionError on deep expressions);
  - results pickled to tmp/compose_census.pkl as promised.

Output: tmp/compose_census.pkl, tmp/compose_report.md, log tmp/compose_census.log
"""
import hashlib
import json
import logging
import multiprocessing as mp
import os
import pickle
import sys
from collections import Counter
from pathlib import Path

import clang.cindex as ci

sys.path.insert(0, str(Path(__file__).resolve().parent))
from region_census import (  # noqa: E402
    body_children, is_stmt_like, load_entries, stmt_fp, tu_args)

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "compose_census.log"
OUT = REPO / "tmp" / "compose_report.md"
PKL = REPO / "tmp" / "compose_census.pkl"

K = ci.CursorKind
LITERALS = {K.INTEGER_LITERAL, K.FLOATING_LITERAL, K.STRING_LITERAL,
            K.CHARACTER_LITERAL}
SINGLETONS = None  # broadcast to pass-2 workers via fork


def tok(c):
    kind = c.kind
    t = str(kind.value)
    if kind == K.CALL_EXPR:
        t += ":" + (c.spelling or "?")
    elif kind == K.DECL_REF_EXPR:
        ref = c.referenced
        cat = "?"
        if ref is not None:
            rk = ref.kind
            cat = ("fn" if rk == K.FUNCTION_DECL else
                   "parm" if rk == K.PARM_DECL else
                   "var" if rk == K.VAR_DECL else "other")
        t += ":" + cat
    elif kind in LITERALS:
        t = "LIT"
    elif kind in (K.BINARY_OPERATOR, K.COMPOUND_ASSIGNMENT_OPERATOR):
        try:
            t += ":" + c.binary_operator.name
        except AttributeError:
            pass
    elif kind == K.VAR_DECL:
        t += ":" + c.type.spelling
    return t


def hash8(s):
    return int.from_bytes(hashlib.sha1(s.encode()).digest()[:8], "big")


BODY_HASH = hash8("BODY")


def subtree_hashes(stmt):
    """Iterative postorder Merkle hashes: list of per-node hashes.
    Control bodies and compounds pruned to a BODY leaf (as in stmt_fp)."""
    nodes = []
    # two-pass: expand children first via explicit stack of frames
    frames = [[stmt, False, None, None]]  # cursor, pruned, kids, hashes
    while frames:
        fr = frames[-1]
        c, pruned = fr[0], fr[1]
        if pruned or c.kind == K.COMPOUND_STMT:
            nodes.append(BODY_HASH)
            frames.pop()
            if frames and frames[-1][3] is not None:
                frames[-1][3].append(BODY_HASH)
            continue
        if fr[2] is None:
            fr[2] = list(c.get_children())
            fr[3] = []
            bodies = {b.hash for b in body_children(c)}
            fr.append(bodies)
            fr.append(0)
        kids, hashes, bodies, i = fr[2], fr[3], fr[4], fr[5]
        if i < len(kids):
            fr[5] += 1
            frames.append([kids[i], kids[i].hash in bodies, None, None])
            continue
        h = hash8(tok(c) + "(" + ",".join(map(str, hashes)) + ")")
        nodes.append(h)
        frames.pop()
        if frames and frames[-1][3] is not None:
            frames[-1][3].append(h)
    return nodes


def iter_statements(entry):
    """Yield statement cursors exactly as census v1.1 counts them."""
    os.chdir(entry["directory"])
    src = entry["file"]
    try:
        src_lines = open(src, errors="replace").read().splitlines()
    except OSError:
        src_lines = []
    index = ci.Index.create()
    try:
        tu = index.parse(src, args=tu_args(entry))
    except ci.TranslationUnitLoadError:
        return
    def walk_block(children, parent_loc):
        for st in children:
            if st.kind == K.COMPOUND_STMT:
                yield from walk_block(list(st.get_children()), parent_loc)
                continue
            if not is_stmt_like(st):
                continue
            yield from walk_stmt(st, parent_loc)
    def walk_stmt(st, parent_loc):
        loc = (st.location.line, st.location.column)
        if loc == parent_loc:
            return
        yield st, src_lines
        for b in body_children(st):
            if b.kind == K.COMPOUND_STMT:
                yield from walk_block(list(b.get_children()), loc)
            elif is_stmt_like(b):
                yield from walk_stmt(b, loc)
    for fn in tu.cursor.get_children():
        if fn.kind != K.FUNCTION_DECL or not fn.is_definition():
            continue
        if fn.location.file is None or fn.location.file.name != src:
            continue
        for ch in fn.get_children():
            if ch.kind == K.COMPOUND_STMT:
                yield from walk_block(list(ch.get_children()), None)


def pass1(entry):
    c = Counter()
    for st, _ in iter_statements(entry):
        for h in subtree_hashes(st):
            c[h] += 1
    return c


def pass2(entry):
    out = []
    for st, src_lines in iter_statements(entry):
        if stmt_fp(st, src_lines) in SINGLETONS:
            hs = subtree_hashes(st)
            out.append((hs[-1] if hs else 0, hs))  # root is last in postorder
    return out


def init_pass2(singletons):
    global SINGLETONS
    SINGLETONS = singletons


def main() -> int:
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    entries = load_entries(REPO)
    jobs = max(1, mp.cpu_count() - 4)

    logging.info("pass 1: subtree counts over %d TUs", len(entries))
    counts = Counter()
    with mp.Pool(jobs) as pool:
        for i, c in enumerate(pool.imap_unordered(pass1, entries, chunksize=8)):
            counts.update(c)
            if (i + 1) % 600 == 0:
                logging.info("pass1 %d/%d TUs, %d distinct subtrees",
                             i + 1, len(entries), len(counts))
    logging.info("pass 1 done: %d distinct subtrees, %d instances",
                 len(counts), sum(counts.values()))

    rc = pickle.load(open(REPO / "tmp" / "region_census.pkl", "rb"))
    singletons = {fp for fp, n in rc["stmts"].items() if n == 1}
    logging.info("pass 2: %d singleton stmt families", len(singletons))

    structs = []
    with mp.Pool(jobs, initializer=init_pass2,
                 initargs=(singletons,)) as pool:
        for i, res in enumerate(pool.imap_unordered(pass2, entries, chunksize=8)):
            structs.extend(res)
            if (i + 1) % 600 == 0:
                logging.info("pass2 %d/%d TUs, %d singleton stmts",
                             i + 1, len(entries), len(structs))
    logging.info("pass 2 done: %d singleton statements", len(structs))

    lines = []
    w = lines.append
    w("# Phase 1 v2.1 — compositionality of the tail (corrected)")
    w("")
    w(f"- corpus subtree instances: {sum(counts.values()):,}; distinct: "
      f"{len(counts):,}")
    w(f"- singleton statements analysed: {len(structs):,}")
    w("- glue = node whose subtree family has < T instances corpus-wide;")
    w("  the ROOT of a singleton is glue by construction and reported "
      "separately (review finding 5).")
    w("")
    summary = {}
    for T in (5, 10, 50):
        glue_dist = Counter()
        covered_nodes = total_nodes = 0
        for root_h, hs in structs:
            glue = sum(1 for h in hs if counts[h] < T)
            nonroot_glue = glue - (1 if counts[root_h] < T else 0)
            total_nodes += len(hs)
            covered_nodes += len(hs) - glue
            glue_dist[nonroot_glue] += 1
        n = len(structs)
        cum = 0
        med = p90 = None
        for g in sorted(glue_dist):
            cum += glue_dist[g]
            if med is None and cum >= n / 2:
                med = g
            if p90 is None and cum >= n * 0.9:
                p90 = g
        for lim in (0, 1, 3, 5, 10):
            cov = sum(v for g, v in glue_dist.items() if g <= lim)
            w(f"- T={T}: non-root glue ≤ {lim} for {100*cov/n:.1f}% of "
              f"singleton stmts")
        w(f"- T={T}: median non-root glue {med}, p90 {p90}; node coverage "
          f"{100*covered_nodes/total_nodes:.1f}%")
        w("")
        summary[T] = dict(glue_dist=dict(glue_dist), median=med, p90=p90,
                          node_coverage=covered_nodes / total_nodes)
    OUT.write_text("\n".join(lines) + "\n")
    with open(PKL, "wb") as f:
        pickle.dump({"summary": summary, "n_singletons": len(structs),
                     "distinct_subtrees": len(counts),
                     "subtree_instances": sum(counts.values())}, f)
    logging.info("wrote %s and %s", OUT, PKL)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
