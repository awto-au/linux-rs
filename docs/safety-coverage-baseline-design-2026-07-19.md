# Per-function safety-tier coverage tracking — schema design

Status: schema designed and added to `rulesdb/schema.sql`. No scanner
implemented. No baseline run.

## Table

`function_safety_status` — new table, keyed `(c_file, c_func_name,
population)`. Peer to `file_oracle_status`, not nested under it: that
table tracks BEHAVIORAL correctness (oracle tiers 1-5, file-grained);
this table tracks MECHANICAL unsafe/safe pipeline state, function-grained.
A function can be oracle-tier-5-verified and 100% unsafe-baseline at the
same time — orthogonal axes.

Not keyed by `target_id` (`translation_targets`, the arch/endian axis from
`docs/multi-arch-safety-tier-tracking-plan-2026-07-19.md`): safe-lift
rules 0023/0024/0025 rewrite lock/refcount/ownership patterns that don't
vary by `-march`/`-mabi`/endianness. Add `target_id` only if a future
safe-lift rule turns out arch-conditional.

Columns:

| column | purpose |
|---|---|
| `c_file`, `c_func_name`, `population` | identity, `population` reuses `file_oracle_status`'s `landed_tu`/`c2rust_corpus` vocabulary |
| `rs_func_name`, `rs_file` | emitted Rust identity, when scanned |
| `state` | the 5-state pipeline field (below) |
| `unsafe_token_count`, `raw_pointer_count` | raw counts backing the state-2 mechanical scan, kept for audit after progression |
| `conversion_rule_id` | FK `rules(id)`, which safe-lift rule (0023/0024/0025-style) was applied, state ≥ 3 |
| `oracle_tier` | 1-5, reusing `file_oracle_status`'s tier vocabulary verbatim — the tier the SAFE conversion passed, not a join key (function-grained vs file-grained) |
| `accepted_exception_rule_id` | FK `rules(id)`, NULL = strict-safe; NOT NULL = safe-with-exceptions, citing the licensing rule (0018-style) |
| `loc` | Rust fn body line count at scan time — the LOC% weighting basis |
| `detail`, `evidence_ref`, `checked_at` | same vocabulary/shape as `file_oracle_status` |

## State machine (5 states)

1. `unsafe-baseline` — default for anything translated at all.
2. `mechanically-checked-already-safe` — scanner found zero `unsafe`
   tokens and zero raw-pointer types (`*const T`/`*mut T`) in the fn
   signature+body. Terminal for functions that need no lift; does not
   pass through 3/4.
3. `attempted-safe-conversion` — a real rewrite onto kernel-crate safe
   wrapper types was tried (locks→`Guard`, refcount→`Refcount`,
   ownership→`ARef`/`Arc`, per rules 0023/0024/0025).
4. `safe-verified` — the state-3 attempt passed a real
   `file_oracle_status`-tier check (`oracle_tier` records which).
   Strict-safe: `accepted_exception_rule_id IS NULL`.
5. `safe-with-exceptions` — same verification bar as state 4, but only
   passes given this project's already-accepted FFI/ABI-boundary
   exceptions (`accepted_exception_rule_id` cites the specific rule).
   Not a step past `safe-verified` — a function is EITHER state 4 (no
   exception invoked) OR state 5 (exception invoked), never both, because
   the exception is precisely what stops it from reaching strict state 4.

## Safe vs. safe-with-exceptions — the real distinction

Grounded in `rulesdb/rules/0018-c-abi-allocator-contract.toml`: a
`kmalloc`/`kfree` pair that crosses the C ABI (some other C or future
Rust caller frees what this function allocates) MUST stay a raw-pointer
FFI shim forever — 0018 explicitly forbids lifting it to
`kernel::alloc::{KVec,KBox}` because their `Drop` calls a different
deallocator; that would be a correctness bug, not merely an unfinished
lift. This is a *permanent, accepted* unsafe/FFI boundary, not a
temporarily-unsafe gap awaiting a rule (PLAN.md class 2). Rules
0023/0024/0025 (safe-lift-lock-guard/refcount/aref-ownership) are the
converse: rules whose entire purpose is closing class-2 gaps.

- **Strict-safe** (state 4, `accepted_exception_rule_id IS NULL`): no
  accepted exception was invoked; every unsafe/raw-pointer surface the
  original translation had has been eliminated.
