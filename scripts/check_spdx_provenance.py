#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""SPDX-provenance checking: verify a translated .rs file's
SPDX-License-Identifier exactly matches its C original's.

Why this exists: on 2026-07-18 a licensing audit found two translated
files whose SPDX line silently drifted from the C original during
hand-translation (lib/iomem_copy_rs.rs said GPL-2.0 when
lib/iomem_copy.c says GPL-2.0-only; 8250_helpers_rs.rs said GPL-2.0
when 8250_port.c says GPL-2.0+). Both were caught only by manual
review — nothing in the pipeline checked this.

Also checks a second, related licensing-provenance question (added
2026-07-18, same audit — rulesdb/rules/0001-export-symbol-gpl.toml):
whether an #[export]-tagged Rust fn silently upgrades a plain
(non-GPL) C EXPORT_SYMBOL to EXPORT_SYMBOL_GPL. See
check_export_gpl_upgrades() below.

This is a SHARED check, not special-cased dev.py-only logic: the core
functions (spdx_of, check_pair, check_export_gpl_upgrades) are
imported by BOTH verification cycles that can land a translated file —
  1. dev.py check (this module's own __main__, hand-translation cycle):
     walks every linux-riscv/**/*_rs.rs.
  2. check_c2rust_output_compiles.py (the c2rust-transpiled-output
     cycle): imports check_pair() directly and checks each candidate
     .rs output against its real C source before counting it as a
     compile-clean/landing-worthy file — a c2rust-produced file has
     the same drift risk as a hand-translated one, even though c2rust
     mechanically carries the header through by default (untested
     assumption prior to this wiring, not something to leave
     unchecked just because it seems less likely to drift).
Both cycles report through their own CLI/logging; this module only
owns the comparison logic, not how each cycle discovers its file list
(hand-translation uses a fixed naming convention across a small stable
tree; c2rust's candidate set is a rotating baseline-run output dir) —
trying to unify file *discovery* across both would be more contortion
than the shared logic is worth.

Mapping from .rs to .c original (used by this module's own __main__,
i.e. the hand-translation cycle only — check_c2rust_output_compiles.py
already knows its own .rs<->.c pairing and calls check_pair() directly
with both paths, bypassing this lookup):
  - Default (all but one file): 1:1 same-directory, strip the `_rs`
    suffix — lib/bcd_rs.rs -> lib/bcd.c. This covers every current
    translated file except the one exception below.
  - Exception table (EXCEPTIONS below): files that are a PARTIAL
    translation of a C file with a different name/stem, so the 1:1
    rule doesn't apply. Currently one entry:
    drivers/tty/serial/8250/8250_helpers_rs.rs -> 8250_port.c (only
    serial8250_compute_lcr() is translated, not the whole TU).
    Deliberately a hardcoded table, not a parse of the module doc's
    "Rust translation of `X`" phrasing — that phrasing wraps across
    multiple `//!` lines in roughly a third of current files (e.g.
    argv_split_rs.rs, cmdline_rs.rs, memweight_rs.rs), so a regex
    would be fragile. A one-line table entry is more honest than a
    parser that silently mis-extracts on the next multi-line doc
    comment.

Matching is EXACT string equality on the identifier only — e.g.
GPL-2.0, GPL-2.0-only and GPL-2.0+ are three different real SPDX
identifiers with different legal meaning and are never treated as
equivalent.

Any file whose C original can't be determined (not in the exception
table, and no same-named .c file next to it) is reported WARN, not
silently skipped — it means either a new partial-translation file
needs an exception-table entry, or the naming convention broke.

Usage: check_spdx_provenance.py [--tree linux-riscv]
Output: PASS/FAIL/WARN per file on stdout; exit 1 if any FAIL.
Log: tmp/check_spdx_provenance.log
"""
import argparse
import logging
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / os.environ.get("LINUXRS_TREE", "linux-riscv")
LOG = REPO / "tmp" / "check_spdx_provenance.log"
SPDX_TAG = "SPDX-License-Identifier:"

# rulesdb/rules/0001-export-symbol-gpl.toml: #[export] always emits
# EXPORT_SYMBOL_GPL, even when the C original used plain (non-GPL)
# EXPORT_SYMBOL. Text/regex scanning only (same standard as
# check_c2rust_rule_conformance.py's checkers — "good enough to be
# useful, documented limitations", not a real Rust/C parser).
#
# #[export] immediately precedes the exported fn's signature in every
# observed translated file (confirmed across all 31 current #[export]
# sites, e.g. lib/math/gcd_rs.rs, lib/hexdump_rs.rs) — no blank line or
# other attribute is known to separate them, so this regex requires
# adjacency rather than scanning the whole file for the two independently
# and trying to pair them up.
EXPORT_ATTR_FN_RE = re.compile(
    r'#\[export\]\s*\n\s*pub unsafe extern "C" fn\s+(\w+)',
)

# C EXPORT_SYMBOL* macro variants that are GPL-only by name (never the
# "silently upgraded" case since they're already at or above GPL_GPL).
# Only bare EXPORT_SYMBOL(name) — no _GPL/_NS_GPL/etc suffix — is the
# non-GPL case #[export] silently tightens.
GPL_EXPORT_MACROS = ("EXPORT_SYMBOL_GPL", "EXPORT_SYMBOL_NS_GPL")
C_EXPORT_RE = re.compile(
    r'\b(EXPORT_SYMBOL(?:_GPL|_NS_GPL|_NS|_FOR_MODULES|_FWTBL_LIB)?)\s*\(\s*(\w+)\s*[,)]'
)


def find_exported_fns(rs_text: str) -> list[str]:
    """Every fn name immediately following an #[export] attribute."""
    return EXPORT_ATTR_FN_RE.findall(rs_text)


def find_c_export_macro(c_text: str, symbol: str) -> str | None:
    """Find the EXPORT_SYMBOL* macro variant used for `symbol` in the C
    original, or None if no EXPORT_SYMBOL*(symbol) call is found at all
    (e.g. the symbol isn't actually exported in C, or is exported via a
    macro variant this regex doesn't recognise — reported separately by
    the caller as "not found", not silently treated as GPL)."""
    for m in C_EXPORT_RE.finditer(c_text):
        macro, name = m.group(1), m.group(2)
        if name == symbol:
            return macro
    return None


def check_export_gpl_upgrades(rs_path: Path, c_path: Path) -> list[tuple[str, str]]:
    """Return a list of (symbol, detail) WARN findings: every #[export]-
    tagged fn in rs_path whose C original (c_path) exports the same-named
    symbol via plain, non-GPL EXPORT_SYMBOL (no _GPL suffix). WARN, not
    FAIL — per rule 0001's own note this is currently license-inert
    (CONFIG_MODULES unset), not yet a hard gate. Symbols where the C
    macro can't be found at all are skipped (not flagged as a deviation;
    could mean a naming mismatch this regex doesn't handle, not
    necessarily a real problem — silence here is intentionally
    conservative, see module doc's "good enough to be useful" standard)."""
    rs_text = rs_path.read_text(errors="replace")
    c_text = c_path.read_text(errors="replace")
    findings = []
    for symbol in find_exported_fns(rs_text):
        macro = find_c_export_macro(c_text, symbol)
        if macro is None:
            continue
        if macro not in GPL_EXPORT_MACROS:
            findings.append((
                symbol,
                f"{rs_path.name}: #[export] fn {symbol} always emits "
                f"EXPORT_SYMBOL_GPL, but {c_path.name} exports it via "
                f"plain {macro} (non-GPL) — silent license upgrade "
                f"(rulesdb/rules/0001-export-symbol-gpl.toml)",
            ))
    return findings

# Files that are not a 1:1 <name>_rs.rs <-> <name>.c same-directory
# translation. Path keys are relative to the kernel tree root.
EXCEPTIONS = {
    "drivers/tty/serial/8250/8250_helpers_rs.rs": "drivers/tty/serial/8250/8250_port.c",
    # Tier B (mem_serial_in/mem_serial_out) — same source file as the Tier A
    # helpers above, see docs/8250-tier-b-scoping-2026-07-18.md.
    "drivers/tty/serial/8250/8250_io_rs.rs": "drivers/tty/serial/8250/8250_port.c",
}


def spdx_of(path: Path) -> str | None:
    """First SPDX-License-Identifier value found in the file's opening
    lines (checked near the top, not strictly line 1, since some files
    have a shebang or other preamble before it)."""
    try:
        head = path.read_text(errors="replace").split("\n", 10)
    except OSError:
        return None
    for line in head[:10]:
        if SPDX_TAG in line:
            return line.split(SPDX_TAG, 1)[1].strip()
    return None


def c_original_for(rs_path: Path, tree: Path) -> Path | None:
    rel = str(rs_path.relative_to(tree))
    if rel in EXCEPTIONS:
        return tree / EXCEPTIONS[rel]
    candidate = rs_path.with_name(rs_path.name.removesuffix("_rs.rs") + ".c")
    return candidate if candidate.exists() else None


def check_pair(rs_path: Path, c_path: Path) -> tuple[str, str]:
    """Compare one .rs/.c pair's SPDX identifiers directly — the shared
    primitive both verification cycles call, independent of how each
    cycle found the pair. Returns (status, detail): status is
    "pass"/"fail"/"warn"; detail is a human-readable one-liner for the
    caller's own logging (this function does not log itself, so it
    composes cleanly inside either cycle's own log format)."""
    rs_spdx = spdx_of(rs_path)
    c_spdx = spdx_of(c_path)
    if rs_spdx is None:
        return "warn", f"{rs_path} — no SPDX-License-Identifier found"
    if c_spdx is None:
        return "warn", f"{c_path} — C original has no SPDX-License-Identifier"
    if rs_spdx == c_spdx:
        return "pass", f"{rs_path} == {c_path} ({rs_spdx})"
    return "fail", f"{rs_path}: {rs_spdx!r} != {c_path}: {c_spdx!r}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(TREE))
    args = ap.parse_args()

    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    tree = Path(args.tree)
    rs_files = sorted(tree.glob("**/*_rs.rs"))
    if not rs_files:
        logging.warning("no *_rs.rs files found under %s", tree)

    passes, fails, warns = [], [], []
    export_gpl_warnings = []
    per_file_rows = []  # (rel_rs, rule_0029_verdict, rule_0001_gpl_count) — the
    # actual per-file, per-idiom checklist this script applies; kept
    # separate from the streaming log above because interleaving each
    # file's rule-0029 line with its own rule-0001 warnings (both logged
    # as the loop runs) reads as one long undifferentiated stream, not a
    # scannable "file x idiom" table. This table is the fix.
    for rs in rs_files:
        rel_rs = rs.relative_to(tree)
        c = c_original_for(rs, tree)
        if c is None:
            logging.warning("WARN %s — could not determine C original "
                            "(no same-named .c, no exception-table entry)",
                            rel_rs)
            warns.append(rel_rs)
            per_file_rows.append((rel_rs, "WARN (no C original)", 0))
            continue
        status, detail = check_pair(rs, c)
        if status == "pass":
            logging.info("PASS %s", detail)
            passes.append(rel_rs)
        elif status == "warn":
            logging.warning("WARN %s", detail)
            warns.append(rel_rs)
        else:
            logging.error("FAIL %s", detail)
            fails.append(rel_rs)

        # rule 0001-export-symbol-gpl.toml: EXPORT_SYMBOL -> _GPL silent
        # upgrade check. WARN-only (see check_export_gpl_upgrades' own
        # doc) — never contributes to this script's exit code.
        gpl_count = 0
        for symbol, gpl_detail in check_export_gpl_upgrades(rs, c):
            logging.warning("WARN (export-gpl) %s", gpl_detail)
            export_gpl_warnings.append((rel_rs, symbol))
            gpl_count += 1
        per_file_rows.append((rel_rs, status.upper(), gpl_count))

    logging.info("SPDX provenance: %d pass, %d fail, %d warn (of %d translated files)",
                 len(passes), len(fails), len(warns), len(rs_files))
    logging.info("EXPORT_SYMBOL->_GPL silent upgrades (rule 0001): %d instance(s) "
                 "across %d file(s)",
                 len(export_gpl_warnings), len({f for f, _ in export_gpl_warnings}))

    # The actual per-file idiom checklist: rule 0029 (SPDX provenance) and
    # rule 0001 (EXPORT_SYMBOL->_GPL) applied to every translated file,
    # one row each — not just an aggregate pass/fail count.
    name_w = max((len(str(f)) for f, _, _ in per_file_rows), default=20)
    print(f"\n{'file':<{name_w}}  rule-0029 (SPDX)   rule-0001 (EXPORT_SYMBOL_GPL)")
    print(f"{'-' * name_w}  -----------------  -----------------------------")
    for rel_rs, verdict, gpl_count in per_file_rows:
        gpl_col = f"{gpl_count} deviation(s)" if gpl_count else "clean"
        print(f"{str(rel_rs):<{name_w}}  {verdict:<17}  {gpl_col}")
    print()

    if fails:
        logging.error("SPDX PROVENANCE FAIL: %d mismatch(es)", len(fails))
        return 1
    logging.info("SPDX PROVENANCE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
