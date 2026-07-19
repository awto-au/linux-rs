#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Mechanical check for rule 0031 (fabricated-register-variable-static).

c2rust fabricates a `#[no_mangle] pub static mut riscv_current_is_tp`
(always null — nothing ever assigns it) standing in for arch/riscv's
register-variable extension binding `current`/`current_stack_pointer` to
a live CPU register. Whether this is dead code or a live null-deref bug
depends on whether get_current() is CALLED anywhere in the TU, not just
whether the static is referenced — grepping the static's own identifier
is not sufficient proof of deadness (see rulesdb/rules/0031, issues
awto-au/linux-rs#30/#31).

Scans every *_rs.rs file in the c2rust baseline corpus (tmp/c2rust-baseline)
and any already-landed/wired TU under linux-riscv/, flags every file where
riscv_current_is_tp is declared AND get_current() has at least one call
site — those need the inline-asm fix (or removal, per rule 0031) before
being trusted as "boots clean".

Usage: check_fabricated_register_statics.py
Outputs:
  tmp/fabricated-register-statics-report.md
Log: tmp/check_fabricated_register_statics.log
"""
import logging
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "tmp" / "c2rust-baseline"
TREE = REPO / "linux-riscv"
WORKTREES = REPO / "linux-riscv-worktrees"
REPORT = REPO / "tmp" / "fabricated-register-statics-report.md"
LOG = REPO / "tmp" / "check_fabricated_register_statics.log"

STATIC_RE = re.compile(r"\briscv_current_is_tp\b")
STACK_STATIC_RE = re.compile(r"\bcurrent_stack_pointer\b")
GET_CURRENT_DEF_RE = re.compile(r"\bfn\s+get_current\s*\(")
# Excludes the definition's own `fn get_current(` via a negative
# lookbehind — without it, the definition line itself miscounts as one
# call, hiding a genuinely-dead (zero real calls) accessor as "live".
GET_CURRENT_CALL_RE = re.compile(r"(?<!fn )\bget_current\s*\(\s*\)")


def strip_comments(text: str) -> str:
    """Drop `//` line comments before scanning — a fixed file's own
    explanatory comment (quoting `riscv_current_is_tp`/`get_current()`
    as prose, documenting what was wrong and how it was fixed) must not
    re-trigger this same check. Doc comments (`///`, `//!`) are also
    `//`-prefixed so this covers those too. Not a full Rust tokenizer —
    doesn't handle `//` inside a string literal, but neither identifier
    this script greps for plausibly appears inside a string literal in
    this corpus."""
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def scan_file(path: Path) -> dict | None:
    try:
        raw_text = path.read_text(errors="replace")
    except OSError:
        return None
    text = strip_comments(raw_text)
    if not STATIC_RE.search(text):
        return None

    def_match = GET_CURRENT_DEF_RE.search(text)
    call_count = len(GET_CURRENT_CALL_RE.findall(text))
    live = def_match is not None and call_count > 0
    stack_referenced = bool(STACK_STATIC_RE.search(text))

    return {
        "path": path,
        "has_get_current_fn": def_match is not None,
        "call_count": call_count,
        "live": live,
        "stack_pointer_referenced": stack_referenced,
    }


def find_targets() -> list[Path]:
    targets = []
    if BASELINE.exists():
        targets.extend(sorted(BASELINE.glob("*/output/src/*.rs")))
    if TREE.exists():
        targets.extend(sorted(TREE.glob("**/*_rs.rs")))
    if WORKTREES.exists():
        # combined-boot screening worktrees (issue #28) — the actual
        # location of every candidate this rule was written to catch,
        # including both files (#30, #31) that motivated writing it.
        targets.extend(sorted(WORKTREES.glob("*/**/*_rs.rs")))
    return targets


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger(__name__)

    targets = find_targets()
    log.info("scanning %d candidate .rs files", len(targets))

    hits = []
    for path in targets:
        result = scan_file(path)
        if result is not None:
            hits.append(result)

    live_hits = [h for h in hits if h["live"]]
    dead_hits = [h for h in hits if not h["live"]]

    lines = [
        "# Fabricated register-variable static scan (rule 0031)",
        "",
        f"Files with `riscv_current_is_tp` present: {len(hits)}",
        f"LIVE (get_current() called — null-deref risk, needs fix): {len(live_hits)}",
        f"Dead (no get_current() call, safe as-is): {len(dead_hits)}",
        "",
    ]
    if live_hits:
        lines.append("## LIVE — needs the rule 0031 fix")
        lines.append("")
        for h in live_hits:
            rel = h["path"].relative_to(REPO) if h["path"].is_relative_to(REPO) else h["path"]
            lines.append(f"- `{rel}` — get_current() called {h['call_count']}x")
        lines.append("")
    if dead_hits:
        lines.append("## Dead — static present, get_current() never called (or never defined)")
        lines.append("")
        for h in dead_hits:
            rel = h["path"].relative_to(REPO) if h["path"].is_relative_to(REPO) else h["path"]
            lines.append(f"- `{rel}`")
        lines.append("")

    REPORT.write_text("\n".join(lines) + "\n")
    log.info("wrote %s", REPORT)

    for h in live_hits:
        log.warning("LIVE null-deref risk: %s (get_current() called %dx)", h["path"], h["call_count"])

    log.info(
        "SCAN OK: %d file(s) with fabricated static, %d live (needs fix), %d dead (safe)",
        len(hits), len(live_hits), len(dead_hits),
    )
    return 1 if live_hits else 0


if __name__ == "__main__":
    sys.exit(main())
