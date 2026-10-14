#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Report which translated TUs' C originals and dependencies changed upstream since translation.

- Per TU from patterns.db translated_tus: c_source_file + recorded c_blob_sha
  (mirrored from rulesdb/tu_provenance.json by build_db.py).
- DRIFTED = c_source_file's blob at --target (default origin/master) != c_blob_sha.
- Unrecorded c_blob_sha → compare against merge-base(linux-riscv HEAD, target), flagged UNRECORDED.
- Deps (patterns.db tu_deps; derivation in tu_deps.py): headers + rust/ kernel-crate files.
  DEPCHG = a dep's blob at target != recorded; numstat per dep. HIGH = header hunk touches a
  #define / static inline whose name appears in the TU's Rust (re-implemented inline, rule 0016).
- --record: write c_source_file + c_blob_sha + c_base_tag and deps + deps_base_tag (at merge-base)
  into tu_provenance.json, then `dev.py db` to mirror. No RS_FILE → only missing fields; named → overwrite.
- --at REV: record at REV instead of merge-base (TU translated from older C, e.g. v7.1 corpus).
- --deps-only: record deps only (dep drift reviewed, C unchanged); c_blob_sha untouched.
- Git helpers here are shared with check_tu_provenance.py.

Usage: check_c_drift.py [--tree DIR] [--db FILE] [--target REV] [--json]
                        [--record [RS_FILE ...] [--at REV] [--deps-only]]
Log: tmp/logs/check_c_drift.log; JSON: tmp/c_drift.json
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import tu_deps
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


