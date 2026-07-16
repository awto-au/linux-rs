#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Import scripts/run_c2rust_baseline.py's results
(tmp/c2rust-baseline-results.jsonl) into patterns.db's c2rust_attempts
table, stamped with the current awtoau/c2rust git revision and run time.

NOT wired into `dev.py db` (unlike cscope/sparse): a c2rust baseline run
is a deliberate, occasional triage step (run manually after changing
awtoau/c2rust or before a fix push), not something to silently re-derive
on every routine DB rebuild. `dev.py db` calls build_db.py, which wipes
patterns.db fresh — re-run THIS script afterward to restore c2rust
history, or better, keep it in a separate long-lived DB if that history
needs to survive routine rebuilds.

Usage: import_c2rust_baseline.py
Input: tmp/c2rust-baseline-results.jsonl (run run_c2rust_baseline.py first)
Log: tmp/import_c2rust_baseline.log
"""
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = REPO / "tmp"
RESULTS = TMP / "c2rust-baseline-results.jsonl"
DB = REPO / "rulesdb" / "patterns.db"
LOG = TMP / "import_c2rust_baseline.log"
C2RUST_FORK = Path("/mnt/2tb/git/github.com/awtoau/c2rust")


def c2rust_rev():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=C2RUST_FORK, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return out
    except Exception:
        return None


def main() -> int:
    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not RESULTS.exists():
        logging.error("no %s — run scripts/run_c2rust_baseline.py first", RESULTS)
        return 1
    if not DB.exists():
        logging.error("no %s — run scripts/build_db.py first", DB)
        return 1

    import sqlite3
    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_attempts ("
        "id INTEGER PRIMARY KEY, c_file TEXT NOT NULL, run_at TEXT NOT NULL, "
        "outcome TEXT NOT NULL, returncode INTEGER, warnings INTEGER, "
        "missing_top_level_nodes INTEGER, missing_children INTEGER, "
        "label_address_exprs INTEGER, rs_files_emitted INTEGER, "
        "c2rust_rev TEXT, notes TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_failure_signatures ("
        "id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES c2rust_attempts(id), "
        "c_file TEXT NOT NULL, kind TEXT NOT NULL, source_file TEXT, "
        "source_line INTEGER, detail TEXT NOT NULL)"
    )

    run_at = datetime.now(timezone.utc).isoformat()
    rev = c2rust_rev()
    n = 0
    with open(RESULTS) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            cur = conn.execute(
                "INSERT INTO c2rust_attempts "
                "(c_file, run_at, outcome, returncode, warnings, "
                " missing_top_level_nodes, missing_children, label_address_exprs, "
                " rs_files_emitted, c2rust_rev) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    r["file"], run_at, r["outcome"], r.get("returncode"),
                    r.get("warnings"), r.get("missing_top_level_nodes"),
                    r.get("missing_children"), r.get("label_address_exprs"),
                    r.get("rs_files_emitted"), rev,
                ),
            )
            attempt_id = cur.lastrowid
            for sig in r.get("signatures", []):
                conn.execute(
                    "INSERT INTO c2rust_failure_signatures "
                    "(attempt_id, c_file, kind, source_file, source_line, detail) "
                    "VALUES (?,?,?,?,?,?)",
                    (attempt_id, r["file"], sig["kind"], sig.get("source_file"),
                     sig.get("source_line"), sig["detail"]),
                )
            n += 1
    conn.commit()

    rows = conn.execute("SELECT outcome, COUNT(*) FROM c2rust_attempts WHERE run_at = ? GROUP BY outcome", (run_at,)).fetchall()
    logging.info("IMPORT OK: %d c2rust_attempts rows (rev %s) into %s", n, rev, DB)
    logging.info("outcomes this run: %s", dict(rows))

    patterns = conn.execute(
        "SELECT kind, detail, tus_affected FROM c2rust_failure_patterns LIMIT 15"
    ).fetchall()
    logging.info("top failure patterns (fix-priority order):")
    for kind, detail, count in patterns:
        logging.info("  [%3d TUs] %-22s %s", count, kind, detail[:100])

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
