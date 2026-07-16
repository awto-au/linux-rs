# Correctness review — 2026-07-16 (census v1.0 → v1.1)

An independent review agent audited all census scripts against the run
artifacts; findings below were verified quantitatively, then fixed in
`region_census.py` v1.1 / `compose_census.py` v2.1 and the corpus re-run.

## Material findings (all fixed or reinterpreted)

1. **Macro-expansion internals dominated v1.0's head.** Statements inside
   GNU statement-expressions and `do{}while(0)` expansions were counted as
   independent instances — 27% of all instances; 90 of the top-200 families
   (54% of the head's mass) were macro internals sharing exemplar lines.
   *Fix:* never descend into expressions; nested statements at the same
   (file,line,col) as their parent are macro-internal and skipped; macro
   shells become one `MACROSTMT:<name>` instance (the invocation is the
   unit, labelled by the macro's own name).
2. **Type erasure.** `struct udphdr *uh;` and `bool x;` shared a family.
   *Fix:* `VAR_DECL:<type>` tokens.
3. **Unbraced bodies.** 59% of `if` statements have unbraced bodies whose
   statement was never counted (~143k instances missed), and brace style
   split families (understating collapse). *Fix:* control bodies always
   pruned to BODY in the parent and always emitted as statements.
4. **v0/v1 normalisation mismatch** (binary-operator opcodes in v1 only).
   *Fix:* opcodes (incl. compound assignment) in both; v0's 8.4% was an
   upper bound on duplication — direction of its conclusion unaffected.
5. **"Median glue 2" included the root node, which is glue by construction
   for a singleton.** *Reinterpretation:* the substantive stat is median
   **1 non-root glue node**; node-level coverage (~91%) is the robust
   number. v2.1 reports non-root glue explicitly.

Minor: duplicate compile_commands entries (2 files) deduped; compose pickle
now written; iterative postorder (no RecursionError risk); callee-cap and
observability notes recorded in the review transcript.

## Headline corrections

| Metric | v1.0 (wrong) | v1.1 (corrected) |
|---|---:|---:|
| statement instances | 1,440,040 | **1,056,134** |
| families | 287,736 | **245,118** |
| collapse | 80.0% | **76.8%** |
| families for 25% | 15 | **26** |
| families for 50% | 199 | **713** |
| families for 80% | 35,403 | **52,013** |

**Gate decision unchanged: GO.** Half of all kernel statements still fall
into ~700 families — hundreds, not tens of thousands — and now the top
families are real source-level statements, not macro shells.

Checked and found correct: coverage-curve math (199 reproduced
independently pre-fix), no double-counting from nested compounds, header
static-inline exclusion, subtree Merkle hashing, hash-collision risk
(negligible everywhere).
