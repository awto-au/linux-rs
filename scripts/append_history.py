#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Append one row to docs/HISTORY.md's milestone table by script, instead
of hand-editing the file. The table lived inline in README.md until
2026-07-18, growing past what a README should carry; moving it to its
own file only helps if new entries keep landing by a single, consistent
path rather than ad-hoc edits, so this is that path.

Appends at the end of the file, matching the existing chronological
(oldest-first) ordering of every row already in HISTORY.md.

Usage: append_history.py DATE MILESTONE_TEXT
Example: append_history.py 2026-07-19 "Landed TU 33: lib/foo.c"
Output: docs/HISTORY.md (one new row appended)
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HISTORY = REPO / "docs" / "HISTORY.md"


def append_row(date: str, milestone: str) -> None:
    text = HISTORY.read_text()
    row = f"| {date} | {milestone} |\n"
    if not text.endswith("\n"):
        text += "\n"
    HISTORY.write_text(text + row)


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} DATE MILESTONE_TEXT", file=sys.stderr)
        return 1
    date, milestone = sys.argv[1], sys.argv[2]
    if "|" in milestone:
        print("milestone text must not contain '|' (breaks the markdown table)",
              file=sys.stderr)
        return 1
    append_row(date, milestone)
    print(f"appended to {HISTORY.relative_to(REPO)}: {date} | {milestone}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
