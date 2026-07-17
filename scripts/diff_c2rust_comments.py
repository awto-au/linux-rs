#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Diff c2rust-emitted comment placement between two snapshots of
tmp/c2rust-baseline/*/output/src/*.rs — the safety net for the
locate_comments() shared-visited-set fix (awtoau/c2rust#4): that fix
touches comment attachment on EVERY file, not just the 10 slow ones, so
outcome-count parity (the usual c2rust-regress gate) can't catch a
silent reordering/drop of comments, since comments have no effect on
whether a declaration translates. This does a line-content diff instead.

Each comment is paired with the nearest non-comment line at or after it
(a proxy for "which declaration this comment is attached to") so a
diff reports "comment near X moved/changed" rather than raw line-number
noise from unrelated formatting drift.

Usage:
  diff_c2rust_comments.py snapshot <name>
      copies tmp/c2rust-baseline/*/output/src/*.rs comment positions
      into tmp/comment-snapshots/<name>.json
  diff_c2rust_comments.py compare <before> <after>
      reports files where comment-to-anchor pairing differs
Output: tmp/comment-snapshots/<name>.json, tmp/comment-diff-report.md
Log: tmp/diff_c2rust_comments.log
"""
import argparse
import json
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "tmp" / "c2rust-baseline"
SNAP_DIR = REPO / "tmp" / "comment-snapshots"
LOG = REPO / "tmp" / "diff_c2rust_comments.log"
REPORT = REPO / "tmp" / "comment-diff-report.md"


def extract_comment_anchors(rs_path):
    """Return [(comment_text, next_non_comment_line_text), ...] for one
    .rs file — a lightweight proxy for "what declaration is this
    comment attached to" without a real Rust parser (consistent with
    check_c2rust_rule_conformance.py's existing text-scan approach)."""
    lines = rs_path.read_text(errors="replace").splitlines()
    pairs = []
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if stripped.startswith("//") or stripped.startswith("/*"):
            comment_start = i
            comment_lines = []
            while i < n and (lines[i].strip().startswith("//")
                              or lines[i].strip().startswith("/*")
                              or lines[i].strip().startswith("*")):
                comment_lines.append(lines[i].strip())
                i += 1
            anchor = ""
            j = i
            while j < n and not lines[j].strip():
                j += 1
            if j < n:
                anchor = lines[j].strip()[:80]
            pairs.append((comment_start, " ".join(comment_lines)[:200], anchor))
        else:
            i += 1
    return pairs


def cmd_snapshot(name):
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    rs_files = sorted(BASELINE.glob("*/output/src/*.rs"))
    for rs in rs_files:
        c_file_dir = rs.parent.parent.parent.name
        key = c_file_dir
        pairs = extract_comment_anchors(rs)
        if pairs:
            data[key] = pairs
    out = SNAP_DIR / f"{name}.json"
    out.write_text(json.dumps(data, indent=1))
    logging.info("snapshot %s: %d files with comments -> %s", name, len(data), out)
    print(f"SNAPSHOT OK: {len(data)} files with comments -> {out}")


def cmd_compare(before_name, after_name):
    before = json.loads((SNAP_DIR / f"{before_name}.json").read_text())
    after = json.loads((SNAP_DIR / f"{after_name}.json").read_text())

    all_files = sorted(set(before) | set(after))
    changed = []
    only_before = []
    only_after = []
    for f in all_files:
        b = before.get(f)
        a = after.get(f)
        if b is None:
            only_after.append(f)
            continue
        if a is None:
            only_before.append(f)
            continue
        b_pairs = [(text, anchor) for _, text, anchor in b]
        a_pairs = [(text, anchor) for _, text, anchor in a]
        if b_pairs != a_pairs:
            changed.append((f, b_pairs, a_pairs))

    lines = [
        f"# c2rust comment-placement diff: {before_name} vs {after_name}",
        "",
        f"Files with comments in both snapshots: {len(all_files) - len(only_before) - len(only_after)}",
        f"Files with comments only in {before_name} (lost from corpus): {len(only_before)}",
        f"Files with comments only in {after_name} (new to corpus): {len(only_after)}",
        f"Files with DIFFERING comment placement: {len(changed)}",
        "",
    ]
    if changed:
        lines.append("## Differing files")
        for f, b_pairs, a_pairs in changed:
            lines.append(f"\n### {f}")
            lines.append(f"before ({len(b_pairs)} comments): {b_pairs[:5]}")
            lines.append(f"after  ({len(a_pairs)} comments): {a_pairs[:5]}")
    REPORT.write_text("\n".join(lines))
    logging.info("compare done: %d differing files -> %s", len(changed), REPORT)
    print(f"COMPARE OK: {len(changed)} files with differing comment placement -> {REPORT}")
    if changed:
        print("DIFFERS — review", REPORT, "before trusting this change")
        return 1
    print("IDENTICAL comment placement across both snapshots")
    return 0


def main():
    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[logging.FileHandler(LOG, mode="a")])
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("snapshot")
    sp.add_argument("name")
    cp = sub.add_parser("compare")
    cp.add_argument("before")
    cp.add_argument("after")
    args = ap.parse_args()

    if args.cmd == "snapshot":
        cmd_snapshot(args.name)
        return 0
    elif args.cmd == "compare":
        return cmd_compare(args.before, args.after)


if __name__ == "__main__":
    sys.exit(main())
