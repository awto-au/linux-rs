# Combined-image boot attempt: raw c2rust output in a real kernel build

First real attempt at wiring a mechanically-passing (c2rust-clean +
rule-conformant) c2rust translation directly into an actual kernel build,
as a proof-of-mechanism for issue #28 (combined-image boot screening).
Never attempted before this session — documenting what was actually
needed, since "clean" and "rule-conformant" turned out not to mean
"compiles in this kernel."

## Candidate chosen

`lib/group_cpus.c` → `group_cpus_evenly()`. Single exported function,
no existing Kconfig gate (built unconditionally via `obj-y`), already
c2rust-clean and rule-conformant (zero violations in
`c2rust_rule_conformance`).

## What was set up

- Isolated worktree: `scripts/linux_riscv_worktree.py create
  combined-c2rust-boot --base linux-rs/phase2-gcd`.
- New Kconfig option `RUST_C2RUST_BOOT_TEST` in `lib/Kconfig`
  (`depends on RUST`, default n), independent of every other `CONFIG_RUST*`
  gate so it can be A/B toggled without disturbing existing landed TUs —
  same pattern as the 8250 Tier C slices' per-slice Kconfig gates.
- `lib/Makefile`: `obj-y += group_cpus_rs.o` under the new config,
  `group_cpus.o` otherwise.
- Copied `tmp/c2rust-baseline/lib_group_cpus.c/output/src/group_cpus.rs`
  to `lib/group_cpus_rs.rs` verbatim as the starting point.

## Real blocker found: raw c2rust output uses features this kernel build hard-blocks

`scripts/Makefile.build`'s `rust_common_cmd` passes
`-Zallow-features=$(rust_allowed_features)` AND
`-Zcrate-attr='feature($(rust_allowed_features))'` for every non-`rust/`
crate compiled — this **overrides** any file-level `#![feature(...)]` the
file itself declares, and the allow-list
(`rust_allowed_features := arbitrary_self_types,asm_goto,generic_arg_infer,used_with_arg`,
line ~320) is a deliberate, narrow, RfL-upstream-driven restriction (see
the comment citing `Rust-for-Linux/linux#2`), not something this
project chose independently.

c2rust's raw output for this file declared
`#![feature(asm, extern_types, raw_ref_op, strict_provenance)]` — **zero
overlap** with the allowed list. This is not specific to
`group_cpus.c`; it's c2rust's standard default codegen shape, so **every**
c2rust-clean file that uses any of these constructs hits the identical
wall. Filed as a real corpus-wide c2rust issue (not yet — see "next
step" below) rather than treated as a one-off.

Additionally hit (once the feature-gate crate attributes were stripped):
141 `E0133` "unsafe operation in unsafe fn requires explicit unsafe
block" errors — c2rust emits raw pointer derefs/unsafe calls directly
inside `unsafe extern "C" fn` bodies without wrapping them, relying on
the old (pre-2024-edition-semantics) implicit-unsafe-in-unsafe-fn
behavior. The kernel's own `--edition=2021` build flag does NOT itself
trigger this (2021 edition should still allow implicit unsafe-in-unsafe)
— under investigation whether a rustc-version-level default changed, or
whether the crate-level feature override chain played a role here too.

## What real fix scope actually looks like

Not a one-line flag change. Two real gaps, corpus-wide:
1. c2rust's default codegen uses features outside this kernel's allowed
   set (`asm`, `extern_types`, `raw_ref_op`, `strict_provenance`) —
   needs either a c2rust-side kernel-idiom rule to avoid emitting them
   (e.g. don't emit `extern_types` opaque markers for unused pulled-in
   header types at all; use a stable pointer-based idiom for the raw_ref
   patterns; avoid `asm!` where a stable alternative exists) or a
   rulesdb-side, per-instance manual rewrite pass before landing.
2. Every raw unsafe operation inside an `unsafe fn` body needs an
   explicit `unsafe {}` wrapper — likely fixable as a mechanical
   post-processing pass over c2rust's output (wrap known
   unsafe-op-shaped statements) rather than a c2rust codegen change,
   since the underlying operations are correctly `unsafe` C-FFI/raw-
   pointer work, just not explicitly scoped.

## Next step

File a real c2rust issue citing this evidence (feature-gate list,
exact E0133/E0658/E0725 counts, the Makefile.build allow-list source)
once this file's boot attempt concludes, so the corpus-wide root cause
is tracked properly rather than left as a one-off worktree note.
