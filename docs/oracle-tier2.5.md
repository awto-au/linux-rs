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

First target (`base64`): **5,000 cases, 10,000 output lines,
byte-identical** — covering all 3 variants, both padding states, and
srclen 0–129 (crosses every mod-3/mod-4 branch in the algorithm).

## Cost vs the other tiers

Cheaper than tier 4 (no kernel boot, no QEMU) but far more thorough than
tier 1–2 for pure functions — thousands of cases in ~50ms vs a handful of
hand-written KUnit vectors. Not a replacement for KUnit where a suite
already exists (`gcd`, `int_log`, …) — those stay the primary oracle;
tier 2.5 fills the gap for TUs that never got one.

## Next

Extend to `ucs2_string`, `win_minmax`, `reciprocal_div` (same pattern:
extract the C, strip kernel-crate types from the Rust, shared LCG,
protocol print). Not yet auto-run by `dev.py check` — deliberate, since
each target needs its own harness file; wire in once 3-4 targets exist
and the pattern is stable enough to templatize.
