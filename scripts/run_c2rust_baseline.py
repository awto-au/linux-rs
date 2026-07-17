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
Output: tmp/c2rust-baseline/<safe_name>/ (per-TU transpile output/logs);
        results written directly to rulesdb/patterns.db's c2rust_attempts/
        c2rust_failure_signatures/c2rust_decl_outcomes tables (persisted
        across DB rebuilds — see build_db.py's PERSISTENT_TABLES).
Log: tmp/run_c2rust_baseline.log
"""
import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
TMP = REPO / "tmp"
OUT_DIR = TMP / "c2rust-baseline"
LOG = TMP / "run_c2rust_baseline.log"
DB = REPO / "rulesdb" / "patterns.db"
# awtoau/c2rust fork, built with the RVV-builtin-type fix (see
# AstExporter.cpp's isRVVSizelessBuiltinType() change, 2026-07-17,
# awtoau/c2rust#1) — NOT the stock ~/.cargo/bin/c2rust upstream build.
C2RUST_FORK = Path("/mnt/2tb/git/github.com/awtoau/c2rust")
C2RUST = str(C2RUST_FORK / "target" / "release" / "c2rust")


def safe_name(file_path):
    return file_path.replace("/", "_").lstrip("_")


def git_rev(repo_dir):
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return out
    except Exception:
        return None


def corpus_rev():
    """linux-riscv's own HEAD — the C-file corpus fed to c2rust changes
    independently of the c2rust tool itself as TUs get translated to
    Rust and drop out of the .c corpus. Without this, a clean-count
    shift could be misread as a c2rust regression when it's really
    "there are fewer/different .c files now"."""
    return git_rev(TREE)


def ensure_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_attempts ("
        "id INTEGER PRIMARY KEY, c_file TEXT NOT NULL, run_at TEXT NOT NULL, "
        "outcome TEXT NOT NULL, returncode INTEGER, warnings INTEGER, "
        "missing_top_level_nodes INTEGER, missing_children INTEGER, "
        "label_address_exprs INTEGER, rs_files_emitted INTEGER, "
        "c2rust_rev TEXT, corpus_rev TEXT, notes TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_failure_signatures ("
        "id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES c2rust_attempts(id), "
        "c_file TEXT NOT NULL, kind TEXT NOT NULL, source_file TEXT, "
        "source_line INTEGER, detail TEXT NOT NULL)"
    )
    # Per-declaration outcome: which of a file's own top-level functions
    # actually made it into the emitted Rust vs were silently dropped. A
    # file-level outcome (e.g. "dropped_decls") hides whether one
    # function was lost out of 40 or the whole file fell over; this is
    # the ground truth c2rust_regression_check.py diffs on.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_decl_outcomes ("
        "id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES c2rust_attempts(id), "
        "c_file TEXT NOT NULL, decl_name TEXT NOT NULL, translated INTEGER NOT NULL, "
        "c2rust_rev TEXT, corpus_rev TEXT, run_at TEXT NOT NULL)"
    )
    import sqlite3
    for table, col in (("c2rust_attempts", "corpus_rev"), ("c2rust_decl_outcomes", "corpus_rev")):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists (older DB carried forward by build_db.py)


NO_MANGLE_FN_RE = re.compile(r'^\s*(?:pub\s+)?(?:unsafe\s+)?(?:extern\s+"C"\s+)?fn\s+(\w+)\s*\(')


def c_top_level_functions(c_path):
    """Enumerate this file's own top-level function names via ctags —
    the ground truth for "what decls does this TU actually define",
    independent of whatever c2rust did or didn't manage to export."""
    try:
        proc = subprocess.run(
            ["ctags", "-x", "--c-kinds=f", str(c_path)],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except Exception:
        return set()
    names = set()
    for line in proc.stdout.splitlines():
        parts = line.split(None, 1)
        if parts:
            names.add(parts[0])
    return names


def rs_translated_functions(rs_path):
    """Enumerate function names c2rust actually emitted for THIS file's
    own decls — identified by the #[no_mangle] attribute immediately
    preceding `fn <name>(`, which c2rust only emits for the target TU's
    own top-level functions (header-inline pulls-in have no #[no_mangle]).
    """
    if not rs_path.exists():
        return set()
    try:
        lines = rs_path.read_text(errors="replace").splitlines()
    except Exception:
        return set()
    names = set()
    for i, line in enumerate(lines):
        if line.strip() == "#[no_mangle]":
            for j in range(i + 1, min(i + 4, len(lines))):
                m = NO_MANGLE_FN_RE.match(lines[j])
                if m:
                    names.add(m.group(1))
                    break
                if lines[j].strip() and not lines[j].strip().startswith(("pub ", "unsafe ", "#[")):
                    break
    return names


# Parse the raw transpile.log into deduplicatable (kind, source_file:line,
# detail) signatures instead of just counts, so failures cluster by
# pattern rather than by TU.
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

# Headroom reserved for the desktop session/VSCode/everything else on
# the host — the 2026-07-17 08:08 OOM incident killed exactly those.
# Not part of the per-job budget; subtracted before dividing.
HOST_HEADROOM_BYTES = 12 * 1024 * 1024 * 1024


def adaptive_job_count():
    """min(nproc, (free_ram - headroom) / per-proc-limit) — scale
    concurrency to what the RLIMIT_AS cap can actually make safe right
    now, instead of a fixed guess. With the memory cap as the real
    safety net, a hardcoded --jobs 4 leaves throughput on the table on a
    62GB/32-core box; this computes the largest job count whose
    worst-case (every job simultaneously at its RLIMIT_AS ceiling) still
    leaves HOST_HEADROOM_BYTES free."""
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                k, v = line.split(":", 1)
                meminfo[k] = int(v.strip().split()[0]) * 1024  # kB -> bytes
        free_bytes = meminfo.get("MemAvailable", 0)
    except Exception:
        free_bytes = 0

    if free_bytes <= 0:
        return 4  # /proc/meminfo unreadable — fall back to the old fixed default

    budget = free_bytes - HOST_HEADROOM_BYTES
    by_memory = max(1, budget // PER_PROC_MEM_LIMIT_BYTES)
    return max(1, min(os.cpu_count() or 4, by_memory))


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
    # c2rust transpile silently no-ops ("Skipping existing file") and
    # exits before running AST export/conversion at all if its output
    # .rs already exists from a prior run — work/ must be cleared
    # between invocations, or every run after the first one against a
    # given file just re-reports the FIRST run's (possibly stale,
    # possibly from a different c2rust revision) outcome as if fresh.
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    cc_path = work / "compile_commands.json"
    cc_path.write_text(json.dumps([entry], indent=1))

    proc = subprocess.run(
        [C2RUST, "transpile", str(cc_path), "-o", str(work / "output"), "--overwrite-existing"],
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

    # Per-declaration outcome: which of THIS file's own top-level
    # functions actually made it into the emitted Rust, vs which were
    # silently dropped. A file-level outcome (e.g. "dropped_decls") hides
    # whether one function was lost out of 40 or the whole file fell
    # over; this is the ground truth a regression check needs to tell
    # "got worse" from "always broken, now honestly reported" apart.
    c_funcs = c_top_level_functions(TREE / file_path)
    rs_funcs = set()
    for rf in rs_files:
        rs_funcs |= rs_translated_functions(rf)
    decl_outcomes = [
        {"name": fn, "translated": fn in rs_funcs} for fn in sorted(c_funcs)
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
        "decl_outcomes": decl_outcomes,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--jobs", type=int, default=None,
        help="parallel c2rust subprocesses (each is a separate OS process — "
             "plain threads suffice to fan them out, no free-threaded/nogil "
             "Python needed). DEFAULT IS ADAPTIVE: computed from current "
             "/proc/meminfo MemAvailable as "
             "min(nproc, (free - HOST_HEADROOM_BYTES) / PER_PROC_MEM_LIMIT_BYTES) "
             "— i.e. the largest job count whose worst case (every job "
             "simultaneously at its RLIMIT_AS ceiling) still leaves "
             "HOST_HEADROOM_BYTES free for the desktop session. Each "
             "c2rust invocation runs a full Clang AST export over kernel "
             "headers and is capped at PER_PROC_MEM_LIMIT_BYTES via "
             "RLIMIT_AS (see _limit_memory) — that cap, not --jobs, is "
             "the actual safety net; a prior UNCAPPED run at --jobs 32 "
             "(== nproc) exhausted host memory and triggered the OOM "
             "killer, which took down the desktop session and several "
             "unrelated processes (2026-07-17 08:08 incident, confirmed "
             "via dmesg/journalctl) — that incident predates the "
             "RLIMIT_AS cap added the same day. Pass --jobs explicitly "
             "to override the adaptive default; still watch `free -h` "
             "during a run, the same as any concurrency change here.",
    )
    args = ap.parse_args()
    if args.jobs is None:
        args.jobs = adaptive_job_count()

    TMP.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    logging.info(
        "jobs=%d (%s)", args.jobs,
        "explicit" if "--jobs" in sys.argv else "adaptive, from /proc/meminfo",
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

    import sqlite3
    from datetime import datetime, timezone

    if not DB.exists():
        logging.error("no %s — run scripts/build_db.py first", DB)
        return 1
    conn = sqlite3.connect(DB)
    ensure_schema(conn)
    run_at = datetime.now(timezone.utc).isoformat()
    rev = git_rev(C2RUST_FORK)
    crev = corpus_rev()

    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_one, entry): entry for entry in entries}
        done = 0
        for fut in as_completed(futures):
            entry = futures[fut]
            try:
                r = fut.result()
            except subprocess.TimeoutExpired:
                r = {"file": entry["file"], "outcome": "timeout"}
            results.append(r)

            # sqlite3 connections aren't thread-safe to share across
            # threads by default; all writes happen here, in the main
            # thread, as each future completes (as_completed yields
            # serially) — worker threads only run run_one(), never touch
            # conn.
            rel = r["file"]
            cur = conn.execute(
                "INSERT INTO c2rust_attempts "
                "(c_file, run_at, outcome, returncode, warnings, "
                " missing_top_level_nodes, missing_children, label_address_exprs, "
                " rs_files_emitted, c2rust_rev, corpus_rev) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rel, run_at, r["outcome"], r.get("returncode"),
                    r.get("warnings"), r.get("missing_top_level_nodes"),
                    r.get("missing_children"), r.get("label_address_exprs"),
                    r.get("rs_files_emitted"), rev, crev,
                ),
            )
            attempt_id = cur.lastrowid
            for sig in r.get("signatures", []):
                conn.execute(
                    "INSERT INTO c2rust_failure_signatures "
                    "(attempt_id, c_file, kind, source_file, source_line, detail) "
                    "VALUES (?,?,?,?,?,?)",
                    (attempt_id, rel, sig["kind"], sig.get("source_file"),
                     sig.get("source_line"), sig["detail"]),
                )
            for decl in r.get("decl_outcomes", []):
                conn.execute(
                    "INSERT INTO c2rust_decl_outcomes "
                    "(attempt_id, c_file, decl_name, translated, c2rust_rev, corpus_rev, run_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (attempt_id, rel, decl["name"], int(decl["translated"]), rev, crev, run_at),
                )

            done += 1
            if done % 10 == 0 or done == len(entries):
                conn.commit()
                logging.info("%d/%d done", done, len(entries))

    conn.commit()

    from collections import Counter
    counts = Counter(r["outcome"] for r in results)
    logging.info("DONE: %s (c2rust %s, corpus %s) into %s", dict(counts), rev, crev, DB)
    for outcome in ("crash", "no_output", "timeout", "oom"):
        bad = [r["file"] for r in results if r.get("outcome") == outcome]
        if bad:
            logging.info("%s (%d): %s", outcome, len(bad), ", ".join(bad[:20]))

    patterns = conn.execute(
        "SELECT kind, detail, tus_affected FROM c2rust_failure_patterns LIMIT 15"
    ).fetchall()
    logging.info("top failure patterns (fix-priority order):")
    for kind, detail, count in patterns:
        logging.info("  [%3d TUs] %-22s %s", count, kind, detail[:100])

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
