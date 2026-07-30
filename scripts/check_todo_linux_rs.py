#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Enforce TODO_LINUX_RS tracking for unresolved translation notes.

Policy (initial rollout): any translated Rust file comment that says
"not yet translated" must have a nearby TODO_LINUX_RS marker so the
follow-up work is mechanically trackable.

Usage:
  check_todo_linux_rs.py

Log:
  tmp/check_todo_linux_rs.log
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
LOG = REPO / "tmp" / "check_todo_linux_rs.log"

NEEDLE = re.compile(r"not yet translated", re.IGNORECASE)
MARKER = "TODO_LINUX_RS"
WINDOW = 3


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


def main() -> int:
    setup_logging()
    log = logging.getLogger(__name__)

    failures: list[tuple[Path, int, str]] = []
    checked = 0

    for path in sorted(TREE.rglob("*_rs.rs")):
        text = path.read_text(errors="replace")
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            if not NEEDLE.search(line):
                continue
            checked += 1
            if not has_marker(lines, idx):
                failures.append((path, idx + 1, line.strip()))

    if failures:
        log.error("TODO_LINUX_RS check failed: %d unresolved note(s) missing marker", len(failures))
        for path, line_no, line in failures:
            rel = path.relative_to(REPO)
            log.error("%s:%d: %s", rel, line_no, line)
        print("TODO_LINUX_RS FAIL")
        return 1

    log.info("TODO_LINUX_RS PASS: %d unresolved note(s) tracked", checked)
    print(f"TODO_LINUX_RS PASS ({checked} tracked notes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
