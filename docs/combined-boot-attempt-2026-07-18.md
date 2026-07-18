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
