#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run a fresh scripts/sweep_c2rust_corpus.py sweep whenever
scripts/apply_c2rust_fix_classes.py or any rulesdb/rules/*.toml file has
changed since the last recorded sweep's fix_classes_rev — event-driven,
not clock-driven, same pattern as c2rust_baseline_watch.py.

Why this exists: a new/changed fix class or rule can only ever unlock
MORE files, never fewer, so re-sweeping after either changes is always
worth it — but re-sweeping on a fixed schedule regardless of whether
anything actually changed just burns ~90s of rustc invocations for
nothing. This script does the cheap check (file mtimes/git-diff against
the recorded fix_classes_rev) and only pays for a real sweep when
something that could plausibly change the outcome actually did.

This script does NOT schedule itself — wire it into cron/systemd
yourself. It is deliberately a single idempotent check-and-maybe-run,
safe to invoke as often as you like.

Usage: sweep_c2rust_corpus_watch.py [--force] [-- SWEEP_ARGS...]
  --force        run a fresh sweep even if nothing relevant changed
  SWEEP_ARGS      forwarded verbatim to sweep_c2rust_corpus.py (e.g.
                  --c2rust-rev 65a4a5222 if the baseline corpus itself
                  is stale relative to current c2rust HEAD)

Suggested crontab entry (every 15 min; cheap no-op when nothing changed):
  */15 * * * * cd /mnt/2tb/git/linux-rs && /usr/bin/python3 scripts/sweep_c2rust_corpus_watch.py -- --c2rust-rev 65a4a5222 >> tmp/logs/sweep_c2rust_corpus_watch.log 2>&1

Inputs: rulesdb/patterns.db's c2rust_sweep_outcomes table (last-swept
        fix_classes_rev), git history of scripts/apply_c2rust_fix_classes.py
        and rulesdb/rules/*.toml
Output: rulesdb/patterns.db (new c2rust_sweep_outcomes rows, via
        sweep_c2rust_corpus.py), tmp/c2rust-sweep-report.md,
        tmp/c2rust-sweep-chart.png (via render_sweep_chart.py)
Log: tmp/logs/sweep_c2rust_corpus_watch.log (append, via the crontab
     redirect above)
"""
import argparse
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "rulesdb" / "patterns.db"
RULES_DIR = REPO / "rulesdb" / "rules"
FIX_CLASSES_SCRIPT = REPO / "scripts" / "apply_c2rust_fix_classes.py"

WATCHED_PATHS = [FIX_CLASSES_SCRIPT, RULES_DIR]


def current_repo_rev() -> str:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                          cwd=REPO, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def last_swept_rev() -> str | None:
    if not DB.exists():
        return None
    conn = sqlite3.connect(str(DB))
    row = conn.execute(
        "SELECT fix_classes_rev FROM c2rust_sweep_outcomes ORDER BY run_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0] if row else None


def watched_paths_changed_since(rev: str) -> bool:
    """True if any commit after `rev` touched a watched path — i.e. the
    fix-class script or any rule file. `git diff --quiet` returns
    non-zero (changed) or zero (no diff); an empty/invalid rev (first
    run ever) always counts as changed."""
    if not rev:
        return True
    try:
        result = subprocess.run(
            ["git", "diff", "--quiet", f"{rev}..HEAD", "--", str(FIX_CLASSES_SCRIPT), str(RULES_DIR)],
            cwd=REPO,
        )
    except subprocess.CalledProcessError:
        return True
    # git diff --quiet: exit 0 = no diff, exit 1 = diff present, other = error (treat as changed to be safe)
    return result.returncode != 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                     help="sweep even if nothing relevant changed")
    ap.add_argument("sweep_args", nargs="*",
                     help="forwarded to sweep_c2rust_corpus.py after --")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    current = current_repo_rev()
    last = last_swept_rev()
    logging.info("linux-rs HEAD: %s (last swept at fix_classes_rev: %s)", current, last or "never")

    changed = watched_paths_changed_since(last) if last else True
    if not changed and not args.force:
        logging.info("no change to apply_c2rust_fix_classes.py or rulesdb/rules/ since last sweep — nothing to do")
        return 0

    logging.info("relevant change detected (or --force) — running a fresh sweep")
    cmd = [sys.executable, str(REPO / "scripts" / "sweep_c2rust_corpus.py")] + args.sweep_args
    r = subprocess.run(cmd, cwd=REPO)
    if r.returncode != 0:
        logging.error("sweep failed (rc=%d) — see tmp/logs/sweep_c2rust_corpus.log", r.returncode)
        return r.returncode

    chart = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "render_sweep_chart.py")], cwd=REPO
    )
    if chart.returncode != 0:
        logging.warning("render_sweep_chart.py failed (rc=%d) — sweep data still landed", chart.returncode)

    logging.info("sweep + chart complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
