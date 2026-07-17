# Phase 0 — tool evaluations & first corpus data

2026-07-16. Corpus: v7.1, x86_64 defconfig+RUST, 2,996 C TUs, 2.79M lines
(see [phase0-environment.md](phase0-environment.md)).

## Idiom density sanity check (scripts/idiom_census.py)

Textual marker counts over exactly the configured TUs (not the whole tree).
Regenerate: `python3 scripts/idiom_census.py` → `tmp/idiom_census.{log,json}`.

| Idiom family | Occurrences | Files touched (of 2996) |
|---|---:|---:|
| goto err/out/fail/unlock/free/cleanup | 17,106 | 1,482 |
| EXPORT_SYMBOL* | 12,563 | 1,316 |
| ERR_PTR / IS_ERR / PTR_ERR | 8,593 | 949 |
| spin_lock family | 4,450 | 689 |
| READ_ONCE / WRITE_ONCE | 4,353 | 500 |
| atomic ops | 3,238 | 481 |
| list_for_each_entry* | 2,795 | 595 |
| mutex_lock* | 2,445 | 545 |
| list_add/del | 2,325 | 524 |
| container_of | 2,058 | 706 |
| rcu_read_lock | 1,654 | 415 |
| ioread/iowrite/readl/writel | 1,552 | 93 |
| rcu_dereference* | 1,391 | 266 |
| refcount ops | 812 | 194 |
| module_init/exit/…_driver | 419 | 250 |
| kref get/put/init | 267 | 81 |
| wait_event* | 200 | 103 |
| **Total** | **~66,200** | |

Reading: one idiom-marker hit per ~42 lines of corpus, from only 17 coarse
families. Half the corpus's files contain the error-goto idiom. Strongly
supports the pattern-collapse thesis; the real (AST-level, normalised)
census is Phase 1.

## Coccinelle — verdict: REUSE

`rulesdb/cocci/locked_region.cocci` (spin_lock/spin_unlock pair on the same
lock expression, `...` between) over `drivers/char`:

- **20 locked regions, 0.27s wall** (`spatch --very-quiet -j 8`, report mode
  with file:line spans via python script rules).
- Structural matching, macro-aware, battle-tested on kernel C. This is the
  pattern-instance *finder* for Phases 1–2; our own fingerprint engine only
  needs to cover what SmPL can't express (semantic context keys: IRQ state,
  RCU section, type layout).
- Quirk found immediately: one match reported span `1679-1672` (end before
  start) — `...` pairing across branches produces crossed pairs. Instance
  extraction must canonicalise/deduplicate pairs; don't trust raw p1/p2
  ordering.

## c2rust — verdict: WORKS on kernel code (with friction); usable as Stage-1 reference

> **Superseded 2026-07-18:** the "not a foundation" call below was the
> right read of the vanilla upstream tool on 2026-07-16. Since then the
> [awtoau/c2rust fork](https://github.com/awtoau/c2rust) has landed
> kernel-idiom rewrite rules, real bug fixes (dangling-decl/label panics,
> `_THIS_IP_`, `offsetof(typeof(...))`, GNU asm-goto, and more), and
> eliminated every known crash across its 552-file baseline corpus — see
> `rulesdb/README.md`'s "c2rust fork integration" section and the
> `c2rust_attempts`/`c2rust_rule_conformance` tables in
> [patterns-db.md](patterns-db.md). The fork is now staged to become a
> **primary translation source**, not just a differential-baseline
> reference emitter, gated by continued pipeline+rule-conformance work.
> The AST-decoupling concern below is still valid for the *rule DB*
> (rules key on our own fingerprints, not c2rust's AST) — what changed is
> how much of the raw transpile output is directly usable as a starting
> point.

Install saga (all reproducible facts, 2026-07-16):
- `cargo install c2rust` (0.22.1, crates.io): **FAILS against LLVM 22**
  (`TagDecl::getTypeForDecl` deleted upstream).
- git master (`1635523`): needs **nightly** rustc (`#![feature]` in
  c2rust-transpile). `cargo +nightly install --git …` → builds clean against
  LLVM 22. It also self-installs a pinned `nightly-2023-04-15` toolchain for
  its rustfmt step on first run.

Transpile eval on `lib/sort.c` with the real kernel flags:
- Feed it a **directory** containing `compile_commands.json` (a bare JSON
  path is silently treated as no-database and it runs flag-less → header
  errors). Filtered single-TU database: see `tmp/cc-sort/`.
- **Output: complete, plausible unsafe Rust** (`tmp/c2rust-sort/src/sort.rs`,
  17KB): all four exported functions (`sort`, `sort_r`, `sort_nonatomic`,
  `sort_r_nonatomic`), `#[repr(C)]` structs, fn-pointer typedefs, and the
  `static_call` machinery behind `cond_resched()` faithfully reproduced.
- **4 warnings**: "Missing top-level node" / invalid exported AST — some
  top-level decls dropped (suspect GCC-extension/attribute constructs or
  LLVM-22 exporter gaps). MUST be investigated before trusting output:
  a transpiler that silently drops decls fails our oracle tier 1 by
  construction.
- Output targets old nightly features (`extern_types`, `raw_ref_op`,
  `strict_provenance` — the latter since stabilized/renamed), so it does not
  compile on current stable as-is.

Verdict for the plan: c2rust is a working *reference emitter* and idiom
corpus, not a foundation — the dropped-decl warnings and moving-target
toolchain coupling confirm the PLAN.md stance that our rules must not be
coupled to its AST. Use it in Phase 2 as a differential baseline for our
own emitter.
