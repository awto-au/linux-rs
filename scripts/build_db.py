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


# Hand-curated, not crawled — small enough to maintain by hand, and each
# entry needs a human judgment call about authoritativeness that a crawler
# can't make. Re-seeded every rebuild via INSERT OR IGNORE (UNIQUE(topic,
# location) in schema.sql), so adding an entry here is safe to re-run and
# never duplicates; edit an existing row's tuple directly to correct it.
DOC_SOURCES = [
    ("rust-for-linux-kernel-crate", "local", "linux-riscv/rust/kernel/",
     "Linux 7.1 (this project's actual vendored source — exact commit this build compiles)",
     1, "The real, vendored Rust-for-Linux kernel crate. Always check here "
        "first for an exact macro/function signature. If the API you need "
        "isn't ported/wrapped here yet, port it first (with a rust/helpers/*.c "
        "shim if needed) rather than bypassing the crate."),
    ("rust-for-linux-kernel-crate", "external", "https://rust.docs.kernel.org/6.12/kernel/index.html",
     "Tracks Linux 6.12, not this project's 7.1 — cross-check any exact "
     "signature against the local source before trusting it",
     0, "Official generated rustdoc, convenient for browsing/searching the "
        "overall crate shape, but a different commit than what this project builds."),
    ("rust-for-linux-kernel-crate", "external", "https://rust-for-linux.github.io/docs/kernel/",
     "Rolling/unclear which commit it tracks — verify before trusting an exact signature",
     0, "Community-hosted docs, useful for prose/design context, not a "
        "substitute for reading the local rust/kernel/ source."),
    ("linux-rs-translation-rules", "local", "rulesdb/rules/",
     "This project's own authored rules, versioned in this repo",
     1, "The authored TOML rules driving c2rust-transpile conformance "
        "checking and the hand-translation pattern catalogue."),
    ("c2rust-kernel-idiom-rules", "local", "awtoau/c2rust README.md (KernelIdiomRule section)",
     "awtoau/c2rust fork, branch master — this project's own clone",
     1, "The Stage-3 kernel-idiom-rewrite process (rank violations -> verify "
        "-> gate behind --enable-rule -> confirm default unchanged) and the "
        "registry of landed rules (warn-on, fls-family, swap-mem-swap)."),
]


def load_doc_sources(conn):
    n = 0
    for topic, kind, location, version_note, authoritative, notes in DOC_SOURCES:
        cur = conn.execute(
            "INSERT OR IGNORE INTO doc_sources (topic, kind, location, "
            "version_note, authoritative, notes, added_at) VALUES (?,?,?,?,?,?,?)",
            (topic, kind, location, version_note, authoritative, notes, "2026-07-17"),
        )
        n += cur.rowcount
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
    for rs in sorted(TREE.rglob("*_rs.rs")):
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
    # These tables are populated by separate, occasional processes (the
    # c2rust baseline/upstream-intel scripts), not re-derived from the
    # census/rules like everything else here — preserve them across the
    # DB.unlink() below instead of silently discarding cross-session
    # fix-progress/upstream-intel data every routine rebuild. Generic by
    # table name (not a hand-maintained column list per table) so adding
    # a new persistent table is a one-line addition to PERSISTENT_TABLES,
    # not a new backup/restore block — c2rust_forks/c2rust_issues were
    # missing from an earlier hand-maintained version of this and got
    # silently wiped for several rebuilds before this was caught
    # (2026-07-17).
    PERSISTENT_TABLES = [
        "c2rust_attempts",
        "c2rust_failure_signatures",
        "c2rust_decl_outcomes",
        "c2rust_compile_outcomes",
        "c2rust_forks",
        "c2rust_issues",
        "c2rust_fix_patterns",
        "c2rust_rule_conformance",
        "file_oracle_status",
        "progress_snapshots",
        "doc_sources",
        "work_items",
    ]
    table_backups = {}
    if DB.exists():
        old_conn = sqlite3.connect(DB)
        for table in PERSISTENT_TABLES:
            try:
                cols = [r[1] for r in old_conn.execute(f"PRAGMA table_info({table})")]
                if not cols:
                    continue  # table doesn't exist in the old DB
                rows = old_conn.execute(f"SELECT * FROM {table}").fetchall()
                table_backups[table] = (cols, rows)
            except sqlite3.OperationalError:
                pass  # table doesn't exist yet (first run, or older DB)
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
    n_docs = load_doc_sources(conn)
    logging.info("doc sources: %d", n_docs)

    dropped_tables = []
    for table, (cols, rows) in table_backups.items():
        if not rows:
            continue
        new_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not new_cols:
            logging.warning("skipping restore of %s: table missing from current schema.sql", table)
            dropped_tables.append((table, len(rows), "missing from schema.sql"))
            continue
        if set(cols) != new_cols:
            logging.warning(
                "skipping restore of %s: column set changed (%s -> %s) — "
                "schema migration needed, not a straight restore",
                table, sorted(cols), sorted(new_cols),
            )
            dropped_tables.append((table, len(rows), "column set changed"))
            continue
        placeholders = ",".join("?" * len(cols))
        # doc_sources is pre-seeded by load_doc_sources() above (curated,
        # re-run every rebuild) — OR IGNORE so restoring its own prior
        # output doesn't collide with itself on UNIQUE(topic, location);
        # any hand-added extra rows still come through untouched.
        verb = "INSERT OR IGNORE" if table == "doc_sources" else "INSERT"
        conn.executemany(f"{verb} INTO {table} ({','.join(cols)}) VALUES ({placeholders})", rows)
        logging.info("restored %d rows into %s", len(rows), table)

    # c2rust_issues_fts is an FTS5 virtual table over c2rust_issues — not
    # directly restorable row-by-row like a normal table, rebuild it from
    # the just-restored c2rust_issues instead.
    if "c2rust_issues" in table_backups and table_backups["c2rust_issues"][1]:
        try:
            # DROP+CREATE, not DELETE — DELETE FROM <fts5 table> has hit
            # a transient "database disk image is malformed" on this DB
            # (recoverable; PRAGMA integrity_check passes right after,
            # real data untouched) — see matching note in
            # crawl_c2rust_upstream.py's rebuild_fts.
            conn.execute("DROP TABLE IF EXISTS c2rust_issues_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE c2rust_issues_fts USING fts5("
                "repo, number UNINDEXED, title, body, content='c2rust_issues', content_rowid='id')"
            )
            conn.execute(
                "INSERT INTO c2rust_issues_fts (rowid, repo, number, title, body) "
                "SELECT id, repo, number, title, body FROM c2rust_issues"
            )
            logging.info("rebuilt c2rust_issues_fts from restored c2rust_issues")
        except sqlite3.OperationalError:
            pass  # FTS table not in schema.sql (older DB) — fine, nothing to rebuild

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
    # Loud, not just logged: a schema/data mismatch here silently drops
    # persisted rows on every rebuild until someone happens to grep the
    # log — this happened for real (2026-07-17, c2rust_attempts/
    # c2rust_decl_outcomes drifted from schema.sql for an unknown number
    # of rebuilds before being noticed).
    if dropped_tables:
        print("WARNING: persistent data DROPPED on this rebuild (schema.sql out of sync):")
        for table, n, reason in dropped_tables:
            print(f"  {table}: {n} rows lost ({reason}) — fix schema.sql to match, "
                  f"then regenerate this table's data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
