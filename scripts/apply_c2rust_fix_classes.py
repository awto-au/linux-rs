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
# 2 of the 3 ::libc:: legs investigated for issue #98, confirmed safe
# and generalizable at adversarial verify:
#   - memcpy/memset/memmove/strcmp/strlen are already bindgen-generated
#     in the `bindings` crate (from the real kernel headers) with
#     argument-for-argument matching signatures, and `bindings` is
#     already linked into every translated file -- confirmed every
#     call site in the corpus is already inside an unsafe{} block (0
#     exceptions across all 49 affected files).
#   - intptr_t is never locally defined in this corpus (unlike its
#     cousin uintptr_t, which IS defined as `pub type uintptr_t =
#     usize;` when needed) -- always used as a plain signed
#     pointer-width scalar, safe to substitute the real primitive.
# The THIRD leg (size_t -> bare `size_t`) was found NOT safe at
# verify: in ~24 files that combine size_t with one of the 5 functions
# above, size_t's local alias chain resolves to c_ulong (u64 on this
# target), but bindings::{memcpy,memset,memmove}'s size parameter is
# usize -- the "just strip the qualifier" fix introduces a NEW E0308
# (u64 vs usize) at the call site in most of those files. Left
# flagged, not auto-fixed, same as the file's other type-ambiguous
# cases -- see issue #98.
LIBC_FN_RE = re.compile(r"::libc::(memcpy|memset|memmove|strcmp|strlen)\b")
LIBC_INTPTR_T_RE = re.compile(r"::libc::intptr_t\b")

# c2rust's wait_event_interruptible*() translation: the early-exit path
# assigns the return value (`__ret[_N] = __int;`) then does a bare
# `break 'label;` with no payload, while the block's other exit
# (fallthrough after the loop) correctly does `break 'label __ret[_N];`
# -- both must carry the same value since the labeled block is used as
# a value-producing expression. The variable is always the one just
# assigned on the immediately preceding line, captured from context
# rather than guessed -- issue awto-au/linux-rs#104, confirmed identical
# shape across 5 files.
LABELED_BLOCK_BARE_BREAK_RE = re.compile(
    r"(?P<assign>(?P<var>\b\w+\b) = __int;\n\s*)break '(?P<label>_c2rust_label(?:_\d+)?);"
)
# c2rust mistranslates the C compound literal `(size_t){0}` (the
# `void *: &(size_t){ 0 }` no-op branch of __set_flex_counter()'s
# _Generic, taken whenever a flexible-array-member has no
# __counted_by() annotation) as a bare Rust cast expression
# `0 as size_t` instead of a real place -- `&raw mut` of that cast
# fails E0745 (temporary value). The write-through is a deliberate C
# no-op sink; give it a real backing local instead -- issue
# awto-au/linux-rs#105.
FLEX_COUNTER_NOOP_RE = re.compile(
    r"let (c2rust_lvalue_ptr(?:_\d+)?) = &raw mut \(0 as size_t\);\n"
    r"(\s*)\*\1 = __count;"
)
# `stdsimd` is a defunct feature name no longer in rustc's feature
# list -- c2rust stamps it onto any TU that #includes
# linux/mmu_notifier.h, unconditionally regardless of
# #[cfg(target_arch = ...)], so it hard-errors (E0635) on every
# non-x86 target even though the gated code is always dead there.
# Strips only the stdsimd token, preserving any other feature names
# (e.g. label_break_value) already present -- issue awto-au/linux-rs#106.
FEATURE_LINE_RE = re.compile(r"^#!\[feature\(([^)]*)\)\]\n", re.MULTILINE)
# c2rust occasionally drops the statement-terminating `;` when
# flattening a triple-nested GNU statement-expression -- seen where a
# WARN_ON(overflows_type(...)) C idiom expands through
# __overflows_type's nested check_add_overflow statement-expr. The
# narrowed result assignment is left with no `;` before the following
# __must_check_overflow(...) tail expression -- issue
# awto-au/linux-rs#111 (confirmed unique to one file/site in the whole
# corpus; safe to run unconditionally).
C2RUST_RESULT_NARROW_MISSING_SEMI_RE = re.compile(
    r"(= c2rust_result_narrow(?:_\d+)?)(\s*\n\s*)(__must_check_overflow\()"
)


def fix_libc_fn_and_intptr(text: str) -> tuple[str, int]:
    text, n1 = LIBC_FN_RE.subn(r"bindings::\1", text)
    text, n2 = LIBC_INTPTR_T_RE.subn("isize", text)
    return text, n1 + n2


