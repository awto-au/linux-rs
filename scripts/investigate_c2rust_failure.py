#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Look up everything known about one C file's c2rust transpile failure:
recorded failure signatures (rulesdb/patterns.db), the raw per-TU log
from the last baseline run (tmp/c2rust-baseline/<safe_name>/), and
optionally a fresh isolated re-run with a full Rust backtrace.

Exists because ad hoc `sqlite3 ...` / `cat tmp/c2rust-baseline/.../
transpile.log` one-liners blow past terminal output limits on files
with large logs (e.g. bitmap-str.c's transpile.log is 1811 lines) and
leave nothing on disk for the next lookup — this always writes the
full untruncated content to tmp/ and prints a bounded summary.

Usage: investigate_c2rust_failure.py <c_file> [--rerun] [--full-log]
                                      [--preprocess [GREP_PATTERN]]
  <c_file>     path relative to linux-riscv/, e.g. lib/ctype.c
  --rerun      re-transpile just this file in isolation with
               RUST_BACKTRACE=full, capturing to tmp/investigate/<safe>.log
  --full-log   print the entire cached transpile.log instead of the
               last N lines (still also written to tmp/ either way)
  --preprocess [GREP_PATTERN]
               run the file's REAL compile command (from
               compile_commands.json, verbatim flags) through `clang -E`
               instead of hand-copying the command's ~50 flags per
               investigation (this is what both the #13 and #14 triage
               passes independently hand-rolled from scratch on
               2026-07-18 — this flag replaces that). Full preprocessed
               output always written to tmp/; if GREP_PATTERN is given,
               only matching lines print to stdout (still grep -n style,
               with line numbers, so you can find the real expansion of
               e.g. an EXPORT_SYMBOL/asm-goto macro without eyeballing
               tens of thousands of preprocessed lines).
Output: tmp/investigate/<safe_name>.log (rerun only),
        tmp/investigate/<safe_name>.preprocessed.i (preprocess only)
