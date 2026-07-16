# linux-rs — pattern-learning C→Rust translation of Linux kernel code

Plan drafted 2026-07-16 from the initial concept discussion.

**End goal (added 2026-07-16):** boot the translated Rust kernel on a RISC-V
soft core on a **Cynthion** (Great Scott Gadgets, Lattice ECP5) — i.e. a
LiteX/VexRiscv-class rv32 SoC with a minimal kernel config. x86_64 is the
development lab; RISC-V is the shipping target (and its tiny config is a far
smaller final corpus than the lab defconfig).

## Thesis

Linux is not 30M lines of unique logic; it is thousands of instances of a few
hundred structural/semantic idiom families (locked access, refcount get/put,
list traversal, RCU read sections, error-pointer returns, callback tables,
MMIO, initcall registration…). A translator that **learns validated
pattern→Rust rules** should see automatic coverage grow superlinearly: solve a
pattern once, apply it to every structurally-equivalent occurrence. The agent
(LLM) is used as a **rule inventor** for unmatched constructs, not as a
per-function code generator. The durable artifact is the **pattern knowledge
base**, not any single translated file.

This thesis is plausible but **unproven**. Phase 1 exists to test it cheaply
before building a translator.

## Prior art — build on, don't reinvent

| Tool | What it already does | What we take |
|---|---|---|
| **c2rust** (Immunant) | Mechanical C→unsafe-Rust transpilation, preserves semantics | The Stage-1 "literal unsafe Rust" emitter — evaluate before writing our own |
| **Coccinelle / SmPL** | Semantic pattern matching over kernel C, used by kernel devs daily | Pattern instance *counting/mining*; possibly the match engine itself |
| **Rust-for-Linux** | In-tree Rust support: kbuild integration, `kernel` crate safe abstractions (locks, refcounts, lists…) | The integration path (mixed C/Rust kernel that boots at every step) and the *target vocabulary* for safe rewrites |
| **c2rust dynamic analysis / Laertes / concrat** (academic) | Lifting raw pointers to references, pthread→Rust lock inference | Techniques for the Stage-3 safety lift |
| **DARPA TRACTOR** (2024–) | Funded C→safe-Rust research; [Lincoln Lab benchmarks](https://www.ll.mit.edu/r-d/projects/translating-all-c-rust-tractor-benchmarks) every 6 months | Benchmarks + published failure modes; read the evaluation reports in Phase 0 |
| **2025–26 LLM translators** — [SACTOR](https://arxiv.org/pdf/2503.12511), [C2SaferRust](https://arxiv.org/html/2501.14257v1), [EvoC2Rust](https://www.arxiv.org/pdf/2508.04295), [SmartC2Rust](https://www.arxiv.org/pdf/2409.10506v2), [Syzygy](https://syzygy-project.github.io/assets/paper.pdf), [ORBIT](https://arxiv.org/pdf/2604.12048) | LLM + static analysis + feedback-loop translation of **userspace** projects; none target the kernel | Verification-loop designs; known weak spots (macros, concurrency) are where our approach must differentiate |
| **[Rules+Semantics](https://arxiv.org/pdf/2508.06926)** (closest to our rule-DB idea) | Static curated rule taxonomy + retrieved demonstration examples guiding an LLM | Rule-taxonomy starting point — but it does not learn/grow/validate rules against all occurrences; that loop remains our novel piece |
| **Immunant kernel-module transpile ([2020 blog](https://immunant.com/blog/2020/06/kernel_modules/), [c2rust#150](https://github.com/immunant/c2rust/issues/150))** | The only published c2rust-on-kernel-code attempt; single module | Concrete list of kernel-specific transpiler pain: function-like macros, GCC extensions, libc type mapping |

Canonical literature map: [Papers on C-to-Rust Translation](https://hjaem.info/c-to-rust-papers)
(curated by Jaemin Hong — ~40 papers as of 2026-07). Kernel-related entries are
*empirical studies only*: [Rust-for-Linux experience study](https://www.usenix.org/conference/atc24/presentation/li-hongyu)
(USENIX ATC 2024) and [security impact of Rust in the kernel](https://doi.org/10.1109/ACSAC63791.2024.00054)
(ACSAC 2024 — of 240 device-driver vulnerabilities, 82 auto-eliminated by Rust,
113 need specific idioms + developer involvement, 45 unaffected; useful
motivating data AND a caution that safety is not automatic). Macro-aware
transpilation exists only as a [WIP paper](https://doi.org/10.1145/3735452.3735535)
(LCTES 2025).

Prior-art check (2026-07-16): the components exist, the idea does not. Nobody
has published (a) a pattern census of the kernel corpus, (b) a self-growing
rule DB validated against all matching occurrences, or (c) any translation
attempt at kernel scale. Rust-for-Linux is permanent as of Dec 2025 but is
hand-written new code only (~25k lines Rust vs ~34M C), no translation by
policy.

**The genuinely novel piece here** is the closed learning loop:
normalise → fingerprint → match rule DB → emit → validate → **generalise the
fix into a rule** → auto-apply to all matching occurrences. Everything else
exists in some form.

## Architecture

```
kernel source (pinned tag + pinned .config, LLVM build)
        │  compile_commands.json
        ▼
clang AST + macro-expansion trace          (keep BOTH macro-level and expanded form)
        ▼
normalisation (names, typedef aliases, commutative reorder) → semantic fingerprint
        ▼
pattern DB match (SQLite)
   ├─ hit  → apply rule → emit Rust ──────────────┐
   └─ miss → cluster with similar misses          │
             → agent proposes a GENERAL rule      │
             → rule validated against ALL         │
               matching occurrences               │
             → human gate (semantics-bearing      │
               categories only) → commit rule ────┘
        ▼
validation oracle (layered, see below)
        ▼
record instance: pattern, rule version, validation evidence, confidence
```

### Pattern DB principles

- **Hierarchy**: broad families (`LOCKED_REGION`, `REFERENCE_ACQUIRE`,
  `MEMORY_ACCESS`) with exact-API leaves. New constructs inherit from the
  parent, specialise at the leaf.
- **Hard rule: semantics-bearing primitives never match on structural family
  alone.** `atomic_inc` / `refcount_inc` / `kref_get` are structurally one
  family but semantically distinct (saturation, overflow behaviour, release
  callbacks). Same for `spin_lock` vs `_irqsave` variants, and anything RCU
  or memory-ordering related. These match on exact API + context (IRQ state,
  RCU read section, preemption), or they don't match.
- Macro names are semantic labels — retain macro ancestry alongside the
  expanded AST so `list_for_each_entry`, wrappers around it, and hand-expanded
  equivalents map to one pattern.
- Confidence is **earned**, not estimated: validated-instance count + oracle
  tier reached, never a model-emitted percentage.

### Validation oracle (the weakest link in the original concept — defined here)

Layered, cheapest first; each pattern instance records the highest tier passed:

1. **Compiles** in-tree (`LLVM=1`, Rust enabled) with the C original removed
   for that symbol.
2. **ABI/symbol diff**: exported symbols, section placement, `#[repr(C)]`
   layout checks (`static_assert` equivalents on struct size/offsets).
3. **KUnit differential tests**: same inputs to C build vs Rust build
   (feasible for `lib/` pure functions; generate harnesses).
4. **Boot + kselftest in QEMU** for the pinned config.
5. **Human review** — mandatory for concurrency / memory-ordering / RCU
   rules regardless of tiers 1–4, because LKMM ≠ C11 ≠ Rust's memory model
   and no automated tier catches a wrong ordering mapping.

### Unsafe policy (three classes, from the concept discussion — adopted as-is)

1. **Intrinsically unsafe**: MMIO, context switch, page tables, FFI, linker
   symbols. Stays unsafe forever; small, audited.
2. **Temporarily unsafe**: mechanically translated pointer/locking code
   awaiting a safe-wrapper rule. Tracked as a metric that must go down.
3. **Safe logic**: everything the type system can carry.

Metrics from day one: unsafe fns / lines / call-graph reachability, split by
class 1 vs 2 vs unclassified.

## Phases

### Phase 0 — environment (exit: reproducible corpus + prior-art verdicts)

- Pin kernel tag (current stable at start date) and one small config —
  x86_64 `defconfig` or ARM64 `virt`; generate `compile_commands.json` with
  `LLVM=1`. The corpus is **per-config**; patterns differ under `#ifdef`.
  One config until Phase 4.
- Verify Rust-for-Linux builds and boots this config in QEMU (this is the
  substrate everything lands on).
- Hands-on evaluation, one afternoon each: c2rust on one kernel `lib/` file;
  Coccinelle counting instances of 5 idioms kernel-wide. Written verdict:
  reuse or rebuild, per tool.

### Phase 1 — pattern census (exit: thesis tested, go/no-go)

Read-only; **no Rust emitted**. Ingest ASTs for the pinned config, normalise,
fingerprint every function, cluster.

Deliverable: *the kernel by pattern* — how many idiom families cover 50% /
80% / 95% of functions; size and shape of the unmatched tail; per-subsystem
breakdown. **Go/no-go gate**: if ordinary code doesn't collapse into
hundreds-not-tens-of-thousands of families, the thesis fails and we stop
having spent no translation effort. The census is independently publishable/
useful even then.

### Phase 2 — the minimal riscv64 boot path (exit: Rust translation running in-tree)

(Re-scoped 2026-07-16 per Dan: go straight at the shipping target's code
path instead of an x86 `lib/` detour.)

1. **Trim the corpus first.** Start from `ARCH=riscv` tinyconfig (rv64) and
   cut everything not needed to prove life: no serial/TTY (aliveness =
   heartbeat via GPIO/CSR poke from a kernel timer, not a console), no
   block/net/USB/filesystems beyond the unavoidable VFS core, no modules,
   no SMP, printk off or deferred. Measure the resulting corpus (TUs,
   lines, statement families vs the lab corpus) — this is the *actual*
   first-pass translation set. Deferred-and-added-later list documented.
2. **Translate on that path, unsafe-first** (c2rust baseline + our rules),
   file-by-file in-tree replacement, oracle tiers 1–4 (boot = heartbeat
   observed under QEMU `-M virt` first; FPGA later).
3. **Safe-version attempt using the kernel crate's rules**: for each
   translated file, attempt a second version lifted onto Rust-for-Linux
   `kernel` crate abstractions (locks→`Guard`, refcounts, `pr_*`) — the
   Stage-3 safety lift run early on a small corpus, to find out what the
   safe-wrapper rules need *before* Phase 3 scales them.
4. Every manual fix must land as a **rule**, not a file patch — this phase
   forces the DB schema to be real.

The old Phase-2 `lib/` targets (sort/CRC, KUnit differential) remain the
oracle-tier-3 test vehicle — pure functions are still where differential
testing is cheapest — but the headline target is the rv64 boot path.

### Phase 2.5 — the pure-leaf optimisation lane (Dan, 2026-07-16)

For **pure leaf functions only** — no I/O, no MMIO, no shared-memory access,
no locks, no allocation; arguments in, value out; exactly the `lib/math`
class — a third variant is permitted alongside the faithful translation:

1. **faithful** (the default lane; differential baseline, always exists)
2. **safe-lifted** (Stage-3 representation change, no algorithm change)
3. **optimised** (pure leaves ONLY): idiomatic/fast Rust, algorithm may
   differ, produced by a *dedicated subagent* per function

The optimised lane does not weaken the discipline because purity makes
exhaustive validation cheap: the C original and both Rust versions can be
compiled host-side and property-tested against each other over millions of
random + boundary inputs (proptest/quickcheck style), plus the in-kernel
KUnit vectors. Kbuild keeps variants switchable (faithful default;
optimised opt-in per symbol after its differential record is clean).

Purity detection is mechanisable from census data we already extract:
no asm/volatile/statics-writes, no pointer-typed parameters (or only
`const` reads), and a callee set that is itself pure — a fixpoint over the
call graph. That census cut is the work queue for the optimisation
subagent.

### Phase 3 — the learning loop (exit: agent-invented rules validated at scale)

Wire the miss path: cluster unmatched subtrees → agent proposes general rule
→ auto-validate against all occurrences → human gate for semantics-bearing
categories → commit. Measure the curve that justifies the project:
**auto-coverage % vs rules count** over a growing target set (more of `lib/`,
then a simple char driver).

### Phase 4 — scale + safety lift (exit criteria set from Phase 3 data)

- Widen targets (a driver family; Rust-for-Linux-supported subsystems first,
  since safe abstractions already exist there).
- Stage-3 rewrites: replace class-2 unsafe instances with `kernel`-crate safe
  wrappers, driven by the same pattern DB (`LOCKED_REGION` instance →
  `Guard`-based rewrite).
- Second config / second arch to test pattern portability — the real test of
  "the DB is the product". The second arch is **RISC-V rv32** (minimal
  config for the Cynthion soft-core end goal); pattern portability across
  arch here is directly on the critical path, not a side quest.

## Risk register

| Risk | Mitigation |
|---|---|
| Pattern-collapse thesis false | Phase 1 gate before any translator work |
| Identical AST, different semantics (concurrency/lifetime/ABI) | Context-keyed matching + mandatory human gate for those categories |
| Validation oracle too weak for concurrency | Accept: those rules are human-reviewed; oracle tiers are recorded per instance, never overstated |
| Kernel macro/GCC-extension hostility to AST tooling | Clang-based everything (`LLVM=1` builds are supported); keep macro-level + expanded views |
| Config explosion | One pinned config until Phase 4 |
| Scope creep toward "boot a full Rust kernel" | Milestones are per-file in-tree replacements in a mixed kernel that boots at every step; whole-kernel is explicitly a non-goal |
| c2rust output quality (known: very ugly, u-ints for bools, goto emulation) | It's Stage-1 scaffolding only; rules operate on our AST, emission strategy swappable |

## Repo layout (intended)

```
PLAN.md            this file
docs/              design notes, phase reports, pattern census
scripts/           Python tooling (per agent rules: no .sh; logs → ./tmp/<name>.log)
mining/            Rust workspace: AST ingest, normalise, fingerprint, cluster
rulesdb/           schema + the pattern DB (SQLite) + rule definitions
emit/              Rust emitter / c2rust post-processing
oracle/            validation harnesses (KUnit generators, ABI diff, QEMU boot)
tmp/               scratch + logs (untracked)
```
