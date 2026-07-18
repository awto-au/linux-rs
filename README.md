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

**Live dashboard: [docs/STATUS.md](docs/STATUS.md)** — graphs + tables
regenerated on every validated boot (`dev.py check`); **two-track
dashboard: [docs/status/dashboard.html](docs/status/dashboard.html)** adds
the work-item queue and `awtoau/c2rust` fork timeline. Full milestone log:
**[docs/HISTORY.md](docs/HISTORY.md)**. Most recent:

| Date | Milestone |
|---|---|
| 2026-07-18 | Kernel-track work items moved from hand-curated DB rows to real GitHub issues — both tracks now sync from real issue trackers, not memory |
| 2026-07-18 | Boot-log history system: every boot archived, diffable, browsable, auto-committed+pushed |
| 2026-07-18 | Interactive console milestone: minimal initramfs `/init` drops to a live `sh` prompt, verified genuinely interactive |
| 2026-07-18 | **Hybrid boot-path milestone: first Rust code in a live device driver** — `8250_port.c`'s `serial8250_compute_lcr()` now calls Rust under `CONFIG_RUST` ([docs/hybrid-boot-milestone-2026-07-18.md](docs/hybrid-boot-milestone-2026-07-18.md)) |
| 2026-07-18 | 32 TUs, 16 KUnit suites / 143 vectors green; two-track work-item dashboard added |

## Layout

```
PLAN.md      the plan: phases, gates, validation oracle, risks
docs/        phase reports and findings (start here for results)
scripts/     census/analysis tooling (Python, libclang)
rulesdb/     pattern rules (SmPL + the growing rule DB)
linux/       pinned kernel tree (local clone, not committed)
tmp/         scratch + logs (not committed)
```

## Tooling & credits

Besides the Linux kernel and Rust-for-Linux themselves (see [Thesis](#thesis)
above), this project's pipeline relies on:

- **[c2rust](https://github.com/immunant/c2rust)** ([Immunant](https://immunant.com/)) —
  the base C→Rust transpiler; this project maintains a fork,
  [`awtoau/c2rust`](https://github.com/awtoau/c2rust), with fixes for
  kernel-specific translation gaps (see `docs/status/dashboard.html`'s issue
  timeline for what's changed and why).
- **[Ollama](https://ollama.com/)** running local open-weight coder models
  (tested: [Qwen2.5-Coder](https://github.com/QwenLM/Qwen2.5-Coder) 14B/32B,
  [DeepSeek-Coder](https://github.com/deepseek-ai/DeepSeek-Coder) 33B) as a
  first-drafter for small, well-scoped fixes to the c2rust fork — always
  behind a compiler-retry gate and independent verification before a draft
  is trusted or merged; never unsupervised (see `docs/streams.md`'s
  c2rust-breadth stream for the gating discipline and why it's mandatory).
- **[clang/LLVM](https://llvm.org/)** and **[bindgen](https://github.com/rust-lang/rust-bindgen)** —
  AST export and FFI binding generation the census/translation tooling is
  built on.

## Reproduce

Fedora 44-ish with clang/LLVM 22, bindgen, QEMU. Then:
clone a kernel at **v7.1**, `make LLVM=1 defconfig`, enable `CONFIG_RUST`,
build, `scripts/clang-tools/gen_compile_commands.py`, and run the scripts in
[scripts/](scripts/). Each writes its log to `tmp/`.

`linux-riscv/` (the actively-built tree, distinct from the pristine
`linux/` reference above) builds with **nightly Rust**, via
`rustup override set nightly` scoped to that directory (`rustup override
list` to check; `rustc`/`cargo` outside that directory are unaffected).
Switched 2026-07-18 so c2rust's raw transpile output — which uses
nightly-only `#![feature(...)]` attributes (`raw_ref_op`, `extern_types`,
`core_intrinsics`, ...) — can be compile-checked against the same
`libcore`/`libbindings`/`libkernel` the real kernel build produces,
instead of a disposable scratch-dir core build. Re-verified boot-clean
(`dev.py check`: 15/15 KUnit suites, `INIT REACHED`) immediately after
the switch — not a speculative change.
