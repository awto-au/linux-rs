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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from build_c2rust_pch import PER_FILE_PREFIXES as PCH_PER_FILE_PREFIXES
from build_c2rust_pch import strip_per_file_flags as _strip_per_file_flags

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
TMP = REPO / "tmp"
OUT_DIR = TMP / "c2rust-baseline"
LOG = TMP / "run_c2rust_baseline.log"
DB = REPO / "rulesdb" / "patterns.db"
# awtoau/c2rust fork, built with the RVV-builtin-type fix (see
# AstExporter.cpp's isRVVSizelessBuiltinType() change, 2026-07-17,
# awtoau/c2rust#1) — NOT the stock ~/.cargo/bin/c2rust upstream build.
#
# C2RUST_FORK_DIR/C2RUST_BIN env overrides let a baseline run target an
# isolated `scripts/c2rust_worktree.py`-created worktree instead of the
# shared checkout, so verifying a fix in progress there doesn't collide
# with another agent's concurrent work in the shared checkout (see
# c2rust_worktree.py's module doc for the collision this was built to
# avoid). Same override convention as investigate_c2rust_failure.py's
# C2RUST_BIN. Unset, both default to the shared checkout as before.
C2RUST_FORK = Path(os.environ.get(
    "C2RUST_FORK_DIR", "/mnt/2tb/git/github.com/awtoau/c2rust"))
C2RUST = os.environ.get("C2RUST_BIN", str(C2RUST_FORK / "target" / "release" / "c2rust"))
# awtoau/c2rust's kernel-idiom rewrites (WARN_ON -> kernel::warn_on!,
# fls-family -> leading_zeros()/trailing_zeros() arithmetic) are opt-in so
# that stock `c2rust transpile` with no flags stays byte-for-byte identical
# to upstream immunant/c2rust. linux-rs's own baseline wants every rewrite
# this build of c2rust knows about, so it opts in explicitly rather than
# naming each rule (which would need updating here every time a new rule
# is added upstream).
C2RUST_ENABLE_RULE_ARGS = ["--enable-rule=all"]

# Files whose transitively-included header closure is large enough (tens
# of thousands of top-level declarations pulled in via headers like
# fs.h/sock.h/scheduler internals) to trigger an O(N x shared-subtree)
# blowup in c2rust-transpile's Translation::locate_comments — see
# awtoau/c2rust#4. locate_comments walks every top-level decl with a
# fresh per-call visited-set, so any AST subtree reachable from many
# decls (routine for a widely-used kernel type) gets re-walked from
# scratch once per decl that reaches it, instead of once total. This
# dominates wall-clock (~14-16s of a ~20s run) independent of the
# file's own source line count — init/do_mounts.c is 521 lines but
# costs as much as fs/select.c's 1438, while mm/vmscan.c (8068 lines,
# the largest file in this corpus) is unaffected because its header
# closure doesn't pull in the same widely-shared decl graph. None of
# these files transpile cleanly anyway (outcome is crash/dropped_decls
# for all of them in the last full run), so skipping them here loses no
# pass/fail signal, only the time spent reaching a non-answer. Remove
# entries (or pass --include-slow) once awtoau/c2rust#4 is fixed.
SLOW_FILES_EXCLUDED = {
    "fs/select.c",
    "init/do_mounts.c",
    "fs/file.c",
    "kernel/pid.c",
    "drivers/base/core.c",
    "kernel/fork.c",
    "kernel/sched/core.c",
    "lib/kobject_uevent.c",
    "kernel/extable.c",
    "lib/vsprintf.c",
}

# How many of the most recent distinct c2rust_rev runs to check for
# outcome stability — see stable_files() below. 3 balances "confident
# a file genuinely isn't moving" against "don't need years of history
# before anything gets excluded"; a file that flips outcome even once
# in that window stays in the routine run.
STABILITY_WINDOW_REVS = 3


