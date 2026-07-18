# Target compile+execute test loop — scoping

2026-07-18. Scoping pass for a new verification stage: "add a Rust
compiler and Linux sources access to the riscv machine and get it to
compile the work we have done." Investigates what that idea needs to
become to be practical, given the constraints already known going in
(QEMU guest boots with `-m 256M`, no native riscv64 rustc exists
anywhere, `riscv64gc-unknown-linux-musl` is installed as a **cross**
target only). No implementation in this pass — read-only investigation of
toolchain state plus existing scripts (`scripts/check_c2rust_output_compiles.py`,
`scripts/boot_qemu.py`, `scripts/build_initramfs.py`, `scripts/diff_oracle.py`,
`bench/`), `linux-riscv/`, `linux/`, and the c2rust fork untouched.

## 1. "rustc inside the QEMU guest" is not the right shape — verified, not just asserted

Confirmed directly rather than assumed:

- `rustup target list --installed` on this host: `riscv64gc-unknown-linux-musl`
  is present, but only as one of eight **cross**-compile targets (alongside
  `riscv64imac-unknown-none-elf`, `aarch64-unknown-linux-musl`, etc.) under
  the host's own `stable-x86_64-unknown-linux-gnu` toolchain. There is no
  `rustc` binary anywhere on this machine that runs *as* riscv64 code —
  cross-compiling *for* riscv64 and running *on* riscv64 are unrelated
  capabilities, and only the former exists here.
- No native riscv64 rustc exists upstream as a realistic option either: a
  real `rustc` + its full bundled `rust-lld`/std source is several hundred
  MB even before a `cargo` registry cache, and would need to be
  cross-built or fetched as a separate riscv64-native rustc release — a
  much bigger undertaking than this task's scope, and orthogonal to the
  actual goal (see below).
- The QEMU guest environment itself rules it out independently of the
  toolchain question: `-m 256M` is barely enough for the kernel + a
  minimal busybox initramfs (`configs/initramfs-init.sh`, `scripts/
  build_initramfs.py`) — no persistent rootfs, no package manager, no
  writable disk. A "few hundred MB rustc" cannot be resident, let alone
  have room to actually invoke `rustc` (which itself needs a working set
  well beyond its on-disk size to run) inside that budget.

