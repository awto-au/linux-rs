#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Regression check: every translated_tus entry must have real
compile_commands.json coverage, unless it's an approved split TU.

Why this exists: the 4 8250_*.c files (8250_helpers.c, 8250_io.c,
8250_irq.c, 8250_startup.c) have no standalone C source at all — their
functions are real, verified extractions from
drivers/tty/serial/8250/8250_port.c (see split_tu_provenance,
awto-au/linux-rs#45/#49/#50). translated_tus is auto-derived by
build_db.py's load_translated_tus() from a glob over every *_rs.rs file
in the tree, unconditionally, on every rebuild — there is no
"approved" gate on what lands there. Without this check, that glob
silently re-adds the 4 split TUs' rows to translated_tus even after
someone manually removes them (confirmed happening in practice, #50),
and any future accidental split-file landing (an .rs file with no real
.c compile-commands entry) would look identical to a genuine
compile_commands.json capture gap (#41) — this check tells the two
apart: covered normally, covered via an approved split_tu_provenance
mapping, or a real, unallowlisted gap that should fail the build.

Uses the same real C-side compile_commands.json recovery mechanism as
c2rust_reference_check.py: a CONFIG_RUST=n worktree of linux-riscv/
itself (linux-riscv-worktrees/c2rust-recheck-baseline by default),
NOT linux/'s pinned x86_64 corpus and NOT linux-riscv/'s own
compile_commands.json.

Neither of those two alternatives is right for this check:
- linux-riscv/compile_commands.json (CONFIG_RUST=y): a landed TU's
  Makefile entry switches to the *_rs.o object, so a genuinely-
  translated file's real C compile command legitimately never appears
  there even though it's a completely normal TU with real standalone
  C source — every one of the 38 translated files would spuriously
  fail this check against that corpus.
- linux/compile_commands.json (the pinned x86_64 census corpus): some
  real, standalone-source files aren't part of that arch's actual
  compile graph (e.g. lib/checksum.c, lib/hweight.c — x86_64 has
  arch-specific assembly implementations, arch/x86/lib/hweight.S,
  that override the generic C version) even though they compile fine
  for riscv64 — confirmed 2026-07-31 while building this check
  (spuriously flagged, investigated, found to be a real riscv64 TU
  with real compile_commands.json coverage in the CONFIG_RUST=n
  worktree, just arch-excluded from the x86_64 corpus).

Usage: check_split_tu_coverage.py
Log: tmp/check_split_tu_coverage.log
"""
import argparse
import logging
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "rulesdb" / "patterns.db"
LOG = REPO / "tmp" / "check_split_tu_coverage.log"
DEFAULT_CC = REPO / "linux-riscv-worktrees" / "c2rust-recheck-baseline" / "compile_commands.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--compile-commands", default=str(DEFAULT_CC),
        help="real C-side (CONFIG_RUST=n) compile_commands.json to check "
             "coverage against — default is the same recovery worktree "
             "c2rust_reference_check.py uses (see module doc for why neither "
             "linux-riscv/'s own nor linux/'s pinned corpus is correct here)",
    )
    args = ap.parse_args()

    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not DB.exists():
        logging.warning("no %s — skipping (run build_db.py first)", DB)
        return 0

    cc_path = Path(args.compile_commands)
    if not cc_path.exists():
        logging.warning("no %s — skipping", cc_path)
        return 0

    import json
    cc = json.loads(cc_path.read_text())
    covered = {Path(e["file"]).name for e in cc}

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    tus = conn.execute("SELECT c_file, rs_file FROM translated_tus").fetchall()
    approved = {row[0] for row in conn.execute("SELECT rs_file FROM split_tu_provenance")}
    conn.close()

    uncovered = []
    for c_file, rs_file in tus:
        if Path(c_file).name in covered:
            continue
        if rs_file in approved:
            continue
        uncovered.append((c_file, rs_file))

    if uncovered:
        logging.error(
            "SPLIT-TU COVERAGE FAIL: %d translated_tus entrie(s) have no "
            "compile_commands.json coverage and no approved split_tu_provenance "
            "mapping: %s. Either the C source genuinely doesn't exist (add a "
            "split_tu_provenance row with real verified function-level "
            "provenance, see the 8250 files for the pattern) or this is a "
            "compile_commands.json capture gap (see #41) that needs "
            "investigating, not silently allowlisting.",
            len(uncovered), uncovered,
        )
        return 1

    logging.info(
        "SPLIT-TU COVERAGE OK: %d translated_tus entries, all covered "
        "(directly or via an approved split_tu_provenance mapping)",
        len(tus),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
