#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run c2rust transpile over every compiled .c TU in this build's
compile_commands.json (excluding scripts/, host-side build tooling), one
TU at a time (isolated compile_commands.json per file so one crash
doesn't abort the batch), and record pass/warn/crash outcomes per file
and per declaration.

awtoau/c2rust is staged to become linux-rs's primary translation
source, gated by both the full verification pipeline and rule
conformance — this run is the reliability side of that: a concrete
failure inventory to prioritize fixing the fork against, run over the
whole kernel-source corpus this build compiles (not just lib/), since
drivers/, kernel/, fs/, and mm/ together are the majority of it.

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
import time
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

# See build_c2rust_pch.py: a Clang PCH built from the single largest
# group of TUs that share identical stable compile flags (89% of this
# build's corpus). Every real per-TU command in compile_commands.json
# pulls in the same 3 kernel headers via this -include triple; swapping
# it for -include-pch lets Clang reuse the already-parsed header AST
# instead of re-lexing/re-parsing it from scratch on every single-TU
# c2rust invocation (see awtoau/c2rust#2 — this was the dominant fixed
# per-process cost that made concurrency scale sub-linearly). Only valid
# for TUs whose OTHER stable flags also match what the PCH was built
# with (see load_pch_flags/command_matches_pch below) — Clang's own
# PCH-validity check would reject a mismatch (e.g. -ffreestanding on
# one side only) rather than silently diverge, so a membership miss
# just falls back to the plain -include path below, never a silent
# correctness risk.
PCH_DIR = TMP / "c2rust-pch"
PCH_FILE = PCH_DIR / "preamble.pch"
PCH_FLAGS_FILE = PCH_DIR / "dominant_flags.json"
OLD_INCLUDES = (
    "-include ./include/linux/compiler-version.h "
    "-include ./include/linux/kconfig.h "
    "-include ./include/linux/compiler_types.h"
)


def safe_name(file_path):
    return file_path.replace("/", "_").lstrip("_")


# Per-file identity macros KBUILD injects (module path/name for
# __FILE__-style diagnostics and MODULE_* macros) — the only flags that
# differ between TUs that otherwise share every other compile flag, so
# they're stripped before comparing a TU's flags against the PCH's
# recorded flag set. Must match build_c2rust_pch.py's PER_FILE_PREFIXES
# exactly, or membership here could disagree with what the PCH was
# actually built from.
PCH_PER_FILE_PREFIXES = (
    "-DKBUILD_MODFILE=",
    "-DKBUILD_BASENAME=",
    "-DKBUILD_MODNAME=",
    "-D__KBUILD_MODNAME=",
    "-Wp,-MMD,",
)


def _strip_per_file_flags(tokens):
    out = []
    skip_next = False
    for t in tokens:
        if skip_next:
            skip_next = False
            continue
        if t == "-o":
            skip_next = True
            continue
        if t == "-c":
            continue
        if any(t.startswith(p) for p in PCH_PER_FILE_PREFIXES):
            continue
        out.append(t)
    return tuple(out)


def load_pch_flags():
    """The stable flag set build_c2rust_pch.py built the PCH from, or
    None if no PCH has been built yet — callers must treat None as "PCH
    unavailable, use plain -include for every TU" rather than erroring,
    since PCH use is a performance optimization, not a correctness
    requirement."""
    if not PCH_FILE.exists() or not PCH_FLAGS_FILE.exists():
        return None
    return tuple(json.loads(PCH_FLAGS_FILE.read_text())["flags"])


def command_matches_pch(command, pch_flags):
    """True if this TU's own stable flags are identical to the flags the
    PCH was built with — the only condition under which swapping its
    plain -include triple for -include-pch is valid. Comparing the
    stripped flag tuples directly (not just "does OLD_INCLUDES appear
    in the string") catches every kind of mismatch Clang's own PCH
    ABI-validity check would otherwise catch at run time (e.g.
    -ffreestanding present on one side only), so a membership miss here
    is always a clean fallback to plain -include, never a run that
    trips Clang's "was disabled ... but is currently enabled" error."""
    toks = command.split()
    body = toks[1:-1]  # drop leading compiler name and trailing file path
    return _strip_per_file_flags(body) == pch_flags


def use_pch_if_eligible(command, pch_flags):
    """Rewrite command's -include triple to -include-pch when eligible;
    return command unchanged otherwise (minority flag-set groups keep
    using plain -include, same as before PCH support existed)."""
    if pch_flags is None or OLD_INCLUDES not in command:
        return command
    if not command_matches_pch(command, pch_flags):
        return command
    return command.replace(OLD_INCLUDES, f"-include-pch {PCH_FILE}")


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
    new_columns = (
        ("c2rust_attempts", "corpus_rev", "TEXT"),
        ("c2rust_decl_outcomes", "corpus_rev", "TEXT"),
        ("c2rust_attempts", "peak_rss_bytes", "INTEGER"),
        ("c2rust_attempts", "duration_s", "REAL"),
    )
    for table, col, coltype in new_columns:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
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

# Fallback per-job memory estimate when no prior-run peak_rss_bytes
# history exists yet (first run ever, or a fresh DB) — deliberately
# conservative since it's untested. Once real data exists,
# _typical_job_memory_bytes() uses the observed p90 instead: measured
# full-corpus peak RSS was p50=532MB/p90=649MB/max=767MB, an order of
# magnitude below this guess and below PER_PROC_MEM_LIMIT_BYTES (the
# RLIMIT_AS safety-net ceiling, which stays fixed regardless — a
# genuine runaway outlier still gets killed at 4GB, this only changes
# how many jobs we plan to run concurrently).
FALLBACK_JOB_MEMORY_BYTES = 1 * 1024 * 1024 * 1024


def _typical_job_memory_bytes():
    """p90 of real peak_rss_bytes from the most recent baseline run in
    patterns.db, or FALLBACK_JOB_MEMORY_BYTES if no history exists."""
    try:
        import sqlite3
        conn = sqlite3.connect(DB)
        latest_run_at = conn.execute(
            "SELECT MAX(run_at) FROM c2rust_attempts WHERE peak_rss_bytes IS NOT NULL"
        ).fetchone()[0]
        if latest_run_at is None:
            return FALLBACK_JOB_MEMORY_BYTES
        rows = conn.execute(
            "SELECT peak_rss_bytes FROM c2rust_attempts "
            "WHERE run_at = ? AND peak_rss_bytes IS NOT NULL ORDER BY peak_rss_bytes",
            (latest_run_at,),
        ).fetchall()
        conn.close()
        if not rows:
            return FALLBACK_JOB_MEMORY_BYTES
        vals = [r[0] for r in rows]
        p90 = vals[min(len(vals) - 1, int(len(vals) * 0.9))]
        return max(p90, 64 * 1024 * 1024)  # floor: never plan below 64MB/job
    except Exception:
        return FALLBACK_JOB_MEMORY_BYTES


def adaptive_job_count():
    """min(nproc, (free_ram - headroom) / typical-job-memory) — scale
    concurrency to what real observed usage says is safe right now,
    instead of budgeting against the RLIMIT_AS ceiling (which is a
    per-process safety net for a runaway outlier, not a plan for the
    common case — budgeting against it left throughput on the table:
    real full-corpus peak RSS was under 800MB while the ceiling is 4GB,
    a 5x+ undercount of safe concurrency). Falls back to a conservative
    fixed estimate when no prior-run history exists yet."""
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
    per_job = _typical_job_memory_bytes()
    by_memory = max(1, budget // per_job)
    return max(1, min(os.cpu_count() or 4, by_memory))


def _limit_memory():
    import resource
    resource.setrlimit(
        resource.RLIMIT_AS, (PER_PROC_MEM_LIMIT_BYTES, PER_PROC_MEM_LIMIT_BYTES)
    )


def _peak_rss_bytes(pid):
    """Current VmHWM (peak resident set size so far) for pid and all its
    live children, summed. Best-effort — a process that already exited
    between the listing and the read just contributes 0."""
    total = 0
    pids = [pid]
    try:
        pids += [int(p) for p in os.listdir(f"/proc/{pid}/task/{pid}/children") if p]
    except Exception:
        pass
    # also catch grandchildren c2rust may spawn (e.g. an invoked clang)
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=2)
        pids += [int(p) for p in out.stdout.split() if p]
    except Exception:
        pass
    for p in set(pids):
        try:
            with open(f"/proc/{p}/status") as f:
                for line in f:
                    if line.startswith("VmHWM:"):
                        total += int(line.split()[1]) * 1024  # kB -> bytes
                        break
        except Exception:
            continue
    return total


def run_one(entry, pch_flags=None):
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

    command = use_pch_if_eligible(entry["command"], pch_flags)
    used_pch = command != entry["command"]
    entry = dict(entry, command=command)

    cc_path = work / "compile_commands.json"
    cc_path.write_text(json.dumps([entry], indent=1))

    start = time.monotonic()
    proc = subprocess.Popen(
        [C2RUST, "transpile", str(cc_path), "-o", str(work / "output"), "--overwrite-existing"],
        cwd=TREE,
        preexec_fn=_limit_memory,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    peak_rss = 0
    try:
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                peak_rss = max(peak_rss, _peak_rss_bytes(proc.pid))
                if time.monotonic() - start > 120:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    raise subprocess.TimeoutExpired(proc.args, 120)
    except subprocess.TimeoutExpired:
        raise
    peak_rss = max(peak_rss, _peak_rss_bytes(proc.pid))
    duration_s = time.monotonic() - start

    class _Result:
        pass
    proc_result = _Result()
    proc_result.returncode = proc.returncode
    proc_result.stdout = stdout
    proc_result.stderr = stderr
    proc = proc_result

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
        "peak_rss_bytes": peak_rss,
        "duration_s": duration_s,
        "signatures": signatures,
        "decl_outcomes": decl_outcomes,
        "used_pch": used_pch,
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

    # Full kernel-source build corpus, not just lib/ — the earlier lib/-only
    # filter was leftover from early hand-translation scoping and never
    # revisited; it left drivers/, kernel/, fs/, mm/ (the majority of this
    # build's 548 TUs) completely untested against c2rust. Excludes
    # scripts/ (host-side build tooling, not kernel code) and generated
    # wrapper TUs.
    entries = [
        e for e in json.load(open(cc_path))
        if e["file"].endswith(".c")
        and "/scripts/" not in e["file"]
        and ".vmlinux.export.c" not in e["file"]
    ]
    # Dedup by file (compile_commands.json can list a TU more than once).
    seen = {}
    for e in entries:
        seen[e["file"]] = e
    entries = sorted(seen.values(), key=lambda e: e["file"])
    if args.limit:
        entries = entries[: args.limit]

    pch_flags = load_pch_flags()
    if pch_flags is None:
        logging.info("no PCH built (run build_c2rust_pch.py) — every TU uses plain -include")
        n_pch = 0
    else:
        n_pch = sum(1 for e in entries if command_matches_pch(e["command"], pch_flags))
        logging.info(
            "PCH loaded from %s — %d/%d TUs eligible for -include-pch, "
            "rest fall back to plain -include",
            PCH_FILE, n_pch, len(entries),
        )
    logging.info(
        "running c2rust transpile over %d .c TUs (%d parallel jobs)",
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
        futures = {pool.submit(run_one, entry, pch_flags): entry for entry in entries}
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
                " rs_files_emitted, c2rust_rev, corpus_rev, peak_rss_bytes, duration_s) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rel, run_at, r["outcome"], r.get("returncode"),
                    r.get("warnings"), r.get("missing_top_level_nodes"),
                    r.get("missing_children"), r.get("label_address_exprs"),
                    r.get("rs_files_emitted"), rev, crev,
                    r.get("peak_rss_bytes"), r.get("duration_s"),
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
    used_pch_count = sum(1 for r in results if r.get("used_pch"))
    logging.info("DONE: %s (c2rust %s, corpus %s, %d/%d ran with -include-pch) into %s",
                 dict(counts), rev, crev, used_pch_count, len(results), DB)
    for outcome in ("crash", "no_output", "timeout", "oom"):
        bad = [r["file"] for r in results if r.get("outcome") == outcome]
        if bad:
            logging.info("%s (%d): %s", outcome, len(bad), ", ".join(bad[:20]))

    # Bottleneck data: real peak RSS / wall-clock per TU, not the
    # RLIMIT_AS ceiling — tells us what --jobs could actually be, and
    # which files are the slowest/heaviest.
    rss_vals = sorted((r.get("peak_rss_bytes") or 0) for r in results)
    dur_vals = sorted((r.get("duration_s") or 0) for r in results)
    if rss_vals:
        def pct(vals, p):
            return vals[min(len(vals) - 1, int(len(vals) * p))]
        logging.info(
            "peak RSS: p50=%.0fMB p90=%.0fMB max=%.0fMB",
            pct(rss_vals, 0.5) / 1e6, pct(rss_vals, 0.9) / 1e6, rss_vals[-1] / 1e6,
        )
        logging.info(
            "duration: p50=%.1fs p90=%.1fs max=%.1fs",
            pct(dur_vals, 0.5), pct(dur_vals, 0.9), dur_vals[-1],
        )
        slowest = sorted(results, key=lambda r: -(r.get("duration_s") or 0))[:10]
        logging.info("slowest TUs:")
        for r in slowest:
            logging.info("  %6.1fs  %5.0fMB  %s", r.get("duration_s") or 0,
                        (r.get("peak_rss_bytes") or 0) / 1e6, r["file"])

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
