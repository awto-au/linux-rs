#!/usr/bin/env python3
"""Phase 1 v2: compositionality of the tail.

Hypothesis (Dan): the expensive-looking tail isn't expensive — a singleton
statement family is usually a novel *combination* of common sub-patterns,
so once the head families are ruled, tail statements cost only "glue".

Measure: two passes over the corpus.
  pass 1 — count every statement-internal subtree fingerprint corpus-wide
           (bottom-up hash-consing; compound bodies pruned as in v1).
  pass 2 — for every statement whose v1 family is a singleton, return its
           subtree hash tree; classify each node: covered (subtree family
           count >= T elsewhere) or glue. Report glue distribution.

If median glue per singleton statement is a few nodes, the tail is cheap
composition, and the Phase-3 cost model changes accordingly.

Output: tmp/compose_census.pkl + tmp/compose_report.md, log tmp/compose_census.log
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
from region_census import stmt_fp, tu_args, walk_compounds  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "compose_census.log"
OUT = REPO / "tmp" / "compose_report.md"

K = ci.CursorKind
LITERALS = {K.INTEGER_LITERAL, K.FLOATING_LITERAL, K.STRING_LITERAL,
            K.CHARACTER_LITERAL}

SINGLETONS = None  # set in pass-2 workers via fork


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
    elif kind == K.BINARY_OPERATOR:
        try:
            t += ":" + c.binary_operator.name
        except AttributeError:
            pass
    return t


def subtree_hashes(stmt):
    """Postorder (hash, parent_idx, nchildren) per node, compounds pruned."""
    nodes = []  # (hash, parent, nchildren)

    def rec(c, parent):
        if c.kind == K.COMPOUND_STMT:
            nodes.append((hash8("BODY"), parent, 0))
            return len(nodes) - 1
        child_idx = []
        my_idx_placeholder = None  # children first (postorder), parent set later
        kids = list(c.get_children())
        # reserve nothing; compute children with parent fixed after append —
        # simpler: build children into temp then append self, then patch
        child_hashes = []
        start = len(nodes)
        for k in kids:
            ci_idx = rec(k, -1)  # parent patched below
            child_idx.append(ci_idx)
            child_hashes.append(nodes[ci_idx][0])
        h = hash8(tok(c) + "(" + ",".join(map(str, child_hashes)) + ")")
        nodes.append((h, parent, len(kids)))
        me = len(nodes) - 1
        for ci_idx in child_idx:
            nodes[ci_idx] = (nodes[ci_idx][0], me, nodes[ci_idx][2])
        return me

    rec(stmt, -1)
    return nodes


def hash8(s):
    return int.from_bytes(hashlib.sha1(s.encode()).digest()[:8], "big")


def iter_statements(entry):
    os.chdir(entry["directory"])
    src = entry["file"]
    index = ci.Index.create()
    try:
        tu = index.parse(src, args=tu_args(entry))
    except ci.TranslationUnitLoadError:
        return
    for fn in tu.cursor.get_children():
        if fn.kind != K.FUNCTION_DECL or not fn.is_definition():
            continue
        if fn.location.file is None or fn.location.file.name != src:
            continue
        for comp in walk_compounds(fn):
            for st in comp.get_children():
                if st.kind == K.DECL_STMT or st.kind.is_statement() or \
                        st.kind.is_expression():
                    yield st


def pass1(entry):
    c = Counter()
    for st in iter_statements(entry):
        for h, _, _ in subtree_hashes(st):
            c[h] += 1
    return c


def pass2(entry):
    out = []
    for st in iter_statements(entry):
        if stmt_fp(st) in SINGLETONS:
            out.append(subtree_hashes(st))
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
    cc = json.load(open(REPO / "linux" / "compile_commands.json"))
    entries = sorted((e for e in cc if e["file"].endswith(".c")),
                     key=lambda e: e["file"])
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
                logging.info("pass2 %d/%d TUs, %d singleton stmts collected",
                             i + 1, len(entries), len(structs))
    logging.info("pass 2 done: %d singleton statements", len(structs))

    lines = []
    w = lines.append
    w("# Phase 1 v2 — compositionality of the tail")
    w("")
    w(f"- corpus subtree instances: {sum(counts.values()):,}; distinct: "
      f"{len(counts):,}")
    w(f"- singleton statements analysed: {len(structs):,}")
    w("")
    for T in (5, 10, 50):
        glue_dist = Counter()
        covered_nodes = total_nodes = 0
        for nodes in structs:
            glue = sum(1 for h, _, _ in nodes if counts[h] < T)
            total_nodes += len(nodes)
            covered_nodes += len(nodes) - glue
            glue_dist[glue] += 1
        n = len(structs)
        cum = 0
        med = p90 = None
        for g in sorted(glue_dist):
            cum += glue_dist[g]
            if med is None and cum >= n / 2:
                med = g
            if p90 is None and cum >= n * 0.9:
                p90 = g
        for lim in (3, 5, 10):
            cov = sum(v for g, v in glue_dist.items() if g <= lim)
            w(f"- T={T}: glue ≤ {lim} for {100*cov/n:.1f}% of singleton stmts")
        w(f"- T={T}: median glue {med}, p90 {p90}; node-level coverage "
          f"{100*covered_nodes/total_nodes:.1f}%")
        w("")
    OUT.write_text("\n".join(lines) + "\n")
    logging.info("wrote %s", OUT)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
