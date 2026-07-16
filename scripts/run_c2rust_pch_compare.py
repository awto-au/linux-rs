#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare c2rust transpile outcomes between plain -include and
-include-pch for every lib/*.c TU, using a PCH built with flags matched
to the exact per-TU compile flags (kernel headers/target/std/etc are
identical across all lib/*.c TUs; only KBUILD_* file-identity macros
differ, and those are deliberately excluded from the PCH build so they
can still be supplied on the command line per TU).

Investigation for the c2rust PCH AST-export divergence report
(awtoau/c2rust#1). Mirrors run_c2rust_baseline.py's per-TU isolated
compile_commands.json + RLIMIT_AS convention.

Usage: run_c2rust_pch_compare.py [--limit N]
Output: tmp/c2rust-pch-compare/<safe_name>/{nopch,pch}/
Log: tmp/run_c2rust_pch_compare.log
"""
import argparse
import json
import logging
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
TMP = REPO / "tmp"
OUT_DIR = TMP / "c2rust-pch-compare"
LOG = TMP / "run_c2rust_pch_compare.log"
C2RUST_FORK = Path("/mnt/2tb/git/github.com/awtoau/c2rust")
C2RUST = str(C2RUST_FORK / "target" / "release" / "c2rust")
PCH_FILE = TMP / "pch-debug" / "kernel-pch" / "preamble.pch"

OLD_INCLUDES = (
    "-include ./include/linux/compiler-version.h "
    "-include ./include/linux/kconfig.h "
    "-include ./include/linux/compiler_types.h"
)

PER_PROC_MEM_LIMIT_BYTES = 4 * 1024 * 1024 * 1024


def _limit_memory():
    import resource
    resource.setrlimit(
        resource.RLIMIT_AS, (PER_PROC_MEM_LIMIT_BYTES, PER_PROC_MEM_LIMIT_BYTES)
    )


def safe_name(file_path):
    return file_path.replace("/", "_").lstrip("_")


def signature_counts(stderr):
    return {
        "warnings": stderr.count("warning:"),
        "missing_top_level_nodes": stderr.count("Missing top-level node"),
        "missing_children": stderr.count("Missing child"),
        "panicked": "panicked at" in stderr,
    }


def run_variant(entry, work, use_pch):
    cc_path = work / "compile_commands.json"
    if use_pch:
        cmd = entry["command"].replace(OLD_INCLUDES, f"-include-pch {PCH_FILE}")
    else:
        cmd = entry["command"]
    e2 = dict(entry)
    e2["command"] = cmd
    cc_path.write_text(json.dumps([e2], indent=1))

    proc = subprocess.run(
        [C2RUST, "transpile", str(cc_path), "-o", str(work / "output")],
        cwd=TREE,
        preexec_fn=_limit_memory,
        capture_output=True,
        text=True,
        timeout=120,
    )
    (work / "transpile.log").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
    counts = signature_counts(proc.stderr)
    counts["returncode"] = proc.returncode
    rs_files = list((work / "output").rglob("*.rs")) if (work / "output").exists() else []
    counts["rs_files_emitted"] = len(rs_files)
    return counts


def run_one(entry):
    file_path = entry["file"]
    rel = Path(file_path).relative_to(TREE)
    name = safe_name(str(rel))
    base = OUT_DIR / name
    (base / "nopch").mkdir(parents=True, exist_ok=True)
    (base / "pch").mkdir(parents=True, exist_ok=True)

    try:
        nopch = run_variant(entry, base / "nopch", use_pch=False)
    except subprocess.TimeoutExpired:
        nopch = {"timeout": True}
    try:
        pch = run_variant(entry, base / "pch", use_pch=True)
    except subprocess.TimeoutExpired:
        pch = {"timeout": True}

    diverged = (
        nopch.get("missing_top_level_nodes") != pch.get("missing_top_level_nodes")
        or nopch.get("missing_children") != pch.get("missing_children")
        or nopch.get("panicked") != pch.get("panicked")
        or nopch.get("rs_files_emitted") != pch.get("rs_files_emitted")
    )

    return {"file": str(rel), "nopch": nopch, "pch": pch, "diverged": diverged}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    TMP.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not PCH_FILE.exists():
        logging.error("no PCH at %s -- build it first", PCH_FILE)
        return 1

    cc_path = TREE / "compile_commands.json"
    entries = [e for e in json.load(open(cc_path)) if "/lib/" in e["file"] and e["file"].endswith(".c")]
    seen = {}
    for e in entries:
        seen[e["file"]] = e
    entries = sorted(seen.values(), key=lambda e: e["file"])
    if args.limit:
        entries = entries[: args.limit]
    logging.info("comparing nopch vs pch over %d lib/*.c TUs (%d jobs)", len(entries), args.jobs)

    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_one, e): e for e in entries}
        done = 0
        for fut in as_completed(futures):
            entry = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:
                r = {"file": entry["file"], "error": str(exc)}
            results.append(r)
            done += 1
            if done % 10 == 0 or done == len(entries):
                logging.info("%d/%d done", done, len(entries))

    diverged = [r for r in results if r.get("diverged")]
    logging.info("DONE: %d/%d diverged", len(diverged), len(results))
    for r in diverged:
        logging.info("DIVERGED %s: nopch=%s pch=%s", r["file"], r["nopch"], r["pch"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
