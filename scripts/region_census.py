#!/usr/bin/env python3
"""Phase 1 v1: statement/region-level fingerprint census — the real gate.

v0 showed whole functions don't collapse. This measures where the thesis
says the collapse lives: statements and short statement sequences.

Units:
  statement  — any cursor in statement position (parent is a compound stmt).
               Fingerprint = normalised preorder walk of its subtree with
               nested COMPOUND_STMT children pruned to a BODY token, so
               `if (ret < 0) { ...20 lines... }` and
               `if (err < 0) { ...3 lines... }` share the family
               IF(cmp(var,LIT)<0)→BODY. Atomic statements (no compound
               children) fingerprint their full subtree.
  bigram/trigram — consecutive sibling statement fingerprints inside one
               compound: composite regions (lock;access;unlock, goto
               ladders, init sequences).

Normalisation (as v0 fp_exact): identifiers→ref category, literals→LIT,
callee names KEPT (semantic labels).

The gate: how many statement families cover 50/80/95% of all instances?

Output: tmp/region_census.pkl (counters), log tmp/region_census.log.
Report: scripts/region_report.py.
"""
import json
import logging
import multiprocessing as mp
import os
import pickle
import shlex
import sys
from collections import Counter
from pathlib import Path

import clang.cindex as ci

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "region_census.log"
OUT = REPO / "tmp" / "region_census.pkl"

K = ci.CursorKind
STMT_PARENT = K.COMPOUND_STMT
LITERALS = {K.INTEGER_LITERAL, K.FLOATING_LITERAL, K.STRING_LITERAL,
            K.CHARACTER_LITERAL}


def tu_args(entry):
    src = os.path.realpath(entry["file"])
    argv = shlex.split(entry["command"])
    args, skip = [], False
    for a in argv[1:]:
        if skip:
            skip = False
            continue
        if a == "-o":
            skip = True
            continue
        if a == "-c":
            continue
        if not a.startswith("-") and \
                os.path.realpath(os.path.join(entry["directory"], a)) == src:
            continue
        args.append(a)
    return args


def stmt_fp(cursor):
    """Normalised fingerprint of one statement, compound children pruned."""
    toks = []
    stack = [cursor]
    n = 0
    while stack and n < 5000:
        c = stack.pop()
        n += 1
        kind = c.kind
        if kind == K.COMPOUND_STMT:
            toks.append("BODY")
            continue  # prune
        tok = str(kind.value)
        if kind == K.CALL_EXPR:
            tok += ":" + (c.spelling or "?")
        elif kind == K.DECL_REF_EXPR:
            ref = c.referenced
            cat = "?"
            if ref is not None:
                rk = ref.kind
                cat = ("fn" if rk == K.FUNCTION_DECL else
                       "parm" if rk == K.PARM_DECL else
                       "var" if rk == K.VAR_DECL else "other")
            tok += ":" + cat
        elif kind in LITERALS:
            tok = "LIT"
        elif kind == K.BINARY_OPERATOR:
            try:
                tok += ":" + c.binary_operator.name
            except AttributeError:
                pass
        toks.append(tok)
        stack.extend(reversed(list(c.get_children())))
    if n >= 5000:
        toks.append("TRUNC")
    return "\x00".join(toks)


def walk_compounds(cursor):
    """Yield every COMPOUND_STMT under cursor (including nested)."""
    stack = [cursor]
    while stack:
        c = stack.pop()
        if c.kind == K.COMPOUND_STMT:
            yield c
        stack.extend(c.get_children())


def snippet(src_lines, extent):
    try:
        line = src_lines[extent.start.line - 1].strip()
        return line[:100]
    except IndexError:
        return "?"


def process(entry):
    os.chdir(entry["directory"])
    src = entry["file"]
    rel = os.path.relpath(src, entry["directory"])
    stmts, bi, tri = Counter(), Counter(), Counter()
    exemplars = {}
    try:
        src_lines = open(src, errors="replace").read().splitlines()
    except OSError:
        src_lines = []
    index = ci.Index.create()
    try:
        tu = index.parse(src, args=tu_args(entry))
    except ci.TranslationUnitLoadError:
        return rel, None
    for fn in tu.cursor.get_children():
        if fn.kind != K.FUNCTION_DECL or not fn.is_definition():
            continue
        if fn.location.file is None or fn.location.file.name != src:
            continue
        for comp in walk_compounds(fn):
            seq = []
            for st in comp.get_children():
                if st.kind == K.DECL_STMT or st.kind.is_statement() or \
                        st.kind.is_expression():
                    fp = stmt_fp(st)
                    seq.append(fp)
                    stmts[fp] += 1
                    if fp not in exemplars:
                        exemplars[fp] = (rel, st.location.line,
                                         snippet(src_lines, st.extent))
            for i in range(len(seq) - 1):
                bi[(seq[i], seq[i + 1])] += 1
            for i in range(len(seq) - 2):
                tri[(seq[i], seq[i + 1], seq[i + 2])] += 1
    return rel, (stmts, bi, tri, exemplars)


def main() -> int:
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    cc = json.load(open(REPO / "linux" / "compile_commands.json"))
    entries = sorted((e for e in cc if e["file"].endswith(".c")),
                     key=lambda e: e["file"])
    jobs = max(1, mp.cpu_count() - 4)
    logging.info("region census over %d TUs, %d workers", len(entries), jobs)

    stmts, bi, tri = Counter(), Counter(), Counter()
    exemplars = {}
    nbad = 0
    with mp.Pool(jobs) as pool:
        for i, (rel, res) in enumerate(
                pool.imap_unordered(process, entries, chunksize=4)):
            if res is None:
                nbad += 1
                logging.warning("parse failed: %s", rel)
                continue
            s, b, t, ex = res
            stmts.update(s)
            bi.update(b)
            tri.update(t)
            for fp, e in ex.items():
                exemplars.setdefault(fp, e)
            if (i + 1) % 400 == 0:
                logging.info("%d/%d TUs; %d stmt instances, %d families",
                             i + 1, len(entries), sum(stmts.values()),
                             len(stmts))
    logging.info("DONE: %d stmt instances / %d families; %d bigrams / %d; "
                 "%d trigrams / %d; %d TUs failed",
                 sum(stmts.values()), len(stmts), sum(bi.values()), len(bi),
                 sum(tri.values()), len(tri), nbad)
    with open(OUT, "wb") as f:
        pickle.dump({"stmts": stmts, "bi": bi, "tri": tri,
                     "exemplars": exemplars}, f)
    logging.info("wrote %s", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
