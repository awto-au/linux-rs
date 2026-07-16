# Phase 1 census — v1 results (statement/region granularity) — **GATE: GO**

> **CORRECTED 2026-07-16 (v1.1):** an independent correctness review found
> the v1.0 numbers below inflated by macro-expansion internals and biased
> by brace style and type erasure. Corrected numbers: **1,056,134
> instances / 245,118 families; 26 families cover 25%, 713 cover 50%**
> (collapse 76.8%). The gate decision is unchanged — see
> [review-findings-2026-07-16.md](review-findings-2026-07-16.md). The
> v1.0 text below is retained for the record; read its numbers as
> superseded.

2026-07-16. Pipeline: `scripts/region_census.py` (statement fingerprints with
compound bodies pruned to a BODY token; sibling bigrams/trigrams) →
`scripts/region_report.py`. Full corpus, 0 TU failures, ~3.5 min.

## The numbers

| Unit | Instances | Families | Collapse |
|---|---:|---:|---:|
| statements | 1,440,040 | 287,736 | **80.0%** |
| bigrams (sibling pairs) | 1,032,150 | 488,079 | 52.7% |
| trigrams | 699,303 | 466,690 | 33.3% |

Coverage curve — statements:

| corpus fraction | families needed |
|---|---:|
| 25% | **15** |
| 50% | **199** |
| 80% | 35,403 |
| 95% | 215,734 |

Singleton statement families: 15.0% of instances.

## Gate decision: **GO**

The thesis required ordinary kernel code to collapse into
hundreds-not-tens-of-thousands of families. At statement granularity it
does: **199 families account for half of all 1.44M statement instances**;
15 families cover a quarter. Translate the top few hundred statement
families as validated rules and half the corpus's statements are covered
mechanically; combined with v0's vocabulary result (top-2,000 external APIs
cover the full call surface of 51% of functions), the "middle 40% mostly
automated" shape of the effort curve is real.

The long tail is equally real: 80%→95% needs ~216k families — that's the
"last 10% expensive" band, as predicted, and it is where agent rule
invention (Phase 3) earns its keep.

## Caveats (know what the number means)

- **Post-macro-expansion AST.** Heavy macros (BUILD_BUG_ON, WARN_ON_ONCE,
  module-param helpers) expand to identical shapes, so single source lines
  exemplify huge families, and top-family exemplar snippets can look odd.
  This *overstates* concentration for macro statements and *understates* it
  for hand-written variants a macro-aware normaliser would merge. Both
  effects shrink with the mining engine's dual macro/expanded view (PLAN
  architecture); direction of the gate is unaffected.
- Near-duplicate families exist (same snippet, different fingerprint —
  e.g. three `pdev = to_pci_dev(dev);` families) from type/category token
  differences: normalisation has headroom; real family counts are LOWER.
- Raw n-grams are the wrong composite unit (they cut across region
  boundaries); paired-call span extraction (lock…unlock etc., à la the
  Coccinelle rule) is the v2 composite unit. Bigram 25%-coverage at 221
  families already hints composite regions concentrate too.

## Next (Phase 2 entry)

Statement-family concentration + API-vocabulary concentration justify
building the rule DB schema and the first emitter loop on a `lib/` target,
per PLAN Phase 2 — with rules keyed on (statement family × API mapping).
