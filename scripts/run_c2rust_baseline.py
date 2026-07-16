#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run c2rust transpile over every lib/*.c TU in compile_commands.json,
one TU at a time (isolated compile_commands.json per file so one crash
doesn't abort the batch), and record pass/warn/crash outcomes.

This is a baseline/triage run, not a translation source — see
docs/phase0-evals.md's verdict (c2rust is a reference emitter/idiom
corpus, not a foundation: it silently drops top-level declarations and
can crash outright on common kernel idioms like GCC's label-address
extension). The point of this run is to build a concrete failure
inventory to prioritize fixing awtoau/c2rust (our fork) against, not to
draft translations.

Usage: run_c2rust_baseline.py [--limit N]
Output: tmp/c2rust-baseline/<safe_name>/ (per-TU transpile output/logs)
Log: tmp/run_c2rust_baseline.log, rows appended to
     tmp/c2rust-baseline-results.jsonl
"""
import argparse
import json
import logging
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
TMP = REPO / "tmp"
OUT_DIR = TMP / "c2rust-baseline"
LOG = TMP / "run_c2rust_baseline.log"
RESULTS = TMP / "c2rust-baseline-results.jsonl"
# awtoau/c2rust fork, built with the RVV-builtin-type fix (see
# AstExporter.cpp's isRVVSizelessBuiltinType() change, 2026-07-17,
# awtoau/c2rust#1) — NOT the stock ~/.cargo/bin/c2rust upstream build.
C2RUST_FORK = Path("/mnt/2tb/git/github.com/awtoau/c2rust")
C2RUST = str(C2RUST_FORK / "target" / "release" / "c2rust")


def safe_name(file_path):
    return file_path.replace("/", "_").lstrip("_")


# 2026-07-17, Dan's request: "extract the issues in the file... so we get
# patterns of failures" — parse the raw transpile.log into deduplicatable
# (kind, source_file:line, detail) signatures instead of just counts.
WARNING_RE = re.compile(r"^(\S+):(\d+):(\d+): warning: c2rust: (.+)$")
MACRO_RE = re.compile(r"^\S+:\d+:\d+: note: expanded from macro '(\w+)'$")
PANIC_RE = re.compile(r"^thread '.*' \(\d+\) panicked at (\S+):(\d+):(\d+):$")
MISSING_NODE_RE = re.compile(r"^warning: Missing top-level node with id: (\d+)$")
MISSING_CHILD_RE = re.compile(r"^warning: Missing child \d+ of node AstNode \{ tag: (\w+),.*?loc: SrcSpan \{ fileid: \d+, begin_line: (\d+), begin_column: (\d+)")


def extract_signatures(stderr):
    """Yield (kind, source_file, source_line, detail) tuples."""
    lines = stderr.splitlines()
    for i, line in enumerate(lines):
        m = WARNING_RE.match(line)
        if m:
            src_file, src_line, _col, msg = m.groups()
            # The next 1-2 lines are often "note: expanded from macro 'X'"
            # — capture the innermost macro name if present, it's the
            # actual reusable signature (many call sites, one macro).
            macro = None
            for j in range(i + 1, min(i + 6, len(lines))):
                mm = MACRO_RE.match(lines[j])
                if mm:
                    macro = mm.group(1)
                if lines[j].startswith("In file included from") or WARNING_RE.match(lines[j]):
                    break
            detail = f"{msg} (macro: {macro})" if macro else msg
            yield ("ast_warning", src_file, int(src_line), detail)
            continue
        m = PANIC_RE.match(line)
        if m:
            src_file, src_line, _col = m.groups()
            msg = lines[i + 1].strip() if i + 1 < len(lines) else "(no message)"
            yield ("panic", src_file, int(src_line), msg)
            continue
        m = MISSING_CHILD_RE.match(line)
        if m:
            tag, src_line, _col = m.groups()
            yield ("missing_child", None, int(src_line), f"tag: {tag}")
            continue
        m = MISSING_NODE_RE.match(line)
        if m:
            yield ("missing_top_level_node", None, None, "AST export incomplete (no location)")
            continue


# Hard per-process address-space cap. 2026-07-17: an unbounded 32-way
# parallel run exhausted host RAM and triggered the OOM killer, which
# took the desktop session down with it (confirmed via dmesg). Each
# c2rust invocation runs a full Clang AST export over kernel headers —
# capping RLIMIT_AS means a single runaway TU dies with its own
# allocation failure (recorded as an "oom" outcome) instead of taking
# the whole host down; sized well above what a normal TU needs (~1-2GB
# observed) with headroom for larger files.
PER_PROC_MEM_LIMIT_BYTES = 4 * 1024 * 1024 * 1024


def _limit_memory():
    import resource
    resource.setrlimit(
        resource.RLIMIT_AS, (PER_PROC_MEM_LIMIT_BYTES, PER_PROC_MEM_LIMIT_BYTES)
    )


def run_one(entry):
    file_path = entry["file"]
    rel = Path(file_path).relative_to(TREE)
    name = safe_name(str(rel))
    work = OUT_DIR / name
    work.mkdir(parents=True, exist_ok=True)

    cc_path = work / "compile_commands.json"
    cc_path.write_text(json.dumps([entry], indent=1))

    proc = subprocess.run(
        [C2RUST, "transpile", str(cc_path), "-o", str(work / "output")],
        cwd=TREE,
        preexec_fn=_limit_memory,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log_path = work / "transpile.log"
    log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)

    stderr = proc.stderr
    crashed = "panicked at" in stderr
    missing_nodes = stderr.count("Missing top-level node")
    missing_children = stderr.count("Missing child")
    label_addr = stderr.count("Cannot translate GNU address of label")
    warnings = stderr.count("warning:")

    rs_files = list((work / "output").rglob("*.rs")) if (work / "output").exists() else []

    # A process killed by hitting RLIMIT_AS is reported by the shell/
    # kernel as SIGKILL/SIGSEGV (negative returncode) with no coherent
    # panic message — distinguish it from a normal crash/failure.
    hit_mem_limit = proc.returncode < 0 and not crashed and not rs_files

    if hit_mem_limit:
        outcome = "oom"
    elif crashed:
        outcome = "crash"
    elif not rs_files:
        outcome = "no_output"
    elif missing_nodes or missing_children:
        outcome = "dropped_decls"
    else:
        outcome = "clean"

    signatures = [
        {"kind": k, "source_file": sf, "source_line": sl, "detail": d}
        for k, sf, sl, d in extract_signatures(stderr)
    ]

    return {
        "file": str(rel),
        "outcome": outcome,
        "returncode": proc.returncode,
        "warnings": warnings,
        "missing_top_level_nodes": missing_nodes,
        "missing_children": missing_children,
        "label_address_exprs": label_addr,
        "rs_files_emitted": len(rs_files),
        "signatures": signatures,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--jobs", type=int, default=4,
        help="parallel c2rust subprocesses (each is a separate OS process — "
             "plain threads suffice to fan them out, no free-threaded/nogil "
             "Python needed). DEFAULT IS DELIBERATELY LOW (4): each "
             "c2rust invocation runs a full Clang AST export over kernel "
             "headers and can use multiple GB of RAM; a prior run at "
             "--jobs 32 (== nproc) exhausted host memory and triggered "
             "the OOM killer, which took down the desktop session and "
             "several unrelated processes (2026-07-17 08:08 incident,  "
             "confirmed via dmesg/journalctl). Raise cautiously and only "
             "while watching `free -h`/`dmesg -wT` in another terminal.",
    )
    args = ap.parse_args()

    TMP.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    cc_path = TREE / "compile_commands.json"
    if not cc_path.exists():
        logging.error("no %s", cc_path)
        return 1

    entries = [e for e in json.load(open(cc_path)) if "/lib/" in e["file"] and e["file"].endswith(".c")]
    # Dedup by file (compile_commands.json can list a TU more than once).
    seen = {}
    for e in entries:
        seen[e["file"]] = e
    entries = sorted(seen.values(), key=lambda e: e["file"])
    if args.limit:
        entries = entries[: args.limit]
    logging.info(
        "running c2rust transpile over %d lib/*.c TUs (%d parallel jobs)",
        len(entries), args.jobs,
    )

    results = []
    with open(RESULTS, "w") as rf, ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_one, entry): entry for entry in entries}
        done = 0
        for fut in as_completed(futures):
            entry = futures[fut]
            try:
                r = fut.result()
            except subprocess.TimeoutExpired:
                r = {"file": entry["file"], "outcome": "timeout"}
            results.append(r)
            rf.write(json.dumps(r) + "\n")
            rf.flush()
            done += 1
            if done % 10 == 0 or done == len(entries):
                logging.info("%d/%d done", done, len(entries))

    from collections import Counter
    counts = Counter(r["outcome"] for r in results)
    logging.info("DONE: %s", dict(counts))
    for outcome in ("crash", "no_output", "timeout", "oom"):
        bad = [r["file"] for r in results if r.get("outcome") == outcome]
        if bad:
            logging.info("%s (%d): %s", outcome, len(bad), ", ".join(bad[:20]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
