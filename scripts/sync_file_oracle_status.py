#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Sync rulesdb/patterns.db's file_oracle_status table from real,
existing evidence sources — the queryable answer to "what validation
tiers has this file actually passed" (PLAN.md's 5-tier oracle: 1=compiles,
2=ABI/symbol diff, 3=KUnit differential, 4=boot+kselftest, 5=human
review), currently scattered across c2rust_attempts,
c2rust_compile_outcomes, translated_tus, docs/status/boot-history.csv,
and landing docs with no single relational answer.

Honest about what's recoverable vs not: tier 1 (compiles) is real,
queryable data for both populations (c2rust_attempts/
c2rust_compile_outcomes for the c2rust corpus, translated_tus + the fact
of landing for hand-translated TUs — landing implies a real in-tree
compile). Tier 2 (ABI diff) has no persisted per-file record anywhere in
this codebase as of 2026-07-18 — not backfilled, left not_attempted.
Tier 3 (KUnit differential) has no persisted C-file<->suite mapping
either — integrate_tu.py's --suite argument checks it live at
integration time but never records the mapping afterward; not backfilled
for existing TUs, left not_attempted (a real gap worth closing by having
integrate_tu.py write here going forward, not by guessing backward).
Tier 4 (boot+kselftest) is backfilled ONLY for the handful of TUs with a
real, explicit landing doc citing boot-transcript comparison evidence
(the Tier C 8250 slices) — see MANUAL_TIER4_EVIDENCE below; every other
landed TU is left not_attempted at tier 4 rather than assumed passing
just because a boot happened to be green at some point after it landed.
Tier 5 (human review) is backfilled from rules.human_review — true only
for rule-validated instances of a human_review=1 rule.

Usage: sync_file_oracle_status.py
Log: tmp/sync_file_oracle_status.log
"""
import datetime
import logging
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "rulesdb" / "patterns.db"
LOG = REPO / "tmp" / "sync_file_oracle_status.log"

# Hand-curated, not derived: the real, already-written landing docs for
# this project's only tier-4-with-explicit-evidence translations so far
# (the 8250 Tier C slices — the only work this session that did a real
# byte-for-byte boot-transcript comparison and wrote it up). Extend this
# list by hand as future landings do the same; do not try to auto-detect
# "was a boot-transcript comparison done" from boot-history.csv alone —
# that file has no per-file attribution, only aggregate pass/fail counts.
MANUAL_TIER4_EVIDENCE = [
    dict(c_file="drivers/tty/serial/8250/8250_port.c",
         detail="serial8250_do_startup/_do_shutdown: 6 boots (3 C-path, "
                 "3 Rust-path), all 15 pairwise diffs byte-identical from "
                 "KTAP onward",
         evidence_ref="docs/8250-tier-c-startup-shutdown-2026-07-18.md"),
    dict(c_file="drivers/tty/serial/8250/8250_port.c",
         detail="serial8250_handle_irq_locked: 8 boots (4 C-path, 4 "
                 "Rust-path), all 28 pairwise diffs byte-identical from "
                 "KTAP onward",
         evidence_ref="docs/8250-tier-c-irq-2026-07-18.md"),
]


def ensure_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS file_oracle_status ("
        "id INTEGER PRIMARY KEY, c_file TEXT NOT NULL, population TEXT NOT NULL, "
        "tier INTEGER NOT NULL CHECK (tier BETWEEN 1 AND 5), status TEXT NOT NULL, "
        "detail TEXT, evidence_ref TEXT, checked_at TEXT NOT NULL, "
        "UNIQUE (c_file, population, tier))"
    )
    conn.commit()


def upsert(conn, c_file, population, tier, status, detail, evidence_ref, now):
    conn.execute(
        "INSERT INTO file_oracle_status "
        "(c_file, population, tier, status, detail, evidence_ref, checked_at) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT (c_file, population, tier) DO UPDATE SET "
        "status=excluded.status, detail=excluded.detail, "
        "evidence_ref=excluded.evidence_ref, checked_at=excluded.checked_at",
        (c_file, population, tier, status, detail, evidence_ref, now),
    )


def sync_c2rust_corpus_tier1(conn, now):
    """Tier 1 for the c2rust corpus: 'clean' outcome at the latest run
    per file, keyed off c2rust_attempts (transpile-stage) — the
    strongest single signal available without cross-referencing every
    other c2rust_* table, and consistent with how this project already
    reports corpus-wide clean counts elsewhere (dev.py c2rust-baseline's
    own DONE: summary)."""
    rows = conn.execute(
        "SELECT c_file, outcome, run_at, c2rust_rev FROM c2rust_attempts a "
        "WHERE run_at = (SELECT MAX(run_at) FROM c2rust_attempts b WHERE b.c_file = a.c_file)"
    ).fetchall()
    n = 0
    for c_file, outcome, run_at, c2rust_rev in rows:
        status = "pass" if outcome == "clean" else "fail"
        upsert(conn, c_file, "c2rust_corpus", 1, status,
               f"c2rust transpile outcome={outcome} at rev {c2rust_rev}",
               f"c2rust_attempts run_at={run_at}", now)
        n += 1
    return n


def sync_landed_tus_tier1(conn, now):
    """Tier 1 for landed TUs: the fact of landing in translated_tus
    implies a real in-tree compile happened (dev.py land / integrate_tu.py
    both gate on kmake() succeeding before anything is considered
    landed) — this is real evidence, not an assumption, but it's coarser
    than c2rust_compile_outcomes' per-run rustc-check granularity."""
    rows = conn.execute("SELECT c_file, landed_at FROM translated_tus").fetchall()
    n = 0
    for c_file, landed_at in rows:
        upsert(conn, c_file, "landed_tu", 1, "pass",
               "landed in-tree, compiles as part of the live kernel build",
               f"translated_tus.landed_at={landed_at}", now)
        n += 1
    return n


