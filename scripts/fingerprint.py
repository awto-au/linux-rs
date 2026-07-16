#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Phase 1: per-function AST fingerprints over the pinned corpus.

For every C TU in linux/compile_commands.json, parse with libclang using the
TU's real kernel flags, and emit one JSONL record per function *defined in
that file*: two fingerprints plus feature flags.

  fp_exact : sha1 of preorder (node-kind, callee-name, ref-category) sequence
             — identifiers/literals normalised away, callee names KEPT
             (macro/API names are semantic labels).
  fp_shape : sha1 of node-kind sequence only — pure structural shape.

Aggregation/report is scripts/census_report.py. Post-expansion AST only:
macro ancestry is a documented v0 limitation (mining engine's job later).

Usage: fingerprint.py [--limit N] [--jobs N]
Output: tmp/functions.jsonl, log tmp/fingerprint.log
"""
import argparse
import hashlib
import json
import logging
import multiprocessing as mp
import os
import shlex
import sys
from pathlib import Path

import clang.cindex as ci

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "fingerprint.log"
OUT = REPO / "tmp" / "functions.jsonl"

K = ci.CursorKind
DROP_ARGS = {"-c", "-nostdinc"}  # -nostdinc kept actually; placeholder set below


def tu_args(entry):
    """Extract clang args from a compile_commands entry, minus argv0/-c/-o/src.

    The source appears in the command as a path relative to `directory` while
    entry["file"] is absolute — compare resolved paths, not strings.
    """
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


FEATURES = {
    "asm": {K.ASM_STMT},
    "goto": {K.GOTO_STMT, K.INDIRECT_GOTO_STMT},
    "switch": {K.SWITCH_STMT},
    "loop": {K.FOR_STMT, K.WHILE_STMT, K.DO_STMT},
}


def fingerprint(cursor):
    exact, shape = [], []
    feats = set()
    ncalls = 0
    callees = set()
    stack = [cursor]
    n = 0
    while stack:
        c = stack.pop()
        n += 1
        if n > 200000:  # runaway guard (giant generated functions)
            feats.add("truncated")
            break
        kind = c.kind
        shape.append(kind.value)
        tok = str(kind.value)
        if kind == K.CALL_EXPR:
            ncalls += 1
            name = c.spelling or "?"
            callees.add(name)
            tok += ":" + name
        elif kind == K.DECL_REF_EXPR:
            ref = c.referenced
            cat = "?"
            if ref is not None:
                rk = ref.kind
                cat = ("fn" if rk == K.FUNCTION_DECL else
                       "parm" if rk == K.PARM_DECL else
                       "var" if rk == K.VAR_DECL else "other")
            tok += ":" + cat
        elif kind in (K.INTEGER_LITERAL, K.FLOATING_LITERAL,
                      K.STRING_LITERAL, K.CHARACTER_LITERAL):
            tok = "LIT"
        elif kind in (K.BINARY_OPERATOR, K.COMPOUND_ASSIGNMENT_OPERATOR):
            try:
                tok += ":" + c.binary_operator.name
            except AttributeError:
                pass
        exact.append(tok)
        for fname, kinds in FEATURES.items():
            if kind in kinds:
                feats.add(fname)
        stack.extend(reversed(list(c.get_children())))
    h = lambda seq: hashlib.sha1("\x00".join(map(str, seq)).encode()).hexdigest()[:16]
    return {
        "fp_exact": h(exact),
        "fp_shape": h(shape),
        "nodes": len(shape),
        "ncalls": ncalls,
        "callees": sorted(callees)[:50],
        "features": sorted(feats),
    }


def process(entry):
    os.chdir(entry["directory"])
    src = entry["file"]
    rel = os.path.relpath(src, entry["directory"])
    index = ci.Index.create()
    recs, nerr = [], 0
    try:
        tu = index.parse(src, args=tu_args(entry))
    except ci.TranslationUnitLoadError as e:
        return rel, [], f"parse-failed: {e}"
    nerr = sum(1 for d in tu.diagnostics if d.severity >= ci.Diagnostic.Error)
    for c in tu.cursor.get_children():
        if c.kind != K.FUNCTION_DECL or not c.is_definition():
            continue
        if c.location.file is None or c.location.file.name != src:
            continue
        r = fingerprint(c)
        r.update(name=c.spelling, file=rel, line=c.location.line,
                 static=c.storage_class == ci.StorageClass.STATIC)
        recs.append(r)
    return rel, recs, f"{nerr} errors" if nerr else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=max(1, mp.cpu_count() - 4))
    args = ap.parse_args()

    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    cc = json.load(open(REPO / "linux" / "compile_commands.json"))
    seen = set()
    entries = []
    for e in cc:
        if e["file"].endswith(".c") and e["file"] not in seen:
            seen.add(e["file"])
            entries.append(e)
    entries.sort(key=lambda e: e["file"])
    if args.limit:
        entries = entries[: args.limit]
    logging.info("fingerprinting %d TUs with %d workers", len(entries), args.jobs)

    nfun = nbad = 0
    with open(OUT, "w") as out, mp.Pool(args.jobs) as pool:
        for i, (rel, recs, err) in enumerate(
                pool.imap_unordered(process, entries, chunksize=4)):
            if err and not recs:
                nbad += 1
                logging.warning("%s: %s", rel, err)
            for r in recs:
                out.write(json.dumps(r) + "\n")
            nfun += len(recs)
            if (i + 1) % 200 == 0:
                logging.info("%d/%d TUs, %d functions, %d failed TUs",
                             i + 1, len(entries), nfun, nbad)
    logging.info("DONE: %d functions from %d TUs (%d TUs failed) -> %s",
                 nfun, len(entries), nbad, OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
