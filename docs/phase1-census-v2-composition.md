# Phase 1 v2 — compositionality of the tail (hypothesis confirmed)

> **CORRECTED 2026-07-16 (v2.1):** re-run on the corrected v1.1 census with
> the root node (glue by construction) reported separately, per
> [review-findings-2026-07-16.md](review-findings-2026-07-16.md).
> Corrected result: 174,985 singleton statements; **median non-root glue =
> 1** at T=5/10/50; ≤3 non-root glue for 88.8% (T=10); node coverage 88.8%.
> The conclusion below stands with these numbers; the v2.0 figures
> (median 2 including root, 91.2% coverage) are superseded.

2026-07-16. `scripts/compose_census.py`: two corpus passes — (1) count all
23.7M statement-internal subtree fingerprints (668,876 distinct), (2) for
each of the 216,659 singleton statement families from v1, classify every
node as *covered* (its subtree is a family with ≥T instances corpus-wide)
or *glue* (novel).

## Result

| T (family min size) | median glue / stmt | p90 | glue ≤5 | glue ≤10 | node coverage |
|---|---|---|---|---|---|
| 5 | **2** | 5 | 91.6% | 98.6% | 92.2% |
| 10 | **2** | 6 | 89.4% | 97.8% | 91.2% |
| 50 | **2** | 7 | 84.2% | 95.9% | 88.9% |

A "unique" tail statement is, at median, a novel arrangement of exactly
**2** nodes over already-common subtrees. The v1 coverage curve's scary
80→95% band (~216k families) therefore does NOT price the tail correctly:
once head families + sub-statement expression families are ruled, tail
statements are compositions, not inventions.

## Consequence for the effort model

- v1 said: 199 families cover 50% of statements (head is tiny).
- v2 says: the tail decomposes into those same common parts + ~2 glue nodes
  each — i.e. **rule composition** (bottom-up emitters per subtree family,
  as any compiler does) covers most of the tail mechanically.
- What stays genuinely expensive: statements whose *semantics* (not shape)
  are novel — concurrency/ordering context, inline asm, ABI tricks. Shape
  compositionality can't see those; the pattern DB's semantic context keys
  (PLAN) remain load-bearing.

This also retroactively explains v0: whole functions don't repeat because
composition explodes combinatorially, while the vocabulary being composed
stays small. The translator must be compositional, with *families as the
unit of rules* at every level (API → expression → statement → region).