- **Safe-with-exceptions** (state 5, `accepted_exception_rule_id` set):
  verified safe *given* one or more of this project's own already-
  documented accepted exceptions (0018 and any future rule of the same
  shape — an intrinsically-unsafe FFI/ABI boundary the project has
  affirmatively decided must stay a raw shim). The coverage report must
  show both numbers so "safe" isn't inflated by exceptions the project
  itself decided are permanent, nor deflated by counting permanently-
  accepted boundaries as failures.

`rules` table already exists; `accepted_exception_rule_id`/
`conversion_rule_id` are plain FKs into it, no new rule-classification
column needed — 0018-shaped rules are already identifiable by content
(their `[emit].deviations` says "none — this is the C allocator by
construction" and their negative-case documents exactly which crossings
qualify).

## Granularity: 4 levels, one storage grain

- **Per-function**: `function_safety_status` rows directly.
- **Per-file**: `function_safety_file_summary` view, `GROUP BY c_file,
  population`.
- **Per-subsection**: `function_safety_subsection_summary` view, `GROUP
  BY` a derived `subsection` = top-level path component of `c_file`
  (`lib/`, `drivers/`, `fs/`, `kernel/`, `net/`, `mm/`, `arch/riscv/` one
  level deeper since bare `arch/` spans every unrelated arch).
- **Whole-corpus**: `function_safety_overall_summary` view, no `GROUP
  BY`.

No stored subsection column — computed in the view via `SUBSTR`/`INSTR`
so it can never drift out of sync with `c_file`.

### Subsection proxy: directory, not Kconfig menu — rejected alternative

Kconfig `menu`/`endmenu` blocks (e.g. `linux-riscv/lib/Kconfig`'s
`menu "Library routines"` … `endmenu`) are a real structural concept in
the kernel source. Rejected as the subsection proxy:

- No existing project tooling parses Kconfig beyond grep (confirmed in
  `docs/multi-arch-safety-tier-tracking-plan-2026-07-19.md` §2 — every
  `compile_commands.json`/build script hardcodes `ARCH=riscv`, none
  touches Kconfig menu structure).
- Kconfig menus don't enumerate to a per-C-file mapping without
  resolving `source` includes recursively and matching `obj-y`/`obj-m`
  lines back to source paths per Makefile — a real parser, new
  infrastructure, fragile against kernel-tree drift on every rebase.
  Confirmed absent anywhere in this project today.
- Directory prefix is already the implicit grouping this project's own
  docs use informally ("lib/ vs drivers/ vs fs/", multi-arch plan §2) and
  is a zero-cost derivation from a column (`c_file`) every table here
  already stores.

Directory-prefix wins on cost and precedent; Kconfig menu parsing is
real but unbuilt infrastructure this design does not require.

## LOC weighting

`function_safety_status.loc` (Rust fn body line count, recorded per row
at scan time) is the weight for every `_loc_pct` column in the three
rollup views. Precedent: `progress_snapshots.corpus_total_loc` /
`landed_loc` already establish LOC-weighted (not count-weighted)
progress as this project's convention — extended here to per-state
granularity so a correctly-safe 5-line function and a correctly-safe
500-line function do not contribute equally to a coverage percentage.

Two percentages are computed at every granularity, matching the strict-
vs-exceptions distinction:

- `strict_safe_loc_pct` = `(already-safe + safe-verified) LOC / total LOC`
- `exceptions_allowed_loc_pct` = `(already-safe + safe-verified +
  safe-with-exceptions) LOC / total LOC`

## Scanner inputs required (next phase, not built here)

- `unsafe` token scan and raw-pointer-type scan over a function's Rust
  body (state 1→2 mechanical check).
- Function-body boundary extraction from the emitted `.rs` (to compute
  `loc` and scope the token/pointer scan to one function, not the whole
  file).
- Cross-reference against `conversion_rule_id`/`accepted_exception_rule_id`
  candidates: which rule(s) in `rulesdb/rules/002[3-5]-safe-lift-*.toml`
  or `rulesdb/rules/0018-c-abi-allocator-contract.toml`-shaped rules
  apply to a given function, likely via the existing rule `[match]`/
  `[validation].instances` text plus manual/LLM classification — exact
  mechanism deferred to the scanning-tool design.