# A c2rust-bitfields #[bitfield] setter (X.set_FIELD(...)) desugars to
# a &mut self method call. When the C source's argument expression
# itself takes the address of the same struct value being assigned
# into (the common `x.field = f(&x)` idiom, translated this way
# because `field` is a C :1 bitfield rather than a plain field), c2rust
# emits `x.set_field(f(&raw mut x))` -- two overlapping mutable borrows
# of `x` in one expression (E0499). Harmless in C (a plain struct
# write), a real borrow conflict in Rust once the field becomes a
# bitfield-derive method call. Fixed by hoisting the argument into a
# local temporary first, breaking the overlap -- exactly rustc's own
# suggested fix -- issue awto-au/linux-rs#115. Strictly single-line:
# `\n` is excluded from both argument-capture classes so the match
# cannot span a c2rust-style multiline call (`.set_FIELD(\n    ...,\n
# );`) -- a future multiline self-referential setter simply won't
# match and surfaces as an unhandled E0499 for manual review instead
# of risking a malformed rewrite.
SELF_BORROW_SETTER_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<recv>[A-Za-z_]\w*)\.(?P<setter>set_\w+)\("
    r"(?P<args>[^;\n]*&raw mut (?P=recv)\b[^;\n]*)\);\n",
    re.MULTILINE,
)


def fix_bitfield_setter_self_borrow(text: str) -> tuple[str, int]:
    def repl(m: re.Match) -> str:
        indent = m.group("indent")
        recv = m.group("recv")
        setter = m.group("setter")
        args = m.group("args")
        tmp = f"__c2rust_{recv}_{setter}_arg"
        return f"{indent}let {tmp} = {args};\n{indent}{recv}.{setter}({tmp});\n"

    return SELF_BORROW_SETTER_RE.subn(repl, text)


def fix_labeled_block_bare_break(text: str) -> tuple[str, int]:
    def repl(m: re.Match) -> str:
        return f"{m.group('assign')}break '{m.group('label')} {m.group('var')};"

    return LABELED_BLOCK_BARE_BREAK_RE.subn(repl, text)


def fix_flex_counter_noop_sink(text: str) -> tuple[str, int]:
    def repl(m: re.Match) -> str:
        name, indent = m.group(1), m.group(2)
        tmp = f"{name}_tmp"
        return (
            f"let mut {tmp}: size_t = 0;\n"
            f"{indent}let {name} = &raw mut {tmp};\n"
            f"{indent}*{name} = __count;"
        )

    return FLEX_COUNTER_NOOP_RE.subn(repl, text)


def fix_stdsimd_feature(text: str) -> tuple[str, int]:
    def repl(m: re.Match) -> str:
        names = [n.strip() for n in m.group(1).split(",") if n.strip()]
        if "stdsimd" not in names:
            return m.group(0)
        remaining = [n for n in names if n != "stdsimd"]
        if not remaining:
            return ""
        return f"#![feature({', '.join(remaining)})]\n"

    new_text = FEATURE_LINE_RE.sub(repl, text)
    return new_text, (1 if new_text != text else 0)


def fix_missing_semicolon_before_must_check_overflow(text: str) -> tuple[str, int]:
    return C2RUST_RESULT_NARROW_MISSING_SEMI_RE.subn(r"\1;\2\3", text)


# c2rust's compiletime_assert() unique-ID reuse bug: c2rust emits a
# bare (non-_0-suffixed) __compiletime_assert_N() call at some sites
# with no matching #[link_name="__compiletime_assert_N"] extern decl
# reachable in scope. The guarded `if` is always a compile-time-
# constant-false BUILD_BUG-style size check, so the call is
# unreachable in practice -- synthesizing a `-> !` extern decl matching
# the shape c2rust itself uses for every working sibling is safe.
# Always synthesizes a FRESH, locally-scoped extern block at EVERY
# orphan site, even when the same N has a decl elsewhere in the file --
# c2rust nests each extern "C" {} inside its own call site's specific
# block-expression, which is NOT visible from a sibling or later
# block-expression in the same function (a prior "reuse by name" draft
# of this fix was disproven against the real corpus: it silently left
# the E0425 in place under a renamed symbol) -- issue awto-au/linux-rs#102.
ORPHAN_COMPILETIME_ASSERT_RE = re.compile(r"\b__compiletime_assert_(\d+)\(\)")
# c2rust flexible-array-member (FAM) initializer emitted as a cast from
# a synthesized helper constant to the field's true zero-length array
# type -- e.g. `MM_STRUCT_FLEXIBLE_ARRAY_INIT as [::core::ffi::c_char; 0]`.
# Rust's `as` never converts between array types of different length
# (E0605) regardless of element type or source size; a C99 flexible
# array member's only valid Rust value is the empty array literal `[]`
# -- confirmed against this exact file's own correctly-transpiled
# sibling initializer of the same field. Negative lookbehind excludes a
# trailing path/field-access segment (`self.foo as [u8; 0]`,
# `Foo::BAR as [u8; 0]`) from matching -- those are left for manual
# review rather than risking a mis-truncated rewrite -- issue
# awto-au/linux-rs#109.
FAM_CAST_RE = re.compile(r"(?<![.\w:])\b\w+ as (\[[^;]+; 0\])")


