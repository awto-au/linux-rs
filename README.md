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
Nobody has done this (checked 2026-07: the ~40-paper C→Rust literature tops
out at userspace projects; Rust-for-Linux writes new code, translates
nothing).

## Status

| Date | Milestone |
|---|---|
| 2026-07-16 | Phase 0 complete: kernel v7.1 pinned, x86_64 defconfig+RUST, LLVM=1 build + QEMU boot verified, Rust-for-Linux working, coccinelle/c2rust evaluated ([docs/phase0-evals.md](docs/phase0-evals.md)) |
| 2026-07-16 | Phase 1 v0 census: 85,773 functions fingerprinted in 76 s. Whole functions do **not** collapse (8.4%); external call vocabulary does (top-2000 APIs cover 51% of functions' call surface) ([docs/phase1-census-v0.md](docs/phase1-census-v0.md)) |
| 2026-07-16 | **Phase 1 v1 census — GATE: GO.** 1.44M statement instances → 199 families cover 50%, 15 cover 25% ([docs/phase1-census-v1.md](docs/phase1-census-v1.md)) |
| 2026-07-16 | **Phase 1 v2: tail is cheap composition.** Singleton statements have median **2** novel glue nodes; 91% of their AST is already-common subtrees ([docs/phase1-census-v2-composition.md](docs/phase1-census-v2-composition.md)) |
| 2026-07-16 | Phase 2 re-scoped: minimal **riscv64** boot path (aliveness heartbeat, no serial), config-trim measurement first ([PLAN.md](PLAN.md)) |

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
