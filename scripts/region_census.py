#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Phase 1 v1.1: statement/region-level fingerprint census (corrected).

v1.0 counted post-expansion clang-AST statements naively. The 2026-07-16
correctness review found three material biases, fixed here:

 1. Macro internals: statements inside GNU statement-expressions and
    do{}while(0) expansions were counted as independent instances (27% of
    all instances). Now: never descend into expressions; a nested statement
    whose (file,line,col) equals its parent's is macro-internal — skipped.
    Macro-generated shells (root token at the source location is an
    identifier, not the expected keyword) become one `MACROSTMT:<name>`
    instance — the macro *invocation* is the unit, labelled semantically.
 2. Brace style: `if (x) foo();` vs `if (x) { foo(); }` landed in different
    families and the unbraced body statement was never counted (59% of
    ifs!). Now: control-statement bodies are always pruned to BODY in the
    parent fingerprint and always emitted as statements themselves.
 3. Type erasure: local declarations now carry the declared type
    (`VAR_DECL:<type>`), and compound-assignment operators their opcode.

Bigrams/trigrams: consecutive sibling statements within one braced block.

Usage: region_census.py   Output: tmp/region_census.pkl, log tmp/region_census.log
Report: scripts/region_report.py
"""
import json
import logging
import multiprocessing as mp
import os
import pickle
import re
import shlex
import sys
from collections import Counter
from pathlib import Path

import clang.cindex as ci

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "region_census.log"
OUT = REPO / "tmp" / "region_census.pkl"

K = ci.CursorKind
LITERALS = {K.INTEGER_LITERAL, K.FLOATING_LITERAL, K.STRING_LITERAL,
            K.CHARACTER_LITERAL}
# control kinds -> which children are body statements (pruned + re-emitted)
#   'last' = last child; IF: all children after the condition; DO: first.
CONTROL_KEYWORD = {K.IF_STMT: "if", K.DO_STMT: "do", K.WHILE_STMT: "while",
                   K.FOR_STMT: "for", K.SWITCH_STMT: "switch"}
MACRO_DETECT = set(CONTROL_KEYWORD) | {K.StmtExpr, K.NULL_STMT}
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def tu_args(entry):
    """Clang args from a compile_commands entry, minus argv0/-c/-o/source.

    The source appears in `command` relative to `directory` while
    entry['file'] is absolute — compare resolved paths, not strings.
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


def load_entries(repo, tree="linux"):
    cc = json.load(open(repo / tree / "compile_commands.json"))
    seen, entries = set(), []
    for e in cc:
        if not e["file"].endswith(".c") or e["file"] in seen:
            continue
        seen.add(e["file"])
        entries.append(e)
    entries.sort(key=lambda e: e["file"])
    return entries


def body_children(c):
    """The children of a control statement that are its body/branches."""
    kids = list(c.get_children())
    kind = c.kind
    if kind == K.IF_STMT:
        return kids[1:]
    if kind == K.DO_STMT:
        return kids[:1]
    if kind in (K.WHILE_STMT, K.FOR_STMT, K.SWITCH_STMT,
                K.CASE_STMT, K.DEFAULT_STMT, K.LABEL_STMT):
        return kids[-1:] if kids else []
    return []


def source_ident(src_lines, line, col):
    """Identifier starting exactly at (line, col) in the source, or None."""
    try:
        m = IDENT.match(src_lines[line - 1][col - 1:])
    except IndexError:
        return None
    return m.group(0) if m else None


def macro_name(c, src_lines):
    """If this statement is a macro expansion shell, the macro's name."""
    if c.kind not in MACRO_DETECT:
        return None
    ident = source_ident(src_lines, c.location.line, c.location.column)
    if ident is None:
        return None
    if ident == CONTROL_KEYWORD.get(c.kind):
        return None
    return ident


def stmt_fp(cursor, src_lines):
    """Normalised statement fingerprint. Control bodies pruned to BODY;
    macro shells collapse to MACROSTMT:<name>."""
    name = macro_name(cursor, src_lines)
    if name is not None:
        return f"MACROSTMT:{name}"
    toks = []
    stack = [(cursor, False)]
    n = 0
    while stack and n < 5000:
        c, pruned = stack.pop()
        n += 1
        if pruned or c.kind == K.COMPOUND_STMT:
            toks.append("BODY")
            continue
        kind = c.kind
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
        elif kind in (K.BINARY_OPERATOR, K.COMPOUND_ASSIGNMENT_OPERATOR):
            try:
                tok += ":" + c.binary_operator.name
            except AttributeError:
                pass
        elif kind == K.VAR_DECL:
            tok += ":" + c.type.spelling
        toks.append(tok)
        bodies = {b.hash for b in body_children(c)}
        for ch in reversed(list(c.get_children())):
            stack.append((ch, ch.hash in bodies))
    if n >= 5000:
        toks.append("TRUNC")
    return "\x00".join(toks)


def is_stmt_like(c):
    return c.kind == K.DECL_STMT or c.kind.is_statement() or \
        c.kind.is_expression()


class Harvester:
    def __init__(self, rel, src_lines):
        self.rel = rel
        self.src_lines = src_lines
        self.stmts = Counter()
        self.bi = Counter()
        self.tri = Counter()
        self.exemplars = {}

    def snippet(self, st):
        try:
            return self.src_lines[st.location.line - 1].strip()[:100]
        except IndexError:
            return "?"

    def block(self, children, parent_loc):
        """A braced block (or fn body): emit each child, collect n-grams."""
        seq = []
        for st in children:
            if st.kind == K.COMPOUND_STMT:  # bare nested {} — flatten
                self.block(list(st.get_children()), parent_loc)
                continue
            if not is_stmt_like(st):
                continue
            fp = self.stmt(st, parent_loc)
            if fp is not None:
                seq.append(fp)
        for i in range(len(seq) - 1):
            self.bi[(seq[i], seq[i + 1])] += 1
        for i in range(len(seq) - 2):
            self.tri[(seq[i], seq[i + 1], seq[i + 2])] += 1

    def stmt(self, st, parent_loc):
        loc = (st.location.line, st.location.column)
        if loc == parent_loc:
            return None  # macro-internal: same expansion point as parent
        fp = stmt_fp(st, self.src_lines)
        self.stmts[fp] += 1
        if fp not in self.exemplars:
            self.exemplars[fp] = (self.rel, st.location.line, self.snippet(st))
        for b in body_children(st):
            if b.kind == K.COMPOUND_STMT:
                self.block(list(b.get_children()), loc)
            elif is_stmt_like(b):
                self.stmt(b, loc)
        return fp


def process(entry):
    os.chdir(entry["directory"])
    src = entry["file"]
    rel = os.path.relpath(src, entry["directory"])
    try:
        src_lines = open(src, errors="replace").read().splitlines()
    except OSError:
        src_lines = []
    index = ci.Index.create()
    try:
        tu = index.parse(src, args=tu_args(entry))
    except ci.TranslationUnitLoadError:
        return rel, None
    h = Harvester(rel, src_lines)
    for fn in tu.cursor.get_children():
        if fn.kind != K.FUNCTION_DECL or not fn.is_definition():
            continue
        if fn.location.file is None or fn.location.file.name != src:
            continue
        for ch in fn.get_children():
            if ch.kind == K.COMPOUND_STMT:
                h.block(list(ch.get_children()), None)
    return rel, (h.stmts, h.bi, h.tri, h.exemplars)


def main() -> int:
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    entries = load_entries(REPO)
    jobs = max(1, mp.cpu_count() - 4)
    logging.info("region census v1.1 over %d TUs, %d workers", len(entries), jobs)

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
