# Rule DB

Rules live as one TOML file each under `rules/` (rule-as-code: diffable,
reviewable; an SQLite index is generated when scale demands it). SmPL
matchers for finding instances live in `cocci/`.

## Rule format

```toml
id = "kebab-slug"            # stable identity
version = 1
tier = 1                     # 1 low-risk / 2 bounded-unsafe / 3 context-dependent (PLAN)
category = "api|macro|expr|stmt|region"

[match]                      # what this rule fires on
c = "..."                    # human-readable C form
family = "..."               # census fingerprint pattern or MACROSTMT:<name>, when known
constraints = ["..."]        # semantic context that MUST hold (tier 3: exact API + context)

[emit]
kind = "vanishes|function|iterator|cfg|rust-macro|rewrite"
rust = "..."                 # target form
deviations = ["..."]         # any semantic deltas vs C, each with justification

[provenance]                 # evidence, never copied code (docs/reference-projects.md)
derivation = "independently derived|structurally inferred|API-inspired|adapted"
evidence = ["..."]

[validation]
instances = ["file:symbol"]  # occurrences this rule has been validated against
oracle = "tier1|tier2|tier3|tier4"   # highest tier passed (PLAN oracle)
negative = ["..."]           # examples that must NOT match
human_review = false         # true required for tier 3 before scale-out
```

Rules 0001–0005 were extracted from the first translated TU
(`lib/math/gcd.c` → `patches/0001-*`), per PLAN Phase 2 rule 4: every
manual fix lands as a rule, not a file patch. The rule set has grown
well past that first batch since (30 rules as of TU 38, `0001`–`0030`;
`ls rulesdb/rules/*.toml` for the current count) — later rules cover
safe-lift lock guards/refcounts/aref ownership, arch-override dead-code
elimination, `EXPORT_SYMBOL`-family variants, the KUnit boot-oracle
gate, SPDX provenance, and the still-`[status]`-deferred
`_THIS_IP_`/`_RET_IP_` instruction-pointer rule (0030 — design resolved
2026-07-18, awto-au/linux-rs#27, not yet applied to a real translation).

A `[status]` block on a rule marks it `deferred = 1` in the generated
DB — authored but not yet applicable to a real translation (a genuine
open question was found, not just an unfinished write-up). Check
`rules.deferred` before applying a rule mechanically; a deferred rule's
`[emit]` section may describe a *chosen direction*, not a validated one.

## c2rust fork integration

Rules also originate from the [awtoau/c2rust fork](https://github.com/awtoau/c2rust)
(`/mnt/2tb/git/github.com/awtoau/c2rust`), a **permanent fork** (never
merged upstream — see the fork's own `CLAUDE.md`), staged as a primary
translation source, not just a Stage-1 reference emitter — see
`docs/phase0-evals.md` for the original (now superseded) "reference
only" evaluation and its supersession note. The fork adds opt-in
kernel-idiom rewrite rules (`--enable-rule=all`) gated behind a flag so
stock `c2rust transpile` stays byte-for-byte identical to upstream
behavior. As of 2026-07-18 the fork's full 542-file baseline corpus
transpiles **entirely clean, zero dropped_decls** (down from 330 at the
start of that session's triage), and rule-conformance against this
project's own idiom rules stands at 9231 conformant / 1612 violation
rows (`c2rust_rule_conformance`, most recent run).

Conformance between c2rust's output and this rule DB is tracked across
several `rulesdb/schema.sql` tables — check the current schema for the
full set (`c2rust_attempts`/`c2rust_decl_outcomes` for transpile-stage
outcomes, `c2rust_compile_outcomes`/`c2rust_clippy_outcomes` for
rustc/clippy-stage checks, `c2rust_rule_conformance` for idiom
conformance, `c2rust_fix_patterns` for a hand-curated record of
already-fixed c2rust bug root causes, `file_oracle_status` for PLAN's
5-tier validation oracle per file) — see
[docs/patterns-db.md](../docs/patterns-db.md) for the query-level view.