def dep_rows(db: Path) -> dict[str, dict[str, str]]:
    """rs_file → {dep_path: recorded blob}; empty if the DB predates tu_deps (`dev.py db`)."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = defaultdict(dict)
    try:
        for rs, path, sha in conn.execute("SELECT rs_file, dep_path, dep_blob_sha FROM tu_deps"):
            out[rs][path] = sha
    except sqlite3.OperationalError:
        pass
    return out


def record(tree: Path, base: str, rows, names: list[str], log, deps_only: bool = False) -> int:
    manifest = json.loads(MANIFEST.read_text())
    entries = manifest["entries"]
    src = {rs: c for rs, c, _ in rows}
    tag = git(tree, "describe", "--tags", base)
    c_targets = [] if deps_only else names or [rs for rs, m in entries.items() if not m.get("c_blob_sha")]
    d_targets = names or [rs for rs, m in entries.items() if "deps" not in m]
    bad = 0
    for rs in sorted(set(c_targets) | set(d_targets)):
        if rs not in entries or rs not in src:
            log(f"RECORD FAIL: {rs} not in both tu_provenance.json and translated_tus")
            bad += 1
            continue
        if rs in c_targets:
            sha = blob_sha(tree, base, src[rs])
            if not sha:
                log(f"RECORD FAIL: {src[rs]} absent at base {tag}")
                bad += 1
                continue
            entries[rs].update(c_source_file=src[rs], c_blob_sha=sha, c_base_tag=tag)
            log(f"RECORD {sha[:12]} {src[rs]}  ({rs})")
        if rs in d_targets:
            deps = tu_deps.derive(tree, rs, src[rs], rust_index(tree))
            if deps is None:
                log(f"RECORD FAIL: no kbuild .cmd for {src[rs]} -- build its object first ({rs})")
                bad += 1
                continue
            blobs = tu_deps.blobs_at(tree, base, deps)
            for p in sorted(set(deps) - blobs.keys()):
                log(f"  skip dep absent at {tag}: {p}  ({rs})")
            entries[rs].update(deps=dict(sorted(blobs.items())), deps_base_tag=tag)
            n_rust = sum(1 for p in blobs if tu_deps.dep_kind(p) == "rust")
            log(f"RECORD deps {len(blobs) - n_rust} header + {n_rust} rust  ({rs})")
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    n = len(set(c_targets) | set(d_targets))
    log(f"RECORDED {n - bad}/{n} at base {tag}; run `dev.py db` to mirror into patterns.db")
    return 1 if bad else 0


_IDX = {}


def rust_index(tree: Path) -> tu_deps.RustIndex:
    return _IDX.setdefault(tree, tu_deps.RustIndex(tree))


def numstat(tree: Path, a: str, b: str) -> tuple[int, int]:
    added, removed, _ = git(tree, "diff", "--numstat", a, b).split("\t", 2)
    return int(added), int(removed)


def dep_drift(tree: Path, target: str, deps: dict[str, dict[str, str]], log) -> dict:
    """Per-dep changes recorded..target, per-TU summary, HIGH (re-implemented inline) flags."""
    now = tu_deps.blobs_at(tree, target, {p for d in deps.values() for p in d})
    changed = {}  # (path, recorded blob) → {added, removed, target_blob, inlines}
    for rs, d in sorted(deps.items()):
        for path, frm in d.items():
            key = (path, frm)
            if key in changed or now.get(path) == frm:
                continue
            tgt = now.get(path)
            a, r = numstat(tree, frm, tgt) if tgt else (0, 0)
            inl = (tu_deps.inline_names(tree, frm, tgt)
                   if tgt and tu_deps.dep_kind(path) == "header" else set())
            changed[key] = {"path": path, "from_blob": frm, "target_blob": tgt,
                            "added": a, "removed": r, "inlines": sorted(inl)}
    tus, high = [], []
    for rs, d in sorted(deps.items()):
        hit = [changed[(p, b)] for p, b in d.items() if (p, b) in changed]
        if not hit:
            continue
        idents = tu_deps.identifiers(tu_deps.strip_comments((tree / rs).read_text()))
        row = {"rs_file": rs, "deps": len(d)}
        for kind in ("header", "rust"):
            k = [c for c in hit if tu_deps.dep_kind(c["path"]) == kind]
            row[kind] = {"changed": len(k), "added": sum(c["added"] for c in k),
                         "removed": sum(c["removed"] for c in k)}
        tus.append(row)
        h, r = row["header"], row["rust"]
        log(f"DEPCHG  header {h['changed']:>3} (+{h['added']} -{h['removed']})  "
            f"rust {r['changed']:>2} (+{r['added']} -{r['removed']})  of {len(d)}  {rs}")
        for c in hit:
            names = sorted(set(c["inlines"]) & idents)
            if names:
                high.append({"rs_file": rs, "path": c["path"], "names": names})
                log(f"  HIGH  {c['path']} (+{c['added']} -{c['removed']}): {', '.join(names)}")
    for c in sorted(changed.values(), key=lambda c: -(c["added"] + c["removed"])):
        users = sum(1 for d in deps.values() if d.get(c["path"]) == c["from_blob"])
        state = "GONE" if c["target_blob"] is None else tu_deps.dep_kind(c["path"])
        log(f"DEP {state:6} +{c['added']:<5} -{c['removed']:<5} {users:>2} TU  {c['path']}")
    return {"changed": list(changed.values()), "tus": tus, "high": high}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", type=Path, default=DEFAULT_TREE)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--target", default=UPSTREAM)
    ap.add_argument("--json", action="store_true", help="also write tmp/c_drift.json")
    ap.add_argument("--record", nargs="*", metavar="RS_FILE")
    ap.add_argument("--at", metavar="REV", help="--record at REV instead of merge-base")
    ap.add_argument("--deps-only", action="store_true", help="--record deps only, keep c_blob_sha")
    args = ap.parse_args()
    if (args.at or args.deps_only) and args.record is None:
        ap.error("--at/--deps-only require --record")
    log = make_log("check_c_drift")
    tree, target = args.tree, args.target

    rows = tu_rows(args.db)
    base = None
    if args.record is not None or any(sha is None for _, _, sha in rows):
        base = series_base(tree, target)
    if args.record is not None:
        return record(tree, args.at or base, rows, args.record, log, args.deps_only)

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
            added, removed = numstat(tree, frm, now)
        if recorded is None:
            state += "(UNRECORDED)"
        out.append({"c_file": c_file, "rs_file": rs_file, "state": state, "from_blob": frm,
                    "target_blob": now, "added": added, "removed": removed})
        log(f"{state:19} +{added:<5} -{removed:<5} {c_file}  ({rs_file})")

    drifted = [r for r in out if not r["state"].startswith("same")]
    deps = dep_rows(args.db)
    for rs_file, _, _ in rows:
        if rs_file not in deps:
            log(f"NODEPS  {rs_file} (check_c_drift.py --record; dev.py db)")
    dd = dep_drift(tree, target, deps, log)
    kinds = defaultdict(int)
    for c in dd["changed"]:
        kinds[tu_deps.dep_kind(c["path"])] += 1
    log(f"SUMMARY: {len(drifted)}/{len(out)} translated TUs have C drift since translation..{target}")
    log(f"SUMMARY: {len(dd['tus'])}/{len(out)} TUs have dep drift; changed deps: "
        f"{kinds['header']} header, {kinds['rust']} rust; HIGH (re-implemented inline touched): "
        f"{len(dd['high'])} in {len({h['rs_file'] for h in dd['high']})} TUs")
    if args.json:
        TMP.mkdir(exist_ok=True)
        (TMP / "c_drift.json").write_text(json.dumps(
            {"target": target_sha, "rows": out, "deps": dd}, indent=1))
    return 1 if drifted or dd["tus"] else 0


if __name__ == "__main__":
    sys.exit(main())
