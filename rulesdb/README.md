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
manual fix lands as a rule, not a file patch.
