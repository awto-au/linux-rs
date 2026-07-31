#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Phase-1 census go/no-go gate, made structural instead of documentary.

PLAN.md names Phase 1 (the pattern census) an explicit go/no-go gate
before any rule-based translation work: "if ordinary code doesn't
collapse into hundreds-not-tens-of-thousands of families, the thesis
fails and we stop" — and Phase 2 step 4 says every manual fix must land
as a rule, "this phase forces the DB schema to be real."

That gate was stated in prose only, and it did not hold: as of
2026-07-31 the rule-learning track has 31 rules / 38 translated_tus but
`functions`/`statement_families` (the tables the census populates) both
have zero rows, while the c2rust-breadth track's equivalent tables
(c2rust_decl_outcomes, function_safety_status) have hundreds of
thousands. Without the corpus ingested, a rule can't be validated
against its structurally-equivalent occurrences, because their
locations aren't known — every "rule" ends up a one-off patch instead.
See awto-au/linux-rs#53 for the full finding.

This check makes the specific already-happened drift impossible to
reintroduce silently: if rules or translated_tus has any rows, functions
and statement_families must too (built by scripts/fingerprint.py +
scripts/region_census.py into tmp/functions.jsonl, loaded by
build_db.py's load_functions()/load_statement_families()). It does NOT
attempt a precise per-rule per-file coverage check — rules.match_family
is free text, not a queryable link to statement_families rows, and
building that link is separate follow-up work (see #53's proposed fix
options), not something to half-build here.

Usage: check_census_gate.py
Log: tmp/check_census_gate.log
"""
import logging
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "rulesdb" / "patterns.db"
LOG = REPO / "tmp" / "check_census_gate.log"


def main() -> int:
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not DB.exists():
        logging.warning("no %s — skipping (run build_db.py first)", DB)
        return 0

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n_rules = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
    n_tus = conn.execute("SELECT COUNT(*) FROM translated_tus").fetchone()[0]
    n_functions = conn.execute("SELECT COUNT(*) FROM functions").fetchone()[0]
    n_families = conn.execute("SELECT COUNT(*) FROM statement_families").fetchone()[0]
    conn.close()

    if (n_rules or n_tus) and not (n_functions and n_families):
        logging.error(
            "CENSUS GATE FAIL: rules=%d, translated_tus=%d, but functions=%d, "
            "statement_families=%d. Rule-based translation work exists without "
            "the Phase-1 census backing it — a rule cannot be validated against "
            "its structurally-equivalent occurrences if their locations aren't "
            "known. Run scripts/fingerprint.py + scripts/region_census.py over "
            "the pinned corpus, then scripts/build_db.py, before adding more "
            "rules or translated TUs. See awto-au/linux-rs#53.",
            n_rules, n_tus, n_functions, n_families,
        )
        return 1

    logging.info(
        "CENSUS GATE OK: rules=%d, translated_tus=%d, functions=%d, "
        "statement_families=%d", n_rules, n_tus, n_functions, n_families,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