def stable_files():
    """c_file values whose PER-DECLARATION translated/not-translated
    status hasn't changed across the last STABILITY_WINDOW_REVS distinct
    c2rust revisions tested — safe to skip in a routine RELIABILITY run.

    Keyed on c2rust_decl_outcomes (per-declaration), not c2rust_attempts'
    file-level `outcome` column — file-level outcome is too coarse a
    signal on its own: a WARN_ON-rewrite fix changes what gets EMITTED
    for a declaration that already failed to transpile for an unrelated
    reason (e.g. the _THIS_IP_ gap), so the file's outcome stays
    "dropped_decls" before and after even though the content genuinely
    changed. Using outcome alone would have made ~all 542 files register
    "stable" after the WARN_ON/fls-family/swap-mem-swap work landed.

    KNOWN LIMITATION (see awtoau/c2rust#5): even the per-declaration
    translated/not-translated BOOLEAN doesn't catch every real change —
    an idiom-rule rewrite (WARN_ON -> kernel::warn_on!, etc.) changes
    WHAT Rust gets emitted inside an already-succeeding declaration,
    never whether that declaration succeeds or fails, so `translated`
    stays identically `true` before and after even though the emitted
    code is materially different. Confirmed directly: drivers/base/
    class.c registers as "stable" here despite the WARN_ON fix that
    session genuinely changed its output, because every one of its
    declarations kept the same pass/fail status throughout. This
    function is therefore only a safe default-run shortcut for
    RELIABILITY work (crash/dropped_decls fixes, which by definition DO
    flip translated) — always pass --include-stable for a full sweep
    when verifying an idiom-rule change, never rely on the default
    routine run to catch a rewrite regression.

    Returns an empty set (skip nothing) if patterns.db doesn't have
    enough run history yet — this is a turnaround-time optimization, not
    a correctness gate, so "not enough data" must fail open (run
    everything) rather than silently exclude files nobody's actually
    confirmed are stable."""
    if not DB.exists():
        return set()
    import sqlite3
    conn = sqlite3.connect(DB)
    try:
        revs = [r[0] for r in conn.execute(
            "SELECT DISTINCT c2rust_rev FROM c2rust_decl_outcomes "
            "WHERE c2rust_rev IS NOT NULL ORDER BY run_at DESC"
        ).fetchall()]
        # dedupe while preserving order (a rev can have multiple run_at
        # timestamps if run_c2rust_baseline.py was invoked more than
        # once at the same commit)
        seen_revs = []
        for r in revs:
            if r not in seen_revs:
                seen_revs.append(r)
            if len(seen_revs) == STABILITY_WINDOW_REVS:
                break
        if len(seen_revs) < STABILITY_WINDOW_REVS:
            return set()

        placeholders = ",".join("?" * len(seen_revs))
        rows = conn.execute(
            f"SELECT c_file, decl_name, c2rust_rev, translated, run_at "
            f"FROM c2rust_decl_outcomes WHERE c2rust_rev IN ({placeholders})",
            seen_revs,
        ).fetchall()
        # Keep only each (c_file, c2rust_rev) pair's LATEST run_at, same
        # "one snapshot per rev" logic c2rust_regression_check.py uses —
        # a rev re-run twice shouldn't double-count as two data points.
        latest_run_at = {}
        for c_file, decl_name, rev, translated, run_at in rows:
            key = (c_file, rev)
            if key not in latest_run_at or run_at > latest_run_at[key]:
                latest_run_at[key] = run_at
        by_file_decl = {}  # (c_file, decl_name) -> {rev: translated}
        for c_file, decl_name, rev, translated, run_at in rows:
            if run_at != latest_run_at[(c_file, rev)]:
                continue
            by_file_decl.setdefault((c_file, decl_name), {})[rev] = translated

        # A file is stable only if EVERY declaration c2rust reported for
        # it (across all seen_revs, unioned — a decl appearing/
        # disappearing between revs, e.g. corpus drift, also counts as
        # "not stable") has the identical translated value in all
        # STABILITY_WINDOW_REVS revisions.
        unstable_files = set()
        all_files = set()
        for (c_file, _decl_name), by_rev in by_file_decl.items():
            all_files.add(c_file)
            if len(by_rev) != STABILITY_WINDOW_REVS or len(set(by_rev.values())) != 1:
                unstable_files.add(c_file)
        return all_files - unstable_files
    finally:
        conn.close()

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


