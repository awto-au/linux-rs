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
  <c_file>     path relative to linux-riscv/, e.g. lib/ctype.c
  --rerun      re-transpile just this file in isolation with
               RUST_BACKTRACE=full, capturing to tmp/investigate/<safe>.log
  --full-log   print the entire cached transpile.log instead of the
               last N lines (still also written to tmp/ either way)
Output: tmp/investigate/<safe_name>.log (rerun only)
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


if __name__ == "__main__":
    sys.exit(main())
