# Phase 2 — first translated TU: `lib/math/gcd.c` → Rust, in-tree, boot-validated

2026-07-16. Patch: [patches/0001-lib-math-replace-gcd-with-Rust-translation-linux-rs-.patch](../patches/0001-lib-math-replace-gcd-with-Rust-translation-linux-rs-.patch)
(applies to v7.1; also on branch `linux-rs/phase2-gcd` of the local worktree).

## What happened

`lib/math/gcd.c` (binary/Stein GCD + even/odd fallback) is now
`lib/math/gcd_rs.rs` in the riscv64 slim kernel: **zero unsafe outside the
one static-key read**, `#[export]`-checked signature, C original retained
behind `!CONFIG_RUST`.

Oracle results:

| Tier | Result |
|---|---|
| 1 compile | ✅ `RUSTC lib/math/gcd_rs.o`, kernel links |
| 2 ABI/symbol | ✅ `gcd` T + `efficient_ffs_key` D in System.map; `#[export]` verifies the prototype against `<linux/gcd.h>` at compile time |
| 3+4 KUnit on booted kernel | ✅ **all 11 `GCD_KUNIT_TEST` vectors pass** on `qemu-system-riscv64 -M virt` (incl. zero and `ULONG_MAX` edges) |

## The semantic trap that validates the whole methodology

`gcd.c` contains `DEFINE_STATIC_KEY_TRUE(efficient_ffs_key)` — it *looks*
like a constant-true fast-path switch, and a shape-based translation would
emit only the binary-GCD path. But `arch/riscv/kernel/setup.c` **disables
that key at boot when the CPU lacks Zbb** — and QEMU's virt CPU has no Zbb,
so on our exact target the "dead" even/odd path is the live one. The KUnit
pass above exercised the fallback path; a naive translation would have
appeared correct on x86 and been silently wrong on the shipping target.
This is PLAN's "identical shape, different semantics" risk caught in the
very first TU — context-keyed rules (tier 3, human-gated) are not optional.

Two boundaries drawn:
- **Data with linker-section semantics stays in C** for now: the
  `DEFINE_STATIC_KEY_TRUE` lives in a 12-line `gcd_key.c` stub; Rust reads
  the key by value (`static_key_count`) — value-equivalent to
  `static_branch_likely`, minus branch patching (fine off hot paths; the
  kernel crate only has `static_branch_unlikely!` for false-keys in v7.1).
- **C unsigned wrap-arithmetic** must be either proven non-wrapping or made
  explicit (`wrapping_neg` for `r & -r`).

## Rules earned (rulesdb/rules/)

`0001 export-symbol-gpl`, `0002 ffs-trailing-zeros`, `0003 swap-mem-swap`,
`0004 unsigned-negate-isolate-lsb`, `0005 static-key-branch` (tier 3,
human_review=true, with negative examples). Rule format: rulesdb/README.md.
Every future manual fix lands the same way — rules, not file patches.

## Reproduce

```
cd linux-riscv && git checkout linux-rs/phase2-gcd
make ARCH=riscv LLVM=1 olddefconfig && make ARCH=riscv LLVM=1 -j$(nproc)
qemu-system-riscv64 -M virt -m 256M -nographic -no-reboot \
  -kernel arch/riscv/boot/Image -append "earlycon=sbi panic=-1"
# expect: "ok 7 math-gcd" / pass:11 fail:0 in the boot log
```
