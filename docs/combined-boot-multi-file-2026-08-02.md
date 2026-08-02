# Combined-image boot test: c2rust-transpiled files in one image

First real test of issue #28's actual ask — multiple c2rust-transpiled
files wired into *one* kernel image and boot-screened together, as
opposed to the 22 individual one-file-per-boot attempts documented in
`combined-boot-attempt-2026-07-18.md`. That doc proved the mechanism
works per-file; this one proves it still works when files are combined,
and surfaces real bugs that only exist at combination time.

Three rounds so far: 3 files (klist/sys_info/rcuref, see below), then
8 (adding glob/group_cpus/bucket_locks/errseq/uuid, "Round 2"), then
18 (adding is_single_threaded/lwq/bust_spinlocks/debug_locks/
devmem_is_allowed/test_sort/kfifo/fonts/lz4_decompress/
decompress_bunzip2, "Round 3" — one candidate, `seq_buf.c`, deferred;
one real transpiler bug found and fixed) — all three clean boots,
20/20 KUnit suites each.

## Candidate selection

Per #28's explicit scope limit ("genuinely on/near the current boot
path, not an arbitrary sample"), a dedicated research pass across the
14-file individual-candidate shortlist found real boot-path-execution
evidence for only two files, plus one link-clean-only filler kept to
exercise the 3-file batching mechanism itself:

- **`lib/klist.c`** — proven executed: caused a real crash in an
  earlier bisection (`load_page_fault` inside `klist_add_tail`, via
  `get_current()`'s now-fixed accessor bug), reached through
  `spin_unlock()`'s preemption-check path hit by every `klist_add_head`/
  `klist_add_tail` call during real `device_add()` traffic at boot.
- **`lib/sys_info.c`** — proven `.initcall4.init` entry that executes
  every boot (though its 3 real functions are panic/hung-task/watchdog
  path only, not exercised by a clean boot).
- **`lib/rcuref.c`** — link-clean filler, no proven natural-boot-path
  execution; included specifically to test whether the batching
  mechanism itself (3 files, 1 Kconfig gate, 1 boot) scales, not to
  claim its own code ran.

Worktree `combined-boot-28`, branch `agent-combined-boot-28`, based on
`linux-rs/phase2-gcd`. New `CONFIG_RUST_C2RUST_BOOT_TEST` in
`lib/Kconfig`; `lib/Makefile` pulled all three `.o`s out of their
bundled `lib-y`/`obj-y` lines and gated `klist_rs.o`/`sys_info_rs.o`/
`rcuref_rs.o` on the new config.

## Fresh transpile, not the 2026-07-18 baseline

Re-ran `c2rust_reference_check.py` at current `awtoau/c2rust` HEAD
(`7a7b63b804e3`) rather than reusing the old doc's cached output.
`REFERENCE-CHECK: 3/3 OK`. Two real transpiler improvements confirmed
by diffing against the fixes the old doc had to hand-apply:

- `klist.c`'s `get_current()` now compiles directly to
  `asm!("mv {0}, tp", ...)` — the exact hand-fix the original attempt
  needed is now the transpiler's own default output.
- `sys_info.c`'s `sys_info_sysctl_init` now carries
  `#[link_section = ".init.text"]` automatically — the `__init`
  section-drop bug the 2026-07-18 doc found and hand-fixed is gone.
- `sys_info_rs.rs`'s 7 `BitfieldStruct`-derived structs (`task_struct`,
  `mmap_action`, `kobject`, etc.) needed **no** manual opaquing this
  time — `c2rust_bitfields`/`c2rust_bitfields_derive` are now wired
  into the real Kbuild path (issue #55), so the file built clean
  raw. (This uncovered its own bug at link time — see below.)

## Fix classes actually needed, per file

**`lib/klist_rs.rs`** (6 fixes, all previously-documented classes):
stripped an unused `#![feature(label_break_value)]`; converted 13
`#[export]` sites to `#[no_mangle]` (no `EXPORT_SYMBOL` in the C
original visible to bindgen); rewrote `LIST_POISON1`/`LIST_POISON2`
from `.offset()`-on-bare-integer (E0080, fails const-eval) to plain
`wrapping_add`+cast; renamed a parameter shadowing c2rust's own
generated `klist` type; deleted the dead `current_stack_pointer`
register-pseudo-global; fixed 14 RISC-V asm bracket-addressing sites
(`[{N}]` → `0({N})`, issue #29's class). Also removed the 4
Zacas/Zabha ISA-guard `if riscv_has_extension_unlikely(...) {amocas
fast path} else {LR/SC fallback}` wrappers down to just the
unconditional LR/SC fallback — LLVM rejects the `amocas.*`
instructions regardless of the runtime guard (issue #29).

**`lib/sys_info_rs.rs`**: zero fixes needed — built clean as raw
transpile output (both gap classes the 2026-07-18 doc hit for this
file are now transpiler-fixed, see above).

**`lib/rcuref_rs.rs`** (4 fixes): stripped the same unused
`#![feature(label_break_value)]`; `#[export]` → `#[no_mangle]` (2
sites; c2rust's own `--extern` type didn't match the real bindgen
signature — same class as klist); two `kernel::warn_on!(...) != 0`
sites fixed to plain `kernel::warn_on!(...)` (the macro already
returns `bool`, c2rust assumed C's int-returning `WARN_ON()`); 12
RISC-V asm bracket-addressing sites. Left the 4 Zacas/Zabha guard
blocks in place unlike klist — this file compiled clean with them
present (LLVM only rejected the instruction encoding in klist's
specific codegen shape, not here), so removal wasn't required to reach
a working build and was skipped to avoid the syntax-fragile
brace-surgery this class of edit carries.

## Two real bugs found only by combining files

Neither is visible from any single-file boot test — both are genuine
"combination reveals what isolation hides" findings, which is the
whole point of #28.

### 1. Duplicate fabricated register-pseudo-globals across TUs

`rcuref_rs.rs` and `sys_info_rs.rs` each independently declared their
own `#[no_mangle] pub static mut riscv_current_is_tp` /
`current_stack_pointer` — c2rust emits these per-TU whenever a file's
header chain pulls in GCC's `register ... __asm__("tp")` pseudo-global
declarations, matching the same class the 2026-07-18 doc names for
`sys_info.c` ("declaration-only, deleted outright"). Linking one such
file is fine; linking two produces `ld.lld: error: duplicate symbol:
current_stack_pointer` / `riscv_current_is_tp`. Grep-confirmed neither
static is ever read in either file (no `get_current()` accessor call
anywhere) before deleting both declarations from both files — same
"verify unused before deleting" discipline as every prior file in this
series.

### 2. `libc2rust_bitfields.rlib` was never linked into `vmlinux`

A genuine Kbuild gap, invisible until two-plus files actually exercise
`c2rust_bitfields::FieldType::get_field` through the real link path
(no individual-file attempt before this one built cleanly *and* got
all the way to a full `vmlinux` link with a `BitfieldStruct` derive
still present — earlier files either opaqued the bitfielded structs
away or predate issue #55's Kbuild wiring entirely).

Root cause: `rust/Makefile` built `libc2rust_bitfields.rlib` via
`cmd_rustc_targetlibrary` (`--emit=link`, `always-$(CONFIG_RUST) +=`)
— this produces an rlib with metadata for `--extern` typechecking, but
that build product is never added to `obj-y`/`libs-y`, so its compiled
code never reaches the final `ld`. The derive macro's generated
`set_field` gets monomorphized directly into each caller's own crate
(shows up as a local `T` symbol per-TU), but the `FieldType::get_field`
trait method (defined once in the `c2rust_bitfields` crate itself,
no `#[inline]`) stays a real external symbol reference — fine as long
as nothing needs it resolved, which no earlier individual boot test
ever triggered.

```
ld.lld: error: undefined symbol: <bool as c2rust_bitfields::FieldType>::get_field
>>> referenced by sys_info_rs.o:(<sys_info_rs::mmap_action>::hide_from_rmap_until_complete)
ld.lld: error: undefined symbol: <u32 as c2rust_bitfields::FieldType>::get_field
>>> referenced by sys_info_rs.o:(<sys_info_rs::kobject>::state_in_sysfs)
```

Fixed the same way this tree already links `bindings.o`/`kernel.o`/
`zerocopy.o`: changed the build rule to emit a real linkable
`c2rust_bitfields.o` via `cmd_rustc_library` (which emits both
`--emit=obj` and `--emit=metadata=...rmeta`, so `--extern
c2rust_bitfields` consumers are unaffected) and added it to
`obj-$(CONFIG_RUST)` so it archives into `built-in.a` through the
normal path. `rust/Makefile` diff isolated from every #28-specific
file and landed on `linux-rs/phase2-gcd` directly as its own commit
(`ee679b5bc167`), verified via a full `dev.py check` on the main tree
(zero regression: `TU PROVENANCE PASS (41 total: 41 hand, 0 c2rust, 0
c2rust+hand-fix)`, `ORACLE PASS (20 suites)`, `INIT REACHED`) before
being pushed — this fix is real and general, not scoped to #28's three
files, so it belongs on the main tree independent of whether/how #28
itself ever lands.

## Outcome: clean combined boot, 20/20 KUnit suites

- `make ARCH=riscv LLVM=1 -j32`: `arch/riscv/boot/Image` produced.
- `llvm-nm vmlinux.unstripped`: all 18 target symbols present and
  `T` — 13 `klist_*` functions, `sys_info`/`sys_info_parse_param`/
  `sysctl_sys_info_handler`, `rcuref_get_slowpath`/
  `rcuref_put_slowpath`. Plain-C `klist.o`/`sys_info.o`/`rcuref.o`
  confirmed absent from the tree.
- `scripts/boot_qemu.py --tree linux-riscv-worktrees/combined-boot-28
  --run-id combined-boot-28`: boots clean, **20/20 KUnit suites pass**
  (0 fail), `initramfs init reached, PID 1 alive` confirms INIT
  REACHED, zero panic/oops/BUG/WARN in the log (only "panic" match is
  the `panic=-1` boot argument). Boot-history row auto-committed/
  pushed by `boot_qemu.py`. Archived at
  `docs/status/boot-logs/20260802T133723.985524-84078+1000-combined-boot-28.log`.

Confirms the "many files per boot, one screening pass" mechanism #28
asked about actually works — three independently-transpiled files,
two with proven boot-path execution and one link-clean filler, boot
together cleanly in one image after fixing the file-level gaps (all
previously-documented classes, applied faster given fresh transpile
already closed two of them) plus the two genuinely new
combination-only bugs above (both now fixed at the root: the register-
pseudo-global duplication by deleting dead per-file declarations, the
`c2rust_bitfields` link gap by a general Kbuild fix already landed on
the main tree).

## Round 2: scaling to 8 files

Added 5 more from the 2026-07-18 individually-verified pool: `glob.c`,
`group_cpus.c`, `bucket_locks.c`, `errseq.c`, `uuid.c` — all link-clean
filler (none proven on the natural boot path; `glob.c`'s own KUnit
suite does exercise `glob_match` at boot, which is real functional
signal even without organic boot-path traffic). Same worktree/branch,
same `CONFIG_RUST_C2RUST_BOOT_TEST` gate, each file pulled out of its
bundled `lib-y`/`obj-y` line individually.

Fresh transpile at the same c2rust HEAD (`7a7b63b804e3`) used for
round 1: `REFERENCE-CHECK: 5/5 OK`.

### Fix classes needed, per file

- **`group_cpus_rs.rs`**: `use ::macros::export;` → dropped, `#[export]`
  → `#[no_mangle]` (1 site); duplicate `riscv_current_is_tp`/
  `current_stack_pointer` declarations deleted (grep-confirmed dead,
  same as every prior occurrence of this class).
- **`glob_rs.rs`**: only the duplicate register-pseudo-global
  declarations needed deleting — no export/feature issues.
- **`bucket_locks_rs.rs`**: same — only the duplicate
  register-pseudo-global declarations.
- **`uuid_rs.rs`**: `use ::macros::export;` → dropped, `#[export]` →
  `#[no_mangle]` (2 sites: `guid_gen`, `uuid_gen`). No duplicate
  register-pseudo-globals in this file.
- **`errseq_rs.rs`**: one `kernel::warn_on!(...) != 0` → plain
  `kernel::warn_on!(...)` (same bool-vs-int class as `rcuref.c` in
  round 1); 12 RISC-V asm bracket-addressing sites (`[{N}]` →
  `0({N})`, issue #29's class — this file's atomic helpers use the
  same Zacas/Zabha-guarded `amocas`/LR-SC pattern as `klist.c`/
  `rcuref.c`, left in place since it compiled clean with the guards
  present, same call as round 1's `rcuref.c`).

### Round 2 combination-only bugs

Same class as round 1, not a new one: `group_cpus_rs.rs`, `glob_rs.rs`,
and `bucket_locks_rs.rs` each independently declared the same
`riscv_current_is_tp`/`current_stack_pointer` fabricated statics
already fixed in `rcuref_rs.rs`/`sys_info_rs.rs` during round 1 — this
confirms the fix is a recurring, mechanical step (grep for
`get_current()` calls, delete if absent) rather than a one-off, and
that essentially any file whose header chain reaches GCC's `register
... __asm__("tp")` declarations will hit this the moment a second such
file joins the same image. No fresh Kbuild-level bug this round — the
`c2rust_bitfields.o` link fix from round 1 covered `bucket_locks_rs.rs`'s
own `BitfieldStruct` derives (`mmap_action`, `percpu_ref_data`,
`signal_struct`, `sched_dl_entity`, `task_struct`) with no further
change needed.

### Round 2 outcome: clean 8-file boot, 20/20 KUnit suites

- `make ARCH=riscv LLVM=1 -j32`: clean on the first full-image attempt
  after the individual-file fixes above — no new link-stage surprises.
- `llvm-nm vmlinux.unstripped`: all 8 files' representative symbols
  present as `T` (`klist_add_tail`, `sys_info`, `rcuref_get_slowpath`,
  `glob_match`, `group_cpus_evenly`, `__alloc_bucket_spinlocks`,
  `errseq_set`, `uuid_gen`).
- `scripts/boot_qemu.py --tree linux-riscv-worktrees/combined-boot-28
  --run-id combined-boot-28-8file`: boots clean, **20/20 KUnit suites
  pass** (0 fail, same suite count as round 1 — no new suites gated on
  by this batch), `initramfs init reached, PID 1 alive`, zero
  panic/oops/BUG/WARN. Notably the `glob` KUnit suite (64/64 pass) ran
  against the transpiled `glob_rs.o`, giving real functional signal
  beyond link-cleanliness for that file. Boot-history row
  auto-committed/pushed. Archived at
  `docs/status/boot-logs/20260802T165237.609662-345270+1000-combined-boot-28-8file.log`.

8 files now combined-boot-verified in one image, up from 3 — the
batching mechanism continues to scale with no per-round growth in fix
complexity (round 2's fixes were the same recurring classes as round 1,
applied faster).

## Round 3: scaling to 18 files, one deferred, one real transpiler bug found

Attempted all 11 remaining individually-verified candidates:
`is_single_threaded.c`, `lwq.c`, `bust_spinlocks.c`, `debug_locks.c`,
`devmem_is_allowed.c`, `tests/test_sort.c`, `seq_buf.c`, `kfifo.c`,
`fonts/fonts.c`, `lz4/lz4_decompress.c`, `decompress_bunzip2.c`.
Fresh transpile at the same c2rust HEAD as rounds 1–2
(`7a7b63b804e3`): `REFERENCE-CHECK: 11/11 OK`.

### Fix classes needed, per file

5 of 11 built clean raw: `is_single_threaded.c`, `bust_spinlocks.c`,
`devmem_is_allowed.c`, `tests/test_sort.c` (before the overflow bug
below), `decompress_bunzip2.c`. All 7 that declared the duplicate
`riscv_current_is_tp`/`current_stack_pointer` register-pseudo-globals
(now hit in 7 of the 8 files across rounds 2–3 that transitively pull
in the C source's `register ... __asm__("tp")` pattern) had them
deleted after grep-confirming dead — same mechanical step as every
prior occurrence.

Two files (`is_single_threaded.c`, `lwq.c`) genuinely **call**
`get_current()` rather than leaving it dead — first time this session
a file in the combined-boot pool actually invokes it rather than just
declaring the pseudo-globals unused. Both already compile to the
correct `asm!("mv {0}, tp", ...)` form (the transpiler fix confirmed in
round 1 for `klist.c` holds here too), so no hand-fix was needed for
the call itself — only the still-independently-declared
`current_stack_pointer` (dead) needed removing.

- **`lwq_rs.rs`**: `use ::macros::export;` (unused `warn_on` import
  too) dropped, `#[export]` → `#[no_mangle]` (2 sites); duplicate
  `current_stack_pointer` deleted; 8 RISC-V asm bracket-addressing
  sites fixed (issue #29's class, `amoswap.d.aqrl`/`lr.w`/`sc.w.rl`
  variants this time, not just `amocas.*`).
- **`debug_locks_rs.rs`**: same export fix (1 site); both duplicate
  statics deleted; 8 bracket-addressing sites (`amoswap.w.aqrl`
  variant).
- **`seq_buf_rs.rs`**: export fix, duplicate statics deleted, 3
  `kernel::warn_on!(...) != 0` → plain `kernel::warn_on!(...)` sites
  fixed — but ultimately **deferred from this round**, see below.
- **`kfifo_rs.rs`**: 1 `warn_on!(...) != 0` site fixed; duplicate
  statics deleted (not caught until a second full pass, since the
  first error-survey only surfaced the first blocking error per file —
  worth remembering that `make`'s fail-fast means a clean "0 errors"
  report from an early stage doesn't guarantee no later-stage issues
  in the same file).
- **`fonts/fonts_rs.rs`**: export fix, duplicate statics deleted, 2
  `warn_on!(...) != 0` sites fixed.
- **`lz4/lz4_decompress_rs.rs`**: duplicate statics deleted; new fix
  class — `::libc::memcpy`/`::libc::memmove`/`::libc::size_t` (c2rust's
  usual userspace/`std`-linked output convention) don't exist in this
  `no_std` kernel build. Added a `memcpy` extern declaration alongside
  the file's existing `memmove` one, then replaced all `::libc::`
  qualifications with the local unqualified names (`memcpy`, `memmove`,
  `size_t`) already declared/aliased in the same file.

### New gap: `seq_buf_printf`'s C-variadic definition needs `c_variadic`

`seq_buf_printf(struct seq_buf *s, const char *fmt, ...)` is a real
C-variadic function *definition* (not just a call site — c2rust output
already declares plenty of `extern "C" { fn foo(..., ...); }` call-only
bindings without issue). Defining one in Rust needs the unstable
`c_variadic` feature, which is not in `rust_allowed_features`
(`scripts/Makefile.build`) — that list is deliberately scoped to mirror
real upstream Rust-for-Linux's own vetted feature set (cites
`Rust-for-Linux/linux#2`).

Researched whether upstream Rust-for-Linux has a pattern for this: they
don't define genuinely variadic Rust functions anywhere. Their
`pr_info!`/`pr_err!` macros avoid the problem entirely — `rust/kernel/
print.rs` builds a `core::fmt::Arguments` at the Rust call site, then
calls C's `_printk` with a **fixed** 3-argument signature (format,
module, an opaque pointer to the `Arguments` value); the C side's
`%pA` format specifier calls back into a Rust formatter
(`rust_fmt_argument`) to consume it. That trick only works because RfL
controls both the caller and `_printk`'s own format-string parsing —
it doesn't generalize to an arbitrary pre-existing C variadic function
like `seq_buf_printf` that's called from many already-transpiled sites
we don't want to also rewrite.

Confirmed `c_variadic` itself compiles cleanly on this project's
existing toolchain (`rustc 1.97.0` via the same `RUSTC_BOOTSTRAP=1`
mechanism that already unlocks the other 4 allow-listed features) —
technically available, just not something upstream RfL has adopted.
Decided to defer rather than add it unilaterally: `seq_buf.c` stays
plain C for this round, `lib/Makefile` documents why inline. Revisit if
upstream ever adds `c_variadic` to their own allow-list, or if a
narrower non-variadic redesign of the call sites becomes worth it.

### New c2rust bug found: signed multiply overflow panics instead of wrapping

`tests/test_sort.c`'s own KUnit test seeds a bounded pseudo-random
sequence: `r = (r * 725861) % 6599`. `r` is always `< 6599` after each
iteration, but the *intermediate* product (`r` up to 6598 times
725861) reaches ~4.79 billion — over `i32::MAX` (~2.1 billion) before
the `% 6599` brings it back down. C's signed overflow wraps in
practice; c2rust emitted a bare Rust `*`, which panics under
`overflow-checks`:

```
rust_kernel: panicked at lib/tests/test_sort_rs.rs:4088:17:
attempt to multiply with overflow
Kernel BUG [#1]
```

Same root-cause class as the already-fixed `awtoau/c2rust#27`
(`refcount.h`'s `+`/`-` overflow-detection idiom, fixed via
`wrapping_add`/`wrapping_sub`) — this is the `*` case of the same gap.
Fixed locally via `r.wrapping_mul(725861)`; filed as
[`awtoau/c2rust#33`](https://github.com/awtoau/c2rust/issues/33) for a
general fix (translate signed `*`/`+`/`-` as `wrapping_mul`/
`wrapping_add`/`wrapping_sub` uniformly, not just for `+`/`-`).

### Round 3 outcome: clean 18-file boot (19 attempted, 1 deferred), 20/20 KUnit suites

- First full-image attempt (19 files, `seq_buf.c` included) failed at
  `modpost`/`ld.lld` link with `seq_buf_printf` undefined — the
  `c_variadic` gap above, correctly deferred.
- Deferring `seq_buf.c` initially still failed the same way on the
  first retry — traced to an editing mistake (the revert to plain C
  deleted the `ifdef`/`else`/`endif` wrapper *and* the fallback
  `lib-y += seq_buf.o` line inside it, leaving `seq_buf.c` wired into
  nothing). Fixed by re-adding the unconditional `lib-y += seq_buf.o`
  line; rebuilt clean.
- Second full-image attempt (18 files) built and linked clean, but
  panicked at boot in the `lib_sort` KUnit suite — the `test_sort.c`
  multiply-overflow bug above, not caught by the individual-file
  build (compiles fine; only *panics at runtime* when the test
  actually executes with `overflow-checks` on).
- Third attempt, after the `wrapping_mul` fix: clean build, clean
  boot, **20/20 KUnit suites pass** (0 fail, `lib_sort` now included
  and passing), `initramfs init reached, PID 1 alive`, zero
  panic/oops/BUG/WARN. `llvm-nm vmlinux.unstripped` confirms all 17
  Rust-transpiled files' representative symbols (`T`) plus
  `seq_buf_printf` correctly resolving to the plain-C object, not a
  Rust one. Boot-history row auto-committed/pushed. Archived at
  `docs/status/boot-logs/20260802T201123.527162-695890+1000-combined-boot-28-18file-v2.log`.

18 files now combined-boot-verified in one image, up from 8 — and,
unlike rounds 1–2, this round surfaced two genuinely new gap classes
(a toolchain-feature boundary, and a real transpiler correctness bug)
rather than just the by-now-familiar recurring ones. Both are handled
the way this project's own established discipline calls for: the
toolchain gap documented and deferred rather than unilaterally
resolved outside upstream's own choices, the transpiler bug fixed
narrowly and reported upstream rather than silently patched.
