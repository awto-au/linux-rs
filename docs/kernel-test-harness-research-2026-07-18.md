# Kernel test harness research — what else exists, what's actually usable

2026-07-18. Survey of established Linux kernel test frameworks beyond the
project's current 15-suite KUnit boot-and-check pipeline
(`scripts/boot_qemu.py` + `dev.py check`), to find real, adoptable
infrastructure rather than build something bespoke. Five areas investigated
per the task brief; verdicts below are as-found, including negative ones.

## 1. kselftest (`tools/testing/selftests/`) — not practical here, in general

`linux-riscv/tools/testing/selftests/` has 136 subdirectories. The normal
execution model (`Documentation/dev-tools/kselftest.rst`, `tools/testing/
selftests/lib.mk`) is: cross-compile each test as a standalone C binary on
the **host** build machine, `rsync`/install it into a **running** target
system (`make TARGETS=... install`), then execute it there against a kernel
matching the source tree. `lib.mk` assumes a real C toolchain producing
dynamically-linked (or at least glibc-flavoured, `-D_GNU_SOURCE`) binaries
with no static-link default — a mismatch against this project's musl-static
riscv64-only cross toolchain (`scripts/build_initramfs.py`, `riscv64-linux-
musl-cross`), which exists solely to build busybox, not arbitrary kselftest
C sources.

**The Rust-specific subset (`tools/testing/selftests/rust/`) is not what it
sounds like.** Contents: a 3-line Kconfig fragment (`CONFIG_SAMPLE_RUST_MINIMAL`/
`CONFIG_SAMPLE_RUST_PRINT`) and a `Makefile`/`test_probe_samples.sh` that
just load/unload the existing Rust *sample modules* under `samples/rust/` and
grep dmesg for known-good strings. It is not a real assertion-driven test
suite, and it requires **loadable module support** (`insmod`/`rmmod`) —
this project's kernel boots to a static busybox `/init`, no module loader,
no module build wired up at all currently.

Architecture-agnostic-looking subsets (`lib/`, `kcmp/`) are individually
small, but each still needs the full kselftest harness scaffolding
(`kselftest_harness.h`, TAP-ish output parsing distinct from KUnit's KTAP,
a shell test-runner invoked from a real rootfs) — cross-compiling and
wiring even one in is a non-trivial new subsystem (build + install + run +
parse), not a config flip. **Verdict: real infrastructure, but the
userspace/module-loading assumptions this project deliberately doesn't have
make it a substantial undertaking, not a quick win. Skip for now.**

## 2. KUnit's broader surface — confirmed, and one suite enabled as proof

Grepped `rust/kernel/**/*.rs` for `#[...kunit_tests(...)]` test modules and
cross-referenced against `linux-riscv/.config`. Rust-for-Linux ships exactly
**7** built-in Kconfig-gated Rust KUnit suites in `rust/kernel/Kconfig.test`
(`RUST_ALLOCATOR_KUNIT_TEST`, `RUST_KVEC_KUNIT_TEST`, `RUST_BITMAP_KUNIT_TEST`,
`RUST_KUNIT_SELFTEST`, `RUST_STR_KUNIT_TEST`, `RUST_ATOMICS_KUNIT_TEST`,
`RUST_BITFIELD_KUNIT_TEST`) plus the separate `RUST_KERNEL_DOCTESTS` flag
(doctests-as-KUnit, see §5). Before this session, 6 of the 7
`Kconfig.test` suites were enabled; only **`CONFIG_RUST_BITFIELD_KUNIT_TEST`**
was off (`# CONFIG_RUST_BITFIELD_KUNIT_TEST is not set`), guarding a 311-line
`mod tests` in `rust/kernel/bitfield.rs:551` (`rust_kernel_bitfield` suite) —
present in the tree, unused, for free. This is genuinely the same class of
infra as the other 6 (built-in Rust-for-Linux plumbing, not translated
project code), so enabling it is consistent with the existing 15.

The C-side `lib/*_kunit.c` KUnit suites (crypto hashes, `overflow`, `glob`,
`printf`/`scanf`, `siphash`, `uuid`, `hashtable`, `fortify`, ~40 files found)
are a much larger pool, but none map to this project's translated Rust code
yet (0 of 30 translated TUs currently correspond to an as-yet-unenabled C
KUnit suite — the ones already on, `bitops`/`cmdline`/`rational`/`math-*`/
`list_sort`/`lib_sort`, are the ones that do map). Enabling more of the C
pool would test C paths this project hasn't touched in Rust, out of scope
for "verify the translations."

**Tested live** (tree was idle — `linux-riscv` HEAD `5c1e0543` from this
morning, `git status` clean, no concurrent activity):

```
python3 scripts/dev.py config -e RUST_BITFIELD_KUNIT_TEST
python3 scripts/dev.py check
```

Result: **16/16 suites pass** (all 15 baseline suites green, plus new
`ok 4 rust_kernel_bitfield`), vector count **136 → 143**, `INIT REACHED`
still verified, `docs/STATUS.md`/`status.png`/`history.csv` auto-regenerated
by `dev.py check`'s normal reporting path. `.config` is gitignored inside
`linux-riscv/` (kernel convention, confirmed via `git check-ignore -v
.config` → matched by `.gitignore:13`), so there is nothing to commit in
`linux-riscv/` itself — `docs/STATUS.md` in this repo is the durable record,
plus this report. **This is a real, verified, zero-new-tooling win, already
applied to the live tree.**

