#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Record one row in patterns.db's progress_snapshots table — a
point-in-time picture of TU/LOC progress plus the most recent c2rust
baseline outcome counts, so progress over the project's actual history
is queryable (not just current state). Run manually after landing a TU
or after a c2rust baseline run.

progress_snapshots is NOT ephemeral — preserved across build_db.py
rebuilds like the other c2rust_* tables (see PERSISTENT_TABLES there).

Usage: take_progress_snapshot.py ["free-text note"]
Log: tmp/take_progress_snapshot.log
"""
import json
import logging
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
TMP = REPO / "tmp"
DB = REPO / "rulesdb" / "patterns.db"
LOG = TMP / "take_progress_snapshot.log"


def corpus_stats():
    cc_path = TREE / "compile_commands.json"
    if not cc_path.exists():
        return None, None
    entries = json.load(open(cc_path))
    files = sorted(set(
        e["file"] for e in entries if "/lib/" in e["file"] and e["file"].endswith(".c")
    ))
    if not files:
        return None, None
    total_loc = 0
    for f in files:
        try:
            total_loc += sum(1 for _ in open(f, errors="replace"))
        except OSError:
            pass
    return len(files), total_loc


def landed_loc(conn):
    rows = conn.execute("SELECT c_file FROM translated_tus").fetchall()
    total = 0
    for (c_file,) in rows:
        p = TREE / c_file
        if p.exists():
            try:
                total += sum(1 for _ in open(p, errors="replace"))
            except OSError:
                pass
    return total


def main() -> int:
    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    note = sys.argv[1] if len(sys.argv) > 1 else None

    if not DB.exists():
        logging.error("no %s — run scripts/build_db.py first", DB)
        return 1

    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS progress_snapshots ("
        "id INTEGER PRIMARY KEY, taken_at TEXT NOT NULL, note TEXT, "
        "tus_landed INTEGER NOT NULL, corpus_total_files INTEGER, corpus_total_loc INTEGER, "
        "landed_loc INTEGER, c2rust_clean INTEGER, c2rust_crash INTEGER, "
        "c2rust_dropped_decls INTEGER, c2rust_no_output INTEGER, c2rust_timeout INTEGER, "
        "c2rust_rev TEXT)"
    )

    tus_landed = conn.execute("SELECT COUNT(*) FROM translated_tus").fetchone()[0]
    corpus_files, corpus_loc = corpus_stats()
    l_loc = landed_loc(conn)

    latest_run = conn.execute("SELECT MAX(run_at) FROM c2rust_attempts").fetchone()[0]
    outcomes = {"clean": None, "crash": None, "dropped_decls": None, "no_output": None, "timeout": None}
    c2rust_rev = None
    if latest_run:
        rows = conn.execute(
            "SELECT outcome, COUNT(*) FROM c2rust_attempts WHERE run_at = ? GROUP BY outcome",
            (latest_run,),
        ).fetchall()
        for outcome, n in rows:
            if outcome in outcomes:
                outcomes[outcome] = n
        c2rust_rev = conn.execute(
            "SELECT c2rust_rev FROM c2rust_attempts WHERE run_at = ? LIMIT 1", (latest_run,)
        ).fetchone()
        c2rust_rev = c2rust_rev[0] if c2rust_rev else None

    taken_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO progress_snapshots "
        "(taken_at, note, tus_landed, corpus_total_files, corpus_total_loc, landed_loc, "
        " c2rust_clean, c2rust_crash, c2rust_dropped_decls, c2rust_no_output, c2rust_timeout, c2rust_rev) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            taken_at, note, tus_landed, corpus_files, corpus_loc, l_loc,
            outcomes["clean"], outcomes["crash"], outcomes["dropped_decls"],
            outcomes["no_output"], outcomes["timeout"], c2rust_rev,
        ),
    )
    conn.commit()

    pct = f"{100 * l_loc / corpus_loc:.1f}%" if corpus_loc else "?"
    logging.info(
        "SNAPSHOT taken_at=%s tus=%d loc=%d/%s (%s) c2rust=%s rev=%s note=%s",
        taken_at, tus_landed, l_loc, corpus_loc, pct, outcomes, c2rust_rev, note,
    )
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
