# Oracle tier 2.5 — host differential testing

Added 2026-07-16, per PLAN's oracle gap: many pure-function TUs
(`ucs2_string`, `base64`, `win_minmax`, `reciprocal_div`) have no
dedicated KUnit suite, so their only validation was "compiles, links,
boot doesn't crash" — tiers 1–2. That's real but weak: a function can be
callable and subtly wrong on some input class without ever being
exercised by the boot path.

## What it is

`dev.py diff <target>` builds `bench/diff_<target>.c` (the C original,
extracted host-buildable) and `bench/diff_<target>.rs` (the Rust
translation, kernel-crate types stripped) against a **shared explicit
LCG** (same constants, same extraction, defined identically in both
files — critical: libc `rand()` / language-stdlib RNGs do NOT agree
across languages even with the same seed, so hand-rolling one shared
generator is required, not optional). Both binaries consume the identical
input stream and print a transcript; the runner diffs it byte-for-byte.

Targets landed:

| target | cases | output lines | notes |
|---|---:|---:|---|
| `base64` | 5,000 | 10,000 | all 3 variants, both padding states, srclen 0–129 (every mod-3/mod-4 branch) |
| `win_minmax` | 12,000 (200 seqs × 60 steps) | 24,000 | **stateful**: each sequence feeds a fresh tracker a run of strictly-increasing timestamps with irregular gaps and a randomised window, to hit the reset / quarter-window / half-window branches of `minmax_subwin_update` |
| `ucs2_string` | 12,500 (2,500 inputs × 5 ops) | 25,000 | random UCS2 strings biased across all 3 UTF-8 length classes (`<0x80`, `0x80..0x800`, `≥0x800`) plus embedded NULs; exercises `strnlen`/`utf8size`/`strncmp`/`strscpy`/`as_utf8` together |

All three **PASS, byte-identical**, first run after fixing the shared-RNG
issue below.

## Cost vs the other tiers

Cheaper than tier 4 (no kernel boot, no QEMU) but far more thorough than
tier 1–2 for pure functions — thousands of cases in ~50ms vs a handful of
hand-written KUnit vectors. Not a replacement for KUnit where a suite
already exists (`gcd`, `int_log`, …) — those stay the primary oracle;
tier 2.5 fills the gap for TUs that never got one.

## Remaining gap: `reciprocal_div`

Not given a diff-oracle target. Its only interesting branch (`l == 32`
overflow) is a `WARN`-and-continue diagnostic path already covered by the
boot-smoke oracle (tier 1–2) and not a correctness fork — the function
returns the same (technically-overflowed) result either way, matching C.
A differential harness would mostly re-test plain 64-bit arithmetic
already proven by `bench/cref.c`'s existing coverage. Lower priority than
the three landed targets; revisit if a bug is ever suspected here.

## Next

All four originally-flagged tier-1/2-only TUs are now covered (3 via
diff oracle, 1 judged not to need it, with reasoning recorded). Not yet
auto-run by `dev.py check` — deliberate, since each target needs its own
harness file; wire in via `dev.py diff --all` once enough targets exist
that iterating them is worth automating.
