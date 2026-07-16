#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build rulesdb/patterns.db (SQLite) from the authored rule TOMLs + the
Phase 1 kernel census + current translation status.

This is a QUERYABLE INDEX, regenerated from source every run — never hand-
edit patterns.db, edit rulesdb/rules/*.toml or re-run the census scripts
instead. Answers the original project proposal's "quick checks against
the whole kernel" idea: e.g. "how many statement families with >N
instances have no rule yet" (uncovered_hot_families view), "which rule
covers this exact snippet", "how many functions call X".

Inputs (all optional — missing ones just leave that part of the DB
empty, logged as a warning):
  rulesdb/rules/*.toml           — the authored rules (always present)
  tmp/functions.jsonl            — Phase 1 whole-function census
  tmp/region_census.pkl          — Phase 1 statement-family census
  linux-riscv/lib/**/*_rs.rs     — current translations (+ git log for dates)

Usage: build_db.py
Output: rulesdb/patterns.db, log tmp/build_db.log
"""
import json
import logging
import pickle
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    print("Python 3.11+ required (tomllib)", file=sys.stderr)
    sys.exit(1)

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
RULES_DIR = REPO / "rulesdb" / "rules"
SCHEMA = REPO / "rulesdb" / "schema.sql"
DB = REPO / "rulesdb" / "patterns.db"
LOG = REPO / "tmp" / "build_db.log"


def load_rules(conn):
    n = 0
    for f in sorted(RULES_DIR.glob("*.toml")):
        d = tomllib.load(open(f, "rb"))
        m = re.match(r"(\d+)-", f.name)
        number = int(m.group(1)) if m else 0
        match = d.get("match", {})
        emit = d.get("emit", {})
        prov = d.get("provenance", {})
        conn.execute(
            "INSERT INTO rules (id, number, version, tier, category, match_c, "
            "match_family, emit_kind, emit_rust, derivation, oracle, "
            "human_review, deferred, source_path) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["id"], number, d.get("version", 1), d.get("tier", 1),
             d.get("category", ""), match.get("c"), match.get("family"),
             emit.get("kind"), emit.get("rust"), prov.get("derivation"),
             d.get("validation", {}).get("oracle"),
             int(bool(d.get("human_review"))), int("status" in d),
             str(f.relative_to(REPO))),
        )
        for c in match.get("constraints", []) or []:
            conn.execute("INSERT INTO rule_constraints VALUES (?,?)", (d["id"], c))
        for neg in d.get("validation", {}).get("negative", []) or []:
            conn.execute("INSERT INTO rule_negatives VALUES (?,?)", (d["id"], neg))
        for ev in prov.get("evidence", []) or []:
            conn.execute("INSERT INTO rule_evidence VALUES (?,?)", (d["id"], ev))
        for inst in d.get("validation", {}).get("instances", []) or []:
            conn.execute("INSERT INTO rule_validation_instances VALUES (?,?)",
                        (d["id"], inst))
        n += 1
    return n


def load_functions(conn):
    path = REPO / "tmp" / "functions.jsonl"
    if not path.exists():
        logging.warning("no tmp/functions.jsonl — skipping function census "
                        "(re-run scripts/fingerprint.py to regenerate)")
        return 0
    n = 0
    for line in open(path):
        r = json.loads(line)
        cur = conn.execute(
            "INSERT INTO functions (file, name, line, fp_exact, fp_shape, "
            "nodes, ncalls, is_static, features) VALUES (?,?,?,?,?,?,?,?,?)",
            (r["file"], r["name"], r["line"], r["fp_exact"], r["fp_shape"],
             r["nodes"], r["ncalls"], int(r["static"]), json.dumps(r["features"])),
        )
        fid = cur.lastrowid
        for c in r["callees"]:
            conn.execute("INSERT INTO callees VALUES (?,?)", (fid, c))
        n += 1
    return n


def load_statement_families(conn):
    path = REPO / "tmp" / "region_census.pkl"
    if not path.exists():
        logging.warning("no tmp/region_census.pkl — skipping statement-family "
                        "census (re-run scripts/region_census.py to regenerate)")
        return 0
    d = pickle.load(open(path, "rb"))
    stmts, exemplars = d["stmts"], d["exemplars"]
    for fp, count in stmts.items():
        ex = exemplars.get(fp, (None, None, None))
        conn.execute(
            "INSERT OR REPLACE INTO statement_families VALUES (?,?,?,?,?)",
            (fp, count, ex[0], ex[1], ex[2]),
        )
    return len(stmts)


def load_translated_tus(conn):
    if not TREE.exists():
        logging.warning("no linux-riscv/ worktree — skipping translation status")
        return 0
    n = 0
    for rs in sorted(TREE.glob("lib/**/*_rs.rs")):
        c_rel = str(rs.relative_to(TREE)).replace("_rs.rs", ".c")
        rs_rel = str(rs.relative_to(TREE))
        landed_at, patch_num = None, None
        try:
            log = subprocess.run(
                ["git", "-C", str(TREE), "log", "-1", "--format=%aI", "--", rs_rel],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            landed_at = log or None
        except Exception:
            pass
        conn.execute(
            "INSERT OR REPLACE INTO translated_tus VALUES (?,?,?,?)",
            (c_rel, rs_rel, landed_at, patch_num),
        )
        n += 1
    return n


def main() -> int:
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    # c2rust_attempts/c2rust_failure_signatures are populated by a
    # separate, occasional process (scripts/run_c2rust_baseline.py +
    # import_c2rust_baseline.py), not re-derived from the census/rules
    # like everything else here — preserve that history across the
    # DB.unlink() below instead of silently discarding fix-progress data
    # every routine rebuild.
    c2rust_backup = []
    signatures_backup = []
    if DB.exists():
        old_conn = sqlite3.connect(DB)
        try:
            c2rust_backup = old_conn.execute(
                "SELECT id, c_file, run_at, outcome, returncode, warnings, "
                "missing_top_level_nodes, missing_children, label_address_exprs, "
                "rs_files_emitted, c2rust_rev, notes FROM c2rust_attempts"
            ).fetchall()
            signatures_backup = old_conn.execute(
                "SELECT id, attempt_id, c_file, kind, source_file, source_line, detail "
                "FROM c2rust_failure_signatures"
            ).fetchall()
        except sqlite3.OperationalError:
            pass  # tables don't exist yet (first run, or pre-c2rust-tracking DB)
        old_conn.close()

    DB.unlink(missing_ok=True)
    conn = sqlite3.connect(DB)
    # WAL: readers (dev.py q ..., a query while a rebuild is mid-flight)
    # never block on the writer, and the writer never blocks on readers —
    # real, justified win for a DB that gets queried interactively while
    # cscope/sparse imports are still running after this script returns.
    # No new dependency, one pragma; not nogil/threading, since none of
    # this import path is CPU-bound on Python bytecode (SQLite's C layer
    # already releases the GIL during actual I/O).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA.read_text())

    n_rules = load_rules(conn)
    logging.info("rules: %d", n_rules)
    n_funcs = load_functions(conn)
    logging.info("functions: %d", n_funcs)
    n_stmts = load_statement_families(conn)
    logging.info("statement families: %d", n_stmts)
    n_tus = load_translated_tus(conn)
    logging.info("translated TUs: %d", n_tus)

    if c2rust_backup:
        conn.executemany(
            "INSERT INTO c2rust_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", c2rust_backup
        )
        conn.executemany(
            "INSERT INTO c2rust_failure_signatures VALUES (?,?,?,?,?,?,?)", signatures_backup
        )
        logging.info(
            "restored %d c2rust_attempts + %d c2rust_failure_signatures rows",
            len(c2rust_backup), len(signatures_backup),
        )

    conn.commit()

    # Sanity: report the two headline "quick check" queries so a broken
    # import fails loudly here, not on first real use. Statement
    # fingerprints are raw libclang CursorKind numbers (e.g. "214 112
    # LIT") — decode them to names for anything humans read; the DB
    # itself stores the raw form so it matches region_census.py exactly.
    if n_stmts:
        try:
            import clang.cindex as ci
            def decode(fp):
                out = []
                for tok in fp.split("\x00")[0].split(" "):
                    if tok.isdigit():
                        try:
                            out.append(ci.CursorKind.from_id(int(tok)).name)
                            continue
                        except ValueError:
                            pass
                    out.append(tok)
                return " ".join(out)
        except ImportError:
            decode = lambda fp: fp  # noqa: E731

        top = conn.execute(
            "SELECT fp, instance_count, exemplar_snippet FROM uncovered_hot_families LIMIT 5"
        ).fetchall()
        logging.info("top 5 uncovered hot statement families (decoded, exemplar):")
        for fp, cnt, snip in top:
            logging.info("  %6d  %-55s  %s", cnt, decode(fp)[:55], (snip or "")[:50])
    tier_summary = conn.execute("SELECT * FROM rule_tier_summary").fetchall()
    logging.info("rule tier summary: %s", tier_summary)

    conn.close()
    logging.info("wrote %s (%.1f MB)", DB, DB.stat().st_size / 1e6)
    print(f"DB OK: {n_rules} rules, {n_funcs} functions, {n_stmts} statement "
         f"families, {n_tus} translated TUs -> {DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
