#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build tmp/cscope.out over the pinned corpus, then query it per function
name (driven by our own census's function list — clean, libclang-verified,
no macro-name false positives) and import call/definition refs into
rulesdb/patterns.db's cscope_symbols table.

Closes the documented gap in schema.sql's `callees`/`call_edges`: an AST
fingerprinter has no whole-program symbol resolution, so it can't tell
which definition of a `static` function a given call resolves to when
multiple TUs define a same-named static fn. cscope has real corpus-wide
symbol-table knowledge.

Usage: import_cscope.py [--rebuild-cscope-db]
Output: tmp/cscope.out (the cscope db itself, regenerable, gitignored via
tmp/), rows in patterns.db's cscope_symbols table
Log: tmp/import_cscope.log
"""
import argparse
import json
import logging
import re
import subprocess
import sys
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = REPO / "tmp"
DB = REPO / "rulesdb" / "patterns.db"
LOG = TMP / "import_cscope.log"
CSCOPE_FILES = TMP / "cscope_files.txt"
CSCOPE_OUT = TMP / "cscope.out"

# cscope -L query modes we care about:
#   -1 <name>  : find definition(s) of name
#   -3 <name>  : find callers of function name
QUERY_MODES = {"definition": "-1", "call": "-3"}


def build_cscope_db():
    cc = json.load(open(REPO / "linux" / "compile_commands.json"))
    files = sorted({e["file"] for e in cc if e["file"].endswith(".c")})
    CSCOPE_FILES.write_text("\n".join(files) + "\n")
    logging.info("cscope file list: %d TUs (same pinned corpus as the census)",
                 len(files))
    subprocess.run(
        ["cscope", "-b", "-k", "-i", str(CSCOPE_FILES), "-f", str(CSCOPE_OUT)],
        cwd=TMP, check=True, timeout=300,
    )
    logging.info("built %s (%.1f MB)", CSCOPE_OUT, CSCOPE_OUT.stat().st_size / 1e6)


LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s+(\d+)\s+(.*)$")


def normalize_path(p):
    """Match functions.jsonl's convention (relative to linux/, e.g.
    'arch/x86/boot/printf.c') so cscope rows join cleanly against census
    rows — cscope's own paths are absolute (REPO/linux/...); a naive
    REPO-prefix strip alone leaves a stray 'linux/' prefix that silently
    breaks every join against `functions`/`translated_tus` (found
    2026-07-16 via a skip_atoi spot-check: half the rows had 'linux/x'
    paths, half had 'x' paths, for the same file)."""
    p = p.replace(str(REPO) + "/", "")
    if p.startswith("linux/"):
        p = p[len("linux/"):]
    return p


def query(name, mode_flag):
    try:
        r = subprocess.run(
            ["cscope", "-d", "-f", str(CSCOPE_OUT), "-L", mode_flag, name],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        logging.warning("cscope query timed out for %r (mode %s) — skipping",
                        name, mode_flag)
        return []
    out = []
    for line in r.stdout.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        file_, context, lineno, rest = m.groups()
        out.append((normalize_path(file_), int(lineno), context))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild-cscope-db", action="store_true",
                    help="rebuild tmp/cscope.out even if present")
    ap.add_argument("--limit", type=int, default=0,
                    help="only query this many function names (debug)")
    args = ap.parse_args()

    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if args.rebuild_cscope_db or not CSCOPE_OUT.exists():
        build_cscope_db()
    else:
        logging.info("reusing existing %s (pass --rebuild-cscope-db to refresh)", CSCOPE_OUT)

    functions_path = TMP / "functions.jsonl"
    if not functions_path.exists():
        logging.error("no %s — run scripts/fingerprint.py first", functions_path)
        return 1
    cc_path = REPO / "linux" / "compile_commands.json"
    if cc_path.exists() and functions_path.stat().st_mtime < cc_path.stat().st_mtime:
        # Loud, not just logged — same principle as run_c2rust_baseline.py's
        # binary-stale warning: a census older than the pinned corpus
        # silently re-seeds cscope_symbols from function/callee data that
        # no longer matches the current tree, with nothing short of a
        # manual date comparison to notice.
        stale_msg = (f"{functions_path} is OLDER than {cc_path} — re-run "
                     f"scripts/fingerprint.py before trusting this import")
        print(f"WARNING: {stale_msg}")
        logging.warning(stale_msg)

    # Scope to `static` functions only (definitions + their callers): that
    # is exactly the class call_edges cannot resolve — non-static names
    # are already unambiguous there. We still query EVERY static name
    # (not just duplicated ones), because cscope_call_edges's
    # definition_ambiguous flag needs the full definition-count per name
    # to be correct, not just the pre-known-duplicate subset. Full-corpus
    # querying (84,515 names, every kind) measured at ~56ms/name = ~79
    # minutes; scoping to static-only (~9k of 84,515 names, measured
    # 2026-07-16) is the real, justified cut — not an arbitrary cap.
    # Unique (non-duplicated) static names need no cscope query at all —
    # the census already gives their one definition site directly
    # (file/line/name where static=1); a cscope call/definition query
    # would just re-confirm what we already know at ~56ms of subprocess
    # overhead per name for nothing. Only names a static function shares
    # with >=1 OTHER static definition in the corpus are genuinely
    # ambiguous and worth cscope's whole-corpus resolution — measured
    # 2026-07-16: 471 of 55,612 static names. Full static-name querying
    # was measured at ~52 minutes; this cuts it to ~30 seconds for the
    # same actually-useful information.
    from collections import Counter
    all_records = [json.loads(l) for l in open(functions_path)]
    static_counts = Counter(r["name"] for r in all_records if r["static"])
    names = sorted(n for n, c in static_counts.items() if c > 1)
    if args.limit:
        names = names[: args.limit]
    logging.info("querying cscope for %d ambiguous static names (shared by "
                 ">1 TU) of %d total static names, %d total functions — "
                 "unique static/non-static names resolve directly from the "
                 "census, no cscope query needed for those",
                 len(names), len(static_counts), len(all_records))

    if not DB.exists():
        logging.error("no %s — run scripts/build_db.py first", DB)
        return 1
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM cscope_symbols")

    # Seed every static definition directly from the census — no cscope
    # call needed, we already have file/line/name from libclang. This
    # makes cscope_call_edges's definition_ambiguous count correct for
    # ALL static names, not just the ones we ran a live query for.
    n_seed = 0
    for r in all_records:
        if r["static"]:
            conn.execute(
                "INSERT INTO cscope_symbols (name, kind, file, line, context) "
                "VALUES (?, 'definition', ?, ?, NULL)",
                (r["name"], r["file"], r["line"]),
            )
            n_seed += 1
    logging.info("seeded %d static definitions directly from the census "
                 "(no cscope query)", n_seed)

    # Live cscope queries only for the genuinely ambiguous names: their
    # definitions (cscope's independent cross-check against the census
    # seed above) and, more importantly, their callers (call_edges cannot
    # give us this at all for static names).
    n_def = n_call = 0
    for i, name in enumerate(names):
        for kind, flag in QUERY_MODES.items():
            for file_, lineno, context in query(name, flag):
                conn.execute(
                    "INSERT INTO cscope_symbols (name, kind, file, line, context) "
                    "VALUES (?,?,?,?,?)",
                    (name, kind, file_, lineno, context if kind == "call" else None),
                )
                if kind == "definition":
                    n_def += 1
                else:
                    n_call += 1
        if (i + 1) % 100 == 0:
            logging.info("%d/%d ambiguous names queried, %d defs / %d calls so far",
                         i + 1, len(names), n_def, n_call)
            conn.commit()

    conn.commit()
    conn.close()
    logging.info("DONE: %d static defs seeded, %d cscope-confirmed defs / "
                 "%d call refs for the %d ambiguous names",
                 n_seed, n_def, n_call, len(names))
    print(f"IMPORT OK: {n_seed} seeded defs, {n_def} cscope defs, "
         f"{n_call} call refs into {DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
