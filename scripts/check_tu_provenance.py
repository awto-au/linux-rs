#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Every translated_tus row must have explicit, visible provenance.

- Hand-translation is an accepted, visible bridge; it must never pass as transpiler output (#56).
- build_db.py lands every *_rs.rs in translated_tus by glob; this check is what tells them apart.
- Reads rulesdb/tu_provenance.json (rs_file -> {provenance, replacement_issue, c_source_file, c_blob_sha, c_base_tag, deps, deps_base_tag}).

Fails if:
  - any real *_rs.rs file in linux-riscv/ has no entry
  - any entry has provenance='hand' with no replacement_issue
  - any entry's provenance is not one of hand/c2rust/c2rust+hand-fix
  - any manifest entry has no corresponding file on disk (stale entry)
  - any entry lacks c_source_file/c_blob_sha (record: check_c_drift.py --record)
  - c_source_file's blob at the series base != c_blob_sha (C changed under the Rust: re-translate)
  - any entry lacks deps (NO DEPS; record: check_c_drift.py --record)
  - a dep's blob at the series base != recorded (STALE DEP: review header/rust change, then --record)

Usage: check_tu_provenance.py [--tree DIR]
Log: tmp/logs/check_tu_provenance.log
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from _repo import REPO, log_path
from check_c_drift import DEFAULT_TREE, MANIFEST, blob_sha, series_base
from tu_deps import blobs_at

LOG = log_path("check_tu_provenance")
VALID_PROVENANCE = {"hand", "c2rust", "c2rust+hand-fix"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", type=Path, default=DEFAULT_TREE)
    tree = ap.parse_args().tree
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not tree.exists():
        logging.warning("no linux-riscv/ worktree — skipping provenance check")
        return 0

    manifest = json.loads(MANIFEST.read_text())
    entries = manifest.get("entries", {})

    real_files = {
        str(rs.relative_to(tree))
        for rs in sorted(tree.rglob("*_rs.rs"))
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

    base = series_base(tree)
    for rs_rel, meta in sorted(entries.items()):
        c_file, want = meta.get("c_source_file"), meta.get("c_blob_sha")
        if not (c_file and want):
            errors.append(f"NO C BLOB: {rs_rel} lacks c_source_file/c_blob_sha (check_c_drift.py --record {rs_rel})")
            continue
        have = blob_sha(tree, base, c_file)
        if have != want:
            errors.append(f"STALE TRANSLATION: {c_file} at base {base[:12]} is {str(have)[:12]}, "
                          f"{rs_rel} translated from {want[:12]} -- re-translate, then --record")

    at_base = blobs_at(tree, base, {p for m in entries.values() for p in m.get("deps", {})})
    for rs_rel, meta in sorted(entries.items()):
        if "deps" not in meta:
            errors.append(f"NO DEPS: {rs_rel} lacks deps (check_c_drift.py --record {rs_rel})")
            continue
        for path, want in sorted(meta["deps"].items()):
            have = at_base.get(path)
            if have != want:
                errors.append(f"STALE DEP: {path} at base {base[:12]} is {str(have)[:12]}, "
                              f"{rs_rel} recorded {want[:12]} -- review (check_c_drift.py), then --record")

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
