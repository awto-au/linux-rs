#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Every translated_tus row must have explicit, visible provenance.

This project builds a transpiler (awtoau/c2rust) -- hand-translation is
an accepted, visible BRIDGE (Dan, 2026-08-01: "hand translated TU are
not a big issue - if they work it is fine ... but you need to show me
how it will be tracked and not hidden"), not silent equivalence with
transpiler output. build_db.py's load_translated_tus() discovers every
*_rs.rs file in linux-riscv/ by filesystem glob and lands it in
translated_tus unconditionally -- without this check, a hand-written
file and a c2rust-transpiled file are indistinguishable in every "N TUs
landed" report, exactly the gap that let 41 files get hand-written
across many sessions before anyone noticed (awto-au/linux-rs#56).

Reads rulesdb/tu_provenance.json (rs_file -> {provenance, replacement_issue}).
Fails if:
  - any real *_rs.rs file in linux-riscv/ has no entry
  - any entry has provenance='hand' with no replacement_issue
  - any entry's provenance is not one of hand/c2rust/c2rust+hand-fix
  - any manifest entry has no corresponding file on disk (stale entry)

Usage: check_tu_provenance.py
Log: tmp/check_tu_provenance.log
"""
import json
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
MANIFEST = REPO / "rulesdb" / "tu_provenance.json"
LOG = REPO / "tmp" / "check_tu_provenance.log"
VALID_PROVENANCE = {"hand", "c2rust", "c2rust+hand-fix"}


def main() -> int:
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not TREE.exists():
        logging.warning("no linux-riscv/ worktree — skipping provenance check")
        return 0

    manifest = json.loads(MANIFEST.read_text())
    entries = manifest.get("entries", {})

    real_files = {
        str(rs.relative_to(TREE))
        for rs in sorted(TREE.rglob("*_rs.rs"))
    }

    errors = []

    for rs_rel in sorted(real_files - entries.keys()):
        errors.append(f"UNDECLARED: {rs_rel} exists but has no rulesdb/tu_provenance.json entry")

    for rs_rel in sorted(entries.keys() - real_files):
        errors.append(f"STALE: {rs_rel} has a manifest entry but no file on disk")

    for rs_rel, meta in sorted(entries.items()):
        prov = meta.get("provenance")
        if prov not in VALID_PROVENANCE:
            errors.append(f"BAD PROVENANCE: {rs_rel} has provenance={prov!r}, must be one of {sorted(VALID_PROVENANCE)}")
            continue
        if prov == "hand" and not meta.get("replacement_issue"):
            errors.append(f"MISSING ISSUE: {rs_rel} is provenance=hand but has no replacement_issue")

    if errors:
        logging.error("TU PROVENANCE FAIL: %d issue(s)", len(errors))
        for e in errors:
            logging.error("  %s", e)
        print("TU PROVENANCE FAIL")
        return 1

    hand = sum(1 for m in entries.values() if m.get("provenance") == "hand")
    c2rust = sum(1 for m in entries.values() if m.get("provenance") == "c2rust")
    fixed = sum(1 for m in entries.values() if m.get("provenance") == "c2rust+hand-fix")
    logging.info(
        "TU PROVENANCE PASS: %d total (%d hand, %d c2rust, %d c2rust+hand-fix)",
        len(entries), hand, c2rust, fixed,
    )
    print(f"TU PROVENANCE PASS ({len(entries)} total: {hand} hand, {c2rust} c2rust, {fixed} c2rust+hand-fix)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
