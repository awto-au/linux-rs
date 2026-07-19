# Combined-image boot attempt: raw c2rust output in a real kernel build

First real attempt at wiring a mechanically-passing (c2rust-clean +
rule-conformant) c2rust translation directly into an actual kernel build,
as a proof-of-mechanism for issue #28 (combined-image boot screening).
Never attempted before this session — documenting what was actually
needed, since "clean" and "rule-conformant" turned out not to mean
"compiles in this kernel."

> **Update (2026-07-19): 3 of the recurring gap classes are now fixed
> in the transpiler itself**, not just hand-patched per file — commit
> `8a19ca39c` (merged to `awtoau/c2rust` master as `8bf6855c6`) fixes
> the `extern_types`-for-opaque-decls pattern, the missing `unsafe {}`
> wrapping (a CLI default flip: `TranspilerConfig::deny_unsafe_op_in_unsafe_fn`
> now defaults ON), and the dead `.init_array`/`__ADDRESSABLE` constructor
> trick, plus a stale `feature(asm)` declaration. Verified against a full
> 542-file corpus baseline (542/542 clean, 0 regressions) and against all
> 5 files documented below via `investigate_c2rust_failure.py --rerun`.
> A 6th file attempted from this point forward should need hand-fixing
> only for the 2 gap classes NOT covered by this change: `#[export]`
> needing `#[no_mangle]` instead (file-specific: depends on whether the
> C original used plain `EXPORT_SYMBOL` vs `EXPORT_SYMBOL_GPL`, a
> licensing judgement call, not safely automatable — see
> `rulesdb/rules/0001-export-symbol-gpl.toml`) and `c2rust_bitfields`
> derive stripping/opaquing for pulled-in-but-partially-used structs
> (needs per-struct load-bearing-ness verification, per the
> `is_single_threaded.c`/`lwq.c` sections below).

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

## Second file: lzo1x_decompress_safe

Second boot-screening candidate, same worktree
(`linux-riscv-worktrees/combined-c2rust-boot`), same
`CONFIG_RUST_C2RUST_BOOT_TEST` gate — no new Kconfig option. Chosen
because it's a real decompression algorithm (loops, labeled blocks,
`goto`-derived control flow via c2rust's `c2rust_current_block`
dispatch idiom) rather than a straight-line allocator like
`group_cpus_evenly`, to see whether the three known gap classes still
cover a structurally different function, or whether more control flow
surfaces new ones.

### What was set up

- `lib/lzo/Makefile`: same `ifdef CONFIG_RUST_C2RUST_BOOT_TEST` /
  `else` swap as `lib/Makefile`'s `group_cpus_rs.o` pattern, adapted to
  lzo's `lzo_decompress-objs := ...` indirection (the object list
  variable, not a direct `obj-y +=`) — selects
  `lzo1x_decompress_safe_rs.o` vs `lzo1x_decompress_safe.o`.
