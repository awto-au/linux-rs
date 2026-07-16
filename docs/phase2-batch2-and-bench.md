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

## Stabilised numbers (min-of-5, pinned core — supersede the table above)

Naive single runs swung ±2× in BOTH directions between runs (turbo/
scheduling noise). Pinned + min-of-5 + interleaved:

| func | C -O2 | C -O3 | faithful | Δ vs C | optimised |
|---|---:|---:|---:|---:|---:|
| gcd | 62.1 | 62.2 | 60.3 | **-3%** | 122.6 (2× slower) |
| int_sqrt | 21.5 | 21.6 | 20.7 | **-3%** | **9.9 (2.2× faster)** |
| int_pow | 2.43 | 2.48 | 2.41 | **-1%** | — |
| intlog2 | 1.55 | 1.58 | 1.52 | **-2%** | — |

All four faithful translations pass the **±10% perf-parity gate**
(rule 0011, Dan's proposal): any translated function outside the band
stops the line for cause analysis, and the cause catalogue feeds the
Phase 2.5 optimisation lane's rule set. Methodology is mandatory in the
rule — the phantom swings of unpinned runs are exactly what it exists
to prevent.

## Why the "idiomatic" gcd lost — the kernel documented it in 2016

Kernel commit `fff7fb0b2d90` ("lib/GCD.c: use binary GCD algorithm instead
of Euclidean", Zhaoxiu Zeng, 2016) is a benchmark tournament of five gcd
variants with full source and numbers in the commit message: Euclidean
division (gcd0, ~10,000 units), basic binary GCD (gcd1, ~2,100), even/odd
(gcd2, ~2,800), binary **with `==1` early exits** (gcd3, ~2,030 — the
winner, today's fast path), even/odd with early exits (gcd4 — today's
fallback). Our "idiomatic" rewrite is essentially **gcd1 with an extra
`trailing_zeros` per iteration — a shape the kernel already measured and
rejected**. The early exits matter because random input pairs are coprime
with probability 6/π² ≈ 61%, so `gcd == small` dominates and the `a == 1`
check terminates most loops almost immediately; the "clean" `while a != b`
loop has no such exit and pays two bit-scans per iteration.

Lesson for the rule DB: kernel C that *looks* baroque frequently encodes a
benchmark result. The optimisation lane must check `git log` of the
original file for prior tournaments before proposing "cleaner" algorithms
— provenance cuts both ways.