**Conclusion: the compiler must run on the host (x86_64), not the
target.** This isn't a close call — it's confirmed by three independent
constraints (no native toolchain exists, none is practical to build, and
the guest has no room for one even if it existed). The user's own framing
anticipated this might be the answer ("this is likely impractical as
literally stated" was given as known-context going in); this investigation
confirms it rather than assuming it.

## 2. What the idea actually needs to become

Re-reading the user's own words — "add a Rust compiler and Linux sources
access to the riscv machine and get it to compile the work we have
done" — the goal was never literally "run rustc on riscv64 hardware." The
goal is: **verify that in-progress/candidate Rust code is correct against
the real riscv64 target, cheaply, before committing to the full
boot-integration cycle.** "Compiler on the machine" was the most direct
mental model for "make the target machine tell me if this is right," not
a hard requirement that the compiler itself execute there.

Once that's separated out, the idea decomposes into two genuinely
different questions this doc treats separately, because streams 1-3
already answer parts of both and conflating them either duplicates
existing work or under-scopes what's actually missing:

1. **Does the candidate code compile correctly against the real riscv64
   ABI/types?** — Stream 1 (`check_c2rust_output_compiles.py`) already
   answers this, and answers it *well* (see §3). Not a gap.
2. **Does the candidate code produce correct output when it actually
   runs, under real riscv64 semantics (endianness, integer width/overflow
   behavior, alignment, ISA quirks)?** — Nothing today answers this for
   *candidate* (not-yet-landed) code without going through the full
   `dev.py integrate` + `dev.py check` kernel-rebuild-and-reboot cycle.
   **This is the actual gap**, and it's an execution/testing gap, not a
   "give the machine a compiler" gap.

## 3. What real target-ABI compile-checking already buys, and what it doesn't

Read `scripts/check_c2rust_output_compiles.py` in full to answer the task's
question honestly: what does *executing* on riscv64 add beyond what the
existing host-side check already captures?

The existing check is not a toy type-check. It runs `rustc --target
riscv64imac-unknown-none-elf --emit=metadata` linked against the **real,
already-built kernel's own** `libcore.rmeta`/`libbindings.rmeta`/
`libkernel.rmeta` (`linux-riscv/rust/*.rmeta`, produced by the actual
kernel build, not a scratch `-Zbuild-std`). That means:

- Real target `cfg`s (`target_arch="riscv64"`, `target_pointer_width="64"`,
  the kernel's own `#[cfg(...)]` gates) are in effect — not host x86_64
  ones.
- Real struct layouts/ABI from `libbindings.rmeta` (bindgen-generated
  against this exact kernel's headers) are checked — a c2rust file that
  gets a struct field type or FFI signature wrong against the *real*
  riscv64 kernel ABI fails here, not silently.
- This already exceeds what a same-target-triple `cargo build` alone would
  give, because it's linked against the project's own real, current
  libraries rather than freshly-built stubs.

So the type-checking/ABI-correctness half of "verify against the real
target" is **already done, and done well** — recreating it via actual
execution would be redundant, not additive.

**What compiling alone structurally cannot catch** — confirmed by
contrast with what `bench/diff_*.{c,rs}` (the tier-2.5 diff-oracle,
`scripts/diff_oracle.py`) exists specifically to catch, and by the 8250
scoping doc's own framing of the C-ABI-called / narrow-function staging
discipline:

- **Runtime logic bugs**: off-by-ones, wrong branch taken, wrong bit
  shifted, an integer overflow that wraps differently than intended —
  `rustc --emit=metadata` only type-checks, it never runs the generated
  code. A file can be perfectly well-typed and still compute the wrong
  answer. This is exactly the class of bug the two `kstrtox`/`memparse`
  overflow fixes in `work_items` (rows 14, 15) were — both are
  **type-correct, logic-wrong** bugs that a compile-only check cannot see
  by construction.
- **Actual riscv64 execution semantics that don't show up in `cfg`
  alone**: this is the one place actual riscv64 execution (real or
  QEMU-emulated) adds something a host x86_64 execution of the same logic
  might not, in principle — e.g. code relying on unspecified behavior that
  x86_64's characteristics happen to paper over. In practice, this
  project's `bench/diff_*` pairs are pure integer/bit/string logic with no
  architecture-dependent UB in play (checked: no raw pointer arithmetic
  relying on a specific width beyond what `u64`/`usize` already pin down,
  no inline asm, no `#[cfg(target_arch)]` branches in the diffed
  functions), so **host-native execution of the same logic already
  produces the same answer riscv64 execution would** for everything
  translated so far. This is why `scripts/diff_oracle.py` compiles and
  runs both the C and Rust sides **natively on the host** (`rustc -O
  --edition=2021`, no `--target` flag at all) and it's a completely valid
  oracle for this class of code — confirmed by reading the script, not
  assumed.

**Honest bottom line for this question**: for the kind of code this
project translates (`lib/`-style integer/bit/string logic), a host-native
diff-oracle run already captures correctness-on-real-inputs. Actual
riscv64 execution adds confidence, not new bug classes, *for this specific
code family*. The real value of riscv64 execution specifically (over
host-native) would show up for code with genuine architecture-dependent
behavior — inline asm, raw MMIO/register access, anything gated on
`#[cfg(target_arch = "riscv64")]` — which describes 8250's Tier B
(`mem_serial_in`/`mem_serial_out`, see `docs/serial-8250-translation-
scoping-2026-07-18.md`) far more than it describes any `lib/` TU landed so
far. That's a useful scoping signal in its own right: this tool is most
valuable for the exact kind of code stream 2 is about to start touching,
less valuable (though still non-zero, for confidence) for stream 4's
`lib/`-style candidates that a host diff-oracle already covers well.

## 4. "Linux sources access" — what it would concretely add, checked

The task asked whether candidate code needs real kernel headers/types at
compile time to be a meaningful test, or whether a standalone cross-compile
(core-only, like stream 1) captures the same signal.

Two cases, genuinely different:

- **c2rust output evaluated for stream 1/2** (e.g. a new file from a
  `crawl_c2rust_upstream.py` pass): stream 1's *existing* check already
  links against `libbindings.rmeta`/`libkernel.rmeta`, i.e. it already has
  the practical equivalent of "Linux sources access" for type-checking
  purposes — the real generated bindings, not synthetic stand-ins. No new
  access is needed for this case; a new tool would just reuse the same
  `-L linux-riscv/rust -–extern` pattern `check_c2rust_output_compiles.py`
  already has.
- **Hand-translation drafts not yet wired into `linux-riscv/lib/`** (the
  actual target audience for this new tool per §2): most of the 32 landed
  TUs are self-contained — pure functions over caller-supplied
  buffers/integers with no kernel-struct dependency (confirmed:
  `bitmap_rs.rs`, `kstrtox`, `string_rs.rs`, etc. take primitive
  slices/ints, not `struct uart_port` or similar kernel types). For this
  family, a standalone compile against `core` alone (no kernel headers at
  all) is sufficient and simpler — exactly what `bench/diff_*.rs` already
  does. Kernel-header/type access only becomes load-bearing once a
  candidate references a real kernel struct (8250's `struct uart_port`,
  `uart_config[]`), which is precisely stream 2/3 territory, not this
  tool's primary target.

**Conclusion**: "Linux sources access" is not a separate infrastructure
requirement to build — it already exists in the two forms that matter
(stream 1's `-L linux-riscv/rust` linking for kernel-struct-dependent
code, and a plain `core`-only build for the self-contained `lib/`-style
majority). A new tool should reuse whichever of those two linking modes
fits the candidate file, not invent a third.

## 5. Toolchain verification — what's actually on this machine, checked live

This is the part of the scoping that most changes the shape of a proposed
tool, so it was verified end-to-end rather than assumed from `rustup
target list` alone.

**Cross-compiling to a static riscv64 musl binary works, with one
important gotcha.** Two paths were tried:

1. `rustc --target riscv64gc-unknown-linux-musl -C target-feature=+crt-static
   -C linker=<riscv64-linux-musl-gcc>` (using the project's own already-
   cached `tmp/initramfs/riscv64-linux-musl-cross/` toolchain, the one
   `scripts/build_initramfs.py` already fetches from musl.cc to build
   busybox) — **fails to link**: `ld: mis-matched ISA version 2.1 for 'i'
   extension, the output version is 2.0`. This 2021-vintage musl.cc
   toolchain's `binutils` assumes an older RISC-V ISA-spec versioning
   scheme than the object files rustc's own prebuilt `riscv64gc-unknown-
   linux-musl` static libs (`libunwind.a` etc., shipped with the `rustup`
   target) were built with. Real, reproducible incompatibility — not a
   flag ordering issue.
2. `rustc --target riscv64gc-unknown-linux-musl -C target-feature=+crt-static
   -C link-self-contained=on -C linker-flavor=ld.lld` — **works cleanly**,
   using rustc's own bundled `rust-lld` instead of any external cross
   `binutils`, sidestepping the ISA-version mismatch entirely (no external
   toolchain involved in the link step at all). Verified: produces a real
   statically-linked riscv64 ELF (`file` confirms `statically linked`, no
   `interpreter` — i.e. no musl dynamic loader dependency).

**Execution: `qemu-riscv64-static` is already installed** (`qemu-user-
static-riscv-10.2.2-1.fc44`, confirmed via `rpm -q`/`rpm -ql` — binary at
`/usr/bin/qemu-riscv64-static`, binfmt_misc registration configs present
too). This is **usermode** QEMU emulation — it runs a single riscv64 ELF
directly as a host process (translating riscv64 syscalls to host Linux
syscalls), not a full guest boot. Verified end-to-end with a trivial
program: cross-compiled per (2) above, then `qemu-riscv64-static
./binary` executed it correctly and returned the right output/exit code —
**no `qemu-system-riscv64`, no kernel image, no initramfs rebuild, no
256MB budget, and no multi-second boot involved at all.**

This is the single biggest scoping finding of this pass: **the "execute
on target" half of the idea does not need `boot_qemu.py`, the initramfs,
or a kernel boot in the loop at all.** Usermode QEMU gives real riscv64
instruction-set execution (a genuinely different code path through QEMU's
TCG than the host's native x86_64 execution `diff_oracle.py` already
uses) in well under a second per binary, entirely decoupled from the
kernel build/boot cycle. This changes the tool from "reuse `boot_qemu.py`'s
`--run-id` parallel-boot machinery for a one-shot payload run" (the shape
sketched in this task's brief) to something structurally simpler and
faster: cross-compile, then invoke a static emulator binary directly, no
QEMU system-emulation machinery involved at all. `--run-id`'s parallel-boot
support remains valuable for stream 2/3's live-boot-path work, just not
for this tool.

## 6. Proposed design: `scripts/target_compile_test.py`

Not implemented in this pass, per the task brief — sketched here as the
concrete next step.

**Purpose**: fast (sub-second to low-single-digit-second) compile+execute
feedback loop for a candidate `.rs` file — either a not-yet-integrated
hand-translation draft, or a c2rust output file being evaluated — using
real riscv64 execution (via `qemu-riscv64-static`) as a second, independent
oracle alongside (not instead of) the existing host-native `diff_oracle.py`
and stream 1's ABI compile-check. Positioned as a cheap **intermediate**
step before `dev.py integrate` + `dev.py check`'s full rebuild-and-reboot
cycle — not a replacement for either the host diff-oracle or the live-boot
gate.

**Step by step:**

1. **Input**: a candidate `.rs` file plus (reusing `bench/diff_*.c`'s
   existing convention) a paired `.c` reference and a shared test-driver
   contract — argv-driven `N`/`SEED`, newline-separated output, matching
   `scripts/diff_oracle.py`'s existing harness shape exactly, so this tool
   is a second execution backend for the *same* `bench/diff_<name>.{c,rs}`
   pairs, not a parallel, incompatible format.
2. **Compile the C reference for riscv64** using the project's existing
   `riscv64-linux-musl-cross` toolchain (already fetched/cached by
   `build_initramfs.py`) — this side is unaffected by the ISA-mismatch
   gotcha found in §5 since it's a straight musl-gcc build, no rustc
   involved.
3. **Compile the Rust candidate for riscv64** using the verified-working
   flags from §5: `--target riscv64gc-unknown-linux-musl -C target-feature
   =+crt-static -C link-self-contained=on -C linker-flavor=ld.lld`. No
   external cross-toolchain dependency for this side at all (self-contained
   via `rust-lld`), which also sidesteps needing to keep the musl.cc
   toolchain's binutils version in sync with whatever `rustup` target libs
   this host has.
4. **Execute both binaries under `qemu-riscv64-static`** with the same
   `N`/`SEED` arguments `diff_oracle.py` already uses, capture stdout from
   each.
5. **Diff byte-for-byte**, same pass/fail shape as `diff_oracle.py`
   (`ORACLE PASS`/`ORACLE FAIL` with a mismatch sample) — deliberately the
   *same* verdict vocabulary, so this slots into the same mental model and
   any future dashboard/reporting code without inventing new status
   strings.
6. **Report both** the host-native (`diff_oracle.py`, already existing)
   and riscv64-emulated (this tool) verdicts side by side for a given
   target — a candidate that passes host-native but fails riscv64-emulated
   would be a genuinely new, actionable signal (architecture-dependent
   bug); one that passes both is expected/redundant-but-cheap for `lib/`-
   style code per §3, and directly useful (non-redundant) for register/
   MMIO/asm-shaped code per §3's Tier-B callout.
7. **Kernel-header-dependent candidates** (the minority per §4): same
   two-mode linking stream 1 already uses — link against
   `linux-riscv/rust/*.rmeta` when the candidate references real kernel
   types, plain `core`-only otherwise. This tool would detect which mode a
   given candidate needs the same way a human would today: does it
   `extern crate kernel`/`bindings`, or not.

**What this deliberately does NOT do**: does not attempt to run inside a
booted kernel, does not touch `configs/initramfs-init.sh` or
`scripts/boot_qemu.py`'s guest boot path, does not require any new
package installs (both `qemu-riscv64-static` and the musl cross-toolchain
already exist on this machine), and does not replace `dev.py check`'s
KUnit boot gate as the authority on "is this actually wired in and
correct in the live kernel" — it answers a narrower, cheaper, earlier
question in the pipeline.

**Estimated cost to build**: small. Steps 2-5 are a straightforward
extension of `scripts/diff_oracle.py`'s existing ~80-line structure (add a
riscv64 target/linker-flags variant of the two `sh([...])` compile calls,
swap direct execution for `qemu-riscv64-static <bin> ...` execution) — not
new machinery, a new backend for machinery that already exists and is
already trusted (the same pass/fail-diff shape 30+ landed TUs were
verified with).

## 7. Recommendation and priority

**"Put rustc in the guest" is not the right shape — confirmed, not just
assumed going in** (§1). **The right shape is: cross-compile on host using
rustc's self-contained `rust-lld` (verified working, §5), execute via
already-installed `qemu-riscv64-static` usermode emulation (verified
working end-to-end, §5), reusing `bench/diff_*` pairs and `diff_oracle.py`'s
existing harness contract as a second execution backend rather than new
machinery** (§6).

This is genuinely new verification capability (an intermediate rung
between "type-checks against real ABI" and "boots and passes KUnit in the
live kernel"), not a fix for something broken, and for the `lib/`-style
code translated so far the *marginal* signal over the existing host-native
diff-oracle is real but modest (§3) — most value would show up once
stream 2/3's register/MMIO/asm-shaped candidates (8250 Tier B and beyond)
start needing evaluation, which is not yet the case for anything currently
in flight. That combination — real but not urgent, and most valuable for
work that hasn't started yet — puts this at **P3**: worth building because
it reuses ~existing, already-verified machinery cheaply (small implementation
cost per §6) and unblocks a better evaluation path for upcoming
register-level translation work, but nothing in the project is blocked on
it today the way stream 2's boot-integration or c2rust's compile-rate work
is.

**Concrete first step**: implement `scripts/target_compile_test.py` per
§6 against one already-landed `bench/diff_*` pair first (e.g. `diff_bcd`
or `diff_win_minmax` — smallest existing pairs, fastest iteration on the
harness itself) to validate the riscv64-emulated backend end-to-end
against a known-good oracle result before pointing it at any real
not-yet-integrated candidate.