def fix_orphaned_compiletime_assert(text: str) -> tuple[str, int]:
    def repl(m: re.Match) -> str:
        n = m.group(1)
        return (
            f'{{\n'
            f'                        extern "C" {{\n'
            f'                            #[link_name = "__compiletime_assert_{n}"]\n'
            f'                            fn __compiletime_assert_{n}_0() -> !;\n'
            f'                        }}\n'
            f'                        __compiletime_assert_{n}_0()\n'
            f'                    }}'
        )

    return ORPHAN_COMPILETIME_ASSERT_RE.subn(repl, text)


def fix_fam_cast_constants(text: str) -> tuple[str, int]:
    return FAM_CAST_RE.subn("[]", text)


# c2rust wraps some structs needing extra padding/alignment in a
# C2Rust_<Name>_Inner newtype (same mechanism as the C2Rust_Unnamed_N
# padding wrappers seen elsewhere) but its initializer-rewrite pass can
# miss a struct LITERAL elsewhere in the same file, leaving it using
# the pre-wrap field-by-name brace syntax against the outer tuple
# struct, which has no such fields -- E0560, issue awto-au/linux-rs#113.
# Anchored on the wrapper's own declaration line per file. `[^{}]*`
# assumes a flat field list with no nested brace-literal field values
# (true of every wrapped-struct literal seen in the corpus so far --
# single scalar fields; a genuinely empty-bodied function returning
# `*mut WrapperName {}` could in principle confuse this on some future
# unswept file, since a regex can't balance braces reliably here any
# more than the already-rejected bare-int warn_on! detector could, but
# that failure mode is a compile error rustc would reject, not silent
# wrong behavior, so it's a known limitation rather than a landing
# blocker).
NEWTYPE_WRAPPER_DECL_RE = re.compile(r"^pub struct (\w+)\(pub (C2Rust_\w+_Inner)\);$", re.MULTILINE)
# c2rust cross-TU merge bug: an incomplete/weak-extern array decl with
# no initializer in this TU (e.g. `const char linux_banner[] __weak;`
# in init/version.c, real definition + real length living in a
# DIFFERENT TU) gets its declared array length defaulted to 0, but the
# real initializer's byte string (length N) still gets merged in on the
# transmute's source side -- producing
# `transmute::<[u8; N], [TYPE; 0]>(*b"...")`, an N-vs-0 size mismatch
# (E0512). The transmute's source array length is the only ground
# truth available in this TU; fixing both the transmute's target array
# length and the static's own declared array length to N makes the
# error disappear entirely -- issue awto-au/linux-rs#114.
ZERO_LEN_ARRAY_DECL_RE = re.compile(
    r"(: \[([\w:]+); )0(\]\s*=\s*unsafe\s*\{\s*::core::mem::transmute::<\s*\[u8;\s*)(\d+)(\s*\],)",
    re.DOTALL,
)
ZERO_LEN_ARRAY_TRANSMUTE_RE = re.compile(
    r"::core::mem::transmute::<\s*\[u8;\s*(\d+)\s*\]\s*,\s*\[([\w:]+);\s*0\s*\]\s*,?\s*>",
    re.DOTALL,
)
# c2rust sometimes attaches a lifetime parameter to a plain-data struct
# translated from C without ever using it in a field type (E0392,
# issue awto-au/linux-rs#117) -- every c2rust struct field type derived
# from C is a raw pointer/value, never a Rust reference, so a lifetime
# param genuinely unused in the field list is always dead here (unlike
# hand-written kernel/rust crate code, which legitimately uses struct
# lifetimes and is untouched by this regex, since it only fires on
# non-generic-bracket structs literally named `pub struct NAME<'lt> {
# ... }` with `'lt` absent from the body).
STRUCT_UNUSED_LIFETIME_RE = re.compile(r"pub struct (\w+)<(\'\w+)>( \{[^}]*\})", re.DOTALL)


def fix_newtype_wrapper_struct_literals(text: str) -> tuple[str, int]:
    total = 0
    for name, inner in NEWTYPE_WRAPPER_DECL_RE.findall(text):
        pattern = re.compile(r"(?<!struct )\b" + re.escape(name) + r"\s*\{([^{}]*)\}")

        def repl(m: re.Match, _name=name, _inner=inner) -> str:
            return f"{_name}({_inner} {{{m.group(1)}}})"

        text, n = pattern.subn(repl, text)
        total += n
    return text, total


