#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Shared path-normalization helper: import_cscope.py and import_sparse.py
each independently hand-rolled the identical function under the same name
(found 2026-07-16, via a skip_atoi spot-check that turned up half the rows
with a stray 'linux/' prefix and half without, for the same file) — one
real bug, two copies to keep in sync. Consolidated 2026-07-31 as part of
the code-review action plan's duplicated-helper-logic cleanup.

Usage: from pathutil import normalize_path
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def normalize_path(p):
    """Match functions.jsonl's convention (relative to linux/, e.g.
    'arch/x86/boot/printf.c') so cscope/sparse rows join cleanly against
    census rows — cscope's and sparse's own paths are absolute
    (REPO/linux/...); a naive REPO-prefix strip alone leaves a stray
    'linux/' prefix that silently breaks every join against
    `functions`/`translated_tus`."""
    p = p.replace(str(REPO) + "/", "")
    if p.startswith("linux/"):
        p = p[len("linux/"):]
    return p
