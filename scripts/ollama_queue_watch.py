#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Find open awtoau/c2rust issues that are real, fixable bugs (not P4
investigations/known-limitations) and not yet dispatched to an
Ollama-gated fix agent — the "what should Ollama work on next" query for
this project's standing policy (see docs/streams.md's Standing Orders
#1: never let Ollama sit idle when the c2rust-breadth queue has a real
candidate).

This script does NOT launch agents itself (this repo's Ollama-gated fix
pipeline runs as a Claude Code subagent dispatch, not a standalone CLI
invocation — see docs/streams.md stream 1's gate description) — it is
the discovery half: "which issue number(s) are ready right now." A human
or an orchestrating agent reads this script's output and does the actual
dispatch.

Candidate filter:
  - state=open on awtoau/c2rust (real, current — crawl first if stale)
  - NOT labeled P4 (this repo's established convention: P4 issues on
    this tracker are investigations/known-limitations with no fix to
    draft, confirmed by hand for #2-#5 — see docs/streams.md stream 1)
  - not already claimed (no open PR on awtoau/c2rust referencing the
    issue number in its title/body — best-effort text match, not a
    guarantee, since GitHub's own issue<->PR linking isn't queried here)

Usage: ollama_queue_watch.py
Inputs: awtoau/c2rust real issue state via `gh issue list`/`gh pr list`
Output: candidate issue numbers + titles to stdout, one per line
Log: tmp/ollama_queue_watch.log
"""
import json
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "ollama_queue_watch.log"


def gh_json(args: list[str]):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def main() -> int:
    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.FileHandler(LOG, mode="a"), logging.StreamHandler(sys.stdout)],
    )

    issues = gh_json(["issue", "list", "-R", "awtoau/c2rust", "--state", "open",
                       "--json", "number,title,labels"])
    open_prs = gh_json(["pr", "list", "-R", "awtoau/c2rust", "--state", "open",
                        "--json", "number,title,body"])

    claimed_numbers = set()
    for pr in open_prs:
        text = f"{pr.get('title', '')} {pr.get('body', '')}"
        for tok in text.replace("#", " #").split():
            if tok.startswith("#") and tok[1:].isdigit():
                claimed_numbers.add(int(tok[1:]))

    candidates = []
    for issue in issues:
        labels = {l["name"] for l in issue["labels"]}
        if "P4" in labels:
            continue  # investigation/known-limitation, no fix to draft
        if issue["number"] in claimed_numbers:
            continue  # already has an open PR referencing it
        candidates.append(issue)

    logging.info("awtoau/c2rust: %d open issues, %d P4 (skipped), %d already "
                 "claimed by an open PR, %d real candidate(s)",
                 len(issues),
                 sum(1 for i in issues if "P4" in {l["name"] for l in i["labels"]}),
                 len(claimed_numbers), len(candidates))

    if not candidates:
        logging.info("no ready candidates — Ollama-breadth queue is empty right now")
        return 0

    for c in candidates:
        labels = ",".join(l["name"] for l in c["labels"]) or "unlabeled"
        logging.info("CANDIDATE #%d [%s] %s", c["number"], labels, c["title"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
