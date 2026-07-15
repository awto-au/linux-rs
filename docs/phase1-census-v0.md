# Phase 1 census — v0 results (whole-function granularity)

2026-07-16. Pipeline: `scripts/fingerprint.py` (libclang per-TU parse with
real kernel flags, per-function normalised fingerprints) →
`scripts/census_report.py`. Full corpus run: **85,773 functions from 2,998
TUs in 76 s on 28 cores** (2 TU failures: `arch/x86/tools/relocs_*.c`, host
tools — ignorable). Regenerate: both scripts, no args.

## Numbers

| Metric | Value |
|---|---:|
| Functions | 85,773 |
| Distinct fp_exact (kinds + callee names, ids/literals normalised) | 78,533 |
| → identifier-renamed duplicates of another function | **8.4%** |
| Distinct fp_shape (node kinds only) | 72,613 (15.3% collapse) |
| Functions containing inline asm (incl. macro-injected) | 10.6% |
| Functions containing goto | 9.9% |
| Distinct callee names (all) | 79,439 |
| Distinct **external** callee names (same-file excluded) | 40,054 |

External-vocabulary coverage (every external call within top-K APIs):

| top-K external callees | functions fully covered |
|---|---:|
| 100 | 32.3% |
| 500 | 40.7% |
| 2,000 | **51.1%** |
| 10,000 | 71.9% |

Largest exact clusters are macro-generated families: `sys_ni` syscall stubs
(513 identical), module-param `__check_*` wrappers (288), noop callbacks,
`DEFINE_SHOW_ATTRIBUTE`-style expansions. Whole-function duplication in
hand-written code is rare.

## Reading — what this does and does not say about the thesis

1. **Whole functions do not collapse** (8–15%). The translation unit of
   leverage is NOT the function. Anyone claiming "the kernel is N unique
   functions repeated" is wrong at this granularity — good to know early,
   cheap to have learned (one afternoon).
2. **The call vocabulary concentrates hard.** Half the corpus's functions
   have their entire cross-file call surface inside ~2,000 APIs. An API
   type/ownership mapping for the top ~2,000 kernel primitives unlocks the
   call-surface half of translating ~44k functions.
3. Combined with Phase 0's idiom density (one idiom-marker hit per 42 lines
   from just 17 families; 20 structural lock regions found by one SmPL rule
   in 0.27 s), the collapse the thesis needs clearly lives at
   **statement/region granularity**, exactly as PLAN.md's pattern hierarchy
   assumed (LOCKED_REGION, REFERENCE_ACQUIRE, … are regions, not functions).
4. **v1 census (next): region-level fingerprints** — segment function bodies
   into statement regions (lock…unlock spans, error-goto ladders, init
   sequences), fingerprint those, and re-measure the coverage curve. The
   go/no-go gate from PLAN.md applies to *that* number.

Also confirmed by doing: the fingerprint pipeline is fast enough to iterate
freely (76 s full-corpus), so census experiments are effectively free.