def binary_stale_warning():
    """None if every dispatcher-reachable binary's mtime is newer than
    every tracked source file in C2RUST_FORK (built after the last edit),
    else a ready-to-print warning string. Checks both c2rust and its
    sibling c2rust-transpile (see build_c2rust.py's REQUIRED_BINS) —
    `cargo build --release --bin c2rust` only rebuilds the dispatcher,
    leaving c2rust-transpile (the binary that actually does AST
    export/transpilation) untouched, so checking the dispatcher's mtime
    alone can report "fresh" while the transpile binary is stale.
    git_rev(C2RUST_FORK) records the SOURCE tree's HEAD — with nothing
    checking that the compiled binaries were actually built from that
    commit, an edit-but-forget-to-`cargo build --release` session would
    silently mislabel every row this run writes with a c2rust_rev the
    binaries don't actually reflect (same class of bug as build_db.py's
    dropped-table silent-mismatch: a real state divergence with no signal
    until someone notices results don't match the rev they expected).
    mtime-only, not a content hash — cheap and sufficient: a `cargo build`
    always touches the output binary's mtime, so any genuine rebuild
    clears this regardless of whether the diff was a no-op. `git ls-files`
    scopes the comparison to tracked, compileable inputs only (build
    artifacts under target/, .git/ internals, etc don't count as "source
    changed since the binary")."""
    bin_paths = [Path(C2RUST), Path(C2RUST).parent / "c2rust-transpile"]
    for bin_path in bin_paths:
        if not bin_path.exists():
            return f"c2rust binary not found at {bin_path}"
    bin_mtime = min(bin_path.stat().st_mtime for bin_path in bin_paths)
    try:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=C2RUST_FORK,
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.splitlines()
    except Exception as e:
        return f"could not check {C2RUST_FORK} source freshness: {e}"
    newer = [f for f in tracked
             if (C2RUST_FORK / f).exists()
             and (C2RUST_FORK / f).stat().st_mtime > bin_mtime]
    if newer:
        return (
            f"one or more of {', '.join(str(p) for p in bin_paths)} is "
            f"OLDER than {len(newer)} tracked source file(s) in "
            f"{C2RUST_FORK} (e.g. {newer[0]}) — results below will be "
            f"recorded under c2rust_rev={git_rev(C2RUST_FORK)} but were "
            f"produced by a STALE build. Run `cargo build --release` in "
            f"{C2RUST_FORK} before trusting this baseline."
        )
    return None


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


# Hard per-file wall-clock cap. Real full-corpus measurements put the
# slowest observed file (fs/select.c) at ~75s — 240s leaves ~3x
# headroom for normal variance (contention from other concurrent jobs,
# a slower host) while still bounding a genuinely wedged process to a
# few minutes rather than hanging the whole batch indefinitely.
PER_FILE_TIMEOUT_S = 240

# How often the main loop checks for whole-batch stalls (every worker
# legitimately busy, but nothing completing) — short enough that a
# stall is noticed promptly, long enough not to spin the poll loop.
STALL_CHECK_INTERVAL_S = 15
# How long with zero completions before logging a stall warning. Above
# PER_FILE_TIMEOUT_S so a single slow-but-eventually-timing-out file
# doesn't itself trigger this — it's meant to catch "the whole pool
# looks wedged", not "one file is slow" (which the per-file timeout
# already handles on its own).
STALL_WARN_INTERVAL_S = 90
# Absolute ceiling for a full run, regardless of job count or corpus
# size — a real safety net against a run that would otherwise hang
# indefinitely (e.g. every remaining worker genuinely deadlocked, not
# just slow). 20 minutes is generously above any full-510-file-corpus
# run observed so far (worst case seen: ~6m10s at 8 jobs).
GLOBAL_RUN_TIMEOUT_S = 20 * 60

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
        [C2RUST, "transpile", str(cc_path), "-o", str(work / "output"), "--overwrite-existing",
         *C2RUST_ENABLE_RULE_ARGS],
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
                if time.monotonic() - start > PER_FILE_TIMEOUT_S:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    raise subprocess.TimeoutExpired(proc.args, PER_FILE_TIMEOUT_S)
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
    try:
        log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
    except OSError:
        # work/ can vanish mid-run if something external clears tmp/
        # while a batch is in flight (e.g. a fresh `rm -rf
        # tmp/c2rust-baseline` racing an already-running harness
        # instance) — recreate it rather than letting one file's I/O
        # race take down every other file's already-computed result via
        # an uncaught exception in this worker thread.
        work.mkdir(parents=True, exist_ok=True)
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


