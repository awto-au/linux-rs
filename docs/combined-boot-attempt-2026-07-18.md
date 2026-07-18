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

## Second candidate: `lib/rcuref.c`

Second real combined-image boot, in its own isolated worktree
(`combined-c2rust-boot-2`, branch `agent-combined-c2rust-boot-2`) so it
could run concurrently with the first file's worktree without
disturbing it. Target: `rcuref_get_slowpath()` /
`rcuref_put_slowpath()` — RCU-protected refcounting, 2
`EXPORT_SYMBOL` sites, 280 lines of C. Same setup pattern: new
`CONFIG_RUST_C2RUST_BOOT_TEST` in `lib/Kconfig` (independent Kconfig
symbol from the first worktree's — different tree/branch, no
collision), `lib/Makefile` swapped `rcuref.o` for `rcuref_rs.o` (had
to be pulled out of a bundled multi-object `obj-y` line rather than
edited in place, since `rcuref.o` shared a line with `once.o`,
`errseq.o`, etc.), raw c2rust output copied from
`tmp/c2rust-baseline/lib_rcuref.c/output/src/rcuref.rs` into
`lib/rcuref_rs.rs` as the starting point.

**Result: boots clean.** 17/17 KUnit suites pass (`fail:0` in every
suite, no `not ok` anywhere), `initramfs init reached, PID 1 alive`
confirms INIT REACHED. `nm` confirms both `rcuref_get_slowpath` and
`rcuref_put_slowpath` as defined (`T`) in both `lib/rcuref_rs.o` and
the final `vmlinux`.

### Fix classes already documented — all recurred as predicted

- `#![feature(asm, extern_types, label_break_value, raw_ref_op,
  strict_provenance)]` — stripped; every `extern "C" { pub type X; }`
  opaque marker converted to the zero-field `#[repr(C)] #[derive(Copy,
  Clone)] struct X { _private: [u8; 0] }` idiom via the same
  `opaque_marker!` macro group_cpus.c's fix introduced.
- ~50 `E0133` "unsafe operation in unsafe fn requires explicit unsafe
  block" errors across every `unsafe extern "C" fn` in the file (not
  just the two exported functions — this file pulls in a much deeper
  chain of KASAN/KCSAN-instrumented atomic helper functions than
  group_cpus.c did, since `atomic_read`/`atomic_set`/
  `atomic_try_cmpxchg_release` and their `raw_atomic_*` /
  `instrument_atomic_*` dependencies all got inlined into the single
  translation unit). Fixed by wrapping each function body in
  `unsafe { ... }`. Given the volume (18 functions), this was done
  with a small one-off Python script that brace-counts to find each
  function's body boundaries (skipping over `"..."` string literals so
  inline-asm template placeholders like `{0}` in `"amocas.w {0}, ...
  "` don't perturb the count) rather than by hand — the string-literal
  skip turned out to matter, since several of these functions contain
  RISC-V inline asm templates that are full of brace-delimited operand
  placeholders.
- The `.init_array`/`__UNIQUE_ID_addressable_*` constructor trick —
  deleted for both exported functions (their `static mut
  __UNIQUE_ID_addressable_*`, the `c2rust_run_static_initializers`
  function, and the `INIT_ARRAY` static), keeping only the real
  `EXPORT_SYMBOL`-emulating `global_asm!` blocks, exactly as
  documented.
- `c2rust_bitfields::BitfieldStruct`-derived structs: two appeared
  this time (`task_struct` and `sched_dl_entity`, both pulled in
  transitively — `rcuref.c`'s only external touchpoint is a
  `*mut task_struct` static, `riscv_current_is_tp`, used purely as an
  opaque pointer). Verified via grep that neither struct's fields —
  bitfield or otherwise — are ever accessed anywhere in the file
  (only ever appear as pointer types: `*mut task_struct`, `*mut
  sched_dl_entity`) before deleting their bodies and adding both names
  to the `opaque_marker!` list alongside the `extern_types` ones. This
  matches group_cpus.c's own fix, which had already folded
  `task_struct`/`sched_dl_entity` into its opaque-marker list for the
  same reason — reassuring that the "verify unused before deleting"
  rule produces consistent answers across files.

### New gap classes found (not in the original doc)

