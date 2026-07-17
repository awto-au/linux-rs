#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check whether c2rust's CURRENT raw output already satisfies each of the
27 linux-rs rules (rulesdb/rules/*.toml), or violates it, per rule — so
future c2rust-patching work targets what's actually broken instead of
patching things c2rust already gets right (e.g. rule 0007 likely/unlikely
is already fine; rule 0006 fls-family is not).

This is text/regex scanning of c2rust's emitted .rs, not a real Rust AST
parser — "good enough to be useful, documented limitations" per rule, not
perfection. Several of the 27 rules are not text-checkable at all (tier 3
region/process rules needing runtime or human judgement) — those are
recorded as kind="not_mechanically_checkable" with a reason, not forced.

Usage: check_c2rust_rule_conformance.py
Inputs:
  rulesdb/rules/*.toml                         — rule definitions
  tmp/c2rust-baseline/*/output/src/*.rs         — c2rust output corpus
  linux-riscv/lib/**/*.c, arch/riscv/lib/**/*.c — C originals (cross-reference)
Outputs:
  tmp/c2rust-rule-conformance-report.md         — human-readable report
  rulesdb/patterns.db: c2rust_rule_conformance table
Log: tmp/check_c2rust_rule_conformance.log
"""
import datetime
import logging
import re
import sqlite3
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    print("Python 3.11+ required (tomllib)", file=sys.stderr)
    sys.exit(1)

REPO = Path(__file__).resolve().parent.parent
RULES_DIR = REPO / "rulesdb" / "rules"
BASELINE = REPO / "tmp" / "c2rust-baseline"
TREE = REPO / "linux-riscv"
DB = REPO / "rulesdb" / "patterns.db"
REPORT = REPO / "tmp" / "c2rust-rule-conformance-report.md"
LOG = REPO / "tmp" / "check_c2rust_rule_conformance.log"

STATUS_CONFORMANT = "conformant"
STATUS_VIOLATION = "violation"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_NOT_CHECKABLE = "not_checkable"


# ---------------------------------------------------------------------------
# Corpus helpers
# ---------------------------------------------------------------------------

def safe_name_to_c_path(safe_name: str) -> str | None:
    """arch_riscv_lib_csum.c -> arch/riscv/lib/csum.c ; lib_math_gcd.c ->
    lib/math/gcd.c. Best-effort: try the arch_riscv_ prefix first (longest
    match), then plain lib_ -> lib/, converting remaining '_' between path
    components is NOT attempted (filenames themselves can contain '_', e.g.
    lib_crc_crc32-main.c -> lib/crc/crc32-main.c) — we only split on the
    known directory-prefix tokens, not blindly replace every underscore.
    """
    name = safe_name
    if not name.endswith(".c"):
        return None
    if name.startswith("arch_riscv_lib_"):
        rest = name[len("arch_riscv_lib_"):]
        return "arch/riscv/lib/" + rest
    if name.startswith("lib_math_"):
        rest = name[len("lib_math_"):]
        return "lib/math/" + rest
    if name.startswith("lib_crypto_"):
        rest = name[len("lib_crypto_"):]
        return "lib/crypto/" + rest
    if name.startswith("lib_fonts_"):
        rest = name[len("lib_fonts_"):]
        return "lib/fonts/" + rest
    if name.startswith("lib_crc_"):
        rest = name[len("lib_crc_"):]
        return "lib/crc/" + rest
    if name.startswith("lib_"):
        rest = name[len("lib_"):]
        return "lib/" + rest
    return None


def find_c_original(safe_name: str) -> Path | None:
    rel = safe_name_to_c_path(safe_name)
    if rel is None:
        return None
    p = TREE / rel
    return p if p.exists() else None


def iter_corpus():
    """Yield (safe_name, rs_path, c_path_or_None) for every successfully
    transpiled TU in tmp/c2rust-baseline/."""
    if not BASELINE.exists():
        return
    for d in sorted(BASELINE.iterdir()):
        if not d.is_dir():
            continue
        src_dir = d / "output" / "src"
        if not src_dir.exists():
            continue
        for rs in sorted(src_dir.glob("*.rs")):
            yield d.name, rs, find_c_original(d.name)


# ---------------------------------------------------------------------------
# Per-rule checkers. Each returns a list of dicts:
#   {rust_file, line, status, detail}
# rust_file is relative to REPO for report/DB storage.
# ---------------------------------------------------------------------------

def relpath(p: Path) -> str:
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def check_0007_likely_unlikely(rs_text: str, rs_path: Path, c_text: str | None):
    """__builtin_expect(cond, N) is c2rust's lowering of likely()/unlikely().
    Conformant iff no residue of the hint survives — i.e. the condition is
    translated as a plain boolean/int test with no expect/hint wrapper.
    c2rust's actual pattern (confirmed, lib_errseq.c / lib_math_int_log.c):
    `(cond) as ::core::ffi::c_int as ::core::ffi::c_long != 0` — recursing
    straight into the condition, hint dropped. We flag VIOLATION only if
    something that looks like an expect/hint survived (it never does in the
    observed corpus, but check for it so a future c2rust change is caught)."""
    out = []
    if "__builtin_expect" in rs_text or "core::intrinsics::likely" in rs_text \
            or "core::intrinsics::unlikely" in rs_text or "core::hint::likely" in rs_text \
            or "core::hint::unlikely" in rs_text:
        for i, line in enumerate(rs_text.splitlines(), 1):
            if any(tok in line for tok in (
                    "__builtin_expect", "intrinsics::likely", "intrinsics::unlikely",
                    "hint::likely", "hint::unlikely")):
                out.append({"line": i, "status": STATUS_VIOLATION,
                            "detail": f"branch hint residue survived: {line.strip()[:160]}"})
        return out
    # No hint residue anywhere. Only meaningful if the C original actually
    # uses likely()/unlikely() at all — otherwise this file says nothing
    # about the rule (silent absence isn't evidence).
    if c_text and re.search(r"\b(un)?likely\s*\(", c_text):
        out.append({"line": None, "status": STATUS_CONFORMANT,
                    "detail": "likely()/unlikely() in C original; no branch-hint "
                              "residue in output — hint dropped, plain boolean test emitted "
                              "(as designed by the rule)"})
    return out


FLS_FAMILY_FNS = {
    "generic_fls": ("fls", "u32::BITS - x.leading_zeros()"),
    "generic___fls": ("__fls", "u32::BITS - 1 - x.leading_zeros()"),
    "fls64": ("fls64", "derived from __fls/leading_zeros with a +1/BITS offset"),
    "generic___ffs": ("__ffs", "x.trailing_zeros()"),
    "__ffs64": ("__ffs64", "derived from __ffs/trailing_zeros"),
    "fls_long": ("fls_long", "derived from fls/leading_zeros"),
}

FN_DEF_RE = re.compile(
    r'(?:pub )?unsafe extern "C" fn\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[^\{]+)?\{',
)


def _extract_fn_body(rs_text: str, start_match: re.Match) -> str:
    """Brace-match from the opening '{' of start_match to find the fn body."""
    i = start_match.end() - 1  # index of the opening '{'
    depth = 0
    for j in range(i, len(rs_text)):
        if rs_text[j] == "{":
            depth += 1
        elif rs_text[j] == "}":
            depth -= 1
            if depth == 0:
                return rs_text[i:j + 1]
    return rs_text[i:]


def check_0006_fls_family(rs_text: str, rs_path: Path, c_text: str | None):
    """Rule explicitly warns: bare `.leading_zeros()`/`.trailing_zeros()`
    with NO `BITS - ...` offset arithmetic is the classic wrong translation
    for the fls/__fls/fls64 (most-significant-bit) functions, which all
    need a `BITS -` (and __fls additionally a `- 1`) subtraction to convert
    a leading-zero count into an MSB index. __ffs (least-significant-bit)
    is different: `x.trailing_zeros()` IS the whole answer with no offset
    at all, since trailing_zeros() directly counts the LSB position — an
    offset-shaped check would be wrong to apply there. We find each
    fls/ffs-family function DEFINITION in the .rs (c2rust inlines the
    header implementation into every TU that pulls it in — the function is
    a faithful transliteration of the C bit-loop, not a rename), and check
    its body against the shape that specific function requires."""
    out = []
    for m in FN_DEF_RE.finditer(rs_text):
        fn_name = m.group(1)
        if fn_name not in FLS_FAMILY_FNS:
            continue
        c_name, expected = FLS_FAMILY_FNS[fn_name]
        line = rs_text[:m.start()].count("\n") + 1
        body = _extract_fn_body(rs_text, m)
        has_bits_offset = bool(re.search(
            r"(BITS\s*[-\.]\s*(1\s*-\s*)?|32\s*(as[^;]*)?-\s*|64\s*(as[^;]*)?-\s*)"
            r".{0,40}(leading|trailing)_zeros", body))
        has_leading_or_trailing = "leading_zeros" in body or "trailing_zeros" in body

        # __ffs's correct shape is bare trailing_zeros() with NO offset —
        # the opposite requirement from fls/__fls/fls64, which all need an
        # offset since leading_zeros() alone is an inverted, unoffset count.
        needs_no_offset = fn_name == "generic___ffs"

        if needs_no_offset:
            if "trailing_zeros" in body:
                out.append({"line": line, "status": STATUS_CONFORMANT,
                            "detail": f"fn {fn_name} ({c_name}) uses bare "
                                      f"trailing_zeros() — matches rule shape "
                                      f"({expected}), no offset needed for LSB index"})
            else:
                out.append({"line": line, "status": STATUS_VIOLATION,
                            "detail": f"fn {fn_name} ({c_name}): literal bit-scan-loop "
                                      f"transliteration of the C header implementation, no "
                                      f"trailing_zeros at all — c2rust faithfully "
                                      f"reproduces the C algorithm instead of the idiomatic "
                                      f"Rust rewrite ({expected})"})
        elif has_leading_or_trailing and has_bits_offset:
            out.append({"line": line, "status": STATUS_CONFORMANT,
                        "detail": f"fn {fn_name} ({c_name}) uses leading/trailing_zeros "
                                  f"with a BITS offset — matches rule shape ({expected})"})
        elif has_leading_or_trailing and not has_bits_offset:
            out.append({"line": line, "status": STATUS_VIOLATION,
                        "detail": f"fn {fn_name} ({c_name}): bare leading/trailing_zeros "
                                  f"call with NO BITS-offset subtraction found in body — "
                                  f"exactly the WRONG pattern the rule's negative field warns "
                                  f"about on sight"})
        else:
            out.append({"line": line, "status": STATUS_VIOLATION,
                        "detail": f"fn {fn_name} ({c_name}): literal bit-scan-loop "
                                  f"transliteration of the C header implementation, no "
                                  f"leading_zeros/trailing_zeros at all — c2rust faithfully "
                                  f"reproduces the C algorithm instead of the idiomatic "
                                  f"Rust rewrite ({expected})"})
    return out


def check_0002_ffs_trailing_zeros(rs_text: str, rs_path: Path, c_text: str | None):
    """Same mechanism as 0006 but scoped to __ffs specifically (rule 0002).
    generic___ffs is the header inline c2rust reproduces verbatim."""
    out = []
    for m in FN_DEF_RE.finditer(rs_text):
        fn_name = m.group(1)
        if fn_name != "generic___ffs":
            continue
        line = rs_text[:m.start()].count("\n") + 1
        body = _extract_fn_body(rs_text, m)
        if "trailing_zeros" in body:
            out.append({"line": line, "status": STATUS_CONFORMANT,
                        "detail": "generic___ffs uses trailing_zeros()"})
        else:
            out.append({"line": line, "status": STATUS_VIOLATION,
                        "detail": "generic___ffs: literal bit-scan-loop transliteration "
                                  "(word & 0xffffffff == 0 { num += 32; ... }), not "
                                  "x.trailing_zeros() — same failure mode as rule 0006's "
                                  "fls, mirrored on the ffs side"})
    # Direct __ffs(x) call sites where x >>= __ffs(x) or similar collapsed
    # into trailing_zeros directly at the call site (not via a helper fn) —
    # not observed in the corpus, but check for it since it would be the
    # BEST-case conformant shape (no helper fn at all).
    if re.search(r"\.trailing_zeros\(\)", rs_text) and not any(
            m.group(1) == "generic___ffs" for m in FN_DEF_RE.finditer(rs_text)):
        pass  # covered elsewhere; not a claim about this rule specifically
    return out


def check_0009_unsigned_wrap_mul(rs_text: str, rs_path: Path, c_text: str | None):
    """Rule wants unsigned a*b / a*=b -> a.wrapping_mul(b). c2rust's actual,
    consistent policy for ALL C unsigned multiply/multiply-assign (not
    limited to any one construct) is to always emit .wrapping_mul(...) —
    confirmed in lib/math/int_pow.c (result *= base -> result.wrapping_mul),
    lib/math/int_log.c (log * 646456993 -> log.wrapping_mul). We flag
    VIOLATION if a raw `*`/`*=` on values that look like unsigned c_u* types
    survives un-wrapped (heuristic: can't reliably tell signedness from text
    alone for locals without types in scope, so this direction is reported
    as ambiguous-if-found rather than a confident violation)."""
    out = []
    wrap_mul_sites = [i for i, line in enumerate(rs_text.splitlines(), 1)
                       if ".wrapping_mul(" in line]
    if wrap_mul_sites:
        for i in wrap_mul_sites:
            out.append({"line": i, "status": STATUS_CONFORMANT,
                        "detail": "wrapping_mul() present — matches rule's required form"})
    # Look for suspicious plain unsigned '*=' or a * b on c_u*/u32_0/u64_0
    # typed locals that did NOT become wrapping_mul — can't type-check text,
    # so mark ambiguous (needs human eyes), never a confident violation here.
    for i, line in enumerate(rs_text.splitlines(), 1):
        if re.search(r"[A-Za-z0-9_\)\]]\s\*=\s", line) and "wrapping_mul" not in line:
            out.append({"line": i, "status": STATUS_AMBIGUOUS,
                        "detail": f"plain '*=' survived, unclear if operand is unsigned "
                                  f"from text alone: {line.strip()[:160]}"})
    return out


def check_0004_unsigned_negate_isolate_lsb(rs_text: str, rs_path: Path, c_text: str | None):
    """r & -r (isolate lowest set bit) -> r & r.wrapping_neg(). Confirmed
    conformant in lib/math/gcd.c: `r &= -r` -> `r &= r.wrapping_neg()`."""
    out = []
    for i, line in enumerate(rs_text.splitlines(), 1):
        if ".wrapping_neg()" in line:
            out.append({"line": i, "status": STATUS_CONFORMANT,
                        "detail": f"wrapping_neg() present: {line.strip()[:160]}"})
    # A bare unary '-' on a value used in a subsequent '&' is a C construct
    # that shouldn't even compile as plain negate on unsigned Rust types, so
    # its absence isn't separately checkable here; wrapping_neg presence is
    # the positive signal we can extract from text.
    return out


def check_0003_swap_mem_swap(rs_text: str, rs_path: Path, c_text: str | None):
    """swap(a, b) macro -> core::mem::swap(&mut a, &mut b). c2rust does NOT
    know about the kernel's swap() macro's semantics as a primitive — it
    macro-expands swap(a,b) (from <linux/minmax.h>, itself built on a GNU
    statement-expression + typeof temp) into an inline temp-variable swap,
    confirmed in lib/math/gcd.c: swap(a, b) -> `let mut __tmp = a; a = b;
    b = __tmp;`. This is semantically equivalent but VIOLATES the rule's
    required emitted form (core::mem::swap)."""
    out = []
    if "core::mem::swap" in rs_text:
        for i, line in enumerate(rs_text.splitlines(), 1):
            if "core::mem::swap" in line:
                out.append({"line": i, "status": STATUS_CONFORMANT,
                            "detail": f"core::mem::swap present: {line.strip()[:160]}"})
        return out
    # Look for the c2rust manual-swap idiom: a fresh __tmp/c2rust_fresh-style
    # local assigned from one var, then two more assignments swapping them,
    # within a few lines of each other — the observed lowering of swap().
    lines = rs_text.splitlines()
    for i in range(len(lines) - 2):
        if re.search(r"let mut __tmp[A-Za-z0-9_]*\s*:", lines[i]) or \
           re.search(r"let mut c2rust_fresh\d+\s*=", lines[i]):
            window = "\n".join(lines[i:i + 4])
            if re.search(r"__tmp[A-Za-z0-9_]*", window) and window.count("=") >= 3:
                out.append({"line": i + 1, "status": STATUS_VIOLATION,
                            "detail": "manual temp-variable swap idiom (c2rust's macro-"
                                      "expansion of swap()'s statement-expression), not "
                                      "core::mem::swap: " + lines[i].strip()[:160]})
    return out


ELVIS_C_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\([^()]*\))?)\s*\?\s*:\s*([A-Za-z_][A-Za-z0-9_]*)")


def check_0010_gnu_elvis(rs_text: str, rs_path: Path, c_text: str | None):
    """GNU `x ? : y` has no direct Rust syntax — detection has to start
    from the C side (find `?:` sites), then check the corresponding Rust
    function for the once-eval-then-branch shape c2rust actually emits.
    Confirmed conformant in lib/sys_info.c: `si_mask ? : kernel_si_mask`
    became `let c2rust_fresh0 = si_mask; if c2rust_fresh0 != 0 {
    c2rust_fresh0 } else { kernel_si_mask }` — bound once, then if/else,
    exactly per the rule (no orphaned ternary-like construct, which Rust
    doesn't have syntax for anyway, so mis-detection risk is low)."""
    out = []
    if not c_text:
        return out
    sites = list(ELVIS_C_RE.finditer(c_text))
    if not sites:
        return out
    has_fresh_bind = bool(re.search(r"let (?:mut )?c2rust_fresh\d+\s*=", rs_text))
    has_if_else_ne0 = bool(re.search(r"if\s+\w+\s*!=\s*0\s*\{", rs_text))
    for m in sites:
        c_line = c_text[:m.start()].count("\n") + 1
        if has_fresh_bind and has_if_else_ne0:
            out.append({"line": None, "status": STATUS_CONFORMANT,
                        "detail": f"C elvis '{m.group(0)[:60]}' (C source line {c_line}): "
                                  f"corresponding output has a c2rust_freshN bind + "
                                  f"if != 0 {{}} else {{}} shape — bound-once-then-branch, "
                                  f"matches rule"})
        else:
            out.append({"line": None, "status": STATUS_AMBIGUOUS,
                        "detail": f"C elvis '{m.group(0)[:60]}' (C source line {c_line}): "
                                  f"could not confirm the once-eval+if/else shape in output "
                                  f"by text scan alone — needs a human look"})
    return out


def check_0008_warn_on(rs_text: str, rs_path: Path, c_text: str | None):
    """WARN_ON(cond)/WARN_ON_ONCE(cond) -> kernel::warn_on!(cond) per the
    rule. c2rust has zero knowledge of the kernel crate's warn_on! macro —
    it macro-expands WARN_ON's C definition (report_bug + unlikely wrapping)
    down to a local `__ret_warn_on` boolean/int, confirmed in
    lib/math/int_log.c and lib/devres.c. VIOLATION whenever this residue
    pattern is present; would be CONFORMANT if `kernel::warn_on!` or
    `warn_on!` ever appeared (never observed — raw c2rust output has no
    concept of the kernel Rust crate's macros)."""
    out = []
    if re.search(r"\bwarn_on!\s*\(", rs_text):
        for i, line in enumerate(rs_text.splitlines(), 1):
            if re.search(r"\bwarn_on!\s*\(", line):
                out.append({"line": i, "status": STATUS_CONFORMANT,
                            "detail": f"kernel warn_on! macro present: {line.strip()[:160]}"})
        return out
    for i, line in enumerate(rs_text.splitlines(), 1):
        if "__ret_warn_on" in line:
            out.append({"line": i, "status": STATUS_VIOLATION,
                        "detail": f"WARN_ON lowered to a local __ret_warn_on variable "
                                  f"(c2rust's literal macro expansion), not "
                                  f"kernel::warn_on!(): {line.strip()[:160]}"})
    return out


def check_0001_export_symbol_gpl(rs_text: str, rs_path: Path, c_text: str | None):
    """N/A for raw c2rust output — c2rust doesn't know about linux-rs's own
    #[export] proc-macro at all; it always emits a plain #[no_mangle] pub
    unsafe extern "C" fn for anything EXPORT_SYMBOL_GPL'd (confirmed:
    lib/math/gcd.c's gcd, lib/math/int_pow.c's int_pow). Recorded as
    not-applicable rather than pass/fail, per the task brief."""
    return []  # handled as NOT_APPLICABLE at the rule level, not per-file


def check_0027_export_data_symbol(rs_text: str, rs_path: Path, c_text: str | None):
    """Similarly N/A: c2rust emits `#[no_mangle] pub static` for exported
    data already by its own default lowering of EXPORT_SYMBOL on data
    (confirmed: lib/math/gcd.c's efficient_ffs_key gets #[no_mangle] pub
    static mut) — this happens to already match the rule's target shape,
    but the rule's *emit* is specifically about linux-rs's own hexdump.c
    case; treat as not-applicable to raw c2rust output like 0001, since
    c2rust isn't choosing this deliberately per the rule's rationale, it's
    just its default lowering for any EXPORT_SYMBOL'd static."""
    return []


CHECKERS = {
    "export-symbol-gpl": ("0001", check_0001_export_symbol_gpl, "not_applicable"),
    "ffs-trailing-zeros": ("0002", check_0002_ffs_trailing_zeros, None),
    "swap-mem-swap": ("0003", check_0003_swap_mem_swap, None),
    "unsigned-negate-isolate-lsb": ("0004", check_0004_unsigned_negate_isolate_lsb, None),
    "static-key-branch": ("0005", None, "tier 3 region rule: requires finding every runtime "
                                          "writer of the static key across the whole tree "
                                          "(the rule's own 'THE TRAP') — not extractable from "
                                          "text-scanning a single .rs file"),
    "fls-family": ("0006", check_0006_fls_family, None),
    "likely-unlikely": ("0007", check_0007_likely_unlikely, None),
    "warn-on": ("0008", check_0008_warn_on, None),
    "unsigned-wrap-mul": ("0009", check_0009_unsigned_wrap_mul, None),
    "gnu-elvis": ("0010", check_0010_gnu_elvis, None),
    "perf-parity-gate": ("0011", None, "process rule: a benchmarking methodology (pinned-"
                                         "core, min-of-N, cross-impl checksum), not a text "
                                         "pattern in generated code at all"),
    "sentinel-fn-pointers": ("0012", None, "tier 2 region rule: requires proving sentinel "
                                             "values are below any mappable address and "
                                             "tracing resolution order across a whole TU — "
                                             "beyond text/regex scanning"),
    "voidptr-byte-arith": ("0013", None, "tier 2 expr rule: requires per-function bounds/"
                                           "alignment invariant analysis not present in the "
                                           "text of the generated Rust"),
    "config-macro-shim": ("0014", None, "tier 1 macro rule but N/A to raw c2rust output: "
                                          "c2rust has no concept of linux-rs's rust_helper_* "
                                          "shim convention — it just macro-expands these "
                                          "C macros inline like any other; nothing to "
                                          "conformance-check against a shim it never emits"),
    "userspace-typed-copy": ("0015", None, "tier 2 region rule: distinguishing count-observing "
                                             "callers from count-discarding callers requires "
                                             "tracing caller control flow, not a per-site "
                                             "text pattern"),
    "header-inline-dep": ("0016", None, "tier 1 but process/judgement-heavy: identifying "
                                          "'the inline must be small and self-contained' and "
                                          "whether other C callers still exist requires "
                                          "cross-TU knowledge beyond a single .rs file's text"),
    "lkmm-primitive-shim": ("0017", None, "tier 3 region rule: LKMM ordering semantics can't "
                                            "be verified from generated-code text; requires "
                                            "memory-model reasoning"),
    "c-abi-allocator-contract": ("0018", None, "tier 2 api rule: whether an allocation is "
                                                 "'caller-visible' requires whole-program "
                                                 "cross-TU escape analysis, not visible in one "
                                                 "file's text"),
    "macro-template-family-closure": ("0019", None, "tier 1 but c2rust doesn't do closure-based "
                                                       "refactoring at all — it transliterates "
                                                       "each macro instantiation as a separate "
                                                       "literal expansion; there's no 'closure "
                                                       "family' shape to even check for, this "
                                                       "rule targets human-authored Rust "
                                                       "structure, not c2rust's output"),
    "goto-shared-label-distinct-value": ("0020", None, "process/checklist rule ('verify the "
                                                          "exact statement range... by hand') "
                                                          "— not a target-code pattern to scan "
                                                          "for at all, it's a translation-time "
                                                          "discipline"),
    "cross-tu-c-call": ("0021", None, "tier 1 api rule but requires knowing which callee TUs "
                                        "are 'already translated' vs not project-wide — c2rust "
                                        "output for a single TU can't express bindings::<name> "
                                        "cross-crate calls at all (it's raw C-to-C-shaped Rust, "
                                        "no bindings:: concept), so there's no output shape to "
                                        "check conformance against"),
    "switch-fallthrough-cumulative-effect": ("0022", None, "process/checklist rule about "
                                                              "correctly reading C fallthrough "
                                                              "semantics by hand — c2rust's "
                                                              "literal transliteration of "
                                                              "fallthrough control flow is "
                                                              "definitionally faithful to the "
                                                              "same bug-prone C source; not a "
                                                              "text pattern to check"),
    "safe-lift-lock-guard": ("0023", None, "tier 3, explicitly DEFERRED/future work per its "
                                             "own [status] note — not applied to any TU yet, "
                                             "raw c2rust output is always the pre-lift form by "
                                             "construction, nothing to check"),
    "safe-lift-refcount": ("0024", None, "tier 3, explicitly DEFERRED per its own [status] "
                                           "note — same as 0023"),
    "safe-lift-aref-ownership": ("0025", None, "tier 3, explicitly DEFERRED per its own "
                                                 "[status] note — same as 0023"),
    "arch-override-dead-generic": ("0026", None, "process rule requiring cross-referencing "
                                                    "arch headers for #ifndef/#define override "
                                                    "chains and post-build System.map "
                                                    "verification — not a single-file text "
                                                    "pattern"),
    "export-data-symbol": ("0027", check_0027_export_data_symbol, "not_applicable"),
}


def load_rule_meta():
    meta = {}
    for f in sorted(RULES_DIR.glob("*.toml")):
        d = tomllib.load(open(f, "rb"))
        m = re.match(r"(\d+)-", f.name)
        number = m.group(1) if m else "?"
        meta[d["id"]] = {
            "number": number,
            "tier": d.get("tier", 1),
            "category": d.get("category", ""),
        }
    return meta


def main() -> int:
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    rule_meta = load_rule_meta()
    corpus = list(iter_corpus())
    logging.info("corpus: %d transpiled TUs found under %s", len(corpus), BASELINE)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    results = []  # rows: (rule_id, c_file, rust_file, line, status, detail, checked_at)

    for rule_id, (number, fn, na_reason) in CHECKERS.items():
        # not_applicable is a rule-level classification independent of
        # whether a checker function is registered (0001/0027 have a real
        # function documenting WHY they're N/A, but the classification
        # itself must not depend on what that function returns).
        if na_reason == "not_applicable":
            results.append((rule_id, None, None, None, "not_applicable",
                            "raw c2rust output has no notion of this linux-rs-side "
                            "convention; N/A rather than pass/fail", now))
            continue
        if fn is None and na_reason:
            results.append((rule_id, None, None, None, STATUS_NOT_CHECKABLE, na_reason, now))
            continue

        any_hit = False
        for safe_name, rs_path, c_path in corpus:
            rs_text = rs_path.read_text(errors="replace")
            c_text = c_path.read_text(errors="replace") if c_path else None
            try:
                hits = fn(rs_text, rs_path, c_text)
            except Exception as e:
                logging.warning("checker for %s crashed on %s: %s", rule_id, rs_path, e)
                continue
            for h in hits:
                any_hit = True
                results.append((
                    rule_id,
                    str(c_path.relative_to(TREE)) if c_path else None,
                    relpath(rs_path),
                    h.get("line"),
                    h["status"],
                    h["detail"],
                    now,
                ))
        if not any_hit:
            results.append((rule_id, None, None, None, STATUS_NOT_CHECKABLE,
                            "checker implemented but found zero matching sites in the "
                            "current corpus (construct not present in any of the "
                            f"{len(corpus)} available transpiled files)", now))

    logging.info("total result rows: %d", len(results))

    # ---- write SQLite ----
    write_db(results)

    # ---- write markdown report ----
    write_report(results, rule_meta, len(corpus))

    logging.info("done: %s, %s", REPORT, DB)
    return 0


def write_db(results):
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS c2rust_rule_conformance (
            id INTEGER PRIMARY KEY,
            rule_id TEXT NOT NULL,
            c_file TEXT,
            rust_file TEXT,
            line INTEGER,
            status TEXT NOT NULL,
            detail TEXT NOT NULL,
            checked_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_c2rust_ruleconf_rule "
                 "ON c2rust_rule_conformance(rule_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_c2rust_ruleconf_status "
                 "ON c2rust_rule_conformance(status)")
    # This run replaces any prior run's rows (current-state snapshot, not
    # accumulating history like c2rust_attempts) — delete then insert.
    conn.execute("DELETE FROM c2rust_rule_conformance")
    conn.executemany(
        "INSERT INTO c2rust_rule_conformance "
        "(rule_id, c_file, rust_file, line, status, detail, checked_at) "
        "VALUES (?,?,?,?,?,?,?)",
        results,
    )
    conn.commit()
    conn.close()


def write_report(results, rule_meta, n_corpus):
    by_rule = {}
    for row in results:
        rule_id, c_file, rust_file, line, status, detail, checked_at = row
        by_rule.setdefault(rule_id, []).append(row)

    lines = []
    lines.append("# c2rust raw-output rule-conformance report")
    lines.append("")
    lines.append(f"Generated by `scripts/check_c2rust_rule_conformance.py`. "
                 f"Corpus: {n_corpus} successfully-transpiled TUs under "
                 f"`tmp/c2rust-baseline/*/output/src/*.rs`.")
    lines.append("")
    lines.append("Purpose: tells us, per rule, whether c2rust's CURRENT output already "
                 "satisfies the rule (no patch needed) or violates it (a concrete future "
                 "c2rust-patch target) — so patch work is prioritized correctly.")
    lines.append("")
    lines.append("| rule | tier | checkable | conformant | violation | ambiguous | n/a |")
    lines.append("|---|---|---|---|---|---|---|")
    for rule_id in CHECKERS:
        number = rule_meta.get(rule_id, {}).get("number", "?")
        tier = rule_meta.get(rule_id, {}).get("tier", "?")
        rows = by_rule.get(rule_id, [])
        statuses = [r[4] for r in rows]
        checkable = "no" if (STATUS_NOT_CHECKABLE in statuses and
                              len(set(statuses)) == 1) else "yes"
        if "not_applicable" in statuses and len(set(statuses)) == 1:
            checkable = "n/a"
        n_conf = statuses.count(STATUS_CONFORMANT)
        n_viol = statuses.count(STATUS_VIOLATION)
        n_amb = statuses.count(STATUS_AMBIGUOUS)
        n_na = statuses.count("not_applicable")
        lines.append(f"| {number}-{rule_id} | {tier} | {checkable} | {n_conf} | {n_viol} "
                     f"| {n_amb} | {n_na} |")
    lines.append("")
    lines.append("## Detail per rule")
    lines.append("")

    for rule_id in CHECKERS:
        number = rule_meta.get(rule_id, {}).get("number", "?")
        tier = rule_meta.get(rule_id, {}).get("tier", "?")
        category = rule_meta.get(rule_id, {}).get("category", "?")
        rows = by_rule.get(rule_id, [])
        lines.append(f"### {number}-{rule_id} (tier {tier}, {category})")
        lines.append("")
        if not rows:
            lines.append("_no results recorded_")
            lines.append("")
            continue
        if rows[0][4] == STATUS_NOT_CHECKABLE and len(rows) == 1 and rows[0][2] is None:
            lines.append(f"**NOT MECHANICALLY CHECKABLE**: {rows[0][5]}")
            lines.append("")
            continue
        if rows[0][4] == "not_applicable" and len(rows) == 1 and rows[0][2] is None:
            lines.append(f"**NOT APPLICABLE to raw c2rust output**: {rows[0][5]}")
            lines.append("")
            continue

        n_files = len({r[2] for r in rows if r[2]})
        n_conf = sum(1 for r in rows if r[4] == STATUS_CONFORMANT)
        n_viol = sum(1 for r in rows if r[4] == STATUS_VIOLATION)
        n_amb = sum(1 for r in rows if r[4] == STATUS_AMBIGUOUS)
        lines.append(f"Found in {n_files} file(s): {n_conf} conformant, {n_viol} violation, "
                     f"{n_amb} ambiguous instance(s).")
        lines.append("")
        for status_filter, label in ((STATUS_VIOLATION, "Violations"),
                                       (STATUS_AMBIGUOUS, "Ambiguous"),
                                       (STATUS_CONFORMANT, "Conformant (examples)")):
            subset = [r for r in rows if r[4] == status_filter]
            if not subset:
                continue
            lines.append(f"**{label}** ({len(subset)}):")
            for r in subset[:8]:
                rule_id2, c_file, rust_file, line, status, detail, checked_at = r
                loc = rust_file or "?"
                if line:
                    loc += f":{line}"
                lines.append(f"- `{loc}` — {detail}")
            if len(subset) > 8:
                lines.append(f"- ... and {len(subset) - 8} more (see DB table "
                             f"`c2rust_rule_conformance`)")
            lines.append("")

    REPORT.write_text("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