# Default batch size for --batch-mode: one c2rust subprocess transpiles
# this many TUs sequentially in-process (see awtoau/c2rust's
# transpile_batch_with_results, added specifically for this harness).
# Chosen as a middle ground — large enough to amortize process-spawn/
# binary-load overhead (the entire point of batching) over many files,
# small enough that one batch's wall-clock stays in the tens-of-seconds
# range so --jobs parallel batches finish around the same time as each
# other (a 510-file corpus in one giant batch would serialize the whole
# run onto whatever --jobs slot picked it up, wasting the other slots).
DEFAULT_BATCH_SIZE = 20

BATCH_TIMEOUT_S = 600  # generous multiple of a single file's ~120s cap
# (see run_one's per-file 120s), since one batch runs up to
# DEFAULT_BATCH_SIZE files sequentially in one process — must be large
# enough that a batch of entirely-slow files doesn't get killed
# mid-batch and lose every remaining file's results, since (unlike
# run_one's per-file timeout) a batch timeout has no partial-result
# recovery: the subprocess is killed before it can print its one JSON
# array to stdout at all.


def make_batches(entries, pch_flags, batch_size=DEFAULT_BATCH_SIZE):
    """Group entries into compile_commands.json-sized chunks for
    --batch-mode: split first by PCH eligibility (a file's own command
    already encodes whether -include-pch is valid for it — see
    command_matches_pch — so mixing eligible/ineligible files in one
    batch would be fine for correctness, but keeping the split makes
    each batch homogeneous and easier to reason about/debug), then
    within each eligibility group, further split so that no single
    batch contains two files with the same basename.

    The basename-uniqueness constraint exists because c2rust's -o <dir>
    batch output layout nests output files under a path derived from
    each C file's path relative to the batch's common ancestor
    directory (see get_output_path/get_module_name in c2rust-transpile's
    lib.rs) — replicating that exact derivation in Python (component-wise
    non-alphanumeric-to-underscore mapping plus a ~50-entry Rust
    reserved-keyword table) to locate each file's own .rs output would be
    a second, drift-prone copy of logic that already lives in the Rust
    source. Guaranteeing basename-uniqueness per batch sidesteps that
    entirely: matching each batch's emitted .rs files back to their
    source .c file by basename (with '-' replaced by '_', the one
    filename-level transform get_output_path applies) is then
    unambiguous without reimplementing the directory-nesting logic at
    all. Real kernel basename collisions exist (e.g. cpu.c appears 4
    times, cacheinfo.c twice, in this corpus) — this only forces those
    particular duplicates apart into different batches, at most a few
    extra small batches out of the whole run.
    """
    eligible, ineligible = [], []
    for e in entries:
        is_eligible = pch_flags is not None and command_matches_pch(e["command"], pch_flags)
        (eligible if is_eligible else ineligible).append(e)

    def chunk_by_unique_basename(group):
        chunks = []
        current = []
        seen_names = set()
        for e in group:
            name = Path(e["file"]).stem.replace("-", "_")
            if name in seen_names or len(current) >= batch_size:
                if current:
                    chunks.append(current)
                current = []
                seen_names = set()
            current.append(e)
            seen_names.add(name)
        if current:
            chunks.append(current)
        return chunks

    return chunk_by_unique_basename(eligible) + chunk_by_unique_basename(ineligible)


