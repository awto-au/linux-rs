#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Report which translated TUs' C originals changed upstream since they were translated.

- Per TU from patterns.db translated_tus: c_source_file + recorded c_blob_sha
  (mirrored from rulesdb/tu_provenance.json by build_db.py).
- DRIFTED = c_source_file's blob at --target (default origin/master) != c_blob_sha.
- Unrecorded c_blob_sha → compare against merge-base(linux-riscv HEAD, target), flagged UNRECORDED.
- --record: write c_source_file + c_blob_sha + c_base_tag (at merge-base) into tu_provenance.json,
  then `dev.py db` to mirror. No RS_FILE named → only entries lacking c_blob_sha; named → overwrite.
- Git helpers here are shared with check_tu_provenance.py.

Usage: check_c_drift.py [--tree DIR] [--db FILE] [--target REV] [--json] [--record [RS_FILE ...]]
Log: tmp/logs/check_c_drift.log; JSON: tmp/c_drift.json
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from _repo import REPO, TMP, make_log

DEFAULT_TREE = REPO / "linux-riscv"
DEFAULT_DB = REPO / "rulesdb" / "patterns.db"
MANIFEST = REPO / "rulesdb" / "tu_provenance.json"
UPSTREAM = "origin/master"  # store remote origin = torvalds/linux


def git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def series_base(tree: Path, target: str = UPSTREAM) -> str:
    """Upstream commit the series sits on."""
    return git(tree, "merge-base", "HEAD", target)


def blob_sha(tree: Path, rev: str, path: str) -> str | None:
    """Blob sha of path at rev; None if absent."""
    out = git(tree, "ls-tree", rev, "--", path)
    return out.split()[2] if out else None


def tu_rows(db: Path) -> list[tuple[str, str, str | None]]:
    """(rs_file, upstream C file, recorded c_blob_sha) per translated TU."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    # split TUs: the series-created .c slice has no upstream; its real source is source_c_file
    return conn.execute(
        "SELECT t.rs_file, COALESCE(t.c_source_file, s.source_c_file, t.c_file), t.c_blob_sha "
        "FROM translated_tus t LEFT JOIN split_tu_provenance s ON s.rs_file = t.rs_file "
        "ORDER BY 2, 1").fetchall()


def record(tree: Path, base: str, rows, names: list[str], log) -> int:
    manifest = json.loads(MANIFEST.read_text())
    entries = manifest["entries"]
    src = {rs: c for rs, c, _ in rows}
    tag = git(tree, "describe", "--tags", base)
    targets = names or [rs for rs, m in entries.items() if not m.get("c_blob_sha")]
    bad = 0
    for rs in targets:
        if rs not in entries or rs not in src:
            log(f"RECORD FAIL: {rs} not in both tu_provenance.json and translated_tus")
            bad += 1
            continue
        sha = blob_sha(tree, base, src[rs])
        if not sha:
            log(f"RECORD FAIL: {src[rs]} absent at base {tag}")
            bad += 1
            continue
        entries[rs].update(c_source_file=src[rs], c_blob_sha=sha, c_base_tag=tag)
        log(f"RECORD {sha[:12]} {src[rs]}  ({rs})")
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    log(f"RECORDED {len(targets) - bad}/{len(targets)} at base {tag}; run `dev.py db` to mirror into patterns.db")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", type=Path, default=DEFAULT_TREE)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--target", default=UPSTREAM)
    ap.add_argument("--json", action="store_true", help="also write tmp/c_drift.json")
    ap.add_argument("--record", nargs="*", metavar="RS_FILE")
    args = ap.parse_args()
    log = make_log("check_c_drift")
    tree, target = args.tree, args.target

    rows = tu_rows(args.db)
    base = None
    if args.record is not None or any(sha is None for _, _, sha in rows):
        base = series_base(tree, target)
    if args.record is not None:
        return record(tree, base, rows, args.record, log)

    target_sha = git(tree, "rev-parse", target)
    log(f"target={git(tree, 'describe', '--tags', target)}")
    out = []
    for rs_file, c_file, recorded in rows:
        frm = recorded or blob_sha(tree, base, c_file)
        now = blob_sha(tree, target, c_file)
        added = removed = 0
        if now is None:
            state = "GONE"
        elif now == frm:
            state = "same"
        else:
            state = "DRIFTED"
            a, r, _ = git(tree, "diff", "--numstat", frm, now).split("\t", 2)
            added, removed = int(a), int(r)
        if recorded is None:
            state += "(UNRECORDED)"
        out.append({"c_file": c_file, "rs_file": rs_file, "state": state, "from_blob": frm,
                    "target_blob": now, "added": added, "removed": removed})
        log(f"{state:19} +{added:<5} -{removed:<5} {c_file}  ({rs_file})")

    drifted = [r for r in out if not r["state"].startswith("same")]
    log(f"SUMMARY: {len(drifted)}/{len(out)} translated TUs have C drift since translation..{target}")
    if args.json:
        TMP.mkdir(exist_ok=True)
        (TMP / "c_drift.json").write_text(json.dumps(
            {"target": target_sha, "rows": out}, indent=1))
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
