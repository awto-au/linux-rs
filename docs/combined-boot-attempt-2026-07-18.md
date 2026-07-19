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

### Retroactive fix: latent null-deref in `get_current()`, same bug class as klist.c (issue #31)

The "boots clean" outcome above was true but incomplete: nothing in
this project's boot path calls `current_is_single_threaded()`, so the
function's guaranteed null-pointer bug was never exercised. Found by
cross-checking klist.c's fix (issue #30) against the other landed
combined-boot files: `get_current()` at
`lib/is_single_threaded_rs.rs:1395` returned the fabricated
`riscv_current_is_tp` `.bss` static verbatim (always null, nothing ever
assigns it), and `current_is_single_threaded()`
(`#[no_mangle]`, line 1533) dereferences its result
(`(*task).mm`) as its first real statement — a guaranteed
null-pointer deref the moment anything calls this function (real
callers in upstream Linux: `fs/exec.c`'s `de_thread()`, a handful of
`/proc` paths — none reached by this project's current minimal boot).

Fixed identically to klist.c: `get_current()` rewritten to read the
real `tp` register directly via
`asm!("mv {0}, tp", out(reg) tp, options(nomem, nostack,
preserves_flags))`, and both fabricated statics
(`riscv_current_is_tp` and the unread `current_stack_pointer`,
grep-confirmed zero references anywhere else in the TU) deleted
outright rather than left as unread dead code.

**Verification (disassembly, not boot-only — boot alone can't catch
this since nothing calls the function during boot):**

Before fix — `llvm-nm lib/is_single_threaded_rs.o`:
```
0000000000000000 T current_is_single_threaded
0000000000000000 B current_stack_pointer
0000000000000008 B riscv_current_is_tp
```
Before fix — disassembly of `current_is_single_threaded`'s task-pointer
load: `auipc a6, 0x0` / `ld a1, 0x0(a6)` — a PC-relative load from
`.bss` (the fabricated always-null static), then `ld a0, 0x5c8(a1)`
dereferences it. Guaranteed fault the instant this runs.

After fix — `llvm-nm lib/is_single_threaded_rs.o`:
```
0000000000000000 T current_is_single_threaded
```
`riscv_current_is_tp`/`current_stack_pointer` symbols gone entirely
(not just unreferenced — absent from the object). `llvm-objdump -r`
confirms zero relocations to either name anywhere in the `.o`.
Disassembly of the same load site now reads `mv a0, tp` — a genuine
register move, immediately followed by the same `ld a2, 0x5c8(a0)`
(`.mm`) / `ld a1, 0x3d0(a0)` (`.signal`) field dereferences, now
against the real task pointer. A second `get_current()` call site
(preempt-count path further down the function) compiles to `mv a2, tp`
identically. `vmlinux` also carries zero references to either
fabricated static
(`llvm-nm vmlinux | grep 'riscv_current_is_tp\|current_stack_pointer'`
empty).

Full rebuild (`dev.py build`, `LINUXRS_TREE=linux-riscv-worktrees/combined-c2rust-boot-3`)
and reboot (`dev.py boot`) after the fix: 17/17 KUnit suites pass,
`fail:0` every suite, 0 `not ok`, INIT REACHED — same as the original
"boots clean" outcome, now backed by object-level proof that
`get_current()` reads the real register instead of a fabricated
always-null global, rather than boot-clean status alone (which cannot
distinguish "fixed" from "still broken but unreached," exactly the gap
issue #31 flagged).

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

### Retroactive fix: refcount.h overflow-detection panic (issue #39)

`__refcount_add_not_zero`/`__refcount_add`/`__refcount_sub_and_test`'s
`old + i < 0`/`old - i < 0` overflow-detection comparisons (inlined
via `refcount_dec_and_test`, reached by `klist_add_tail`/`klist_del`)
translated to plain checked `+`/`-`, panicking on overflow instead of
running the intended graceful-detection path — `CONFIG_RUST_OVERFLOW_CHECKS=y`
is set, so this was live, not hypothetical. Fixed in-place: 3 comparison
sites changed to `old.wrapping_add(i)`/`old.wrapping_sub(i)`
(the `atomic_try_cmpxchg_relaxed` argument's own `old + i` is untouched —
that's the real atomic write value, not a local sentinel). Rebuilt,
reboot confirms 17/17 KUnit suites, INIT REACHED, no regression.
Root-caused and fixed at the c2rust source too (`awtoau/c2rust#27`,
merged `5622104e9`) — future re-transpiles of this file won't need
this hand-fix.

## Eighth candidate: `lib/bucket_locks.c`

Worktree `combined-c2rust-boot-10`, branch `agent-combined-c2rust-boot-10`,
based on `linux-rs/phase2-gcd` at `04312ea1ff7e` (includes the
Zacas/Zabha `KBUILD_RUSTFLAGS` fix, issue #29's closing commit).
Target: `__alloc_bucket_spinlocks`/`free_bucket_spinlocks`, both
`EXPORT_SYMBOL` (not `_GPL`), 55 lines of C. c2rust binary at
`6065eaf19`. Fresh transpile via `investigate_c2rust_failure.py
--rerun`: `outcome=clean`, byte-identical (`diff -q`) to the
pre-existing `tmp/c2rust-baseline/lib_bucket_locks.c/` output.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `bucket_locks.o` pulled out of the
bundled `obj-y` line (shared with `once.o refcount.o rcuref.o
errseq.o`), swapped for `bucket_locks_rs.o` under the config. Raw
transpile copied to `lib/bucket_locks_rs.rs` (3438 lines).

Of the 3 gap classes fixed upstream in the c2rust fix series: confirmed
absent — no `#![feature(...)]` line at all (not even a leftover), both
functions already carried `#[no_mangle]` (not `#[export]`/
`::macros::export`) directly in the raw output, `unsafe {}` already
wrapping both function bodies, no `.init_array`/
`__UNIQUE_ID_addressable_*` constructor trick anywhere. First file in
this series needing zero `#[export]`-class fixes — cleanest raw output
so far.

### `BitfieldStruct` derives: 5 structs, all dead-by-value and dead-by-field

Most of any file so far: `task_struct`, `mmap_action`,
`percpu_ref_data`, `signal_struct`, `sched_dl_entity`. Grep-confirmed
per struct (not assumed): the file's only 6 functions
(`__must_check_overflow`, `size_mul`, `spinlock_check`,
`mem_alloc_profiling_enabled`, `__alloc_bucket_spinlocks`,
`free_bucket_spinlocks`) touch none of the 5 by value or by field —
`task_struct` appears only as `*mut task_struct` (13 occurrences, all
pointer-typed struct fields or the fabricated `riscv_current_is_tp`
static itself), the other 4 only as pointer fields or, for
`sched_dl_entity`, embedded by value once inside the also-dead
`task_struct`. No `container_of`-style offset arithmetic or
`size_of::<>()` call touches any of the 5 anywhere in the TU (unlike
`klist.c`'s `task_struct.__state` case). Opaqued all 5 via the standard
`opaque_marker!` macro. `mmap_action`'s nested anonymous union
(`C2Rust_Unnamed_17`) becomes unreferenced dead code once `mmap_action`
is opaqued but needed no separate handling — covered by the file's
existing `#![allow(dead_code)]`.

### Register-variable pseudo-globals: both dead, and — unusually — no `get_current()` synthesized at all

Per the standing rule (rulesdb `0031-fabricated-register-variable-static`,
closed issues #30/#31): grepped for a `get_current()` accessor
specifically, not just the `riscv_current_is_tp`/`current_stack_pointer`
identifiers themselves, before deleting anything. **No `get_current()`
function exists anywhere in this TU** — this file's only spinlock
operation is `spin_lock_init()`, whose non-`CONFIG_DEBUG_SPINLOCK`
macro expansion (confirmed against this worktree's `.config`:
`CONFIG_DEBUG_SPINLOCK` unset) is `spinlock_check(_lock);
*(_lock) = __SPIN_LOCK_UNLOCKED(_lock);` — a raw struct write, no
`task_struct`/preemption-count touchpoint at all. Unlike `klist.c`
(hit via `spin_unlock`'s inlined preemption-check path) or `lwq.c`
(accessor present but grep-confirmed uncalled), this file never
synthesizes the accessor in the first place: no `preempt_count`,
`kasan_check_write`, `kcsan_check_access`, or `instrument_atomic_*`
helper functions appear anywhere in the TU either, confirming
`spin_lock_init`-only usage doesn't pull in the chain that made
`klist.c`'s case live. Both `riscv_current_is_tp` (declaration only,
zero reads) and `current_stack_pointer` (same) deleted outright.
Independently confirmed via `scripts/check_fabricated_register_statics.py`
run against the full baseline corpus: `lib_bucket_locks.c` is listed in
the dead/safe set, not the 163-file live/needs-fix set.

No RISC-V inline-asm gaps this time (no `amocas`/`amoswap`/`amoadd`/
`cmpxchg`/`xchg` anywhere in the TU — `spin_lock_init` doesn't lower to
an atomic RMW), the first file in the series not to touch issue #29 at
all.

### Environment red herring: fresh worktree lacked the project's real `.config`, produced a false-positive hang unrelated to this file

First boot attempt (both the Rust build and, for isolation, a plain-C
`CONFIG_RUST_C2RUST_BOOT_TEST=n` rebuild in the same worktree) hung
identically after the OpenSBI banner — no `Linux version` line, no
KUnit output — which looked exactly like the `klist.c` register-static
class of bug. `-d guest_errors,unimp,int` tracing showed continuous
`s_timer`/`supervisor_ecall` traps across a wide range of kernel
addresses (consistent with a running scheduler, not a genuine stall),
and the plain-C rebuild reproduced the identical hang, which is
conclusive: `__alloc_bucket_spinlocks`/`free_bucket_spinlocks` are
never even called in this kernel's boot path in the first place (their
only in-tree caller is `net/ipv6/ila/ila_xlat.c`, `CONFIG_ILA` unset),
so a bug in this file's Rust translation cannot be the cause of a hang
that also reproduces with `bucket_locks.c`'s original C. Root cause:
`scripts/linux_riscv_worktree.py create` does not carry over a working
`.config` (worktrees "share git history but NOT build artifacts" per
the script's own docstring), and this worktree's `.config` had somehow
picked up a generic riscv defconfig-shaped config missing
`CONFIG_BLK_DEV_INITRD`/`CONFIG_KUNIT`/`CONFIG_RUST` and hundreds of
other options present in `linux-riscv/.config` (the main tree) — not a
c2rust or bucket_locks.c problem at all. Fixed by copying
`linux-riscv/.config` into the worktree, `make olddefconfig`, then
re-applying `RUST_C2RUST_BOOT_TEST=y`. Worth flagging as a setup-step
gap for future worktrees: `linux_riscv_worktree.py create`'s output
already warns "this worktree has git history only — no build artifacts
... build a kernel image in it before boot-testing," but doesn't call
out that the `.config` specifically needs to be seeded from the main
tree, not regenerated from the base defconfig file — the two produce
materially different configs and only one boots this project's
initramfs.

### Outcome: clean boot

- `make ARCH=riscv LLVM=1 lib/bucket_locks_rs.o` — clean, 0 errors
  (1656 warnings, all missing-doc lints, pre-existing pattern).
- `make ARCH=riscv LLVM=1 -j32` (`dev.py build`) succeeds,
  `arch/riscv/boot/Image` produced. `lib/bucket_locks.o` correctly
  never built once `.config` was fixed.
- `llvm-nm`: both symbols `T` in both `lib/bucket_locks_rs.o` and
  `vmlinux` (`ffffffff801345ae T __alloc_bucket_spinlocks`,
  `ffffffff801345d8 T free_bucket_spinlocks`).
- `scripts/boot_qemu.py --tree linux-riscv-worktrees/combined-c2rust-boot-10
  --run-id combined-c2rust-10-v2`: boots clean, 17/17 KUnit suites pass
  (`fail:0` every suite, 0 `not ok`), `initramfs init reached, PID 1
  alive`, no panic/oops/BUG/WARN in the log. Archived at
  `docs/status/boot-logs/20260719T131820+1000-combined-c2rust-10-v2.log`.
  No dedicated bucket_locks KUnit suite in this config.

Eighth file to clear the bar. Cleanest raw c2rust output of the series
so far (no `#[export]` fix, no leftover feature line, no RISC-V
inline-asm gap) — confirms the transpiler fix series continues to hold
and that a file's actual gap-class exposure depends heavily on what it
transitively pulls in via headers (this file's `spin_lock_init`-only
usage avoids the whole preemption-check/atomic-helper chain that drove
most other files' complexity). The `get_current()`-liveness check
(rule 0031) produced a clean "genuinely dead, no accessor at all"
verdict here, distinct from both `klist.c`'s "live, must fix" and
`lwq.c`'s "accessor present but uncalled" cases — a third distinct
outcome for the same check, reinforcing that it must be run fresh per
file rather than assumed from a prior file's result in either
direction.

## Ninth candidate: `lib/glob.c`

Worktree `combined-c2rust-boot-8`, branch `agent-combined-c2rust-boot-8`,
based on `linux-rs/phase2-gcd`. Target: `glob_match`, `glob_match_len`
(both plain `EXPORT_SYMBOL`, non-GPL — matches the file's own SPDX
`GPL-2.0 OR MIT` dual license), 155 lines of C, pure string matching,
no register/FFI/hardware dependency. c2rust binary at `6065eaf19`
(post the `extern_types`/`asm`/`raw_ref_op`/`strict_provenance`/
unsafe-wrapping/`.init_array` fix series). Baseline transpile at
`tmp/c2rust-baseline/lib_glob.c/output/src/glob.rs` (3748 lines,
almost entirely pulled-in `task_struct`-web struct furniture from
`linux/sched.h`'s transitive include chain — the real TU is 3
functions) used as-is, no re-transpile needed.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `glob.o`'s `obj-$(CONFIG_GLOB)`
line swapped for a conditional `glob_rs.o`/`glob.o` pair under the
config. `CONFIG_GLOB` was already `y` in this boot config (pulled in
transitively via `lib/kunit/Kconfig`'s `select GLOB`), confirming this
file is genuinely linked into the combined image already, not a
config-gated no-op.

Of the 3 gap classes fixed upstream in the c2rust fix series: confirmed
absent — no `#![feature(...)]` line at all (cleaner than every prior
file in this series, all of which had at least a leftover entry),
`unsafe {}` already wrapping every function body, no `.init_array`/
`__UNIQUE_ID_addressable_*` constructor trick anywhere in the output.
Also confirmed absent: no `#[export]`/`use ::macros::export` anywhere
— c2rust's raw output already emitted `#[no_mangle]` plus the correct
hand-shaped `global_asm!` `.export_symbol` section directly (with an
empty license field, matching `EXPORT_SYMBOL`'s real macro expansion
in `include/linux/export.h` byte-for-byte), so the `#[export]` →
`#[no_mangle]` fix class simply didn't apply here — first file in the
series needing zero action on either of the 2 gap classes the upstream
fix series doesn't cover on the export-attribute front.

Only real fix needed: `c2rust_bitfields::BitfieldStruct` derive on 6
structs (`task_struct`, `mmap_action`, `kobject`, `kernfs_open_file`,
`signal_struct`, `sched_dl_entity`), all pulled in transitively and
unused *by value* in this TU (grep-confirmed). Traced the by-value
dependency graph rather than assuming leaf-node deletion (per the
lesson from `lzo1x_decompress_safe.c`): `mmap_action` is embedded
by-value in `vm_area_desc`, `kobject` is embedded by-value in both
`kset` and `module_kobject`, and `module_kobject` is itself embedded
by-value in `module` — none of `vm_area_desc`/`kset`/`module` are used
by-value anywhere else (grep-confirmed, pointer-only). Real leaf set:
9 types opaqued via the standard `opaque_marker!` idiom (`task_struct`,
`kobject`, `kernfs_open_file`, `signal_struct`, `sched_dl_entity`,
`kset`, `module`, `module_kobject`, `vm_area_desc`); `mmap_action`
itself deleted outright (becomes fully unreferenced — its only use
anywhere was the one field inside `vm_area_desc` — once that struct is
opaqued). `mmap_action`'s downstream union/type-alias furniture
(`C2Rust_Unnamed_21`..`24`, `mmap_action_type` + 5 consts) doesn't
carry the `BitfieldStruct` derive itself, so left in place as harmless
dead code under the file's existing `#![allow(dead_code)]`, same as
every prior file's non-bitfield dead furniture.

Fabricated register-variable statics check (per the issue #30/#31
finding that these aren't always safe to delete blanket): grepped for
`get_current` specifically, not just the `riscv_current_is_tp`
identifier — zero occurrences anywhere in the 3748-line TU, no
`get_current()` accessor function defined or called at all. Both
`riscv_current_is_tp` and `current_stack_pointer` appear exactly once
each (their own fabricated-static declaration line), confirmed
genuinely dead here, unlike `klist.c`. Deleted both outright, same
disposition as `lzo1x_decompress_safe.c`/`objpool.c`.

Fresh-worktree gotcha (environment, not a c2rust bug): the newly
created worktree had no `.config` at all, so the first `dev.py config
-e RUST_C2RUST_BOOT_TEST` + `olddefconfig` cycle generated one from
bare Kconfig defaults — silently defaulting `CONFIG_RUST` itself to
off (`# CONFIG_RUST is not set`) and producing a vmlinux with no Rust
code linked in at all (wrong, but not loudly wrong — the build still
said `BUILD OK`). Caught by checking `llvm-nm vmlinux` for
`glob_match` directly rather than trusting the build's exit code alone
(the shared `tmp/dev-build.log` this project's tooling writes is also
unsafe to inspect post-hoc when multiple worktrees are building
concurrently — it was mid-overwrite by a sibling agent's boot-11 build
by the time this was checked). Fixed by copying a sibling worktree's
already-`CONFIG_RUST=y`/`CONFIG_GLOB=y` `.config` in as the base before
re-running `dev.py config -e`. Worth calling out for future parallel
combined-boot-N worktrees: verify the target symbol actually appears in
`vmlinux` after every build, don't trust `BUILD OK` alone, especially
in a brand-new worktree with no pre-seeded `.config`.

### Outcome: clean boot, cleanest fix scope in the series so far

- `make ARCH=riscv LLVM=1 lib/glob_rs.o` — clean, 0 errors (1721
  warnings, all missing-doc/unused-var lints, pre-existing pattern).
- `make ARCH=riscv LLVM=1 -j32` (`dev.py build`) succeeds,
  `arch/riscv/boot/Image` and `Image.xz` produced. `lib/glob.o`
  correctly never built (no `.glob.o.cmd` present).
- `llvm-nm`: both exported symbols `T` in both `lib/glob_rs.o` and
  `vmlinux` (`ffffffff80158ce6 T glob_match`, `ffffffff80158cfe T
  glob_match_len`), plus `glob_match_str` correctly `t` (local,
  matching the C original's `static` linkage) as
  `_RNvCs3OAnprC1gR0_7glob_rs14glob_match_str`. No
  `riscv_current_is_tp`/`current_stack_pointer` symbols anywhere in
  `vmlinux` (confirms the deletion left nothing dangling).
- `scripts/boot_qemu.py --tree linux-riscv-worktrees/combined-c2rust-boot-8
  --run-id combined-c2rust-9`: boots clean, 17/17 KUnit suites pass
  (`fail:0` every suite, 0 `not ok`), `initramfs init reached, PID 1
  alive`, no panic/oops/BUG/WARN in the log. Archived at
  `docs/status/boot-logs/20260719T131521+1000-combined-c2rust-9.log`.
  No dedicated glob KUnit suite in this kernel config, so
  `glob_match`/`glob_match_len`'s pattern-matching logic itself wasn't
  runtime-exercised by this run — boot-screened only, same caveat as
  every other file in this series without a dedicated suite.

Eighth file to clear the bar. First file in the series with a
genuinely minimal fix scope (one gap class only — `BitfieldStruct`
opaquing — zero feature-line strips, zero `#[export]` handling, zero
libc/const-eval/namespace-collision fixes), confirming the task's
prediction that a small pure-logic file with no register/FFI/hardware
surface would need the least hand-fixing of the series. The only real
finding was environment-side (fresh-worktree `.config` seeding), not
transpiler-side. `get_current()`-liveness check (issue #30/#31 class)
came back negative here, unlike `klist.c` — third confirmation that
this check must be done per-file via grep, not assumed either way.

## Tenth candidate: `lib/errseq.c`

Worktree `combined-c2rust-boot-11`, branch `agent-combined-c2rust-boot-11`,
based on `linux-rs/phase2-gcd` (already carries `04312ea1ff7e`, the
issue #29 `KBUILD_RUSTFLAGS` Zacas/Zabha fix). Target: all 4
`EXPORT_SYMBOL`'d functions (`errseq_set`, `errseq_sample`,
`errseq_check`, `errseq_check_and_advance`), 209 lines of C, pure
atomic-int error-sequence-counter logic using `cmpxchg()`. c2rust binary
at `6065eaf19` (post the `extern_types`/`asm`/`raw_ref_op`/
`strict_provenance`/unsafe-wrapping/`.init_array` fix series, plus the
further merge fix stopping stale `raw_ref_op`/`strict_provenance`
declarations). Baseline transpile at
`tmp/c2rust-baseline/lib_errseq.c/output/src/errseq.rs` (1255 lines),
freshness confirmed via `investigate_c2rust_failure.py --rerun`:
`outcome=clean`, `c2rust_rev=6065eaf19`, byte-identical rerun.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `errseq.o` pulled out of a bundled
`obj-y` line shared with `once.o`, `refcount.o`, `rcuref.o`,
`bucket_locks.o`, etc., swapped for `errseq_rs.o` under the config.

Cleanest baseline output of the series: no `extern_types`/`asm` in the
feature line, `unsafe {}` already wrapping every function body (only 2
of 17 `unsafe extern "C" fn`s are genuinely empty and correctly
unwrapped), no `.init_array`/`__UNIQUE_ID_addressable_*` constructor
trick, no `#[export]`/`::macros::export` (all 4 functions already carry
plain `#[no_mangle]`), no `c2rust_bitfields::BitfieldStruct` derive at
all, no `asm/current.h` pull-in (`get_current`/`riscv_current_is_tp`/
`current_stack_pointer` grep-confirmed absent — this TU never touches
`task_struct`), no `::libc::` calls, no `LIST_POISON`. Only the
leftover-feature-line class recurred: `#![feature(label_break_value)]`,
stripped.

`kernel::warn_on!` gap class (from `rcuref.c`) recurred: `errseq_set`'s
`if kernel::warn_on!(cond) != 0 { ... }` fails to typecheck (`E0308`,
expected bool found integer) since `warn_on!` already returns `cond` as
a `bool`. Fixed by dropping the trailing `!= 0`, same as `rcuref.c`.

Issue #29 bracket-addressing finding recurred, a fifth confirmation:
24 occurrences of `[{N}]`/`[{N:}]` across `amocas.{b,h,w,d}.aqrl` and
their LR/SC fallback templates (both `errseq_set` and
`errseq_check_and_advance` each cmpxchg a 32-bit `errseq_t`, unrolling
all 4 width arms per call site). Fixed: `[{N}]`/`[{N:}]` ->
`0({N})`/`0({N:})` throughout, mechanically via a small Python regex
pass rather than by hand.

### New gap class: `asm goto(ALTERNATIVE(...))` has no real linkable symbol to fall back to — first file where this actually breaks the link

Unlike every prior file, this is the first candidate where the issue #29
KBUILD_RUSTFLAGS fix is actually in play — since Zacas/Zabha now have
real Rust target-feature support, the `amocas` fast-path arms didn't
need deleting outright the way `rcuref.c`/`lwq.c`/`objpool.c`/`klist.c`
did. This surfaced a different, previously-hidden problem instead: the
`riscv_has_extension_unlikely()`/`riscv_has_extension_likely()` runtime
guards feeding those fast-path `if` conditions themselves depend on
`__riscv_has_extension_unlikely()`/`__riscv_has_extension_likely()` —
`static __always_inline` C functions built entirely from `asm
goto(ALTERNATIVE(...))` (RISC-V's boot-time alternative-patching
mechanism: patches a `j`/`nop` branch in the `.alternative` section at
`apply_boot_alternatives()` time, based on detected CPU extensions).
c2rust's transpile.log already flagged this as untranslatable
("Falling back to an extern declaration for
'__riscv_has_extension_unlikely': body failed to translate: Cannot
translate GNU asm goto (extended asm with label operands)") and emitted
bare `extern "C" { fn __riscv_has_extension_unlikely(...); ... }`
declarations instead of a body — but because these are C
`__always_inline` functions, there is **no real linkable symbol**
anywhere in the kernel for either name; every C call site is inlined
away. `lib/objpool_rs.o` carries the identical extern declarations but
never hit this at link time, because that file's fix deleted the
`amocas` fast-path wrappers outright (issue #29's original fix, before
`04312ea1ff7e` landed), which made the `riscv_has_extension_unlikely`
call sites dead code eliminated before linking — this file is the first
to actually try keeping the fast path (now that KBUILD_RUSTFLAGS
supports it), which is what exposed the real problem:
`ld.lld: error: undefined symbol: __riscv_has_extension_likely` /
`__riscv_has_extension_unlikely`, referenced from `errseq_check`,
`errseq_check_and_advance`, and `errseq_set`.

Deleting the fast-path wrappers (the established issue #29 playbook fix)
was not viable here: `riscv_has_extension_likely(ZBB)` is also called
from `variable__fls`/`variable_fls` (feeding the `__ilog2_u32`/
`__ilog2_u64` chain that every one of this file's 4 exported functions
uses to compute `ERRSEQ_SHIFT` from `MAX_ERRNO` — c2rust translates the
C `is_power_of_2`/`__builtin_constant_p`-gated compile-time-constant
`ilog2()` path as a runtime `if false { ... } else { variable_fls(...) }`
dead branch, so the runtime call survives translation even though the
real C build resolves it at compile time). This call is genuinely
reached on every function call, not an optional fast path — so unlike
the amocas arms, it isn't a deletable-without-behavior-change branch.

Fixed by routing both `riscv_has_extension_unlikely`/`_likely` through
the real, already-linkable `__riscv_isa_extension_available(NULL, ext)`
— the exact fallback path `arch/riscv/include/asm/cpufeature-macros.h`'s
own C source uses when `CONFIG_RISCV_ALTERNATIVE` is off, already
declared correctly in this TU's `extern "C" { ... }` block (c2rust had
already emitted a correct declaration for it, just never wired the two
broken functions to call it). Semantically identical query — same
true/false answer for the same extension bit; "likely"/"unlikely" was
only ever a branch-prediction hint for the boot-patched-branch fast
path, not a correctness difference — just without the
`CONFIG_RISCV_ALTERNATIVE=y` patched-branch speed-up (a real function
call instead of a patched inline branch). Removed the now-dead
`__riscv_has_extension_likely`/`_unlikely` extern declarations
afterward, leaving only `__riscv_isa_extension_available` in the block.

General lesson for future files: an `extern "C"` declaration c2rust
emits as a "fallback for an untranslatable body" is not automatically
safe to leave alone just because it currently compiles — it only
surfaces as a real problem at the *link* stage, and only if the call
site actually survives to link time (dead-code elimination via an
unrelated fix, as happened for `objpool.c`, can mask it entirely). Any
file pulling in `asm/cpufeature-macros.h`'s `riscv_has_extension_likely`/
`_unlikely` (directly or via any ISA-extension-gated fast path, not just
Zacas/Zabha's) is a candidate for this — worth grepping the full
c2rust-baseline corpus for `Cannot translate GNU asm goto` in
transpile logs before landing anything else that reaches link stage
with the guard intact.

### Outcome: clean boot, ninth file to clear the bar

- `make ARCH=riscv LLVM=1 lib/errseq_rs.o` — clean, 0 errors (73
  warnings, all missing-doc/dead_code lints, pre-existing pattern).
- `make ARCH=riscv LLVM=1 -j32` (`dev.py build`) succeeds,
  `arch/riscv/boot/Image` and `Image.xz` produced. `lib/errseq.o`
  correctly never built into `lib.a`/`vmlinux.a`.
- `llvm-nm`: all 4 symbols `T` in both `lib/errseq_rs.o` and `vmlinux`
  (`ffffffff80134516 T errseq_check`, `ffffffff801345b6 T
  errseq_check_and_advance`, `ffffffff80134706 T errseq_sample`,
  `ffffffff80134784 T errseq_set`).
- `scripts/boot_qemu.py --run-id combined-c2rust-11`: boots clean,
  17/17 KUnit suites pass (`fail:0` every suite, 0 `not ok`),
  `initramfs init reached, PID 1 alive`, no panic/oops/BUG/WARN in the
  log. Archived at
  `docs/status/boot-logs/20260719T131403+1000-combined-c2rust-11.log`.
  No dedicated errseq KUnit suite in this kernel config, so this run
  demonstrates linking and booting cleanly, not a runtime correctness
  check of the error-sequence-counter logic itself against known inputs.

Ninth file to clear the bar, and the first to need real hand-fixing for
a gap class no prior file's fix scope had exercised: the `asm
goto(ALTERNATIVE(...))` fallback-extern-with-no-real-symbol problem,
uncovered specifically because this is the first file where the issue
#29 KBUILD_RUSTFLAGS fix let a Zacas/Zabha-guarded fast path survive to
link time instead of being deleted outright. Otherwise the cleanest file
of the series structurally — no `BitfieldStruct`, no register-variable
statics, no `.init_array`, no `#[export]` — confirming those classes are
genuinely conditional on what a TU's C source actually pulls in
(`EXPORT_SYMBOL`, `task_struct` touchpoints, `asm/current.h`), not
universal.

## Eleventh candidate: `lib/uuid.c`

Worktree `combined-c2rust-boot-14`, branch `agent-combined-c2rust-boot-14`,
based on `linux-rs/phase2-gcd`. Target: all 7 exported symbols
(`generate_random_uuid`, `generate_random_guid`, `guid_gen`, `uuid_gen`,
`uuid_is_valid`, `guid_parse`, `uuid_parse`, plus the two data statics
`guid_null`/`uuid_null`), 135 lines of C, pure UUID/GUID parse-format-
generate logic with no hardware dependency. Baseline transpile at
`tmp/c2rust-baseline/lib_uuid.c/output/src/uuid.rs` (224 lines).

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `uuid.o` pulled out of a bundled
`obj-y` line shared with `iov_iter.o`, `clz_ctz.o`, `bsearch.o`, etc.,
swapped for `uuid_rs.o` under the config.

`python3 scripts/dev.py check-register-statics` run first per the
mandatory rule 0031 check: `lib_uuid.c` did not appear in either the
LIVE or dead list output at all — grep-confirmed separately (0
occurrences of `get_current`/`riscv_current_is_tp`/
`current_stack_pointer` anywhere in the baseline `uuid.rs`), consistent
with the tool's silence. This TU never pulls in `asm/current.h` at all
(no `task_struct` touchpoint of any kind), so the check doesn't apply
here.

Cleanest baseline output of the series so far, cleaner even than
`errseq.c`: no `#![feature(...)]` line at all (not even
`label_break_value`), `unsafe {}` already wrapping every function body,
no `.init_array`/`__UNIQUE_ID_addressable_*` constructor trick, no
`c2rust_bitfields::BitfieldStruct` derive anywhere (no `task_struct`
pulled in at all — this TU's only external call is `get_random_bytes`),
no register-variable statics, no `::libc::` calls, no RISC-V inline-asm
(`amocas`/`amoswap`) at all — the function bodies are straight-line
byte-array manipulation and a loop over `hex_to_bin`/`isxdigit`, nothing
atomic or cmpxchg-shaped.

Only one gap class recurred: **`#[export]` on the two
`EXPORT_SYMBOL_GPL` functions** (`guid_gen`, `uuid_gen`) — the
established `rcuref.c`/`lwq.c` fix, dropped `#[export]` and
`use ::macros::export;` (now-unused import), added `#[no_mangle]` in
its place. The 5 plain `EXPORT_SYMBOL` functions/statics
(`generate_random_uuid`, `generate_random_guid`, `uuid_is_valid`,
`guid_parse`, `uuid_parse`, `guid_null`, `uuid_index`, `guid_index`)
already carried plain `#[no_mangle]` in the raw c2rust output, no fix
needed — matching `is_single_threaded.c`'s finding that c2rust only
emits `#[export]`/`::macros::export` for `EXPORT_SYMBOL_GPL`, not plain
`EXPORT_SYMBOL`. No other manual intervention of any kind.

### Outcome: clean boot, eleventh file to clear the bar

- `make ARCH=riscv LLVM=1 lib/uuid_rs.o` — clean, 0 errors.
- `make ARCH=riscv LLVM=1 -j32` (`dev.py build`) succeeds,
  `arch/riscv/boot/Image` produced. `lib/uuid.o` correctly never built
  (only `lib/uuid_rs.o` present).
- `llvm-nm`: all 9 symbols defined in both `lib/uuid_rs.o` and
  `vmlinux` — `T generate_random_guid`, `T generate_random_uuid`,
  `T guid_gen`, `D guid_index`, `B guid_null`, `T guid_parse`,
  `T uuid_gen`, `D uuid_index`, `T uuid_is_valid`, `B uuid_null`,
  `T uuid_parse` (`vmlinux` addresses e.g.
  `ffffffff8016062e T guid_gen`, `ffffffff8016068a T uuid_gen`) — both
  confirming `#[no_mangle]` correctly emitted the plain C names rather
  than a Rust-mangled symbol.
- `dev.py boot --run-id combined-c2rust-boot-14`: boots clean, 17/17
  KUnit suites pass (`fail:0` every suite, 0 `not ok`), `initramfs init
  reached, PID 1 alive` confirms INIT REACHED, no panic/oops/BUG/WARN in
  the log. Archived at
  `docs/status/boot-logs/20260719T140406+1000-combined-c2rust-boot-14.log`.
  Boot-history row committed/pushed automatically by `boot_qemu.py`
  (`750f3e4`). No dedicated uuid KUnit suite in this kernel config, so
  this run demonstrates linking and booting cleanly, not a runtime
  correctness check of the UUID parse/format/generate logic itself
  against known inputs.

Eleventh file to clear the bar, and the cleanest fix scope of the
series tied with `glob.c` — a single gap class (`#[export]` on the
GPL-exported pair), zero feature-line strips, zero unsafe-wrapping
needed, zero `BitfieldStruct` handling, zero register-variable-static
handling, zero RISC-V inline-asm gaps. Confirms the pattern
`is_single_threaded.c` and `glob.c` both established: a small,
self-contained, hardware-independent pure-logic file needs
substantially less hand-fixing than one touching atomics, `task_struct`,
or RISC-V memory-ordering primitives — which gap classes fire seems to
track directly with what headers/kernel facilities the C source actually
pulls in, not file size or line count.

## Twelfth candidate: `lib/bust_spinlocks.c`

Worktree `combined-c2rust-boot-13`, branch `agent-combined-c2rust-boot-13`,
based on `linux-rs/phase2-gcd`. Target: `bust_spinlocks()`, the sole
function, not `EXPORT_SYMBOL`'d (plain internal `extern`, declared in
`include/linux/kernel.h`, called from `panic()`/`die()`/oops paths), 26
lines of C. Baseline transpile at
`tmp/c2rust-baseline/lib_bust_spinlocks.c/output/src/bust_spinlocks.rs`
(4566 lines).

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `bust_spinlocks.o` pulled out of a
bundled `obj-y` line shared with `debug_locks.o`, `random32.o`,
`scatterlist.o`, etc. (left `debug_locks.o` untouched — a sibling agent
was concurrently working that file in its own worktree), swapped for
`bust_spinlocks_rs.o` under the config.

`python3 scripts/dev.py check-register-statics` run first per the
mandatory rule 0031 check, as flagged given the file's spinlock/
preemption-adjacent name: `lib_bust_spinlocks.c` did not appear in
either the LIVE or dead list output. Grep-confirmed directly instead —
no `get_current()` accessor is even synthesized in this TU (0
occurrences), so the two fabricated statics `riscv_current_is_tp`/
`current_stack_pointer` are present but definitionally unreachable (no
accessor to call them through). `bust_spinlocks()`'s own body only
touches `oops_in_progress` (a plain `extern` int, unrelated to
`current`), `console_unblank()`, `wake_up_klogd()` — confirmed via grep
neither fabricated static is referenced anywhere else in the TU either.
Matches the `group_cpus.c`/`rcuref.c`/`timerqueue.c` "no accessor
synthesized at all" branch of rule 0031, the safe-to-delete-outright
case, not the `klist.c`/`is_single_threaded.c` live-via-accessor case.
Deleted both statics outright.

Cleanest structural shape of the series: no `#![feature(...)]` line at
all, `unsafe {}` already wrapping the function's one body, no
`.init_array`/`__UNIQUE_ID_addressable_*` constructor trick (consistent
with `is_single_threaded.c`'s finding — no `EXPORT_SYMBOL` in the C
source, so c2rust never emits the trick), no `#[export]`/
`::macros::export` (already plain `#[no_mangle]`), no `::libc::` calls,
no RISC-V inline-asm. The one real gap: **9 dead `BitfieldStruct`-derived
structs** pulled in transitively via `task_struct` and its
by-value-embedded/pointer-adjacent neighbors (`mmap_action`, `kobject`,
`kernfs_open_file`, `percpu_ref_data`, `signal_struct`, `tty_port`,
`dev_pm_info`, `sched_dl_entity`) — more than any prior file (previous
max was 6, `lzo1x_decompress_safe.c`). Verified via grep that
`bust_spinlocks()`'s body touches none of them, directly or by pointer,
and that every other struct that embeds one of the nine by value
(`inode`, `vm_area_desc`, `sysfs_ops`, `module_attribute`,
`proc_dir_entry`, `device`) is itself only ever referenced by pointer
from elsewhere in the TU, never by value and never from the live
function — so, unlike `lzo1x_decompress_safe.c`'s partial-chain case,
the whole 9-struct subgraph collapses to dead-by-construction without
needing per-container load-bearing checks (nothing pointer-only cares
about pointee layout). All 9 converted to the standard zero-field
`opaque_marker!` idiom in one macro invocation.

### Outcome: clean boot, twelfth file to clear the bar

- `make ARCH=riscv LLVM=1 lib/bust_spinlocks_rs.o` — clean, 0 errors
  (2327 warnings, all missing-doc lints, pre-existing pattern).
- `make ARCH=riscv LLVM=1 -j32` (`dev.py build`) succeeds,
  `arch/riscv/boot/Image` produced. `lib/bust_spinlocks.o` correctly
  never built (only `lib/bust_spinlocks_rs.o` present in `lib.a`).
- `llvm-nm`: `bust_spinlocks` defined (`T`) in both
  `lib/bust_spinlocks_rs.o` and `vmlinux`
  (`ffffffff801606ea T bust_spinlocks`); `riscv_current_is_tp`/
  `current_stack_pointer` absent from both objects entirely (not just
  unreferenced — confirmed via `llvm-nm` grep, empty result).
- `dev.py boot --run-id combined-c2rust-boot-13`: boots clean, 17/17
  KUnit suites pass (`fail:0` every suite, 0 `not ok`), `initramfs init
  reached, PID 1 alive` confirms INIT REACHED, no panic/oops/BUG/WARN in
  the log. Archived at
  `docs/status/boot-logs/20260719T140624+1000-combined-c2rust-boot-13.log`.
  Boot-history row committed/pushed automatically by `boot_qemu.py`
  (`e08dcd9`). No dedicated bust_spinlocks KUnit suite in this kernel
  config, so this run demonstrates linking and booting cleanly, not a
  runtime correctness check against known inputs — and since
  `bust_spinlocks()` is only ever called from real panic/oops paths,
  this project's minimal boot never exercises it at all regardless.

Twelfth file to clear the bar. Tiny (26-line) source, but pulled in the
largest `BitfieldStruct` dead-struct set of the series (9, vs. the prior
max of 6) purely as header noise — reconfirms the `uuid.c`/`errseq.c`
lesson that gap-class count tracks what headers a TU pulls in, not
source line count: a 26-line file can still drag in a ~4500-line
`task_struct`-rooted prelude. Also the first file since `lwq.c` to
exercise rule 0031's "no accessor synthesized" dead branch explicitly
flagged for a spinlock-adjacent file and confirmed rather than assumed.

## Thirteenth candidate: `lib/debug_locks.c`

Worktree `combined-c2rust-boot-12`, branch `agent-combined-c2rust-boot-12`,
based on `linux-rs/phase2-gcd` at `04312ea1ff7e` (Zacas/Zabha
`KBUILD_RUSTFLAGS` fix, issue #29's closing commit). Target:
`debug_locks_off()` (`EXPORT_SYMBOL_GPL`) plus the two module-level data
statics it guards, `debug_locks`/`debug_locks_silent` (both also
`EXPORT_SYMBOL_GPL`), 49 lines of C. c2rust binary at `6065eaf19`. Fresh
transpile via `investigate_c2rust_failure.py --rerun`: `outcome=clean`,
`c2rust_rev=6065eaf19`, byte-identical (`diff -q`) to the pre-existing
`tmp/c2rust-baseline/lib_debug_locks.c/` output.

Mandated `scripts/dev.py check-register-statics` run first, per rule
0031: `lib_debug_locks.c` listed in the report's Dead section
(`tmp/fabricated-register-statics-report.md`), not the 190-file live
set. Corroborated by grep: no `get_current()` function defined or
called anywhere in the 464-line raw TU — the "no accessor synthesized
at all" branch of rule 0031, same as `bucket_locks.c`/`errseq.c`, not
the `klist.c`/`is_single_threaded.c` live-via-accessor case.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `debug_locks.o` pulled out of a
bundled `obj-y` line shared with `random32.o`, `bust_spinlocks.o`,
`uuid.o`, `bsearch.o`, `lwq.o`, `rcuref.o`, `errseq.o`,
`bucket_locks.o`, etc., swapped for `debug_locks_rs.o` under the
config. Raw transpile copied to `lib/debug_locks_rs.rs` verbatim as the
starting point.

Cleanest structural shape of the series alongside `bucket_locks.c`/
`glob.c`: `unsafe {}` already wrapping every function body, no
`.init_array`/`__UNIQUE_ID_addressable_*` constructor trick, no
`c2rust_bitfields::BitfieldStruct` derive at all (the raw output's only
`task_struct` is already a plain zero-field opaque struct — no header
chain here pulls in the bitfield-carrying version), no `::libc::`
calls, no `LIST_POISON`, no parameter/type namespace collision.

Fixes actually needed:
- Leftover `#![feature(label_break_value)]` — stripped, same
  per-file-varying leftover class every file since `timerqueue.c` has
  hit some subset of.
- `#[export]` → `#[no_mangle]` on `debug_locks_off` (its one source
  site is `EXPORT_SYMBOL_GPL`, no licensing judgement call), unused
  `use ::macros::export;` import dropped. `debug_locks`/
  `debug_locks_silent` needed no equivalent fix — c2rust already emits
  plain data statics with `#[no_mangle]` for `EXPORT_SYMBOL`'d
  variables (as opposed to functions), correctly paired with their own
  `global_asm!` `.export_symbol` blocks; the `#[export]` class only
  ever applies to functions.
- Unused `use ::kernel::warn_on;` import — dropped (grep-confirmed zero
  `warn_on!` call sites in this TU; c2rust emits the import
  unconditionally regardless of use, not itself one of the tracked gap
  classes, just dead-import cleanup).
- Register-variable pseudo-globals (`riscv_current_is_tp`,
  `current_stack_pointer`): both grep-confirmed dead (declaration only,
  no `get_current()` accessor anywhere in the TU to call them through),
  matching the mechanized check's verdict above. Deleted outright.
  `task_struct` (now fully unreferenced with `riscv_current_is_tp`
  gone) left in place as harmless dead furniture under the file's
  `#![allow(dead_code)]`, same disposition as every prior file's
  non-bitfield dead structs.
- Issue #34's `asm goto(ALTERNATIVE(...))` fallback-extern gap
  recurred, a second confirmation after `errseq.c`: `__debug_locks_off`'s
  c2rust-translated `cmpxchg`-style RMW on `debug_locks` unrolls a
  4-width `match ::core::mem::size_of::<c_int>()` (only the `4 =>` arm
  is dynamically reachable, but all arms compile and reach link time —
  the match isn't const-folded away since it isn't provably dead at
  the MIR level), and the size-1/size-2 arms' Zabha-gated
  `amoswap.b`/`.h` fast paths guard on `riscv_has_extension_unlikely()`
  → the untranslatable `__riscv_has_extension_unlikely()` `asm
  goto(ALTERNATIVE(...))` C function, which c2rust falls back to a
  bare `extern "C"` declaration for with no real linkable symbol
  anywhere in the kernel. Fixed identically to `errseq.c`: routed
  `riscv_has_extension_unlikely()` through the real, already-linkable
  `__riscv_isa_extension_available(NULL, ext)` (c2rust had already
  emitted a correct declaration for it, unused until now), removed the
  now-dead `__riscv_has_extension_unlikely` extern declaration.
  Confirms issue #34 is not `errseq.c`-specific: any file whose
  c2rust-translated `cmpxchg()`/`xchg()` keeps a Zacas/Zabha fast-path
  guard alive to link time (rather than being deleted outright, as the
  pre-`04312ea1ff7e` files in this series did) will hit it.
- Issue #29's bracket-addressing finding recurred, a sixth
  confirmation: 8 occurrences of `[{N}]`/`[{N:}]` across
  `amoswap.{b,h,w,d}.aqrl` and their LR/SC fallback templates (all 4
  width arms, same shape `objpool.c`'s `cmpxchg()` hit for `amocas`).
  Fixed mechanically: `[{N}]`/`[{N:}]` → `0({N})`/`0({N:})` throughout,
  via the same small Python regex pass `errseq.c`'s fix used. No Zabha
  fast-path deletion needed this time (unlike the pre-`04312ea1ff7e`
  files) — `KBUILD_RUSTFLAGS` now supports Zacas/Zabha directly, so the
  `amoswap.b`/`.h` arms compile as-is once the addressing syntax and
  the asm-goto fallback above are both fixed.

No new gap class found — every fix here is a recurrence of an already-
filed/already-documented pattern (rule 0031's dead branch, `#[export]`,
leftover feature line, issue #29 bracket addressing, issue #34 asm-goto
fallback).

### Outcome: clean boot, thirteenth file to clear the bar

- `make ARCH=riscv LLVM=1 lib/debug_locks_rs.o` — clean, 0 errors (38
  warnings, all missing-doc lints, pre-existing pattern).
- `make ARCH=riscv LLVM=1 -j32` (`dev.py build`) succeeds,
  `arch/riscv/boot/Image` produced. `lib/debug_locks.o` correctly never
  built (no `.debug_locks.o.cmd` present).
- `llvm-nm`: all 3 symbols defined in both `lib/debug_locks_rs.o` and
  `vmlinux` — `debug_locks` (`D`, `ffffffff808892d8`), `debug_locks_off`
  (`T`, `ffffffff801606f2`), `debug_locks_silent` (`B`,
  `ffffffff808c85d0`). `riscv_current_is_tp`/`current_stack_pointer`/
  `__riscv_has_extension_unlikely` absent from both objects entirely
  (confirmed via `llvm-nm` grep, empty result both places).
- `dev.py boot --run-id combined-c2rust-boot-12`: boots clean, 17/17
  KUnit suites pass (`fail:0` every suite, 0 `not ok`), `initramfs init
  reached, PID 1 alive` confirms INIT REACHED, no panic/oops/BUG/WARN in
  the log. Archived at
  `docs/status/boot-logs/20260719T140731+1000-combined-c2rust-boot-12.log`.
  Boot-history row committed/pushed automatically by `boot_qemu.py`
  (`b5a8b28`). No dedicated debug_locks KUnit suite in this kernel
  config, so this run demonstrates linking and booting cleanly, not a
  runtime correctness check of the lock-debugging on/off logic itself —
  and since `debug_locks`/`debug_locks_silent` default to `1`/`0` and
  nothing in this project's minimal boot path calls `debug_locks_off()`
  or trips a locking bug, the function's actual behavior isn't
  exercised by this run either.

Thirteenth file to clear the bar. Smaller than every file in the series
except `bust_spinlocks.c` (26 lines), but the smallest one with a
genuinely multi-symbol export surface: 2 data statics plus 1 function,
all three separately `EXPORT_SYMBOL_GPL`'d and separately verified in
`vmlinux`. First file to combine issue #34 (asm-goto fallback) with
issue #29 (bracket addressing) on the *same* underlying RMW without
needing a Zabha-arm deletion, now that both intermediate gaps are fixed
independently — the two fixes stack cleanly rather than one subsuming
the other, confirming both remain distinct, separately-required steps
going forward for any file exercising this kernel's Zabha-gated
sub-word atomics.

## Fourteenth candidate: `lib/devmem_is_allowed.c`

Worktree `combined-c2rust-boot-15`, branch `agent-combined-c2rust-boot-15`,
based on `linux-rs/phase2-gcd`. Target: `devmem_is_allowed()`, the sole
function, not `EXPORT_SYMBOL`'d (plain internal `extern`, declared in
`include/linux/io.h`, called only from `drivers/char/mem.c`'s `/dev/mem`
read/write path), 8 lines of real logic, 28 lines of C total. c2rust
binary built from local branch `register-var-accessor-fix` (`b1f01a3d2`,
one commit ahead of master `6065eaf19`, not yet merged — the
in-progress fix for issue #22/rule 0031's fabricated-register-static
class). Confirmed this doesn't affect the baseline here: that fix only
rewrites accessors whose entire body is `return <register-var>;`
(`get_current()`-shaped), and this TU has no such accessor at all (see
below), so its output is unchanged regardless of which side of the fix
the binary was built from — verified via
`investigate_c2rust_failure.py --rerun`: `outcome=clean`, `returncode=0`,
byte-identical (`diff -q`) to the pre-existing
`tmp/c2rust-baseline/lib_devmem_is_allowed.c/` output.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `devmem_is_allowed.o` had its own
dedicated `obj-$(CONFIG_GENERIC_LIB_DEVMEM_IS_ALLOWED)` line (not
bundled with others, unlike most prior files), swapped for a conditional
`devmem_is_allowed_rs.o`/`devmem_is_allowed.o` pair under the new
config. `CONFIG_GENERIC_LIB_DEVMEM_IS_ALLOWED=y` already set in this
worktree's `.config` (unconditionally `select`ed by `arch/riscv/Kconfig`),
confirming the file is genuinely linked into the combined image already.

`python3 scripts/dev.py check-register-statics` run first per the
mandatory rule 0031 check: `lib_devmem_is_allowed.c` did not appear in
either the LIVE or dead list. Grep-confirmed directly — no `get_current()`
accessor is synthesized anywhere in the TU (0 occurrences), so the two
fabricated statics `riscv_current_is_tp`/`current_stack_pointer` are
present but definitionally unreachable, matching the
`bucket_locks.c`/`errseq.c`/`debug_locks.c`/`bust_spinlocks.c` "no
accessor synthesized at all" branch of rule 0031, not the
`klist.c`/`is_single_threaded.c` live-via-accessor case. Both deleted
outright.

Cleanest structural shape of the series, alongside `uuid.c`: no
`#![feature(...)]` line at all, `unsafe {}` already wrapping the
function's one body, no `.init_array`/`__UNIQUE_ID_addressable_*`
constructor trick (no `EXPORT_SYMBOL` in the C source, consistent with
`is_single_threaded.c`/`bust_spinlocks.c`'s finding), no `#[export]`/
`::macros::export` (already plain `#[no_mangle]`), no `::libc::` calls,
no RISC-V inline-asm (`devmem_is_allowed()`'s body is two plain function
calls and a comparison, nothing atomic or cmpxchg-shaped), no `warn_on!`,
no `LIST_POISON`, no parameter/type namespace collision.

The one real gap: **5 dead `BitfieldStruct`-derived structs**
(`task_struct`, `mmap_action`, `percpu_ref_data`, `signal_struct`,
`sched_dl_entity`) pulled in transitively via headers this TU never
touches — same struct set `bucket_locks.c` hit. Traced the by-value
dependency graph (per the `lzo1x_decompress_safe.c`/`glob.c` lesson)
rather than assuming a flat leaf-node set: `mmap_action` is embedded
by-value inside `vm_area_desc` (a plain, non-`BitfieldStruct` struct),
and grep confirmed `vm_area_desc` itself is never used by value anywhere
in the TU (only ever `*mut vm_area_desc`, a function-pointer parameter
type) — so `vm_area_desc` joins the opaque set too, real leaf count 6,
not 5. `task_struct`/`percpu_ref_data`/`signal_struct` are pointer-only
throughout (13/1/1 occurrences respectively, all `*mut`/`*const`);
`sched_dl_entity` appears by-value exactly once, embedded inside the
also-dead `task_struct`. All 6 converted to the standard zero-field
`opaque_marker!` idiom in one macro invocation (opaquing `mmap_action`
directly rather than deleting it outright, matching `bust_spinlocks.c`'s
approach — simpler than tracing which downstream union/const furniture
would go dangling, since the marker macro just replaces the whole
definition regardless of derive).

No new gap class found — every fix here is a recurrence of an
already-documented pattern (rule 0031's dead branch, transitive
`BitfieldStruct` by-value tracing).

### Outcome: clean boot, fourteenth file to clear the bar

- `make ARCH=riscv LLVM=1 lib/devmem_is_allowed_rs.o` — clean, 0 errors
  (1617 warnings, all missing-doc lints, pre-existing pattern).
- `make ARCH=riscv LLVM=1 -j32` (`dev.py build`) succeeds,
  `arch/riscv/boot/Image` produced. `lib/devmem_is_allowed.o` correctly
  never built (no `.devmem_is_allowed.o.cmd` present).
- `llvm-nm`: `devmem_is_allowed` defined (`T`) in both
  `lib/devmem_is_allowed_rs.o` and `vmlinux`
  (`ffffffff801863e2 T devmem_is_allowed`); `riscv_current_is_tp`/
  `current_stack_pointer` absent from both objects entirely (confirmed
  via `llvm-nm` grep, empty result both places).
- `dev.py boot --run-id combined-c2rust-boot-15`: boots clean, 17/17
  KUnit suites pass (`fail:0` every suite, 0 `not ok`), `INIT REACHED
  (initramfs userspace boot verified)`, no panic/oops/BUG/WARN in the
  log. Archived at
  `docs/status/boot-logs/20260719T145529+1000-combined-c2rust-boot-15.log`.
  Boot-history row committed/pushed automatically by `boot_qemu.py`
  (`fa50e6a`). No dedicated devmem_is_allowed KUnit suite in this kernel
  config, and `devmem_is_allowed()` is only ever called from
  `/dev/mem`'s read/write path, not exercised by this project's minimal
  boot — this run demonstrates linking and booting cleanly, not a
  runtime correctness check of the RAM/MMIO exclusivity logic itself.

Fourteenth file to clear the bar. Smallest real-logic body of the
series (3 statements), tied with `bust_spinlocks.c`/`uuid.c` for the
minimal-fix-scope tier — one gap class only (`BitfieldStruct` opaquing,
with the same transitive-tracing nuance `glob.c` and
`lzo1x_decompress_safe.c` already established), zero feature-line
strips, zero `#[export]` handling, zero RISC-V inline-asm gaps, zero
register-variable-liveness surprises. Also the first file boot-tested
against a c2rust binary built from an unmerged local branch
(`register-var-accessor-fix`, targeting issue #22) rather than master
directly — confirmed immaterial to this file's output since the fix
only touches `get_current()`-shaped accessors and this TU has none, but
worth flagging the provenance explicitly per the task's freshness-check
requirement rather than silently treating "some c2rust binary" as
interchangeable with "master's c2rust binary."

## Fifteenth candidate: `lib/tests/test_sort.c`

Worktree `combined-c2rust-boot-16`, branch `agent-combined-c2rust-boot-16`,
based on `linux-rs/phase2-gcd`. c2rust binary at `6065eaf19` (current
master; supersedes the `raw_ref_op`/`strict_provenance` stale-feature
fix cited in this doc's 2026-07-19 update). Fresh transpile via
`c2rust-baseline`, `tmp/c2rust-baseline/lib_tests_test_sort.c/output/src/test_sort.rs`
(4370 lines raw).

Structurally different from every prior candidate: `test_sort.c` is
itself a KUnit test suite, not a plain `lib/` utility. Gated by
`obj-$(CONFIG_TEST_SORT) += test_sort.o` (`config TEST_SORT`, `tristate
... depends on KUNIT, default KUNIT_ALL_TESTS`, `=y` in this project's
`.config`), not unconditional `obj-y`. Registers `sort_test_suite`
(name `"lib_sort"`, one case `test_sort`) via
`kunit_test_suites(&sort_test_suite)`, which lowers to a
`#[used] #[link_section = ".kunit_test_suites"]` static pointer array —
the linker collects these across every KUnit TU into one section, the
KUnit executor walks it at runtime, and picks up each suite purely by
array position. No `EXPORT_SYMBOL`, no external caller by name at all;
`test_sort`/`cmpint` stay internal-linkage. Because of this, c2rust's
raw output already carries plain Rust-mangled symbol names for both
functions (no `#[no_mangle]`, no `#[export]`/`::macros::export`) — this
is *correct* here, not a bug to fix, since nothing outside the TU ever
resolves them by name. Confirmed via `dev.py boot`'s KUnit summary line
`ok 16 lib_sort` (with `ok 1 test_sort` inside it) both before and after
this file's Rust wiring — same suite/case name, same position in the
KTAP tree, sitting immediately after `list_sort` (suite 15) and before
`rust_8250_mem_serial_io` (suite 17) in every run.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, `default n`), independent of every other worktree's copy.
`lib/tests/Makefile`: wrapped the existing
`obj-$(CONFIG_TEST_SORT) += test_sort.o` line in an `ifdef
CONFIG_RUST_C2RUST_BOOT_TEST` / `else` swap for `test_sort_rs.o` —
`CONFIG_TEST_SORT` itself stays the real gate (already `y`), the new
symbol only selects which object satisfies it, same pattern as every
prior file's Makefile edit, adapted for a `tristate`-gated `obj-$(...)`
line rather than a bare `obj-y`. Raw transpile copied to
`lib/tests/test_sort_rs.rs` verbatim as the starting point.

`python3 scripts/dev.py check-register-statics` run first per rule
0031: `lib_tests_test_sort.c` in the report's Dead section (274 total,
11 live), corroborated by grep — 0 `get_current()` calls anywhere in
the TU, the two fabricated statics (`riscv_current_is_tp`,
`current_stack_pointer`) present but definitionally unreachable.
Deleted both outright.

Cleanest structural shape of the series alongside `bust_spinlocks.c`/
`debug_locks.c`: no `#![feature(...)]` line at all, `unsafe {}` already
wrapping every function body, no `.init_array`/
`__UNIQUE_ID_addressable_*` constructor trick (this file's real
`#[used] #[link_section = ".kunit_test_suites"]` registration array is
the genuine mechanism, not the dead constructor-trick c2rust emits for
`EXPORT_SYMBOL`'d functions — nothing to strip here), no `#[export]`,
no `::libc::` calls, no RISC-V inline-asm. One gap already known: 7
dead `BitfieldStruct`-derived structs (`task_struct`, `mmap_action`,
`kobject`, `kernfs_open_file`, `percpu_ref_data`, `signal_struct`,
`sched_dl_entity`) pulled in transitively via `task_struct`'s header
chain — grep-confirmed zero references anywhere in the two live
function bodies (`test_sort`, `cmpint`), and the two by-value embeddings
(`sched_dl_entity` inside `task_struct`; `mmap_action` inside
`vm_area_desc`, itself pointer-only) both trace to already-dead
containers, so the whole 7-struct subgraph collapses cleanly (same
"whole chain dead, no per-container load-bearing check needed" shape
`bust_spinlocks.c` hit). All 7 converted to the standard zero-field
`opaque_marker!` idiom in one macro invocation.

### New gap class: literal C signed-overflow arithmetic panics under Rust's default overflow checks

The `.o` built clean and the full kernel linked, but the first boot
(`combined-c2rust-boot-16`, pre-fix) stopped dead after suite 15
(`list_sort`) — `lib_sort` never completed, no `INIT REACHED`, only
15/17 suites in the KTAP summary. Raw log:
`rust_kernel: panicked at lib/tests/test_sort_rs.rs:3759:17: attempt to
multiply with overflow`, followed by a real riscv `Kernel BUG` trap and
`Kernel panic - not syncing: Fatal exception in interrupt` — this is a
genuine runtime crash, not a benign warning; the whole boot dies at
exactly the point `test_sort` itself runs.

Root cause: the C source's test-data generator,
`r = (r * 725861) % 6599;` (run ~2000 times total across the function's
two loops), relies on `int` multiplication overflowing and wrapping —
`r` ranges 0-6598, so `r * 725861` exceeds `INT_MAX` on the very second
iteration (confirmed: `6598 * 725861 ≈ 4.79e9`, more than double
`i32::MAX`). This is implementation-defined/UB-on-paper C, but every
real compiler (including this kernel's) wraps silently in two's
complement, and the test only needs the wrapped value's statistical
scatter to build non-trivial sort input — the overflow is load-bearing,
not accidental. c2rust translated the multiply literally as Rust `*`,
which is checked-by-default and panics on overflow in a debug-flagged
build (this kernel's Rust build carries overflow checks on). Fixed by
replacing both occurrences of `r * 725861 as ::core::ffi::c_int` with
`r.wrapping_mul(725861 as ::core::ffi::c_int)` — semantically identical
to C's actual (wrapping) runtime behavior, and avoids `#![feature(...)]`
or build-flag changes entirely. Scanned the rest of the TU's two live
function bodies for other unguarded arithmetic that could plausibly
overflow (index arithmetic, `TEST_LEN`-bounded loop counters,
`cmpint`'s `a - b` over the same 0-6598 range) — none do; this was the
only site. First file in the series where a KUnit suite's own **test
logic**, not just linkage/build plumbing, needed a semantic fix — every
prior gap class was structural (features, unsafe-wrapping, dead
structs, symbol linkage); this one is the first correctness bug in
translated *runtime arithmetic behavior*, worth tracking as its own
class for future c2rust-translated files with C-idiom pseudo-random or
hash-like generators that lean on overflow wraparound.

### Outcome: clean boot + matching KUnit pass count, fifteenth file to clear the bar

- `make ARCH=riscv LLVM=1 lib/tests/test_sort_rs.o` — clean, 0 errors
  (1994 warnings, all missing-doc lints, pre-existing pattern).
- `make ARCH=riscv LLVM=1 -j32` (`dev.py build`) succeeds,
  `arch/riscv/boot/Image` produced. `lib/tests/built-in.a` confirmed via
  `llvm-ar t` to contain `test_sort_rs.o`, not `test_sort.o`.
- `llvm-nm`: `_RNvCs32tGb0XMFDa_12test_sort_rs9test_sort` (Rust-mangled,
  correct per the internal-linkage analysis above) and its sibling
  `cmpint`/`sort_test_suite`/`sort_test_cases` symbols all present in
  both `lib/tests/test_sort_rs.o` and `vmlinux`; `riscv_current_is_tp`/
  `current_stack_pointer` absent from both objects entirely (confirmed
  via `llvm-nm` grep, empty result both places).
- `dev.py boot --run-id combined-c2rust-boot-16` (post-fix run): boots
  clean, **17/17 KUnit suites pass** (`ORACLE PASS (17 suites)`, 0 real
  `not ok` lines), `INIT REACHED (initramfs userspace boot verified)`,
  no panic/oops/BUG/WARN in the log. Archived at
  `docs/status/boot-logs/20260719T145820+1000-combined-c2rust-boot-16.log`.
  Boot-history rows committed/pushed automatically by `boot_qemu.py`
  for both the failing pre-fix run (`f21f168`, 15 ok/0 not ok, no INIT
  REACHED) and the passing post-fix run (`414c0de`, 17 ok/0 not ok,
  INIT REACHED) — kept as-is per this project's rule against
  hand-editing generated history.
- **Correctness signal specific to this file**: `lib_sort`'s KTAP output
  is identical before/after the swap — `# Subtest: lib_sort` / `1..1` /
  `ok 1 test_sort` / `ok 16 lib_sort`, same suite name, same case name,
  same position, same pass count as the pre-existing plain-C build.
  This is a stronger signal than the `nm`-only check every non-test
  file in this series has relied on: the Rust translation was actually
  *executed* by the KUnit runner and produced the same pass/fail
  verdict as the reference C implementation, not just linked and never
  exercised.

Fifteenth file to clear the bar, and the first with a genuinely
different linkage shape (internal-linkage KUnit suite registration via
a linker-collected section array, not `EXPORT_SYMBOL`/`#[no_mangle]`)
and the first to surface a real runtime-behavior bug (integer-overflow
panic) rather than a purely structural/build-time one. Confirms the
established gap classes (rule 0031's dead branch, `BitfieldStruct`
opaquing) still apply unchanged to KUnit test files, but that test
files pull in a new risk category plain utility files don't: C idioms
that intentionally rely on overflow wraparound for pseudo-random test
data generation, which c2rust's literal-operator translation turns into
a hard panic under Rust's default overflow checks.

## Sixteenth candidate: `lib/seq_buf.c`

Worktree `combined-c2rust-boot-17`, branch `agent-combined-c2rust-boot-17`,
based on `linux-rs/phase2-gcd`. Target: all 5 `EXPORT_SYMBOL_GPL`
functions (`seq_buf_printf`, `seq_buf_do_printk`, `seq_buf_puts`,
`seq_buf_putc`, `seq_buf_putmem_hex`), 119 statements (`dev.py readiness
"lib/*.c"`), the largest file attempted in this series so far —
string-buffer-append infrastructure used by tracing/`seq_file`. c2rust
binary at `6065eaf19` (current master at start of this run; rebuilt
14:43, after master's 12:15 merge, confirmed no newer commit landed
mid-task). Fresh transpile via `investigate_c2rust_failure.py --rerun`:
`outcome=clean`, byte-identical (`diff -q`) to the pre-existing
`tmp/c2rust-baseline/lib_seq_buf.c/` output — no re-transpile needed.

`python3 scripts/dev.py check-register-statics` run first per rule 0031:
`lib_seq_buf.c` not in the 11-file live set (drivers_base_core.c,
fs_file.c, fs_select.c, init_do_mounts.c, kernel_fork.c, kernel_pid.c,
kernel_sched_core.c, lib_strncpy_from_user.c, lib_strnlen_user.c,
lib_usercopy.c, lib_vsprintf.c), present only in the dead/safe set.
Corroborated by grep: no `get_current()` accessor defined anywhere in
the TU — the "no accessor synthesized at all" branch of rule 0031, same
as `bucket_locks.c`/`debug_locks.c`. `riscv_current_is_tp`/
`current_stack_pointer` both declaration-only, deleted outright.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile`: `seq_buf.o` pulled out of the bundled
`lib-y` line, swapped for `seq_buf_rs.o` (plus a second new object, see
below) under the config.

Known classes confirmed present: `#[export]` -> `#[no_mangle]` on all 5
functions (`use ::macros::export;` import dropped); 4 dead
`BitfieldStruct`-derived structs (`task_struct`, `mmap_action`,
`signal_struct`, `sched_dl_entity`) traced and opaqued — `sched_dl_entity`
embedded by-value inside the also-pointer-only `task_struct`,
`mmap_action` embedded by-value inside `vm_area_desc`, itself only ever
`*mut`, same collapse-to-dead-by-construction shape `bust_spinlocks.c`/
`glob.c` established; `kernel::warn_on!(cond) != 0` recurred at all 3
sites where c2rust added the comparison (the other 7 `warn_on!` call
sites in this file are bare statements with no comparison, already
correct), fixed by dropping `!= 0`, same as `rcuref.c`/`errseq.c`; unused
`use ::kernel::warn_on;` import dropped, same dead-import class
`debug_locks.c` established. No RISC-V inline-asm gaps (no
`amocas`/`amoswap`/`cmpxchg` anywhere in the TU). No `.init_array` trick.

### New gap class: `c_variadic`/`core::ffi::VaList` — unstable, disallowed, and has no stable substitute for *defining* a variadic function (filed as issue #37)

`seq_buf_printf(struct seq_buf *s, const char *fmt, ...)` is a genuine
C-variadic function, `EXPORT_SYMBOL_GPL`'d with ~38 real call sites
across the tree (`kernel/panic.c`, `kernel/trace/trace_seq.c`,
`lib/alloc_tag.c`, etc.). c2rust translated it, plus `seq_buf_vprintf`'s
`va_list` parameter and the `vsnprintf()` extern declaration, using
Rust's `#![feature(c_variadic)]` and `core::ffi::VaList` — both
unstable, zero overlap with `rust_allowed_features`
(`E0725`), and additionally rejected outright even with the feature
line present (`E0658: C-variadic functions are unstable`,
`E0658: use of unstable library feature c_variadic` x3) since the
compiler itself refuses these without the disallowed flag. Unlike
`raw_ref_op`/`extern_types`/`strict_provenance`, there is no stable
Rust syntax for *defining* a C-variadic function at all — this is a
hard structural gap, not a strip-the-feature-line fix.

c2rust's own output already contained the actual fix material:
`pub type __builtin_va_list = *mut ::core::ffi::c_void;` (line 85),
matching real bindgen's own representation of C's `va_list`
(`rust/bindings/bindings_generated.rs`: `pub type va_list =
__builtin_va_list;` / `pub type __builtin_va_list = *mut ffi::c_void;`
— riscv64's `va_list` is ABI-defined as a single pointer, not a struct
like x86-64 SysV) — c2rust just didn't use it consistently, emitting
`core::ffi::VaList` for `seq_buf_vprintf`'s parameter and the
`vsnprintf` extern instead of its own already-declared
`__builtin_va_list` alias. Fixed by: (1) retyping `seq_buf_vprintf`'s
`args` parameter and the `vsnprintf` extern's `args` parameter from
`core::ffi::VaList` to `__builtin_va_list`, dropping the now-unneeded
`.as_va_list()` calls; (2) deleting the Rust `seq_buf_printf` function
and its `global_asm!` `.export_symbol` block entirely; (3) adding a new
2-object-file `lib/seq_buf_rs_shim.c` carrying just `seq_buf_printf`'s
real variadic entry point (`va_start`/`va_end`, forwards to
`seq_buf_vprintf` — mirrors the original C function line-for-line) and
its `EXPORT_SYMBOL_GPL`; (4) adding an `extern "C" { fn seq_buf_printf(
..., ...) -> c_int; }` declaration back into `seq_buf_rs.rs`, needed
because `seq_buf_hex_dump()` itself calls `seq_buf_printf()` at 3 call
sites — a variadic *call site* needs no unstable feature (same as the
`_printk(...)` extern call c2rust's own output already declares
unmodified), only a variadic *definition* does, so this direction of
the split works with zero feature-gate friction. `lib/Makefile` builds
both `seq_buf_rs.o` and `seq_buf_rs_shim.o` under
`CONFIG_RUST_C2RUST_BOOT_TEST`. First file in the series needing a
second, hand-written non-generated `.c` file alongside the c2rust
output rather than a pure in-place Rust edit.

### New gap class: `-1 as usize` — invalid syntax from a `__builtin_object_size` dead-branch translation (filed as issue #38, 50 corpus files affected)

`check_copy_size()`'s c2rust-translated `__builtin_object_size()`
compile-time-constant idiom produced `(if <always-true-const> { -1 as
usize } else { 0 as usize })` — `-1 as usize` doesn't parse in Rust
(`E0600`: unary `-` not valid on an unsigned type), unlike C where
`(size_t)-1` is well-defined two's-complement wraparound. Fixed by
substituting `usize::MAX`, the bit-identical value C's `(size_t)-1`
represents — same "always-true dead branch, fix the value not the
control flow" shape `errseq.c`'s `ilog2`/`variable_fls` finding already
established, but this is the first file where the dead branch's own
literal syntax (not a downstream unresolved symbol) was the actual
break.

### Outcome: clean boot, sixteenth file to clear the bar, largest file of the series so far

- `make ARCH=riscv LLVM=1 lib/seq_buf_rs.o lib/seq_buf_rs_shim.o` —
  clean, 0 errors (1583 warnings, all missing-doc/FFI-safety-lint noise,
  pre-existing pattern — the 2 `improper_ctypes` warnings on
  `lock_class_key`/`arch_spinlock_t` through `d_path`/`seq_write`'s
  extern params are zero-sized-struct false positives, cosmetic, same
  shape every prior file's `extern "C"` blocks already carry).
- `dev.py build` (`LINUXRS_TREE=linux-riscv-worktrees/combined-c2rust-boot-17`)
  succeeds, `arch/riscv/boot/Image` produced. `lib/seq_buf.o` correctly
  never built (absent from the tree).
- `llvm-nm`: all 11 real functions (5 `EXPORT_SYMBOL_GPL` plus 6
  internal-linkage-but-`#[no_mangle]`'d helpers c2rust already emitted
  that way) `T` in both the two new `.o`s and `vmlinux`
  (`ffffffff801e1e6c T seq_buf_printf`, `ffffffff801e18f2 T
  seq_buf_do_printk`, `ffffffff801e1d50 T seq_buf_puts`,
  `ffffffff801e1be2 T seq_buf_putc`, `ffffffff801e1c60 T
  seq_buf_putmem_hex`, plus `seq_buf_vprintf`/`seq_buf_putmem`/
  `seq_buf_path`/`seq_buf_to_user`/`seq_buf_hex_dump`/
  `seq_buf_print_seq`). `seq_buf_printf` correctly `U` (undefined) in
  `lib/seq_buf_rs.o` and `T` (defined) in `lib/seq_buf_rs_shim.o`;
  `seq_buf_vprintf` the mirror image (`T` in the Rust object, `U` in the
  shim) — confirms the two-object split links both directions cleanly.
  `riscv_current_is_tp`/`current_stack_pointer` absent from `vmlinux`
  entirely (0 occurrences via grep).
- `dev.py boot --run-id combined-c2rust-boot-17`: boots clean, **17/17
  KUnit suites pass** (`ORACLE PASS (17 suites)`, 0 `not ok`),
  `INIT REACHED (initramfs userspace boot verified)`, no panic/oops/
  BUG/WARN in the log (the only "panic" string match is the
  `panic=-1` boot command-line argument, not a real panic event).
  Archived at
  `docs/status/boot-logs/20260719T150017+1000-combined-c2rust-boot-17.log`.
  Boot-history row committed/pushed automatically by `dev.py boot`
  (`a0ecd1d`). `CONFIG_SEQ_BUF_KUNIT_TEST` is not set in this config, so
  `seq_buf_printf`/`seq_buf_vprintf`'s actual formatting logic wasn't
  runtime-exercised by this run (same caveat as every prior file
  without a dedicated enabled suite) — this run demonstrates the
  variadic-ABI split links and boots correctly, not that the
  printf-format-string logic itself matches the C reference output for
  known inputs.

Largest file of the series (119 statements) needed the most distinct
fix classes of any file so far (6: `#[export]`, `BitfieldStruct`
opaquing x4-struct chain, `warn_on! != 0`, dead-import cleanup,
register-static deletion, plus the 2 new ones below) but every
previously-known class recurred exactly as documented — confirms the
series' fixes scale to a 2-3x-larger file without needing a
qualitatively different approach, aside from the two genuinely new
classes: the `c_variadic`/`VaList` gap (a hard language-level wall, not
a strip-the-line fix, resolved by a C-shim split rather than an
in-place Rust edit — the first file in the series needing one) and the
`-1 as usize` literal-syntax break in a `__builtin_object_size`
dead-branch translation.

## Seventeenth candidate: `lib/kfifo.c`

Worktree `combined-c2rust-boot-19`, branch `agent-combined-c2rust-boot-19`,
based on `linux-rs/phase2-gcd`. Target: lock-free circular-buffer FIFO,
264 statements, largest file of the series by a wide margin (previous
largest was `seq_buf.c` at 119). c2rust binary rebuilt via `dev.py
c2rust-build` immediately before starting (rev `1f8c61cf2`, newer than
`6065eaf19` cited in the `seq_buf.c` section — confirms fresh master,
no repeat of the stale-worker-binary false alarm from awtoau/c2rust#26).
Fresh transpile via `investigate_c2rust_failure.py --rerun`:
`outcome=clean`, byte-identical (`diff -q`) to the pre-existing
`tmp/c2rust-baseline/lib_kfifo.c/` output.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n), independent of every other worktree's copy.
`lib/Makefile`: `kfifo.o` pulled out of the bundled `obj-y +=
debug_locks.o random32.o ... bsearch.o lwq.o kfifo.o ...` line, swapped
for a conditional `kfifo_rs.o`/`kfifo.o` pair under the new config, same
shape as `bust_spinlocks.c`/`debug_locks.c`'s split out of the same
bundled line. Also enabled `CONFIG_KFIFO_KUNIT_TEST=y` (off by default,
gated on `KUNIT_ALL_TESTS` which is off in this project's `.config`) to
actually exercise the translated logic at runtime, not just link it —
`lib/tests/kfifo_kunit.c` is a real dedicated KUnit suite calling
`kfifo_put`/`kfifo_in`/`kfifo_out`/`kfifo_peek`/`kfifo_alloc` through the
public macro layer.

`python3 scripts/dev.py check-register-statics` run first per rule
0031: 0 live corpus-wide (265 dead, 0 live) — confirms issue #22's fix
eliminated every previously-live case as the brief predicted; no
regression. `lib_kfifo.c` in the dead/safe set, corroborated by grep (0
`get_current()` accessors in the TU). Both fabricated statics deleted
outright. Grep for `-1 as usize`: zero hits — confirms issue #38's fix
covers this TU too (its one `__builtin_object_size` dead branch,
inherited via `check_copy_size()`, already emits
`(1 as usize).wrapping_neg()`), no regression.

**Index/wraparound arithmetic — the file-specific risk flagged going
in — was already fully correct in the raw c2rust output.** Read every
one of the 17 `EXPORT_SYMBOL`'d functions plus their `kfifo_copy_in`/
`kfifo_copy_out`/`kfifo_copy_from_user`/`kfifo_copy_to_user`/
`setup_sgl`/`__kfifo_peek_n`/`__kfifo_poke_n` helpers end to end:
every `fifo->in`/`fifo->out` `+=`/`-=`/comparison (the deliberately
wrapping-past-`UINT_MAX` counters `kfifo_unused()`'s header comment and
`include/linux/kfifo.h`'s mask-based indexing rely on) is already
`wrapping_add`/`wrapping_sub` in the generated Rust, including the
record-mode (`_r` suffix) functions' `len + recsize` additions. This is
rule 0009 (`is_unsigned_integral_type()` → `wrapping_*`) doing exactly
what it's for — `in`/`out`/`mask`/`size`/`len`/`recsize` are all
`unsigned int` or `size_t` in the C source, so every site lands on the
already-covered unsigned path, not the signed gap scoped in
`docs/overflow-wraparound-detection-scoping-2026-07-19.md`. No
`refcount_t`/`__refcount_add`/`__refcount_sub_and_test` pattern present
either (`grep` confirms zero — this TU doesn't pull in `refcount.h`'s
counter logic). **Not the same bug class as #36/#39 recurring, and not
a new class** — simply a file where every arithmetic site happened to
already be unsigned-typed, so the existing fix fully covers it.
Confirmed at runtime too: booted clean with `KFIFO_KUNIT_TEST=y`, all
10 `kfifo` KUnit cases pass, zero panics.

Two known gap classes recurred: `kernel::warn_on!(bytes > ...) != 0` in
`check_copy_size()` (the one `warn_on!` call site in this TU), fixed by
dropping `!= 0`, same as `rcuref.c`/`errseq.c`/`seq_buf.c`; the
resulting unused `use ::kernel::warn_on;` import (macro invoked fully
qualified as `kernel::warn_on!`) dropped, same dead-import class
`debug_locks.c`/`seq_buf.c` established. 7 dead `BitfieldStruct`-derived
structs pulled in transitively via `task_struct`'s header chain
(`task_struct`, `mmap_action`, `kobject`, `kernfs_open_file`,
`percpu_ref_data`, `signal_struct`, `sched_dl_entity` — same set
`test_sort.c` hit plus `kobject`/`kernfs_open_file`) — grep-confirmed
zero references to any of the 7 inside this TU's actual function bodies
(`kfifo_unused` through `__kfifo_dma_out_prepare_r`), whole chain dead
by construction same as `bust_spinlocks.c`/`test_sort.c` established,
all 7 converted to the standard `opaque_marker!` idiom in one macro
invocation. No `#[export]`/`::macros::export` (already plain
`#[no_mangle]` on all 17 functions, matching the newer-generation
pattern `test_sort.c`/`seq_buf.c` also showed). No `#![feature(...)]`
line. `__riscv_has_extension_likely`'s asm-goto arch-extension fallback
(issue #34) present in `variable__fls`/`variable_fls` (pulled in via
`fls_long`/`__ilog2_u32` from `roundup_pow_of_two()`) and already
correctly resolved — no `asm goto` gap, matching the already-fixed
pattern.

### Outcome: clean boot + full KUnit suite pass, seventeenth file to clear the bar, largest file of the series so far

- `make ARCH=riscv LLVM=1 lib/kfifo_rs.o` — clean, 0 errors (2015
  warnings, all missing-doc lints, pre-existing pattern).
- `dev.py build` succeeds, `arch/riscv/boot/Image` produced.
  `lib/kfifo.o` correctly never built (no `.kfifo.o.cmd` present in this
  worktree).
- `llvm-nm vmlinux`: all 17 `EXPORT_SYMBOL`'d functions `T` (defined),
  e.g. `ffffffff8015fee4 T __kfifo_alloc_node`,
  `ffffffff80160320 T __kfifo_in`, `ffffffff801605ee T __kfifo_out`.
  `riscv_current_is_tp`/`current_stack_pointer` absent from `vmlinux`
  entirely (0 occurrences).
- `dev.py boot --run-id combined-c2rust-boot-19`: boots clean, **18/18
  KUnit suites pass** (one more suite than the series' usual 17 — the
  newly-enabled `kfifo` suite at position 15, `# kfifo: pass:10 fail:0
  skip:0 total:10`), 0 `not ok`, `INIT REACHED (initramfs userspace boot
  verified)`, no panic/oops/BUG/WARN in the log (only `panic=-1` boot
  cmdline match). Archived at
  `docs/status/boot-logs/20260719T173632+1000-combined-c2rust-boot-19.log`.
  Boot-history row committed/pushed automatically by `dev.py boot`
  (`b288b2a`). Unlike every prior file except `test_sort.c`, this run's
  KUnit pass count is real functional coverage of the translated logic
  itself (`kfifo_put`/`kfifo_in`/`kfifo_out`/`kfifo_peek`/`kfifo_alloc`
  exercising `__kfifo_in`/`__kfifo_out`/`__kfifo_alloc_node`/
  `kfifo_copy_in`/`kfifo_copy_out`/`kfifo_unused` and their wraparound
  index arithmetic directly), not just link-and-boot — deepest
  runtime verification of any file in the series so far.

Largest file of the series (264 statements, 2.2x `seq_buf.c`) but the
smallest *fix* scope relative to size: every gap was a recurrence of an
already-documented class (dead-import, `warn_on! != 0`, `BitfieldStruct`
opaquing, register-static deletion), zero new gap classes, and the
file-specific risk flagged going in (signed/unsigned wraparound on the
`in`/`out` index counters) turned out to be a non-issue on inspection —
every site was already unsigned and already `wrapping_*`. Confirms rule
0009's unsigned-wrap coverage scales cleanly to the densest
pointer/index-arithmetic file attempted yet, and that a 2x+ jump in
statement count doesn't necessarily mean a proportional jump in
hand-fixing — this file needed less *novel* work than several smaller
files earlier in the series (`seq_buf.c`, `errseq.c`) despite being
the largest.

## Eighteenth candidate: `lib/sys_info.c`

Worktree `combined-c2rust-boot-18`, branch `agent-combined-c2rust-boot-18`,
based on `linux-rs/phase2-gcd`. Target: `sys_info_parse_param`,
`sysctl_sys_info_handler`, `sys_info` (3 externally-declared functions
via `include/linux/sys_info.h`, called from `kernel/panic.c`,
`kernel/hung_task.c`, `kernel/watchdog.c` — no `EXPORT_SYMBOL` anywhere
in the file, direct build-time linkage only), 59 statements (`dev.py
readiness "lib/*.c"`), a kernel-state-dump utility. c2rust binary
rebuilt via `dev.py c2rust-build` immediately before starting (fresh,
newer than all tracked sources). Fresh transpile via
`investigate_c2rust_failure.py --rerun`: byte-identical (`diff -q`) to
the pre-existing `tmp/c2rust-baseline/lib_sys_info.c/` output — no
re-transpile needed. Transpile's own compile-commands step logs 2
`-Werror=incompatible-pointer-types-discards-qualifiers` diagnostics on
`char *delim = ""` / `delim = ","` in `sys_info_read_handler` (C's
implicit `const char *` -> `char *` narrowing on string literals,
harmless and pre-existing in the C source itself), transpile still
succeeds and produces full output.

`python3 scripts/dev.py check-register-statics` run first per rule
0031: `lib_sys_info.c` not in the 0-file live set (0/263 corpus-wide,
confirming the #22 fix's corpus-wide effect holds one file further).
Corroborated by grep: no `get_current()` accessor defined or called
anywhere in the TU — the "no accessor synthesized at all" branch,
same as `bucket_locks.c`/`debug_locks.c`/`seq_buf.c`. Both
`riscv_current_is_tp`/`current_stack_pointer` declaration-only, deleted
outright.

Checked for the refcount.h overflow-detection pattern (issue #39,
`wrapping_add`/`wrapping_sub`): `refcount_t`/`refcount_struct` appear
only as struct field type declarations pulled in transitively via
`task_struct` and friends (itself dead, see below) — this file performs
no refcounting of its own, no `__refcount_add`/`refcount_dec_and_test`
call chain present, class doesn't apply here. Checked for `-1 as
usize` (issue #38): 0 occurrences, consistent with the corpus-wide
50-file fix.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n), independent of every other worktree's copy.
`lib/Makefile`: `sys_info.o` pulled out of the bundled `lib-y` line,
swapped for `sys_info_rs.o` under the config. Raw transpile copied to
`lib/sys_info_rs.rs` verbatim as the starting point.

Cleanest structural shape of the series alongside `bust_spinlocks.c`/
`debug_locks.c`/`test_sort.c`: no `#![feature(...)]` line at all
(not even a leftover one), `unsafe {}` already wrapping every function
body, no `.init_array`/`__UNIQUE_ID_addressable_*` constructor trick,
no `#[export]`/`::macros::export` (nothing to convert — the C source
never used `EXPORT_SYMBOL` at all, so c2rust emitted `#[no_mangle]`
directly on all 3 externally-called functions with zero licensing
judgement call needed), no `warn_on! != 0`, no `c_variadic`/`VaList`,
no RISC-V inline-asm gaps touching this file's own logic. One gap
already known: the same 7 dead `BitfieldStruct`-derived structs
`test_sort.c` hit (`task_struct`, `mmap_action`, `kobject`,
`kernfs_open_file`, `percpu_ref_data`, `signal_struct`,
`sched_dl_entity`), pulled in transitively via `task_struct`'s header
chain — grep-confirmed zero references anywhere in the 4 real
(`#[no_mangle]`) function bodies or their 3 internal-linkage helpers
(`sys_info_write_handler`, `sys_info_read_handler`,
`sys_info_sysctl_init`). `sched_dl_entity` embeds by-value inside
`task_struct` (itself dead); `mmap_action` embeds by-value inside
`vm_area_desc`, itself only ever `*mut` — same "whole chain dead"
collapse as every prior occurrence. All 7 converted to the standard
zero-field `opaque_marker!` idiom in one macro invocation.

### New gap class: `__init`'s `__section(".init.text")` silently dropped, only `__cold` survives translation

The `.o` built clean, but `modpost` failed the full kernel link:
`WARNING: modpost: vmlinux: section mismatch in reference:
sys_info_sysctl_init+0x22 (section: .text.unlikely.) ->
__register_sysctl_init (section: .init.text)`, escalated to a hard
`ERROR: modpost: Section mismatches detected` (this tree's modpost
config is not `CONFIG_SECTION_MISMATCH_WARN_ONLY=y`, so this is fatal,
not cosmetic) — first `modpost`-stage failure in the series; every
prior gap was either a `rustc` compile error or a runtime/boot-time
bug.

Root cause: the C source's `sys_info_sysctl_init` is `static int
__init sys_info_sysctl_init(void)`, and `__init` expands
(`include/linux/init.h`) to `__section(".init.text") __cold
__latent_entropy`. c2rust translated `__cold` to `#[cold]` correctly,
but dropped the `__section(".init.text")` component entirely — the
function landed in Rust's own default placement for `#[cold]` functions
(`.text.unlikely.`) instead of the section the macro actually
specifies. The function is only ever reached indirectly, through the
`.initcall4.init`-section function-pointer array c2rust *did* place
correctly (`__initcall__kmod_sys_info__327_136_sys_info_sysctl_init4`),
and it calls `__register_sysctl_init`, itself genuinely `__init`
(`.init.text`, freed after boot) — a cross-section reference from
`.text.unlikely.` (never freed) into `.init.text` (freed) is exactly
the dangling-reference shape `modpost`'s section-mismatch checker
exists to catch, and it caught a real one here, not a false positive.
Fixed by adding `#[link_section = ".init.text"]` back alongside the
existing `#[cold]`, restoring the section GCC's macro expansion always
gave this function. First file in the series where `__init`'s
*section* half (as opposed to its more commonly-checked `__cold`/
inlining-hint half) was the thing c2rust's attribute translation
missed — worth watching for on any future file whose C source declares
a `static … __init` function that survives into the translated output
rather than being fully dead-code-eliminated.

### Outcome: clean boot, seventeenth file to clear the bar

- `make ARCH=riscv LLVM=1 lib/sys_info_rs.o` — clean, 0 errors (2078
  warnings, all missing-doc lints, pre-existing pattern).
- `make ARCH=riscv LLVM=1 -j32` (`dev.py build`) succeeds after the
  `__init` section fix, `arch/riscv/boot/Image` produced. `lib/sys_info.o`
  confirmed absent from the entire tree; `sys_info_rs.o` confirmed
  present in `lib/lib.a` (the `lib-y` archive `sys_info_rs.o` builds
  into, not `lib/built-in.a` directly — `lib-y` objects archive one
  level down from `obj-y`).
- `llvm-objdump -h lib/sys_info_rs.o`: confirms `sys_info_sysctl_init`
  actually sits in `.init.text` post-fix (`4 .init.text 00000034 ...`),
  not `.text.unlikely.`.
- `llvm-nm`: `sys_info`, `sys_info_parse_param`,
  `sysctl_sys_info_handler` all `T` in `vmlinux`
  (`ffffffff801e4982 T sys_info`, `ffffffff801e49ee T
  sys_info_parse_param`, `ffffffff801e4a74 T sysctl_sys_info_handler`).
  `riscv_current_is_tp`/`current_stack_pointer` absent from `vmlinux`
  entirely (0 occurrences via grep).
- `dev.py boot --run-id combined-c2rust-boot-18`: boots clean, **17/17
  KUnit suites pass** (`ORACLE PASS (17 suites)`, 0 `not ok`),
  `INIT REACHED (initramfs userspace boot verified)`, no panic/oops/
  BUG/WARN in the log (only "panic" string match is the `panic=-1`
  boot command-line argument). Archived at
  `docs/status/boot-logs/20260719T173641+1000-combined-c2rust-boot-18.log`.
  Boot-history row committed/pushed automatically by `dev.py boot`
  (`71cfba4`). No dedicated `sys_info` KUnit suite in this config, so
  this run demonstrates the build/link/boot path is correct, not that
  `sys_info_read_handler`'s comma-joined-name formatting matches the C
  reference output byte-for-byte for known inputs.

Seventeenth file to clear the bar, and the first to fail at the
`modpost` link stage rather than `rustc` compile or QEMU boot — a
different failure class from anything documented so far in this
series (`rustc`-stage: features/unsafe-wrapping/dead-structs/naming
collisions/const-eval; boot-stage: register-variable liveness,
overflow-panics, variadic-ABI gaps). Confirms `check-register-statics`
staying at 0 live corpus-wide continues to hold (263 -> still 0 live,
one file further from the #22 fix's original baseline), and that the
refcount-overflow and `-1 as usize` classes correctly don't fire on a
file that doesn't exercise either pattern.

## Nineteenth candidate: `lib/fonts/fonts.c`

Worktree `combined-c2rust-boot-20`, branch `agent-combined-c2rust-boot-20`,
based on `linux-rs/phase2-gcd`. Target: all 6 `EXPORT_SYMBOL_GPL`
functions (`font_data_import`, `font_data_get`, `font_data_put`,
`font_data_size`, `font_data_is_equal`, `font_data_export`) plus the 2
plain-`EXPORT_SYMBOL` functions (`find_font`, `get_default_font`), 90
statements (`dev.py readiness "lib/*.c"`) — the font-registry
lookup/dispatcher, not glyph bitmap data. Confirmed by inspection:
`fonts.c` only holds refcounted-font-data helpers and the
`find_font()`/`get_default_font()` lookup logic over a
`static const struct font_desc *fonts[]` array of `extern` pointers;
the actual glyph bitmaps live in separate per-font TUs
(`font_8x8.c`, `font_8x16.c`, etc., selected by `lib/fonts/Makefile`'s
`font-$(CONFIG_FONT_*)` lines) that stay plain C, untouched by this
run — this worktree's `.config` only enables `CONFIG_FONT_8x16`
(`CONFIG_FONT_AUTOSELECT=y` default), so the c2rust baseline's
`fonts[]` array has exactly one entry (`&font_vga_8x16`, an `extern`
symbol resolved at link time against `font_8x16.o`), matching the real
kernel build's preprocessor output exactly. c2rust binary verified
fresh via `dev.py c2rust-build` before starting; baseline transpile
already present at `tmp/c2rust-baseline/lib_fonts_fonts.c/output/src/fonts.rs`
(4388 lines), not re-run.

`python3 scripts/dev.py check-register-statics` run first per rule
0031: 263 files with the fabricated static, 0 live, 263 dead/safe —
`lib_fonts_fonts.c` in the dead/safe set, corroborated by grep: no
`get_current()` accessor synthesized anywhere in the TU (this file
never touches current-task state). Scanned separately for `-1 as
usize` (issue #38): none present — `check_mul_overflow`/
`check_add_overflow` already translate via the i128-overflow-check
idiom, not the old dead-branch literal.

Kconfig: `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (`depends on
RUST`, default n). `lib/Makefile` untouched — `lib/fonts/Makefile`'s
`font-y := fonts.o` was already a standalone single-item assignment
(no bundled-line extraction needed, unlike most prior files), swapped
for an `ifdef CONFIG_RUST_C2RUST_BOOT_TEST` / `else` selecting
`fonts_rs.o` vs `fonts.o`.

### Cleanest baseline output of the series — zero `#![feature(...)]`, zero `.init_array`, zero RISC-V asm

No `#![feature(...)]` line at all (no `asm`, `extern_types`,
`raw_ref_op`, `strict_provenance`, or `label_break_value` — the first
file in the series with none of these), no `.init_array`/
`__UNIQUE_ID_addressable_*` constructor trick, no `::libc::` calls, no
RISC-V inline asm (`amocas`/`amoswap`/`cmpxchg` all absent — this file
does no atomics). Only 3 known classes recurred, all mechanical:

1. **`#[export]` -> `#[no_mangle]`.** The 6 `EXPORT_SYMBOL_GPL`
   functions carried `#[export]` (`use ::macros::export;` import);
   dropped the attribute, added `#[no_mangle]`, deleted the now-unused
   import — the `rcuref.c`-established two-part fix. `find_font`/
   `get_default_font` (plain `EXPORT_SYMBOL`) already carried
   `#[no_mangle]` correctly in the raw output, confirming c2rust's
   `EXPORT_SYMBOL` vs `EXPORT_SYMBOL_GPL` -> `#[no_mangle]` vs
   `#[export]` split is consistent across files.
2. **`kernel::warn_on!(cond) != 0`.** 2 sites (`font_data_get`,
   `font_data_put`, both guarding `WARN_ON(!REFCOUNT(fd))`), fixed by
   dropping `!= 0`, same as every prior file with this class.
3. **Register-variable pseudo-globals.** `riscv_current_is_tp`/
   `current_stack_pointer` fabricated as usual (`asm/current.h` pulled
   in transitively via `task_struct`'s header chain, despite this file
   never calling `get_current()`); grep-confirmed zero references
   anywhere in the TU beyond the fabricated declarations themselves,
   deleted outright.

`BitfieldStruct` opaquing: the standard 6-struct set (`task_struct`,
`mmap_action`, `kobject`, `kernfs_open_file`, `signal_struct`,
`sched_dl_entity`) recurred, pulled in transitively via the same
`task_struct` header chain as `riscv_current_is_tp`. Grep-confirmed
dead-by-value throughout (only ever `*mut task_struct` on the deleted
fabricated static, `sched_dl_entity`/`mmap_action` embedded by value
inside `task_struct` only) — same collapse-to-dead-by-construction
shape `bust_spinlocks.c`/`glob.c`/`seq_buf.c` established. Unlike prior
files, this baseline had no pre-existing `extern "C" { pub type X; }`
opaque block to fold into (no `#![feature(extern_types)]` at all), so
the `opaque_marker!` macro group was declared fresh and each struct's
full derive+body was replaced in place with a single
`opaque_marker!(Name);` invocation — the standard idiom, just applied
to a from-scratch struct body rather than an existing opaque-extern
block.

One c2rust idiom noted but requiring no fix: `num_fonts` (from C's
`#define num_fonts ARRAY_SIZE(fonts)`) translates to
`size_of::<[*const font_desc; N]>() / size_of::<*const font_desc>() +
size_of::<ZST>()`, where the `ZST` (`C2Rust_Unnamed_82`/`_83`, empty
`#[repr(C)]` structs) is c2rust's translation of `ARRAY_SIZE`'s
`__must_be_array()` compile-time type-check tag — `size_of::<ZST>()`
is always 0, a semantically inert addend. Compiles and links cleanly
as-is; not a gap, just an unusual-looking artifact worth noting for
future files using `ARRAY_SIZE`.

### Outcome: clean boot, fewest fix classes of any file so far

- `make ARCH=riscv LLVM=1 lib/fonts/fonts_rs.o` — clean, 0 errors (1875
  warnings, all missing-doc/FFI-safety lints, pre-existing pattern —
  the `kmalloc_token_t` zero-sized-struct `improper_ctypes` warnings on
  2 kmalloc-family extern params are the same cosmetic false positive
  every prior file's `extern "C"` blocks already carry).
- `dev.py build` (`LINUXRS_TREE=linux-riscv-worktrees/combined-c2rust-boot-20`)
  succeeds, `arch/riscv/boot/Image`/`Image.xz` produced. `lib/fonts/
  built-in.a` confirmed via `llvm-ar t` to contain `fonts_rs.o` and
  `font_8x16.o` (the untouched plain-C glyph-bitmap TU), not
  `fonts.o`.
- `llvm-nm vmlinux`: all 8 functions `T` (`ffffffff8018506a T
  find_font`, `ffffffff8018509e T font_data_export`, `ffffffff80185150
  T font_data_get`, `ffffffff8018517c T font_data_import`,
  `ffffffff8018527a T font_data_is_equal`, `ffffffff801852ca T
  font_data_put`, `ffffffff8018530e T font_data_size`,
  `ffffffff80185322 T get_default_font`); `riscv_current_is_tp`/
  `current_stack_pointer` absent from `vmlinux` entirely (empty grep).
- `dev.py boot --run-id combined-c2rust-boot-20`: boots clean, **17/17
  KUnit suites pass** (`ORACLE PASS (17 suites)`, 0 `not ok`),
  `INIT REACHED (initramfs userspace boot verified)`, no panic/oops/
  BUG/WARN in the log (only "panic" match is the `panic=-1` boot
  argument). Archived at
  `docs/status/boot-logs/20260719T173653+1000-combined-c2rust-boot-20.log`.
  Boot-history row committed/pushed automatically by `dev.py boot`
  (`96bf58b`). No dedicated font-registry KUnit suite exists in this
  kernel config, so `font_data_import`/`find_font`/`get_default_font`'s
  actual lookup/refcounting logic wasn't runtime-exercised by this run
  (same caveat as every prior file without a dedicated enabled suite)
  — this run demonstrates the dispatcher links and boots correctly
  alongside the untouched plain-C bitmap TU, not that the font-lookup
  logic itself matches the C reference for known inputs.

Needed the fewest distinct fix classes of any file in the series so
far (3: `#[export]`, `warn_on! != 0`, register-static deletion, plus
`BitfieldStruct` opaquing) and zero genuinely new gap classes — every
class that fired was already in the established playbook, and several
whole categories that have hit most prior files (feature-gate
stripping, `.init_array`, RISC-V inline-asm, `::libc::`) didn't fire at
all here. Confirms the task's working hypothesis directly: `fonts.c` is
pure registry/dispatcher logic with no embedded bitmap data of its own
— the actual glyph arrays live in separate, still-plain-C translation
units this run left untouched, and the dispatcher itself needed only
mechanical, already-cataloged fixes to link and boot alongside them.

## Twentieth candidate: `lib/lz4/lz4_decompress.c`

Worktree `combined-boot-lz4_decompress`, based on `linux-rs/phase2-gcd`.
Target: all 10 `EXPORT_SYMBOL`/`EXPORT_SYMBOL_GPL` LZ4 decode functions
(`LZ4_decompress_safe`, `LZ4_decompress_safe_partial`,
`LZ4_decompress_safe_continue`, `LZ4_decompress_safe_usingDict`,
`LZ4_decompress_fast`, `LZ4_decompress_fast_continue`,
`LZ4_decompress_fast_usingDict`, plus 3 internal-linkage helpers), 239
statements.

**First candidate produced by `scripts/combined_boot_scaffold.py`**
(mechanical scaffolding: c2rust-freshness check, worktree creation,
baseline-output copy, Kconfig/Makefile wiring, the 3 mandatory checks,
build+boot) rather than a hand-run worktree setup — landed with
**zero hand-fixes needed**, the first fully-clean scaffold-to-boot run
in the series. Confirms today's earlier c2rust-source fixes (#22, #34,
#38, #40) are genuinely reducing per-file work, not just shifting it.

Evidence: `llvm-nm vmlinux` shows all `LZ4_decompress_*` symbols `T`
(defined); `dev.py boot --run-id combined-boot-lz4_decompress`: 17/17
KUnit suites pass, 0 not ok, INIT REACHED. `lib/lz4/lz4_decompress.o`
never built. Log:
`docs/status/boot-logs/20260719T202959+1000-combined-boot-lz4_decompress.log`.

### Correction (found while adding functional KUnit coverage)

The "zero hand-fixes needed" / "`lz4_decompress.o` never built" claims
above were **wrong**. `RUST_C2RUST_BOOT_TEST` was never actually added
to any `Kconfig` reachable by this worktree (`lib/lz4/` has no
`Kconfig` of its own — the scaffold script's "no Kconfig at ... check
by hand" warning path — and it wasn't added to the parent `lib/Kconfig`
either), so the symbol was undefined, `.config` never set it, and
`lib/lz4/Makefile`'s `ifdef CONFIG_RUST_C2RUST_BOOT_TEST` always took
the `else` branch. `vmlinux`'s `LZ4_decompress_safe` disassembly was
confirmed **byte-for-byte identical** to `lib/lz4/lz4_decompress.o`
(the plain-C object, which was present in the tree the whole time) —
the Rust translation was never actually built or linked despite the
worktree's boot passing 17/17. `lz4_decompress_rs.rs` itself was also
never committed to the kernel worktree (uncommitted working file only).

Compounding this, the Rust file does not actually build standalone:
`::c2rust_bitfields::BitfieldStruct` derives on 6 dead-by-construction
structs (`task_struct`/`mmap_action`/`kobject`/`kernfs_open_file`/
`signal_struct`/`sched_dl_entity` — the same recurring header-chain
bloat as every other file in this series) and `::libc::memcpy`/
`::libc::size_t` references (crate unavailable in this build) both
fail with `E0433`. Both fixed as part of adding the functional test
below: 6 structs opaqued via the standard `opaque_marker!` idiom
(grep-confirmed zero field-level references, same as every prior
file), `::libc::memcpy`/`::libc::size_t` swapped for a local
`extern "C" { fn memcpy(...) }` declaration (mirroring the file's
existing `memmove` extern) and the file's own pre-existing `size_t`
alias. `RUST_C2RUST_BOOT_TEST` added to `lib/Kconfig` (parent, since
`lib/lz4/` has none) and enabled in `.config`. All of this — the
Rust file, the Kconfig/Makefile wiring, both fixes — is now actually
committed to the kernel worktree (`8a45d8b80e68`); it was not before.

### Functional KUnit coverage added (closes "links but never called" —
### and the above build-gap correction)

New suite `rust_lz4_decompress` (`lib/lz4/lz4_decompress_rs.rs`,
`#[kunit_tests(rust_lz4_decompress)]`), 1 test case
`decompresses_known_payload`: calls the real `LZ4_decompress_safe()`
on `lib/lz4/testdata/lz4_decompress_kunit_test.lz4block` (a real raw
LZ4 block — no frame header, matching what `LZ4_decompress_safe`
actually decodes — produced by the `lz4` Python package's C extension,
which links real `liblz4` 1.10.4, confirmed via
`lz4.library_version_number()`, not a hand-rolled encoder; embedded
via `include_bytes!`) and asserts both the returned decompressed byte
count and the decompressed bytes match the known 105-byte plaintext
exactly. `LZ4_decompress_safe` is a plain `EXPORT_SYMBOL` function (not
`__init`), so no section-mismatch handling was needed here (contrast
`decompress_bunzip2.c`'s `__init`-section fix below).

Evidence: `llvm-nm vmlinux` / `find . -name lz4_decompress_rs.o`
confirm the Rust `.o` is what's actually linked this time (test symbols
present in `vmlinux` at real addresses, not just the `.o`).
`dev.py boot --run-id combined-boot-lz4_decompress-lz4test2`:
**18/18 KUnit suites pass** (up from the never-actually-achieved 17),
`ok 17 rust_lz4_decompress` / `ok 1 decompresses_known_payload`,
`rust_lz4_decompress: pass:1 fail:0 skip:0 total:1`, 0 not ok,
INIT REACHED. Log:
`docs/status/boot-logs/20260719T205922+1000-combined-boot-lz4_decompress-lz4test2.log`.
Boot-history row committed/pushed automatically by `dev.py boot`.

## Twenty-first candidate: `lib/decompress_bunzip2.c`

Worktree `combined-c2rust-boot-22`, branch `agent-combined-c2rust-boot-22`,
based on `linux-rs/phase2-gcd`. 308 statements — largest file attempted
in the series so far. All 6 functions (`get_bits`, `get_next_block`,
`read_bunzip`, `nofill`, `start_bunzip`, `bunzip2`) are `__init`-marked
(`INIT` macro under non-`PREBOOT` build); only `bunzip2` is externally
called (from `lib/decompress.c`, plain C, by symbol name — no
`EXPORT_SYMBOL` at all in this file). c2rust binary verified fresh via
`dev.py c2rust-build` first.

Corpus-capture quirk, not a c2rust bug: raw baseline transpile (via the
cached `compile_commands.json` entry) failed AST-export with 4x
`-Werror=incompatible-pointer-types-discards-qualifiers` on the
`error("literal")` call sites (`bunzip2.h`'s `error` callback takes
non-const `char *x`, real kernel `-Werror=incompatible-pointer-types`
does **not** promote the discards-qualifiers sub-diagnostic — confirmed
by building `lib/decompress_bunzip2.o` clean with the real captured
kbuild flags). Re-transpiled with `-Wno-incompatible-pointer-types-discards-qualifiers`
appended to the scratch compile_commands.json only (no source or
c2rust changes) — clean transpile, and the resulting Rust correctly
casts the literals to `*mut c_char` matching the (loose) C signature.

`check-register-statics`: 0 live corpus-wide (unchanged), this file
dead/safe — 0 `get_current()` calls. `-1 as usize`: absent. All 6
`__init` functions carry `#[link_section = ".init.text"]` correctly in
the raw c2rust output (issue #40's fix holding, no regression). No
`#![feature(...)]`, no `.init_array`, no `::libc::`, no `warn_on!`, one
`asm!("ebreak")` (standard `BUG()` trap). Rule 0009's unsigned-wrapping
machinery already covers every bit-buffer/CRC-table op in `get_bits`/
`start_bunzip` (`.wrapping_add`/`.wrapping_sub`/`.wrapping_mul`)
end-to-end — manually reviewed all signed-`int` arithmetic in
`get_next_block` (Huffman `limit[]`/`base[]`/`permute[]` construction,
MTF run-length accumulation) against the format's own bounds
(`MAX_HUFCODE_BITS`=20, `MAX_SYMBOLS`=258, `MAX_GROUPS`=6): all
well-bounded, no test_sort.c-style intentional-wraparound idiom present
here, no wrapping_* hand-fix needed anywhere in this file.

Kconfig: `RUST_C2RUST_BOOT_TEST` added fresh to this worktree's
`lib/Kconfig` (not yet present on `phase2-gcd`). `lib/Makefile`'s
`lib-$(CONFIG_DECOMPRESS_BZIP2) += decompress_bunzip2.o` wrapped in
`ifdef CONFIG_RUST_C2RUST_BOOT_TEST` / `else` selecting
`decompress_bunzip2_rs.o` vs `decompress_bunzip2.o` (`lib-y`/`lib.a`
linkage, not `obj-y`/`built-in.a` — confirmed via `llvm-ar t lib/lib.a`
containing `decompress_bunzip2_rs.o`, not the plain-C object).

One self-inflicted bug caught before commit, not a c2rust gap: an
initial `BitfieldStruct`-opaquing pass (same idiom as `fonts.c`/
`test_sort.c` for the standard `task_struct`/`mmap_action`/
`signal_struct`/`sched_dl_entity` dead-by-construction quartet —
grep-confirmed zero references in the 6 live function bodies) had an
off-by-one in the derive-line detection, deleting each struct's body
but leaving its `#[derive(Copy, Clone, ::c2rust_bitfields::BitfieldStruct)]`
attribute line orphaned above the next unrelated item — 2 cases
produced `E0119` conflicting-Copy/Clone-impl errors (colliding with
`thread_struct`'s/`rlimit`'s own genuine `#[derive(Copy, Clone)]`), 2
cases left a bare attribute before a `pub type` alias. Caught by the
first `rustc` build attempt, fixed by deleting the 4 orphaned lines;
rebuilt clean on the second attempt.

### Outcome: clean boot, largest file in the series to date

- `make ARCH=riscv LLVM=1 lib/decompress_bunzip2_rs.o` — clean, 0
  errors (1649 warnings, all missing-doc/FFI-safety, pre-existing
  pattern).
- `dev.py build` succeeds, `arch/riscv/boot/Image` produced. `lib/lib.a`
  contains `decompress_bunzip2_rs.o` (`llvm-ar t` confirmed), not
  `decompress_bunzip2.o`.
- `llvm-nm`: `bunzip2` `T` in both the `.o` and `vmlinux`
  (`ffffffff8021dfc6 T bunzip2`); internal helpers (`get_bits`,
  `get_next_block`, `read_bunzip`, `start_bunzip`, `nofill`) correctly
  Rust-mangled `t` (local); `riscv_current_is_tp`/`current_stack_pointer`
  absent from both entirely.
- `dev.py boot --run-id combined-c2rust-boot-22`: boots clean, **17/17
  KUnit suites pass** (`ORACLE PASS (17 suites)`, 0 `not ok`),
  `INIT REACHED (initramfs userspace boot verified)`, no panic/oops/
  BUG/WARN in the log. Archived at
  `docs/status/boot-logs/20260719T203313+1000-combined-c2rust-boot-22.log`.
  Boot-history row committed/pushed automatically by `dev.py boot`
  (`1d4c18b`).
- **Runtime-exercise caveat**: this kernel's initramfs is gzip- not
  bzip2-compressed (`CONFIG_RD_BZIP2=y` selects `DECOMPRESS_BZIP2` for
  link-in, but nothing in this build's boot path actually calls
  `bunzip2()`), and no dedicated KUnit suite exists for bzip2 decode —
  same "links and boots, not runtime-exercised" caveat as most non-test
  files in this series. The wraparound-arithmetic review above is by
  inspection against the format's own bounds, not by an observed
  execution trace.

Largest file (308 statements) and most `__init` functions (6, all of
them) of any file in the series so far; confirms #40's `__init` ->
`#[link_section]` fix holds across a whole-file, not just single-symbol,
case, and that rule 0009's unsigned-wrapping coverage extends cleanly to
dense bit-manipulation code without needing any file-specific
wrapping_* hand-fix. The only genuine friction was the pre-existing
corpus-capture `-Werror` mismatch (worked around locally, not a source
or c2rust change) and a self-inflicted script bug in this session's own
opaquing pass (caught by the normal build-then-fix loop, not a hidden
runtime issue).

### Functional KUnit coverage added (closes "links but never called")

New suite `rust_decompress_bunzip2` (`lib/decompress_bunzip2_rs.rs`,
`mod tests`), 1 test case `decompresses_known_payload`: calls the real
`bunzip2()` on `lib/testdata/bunzip2_kunit_test.bz2` (host-`bzip2 -9`,
embedded via `include_bytes!`) and asserts the output matches the known
93-byte plaintext exactly.

`bunzip2()` is `__init` in this build (non-`STATIC`/non-`PREBOOT`), so
the test fn, its `error` callback, and the KUnit C-ABI wrapper all had
to be placed in `.init.text`; the `#[kunit_tests(...)]` proc macro
always emits `.text.unlikely.`-placed wrappers via
`kunit_unsafe_test_suite!`, which produced a genuine modpost
section-mismatch error against `.init.text`. Registered by hand instead,
mirroring C's `kunit_test_init_section_suites()` idiom
(`lib/kunit/kunit-example-test.c`, also used by
`init/initramfs_test.c` in this tree): suite/case-array *data* statics
in `.init.data` (not `.init.text` — a first attempt put the
`kunit_suite` struct itself in `.init.text`, which built and linked but
**oopsed at boot** — `Unable to handle kernel paging request`, `swapper/0`
killed, `PID: 1 Comm: swapper/0` — dereferencing the struct after its
section had already been unmapped; `INIT_DATA`/`INIT_TEXT` are distinct
linker output sections per `include/asm-generic/vmlinux.lds.h`, and
`KUNIT_INIT_TABLE()`/`.kunit_init_test_suites` is embedded inside
`INIT_DATA`, not `INIT_TEXT`), suite-pointer array in
`.kunit_init_test_suites` with a `_probe`-suffixed symbol (modpost's
whitelist heuristic for "legitimately references init code").

Evidence: `dev.py boot --run-id combined-c2rust-boot-22-bunzip2test`:
**18/18 KUnit suites pass** (up from 17), `ok 1 rust_decompress_bunzip2`
/ `ok 1 decompresses_known_payload`, `is_init: true`,
`rust_decompress_bunzip2: pass:1 fail:0 skip:0 total:1`, 0 not ok,
`INIT REACHED`, no oops/panic (beyond the fixed first attempt, which
never reached this run's committed history). Log:
`docs/status/boot-logs/20260719T205026+1000-combined-c2rust-boot-22-bunzip2test.log`.
Boot-history row committed/pushed automatically by `dev.py boot`
(`bbfccf8`).

### Recommendation: host-compress + embed + KUnit-verify is reusable

The pattern used for both files above (host-compress a small known
payload with the real matching tool, embed via `include_bytes!`,
KUnit-assert the real decompress function's output byte-for-byte)
generalizes cleanly to any remaining `decompress_*`/codec-family file
in this series (e.g. `decompress_unlz4.c` if it lands, `decompress_unxz.c`,
`decompress_unlzo.c`, `decompress_unzstd.c`, `decompress_inflate.c`) —
each just needs its own host tool (`xz`/`lzop`/`zstd`/`gzip`) and its
entry function's real calling convention checked (particularly whether
it's `__init`, which determines whether the bunzip2-style hand-rolled
`.kunit_init_test_suites` registration is needed, or the plain
`#[kunit_tests(...)]` macro suffices as it did for lz4). Not building a
generic harness for this now — each file's actual function signature
and init-vs-not status still needs a quick manual check first — but
worth remembering next time one of these lands.