def run_batch(entries, pch_flags=None):
    """Batched sibling of run_one: transpile every entry in `entries` with
    ONE c2rust subprocess (awtoau/c2rust's `--batch-json` CLI mode calling
    `transpile_batch_with_results` in-process, per-file `catch_unwind`
    crash isolation and per-file stderr capture — see that function's doc
    comment in c2rust-transpile/src/lib.rs for how crash/diagnostic
    isolation is preserved without a process boundary per file), instead
    of one subprocess per file. Returns a list of dicts in the exact same
    shape run_one() returns, so callers (main()'s DB-insertion loop) don't
    need to know which path produced a given result.
    """
    if not entries:
        return []

    batch_id = safe_name(str(Path(entries[0]["file"]).relative_to(TREE))) + f"-batch{len(entries)}"
    work = OUT_DIR / batch_id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    cc_entries = []
    used_pch_by_file = {}
    for e in entries:
        command = use_pch_if_eligible(e["command"], pch_flags)
        cc_entries.append(dict(e, command=command))
        used_pch_by_file[e["file"]] = command != e["command"]
    cc_path = work / "compile_commands.json"
    cc_path.write_text(json.dumps(cc_entries, indent=1))

    start = time.monotonic()
    proc = subprocess.Popen(
        [C2RUST, "transpile", str(cc_path), "-o", str(work / "output"),
         "--overwrite-existing", "--batch-json", *C2RUST_ENABLE_RULE_ARGS],
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
                if time.monotonic() - start > BATCH_TIMEOUT_S:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    raise subprocess.TimeoutExpired(proc.args, BATCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        # Unlike run_one's per-file timeout, a batch timeout has no
        # partial JSON to recover (the process is killed before it
        # writes its one stdout JSON array) — every file in this batch
        # is reported as a timeout, same as if each had individually
        # timed out in the per-file path, so downstream outcome counts
        # stay comparable between the two modes.
        (work / "batch_stderr.log").write_text(stderr or "")
        return [
            {"file": str(Path(e["file"]).relative_to(TREE)), "outcome": "timeout"}
            for e in entries
        ]
    duration_s = time.monotonic() - start
    peak_rss = max(peak_rss, _peak_rss_bytes(proc.pid))

    (work / "batch_stdout.json").write_text(stdout or "")
    (work / "batch_stderr.log").write_text(stderr or "")

    if proc.returncode != 0:
        # The whole subprocess died (e.g. SIGSEGV/OOM outside a single
        # file's catch_unwind — a genuine process-level crash rather than
        # a caught-and-reported per-file panic) before it could print its
        # JSON array at all. Every file in this batch shares that fate;
        # distinguish OOM (RLIMIT_AS kill, negative returncode, no JSON)
        # from any other whole-process crash the same way run_one does.
        hit_mem_limit = proc.returncode < 0
        outcome = "oom" if hit_mem_limit else "crash"
        logging.warning(
            "batch %s: whole subprocess exited %d before emitting JSON "
            "(%d files affected) — see %s",
            batch_id, proc.returncode, len(entries), work / "batch_stderr.log",
        )
        return [
            {"file": str(Path(e["file"]).relative_to(TREE)), "outcome": outcome,
             "returncode": proc.returncode}
            for e in entries
        ]

    try:
        file_results = json.loads(stdout)
    except json.JSONDecodeError as exc:
        logging.error("batch %s: could not parse --batch-json stdout: %s", batch_id, exc)
        return [
            {"file": str(Path(e["file"]).relative_to(TREE)), "outcome": "no_output"}
            for e in entries
        ]

    # Match each batch-relative emitted .rs file back to its source .c
    # file by basename (dashes underscored, same transform
    # get_output_path applies to the filename component) — see
    # make_batches' doc comment for why basename matching is safe here
    # (batches are constructed to have no basename collisions) instead of
    # replicating c2rust's full output-path derivation.
    rs_by_stem = {}
    output_dir = work / "output"
    if output_dir.exists():
        for rs_path in output_dir.rglob("*.rs"):
            rs_by_stem.setdefault(rs_path.stem, []).append(rs_path)

    # duration_s is per-batch wall-clock, not per-file — apportion evenly
    # so the DB's existing duration_s column (used for percentile/slowest
    # reporting) stays meaningful under batch mode too, even though it's
    # now an average rather than a true per-file measurement. The
    # structured phase_timings.total_s from --batch-json is the accurate
    # per-file number and is what a real per-file timing comparison
    # should use (see run_batch's caller / the wall-clock A/B in this
    # script's --measure-timing path); this apportioned value only backs
    # the same slowest-TU-style diagnostics run_one's duration_s backs.
    per_file_duration = duration_s / len(entries) if entries else 0.0

    results = []
    by_file = {str(Path(e["file"]).relative_to(TREE)): e for e in entries}
    seen_files = set()
    for fr in file_results:
        # fr["file"] is the exact `file` field from the compile_commands
        # entry we submitted (see FileTranspileResult::file's doc
        # comment) — an absolute path under TREE, matching entries' own
        # "file" values exactly, so this round-trips without needing any
        # path normalization.
        rel = str(Path(fr["file"]).relative_to(TREE))
        seen_files.add(rel)
        entry = by_file[rel]

        captured_stderr = fr.get("captured_stderr", "")
        panic_message = fr.get("panic_message")
        crashed = panic_message is not None
        missing_nodes = captured_stderr.count("Missing top-level node")
        missing_children = captured_stderr.count("Missing child")
        label_addr = captured_stderr.count("Cannot translate GNU address of label")
        warnings = captured_stderr.count("warning:")

        # Match this C file's basename (dashes underscored, matching the
        # one filename-level transform get_output_path applies — see
        # make_batches' doc comment) to the .rs files a batch emitted.
        # No per-file "oom" outcome here: RLIMIT_AS applies to the whole
        # batch subprocess, not a per-file sub-limit, so a single file
        # blowing the batch's memory budget kills the entire batch
        # (handled by the proc.returncode != 0 branch above, well before
        # this per-file loop runs).
        stem = Path(rel).stem.replace("-", "_")
        rs_files = rs_by_stem.get(stem, [])

        if crashed:
            outcome = "crash"
        elif not rs_files:
            outcome = "no_output"
        elif missing_nodes or missing_children:
            outcome = "dropped_decls"
        else:
            outcome = "clean"

        signatures = [
            {"kind": k, "source_file": sf, "source_line": sl, "detail": d}
            for k, sf, sl, d in extract_signatures(captured_stderr)
        ]

        c_funcs = c_top_level_functions(TREE / entry["file"])
        rs_funcs = set()
        for rf in rs_files:
            rs_funcs |= rs_translated_functions(rf)
        decl_outcomes = [
            {"name": fn, "translated": fn in rs_funcs} for fn in sorted(c_funcs)
        ]

        pt = fr.get("phase_timings") or {}
        results.append({
            "file": rel,
            "outcome": outcome,
            "returncode": 0 if fr.get("ok") or not crashed else 1,
            "warnings": warnings,
            "missing_top_level_nodes": missing_nodes,
            "missing_children": missing_children,
            "label_address_exprs": label_addr,
            "rs_files_emitted": len(rs_files),
            "peak_rss_bytes": peak_rss,  # batch-level, not per-file — see per_file_duration note
            "duration_s": per_file_duration,
            "signatures": signatures,
            "decl_outcomes": decl_outcomes,
            "used_pch": used_pch_by_file[entry["file"]],
            "ast_export_s": pt.get("ast_export_s"),
            "translate_s": pt.get("translate_s"),
            "total_s": pt.get("total_s"),
        })

    # A file submitted in this batch that --batch-json didn't report at
    # all would be a real bug in the batched path (every entry in
    # compile_commands.json should produce exactly one FileTranspileResult
    # — see transpile_batch_with_results' loop) rather than something to
    # silently paper over; surface it as a distinct outcome instead of
    # dropping the file from results entirely, which would silently
    # shrink the corpus a batch-mode run reports against.
    missing = set(by_file) - seen_files
    for rel in sorted(missing):
        logging.error("batch %s: %s missing from --batch-json output", batch_id, rel)
        results.append({"file": rel, "outcome": "no_output"})

    return results


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
    ap.add_argument(
        "--batch-mode", action="store_true",
        help="Use awtoau/c2rust's --batch-json entry point (transpile_batch_"
             "with_results): group TUs into "
             "DEFAULT_BATCH_SIZE-sized compile_commands.json batches "
             "(split by PCH eligibility, then by basename-uniqueness — see "
             "make_batches) and spawn one c2rust subprocess PER BATCH "
             "instead of one per file, eliminating per-file process-spawn/"
             "binary-load overhead (fork+exec, dynamic linker, LLVM/Clang "
             "static init). Crash isolation is preserved inside the Rust "
             "side via catch_unwind, not via a process boundary — see "
             "transpile_batch_with_results' doc comment. Same DB schema, "
             "same outcome classification as the default per-file path; "
             "this flag only changes how many subprocesses get spawned. "
             "Default is OFF (the original one-process-per-file path) so "
             "the two modes can be A/B compared on demand.",
    )
    ap.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"TUs per c2rust subprocess in --batch-mode (default {DEFAULT_BATCH_SIZE}).",
    )
    ap.add_argument(
        "--include-slow", action="store_true",
        help="Include SLOW_FILES_EXCLUDED (skipped by default — see "
             "awtoau/c2rust#4, ~50-75s each with no clean-pass outcome "
             "anyway) in this run.",
    )
    ap.add_argument(
        "--include-stable", action="store_true",
        help=f"Include files whose per-declaration translated status has "
             f"been unchanged across the last {STABILITY_WINDOW_REVS} "
             f"distinct c2rust revisions (skipped by default in a routine "
             f"run). ALWAYS PASS THIS when verifying an idiom-rule change "
             f"(WARN_ON/fls-family/swap-mem-swap/etc.) — see stable_files()'s "
             f"KNOWN LIMITATION docstring (awtoau/c2rust#5): an idiom rule "
             f"changes what gets emitted inside an already-succeeding "
             f"declaration without flipping its translated status, so the "
             f"default routine run cannot catch an idiom-rule regression. "
             f"Safe to omit only for reliability work (crash/dropped_decls "
             f"fixes), which by definition flips translated for the files "
             f"it actually changes.",
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

    if not args.include_slow:
        kept = []
        skipped = []
        for e in entries:
            rel = str(Path(e["file"]).relative_to(TREE))
            (skipped if rel in SLOW_FILES_EXCLUDED else kept).append(e)
        entries = kept
        if skipped:
            logging.info(
                "skipping %d/%d corpus files in SLOW_FILES_EXCLUDED "
                "(awtoau/c2rust#4, pass --include-slow to run them): %s",
                len(skipped), len(skipped) + len(entries),
                ", ".join(sorted(str(Path(e["file"]).relative_to(TREE)) for e in skipped)),
            )

    if not args.include_stable:
        stable = stable_files()
        if stable:
            kept = []
            skipped = []
            for e in entries:
                rel = str(Path(e["file"]).relative_to(TREE))
                (skipped if rel in stable else kept).append(e)
            entries = kept
            if skipped:
                logging.info(
                    "skipping %d/%d corpus files: per-decl translated status "
                    "unchanged across the last %d c2rust revisions (pass "
                    "--include-stable for a full sweep — required when "
                    "verifying an idiom-rule change, see stable_files()'s "
                    "docstring)",
                    len(skipped), len(skipped) + len(entries), STABILITY_WINDOW_REVS,
                )

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
    batches = make_batches(entries, pch_flags, args.batch_size) if args.batch_mode else None
    logging.info(
        "running c2rust transpile over %d .c TUs (%d parallel jobs, mode=%s%s)",
        len(entries), args.jobs,
        "batch" if args.batch_mode else "per-file",
        f", {len(batches)} batches of up to {args.batch_size}" if batches is not None else "",
    )

    import sqlite3
    from datetime import datetime, timezone

    if not DB.exists():
        logging.error("no %s — run scripts/build_db.py first", DB)
        return 1
    stale_warning = binary_stale_warning()
    if stale_warning:
        # Loud, not just logged — same principle as build_db.py's
        # dropped-table warning: a state mismatch here silently mislabels
        # every row this run writes until someone happens to notice the
        # results don't match the c2rust_rev they expected.
        print(f"WARNING: {stale_warning}")
        logging.warning(stale_warning)
    conn = sqlite3.connect(DB)
    ensure_schema(conn)
    run_at = datetime.now(timezone.utc).isoformat()
    rev = git_rev(C2RUST_FORK)
    crev = corpus_rev()

    # Batch mode submits one future per BATCH (each internally covering
    # several files via one c2rust subprocess); per-file mode submits one
    # future per FILE, same as before this flag existed. Either way each
    # future resolves to a list of run_one()-shaped dicts — a completed
    # per-file future is just a single-element list — so the results-
    # draining/DB-write loop below doesn't need to know which mode
    # produced what it's writing.
    results = []
    total_units = len(batches) if batches is not None else len(entries)
    unit_start = {}  # future -> time.monotonic() it was submitted, for the stall watchdog below
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        if batches is not None:
            futures = {pool.submit(run_batch, batch, pch_flags): batch for batch in batches}
        else:
            futures = {pool.submit(run_one, entry, pch_flags): [entry] for entry in entries}
        now = time.monotonic()
        for fut in futures:
            unit_start[fut] = now
        pending = set(futures)
        done = 0
        run_start = time.monotonic()
        last_progress = run_start
        while pending:
            # Poll with a short timeout instead of a single blocking
            # as_completed(futures) call, so a whole-batch stall (every
            # worker still legitimately busy, nothing has finished in a
            # while) is visible as a log line instead of silent — the
            # only prior signal for "is this run still making progress"
            # was a human noticing the periodic done-count log stopped
            # advancing.
            newly_done, pending = wait(pending, timeout=STALL_CHECK_INTERVAL_S,
                                        return_when=FIRST_COMPLETED)
            if newly_done:
                last_progress = time.monotonic()
            for fut in newly_done:
                unit = futures[fut]
                try:
                    unit_results = fut.result() if batches is not None else [fut.result()]
                except subprocess.TimeoutExpired:
                    unit_results = [{"file": e["file"], "outcome": "timeout"} for e in unit]
                except Exception:
                    # A worker-thread exception (e.g. an I/O race the
                    # per-file path itself didn't already recover from)
                    # must not propagate out of this loop and abort
                    # every other in-flight unit's already-computed
                    # result — record it as a distinct outcome and keep
                    # draining the rest of the pool, matching the
                    # recovery this project already added inside
                    # run_one() for the specific ENOENT race that
                    # prompted this — this is the same principle
                    # applied at the loop level, for any other
                    # unexpected exception shape.
                    logging.exception("unit for %s raised an unexpected exception", unit)
                    unit_results = [{"file": e["file"], "outcome": "error"} for e in unit]
                results.extend(unit_results)
                done += 1
                if done % max(1, total_units // 20 or 1) == 0 or done == total_units:
                    logging.info("%d/%d %s done", done, total_units,
                                 "batches" if batches is not None else "files")

            stalled_for = time.monotonic() - last_progress
            if pending and stalled_for > STALL_WARN_INTERVAL_S:
                in_flight = sorted(
                    (time.monotonic() - unit_start[f], futures[f][0]["file"]) for f in pending
                )
                logging.warning(
                    "no progress for %.0fs — %d unit(s) still running: %s",
                    stalled_for, len(pending),
                    ", ".join(f"{name} ({elapsed:.0f}s)" for elapsed, name in in_flight[-10:]),
                )
                last_progress = time.monotonic()  # only warn once per interval, not every poll

            if time.monotonic() - run_start > GLOBAL_RUN_TIMEOUT_S:
                logging.error(
                    "global run timeout (%.0fs) exceeded with %d/%d %s still pending — "
                    "killing remaining work and reporting what finished",
                    GLOBAL_RUN_TIMEOUT_S, len(pending), total_units,
                    "batches" if batches is not None else "files",
                )
                for fut in pending:
                    fut.cancel()
                    for e in futures[fut]:
                        results.append({"file": e["file"], "outcome": "global_timeout"})
                pending = set()
                break

        rows_written = 0
        for r in results:
            # sqlite3 connections aren't thread-safe to share across
            # threads by default; all writes happen here, in the main
            # thread, after every future has resolved (batch mode can't
            # write incrementally as futures complete the way per-file
            # mode's original loop did, since one future now yields many
            # rows) — worker threads only run run_one()/run_batch(),
            # never touch conn.
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

            rows_written += 1
            if rows_written % 10 == 0 or rows_written == len(results):
                conn.commit()
                logging.info("%d/%d rows written", rows_written, len(results))

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

    try:
        patterns = conn.execute(
            "SELECT kind, detail, tus_affected FROM c2rust_failure_patterns LIMIT 15"
        ).fetchall()
    except sqlite3.OperationalError as e:
        logging.warning("could not query c2rust_failure_patterns (%s) — "
                         "run scripts/build_db.py to apply rulesdb/schema.sql", e)
        patterns = None
    if patterns is not None:
        logging.info("top failure patterns (fix-priority order):")
        for kind, detail, count in patterns:
            logging.info("  [%3d TUs] %-22s %s", count, kind, detail[:100])

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
