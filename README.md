# linux-rs

**Pattern-learning C→Rust translation of the Linux kernel.**

## End goal

Boot a machine-translated Rust Linux kernel on a **RISC-V soft core running
on a [Cynthion](https://greatscottgadgets.com/cynthion/)** (Great Scott
Gadgets, Lattice ECP5 FPGA) — a LiteX/VexRiscv-style SoC. That target is
deliberately extreme: a minimal rv32 kernel config, tight RAM, every byte of
the boot path exercised. If the pipeline can produce that kernel, the
approach works.

The x86_64 corpus used during development is the *lab* (fast build/boot
iteration on a 32-core box); RISC-V is the *shipping target*, and its
minimal config is a far smaller corpus than any desktop defconfig.

## Thesis

Linux is not ~30M lines of unique logic — it is a bounded vocabulary of
recurring idioms (locked regions, refcount get/put, list traversal, RCU
sections, error-goto ladders, MMIO access, callback registration) composed
millions of times. So:

1. **Learn rules, not files.** Every solved translation becomes a general
   pattern→Rust rule, validated against *all* structurally-equivalent
   occurrences in the corpus, never a one-off patch.
2. **The agent invents rules, it does not translate functions.** LLM effort
   goes into "this AST subtree matches no rule — infer the general
   transformation and validate it everywhere", not into per-file rewriting.
3. **The pattern knowledge base is the product.** The Rust kernel is the
   proof it works.

Details, phase gates, validation oracle, prior-art map: **[PLAN.md](PLAN.md)**.

Prior art, stated precisely (2026-07): Rust *in* Linux exists
(Rust-for-Linux — hand-written new code, no translation); clean-slate Rust
kernels that boot real Linux userspace exist (Asterinas, Moss, Kerla);
historical Linux has been reimplemented (linux-0.11-rs); and academic C→Rust
translation tops out at userspace projects. What has not been demonstrated —
and is this project's claim — is **corpus-scale, pattern-learning,
incrementally validated translation of a current Linux tree that preserves
its internal architecture**. The booting Rust kernels are used here as a
target-design corpus: [docs/reference-projects.md](docs/reference-projects.md).

## Translation discipline: faithful, not clever

Construct-by-construct conversion, **no optimisation** — the output must be
behaviourally identical to the C, not improved:

- **The oracle certifies equivalence, never improvement.** Every tier
  (ABI diff, KUnit differential, boot) compares Rust against the C
  original; an "optimised" translation makes differential testing
  meaningless and review unscalable.
- **Optimisation is the compiler's job.** LLVM sees both versions.
- **Deviations are never silent.** When Rust forces a difference it goes in
  the rule's `deviations` field with justification — and so far every one
  is strictly *safer* (explicit `wrapping_*` where C wraps implicitly,
  `trailing_zeros(0)` defined where `__ffs(0)` is UB, `#[export]`'s
  compile-time prototype check).
- **Not literally line-by-line:** structure maps (`do{}while(0)` shells
  vanish, `goto err` ladders become early returns, list macros become
  iterators) but semantics, side-effect order and error behaviour must not.
- **Making it nicer is a separate pass.** The Stage-3 safety lift (raw
  pointers → guards and `kernel`-crate types) transforms representation,
  never algorithms, and runs only on rule-validated instances with the
  unsafe-first version as its differential baseline.

Worked example: the first translated TU kept *both* GCD algorithms and the
runtime static-key dispatch, though the key looked constant — riscv
disables it at boot without Zbb, so the "dead" path is the live one on our
target ([docs/phase2-first-translation.md](docs/phase2-first-translation.md)).

## Status

| Date | Milestone |
|---|---|
| 2026-07-16 | Phase 0 complete: kernel v7.1 pinned, x86_64 defconfig+RUST, LLVM=1 build + QEMU boot verified, Rust-for-Linux working, coccinelle/c2rust evaluated ([docs/phase0-evals.md](docs/phase0-evals.md)) |
| 2026-07-16 | Phase 1 v0 census: 85,773 functions fingerprinted in 76 s. Whole functions do **not** collapse (8.4%); external call vocabulary does (top-2000 APIs cover 51% of functions' call surface) ([docs/phase1-census-v0.md](docs/phase1-census-v0.md)) |
| 2026-07-16 | **Phase 1 v1 census — GATE: GO.** 1.44M statement instances → 199 families cover 50%, 15 cover 25% ([docs/phase1-census-v1.md](docs/phase1-census-v1.md)) |
| 2026-07-16 | **Phase 1 v2: tail is cheap composition.** Singleton statements have median **2** novel glue nodes; 91% of their AST is already-common subtrees ([docs/phase1-census-v2-composition.md](docs/phase1-census-v2-composition.md)) |
| 2026-07-16 | Phase 2 re-scoped: minimal **riscv64** boot path; corpus measured at **511 TUs (~16% of lab)**, slim-serial kernel **boots in QEMU** ([docs/phase2-minimal-target.md](docs/phase2-minimal-target.md)) |
| 2026-07-16 | **Correctness review → census v1.1/v2.1.** Macro-internal inflation, brace bias, type erasure fixed. Corrected gate: **26 families = 25%, 713 = 50%** of 1.06M statements — GO stands; tail: median **1** non-root glue node ([docs/review-findings-2026-07-16.md](docs/review-findings-2026-07-16.md)) |
| 2026-07-16 | Reference corpus added: Asterinas, Moss, Kerla, linux-0.11-rs, rCore as target-design evidence ([docs/reference-projects.md](docs/reference-projects.md)) |
| 2026-07-16 | **First translated TU running in the kernel.** `lib/math/gcd.c` → Rust, in-tree on the riscv64 target, all 11 KUnit vectors pass on the booted kernel — including the static-key fallback path a naive translation would have dropped. 5 rules extracted ([docs/phase2-first-translation.md](docs/phase2-first-translation.md)) |
| 2026-07-16 | **First tier-2 TU: `lib/sort.c` heapsort in Rust — `ok 11 lib_sort` on the booted kernel.** Sentinel fn-pointers, raw `void*` arithmetic, callback dispatch, `cond_resched` C shim; rules 0012–0014 (patches/0003) |
| 2026-07-16 | **Batch 2: five Rust TUs in the booted kernel, 58/58 KUnit vectors.** lcm/int_log/int_pow/int_sqrt; first Rust→Rust cross-TU call; rules 0006–0010. Benchmark: **faithful Rust ≡ C** at equal opt level; `isqrt` optimised lane 2.1× — but "idiomatic" gcd 2× slower, so per-function measurement gates the lane ([docs/phase2-batch2-and-bench.md](docs/phase2-batch2-and-bench.md)) |

## Layout

```
PLAN.md      the plan: phases, gates, validation oracle, risks
docs/        phase reports and findings (start here for results)
scripts/     census/analysis tooling (Python, libclang)
rulesdb/     pattern rules (SmPL + the growing rule DB)
linux/       pinned kernel tree (local clone, not committed)
tmp/         scratch + logs (not committed)
```

## Reproduce

Fedora 44-ish with clang/LLVM 22, rustc ≥1.97, bindgen, QEMU. Then:
clone a kernel at **v7.1**, `make LLVM=1 defconfig`, enable `CONFIG_RUST`,
build, `scripts/clang-tools/gen_compile_commands.py`, and run the scripts in
[scripts/](scripts/). Each writes its log to `tmp/`.