## 3. LTP (Linux Test Project) — not practical here

LTP (github.com/linux-test-project/ltp) is a separate ~1000-binary userspace
test suite requiring: its own autotools/make build, a real filesystem with
`/opt/ltp` install layout, dynamic linking against glibc (no musl-static
build path maintained upstream as a first-class target), and typically a
shell (`bash`, not just busybox `ash`) plus dozens of standard userspace
tools LTP's test scripts shell out to (`awk`, `bc`, various `/proc` and
`/sys` assumptions about a fuller distro). This project's initramfs is
deliberately minimal — one static busybox binary and a hand-written `/init`
— the opposite end of the spectrum from what LTP assumes. Standing up even
a small honest LTP subset would mean building a real userspace from
scratch, which is a multi-week project on its own, orthogonal to translation
verification. **Verdict: not practical at this project's current stage. No
partial/cherry-picked recommendation — LTP's per-test scripts are not
designed to run standalone outside its harness/install layout.**

## 4. syzkaller / fuzzing — premature, correctly out of scope

syzkaller needs: a fuzzing-capable target (typically KVM/QEMU with a full
disk image, not a throwaway initramfs-only boot), a real network/serial
management loop for corpus feedback, coverage instrumentation
(`CONFIG_KCOV`, not currently enabled), and — most importantly — enough
*surface area* to be worth fuzzing. This project has translated 30 library
TUs (string/bitmap/math helpers), not syscalls, drivers, or anything with
attacker-reachable input parsing at the level fuzzing targets. Fuzzing a
`gcd()` implementation is not a meaningful use of the tooling. **Verdict:
correctly out of scope until there's a syscall-facing or parser-heavy
translated surface — revisit if/when translation work reaches something
like `bitmap_parse()`-style user-input parsing at a syscall boundary, still
likely 30+ TUs away.**

## 5. Rust-for-Linux's own broader test infra

`Documentation/rust/testing.rst` (read in full) describes three sorts of
Rust-specific tests: KUnit suites (§2, already used), `#[test]`
doctests-as-KUnit (`CONFIG_RUST_KERNEL_DOCTESTS`), and host-side `rusttest`
(a `make LLVM=1 rusttest` target for the `macros` crate's own examples,
run on the *build host*, not the target — not applicable to a QEMU-boot
verification pipeline at all).

**`CONFIG_RUST_KERNEL_DOCTESTS`** (currently `# ... is not set`, confirmed
in `linux-riscv/.config`) is more interesting than the doc's UML/`kunit.py`
example implies: `rust/Makefile` shows it generates
`doctests_kernel_generated_kunit.c`/`.o` as a **normal in-tree object**
(`obj-$(CONFIG_RUST_KERNEL_DOCTESTS) += doctests_kernel_generated_kunit.o`),
and its Kconfig entry (`lib/Kconfig.debug:3613`) only requires `RUST &&
KUNIT=y` — no UML or host-only restriction. So in principle it *can* run
under this project's existing boot-time KUnit path, same mechanism as the
16 suites above, not a different `cargo test`-at-build-time execution
model as the task brief speculated. However it compiles **every doc example
in the entire `kernel` crate** as a KUnit suite (`Documentation/rust/
testing.rst` shows an upstream example with 59 sub-tests from a handful of
files) — a much larger, less-audited surface than the single, self-contained
`rust_kernel_bitfield` module, and higher risk of pulling in a doc example
that doesn't compile cleanly against this tree's current subset of the
`kernel` crate. Left untested this session per the task's risk guidance
(materially different blast radius than a single isolated suite) —
documented here as the next concrete candidate, not applied.

## Recommendation

**Already done, verified, low-risk:** `RUST_BITFIELD_KUNIT_TEST` is enabled
and boot-verified (16 suites, 143 vectors, full baseline intact). No further
action needed for this one — it's live on the shared `linux-riscv` tree.

**Next concrete candidate, not yet attempted (higher blast radius, worth a
dedicated pass):**

```
python3 scripts/dev.py config -e RUST_KERNEL_DOCTESTS
python3 scripts/dev.py check
```

This is the highest-value *remaining* lead: it reuses the exact same
boot-KUnit mechanism (zero new tooling, confirmed via `rust/Makefile` +
`lib/Kconfig.debug`), but touches much more surface (every public-item
doctest in `kernel`), so it deserves its own isolated test run — not bundled
into this session's already-verified change — with attention to whether it
inflates build time meaningfully and whether every doctest in this tree's
(possibly pruned/older) `kernel` crate subset actually compiles clean.

kselftest, LTP, and syzkaller are all real, well-established tools, but each
assumes infrastructure (dynamic/glibc userspace, module loading, full
rootfs, coverage-instrumented fuzzing target) that this project has
deliberately not built yet and that building would be a project-sized
effort in its own right, disproportionate to verifying library-level C to
Rust translations. None are false starts — they're just the right tool for
a later, more mature stage of this project (real userspace, syscall-facing
translated code) rather than now.
