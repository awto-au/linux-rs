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
well past that first batch since (27 files as of TU 30, `0001`–`0027`;
`ls rulesdb/rules/*.toml` for the current count) — later rules cover
safe-lift lock guards/refcounts/aref ownership, arch-override dead-code
elimination, and `EXPORT_SYMBOL`-family variants, not just the original
gcd extraction.

## c2rust fork integration

Rules also originate from the [awtoau/c2rust fork](https://github.com/awtoau/c2rust)
(`/mnt/2tb/git/github.com/awtoau/c2rust`), which is staged to become a
**primary translation source**, not just a Stage-1 reference emitter —
see `docs/phase0-evals.md` for the original (now superseded) "reference
only" evaluation and its supersession note. The fork adds opt-in
kernel-idiom rewrite rules (`--enable-rule`) and has eliminated every
known crash across its full 552-file baseline corpus as of 2026-07-18.
Conformance between c2rust's output and this rule DB is tracked in
`rulesdb/schema.sql`'s `c2rust_attempts`, `c2rust_decl_outcomes`,
`c2rust_rule_conformance` and related tables — see
[docs/patterns-db.md](../docs/patterns-db.md) for the query-level view.