- Copied
  `tmp/c2rust-baseline/lib_lzo_lzo1x_decompress_safe.c/output/src/lzo1x_decompress_safe.rs`
  to `lib/lzo/lzo1x_decompress_safe_rs.rs` verbatim as the starting
  point (3866 lines raw, vs. group_cpus.c's much smaller TU).

### The three known gap classes, confirmed present again

All three from the first file recurred here exactly as documented:

1. **Disallowed unstable features.** Same
   `#![feature(asm, extern_types, raw_ref_op, strict_provenance)]`
   declaration, same zero-overlap with `rust_allowed_features`. Fixed
   the same way: stripped the `#![feature(...)]` line, converted the
   68-entry `extern "C" { pub type X; ... }` opaque-type block into the
   stable `opaque_marker!` zero-field-struct macro (duplicated locally
   in this file rather than shared — these are separate crates, no
   shared home exists for it without inventing one, not worth it for a
   ~15-line macro).
2. **Missing explicit `unsafe {}` blocks.** 113 compiler errors on the
   header-fixed-but-not-yet-wrapped file: 111 `E0133` ("unsafe
   operation in unsafe fn requires explicit unsafe block") plus 2
   `E0433` (a separate issue, see below) reported in the same `rustc`
   invocation. Fixed identically to the first file: wrapped each of
   this TU's two `unsafe extern "C" fn` bodies
   (`get_unaligned_le16`, `lzo1x_decompress_safe`) in a single
   top-level `unsafe { ... }` spanning the whole function, mirroring
   `group_cpus_rs.rs`'s fix exactly (that file wraps 10 functions the
   same way). Zero per-statement wrapping needed — one wrap per
   function body is sufficient since the fn signature is already
   `unsafe extern "C"`.
3. **Dead `.init_array`/`__UNIQUE_ID_addressable_*` constructor
   trick.** Present again, same shape:
   `__UNIQUE_ID_addressable_lzo1x_decompress_safe_281`,
   `c2rust_run_static_initializers()`, and the `INIT_ARRAY` static
   with `#[cfg_attr(target_os = "linux", link_section = ".init_array")]`.
   Deleted the same way — dead in a kernel-linker context, and the
   real `.export_symbol`-section `global_asm!` block a few lines above
   it already keeps `lzo1x_decompress_safe` referenced, so nothing is
   lost by removing the constructor trick.

### Unused `BitfieldStruct`-derived structs: same class, harder cascade

Confirmed present again: 6 struct definitions carrying
`#[derive(Copy, Clone, ::c2rust_bitfields::BitfieldStruct)]` —
`task_struct`, `mmap_action`, `kobject`, `kernfs_open_file`,
`signal_struct`, `sched_dl_entity` — pulled in from headers this TU
never actually needs, requiring the unavailable `c2rust_bitfields`
proc-macro crate.

Unlike `group_cpus.c`, deleting these 6 wasn't a clean leaf-node
removal: grepping the function body confirmed all 6 are genuinely
unused *by value* inside `lzo1x_decompress_safe` (zero references), but
two of them — `mmap_action` (inline field `vm_area_desc.action`) and
`kobject` (inline fields `kset.kobj`, `module_kobject.kobj`) — are
still referenced *by value* inside three other structs
(`vm_area_desc`, `kset`, `module_kobject`) that are themselves also
dead (unused by value anywhere, only ever appearing as `*mut
vm_area_desc` / no live references / `*mut module_kobject`
respectively). Deleting `mmap_action`/`kobject` alone without also
opaquing their by-value containers would have left a dangling
undefined-type reference.

Resolved by tracing the by-value dependency chain to its actual roots
(verified via grep at each step, not assumed): the real leaf set
needing opaque-marker treatment is 9 types, not 6 —
`task_struct`, `kobject`, `kernfs_open_file`, `signal_struct`,
`sched_dl_entity`, `kset`, `module`, `module_kobject`, `vm_area_desc`
— plus `mmap_action`, which becomes fully unreferenced (zero uses
anywhere, not even as a pointer) once `vm_area_desc` is opaqued, and
was deleted outright rather than turned into an opaque marker. General
lesson for future files: when a `BitfieldStruct` struct is used
by-value inside another struct, check whether that *container* is
itself dead-by-value too, transitively, before deciding the fix is a
flat leaf-node deletion — the dependency graph is not always one level
deep.

### New gap class #1: c2rust emits a `libc` crate call this build can't resolve

`get_unaligned_le16()` (a small `#[inline]` helper c2rust
synthesizes from the kernel's `get_unaligned_le16()` macro) calls
`::libc::memcpy(...)` and casts a size with `::libc::size_t` in the
raw c2rust output — but nothing in this TU imports or links a `libc`
crate, and the kernel's Rust build has no such crate available at all
(`E0433: cannot find libc in the crate root`, 2 occurrences). This is
inconsistent even within the same file: three lines earlier in the
same raw output, the real `memcpy`/`memset`-style calls the TU
actually needs (e.g. the LZO literal-copy path) are declared properly
via a local `extern "C" { fn memset(...); }` block c2rust itself
emitted — c2rust's codegen just picked a different, unavailable path
for this one helper function's body.

Fixed by adding `memcpy` to the same local `extern "C" { ... }` block
already holding the real `memset` declaration (identical signature
shape, `*mut c_void, *const c_void, size_t -> *mut c_void`), then
replacing the two `::libc::` prefixed call sites
(`::libc::memcpy(...)` → `memcpy(...)`, `::libc::size_t` → `size_t`,
the TU's own local `size_t` typedef alias) with the locally-declared
version. Not encountered in `group_cpus.c`'s TU at all — that file's
raw c2rust output apparently never needed this particular libc call
shape. Likely corpus-wide: any c2rust translation of a function using
a stdlib-shaped intrinsic (memcpy/memmove/strlen-style) is a candidate
for hitting this, worth grepping the full c2rust-baseline corpus for
`::libc::` before landing anything else.

### New gap class #2: dropping `#[export]` silently un-names the exported symbol

Removing c2rust's raw `#[export]` attribute (the same removal done for
`group_cpus_evenly`, since `::macros::export` isn't meaningfully usable
outside a real `#[export_symbol_group]`-wrapped kernel Rust module)
is not sufficient on its own — without it, rustc mangles the function
under the crate's Rust ABI name
(`_RNvCs86KPhn3aWXj_24lzo1x_decompress_safe_rs21lzo1x_decompress_safe`)
instead of emitting the plain C symbol name. This didn't fail the
`.o` compile (the file's own `global_asm!` `.export_symbol` block still
assembles fine referencing the *expected* plain name), but produced an
`.o` where the plain `lzo1x_decompress_safe` symbol is `U`
(undefined) rather than `T` (defined) — a real, but not immediately
obvious, breakage that would only surface as a link error much later
(or worse, a silently different symbol if nothing else referenced it
at link time).

Fixed by adding `#[no_mangle]` directly above the function definition
when `#[export]` is removed — the same thing `group_cpus_rs.rs` already
does for `group_cpus_evenly` (confirmed by inspection: it carries
`#[no_mangle]` immediately above its `pub unsafe extern "C" fn`), which
means this was already the established fix pattern, just not
previously called out explicitly as a distinct step in this doc. Adding
it to the playbook explicitly now: **`#[export]` removal is a two-part
fix, not one** — delete the attribute AND add `#[no_mangle]`, or the
function silently gets Rust name-mangled instead of exported under its
real C name.

### New gap class #3: c2rust re-emits register-variable pseudo-globals as real per-TU statics — duplicate symbols at link time

The `.o` compiled clean and passed every check above, but the **full
kernel link failed** with `ld.lld: error: duplicate symbol:
current_stack_pointer` and `duplicate symbol: riscv_current_is_tp`,
each defined once in `lib/group_cpus_rs.o` and again in
`lib/lzo/lzo1x_decompress_safe_rs.o`.

Root cause: `arch/riscv/include/asm/current.h` declares these as GCC/
Clang **register variables** —
`register unsigned long current_stack_pointer __asm__("sp");` and
`register struct task_struct *riscv_current_is_tp __asm__("tp");` — a
C extension binding an identifier directly to a CPU register, with no
backing memory and no single definition site anywhere in the kernel to
link against. c2rust has no representation for "this identifier is a
register alias, not a variable" in Rust, so for every TU that
transitively pulls in `current.h` (via any header chain, whether or
not the TU's own function body actually touches either symbol) it
emits a fabricated `#[no_mangle] pub static mut current_stack_pointer:
... = 0;` / `riscv_current_is_tp: ... = null` pair. In a single-TU
build this is silently wrong but harmless (unused fake global). The
moment a second TU does the same thing, both TUs' `#[no_mangle]`
statics collide at link time — this is corpus-wide by construction:
**every** c2rust-translated riscv TU that pulls in `asm/current.h`
will emit this exact pair, so a combined image with more than one such
TU linked together will always hit this, not just this specific file
pair.

Confirmed via grep that neither symbol is referenced anywhere in
either TU's actual translated function body (`group_cpus_evenly` or
`lzo1x_decompress_safe`) — both are purely unused pulled-in-header
artifacts, same "confirmed dead via grep, not assumed" standard used
for the `BitfieldStruct` deletions. Fixed by deleting both fabricated
static declarations from `lzo1x_decompress_safe_rs.rs` (group_cpus_rs.rs
was left as-is since it built and linked first; either file could have
been the one fixed, this was just chosen as the smaller diff going
forward). If a third such TU is added later and also needs one of
these deleted from it, same check-then-delete applies.

### Outcome: clean boot, matching the first file's bar

- `make ARCH=riscv LLVM=1 lib/lzo/lzo1x_decompress_safe_rs.o` — clean,
  zero errors, zero warnings from this TU specifically.
- `make ARCH=riscv LLVM=1 -j32` — full kernel build succeeds,
  `arch/riscv/boot/Image` produced.
- `llvm-nm` confirms `lzo1x_decompress_safe` defined (`T`) in both
  `lib/lzo/lzo1x_decompress_safe_rs.o` and the final `vmlinux`
  (`ffffffff801438de T lzo1x_decompress_safe`), alongside
  `group_cpus_evenly` (`ffffffff801590d4 T group_cpus_evenly`) — both
  Rust translations coexisting in the same linked kernel.
- `scripts/boot_qemu.py --run-id combined-c2rust-2`: boots clean,
  **17/17 KUnit suites pass** (identical suite count and pass rate to
  the first file's run), `INIT REACHED`
  (`linux-rs: initramfs init reached, PID 1 alive`), no panics, oops,
  BUG, or WARN in the boot log. No dedicated `lzo1x_decompress_safe`
  KUnit suite exists in this kernel config, so the function's runtime
  correctness wasn't directly exercised by a KUnit test — this run
  demonstrates it links and boots cleanly alongside real kernel code,
  not that its decompression logic was runtime-verified against known
  inputs. Two independently-fixed c2rust translations now coexist in
  one booting combined image, which is the actual point of this
  proof-of-mechanism (issue #28).

### Updated fix-scope tally

With two files done, the fix scope is: the original two corpus-wide
gaps (feature-gate stripping, unsafe-block wrapping) plus now three
more real patterns worth folding into whatever tooling eventually
automates this:
4. Dead `BitfieldStruct`-derived structs may have transitive
   dead-by-value dependents beyond the struct itself — trace the
   whole by-value chain via grep, don't assume leaf-node deletion.
5. c2rust's `libc` crate calls (seen so far: `memcpy`/`size_t`) need
   redirecting to the TU's own locally-declared `extern "C"` block.
6. `#[export]` removal must be paired with adding `#[no_mangle]`, or
   the symbol silently mangles instead of exporting under its real
   name.
7. Register-variable pseudo-globals (`current_stack_pointer`,
   `riscv_current_is_tp`, likely others behind other architectures'
   equivalent `register ... __asm__(...)` header idioms) get
   fabricated as real per-TU `#[no_mangle]` statics by c2rust and
   collide at link time the moment two affected TUs are combined —
   confirm unused via grep, delete.

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

## Third candidate: `lib/is_single_threaded.c`

Third real combined-image boot, in its own isolated worktree
(`combined-c2rust-boot-3`, branch `agent-combined-c2rust-boot-3`), so it
could run concurrently with the other files' worktrees without
disturbing them. Target: `current_is_single_threaded()` — a single,
small (54-line), self-contained function with no `EXPORT_SYMBOL` (it's
a plain internal kernel-wide symbol, declared `extern` in
`include/linux/sched/signal.h` and called from other core files, not
exported to modules). Chosen deliberately as the smallest/simplest
candidate so far, to see whether a much shorter, straight-line function
still hits the same gap classes or needs a lighter touch.

### What was set up

Same pattern as the first two files: new `CONFIG_RUST_C2RUST_BOOT_TEST`
in `lib/Kconfig` (independent Kconfig symbol from every other
worktree's — different tree/branch, no collision), `lib/Makefile`
swapped `is_single_threaded.o` for `is_single_threaded_rs.o` (had to be
pulled out of a bundled multi-object `lib-y` line, same shape as
`rcuref.c`'s fix — shared a line with `plist.o`, `kobject_uevent.o`,
etc.), raw c2rust output copied from
`tmp/c2rust-baseline/lib_is_single_threaded.c/output/src/is_single_threaded.rs`
into `lib/is_single_threaded_rs.rs` as the starting point (1643 lines
raw — most of it pulled-in header/struct noise for a 54-line source
function, the same "small C file, huge single-TU translation" pattern
seen in every file so far).

### No `.init_array`/`EXPORT_SYMBOL` trick this time — confirms the trick is conditional, not universal

Unlike every prior file, this TU's raw c2rust output has **no**
`__UNIQUE_ID_addressable_*`, `c2rust_run_static_initializers`,
`INIT_ARRAY` static, or `global_asm!` `.export_symbol` block at all —
and none was needed, since `current_is_single_threaded` was never
`EXPORT_SYMBOL`'d in the C source in the first place (confirmed via
grep: zero `EXPORT_SYMBOL` in `lib/is_single_threaded.c`). The function
already carried a plain `#[no_mangle]` directly above its
`pub unsafe extern "C" fn` definition in the raw c2rust output, with no
`#[export]`/`::macros::export` involved either. This confirms gap
classes 3 and 6 from the tally below are specifically tied to
`EXPORT_SYMBOL`'d C functions — c2rust only emits the constructor trick
and the `#[export]` attribute when the source function is actually
exported to modules; a plain internal `extern` function like this one
gets a much simpler, already-correct linkage shape for free.

### Fix classes confirmed present

Two of the playbook's classes recurred exactly as documented:

1. **Disallowed unstable features.** Same
   `#![feature(asm, extern_types, raw_ref_op, strict_provenance)]`
   declaration (no `label_break_value` this time, despite the function
   using a labeled block (`'_found: { ... } break '_found;`) — that
   construct is apparently already stable in this compiler, since the
   build log showed zero label-related errors once the feature line was
   stripped). Stripped the line; converted the 30-entry
   `extern "C" { pub type X; ... }` opaque-type block to the stable
   `opaque_marker!` idiom (declared locally in this file, same as every
   prior file — still not worth sharing without inventing a real home
   for it).
2. **Missing explicit `unsafe {}` blocks.** 73 `E0133` errors once the
   header/feature issues were fixed, spread across every
   `unsafe extern "C" fn` in the TU (21 function definitions total: the
   exported function itself plus a chain of inlined
   `atomic_read`/`get_current`/bit-test/preempt-count/RCU helpers, the
   same "small function pulls in a deep KASAN/KCSAN/atomic helper chain"
   shape `rcuref.c` hit). Fixed with the same brace-counting,
   string-literal-skipping Python wrapper script `rcuref.c`'s fix
   introduced (re-derived fresh rather than reused verbatim, since the
   original was a one-off — worth promoting to a real `scripts/` tool
   if a fourth file needs it again), one `unsafe { ... }` per function
   body. Zero errors remained after this pass.

### `BitfieldStruct`-derived structs: genuinely load-bearing this time, not deletable

Three structs carried `#[derive(Copy, Clone, ::c2rust_bitfields::BitfieldStruct)]`:
`task_struct`, `signal_struct`, `sched_dl_entity`. Unlike every prior
file, grepping confirmed these are **not** dead-by-value here — this
function's entire job is walking real `task_struct`/`signal_struct`
fields by name (`.tasks`, `.flags`, `.group_leader`, `.mm`, `.signal`,
`.thread_node`, `.thread_head`) through live pointers, and
`sched_dl_entity` is embedded by value inside `task_struct` purely for
its layout contribution to the `container_of`-style pointer arithmetic
c2rust emits (`__mptr.offset(-(1272 as usize as isize))`). Opaquing any
of the three would have broken real field access or corrupted the
struct's size/offsets that the pointer arithmetic depends on being
correct. The actual problem was narrower than "unused struct, delete
it": only the `::c2rust_bitfields::BitfieldStruct` *derive* is
unavailable (that proc-macro crate isn't linked into this build) — the
underlying bitfield-backed storage fields (declared as raw `[u8; N]`
arrays with `#[bitfield(name = ..., ty = ..., bits = ...)]` attributes
describing how to interpret them) are never read or written through
their would-be-generated named accessor methods anywhere in this TU
(confirmed via grep: zero calls to any bitfield accessor name). Fixed
by stripping the derive down to plain `#[derive(Copy, Clone)]` and
deleting all 25 `#[bitfield(...)]` attribute lines (including
`#[bitfield(padding)]` markers) across the three structs, while leaving
the underlying `[u8; N]` storage fields themselves untouched — this
keeps every struct's real `#[repr(C)]` layout and size intact (the
bytes are still there, just no longer described/accessed field-by-field
through generated accessors this TU doesn't use), which is what field
accesses like `.thread_head` at a fixed real offset actually depend on.

General lesson for future files, extending the one `lzo1x_decompress_safe`
already established: before assuming a `BitfieldStruct` struct is a
deletable leaf, check not just "is it dead-by-value" but "is it
load-bearing for real field access" — if the function actually reads
named fields through it (not just passes it around as an opaque
pointer), the fix is derive-stripping plus attribute-removal, not
deletion or opaquing, because the real field layout still needs to be
correct.

### Outcome: clean boot, third file to clear the bar

- `make ARCH=riscv LLVM=1 lib/is_single_threaded_rs.o` — clean, zero
  errors (865 warnings, all pre-existing missing-doc lints from the
  `#[warn(missing_docs)]` kernel crate attribute, nothing new).
- `make ARCH=riscv LLVM=1 -j32` — full kernel build succeeds,
  `arch/riscv/boot/Image` and `Image.xz` produced.
- `llvm-nm` confirms `current_is_single_threaded` defined (`T`) in both
  `lib/is_single_threaded_rs.o` and the final `vmlinux`
  (`ffffffff801a233e T current_is_single_threaded`).
- `scripts/boot_qemu.py --run-id combined-c2rust-4`: boots clean,
  **17/17 KUnit suites pass** (`fail:0` in every suite, no `not ok`
  anywhere), `initramfs init reached, PID 1 alive` confirms INIT
  REACHED, no panics, oops, BUG, or WARN in the boot log. No dedicated
  `is_single_threaded`/`current_is_single_threaded` KUnit suite exists
  in this kernel config (same situation as `lzo1x_decompress_safe`), so
  this run demonstrates linking and booting cleanly, not a runtime
  correctness check of the single-threaded-detection logic itself
  against known inputs.

This is the smallest and simplest file of the three done so far, and it
needed the *least* manual intervention: no `#[export]`/`warn_on!`
mismatch, no RISC-V inline-asm rewriting, no `.init_array` trick to
strip, no `libc::` redirection, no transitive dead-struct-chain tracing
— just feature-gate stripping, unsafe-block wrapping, and (newly) a
"derive-strip, don't delete" resolution for load-bearing `BitfieldStruct`
structs. Confirms the fix classes scale down cleanly to small files, not
just up to large ones, and that not every gap class fires on every
file — which file surfaces which subset seems to depend mostly on
whether the function is `EXPORT_SYMBOL`'d (drives the `.init_array`/
`#[export]` classes) and whether its `BitfieldStruct`-derived structs
are actually touched by value/by-field versus merely pulled in as dead
header noise.

### Updated fix-scope tally

With three files done, tally unchanged in kind from the two-file tally
above (no genuinely new corpus-wide gap class found in this file) —
this file's only contribution is the refinement to lesson 4:
4. (refined) Dead `BitfieldStruct`-derived structs may be
   transitively dead-by-value (trace the chain, as `lzo1x_decompress_safe`
   showed) **or** genuinely load-bearing for real field access (as
   `is_single_threaded.c` showed) — in the load-bearing case, strip the
   derive and the `#[bitfield(...)]` attributes rather than deleting or
   opaquing the struct, keeping the underlying storage fields' bytes
   and layout intact.

## Fourth candidate: `lib/lwq.c`

Fourth real combined-image boot, in its own isolated worktree
(`combined-c2rust-boot-4`, branch `agent-combined-c2rust-boot-4`), so it
could run concurrently with the other files' worktrees without
disturbing them. Target: the lock-free-ish work queue in
`lib/lwq.c` — `__lwq_dequeue()` and `lwq_dequeue_all()`, both
`EXPORT_SYMBOL_GPL`, 158 lines of C (excluding the `CONFIG_LWQ_TEST`
boot-time self-test block, which the c2rust baseline doesn't include
since it's conditionally compiled out). Chosen for being structurally
similar in risk profile to `lib/rcuref.c` — real atomic-operation-heavy
code pulling in the same `asm/current.h`/`cmpxchg.h` header chain — to
see whether the RISC-V inline-asm gaps `rcuref.c` hit (issue #29, the
`amocas`/Zacas one specifically) recur for a *different* RISC-V atomic
primitive (`llist_del_all()`'s internal `xchg()` on `struct
llist_head.first`, which lowers to `amoswap` rather than rcuref's
`amocas`).

### What was set up

Same pattern as every prior file: new `CONFIG_RUST_C2RUST_BOOT_TEST` in
`lib/Kconfig` (independent Kconfig symbol from every other worktree's —
different tree/branch, no collision), `lib/Makefile` swapped `lwq.o`
for `lwq_rs.o` (had to be pulled out of a bundled multi-object `obj-y`
line shared with `bsearch.o`, `kfifo.o`, `rcuref.o`, `errseq.o`, etc. —
same shape as `rcuref.c`'s and `is_single_threaded.c`'s own Makefile
fixes), raw c2rust output copied from
`tmp/c2rust-baseline/lib_lwq.c/output/src/lwq.rs` into `lib/lwq_rs.rs`
as the starting point (1474 lines raw for a 158-line source file, the
same "small C file, huge single-TU translation" pattern seen in every
file so far — most of the bulk is a fully-inlined `task_struct` and its
~50 transitively-pulled-in struct dependents, none of which
`__lwq_dequeue`/`lwq_dequeue_all` actually touch by value).

### Known fix classes, confirmed present again

- `#![feature(asm, extern_types, label_break_value, raw_ref_op,
  strict_provenance)]` — stripped.
- `use ::macros::export;` / `#[export]` on both exported functions —
  dropped `#[export]`, added `#[no_mangle]` in its place (the
  `rcuref.c`-established fix: `#[export]` needs a real bindgen-backed
  type match this raw translation doesn't have; the real linkage comes
  from the `global_asm!` `.export_symbol` block c2rust already emits).
- The `.init_array`/`__UNIQUE_ID_addressable_*` constructor trick —
  present for both exported functions, deleted, keeping the
  `EXPORT_SYMBOL`-emulating `global_asm!` blocks.
- `unsafe {}` block wrapping — needed across all 19 `unsafe extern "C"
  fn` bodies in the file (not just the two exported ones), the same
  KASAN/KCSAN-instrumented-atomic-helper-chain shape `rcuref.c` hit
  (`kasan_check_write`, `kcsan_check_access`,
  `instrument_atomic_read_write`, the `preempt_count`/`tif_need_resched`
  chain feeding `spin_lock`/`spin_unlock`, plus `llist_empty`/
  `llist_next`/`llist_del_all` themselves). Used the same brace-counting
  Python script approach the `rcuref.c` entry describes (skip string
  literals and `'_c2rust_label_N:` block-label syntax so they don't
  perturb the brace count), rather than hand-wrapping 19 functions.
- Register-variable pseudo-globals (`current.h`'s `current_stack_pointer`
  and `riscv_current_is_tp`) — both fabricated by c2rust as
  `#[no_mangle] pub static mut` globals as usual. `riscv_current_is_tp`
  is genuinely used here (via `get_current()`, feeding the
  `preempt_count_ptr()` chain `spin_lock`/`spin_unlock` call — this
  file's *only* real touchpoint into `task_struct`/`thread_info`),
  so it was kept. `current_stack_pointer` was confirmed dead by grep
  (only its own declaration, never read or written) and deleted per
  the established rule — no collision materialized in this worktree
  since `lwq_rs.rs` is the only raw-c2rust Rust TU built here (`rcuref.c`
  stays plain C in this worktree), but deleting it removes the latent
  risk for if a second such TU is ever added.
- Dead `BitfieldStruct`-derived structs: `task_struct` and
  `sched_dl_entity`, same pair `rcuref.c` hit, confirmed unused by
  value anywhere in the file (only ever `*mut task_struct` / inline
  `sched_dl_entity` field inside the now-opaqued `task_struct`) and
  opaqued via the same `opaque_marker!` macro.

### Refinement: tracing which structs are real vs. dead is not a clean single-boundary split here

Unlike `lzo1x_decompress_safe.c` (whole dead chain deletable as one
contiguous block after tracing) or `is_single_threaded.c` (nothing dead
to trace), this file's ~700-line dead `task_struct`-chain block had
three genuinely-needed structs interspersed *inside* it:
`raw_spinlock`/`arch_spinlock_t` and `spinlock`/its anonymous union
(both needed for `struct lwq.lock: spinlock_t`, used by the real
`spin_lock`/`spin_unlock` calls), `llist_node`/`llist_head` (the
queue's actual payload types), plus — found only once the build
started erroring on it — `thread_info`, which isn't dead despite being
part of the same header pull-in: `preempt_count_ptr()` casts
`get_current()`'s `*mut task_struct` result to `*mut thread_info` and
dereferences `.preempt_count`/`.flags` through it, a real (if
indirect) load-bearing use. General lesson: when the dead chain is
large enough to be worth deleting as a block rather than one struct at
a time, grep *each* interspersed struct individually before deleting
the block — "mostly dead" is not "entirely dead," and the load-bearing
survivor may not be the struct the exported functions reference
directly (here it's two casts away: `task_struct` → `thread_info`, not
`task_struct` used directly).

### RISC-V inline-asm gaps: both `rcuref.c` findings recurred exactly as issue #29 predicted

`llist_del_all()`'s c2rust-translated `xchg(&head->first, NULL)` lowers
to a 4-way `match` on pointer width (1/2/4/8 bytes — dead code for the
1/2 cases on a 64-bit pointer target, but c2rust translates the full
generic `xchg()` macro regardless) with two distinct RISC-V gaps:

1. **Zabha ISA-string extension unavailable to Rust regardless of
   Kconfig** — exactly the `KBUILD_RUSTFLAGS` gap filed as issue #29
   from the `rcuref.c` boot. The size-1 and size-2 match arms each had
   an `if riscv_has_extension_unlikely(RISCV_ISA_EXT_ZABHA) { asm!("
   amoswap.b.aqrl ...") } else { <LR/SC loop> }` runtime-guarded fast
   path — `amoswap.b`/`amoswap.h` (byte/halfword atomic swap) are
   Zabha-gated instructions, same wall as `rcuref.c`'s `amocas.b`/`.h`:
   LLVM's RISC-V assembler rejects them for the Rust target string
   regardless of the runtime guard, since `asm!` validity is checked at
   codegen time independent of reachability. Fixed identically to
   `rcuref.c`: dropped both `amoswap.b`/`amoswap.h` fast-path arms
   entirely (the `if` condition, the `asm!` block, and the `} else {`
   line), leaving the LR/SC fallback path unconditional — it needs
   nothing beyond base `A`. The size-4 and size-8 arms
   (`amoswap.w`/`amoswap.d`) needed no such fix: whole-word/doubleword
   atomic swap is base-ISA `A`, not Zabha-gated, and both compiled
   as-is. Confirms issue #29's finding is not specific to `amocas` —
   it's any Zabha/Zacas-gated `amo*.{b,h}` instruction c2rust emits a
   runtime-guarded fast path for, which any future c2rust-translated
   file touching sub-word atomics on this arch/config combination will
   hit again until issue #29 is fixed upstream in `KBUILD_RUSTFLAGS`.
2. **`[reg]` bracket addressing syntax** — the same c2rust addressing
   bug `rcuref.c` hit, this time in the size-4/size-8 arms' `amoswap.w`/
   `amoswap.d` asm templates: `"amoswap.w.aqrl {0}, {2}, [{1}]"` uses
   `[{1}]` bracket-wrapped addressing, which RISC-V's assembler doesn't
   support (an ARM/x86-ism, not a RISC-V one). LLVM rejected both with
   "expected '(' or optional integer offset". Fixed identically to
   `rcuref.c`: `[{N}]` → `0({N})`, confirmed against the real C source's
   `"amoswap%0 %0, %2, %1\n"` `"+A"`-constrained memory operand in
   `arch/riscv/include/asm/cmpxchg.h`. Interestingly the size-1/size-2
   arms' `amoswap.b`/`.h` asm also used the same `[{N}]` syntax, but
   since those arms were deleted outright for the Zabha gap above, that
   instance never needed a separate addressing fix — a reminder that
   gap classes can compound on the same code without both needing
   independent fixes if one fix subsumes the site entirely.

### Outcome: clean boot, fourth file to clear the bar

- `make ARCH=riscv LLVM=1 lib/lwq_rs.o` — clean, zero errors (cosmetic
  warnings only: missing-doc lints, a handful of unused-variable
  warnings in the KASAN/KCSAN no-op stub bodies, two "unused borrow
  that must be used" warnings on `spin_lock`/`spin_unlock`'s
  c2rust-translated no-op field-address expressions — none new
  relative to the pattern already seen in `rcuref.c`).
- `make ARCH=riscv LLVM=1 -j32` — full kernel build succeeds,
  `arch/riscv/boot/Image` and `Image.xz` produced.
- `llvm-nm` confirms both `__lwq_dequeue` and `lwq_dequeue_all` defined
  (`T`) in both `lib/lwq_rs.o` and the final `vmlinux`
  (`ffffffff8013450a T __lwq_dequeue`,
  `ffffffff801345c8 T lwq_dequeue_all`), and confirms `lib/lwq.o` was
  correctly never built (the Kconfig/Makefile swap worked as intended).
- `scripts/boot_qemu.py --run-id combined-c2rust-5`: boots clean,
  **17/17 KUnit suites pass** (`fail:0` in every suite, no `not ok`
  anywhere), `initramfs init reached, PID 1 alive` confirms INIT
  REACHED, no panics, oops, BUG, or WARN in the boot log. No dedicated
  `lwq`/`llist` KUnit suite exists in this kernel config (`CONFIG_
  LWQ_TEST` is a boot-time smoke test gated separately and wasn't
  enabled here), so this run demonstrates linking and booting cleanly
  alongside real kernel code, not a runtime correctness check of the
  queue logic itself against known concurrent-access patterns.

No genuinely new corpus-wide gap class found in this file — both
RISC-V inline-asm gaps are confirmations of issue #29 and the `rcuref.c`
addressing-syntax fix applying to a second, different atomic primitive
(`amoswap` vs. `amocas`), which strengthens the case that issue #29 is
a real, general `KBUILD_RUSTFLAGS` gap rather than an
`amocas`-instruction-specific one. The one refinement worth carrying
forward is the "interspersed needed structs inside a dead block, trace
each one individually" lesson above, extending lesson 4's family of
"don't assume a whole dead-looking region is uniformly dead" findings
to the *deletion-of-a-large-contiguous-block* case, not just the
individual-struct case `lzo1x_decompress_safe.c` and
`is_single_threaded.c` already covered.

## Fifth candidate: `lib/timerqueue.c`

Worktree `combined-c2rust-boot-5`, branch `agent-combined-c2rust-boot-5`.
Target: `timerqueue_add`, `timerqueue_del`, `timerqueue_iterate_next`,
`timerqueue_linked_add`, all `EXPORT_SYMBOL_GPL`, 98 lines of C.
c2rust binary at `8bf6855c6` (post-fix). Fresh transpile confirmed via
`investigate_c2rust_failure.py --rerun`: `outcome=clean`,
`c2rust_rev=8bf6855c6`, byte-identical to the pre-existing
`tmp/c2rust-baseline/lib_timerqueue.c/` output.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `timerqueue.o` pulled out of the
bundled `lib-y` line, swapped for `timerqueue_rs.o` under the config.
Raw transpile copied to `lib/timerqueue_rs.rs` (1226 lines).

Of the 3 gap classes fixed upstream in `8bf6855c6`: confirmed absent —
no `extern_types`/`asm` in the feature line, `unsafe {}` already
wrapping every function body, no `.init_array`/
`__UNIQUE_ID_addressable_*` constructor trick anywhere in the output.

Fixes actually needed, both from the 2 classes the fix didn't cover:
- `#[export]` → `#[no_mangle]` on all 4 functions (all 4 source sites
  are `EXPORT_SYMBOL_GPL`, no mixed-licensing judgement call this
  file), `use ::macros::export;` import dropped.
- `c2rust_bitfields::BitfieldStruct` derive on `task_struct` and
  `sched_dl_entity`: both dead-by-value (grep-confirmed, zero field
  access anywhere in the TU — `sched_dl_entity` only appears by-value
  once, embedded inside `task_struct` itself, which is also fully
  dead). Opaqued both via the standard `opaque_marker!` macro rather
  than derive-stripped, since neither is load-bearing here.

One item not called out in the 3-class fix: `#![feature(raw_ref_op,
strict_provenance)]` was still present in the fresh output and still
disallowed (`rust_allowed_features` in `scripts/Makefile.build` is
`arbitrary_self_types,asm_goto,generic_arg_infer,used_with_arg`, no
overlap) — `E0725` until the line was stripped. Not one of the 3 fixed
classes (those covered `asm`/`extern_types`); `raw_ref_op` and
`strict_provenance` are a separate leftover in c2rust's default feature
declaration that the fix didn't touch. Stripped the same way as prior
files.

Build: `make ARCH=riscv LLVM=1 lib/timerqueue_rs.o` clean, 0 errors
(381 warnings, all missing-doc lints, pre-existing pattern). `make
ARCH=riscv LLVM=1 -j32` succeeds, `arch/riscv/boot/Image` and
`Image.xz` produced. `lib/timerqueue.o` correctly never built.

`llvm-nm`: all 4 symbols `T` in both `lib/timerqueue_rs.o` and
`vmlinux` (`ffffffff801b13e4 T timerqueue_add`, `ffffffff801b1454 T
timerqueue_del`, `ffffffff801b149a T timerqueue_iterate_next`,
`ffffffff801b14ba T timerqueue_linked_add`).

`scripts/boot_qemu.py --run-id combined-c2rust-6`: boots clean, 17/17
KUnit suites pass (`fail:0` every suite, 0 `not ok`), `initramfs init
reached, PID 1 alive`, no panic/oops/BUG/WARN in the log. No dedicated
timerqueue KUnit suite in this config.

Confirms the update note's prediction: this file needed hand-fixing
only for the 2 gap classes the transpiler fix didn't cover
(`#[export]`, `BitfieldStruct` derive), plus one previously-undocumented
leftover-feature-line strip. Zero instances of the 3 now-fixed classes.

## Sixth candidate: `lib/objpool.c`

Worktree `combined-c2rust-boot-6`, branch `agent-combined-c2rust-boot-6`.
Target: `objpool_init`, `objpool_free`, `objpool_drop`, `objpool_fini`,
all `EXPORT_SYMBOL_GPL`, 203 lines of C, lock-free per-CPU object pool
using `cmpxchg()`. c2rust binary at `8bf6855c6` (post-fix). Fresh
transpile via `investigate_c2rust_failure.py --rerun`: `outcome=clean`,
`c2rust_rev=8bf6855c6`.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `objpool.o` pulled out of the bundled
`lib-y` line, swapped for `objpool_rs.o` under the config. Raw transpile
copied to `lib/objpool_rs.rs` (2781 lines).

Of the 3 gap classes fixed upstream in `8bf6855c6`: confirmed absent —
no `extern_types`/`asm` in the feature line, `unsafe {}` already
wrapping every function body, no `.init_array`/
`__UNIQUE_ID_addressable_*` constructor trick anywhere in the output.

Fixes needed, both from the 2 classes the fix didn't cover:
- `#[export]` → `#[no_mangle]` on all 4 functions (all 4 source sites
  are `EXPORT_SYMBOL_GPL`), `use ::macros::export;` import dropped.
- `c2rust_bitfields::BitfieldStruct` derive on `task_struct` and
  `sched_dl_entity`: both dead-by-value (grep-confirmed zero field
  access anywhere in the TU; `riscv_current_is_tp`, the only pointer
  touchpoint into `task_struct`, is itself unreferenced after
  declaration). Opaqued both via the standard `opaque_marker!` idiom.

Same leftover-feature-line item `timerqueue.c` hit:
`#![feature(label_break_value, raw_ref_op, strict_provenance)]` present
and disallowed (zero overlap with `rust_allowed_features`); stripped.
Differs from `timerqueue.c`'s leftover set by also carrying
`label_break_value` — confirms the leftover set is per-file, not fixed.

Register-variable pseudo-globals (`current.h`): both
`riscv_current_is_tp` and `current_stack_pointer` fabricated as usual;
both confirmed dead by grep (declaration only, never read/written) and
deleted.

RISC-V inline-asm: this file's `cmpxchg()` (4-arm match, sizes 1/2/4/8
bytes) hit both issue #29 findings again, a third confirmation after
`rcuref.c` (`amocas`) and `lwq.c` (`amoswap`):
- `[reg]` bracket addressing in 13 occurrences across `amocas.{b,h,w,d}`
  and their LR/SC fallback asm templates. Fixed: `[{N}]`/`[{N:}]` →
  `0({N})`/`0({N:})` throughout.
- Zacas/Zabha ISA-string gap (`KBUILD_RUSTFLAGS` has no
  `_zacas`/`_zabha` target-feature augmentation for Rust): all 4
  `amocas.{b,h,w,d}` fast-path arms rejected by LLVM regardless of the
  `riscv_has_extension_unlikely()` runtime guard. Fixed: dropped all 4
  `if <Zabha/Zacas guard> { amocas asm } else { <LR/SC fallback> }`
  wrappers, keeping the LR/SC fallback unconditional in all 4 arms
  (sizes 1/2/4/8, not just the sub-word ones `lwq.c` needed this for —
  this file's `cmpxchg()` macro unrolls all 4 widths in one match, and
  even the word/doubleword arms use plain Zacas, not just Zabha).

Build: `make ARCH=riscv LLVM=1 lib/objpool_rs.o` clean, 0 errors (661
warnings, all missing-doc lints). `make ARCH=riscv LLVM=1 -j32`
succeeds, `arch/riscv/boot/Image` and `Image.xz` produced.
`lib/objpool.o` correctly never built.

`llvm-nm`: all 4 symbols `T` in both `lib/objpool_rs.o` and `vmlinux`
(`ffffffff801abf0e T objpool_init`, `ffffffff801abed8 T objpool_free`,
`ffffffff801abd5a T objpool_drop`, `ffffffff801abda8 T objpool_fini`).

`scripts/boot_qemu.py --run-id combined-c2rust-7`: boots clean, 17/17
KUnit suites pass (`fail:0` every suite, 0 `not ok`), `initramfs init
reached, PID 1 alive`, no panic/oops/BUG/WARN in the log. No dedicated
objpool KUnit suite in this config.

Sixth file to clear the bar. Confirms the update note's prediction
again: hand-fixing needed only for the 2 gap classes the transpiler fix
didn't cover, plus the same leftover-feature-line strip `timerqueue.c`
needed. Both issue #29 findings (bracket addressing, Zacas/Zabha)
recurred on a third distinct atomic primitive (`cmpxchg` vs. `rcuref.c`'s
bare CAS and `lwq.c`'s `xchg`), and for the first time on all 4 width
arms in a single match rather than just the sub-word ones — strengthens
issue #29's scope to "any Zacas/Zabha-gated `amo*`/`amocas*`
instruction c2rust emits a runtime-guarded fast path for, any width."

## Seventh candidate: `lib/klist.c`

Worktree `combined-c2rust-boot-7`, branch `agent-combined-c2rust-boot-7`,
based on `linux-rs/phase2-gcd`. Target: all 13 `EXPORT_SYMBOL_GPL`
functions (`klist_init`, `klist_add_head`, `klist_add_tail`,
`klist_add_behind`, `klist_add_before`, `klist_del`, `klist_remove`,
`klist_node_attached`, `klist_iter_init_node`, `klist_iter_init`,
`klist_iter_exit`, `klist_prev`, `klist_next`), 419 lines of C. c2rust
binary at `6065eaf19` (post the `extern_types`/`asm`/`raw_ref_op`/
`strict_provenance`/unsafe-wrapping/`.init_array` fix series). Baseline
transpile at `tmp/c2rust-baseline/lib_klist.c/output/src/klist.rs`
(2561 lines) used as-is, no re-transpile needed.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `klist.o` pulled out of the bundled
`lib-y` line, swapped for `klist_rs.o` under the config.

Of the 3 gap classes fixed upstream in the c2rust fix series: confirmed
absent — no `extern_types`/`asm` in the feature line (only a leftover
`#![feature(label_break_value)]`, same leftover-feature-line class
`timerqueue.c`/`objpool.c` hit, stripped the same way), `unsafe {}`
already wrapping every function body, no `.init_array`/
`__UNIQUE_ID_addressable_*` constructor trick anywhere in the output.

Fixes from the 2 classes the fix series doesn't cover:
- `#[export]` -> `#[no_mangle]` on all 13 functions (all 13 source
  sites are `EXPORT_SYMBOL_GPL`, no mixed-licensing judgement call),
  `use ::macros::export;` import dropped.
- `c2rust_bitfields::BitfieldStruct` derive on `task_struct` and
  `sched_dl_entity`: `sched_dl_entity` is dead-by-value (grep-confirmed,
  only referenced via the `dl_server_pick_f` function-pointer type,
  never instantiated) and opaqued via the standard `opaque_marker!`
  idiom. `task_struct` is **not** dead here — `klist_remove`'s wait
  loop writes `(*get_current()).__state` directly (the C
  `__set_current_state()`/`set_current_state()` macro expansion),
  the only field of this struct genuinely accessed anywhere in the TU.
  Restored the full field layout instead of opaquing, and instead
  surgically stripped only the derive plus the two `#[bitfield(...)]`-
  packed field groups (grep-confirmed neither packed group is read or
  written anywhere in the TU), replacing them with equivalent-size
  plain byte arrays so every other field keeps its original offset.
  First file in this series where the struct carrying the derive is
  genuinely partially load-bearing rather than a clean opaque-or-don't
  binary choice.

Issue #29 (KBUILD_RUSTFLAGS missing riscv-march-y's Zacas/Zabha) hit
again, a fourth confirmation after `rcuref.c` (`amocas`), `lwq.c`
(`amoswap`), `objpool.c` (`cmpxchg`, all 4 widths): this file's
`refcount_dec_and_test` -> `try_cmpxchg` -> `cmpxchg` chain (used by
`kref_put`/`klist_release`) hit both issue #29 findings:
- `[reg]` bracket addressing in 14 occurrences across `amoadd.w` and
  `amocas.{b,h,w,d}` plus their LR/SC fallback templates. Fixed:
  `[{N}]`/`[{N:}]` -> `0({N})`/`0({N:})` throughout.
- Zacas/Zabha ISA-string gap: all 4 `amocas.{b,h,w,d}` fast-path arms
  rejected by LLVM regardless of the `riscv_has_extension_unlikely()`
  runtime guard. Fixed: dropped all 4 `if <guard> { amocas asm } else
  { <LR/SC fallback> }` wrappers, keeping the LR/SC fallback
  unconditional in all 4 arms, same fix as `objpool.c`.

### New gap class: `LIST_POISON1`/`LIST_POISON2` fail Rust const-eval

`lib/klist_rs.o` initially failed with `E0080` ("in-bounds pointer
arithmetic failed ... dangling pointer, it has no provenance") on both
`LIST_POISON1`/`LIST_POISON2`, c2rust's direct translation of
`include/linux/poison.h`'s `((void *) 0x100 + POISON_POINTER_DELTA)`
idiom as a Rust `const` item using `.offset()` on a pointer fabricated
from a bare integer cast. C allows this trivially; Rust's const
evaluator rejects pointer arithmetic on a no-provenance pointer even
though both addresses are fixed non-null sentinels the kernel never
dereferences (`list_del()` only ever compares against them). Not seen
in any prior file in this series — first file whose C source actually
uses `LIST_POISON1`/`LIST_POISON2` (`list_del()`, called by
`klist_release`/`klist_del`, both exported). Fixed by rewriting both as
plain `u64` integer arithmetic + `as` cast (`0x100u64.wrapping_add(
POISON_POINTER_DELTA) as *mut c_void`), bit-identical to the C value,
no pointer-provenance question involved either way.

### New gap class: parameter name shadows a same-named tuple struct

`E0530` ("function parameters cannot shadow tuple structs") on the
static helper `knode_set_klist(struct klist_node *knode, struct klist
*klist)`: c2rust translated the C parameter name `klist` literally (C
keeps the `struct klist` type tag and the `klist` identifier in
separate namespaces), but c2rust's own generated Rust type for `struct
klist` is also named `klist` (a tuple struct, `pub struct
klist(pub C2Rust_klist_Inner)`), and Rust has one flat namespace for
both — the parameter shadows the type. Fixed by renaming the parameter
only (`klist` -> `klist_ptr`); the type and both call sites are
unaffected (both are positional). Not a fix a rulesdb-side
`#[no_mangle]`/`opaque_marker!`-style mechanical pass would catch —
this is a naming collision, not a feature/unsafe/dead-code gap, so it's
worth calling out as its own class going forward, distinct from the
other 5 known ones.

### Real boot-blocking bug: fabricated register-variable global is genuinely live here, not dead

Build succeeded clean, but the kernel hung after the OpenSBI banner —
no `Linux version` line, no KUnit output, no init. `-d guest_errors,
unimp,int` tracing pinpointed a `load_page_fault` at address `0x8`
with `epc` inside `klist_add_tail`, tracing (via `llvm-nm
--numeric-sort` + disassembly of the faulting `auipc`/`ld`/`lw`
sequence) to a dereference of the fabricated
`riscv_current_is_tp` static at offset 8 (`task_struct.__state`,
immediately after `thread_info`).

Root cause: unlike `lzo1x_decompress_safe.c`/`objpool.c` (both fully
dead, grep-confirmed, safe to delete), this file's `get_current()` —
which just returns the fabricated `riscv_current_is_tp` static
verbatim — **is** genuinely called, via `spin_unlock()`'s inlined
preemption-check path (`__preempt_count_dec_and_test` ->
`tif_need_resched` -> `get_current()`), which is reached on every
`klist_add_head`/`klist_add_tail` call — real device-registration
traffic (`device_add()` in `drivers/base/core.c`) during boot. Since
nothing ever assigns the fabricated static, `get_current()` always
returned a null `*mut task_struct`, and the preemption check
dereferenced it. The existing "confirmed dead via grep, delete" fix
from prior files is unsound in general — it happened to be correct for
`lzo1x_decompress_safe.c`/`objpool.c` only because grep confirmed those
specific call sites were unreachable, not because the fabricated-static
pattern is inherently safe to delete.

Fixed by rewriting `get_current()` to read the real `tp` register
directly via `asm!("mv {0}, tp", out(reg) tp, ...)`, mirroring what
`arch/riscv/include/asm/current.h`'s `register struct task_struct
*riscv_current_is_tp __asm__("tp")` extension actually compiles to,
instead of reading a fake `.bss` global back. `current_stack_pointer`
(the `sp`-bound half of the same register-variable pair) remained
grep-confirmed dead in this TU and was deleted outright, not given the
same treatment. General lesson for future files: **before deleting a
fabricated register-variable static, grep for calls to `get_current()`
specifically** (not just direct references to the static's name), since
the static is only ever accessed indirectly through that accessor —
a textual grep for the static's own identifier alone is not sufficient
to prove it's dead.

### Outcome: clean boot after the `get_current()` fix

- `make ARCH=riscv LLVM=1 lib/klist_rs.o` — clean, 0 errors (606
  warnings, all missing-doc/unused-var lints, pre-existing pattern).
- `make ARCH=riscv LLVM=1 -j32` (`dev.py build`) succeeds,
  `arch/riscv/boot/Image` and `Image.xz` produced. `lib/klist.o`
  correctly never built.
- `llvm-nm`: all 13 symbols `T` in both `lib/klist_rs.o` and `vmlinux`
  (`ffffffff801a25aa T klist_add_before`, `ffffffff801a2658 T
  klist_add_behind`, `ffffffff801a2706 T klist_add_head`,
  `ffffffff801a27ae T klist_add_tail`, `ffffffff801a2856 T klist_del`,
  `ffffffff801a286e T klist_init`, `ffffffff801a2886 T
  klist_iter_exit`, `ffffffff801a28ae T klist_iter_init`,
  `ffffffff801a28c4 T klist_iter_init_node`, `ffffffff801a2954 T
  klist_next`, `ffffffff801a2a70 T klist_node_attached`,
  `ffffffff801a2a86 T klist_prev`, `ffffffff801a2ba2 T klist_remove`).
- `scripts/boot_qemu.py --run-id combined-c2rust-8`: boots clean,
  17/17 KUnit suites pass (`fail:0` every suite, 0 `not ok`),
  `initramfs init reached, PID 1 alive`, no panic/oops/BUG/WARN in the
  log. Archived at
  `docs/status/boot-logs/20260719T124934+1000-combined-c2rust-8.log`.
  No dedicated klist KUnit suite in this config.

Seventh file to clear the bar, and the first to hit a genuine
boot-time (not just build-time) bug from the transpiler's output —
every prior file's register-variable statics were dead code; this one
proves the "grep for the identifier, delete if unused" heuristic is
not sufficient in general and the accessor function must be checked
too. Also the first file needing a real `LIST_POISON`-style const-eval
fix and the first hitting a parameter/type namespace collision. Issue
#29 (bracket addressing + Zacas/Zabha) recurred a fourth time,
unchanged in shape from `objpool.c`.
