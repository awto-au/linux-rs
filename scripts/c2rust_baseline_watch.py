#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run a fresh c2rust baseline whenever awtoau/c2rust's real HEAD has
moved since the last baselined revision — event-driven, not clock-driven.

Why this exists: a full baseline is cheap (documented worst case ~6min
at 8 jobs, see run_c2rust_baseline.py) but was only ever run manually,
so it went stale relative to real fixes landing on the fork (e.g. the
last baseline before 2026-07-18 predated issues #9-#12's fixes by
hours). Polling this script cheaply (it's a `git rev-parse` + one SQLite
query when nothing's changed) from a real cron/systemd timer keeps the
baseline honestly current without wasting a ~6min run when nothing on
the fork has actually changed.

This script does NOT schedule itself — wire it into cron/systemd
yourself (see the module docstring's crontab line below). It is
deliberately a single idempotent check-and-maybe-run, safe to invoke as
often as you like.

Usage: c2rust_baseline_watch.py [--force]
  --force    run a fresh baseline even if HEAD hasn't moved (useful for
             a first manual run, or after changing the corpus itself)

Suggested crontab entry (every 15 min; cheap no-op when nothing changed):
  */15 * * * * cd /mnt/2tb/git/linux-rs && /usr/bin/python3 scripts/c2rust_baseline_watch.py >> tmp/c2rust_baseline_watch.log 2>&1

Inputs: /mnt/2tb/git/github.com/awtoau/c2rust (real fork checkout),
        rulesdb/patterns.db's c2rust_attempts table (last-baselined rev)
Output: rulesdb/patterns.db (new c2rust_attempts rows, via dev.py
        c2rust-baseline), this script's own stdout/stderr
Log: tmp/c2rust_baseline_watch.log (append, via the crontab redirect
     above — this script does not manage its own log file, unlike most
     scripts/*.py, since it's meant to be invoked by cron which already
     redirects stdout/stderr)
"""
import argparse
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "rulesdb" / "patterns.db"
C2RUST_SRC = Path("/mnt/2tb/git/github.com/awtoau/c2rust")


def current_c2rust_rev() -> str:
    out = subprocess.run(["git", "rev-parse", "--short=9", "HEAD"],
                         cwd=C2RUST_SRC, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def last_baselined_rev() -> str | None:
    if not DB.exists():
        return None
    conn = sqlite3.connect(str(DB))
    row = conn.execute(
        "SELECT c2rust_rev FROM c2rust_attempts ORDER BY run_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0] if row else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                     help="run even if HEAD hasn't moved since the last baseline")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not C2RUST_SRC.exists():
        logging.error("no c2rust checkout at %s", C2RUST_SRC)
        return 1

    current = current_c2rust_rev()
    last = last_baselined_rev()
    logging.info("awtoau/c2rust HEAD: %s (last baselined: %s)", current, last or "never")

    if current == last and not args.force:
        logging.info("no change since last baseline — nothing to do")
        return 0

    logging.info("HEAD moved (or --force) — running a fresh baseline")
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "dev.py"), "c2rust-baseline"],
                       cwd=REPO)
    if r.returncode != 0:
        logging.error("baseline run failed (rc=%d) — see tmp/c2rust-baseline.log", r.returncode)
        return r.returncode

    new_rev = current_c2rust_rev()
    logging.info("baseline complete at rev %s", new_rev)
    return 0


if __name__ == "__main__":
    sys.exit(main())
