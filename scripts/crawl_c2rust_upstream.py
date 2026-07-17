#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Crawl immunant/c2rust's forks + issues + PRs (open and closed) via
`gh api`, populate patterns.db's c2rust_forks/c2rust_issues tables, so
"has someone already fixed this" is one query instead of a manual
GitHub search every time. 2026-07-17, Dan's request.

Deliberately NOT wired into dev.py db — this is an occasional refresh
(upstream doesn't change every minute), and expensive (hundreds of API
calls). Re-run manually when starting a new fix-triage pass.

Usage: crawl_c2rust_upstream.py [--forks-only|--issues-only] [--limit N]
Log: tmp/crawl_c2rust_upstream.log
"""
import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = REPO / "tmp"
DB = REPO / "rulesdb" / "patterns.db"
LOG = TMP / "crawl_c2rust_upstream.log"
UPSTREAM = "immunant/c2rust"


def gh_api_paginated(path, extra_fields=""):
    """Yield all pages of a gh api call using --paginate."""
    cmd = ["gh", "api", "--paginate", path]
    if extra_fields:
        cmd += extra_fields.split()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    # --paginate concatenates JSON arrays back-to-back; split conservatively.
    text = proc.stdout.strip()
    if not text:
        return
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        obj, end = decoder.raw_decode(text, idx)
        idx = end
        if isinstance(obj, list):
            yield from obj
        else:
            yield obj


def crawl_forks(conn, limit=None):
    logging.info("crawling forks of %s", UPSTREAM)
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for fork in gh_api_paginated(f"repos/{UPSTREAM}/forks?per_page=100&sort=newest"):
        conn.execute(
            "INSERT OR REPLACE INTO c2rust_forks "
            "(id, full_name, html_url, pushed_at, ahead_by, stargazers_count, "
            " default_branch, crawled_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                fork["id"], fork["full_name"], fork["html_url"], fork.get("pushed_at"),
                None,  # ahead_by requires a separate compare call per fork; skip by default
                fork.get("stargazers_count"), fork.get("default_branch"), now,
            ),
        )
        n += 1
        if limit and n >= limit:
            break
    conn.commit()
    logging.info("forks: %d rows", n)
    return n


def crawl_issues(conn, repo, limit=None):
    logging.info("crawling issues+PRs of %s (state=all)", repo)
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for item in gh_api_paginated(f"repos/{repo}/issues?state=all&per_page=100"):
        is_pr = 1 if "pull_request" in item else 0
        merged = None
        if is_pr:
            pr = item.get("pull_request", {})
            merged = 1 if pr.get("merged_at") else (0 if item["state"] == "closed" else None)
        labels = ",".join(l["name"] for l in item.get("labels", []))
        conn.execute(
            "INSERT OR REPLACE INTO c2rust_issues "
            "(id, repo, number, title, state, is_pr, labels, html_url, body, "
            " created_at, updated_at, closed_at, merged, crawled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item["id"], repo, item["number"], item["title"], item["state"], is_pr,
                labels, item["html_url"], item.get("body"), item.get("created_at"),
                item.get("updated_at"), item.get("closed_at"), merged, now,
            ),
        )
        n += 1
        if limit and n >= limit:
            break
    conn.commit()
    logging.info("%s: %d issue/PR rows", repo, n)
    return n


def rebuild_fts(conn):
    # DELETE FROM <fts5 table> has twice hit "database disk image is
    # malformed" transiently on this DB (recoverable — PRAGMA
    # integrity_check passes right after, real data untouched) — drop
    # and recreate instead, which hasn't shown the issue.
    conn.execute("DROP TABLE IF EXISTS c2rust_issues_fts")
    conn.execute(
        "CREATE VIRTUAL TABLE c2rust_issues_fts USING fts5("
        "repo, number UNINDEXED, title, body, content='c2rust_issues', content_rowid='id')"
    )
    conn.execute(
        "INSERT INTO c2rust_issues_fts (rowid, repo, number, title, body) "
        "SELECT id, repo, number, title, body FROM c2rust_issues"
    )
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forks-only", action="store_true")
    ap.add_argument("--issues-only", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not DB.exists():
        logging.error("no %s — run scripts/build_db.py first", DB)
        return 1

    import sqlite3
    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_forks (id INTEGER PRIMARY KEY, "
        "full_name TEXT NOT NULL UNIQUE, html_url TEXT NOT NULL, pushed_at TEXT, "
        "ahead_by INTEGER, stargazers_count INTEGER, default_branch TEXT, crawled_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_issues (id INTEGER PRIMARY KEY, repo TEXT NOT NULL, "
        "number INTEGER NOT NULL, title TEXT NOT NULL, state TEXT NOT NULL, is_pr INTEGER NOT NULL, "
        "labels TEXT, html_url TEXT NOT NULL, body TEXT, created_at TEXT, updated_at TEXT, "
        "closed_at TEXT, merged INTEGER, crawled_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS c2rust_issues_fts USING fts5("
        "repo, number UNINDEXED, title, body, content='c2rust_issues', content_rowid='id')"
    )

    if not args.issues_only:
        crawl_forks(conn, limit=args.limit)
    if not args.forks_only:
        crawl_issues(conn, UPSTREAM, limit=args.limit)
        rebuild_fts(conn)

    n_forks = conn.execute("SELECT COUNT(*) FROM c2rust_forks").fetchone()[0]
    n_issues = conn.execute("SELECT COUNT(*) FROM c2rust_issues WHERE is_pr=0").fetchone()[0]
    n_prs = conn.execute("SELECT COUNT(*) FROM c2rust_issues WHERE is_pr=1").fetchone()[0]
    n_merged = conn.execute("SELECT COUNT(*) FROM c2rust_issues WHERE merged=1").fetchone()[0]
    conn.close()
    logging.info(
        "DONE: %d forks, %d issues, %d PRs (%d merged) in %s",
        n_forks, n_issues, n_prs, n_merged, DB,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