Log: tmp/investigate_c2rust_failure.log
"""
import argparse
import logging
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
DB = REPO / "rulesdb" / "patterns.db"
BASELINE = REPO / "tmp" / "c2rust-baseline"
OUT_DIR = REPO / "tmp" / "investigate"
LOG = REPO / "tmp" / "investigate_c2rust_failure.log"
C2RUST_BIN = Path(os.environ.get(
    "C2RUST_BIN", "/mnt/2tb/git/github.com/awtoau/c2rust/target/release/c2rust"))

TAIL_LINES = 60


def safe_name(c_file):
    return c_file.replace("/", "_")


def print_and_log(msg):
    print(msg)
    logging.info(msg)


def show_signatures(c_file):
    if not DB.exists():
        print_and_log(f"no patterns.db at {DB}")
        return
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT kind, detail, source_file, source_line, COUNT(*) FROM c2rust_failure_signatures "
        "WHERE c_file = ? GROUP BY kind, detail, source_file, source_line "
        "ORDER BY COUNT(*) DESC", (c_file,),
    ).fetchall()
    attempt = conn.execute(
        "SELECT outcome, returncode, run_at, c2rust_rev, missing_top_level_nodes, "
        "missing_children, label_address_exprs FROM c2rust_attempts "
        "WHERE c_file = ? ORDER BY run_at DESC LIMIT 1", (c_file,),
    ).fetchone()
    conn.close()

    print_and_log(f"=== {c_file}: c2rust_attempts (latest) ===")
    if attempt:
        outcome, rc, run_at, rev, mtln, mc, lae = attempt
        print_and_log(f"outcome={outcome} returncode={rc} run_at={run_at} c2rust_rev={rev}")
        print_and_log(f"missing_top_level_nodes={mtln} missing_children={mc} label_address_exprs={lae}")
    else:
        print_and_log("no c2rust_attempts row for this file")

    total = sum(r[4] for r in rows)
    print_and_log(f"=== {c_file}: c2rust_failure_signatures ({len(rows)} distinct, {total} total rows) ===")
    for kind, detail, src, line, n in rows:
        loc = f" ({src}:{line})" if src else ""
        count = f" x{n}" if n > 1 else ""
        print_and_log(f"  [{kind}] {detail}{loc}{count}")


def show_cached_log(c_file, full):
    d = BASELINE / safe_name(c_file)
    log_path = d / "transpile.log"
    if not log_path.exists():
        print_and_log(f"no cached log at {log_path} — run dev.py c2rust-baseline first")
        return
    text = log_path.read_text(errors="replace")
    n_lines = text.count("\n")
    dest = OUT_DIR / f"{safe_name(c_file)}.cached.log"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    print_and_log(f"=== {c_file}: cached transpile.log ({n_lines} lines, full copy at {dest}) ===")
    if full or n_lines <= TAIL_LINES:
        print_and_log(text)
    else:
        tail = "\n".join(text.splitlines()[-TAIL_LINES:])
        print_and_log(f"(showing last {TAIL_LINES} of {n_lines} lines — see {dest} for the rest)")
        print_and_log(tail)


def preprocess(c_file, grep_pattern):
    """Run the file's REAL compile command through `clang -E` (macro
    expansion only, no compilation) — reads the exact command
    verbatim from the isolated compile_commands.json (same source
    rerun_isolated() uses), so this is never a hand-copied/stale
    approximation of the real kernel build flags."""
    d = BASELINE / safe_name(c_file)
    cc_json = d / "compile_commands.json"
    if not cc_json.exists():
        print_and_log(f"no isolated compile_commands.json at {cc_json} — run dev.py c2rust-baseline first")
        return
    import json
    entries = json.loads(cc_json.read_text())
    if not entries:
        print_and_log(f"empty compile_commands.json at {cc_json}")
        return
    entry = entries[0]
    directory = entry["directory"]
    command = entry.get("command")
    if command is None:
        command = " ".join(entry["arguments"])
    # Strip the trailing "-c -o X.o X.c" compile step and swap in -E —
    # a real macro-expansion-only pass using the exact same flags
    # (-I/-D/-include/etc.) the real kernel build uses for this file.
    parts = command.split()
    try:
        c_idx = parts.index("-c")
        parts = parts[:c_idx]
    except ValueError:
        pass
    parts += ["-E", entry["file"]]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"{safe_name(c_file)}.preprocessed.i"
    print_and_log(f"=== {c_file}: preprocessed (macro-expanded) source ===")
    print_and_log(f"$ cd {directory} && {' '.join(parts)}")
    p = subprocess.run(parts, cwd=directory, capture_output=True, text=True, timeout=120)
    dest.write_text(p.stdout)
    n_lines = p.stdout.count("\n")
    print_and_log(f"wrote {n_lines} lines to {dest} (rc={p.returncode}"
                  f"{', stderr: ' + p.stderr[:500] if p.returncode != 0 else ''})")

    if grep_pattern:
        matches = [f"{i}:{line}" for i, line in enumerate(p.stdout.splitlines(), 1)
                   if re.search(grep_pattern, line)]
        print_and_log(f"--- {len(matches)} line(s) matching {grep_pattern!r} ---")
        for m in matches[:200]:
            print_and_log(m)
        if len(matches) > 200:
            print_and_log(f"... and {len(matches) - 200} more — see {dest} for the full file")


def rerun_isolated(c_file):
    d = BASELINE / safe_name(c_file)
    cc_json = d / "compile_commands.json"
    if not cc_json.exists():
        print_and_log(f"no isolated compile_commands.json at {cc_json} — run dev.py c2rust-baseline first")
        return
    if not C2RUST_BIN.exists():
        print_and_log(f"c2rust binary not found at {C2RUST_BIN} (set C2RUST_BIN to override)")
        return
    out_dir = OUT_DIR / safe_name(c_file) / "rerun_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = OUT_DIR / f"{safe_name(c_file)}.rerun.log"
    env = dict(os.environ, RUST_BACKTRACE="full")
    cmd = [str(C2RUST_BIN), "transpile", str(cc_json), "-o", str(out_dir),
           "--overwrite-existing", "--enable-rule=all"]
    print_and_log(f"=== {c_file}: isolated re-run with RUST_BACKTRACE=full ===")
    print_and_log(" ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=240)
    combined = f"--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}\n--- returncode {p.returncode} ---\n"
    log_path.write_text(combined)
    n_lines = combined.count("\n")
    print_and_log(f"returncode={p.returncode}, full output ({n_lines} lines) at {log_path}")
    tail = "\n".join(combined.splitlines()[-TAIL_LINES:])
    print_and_log(f"(showing last {TAIL_LINES} lines)")
    print_and_log(tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("c_file")
    ap.add_argument("--rerun", action="store_true")
    ap.add_argument("--full-log", action="store_true")
    ap.add_argument("--preprocess", nargs="?", const="", default=None, metavar="GREP_PATTERN")
    args = ap.parse_args()

    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.FileHandler(LOG, mode="a")],
    )
    logging.info("=== investigate_c2rust_failure.py %s ===", args.c_file)

    show_signatures(args.c_file)
    show_cached_log(args.c_file, args.full_log)
    if args.rerun:
        rerun_isolated(args.c_file)
    if args.preprocess is not None:
        preprocess(args.c_file, args.preprocess or None)


if __name__ == "__main__":
    sys.exit(main())