1. **`#[export]` cannot be used on a raw c2rust translation.** c2rust's
   output for this file used the real kernel macro path
   `use ::macros::export;` (wrong crate path — corrected to
   `::kernel::macros::export`) and `#[export]` on both functions. This
   compiled far enough to hit `E0308`: `#[export]` does a
   compile-time signature check against the real bindgen-generated
   `bindings::bindings_raw::rcuref_get_slowpath` declaration (from
   `rust/bindings/bindings_helper.h`), which takes a
   `*mut bindings::bindings_raw::rcuref_t` — a different nominal type
   from this file's own locally-defined, structurally-identical
   `rcuref_t`. `#[export]` is designed for real bindgen-integrated
   Rust code where the local type *is* the bindings type; it's
   fundamentally incompatible with a raw c2rust translation that
   defines its own shadow structs for everything it touches. Fix:
   drop `#[export]` entirely and use `#[no_mangle]` instead (matching
   how group_cpus.c's single exported function was already wired) —
   the real kernel linkage comes from the `global_asm!`
   `EXPORT_SYMBOL`-emulating block c2rust already emits, not from
   `#[export]`.
2. **`kernel::warn_on!` returns `bool`, c2rust assumed C's int-returning
   `WARN_ON()`.** Two call sites compared
   `kernel::warn_on!(cond) != 0`, which fails to typecheck (`E0308`:
   "expected bool, found integer") because the real Rust-for-Linux
   `warn_on!` macro (`rust/kernel/bug.rs`) evaluates and returns `cond`
   directly as a `bool`, semantically already equivalent to C's
   `WARN_ON()`. c2rust's C-to-Rust pass carried over the C idiom of
   testing a macro's return value against `0` without adjusting for
   the target macro's real Rust signature. Fix: drop the `!= 0`,
   compare the macro's `bool` result directly. This is a real
   correctness gap in c2rust's translation of a specific, real kernel
   Rust macro (not a generic C-shape issue) — worth flagging
   separately from the four already-filed corpus-wide gaps, since it's
   specific to files that reference kernel macros like `warn_on!`
   rather than being purely mechanical/structural.
3. **RISC-V `amocas`/Zacas inline-asm addressing syntax:** c2rust
   translated GCC/Clang C inline-asm memory constraints (`"+A" (*p)`,
   a full memory-operand constraint class) into Rust `asm!` with a
   literal `[{N}]` bracket-wrapped register operand — but RISC-V has
   no `[reg]` bracket addressing syntax at all (that's an ARM/x86-ism);
   LLVM's RISC-V assembler rejected all 9 occurrences with "expected
   '(' or optional integer offset". Fixed by rewriting `[{N}]` →
   `0({N})` (RISC-V's real `offset(base)` addressing) throughout —
   confirmed correct against the real C source's use of `%2` GCC
   operand references expanding through an `"+A"` constraint in
   `arch/riscv/include/asm/cmpxchg.h`.
4. **Zacas/Zabha ISA-string extension unavailable to Rust regardless of
   Kconfig — a real, structural Rust-for-Linux RISC-V arch gap, not
   c2rust's fault.** Even after fixing the addressing syntax, LLVM's
   assembler still hard-rejected the `amocas.{b,h,w,d}` instructions:
   "instruction requires the following: 'Zacas'". This worktree's
   `.config` has `CONFIG_RISCV_ISA_ZACAS=y`,
   `CONFIG_TOOLCHAIN_HAS_ZACAS=y`, `CONFIG_RISCV_ISA_ZABHA=y` — so the
   real C build of this same file *does* compile this code path,
   because `arch/riscv/Makefile` appends `_zacas`/`_zabha` to the
   whole-TU `-march=` string for C via
   `riscv-march-$(CONFIG_TOOLCHAIN_HAS_ZACAS) := $(riscv-march-y)_zacas`
   (`KBUILD_AFLAGS`/`KBUILD_CFLAGS`). Rust's equivalent
   (`KBUILD_RUSTFLAGS`) instead hard-codes a fixed
   `--target=riscv64imac-unknown-none-elf -Ctarget-cpu=generic-rv64`
   with no matching `_zacas`/`_zabha` target-feature augmentation
   anywhere in `arch/riscv/Makefile` — so `asm!` blocks using these
   extensions can never compile for Rust in this tree today,
   independent of Kconfig, independent of c2rust. c2rust also
   preserved the C macro's `IS_ENABLED(CONFIG_RISCV_ISA_ZACAS) && ...`
   compile-time guards as *runtime* `riscv_has_extension_unlikely()`
   checks instead of eliminating the dead branch at compile time —
   irrelevant here since `asm!` validity is checked at codegen
   regardless of runtime reachability, but worth noting as another
   instance of c2rust flattening C preprocessor conditionals into
   runtime code where the semantics don't carry over cleanly. Fix
   applied here: dropped the `amocas` fast-path arms entirely (all 4
   width variants: `.b`/`.h`/`.w`/`.d`), falling back unconditionally
   to the LR/SC loop, which needs no ISA extension beyond base `A`.
   This is a real, publishable gap for Rust-for-Linux's RISC-V arch
   support generally (any future hand-written Rust code wanting to use
   Zacas/Zabha would hit the identical wall) — worth raising upstream
   independent of the c2rust corpus-wide issue from the first file.

### Takeaway

Every fix class from `group_cpus.c` recurred here exactly as the
playbook predicted, confirming they're genuinely corpus-wide rather
than one-off. Beyond those, this file surfaced two categories of
genuinely new gap: (a) a `#[export]`/`warn_on!` mismatch specific to
files that reference real kernel Rust macros/attributes rather than
staying purely mechanical, and (b) real RISC-V inline-asm gaps —
one a straightforward c2rust addressing-syntax bug, the other a
legitimate structural hole in this kernel's Rust-for-Linux RISC-V arch
wiring (Zacas/Zabha target-feature support) that has nothing to do
with c2rust and would block hand-written Rust just as much.