def fix_zero_len_array_transmute(text: str) -> tuple[str, int]:
    def decl_repl(m: re.Match) -> str:
        prefix, _ty, mid, n, tail = m.groups()
        return f"{prefix}{n}{mid}{n}{tail}"

    def transmute_repl(m: re.Match) -> str:
        n, ty = m.group(1), m.group(2)
        return f"::core::mem::transmute::<[u8; {n}], [{ty}; {n}]>"

    text, n_decl = ZERO_LEN_ARRAY_DECL_RE.subn(decl_repl, text)
    text, n_trans = ZERO_LEN_ARRAY_TRANSMUTE_RE.subn(transmute_repl, text)
    return text, n_trans


# `#[used]` attached directly to the __exit-macro function itself
# (`#[link_section = ".exit.text"]` / `#[used]` / `#[cold]` / `unsafe
# extern "C" fn ..._exit/_fini(...)`) -- Rust's `#[used]` is only valid
# on `static` items, but c2rust carries over GCC's
# `__attribute__((used))` from the kernel's __exit macro expansion onto
# the function unconditionally. Every sibling __init function in the
# same files gets the matching `#[link_section = ".init.text"]` +
# `#[cold]` pair with NO `#[used]`, confirming this is specific to the
# __exit shape -- issue awto-au/linux-rs#101. NOTE: `#[used]` is
# dropped rather than translated to an equivalent; this changes
# --gc-sections retention for these specific functions versus GCC's
# original semantics, and is currently inert only because
# CONFIG_MODULES is unset in this build's .config (an unreferenced
# __exit function is normally only reachable via module unload, which
# doesn't exist without CONFIG_MODULES) -- revisit if/when a
# modules-enabled config is ever targeted.
USED_ON_EXIT_FN_RE = re.compile(
    r'(#\[link_section = "\.exit\.text"\]\n)#\[used\]\n(#\[cold\]\nunsafe extern "C" fn)'
)


def fix_used_on_exit_fn(text: str) -> tuple[str, int]:
    return USED_ON_EXIT_FN_RE.subn(r"\1\2", text)


def fix_unused_struct_lifetime(text: str) -> tuple[str, int]:
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        name, lt, body = m.group(1), m.group(2), m.group(3)
        if lt in body:
            return m.group(0)
        n += 1
        return f"pub struct {name}{body}"

    return STRUCT_UNUSED_LIFETIME_RE.sub(repl, text), n


def fix_poison_pointer_constants(text: str) -> tuple[str, int]:
    def repl(m: re.Match) -> str:
        n = m.group(1)
        # usize, not a fixed u64: POISON_POINTER_DELTA is `unsigned
        # long` in C (pointer-width -- ILP32's 32-bit UL on a 32BIT
        # config, LP64's 64-bit UL on 64BIT), currently 0 on 32-bit
        # configs (arch/riscv/Kconfig) so a hardcoded u64 has been
        # harmless so far by accident, not by correctness -- RV32
        # feasibility research (2026-08-02) found this the one
        # target-width assumption in this fix worth calling out.
        return f"({n}usize.wrapping_add(POISON_POINTER_DELTA as usize)) as *mut ::core::ffi::c_void"

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

    text, n = fix_libc_fn_and_intptr(text)
    report["libc_fn_and_intptr_sites_fixed"] = n

    text, n = fix_bitfield_setter_self_borrow(text)
    report["bitfield_setter_self_borrow_sites_fixed"] = n

    text, n = fix_labeled_block_bare_break(text)
    report["labeled_block_bare_break_sites_fixed"] = n

    text, n = fix_flex_counter_noop_sink(text)
    report["flex_counter_noop_sink_sites_fixed"] = n

    text, n = fix_stdsimd_feature(text)
    report["stdsimd_feature_sites_fixed"] = n

    text, n = fix_missing_semicolon_before_must_check_overflow(text)
    report["missing_semicolon_before_must_check_overflow_sites_fixed"] = n

    text, n = fix_orphaned_compiletime_assert(text)
    report["orphaned_compiletime_assert_sites_fixed"] = n

    text, n = fix_fam_cast_constants(text)
    report["fam_cast_sites_fixed"] = n

    text, n = fix_newtype_wrapper_struct_literals(text)
    report["newtype_wrapper_struct_literal_sites_fixed"] = n

    text, n = fix_zero_len_array_transmute(text)
    report["zero_len_array_transmute_sites_fixed"] = n

    text, n = fix_unused_struct_lifetime(text)
    report["unused_struct_lifetime_sites_fixed"] = n

    text, n = fix_used_on_exit_fn(text)
    report["used_on_exit_fn_sites_fixed"] = n

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
