# Phase 2 batch 2 — four more TUs + the first benchmark

2026-07-16. Patch: `patches/0002-*` (kernel branch `linux-rs/phase2-gcd`).

## Translations: lcm, int_log, int_pow, int_sqrt

**58/58 KUnit vectors pass** on the booted riscv64 kernel (gcd 11, int_log
17 — including the `WARN_ON` zero paths via `kernel::warn_on!` with real
taint semantics — int_pow 9, int_sqrt 21). Boot log at a stable path now:
`scripts/boot_qemu.py` → `tmp/qemu-boot.log` (tail-able every run).

Firsts: Rust→Rust cross-TU call via the C ABI (`lcm`→`bindings::gcd`);
licence preservation (int_log stays LGPL-2.1-or-later); C integer-promotion
arithmetic reproduced exactly (int_log interpolation); EXPORT_SYMBOL
(non-GPL) → `#[export]`'s auto-GPL noted as a reviewed deviation
(acceptable while CONFIG_MODULES=n; tracked in rule 0001).

New rules: 0006 fls/__fls→leading_zeros (1-based/0-based variants),
0007 likely/unlikely dropped (hint only), 0008 WARN_ON→warn_on!,
0009 implicit C unsigned wrap→wrapping_mul, 0010 GNU `?:` elvis.

## Benchmark (host x86, same LLVM 22 backend; `scripts/bench_math.py`)

10M fixed-seed LCG inputs; checksums agree across all implementations;
optimised variants property-checked against faithful over 1M inputs first.

| func | C -O2 | C -O3 | Rust faithful | Rust optimised |
|---|---:|---:|---:|---:|
| gcd | 100.5 ns | 61.4 | 66.5 | 121.7 (!) |
| int_sqrt | 37.9 | 21.1 | 21.1 | **10.0** (`u64::isqrt`) |
| int_pow | 4.6 | 2.4 | 2.4 | — |
| intlog2 | 2.5 | 1.5 | 1.5 | — |

Three conclusions (host-indicative; FPGA numbers later):

1. **No translation tax.** At equal opt level, faithful Rust ≡ C (same
   LLVM). The main perf argument against faithful-first is dead.
2. **The optimisation lane is real but must be measured**: std `isqrt` is
   2.1× faster than the kernel's shift-and-subtract.
3. **"Idiomatic" ≠ faster**: the branch-reduced "clean" gcd was ~2×
   SLOWER than the kernel's early-exit Stein variant. Kernel C is often
   already tuned; PLAN Phase 2.5's per-function, benchmark-gated, opt-in
   policy is confirmed — no blanket rewrites.

Path impact of the bench: none required — it validates the current
staging. The optimisation subagent (Phase 2.5) gets a work queue from the
purity census and must beat the faithful version on measurement or the
faithful version ships.
