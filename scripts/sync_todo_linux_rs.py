#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Auto-insert TODO_LINUX_RS markers for unresolved translation notes.

Policy (initial rollout): whenever a translated Rust file contains
"not yet translated" and there is no nearby TODO_LINUX_RS marker,
insert one mechanically on the next line.

Usage:
  sync_todo_linux_rs.py

Log:
  tmp/sync_todo_linux_rs.log
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
LOG = REPO / "tmp" / "sync_todo_linux_rs.log"

NEEDLE = re.compile(r"not yet translated", re.IGNORECASE)
MARKER = "TODO_LINUX_RS"
WINDOW = 3
AUTO_TEXT = "TODO_LINUX_RS: auto-tracked unresolved translation dependency; replace with specific owner/scope when touching this code."


def setup_logging() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )


def has_marker(lines: list[str], idx: int) -> bool:
    start = max(0, idx - WINDOW)
    end = min(len(lines), idx + WINDOW + 1)
    for j in range(start, end):
        if MARKER in lines[j]:
            return True
    return False


def comment_prefix(line: str) -> str:
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if stripped.startswith("///"):
        return indent + "/// "
    if stripped.startswith("//!"):
        return indent + "//! "
    if stripped.startswith("//"):
        return indent + "// "
    return indent + "// "


def process_file(path: Path) -> int:
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    inserts: list[tuple[int, str]] = []

    for idx, line in enumerate(lines):
        if not NEEDLE.search(line):
            continue
        if has_marker(lines, idx):
            continue
        prefix = comment_prefix(line)
        inserts.append((idx + 1, prefix + AUTO_TEXT))

    if not inserts:
        return 0

    # Apply from bottom to top so indices stay valid.
    for insert_at, insert_line in reversed(inserts):
        lines.insert(insert_at, insert_line)

    path.write_text("\n".join(lines) + "\n")
    return len(inserts)


def main() -> int:
    setup_logging()
    log = logging.getLogger(__name__)

    touched = 0
    inserted = 0
    for path in sorted(TREE.rglob("*_rs.rs")):
        count = process_file(path)
        if count:
            touched += 1
            inserted += count
            log.info("%s: inserted %d TODO_LINUX_RS marker(s)", path.relative_to(REPO), count)

    log.info("sync complete: %d marker(s) inserted across %d file(s)", inserted, touched)
    print(f"TODO_LINUX_RS SYNC ({inserted} inserted across {touched} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
