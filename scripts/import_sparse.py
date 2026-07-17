#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build sparse from the local kernel.org mirror (Fedora's packaged 0.6.4
cannot parse this kernel version — no __typeof_unqual__ support; upstream
HEAD has it, see docs/patterns-db.md), run it over the corpus, import
diagnostics into rulesdb/patterns.db.

The sparse BUILD lives entirely under tmp/ (gitignored, rebuilt from the
local mirror at /mnt/2tb/git_mirror/sparse/sparse.git — never committed,
never vendored into the repo; per Dan's decision 2026-07-16, option 1:
build locally as part of the dev workflow, don't ship a compiled binary
in a public repo).

Usage: import_sparse.py [--rebuild] [--limit N]
Output: tmp/sparse-build/ (build dir), rows in patterns.db's
sparse_diagnostics table
Log: tmp/import_sparse.log
"""
import argparse
import json
import logging
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux"
TMP = REPO / "tmp"
BUILD_DIR = TMP / "sparse-build"
MIRROR = Path("/mnt/2tb/git_mirror/sparse/sparse.git")
DB = REPO / "rulesdb" / "patterns.db"
LOG = TMP / "import_sparse.log"

DIAG_RE = re.compile(r"^(\S+):(\d+):(\d+): (warning|error): (.*)$")


def ensure_sparse_binary(rebuild=False):
    sparse_bin = BUILD_DIR / "sparse"
    if sparse_bin.exists() and not rebuild:
        logging.info("reusing existing build at %s (pass --rebuild to refresh)", sparse_bin)
        return sparse_bin
    if not MIRROR.exists():
        logging.error("no local sparse mirror at %s — clone it first: "
                      "git clone --bare https://git.kernel.org/pub/scm/devel/"
                      "sparse/sparse.git %s", MIRROR, MIRROR)
        raise SystemExit(1)
    if BUILD_DIR.exists():
        subprocess.run(["rm", "-rf", str(BUILD_DIR)], check=True)
    subprocess.run(["git", "clone", str(MIRROR), str(BUILD_DIR)],
                   check=True, capture_output=True, timeout=120)
    rev = subprocess.run(["git", "-C", str(BUILD_DIR), "log", "-1", "--format=%H %cI"],
                         capture_output=True, text=True).stdout.strip()
    logging.info("building sparse from local mirror, HEAD %s", rev)
    subprocess.run(["make", "-j", str(__import__("os").cpu_count() or 4)],
                   cwd=BUILD_DIR, check=True, capture_output=True, timeout=300)
    ver = subprocess.run([str(sparse_bin), "--version"], capture_output=True,
                         text=True).stdout.strip()
    logging.info("built %s (%s)", sparse_bin, ver)
    return sparse_bin


def extract_checker_flags(command):
    """Reduce a clang compile_commands entry to sparse-compatible flags:
    -I/-D/-nostdinc plus -include with its argument. Sparse doesn't
    understand clang-only flags (-Wp,..., target triples, etc)."""
    argv = shlex.split(command)
    keep = []
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "-o":
            i += 2
            continue
        if a == "-include":
            keep.append(a)
            keep.append(argv[i + 1])
            i += 2
            continue
        if a.startswith(("-I", "-D", "-nostdinc")):
            keep.append(a)
        i += 1
    return keep


def normalize_path(p):
    p = p.replace(str(REPO) + "/", "")
    if p.startswith("linux/"):
        p = p[len("linux/"):]
    return p


def run_sparse(sparse_bin, entry):
    flags = extract_checker_flags(entry["command"])
    r = subprocess.run(
        [str(sparse_bin), *flags, "-Wsparse-all", entry["file"]],
        cwd=entry["directory"], capture_output=True, text=True, timeout=60,
    )
    out = []
    for line in r.stderr.splitlines():
        m = DIAG_RE.match(line)
        if not m:
            continue
        file_, line_, col, sev, msg = m.groups()
        out.append((normalize_path(file_), int(line_), int(col), sev, msg))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="rebuild sparse from the mirror")
    ap.add_argument("--limit", type=int, default=0, help="only check this many TUs (debug)")
    ap.add_argument(
        "--jobs", type=int, default=None,
        help="parallel sparse subprocesses (default: nproc). Unlike "
             "run_c2rust_baseline.py's adaptive default, sparse is a small, "
             "single-file static-analysis tool (no full Clang AST export), "
             "so it's CPU- not memory-bound — nproc is a reasonable default "
             "without needing a RLIMIT_AS-style cap.",
    )
    args = ap.parse_args()
    jobs = args.jobs or os.cpu_count() or 4

    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    sparse_bin = ensure_sparse_binary(rebuild=args.rebuild)

    cc_path = TREE / "compile_commands.json"
    if not cc_path.exists():
        logging.error("no %s", cc_path)
        return 1
    entries = [e for e in json.load(open(cc_path)) if e["file"].endswith(".c")]
    entries.sort(key=lambda e: e["file"])
    if args.limit:
        entries = entries[: args.limit]
    logging.info("running sparse over %d TUs (%d parallel jobs)", len(entries), jobs)

    if not DB.exists():
        logging.error("no %s — run scripts/build_db.py first", DB)
        return 1
    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sparse_diagnostics ("
        "id INTEGER PRIMARY KEY, file TEXT NOT NULL, line INTEGER NOT NULL, "
        "col INTEGER, severity TEXT NOT NULL, message TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sparse_file ON sparse_diagnostics(file)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sparse_severity ON sparse_diagnostics(severity)"
    )
    conn.execute("DELETE FROM sparse_diagnostics")

    n_diag = n_failed = 0
    # Worker threads only run run_sparse() (pure: subprocess in, list out,
    # no shared state). All conn.execute() calls happen here in the main
    # thread as each future completes, same pattern as
    # run_c2rust_baseline.py — sqlite3 connections aren't safe to share
    # across threads by default.
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_sparse, sparse_bin, entry): entry for entry in entries}
        for i, fut in enumerate(as_completed(futures)):
            try:
                diags = fut.result()
            except subprocess.TimeoutExpired:
                n_failed += 1
                continue
            for file_, line_, col, sev, msg in diags:
                conn.execute(
                    "INSERT INTO sparse_diagnostics (file, line, col, severity, message) "
                    "VALUES (?,?,?,?,?)",
                    (file_, line_, col, sev, msg),
                )
                n_diag += 1
            if (i + 1) % 300 == 0:
                logging.info("%d/%d TUs checked, %d diagnostics so far, %d timeouts",
                             i + 1, len(entries), n_diag, n_failed)
                conn.commit()

    conn.commit()
    conn.close()
    logging.info("DONE: %d diagnostics from %d TUs (%d timed out)",
                 n_diag, len(entries), n_failed)
    print(f"IMPORT OK: {n_diag} sparse diagnostics into {DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
