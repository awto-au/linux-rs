#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Mechanically apply the recurring, well-verified c2rust output fix
classes to one or more freshly-copied `*_rs.rs` files.

These 4 classes have been applied by hand, identically, across every
round of the #28 combined-boot screening effort (rounds 1-3) with zero
false positives once each pattern was nailed down — they are safe to
automate:

  1. Strip an unused `#![feature(label_break_value)]` line (only if
     genuinely unused — grep-verified, not a blind strip).
  2. `use ::macros::export;` -> deleted, `#[export]` -> `#[no_mangle]`
     (c2rust's raw-transpile export path is incompatible with this
     kernel's bindgen-integrated `#[export]`; see
     docs/combined-boot-attempt-2026-07-18.md's "new gap class" #1).
  3. RISC-V inline-asm bracket-addressing (`[{N}]`/`[{N:}]` ->
     `0({N})`/`0({N:})` -- issue awto-au/linux-rs#29).
  4. `kernel::warn_on!(cond) != 0` / `== 0` -> `kernel::warn_on!(cond)`
     (the macro already returns bool; both comparisons are equally an
     E0308 type error, c2rust assumes C's int-returning WARN_ON() either
     way -- confirmed `bool == 0` fails identically to `bool != 0` via a
     direct rustc probe).
  5. Fabricated register-pseudo-global statics
     (`riscv_current_is_tp`/`current_stack_pointer`, rule 0031) --
     deleted ONLY when grep-confirmed dead (no `get_current()` CALL
     anywhere in the TU, checked separately per static per the rule's
     own [status] note). A LIVE verdict (accessor genuinely called) is
     left untouched and flagged -- auto-"fixing" that case would need
     rewriting to the real inline-asm read, not a deletion, and a wrong
     guess here is exactly the boot-crashing bug class rule 0031 exists
     to prevent (issues #30/#31).
  6. `((void*)N + POISON_POINTER_DELTA)` translated as `.offset()` on a
     bare-integer-cast pointer -- fails Rust's const evaluator (E0080,
     no-provenance pointer arithmetic). Rewritten to plain integer
     `wrapping_add` + cast, bit-identical value, for any integer literal
     N (issue #99 -- LIST_POISON1/2, TIMER_ENTRY_STATIC, and any other
     constant sharing this exact c2rust output shape).

Detected and FLAGGED, not auto-fixed (needs judgement, not mechanics):
  - `kernel::warn_on!(<expr>)` where `<expr>` is bare (not already a
    bool-yielding comparison) -- c2rust sometimes omits the truthiness
    check C's WARN_ON() has, the mirror image of fix class 4 above
    (issue #100). NOT auto-detected here either -- a regex can't
    reliably tell "bare int expression" from "already-bool method
    call" (e.g. `warn_on!(x.is_none())` is already valid; a naive
    non-greedy paren match misreads it as bare-int, confirmed via
    testing) without real type information. Caught by the sweep's own
    rustc E0308 output per-file instead.
  - `::libc::` qualified paths (memcpy/memmove/strlen/size_t/etc) --
    c2rust's usual userspace/std-linked output convention, absent in
    this no_std kernel build (issue #98). The fix (strip the qualifier,
    relying on an existing local `extern "C"` declaration of the same
    name -- or add one) varies per file depending on what's already
    declared, so this is flagged for a per-file look rather than
    auto-rewritten.

Deliberately NOT automated (needs judgement, not mechanics):
  - Zacas/Zabha ISA-guard removal (issue #29's amocas/amoswap class) --
    an earlier automated attempt at this via brace-counting produced a
    real, hard-to-diagnose syntax error (see combined-boot-multi-file-
    2026-08-02.md's klist.c section). Left for manual per-block Edit
    calls with rustfmt --check verification after each one.
  - C-variadic function definitions (rule 0032) -- needs a real hand-
    written function body, not a mechanical rewrite.
  - BitfieldStruct opaquing -- try building raw first; only needed at
    all if c2rust_bitfields isn't already wired in cleanly (issue #55).

Usage:
  scripts/apply_c2rust_fix_classes.py <tree-dir> <file1_rs.rs> [file2_rs.rs ...]

  <tree-dir> is the kernel tree/worktree root (e.g.
  linux-riscv-worktrees/combined-boot-28) containing the files, given
  as paths relative to it (e.g. lib/once_rs.rs).

Prints a per-file, per-class summary of what was changed, and a final
list of files that still need the register-static and/or Zacas/Zabha
manual passes before they're likely to build. Always run
`rustfmt --edition 2021 --check <file>` yourself after this script
touches a file -- it does not run rustfmt itself, to keep this script's
own diffs minimal and reviewable.
"""
import logging
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "logs" / "apply_c2rust_fix_classes.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

FEATURE_LABEL_BREAK_RE = re.compile(r"^#!\[feature\(label_break_value\)\]\n", re.MULTILINE)
LABEL_USE_RE = re.compile(r"'_c2rust_label")
MACROS_EXPORT_USE_RE = re.compile(r"^use ::macros::export;\n?", re.MULTILINE)
EXPORT_ATTR_RE = re.compile(r"^#\[export\]$", re.MULTILINE)
BRACKET_ADDR_RE = re.compile(r"\[\{(\d+)(:?)\}\]")
# Both `!= 0` (the common case) and `== 0` (rarer, but equally an
# E0308 -- `bool == 0`/`bool != 0` are both type errors, c2rust assumes
# C's int-returning WARN_ON() either way) -- confirmed via a direct
# rustc probe that `bool == 0` fails identically to `bool != 0`.
WARN_ON_NEQ0_RE = re.compile(r"(kernel::warn_on!\([^;]*?\))\s*(!=|==)\s*0")
# ((void*)N + POISON_POINTER_DELTA) translated as .offset() on a bare
# integer-cast pointer -- fails Rust's const evaluator (E0080, no
# provenance). Any integer literal N, not just the LIST_POISON1/2
# (0x100/0x122) or TIMER_ENTRY_STATIC (0x300) values seen so far --
# issue awto-au/linux-rs#99.
POISON_POINTER_RE = re.compile(
    r"\((0x[0-9a-fA-F]+|\d+) as ::core::ffi::c_int as \*mut ::core::ffi::c_void\)"
    r"\.offset\(POISON_POINTER_DELTA as isize\)"
)
# Flagged, not auto-fixed -- see module docstring detected-not-fixed
# section. NOT attempting a bare-int warn_on!() detector here: matching
# a macro call's own closing paren with a regex is unreliable whenever
# the argument itself contains a nested call (e.g. `warn_on!(x.is_none())`
# is already valid -- bool -- but a naive `[^;]*?` non-greedy match stops
# at is_none()'s own `)`, not warn_on!'s, misreading it as a bare-int
# case). Confirmed false-positive-prone in testing; left to the sweep's
# real rustc E0308 output (issue #100) rather than a heuristic that can't
# balance parens.
LIBC_QUALIFIED_RE = re.compile(r"::libc::(\w+)")


def fix_poison_pointer_constants(text: str) -> tuple[str, int]:
    def repl(m: re.Match) -> str:
        n = m.group(1)
        return f"({n}u64.wrapping_add(POISON_POINTER_DELTA as u64)) as *mut ::core::ffi::c_void"

    return POISON_POINTER_RE.subn(repl, text)


def fix_unused_feature_label_break(text: str) -> tuple[str, bool]:
    if not FEATURE_LABEL_BREAK_RE.search(text):
        return text, False
    without_line = FEATURE_LABEL_BREAK_RE.sub("", text, count=1)
    if LABEL_USE_RE.search(without_line):
        # Genuinely used elsewhere (label_break_value blocks present) --
        # leave the feature line alone, do not strip.
        return text, False
    return without_line, True


def fix_export_attr(text: str) -> tuple[str, int]:
    text, removed_use = MACROS_EXPORT_USE_RE.subn("", text, count=1)
    text, n = EXPORT_ATTR_RE.subn("#[no_mangle]", text)
    return text, n


def fix_bracket_addressing(text: str) -> tuple[str, int]:
    def repl(m: re.Match) -> str:
        n, colon = m.group(1), m.group(2)
        return f"0({{{n}{colon}}})"

    return BRACKET_ADDR_RE.subn(repl, text)


def fix_warn_on_neq0(text: str) -> tuple[str, int]:
    return WARN_ON_NEQ0_RE.subn(r"\1", text)


def apply_fixes(path: Path) -> dict:
    text = path.read_text()
    original = text
    report = {}

    text, changed = fix_unused_feature_label_break(text)
    report["unused_feature_label_break_value_stripped"] = changed

    text, n = fix_export_attr(text)
    report["export_attr_sites_fixed"] = n

    text, n = fix_bracket_addressing(text)
    report["bracket_addressing_sites_fixed"] = n

    text, n = fix_warn_on_neq0(text)
    report["warn_on_neq0_sites_fixed"] = n

    text, n = fix_poison_pointer_constants(text)
    report["poison_pointer_sites_fixed"] = n

    if text != original:
        path.write_text(text)
    report["file_modified"] = text != original

    # Flagged, not auto-fixed -- see docstring.
    libc_symbols = sorted(set(LIBC_QUALIFIED_RE.findall(text)))
    report["libc_qualified_flagged"] = libc_symbols

    return report


RISCV_CURRENT_IS_TP_DECL_RE = re.compile(
    r"#\[no_mangle\]\npub static mut riscv_current_is_tp: \*mut task_struct ="
    r" ::core::ptr::null_mut::<task_struct>\(\);\n"
)
CURRENT_STACK_POINTER_DECL_RE = re.compile(
    r"#\[no_mangle\]\npub static mut current_stack_pointer: ::core::ffi::c_ulong = 0;\n"
)
GET_CURRENT_CALL_RE = re.compile(r"(?<!fn )\bget_current\s*\(\s*\)")
GET_CURRENT_DEF_RE = re.compile(r"\bfn\s+get_current\s*\(")


def check_and_fix_register_statics(tree: Path, rel_paths: list[str]) -> dict:
    """Rule 0031: delete riscv_current_is_tp/current_stack_pointer ONLY
    when grep-confirmed dead. Per rule 0031's own [status] note, the two
    statics are independently live/dead and must be checked separately
    -- deadness is proven by absence of any get_current() CALL (not
    just absence of the static's own identifier elsewhere), since the
    static is only ever touched indirectly through that accessor.
    Actually deletes the exact declaration block when safe; a LIVE
    verdict (get_current() genuinely called) is left untouched and
    flagged for the manual inline-asm-rewrite pass -- auto-deleting a
    live one is exactly the class of boot-crashing bug rule 0031 exists
    to prevent (see issues #30/#31)."""
    results = {}
    for rel in rel_paths:
        p = tree / rel
        text = p.read_text()
        no_comments = "\n".join(line.split("//", 1)[0] for line in text.splitlines())
        has_tp_static = bool(RISCV_CURRENT_IS_TP_DECL_RE.search(text))
        has_sp_static = bool(CURRENT_STACK_POINTER_DECL_RE.search(text))
        if not has_tp_static and not has_sp_static:
            results[rel] = "no fabricated static present"
            continue
        call_hits = list(GET_CURRENT_CALL_RE.finditer(no_comments))
        if call_hits:
            # get_current() is genuinely called -- both statics stay
            # (deleting current_stack_pointer alone would still be safe
            # since it's independent of get_current(), but leaving both
            # for one clear manual pass is safer than partial automation
            # on a file already flagged as needing eyes-on review).
            results[rel] = (
                f"LIVE — get_current() called {len(call_hits)}x, needs the rule 0031 "
                "inline-asm rewrite (not auto-fixed, left untouched)"
            )
            continue
        deleted = []
        if has_tp_static:
            text = RISCV_CURRENT_IS_TP_DECL_RE.sub("", text, count=1)
            deleted.append("riscv_current_is_tp")
        if has_sp_static:
            text = CURRENT_STACK_POINTER_DECL_RE.sub("", text, count=1)
            deleted.append("current_stack_pointer")
        p.write_text(text)
        results[rel] = f"dead, deleted: {', '.join(deleted)}"
    return results


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    tree = Path(sys.argv[1]).resolve()
    rel_paths = sys.argv[2:]
    if not tree.is_dir():
        log.error("not a directory: %s", tree)
        return 2

    LOG.parent.mkdir(parents=True, exist_ok=True)

    any_missing = False
    for rel in rel_paths:
        p = tree / rel
        if not p.is_file():
            log.error("missing file: %s", p)
            any_missing = True
    if any_missing:
        return 2

    log.info("applying mechanical fix classes to %d file(s) under %s", len(rel_paths), tree)
    summary = {}
    for rel in rel_paths:
        p = tree / rel
        report = apply_fixes(p)
        summary[rel] = report
        changes = [
            k
            for k, v in report.items()
            if k != "file_modified" and (v is True or (isinstance(v, int) and v > 0))
        ]
        if changes:
            log.info("%s: %s", rel, ", ".join(f"{k}={report[k]}" for k in changes))
        else:
            log.info("%s: no mechanical fixes needed", rel)
        if report.get("libc_qualified_flagged"):
            log.warning(
                "%s: FLAGGED ::libc:: symbol(s) (needs a local extern decl or qualifier strip): %s",
                rel, report["libc_qualified_flagged"],
            )

    log.info("checking register-pseudo-global static class (rule 0031 — deletes only when grep-confirmed dead)")
    static_results = check_and_fix_register_statics(tree, rel_paths)
    for rel, verdict in static_results.items():
        log.info("%s: %s", rel, verdict)

    needs_manual = [rel for rel, verdict in static_results.items() if verdict.startswith("LIVE")]
    if needs_manual:
        log.warning(
            "%d file(s) need the MANUAL rule-0031 inline-asm rewrite before building (get_current() is live): %s",
            len(needs_manual),
            ", ".join(needs_manual),
        )

    log.info("APPLY FIX CLASSES DONE — remember to rustfmt --edition 2021 --check each modified file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
