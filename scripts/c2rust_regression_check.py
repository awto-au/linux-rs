#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Diff two c2rust_decl_outcomes snapshots (by c2rust_rev) at
per-declaration granularity, so a change to awtoau/c2rust can be judged
on "did any function that used to translate stop translating" rather
than the much noisier whole-file outcome (a file can flip from "clean"
to "dropped_decls" while every one of its own functions still
translates fine — see lib/bcd.c).

--file-issue posts a summary to awtoau/c2rust's issue tracker instead of
just printing, without blocking anything by itself (the caller decides
whether a regression is fatal).

Usage:
  c2rust_regression_check.py <before-rev> <after-rev> [--file-issue]

Requires two prior `run_c2rust_baseline.py` runs already in patterns.db
(one per rev being compared) — this script only diffs, it doesn't run
c2rust itself.
Log: tmp/c2rust_regression_check.log
"""
import argparse
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = REPO / "tmp"
DB = REPO / "rulesdb" / "patterns.db"
LOG = TMP / "c2rust_regression_check.log"


def decl_set(conn, rev):
    """{(c_file, decl_name): translated} for the LATEST run at this rev."""
    latest_run_at = conn.execute(
        "SELECT MAX(run_at) FROM c2rust_decl_outcomes WHERE c2rust_rev = ?", (rev,)
    ).fetchone()[0]
    if latest_run_at is None:
        return None
    rows = conn.execute(
        "SELECT c_file, decl_name, translated FROM c2rust_decl_outcomes "
        "WHERE c2rust_rev = ? AND run_at = ?", (rev, latest_run_at),
    ).fetchall()
    return {(f, d): bool(t) for f, d, t in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before_rev")
    ap.add_argument("after_rev")
    ap.add_argument("--file-issue", action="store_true",
                     help="post a summary comment to awtoau/c2rust issue #1 if any regression is found")
    args = ap.parse_args()

    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not DB.exists():
        logging.error("no %s", DB)
        return 1

    import sqlite3
    conn = sqlite3.connect(DB)

    before = decl_set(conn, args.before_rev)
    after = decl_set(conn, args.after_rev)
    if before is None:
        logging.error("no c2rust_decl_outcomes rows for rev %s — run+import a baseline at that rev first", args.before_rev)
        return 1
    if after is None:
        logging.error("no c2rust_decl_outcomes rows for rev %s — run+import a baseline at that rev first", args.after_rev)
        return 1

    all_keys = set(before) | set(after)
    regressed, fixed, unchanged_ok, unchanged_bad, new_decl, removed_decl = [], [], 0, 0, [], []

    for key in sorted(all_keys):
        b = before.get(key)
        a = after.get(key)
        if b is None:
            new_decl.append(key)
        elif a is None:
            removed_decl.append(key)
        elif b and not a:
            regressed.append(key)
        elif not b and a:
            fixed.append(key)
        elif b and a:
            unchanged_ok += 1
        else:
            unchanged_bad += 1

    logging.info("=== c2rust decl-level regression check: %s -> %s ===", args.before_rev, args.after_rev)
    logging.info("before: %d decls, after: %d decls", len(before), len(after))
    logging.info("regressed (was translated, now isn't): %d", len(regressed))
    logging.info("fixed (wasn't translated, now is): %d", len(fixed))
    logging.info("unchanged, still translating: %d", unchanged_ok)
    logging.info("unchanged, still not translating: %d", unchanged_bad)
    logging.info("new decl (not in before-rev's corpus): %d", len(new_decl))
    logging.info("removed decl (not in after-rev's corpus): %d", len(removed_decl))

    if regressed:
        logging.warning("REGRESSED DECLS:")
        for f, d in regressed:
            logging.warning("  %s :: %s", f, d)
    if fixed:
        logging.info("FIXED DECLS:")
        for f, d in fixed[:30]:
            logging.info("  %s :: %s", f, d)
        if len(fixed) > 30:
            logging.info("  ... and %d more", len(fixed) - 30)

    verdict = "REGRESSION" if regressed else "OK"
    logging.info("VERDICT: %s", verdict)

    if regressed and args.file_issue:
        body_lines = [
            f"## c2rust decl-level regression: `{args.before_rev}` -> `{args.after_rev}`",
            "",
            f"Automated per-declaration regression check (`scripts/c2rust_regression_check.py`) found "
            f"**{len(regressed)} function(s)** that translated successfully at `{args.before_rev}` "
            f"and no longer do at `{args.after_rev}`.",
            "",
            "This is filed for later analysis, not as a blocking claim — a per-file outcome "
            "(clean/dropped_decls/crash) can be noisy on its own (a pre-existing gap unrelated to "
            "the compared revisions can flip a file's classification without any function actually "
            "regressing). This issue records the concrete before/after decl list so a human can "
            "triage whether it's real.",
            "",
            "### Regressed declarations",
            "```",
        ]
        for f, d in regressed:
            body_lines.append(f"{f} :: {d}")
        body_lines.append("```")
        if fixed:
            body_lines.append("")
            body_lines.append(f"### Fixed declarations ({len(fixed)} total, first 30 shown)")
            body_lines.append("```")
            for f, d in fixed[:30]:
                body_lines.append(f"{f} :: {d}")
            body_lines.append("```")

        body = "\n".join(body_lines)
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as tf:
            tf.write(body)
            body_path = tf.name

        try:
            out = subprocess.run(
                ["gh", "issue", "comment", "1", "-R", "awtoau/c2rust", "--body-file", body_path],
                capture_output=True, text=True, check=True,
            )
            logging.info("posted regression report: %s", out.stdout.strip())
        except subprocess.CalledProcessError as e:
            logging.error("failed to post issue comment: %s", e.stderr)
        finally:
            Path(body_path).unlink(missing_ok=True)

    conn.close()
    return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