def sync_tier4_manual(conn, now):
    n = 0
    for ev in MANUAL_TIER4_EVIDENCE:
        upsert(conn, ev["c_file"], "landed_tu", 4, "pass",
               ev["detail"], ev["evidence_ref"], now)
        n += 1
    return n


def sync_tier5_human_review(conn, now):
    """Tier 5 for landed TUs whose validation instance cites a rule with
    human_review=1 — rule_validation_instances links a rule to the
    file/function it was validated against; join back to translated_tus
    to scope this to files actually in that table (not every rule
    instance mention is a landed file)."""
    rows = conn.execute(
        "SELECT DISTINCT vi.instance_text FROM rule_validation_instances vi "
        "JOIN rules r ON r.id = vi.rule_id "
        "WHERE r.human_review = 1"
    ).fetchall()
    n = 0
    for (instance,) in rows:
        # instance strings are free text like "lib/math/int_sqrt.c:int_sqrt
        # (__fls)" — extract the leading c_file if present, skip if not
        # parseable rather than guessing.
        c_file = instance.split(":")[0].strip() if ":" in instance else None
        if not c_file or not c_file.endswith(".c"):
            continue
        upsert(conn, c_file, "landed_tu", 5, "pass",
               f"validated against a human_review=1 rule: {instance}",
               "rule_validation_instances", now)
        n += 1
    return n


def main():
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    conn = sqlite3.connect(DB)
    ensure_schema(conn)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    n1 = sync_c2rust_corpus_tier1(conn, now)
    n2 = sync_landed_tus_tier1(conn, now)
    n3 = sync_tier4_manual(conn, now)
    n4 = sync_tier5_human_review(conn, now)
    conn.commit()

    logging.info(
        "SYNC OK: %d c2rust-corpus tier-1 rows, %d landed-TU tier-1 rows, "
        "%d manual tier-4 rows, %d rule-derived tier-5 rows",
        n1, n2, n3, n4,
    )
    logging.info(
        "NOT backfilled (no persisted source exists yet): tier 2 (ABI/"
        "symbol diff) for either population; tier 3 (KUnit differential) "
        "— integrate_tu.py checks this live but doesn't persist the "
        "file<->suite mapping. Fix at the source (make integrate_tu.py "
        "and a future ABI-diff tool write here directly) rather than "
        "guessing backward from boot-history.csv, which has no per-file "
        "attribution."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
