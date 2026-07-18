# Local-model offload experiment — retro-test against ground truth

2026-07-16. Built `scripts/offload_translate.py` (rule-matched draft
generation), `scripts/offload_review.py` (independent fresh-context
conformance check), `scripts/offload_measure.py` (retro-test harness +
real token accounting). Model: `qwen2.5-coder:14b`, 100% GPU (RTX 5060
Ti, confirmed via `ollama ps`).

## Method

Ran both stages against 3 of the 17 already-translated TUs
(`bcd`, `argv_split`, `base64`) — deliberately files with a **known
correct answer** (the landed translation), so drafts and reviews could be
checked against ground truth instead of judged by eye.

## Result: 3/3 drafts broken, 3/3 reviews said PASS

Manually inspected all three drafts (not just the automated verdict):

| TU | draft defect | review verdict | correct? |
|---|---|---|---|
| `bcd` | `const t: u32 = (val.wrapping_mul(103)) >> 10;` — **`const` on a runtime value, does not compile** | PASS | **NO** |
| `argv_split` | `static fn count_argc(...)` — **invalid syntax**; final `argv.as_mut_ptr()` returns a pointer into a `Vec` about to be dropped — **use-after-free** | PASS | **NO** |
| `base64` | `unlikely!(...)` called, never defined — **compile error**; `dst.push(base64_table[...])` indexes a `&str` by int, **does not compile**; decode tail logic silently rewritten (returns `Err` instead of C's padding-aware truncation) — **semantic rewrite**, not a translation | PASS | **NO** |

Zero for three. The reviewer did not just miss subtle issues — it walked
past compile errors inside its own quoted code blocks while explicitly
being asked to be exhaustive, and rubber-stamped a *semantic algorithm
rewrite* in `base64_decode`'s tail-handling as faithful.

## What this changes vs the earlier single-function test

The 2026-07-16 rule-0006 test (single function, single rule, hand-picked
example) showed a tightened rule fixes a targeted miss, and a fresh
review pass catches an unlicensed `saturating_sub`. That result stands —
but it was one function with one rule active. At the scale of a real
file (multiple functions, several rules, ~100+ lines), with the model
choosing its own approach rather than being walked through one
transform, **both stages failed across the board**.

Conclusion: the earlier positive result does not generalise from
"single isolated construct" to "whole file." The offload harness as
built is not yet trustworthy at file scale, for either drafting or
reviewing, on this model.

## Token cost (measured, not estimated)

Ollama's own `prompt_eval_count`/`eval_count` per call, real numbers:

| TU | translate tok (prompt/eval) | review tok (prompt/eval) | total |
|---|---|---|---|
| `argv_split` | 4096/558 | 1438/845 | 6,937 |
| `base64` | 4096/1180 | 3487/685 | 9,448 |
| `bcd` | 1867/116 | 517/740 | 3,240 |
| **total** | | | **19,625** |

Note `translate_prompt_tok` pinned at 4096 for the two larger files —
that's the model's **context window limit** (`ollama ps` showed
`CONTEXT 4096`), not the true prompt size; the rule-matching prompt was
silently truncated for `argv_split` and `base64`. That is itself a
likely contributor to the broken output — the model may not have seen
its own full instructions.

**No comparable "Claude cost for the same draft" number exists** — that
would require running the identical prompts through the Claude API
standalone, which was not done. Reporting a savings estimate without that
number would be a fabrication; the report explicitly declines to guess.

## Verdict

1. **The offload_translate/review scripts are useful infrastructure**
   (rule-matching, token accounting, retro-test harness against ground
   truth) — keep them.
2. **qwen2.5-coder:14b at 4096-token context is not viable for whole-file
   offload**, draft or review, as currently prompted. The context-window
   truncation is a confounding bug in the experiment, not just a model
   capability finding — must fix before drawing a model-capability
   conclusion.
3. **Do not use this harness for anything beyond further experimentation**
   until (a) context window is raised or prompts are shortened to fit,
   and (b) a run shows the reviewer catching at least the planted-bug
   class of error (compile errors) reliably.

## Part 2 (same day): the retry cycle (`offload_cycle.py`)

Approach: "maybe Ollama for the translation, rustc/clippy as compile
checks, then run the tests, and only then review — if not, we still
review, but we do the whole cycle again." Built exactly that: draft →
rustc → clippy → (retry with the diagnostic fed back, NOT fresh context —
this is debugging, the model needs to see its own error) → up to N
rounds → only escalate to the paid review stage once free gates pass.

**Harness bug found and fixed along the way**: `rustc -o /dev/null`
intermittently fails with "couldn't create a temp dir... Permission
denied" trying to create a scratch dir *under /dev* — unrelated to the
draft's correctness, and it silently poisoned round 2 of the first cycle
run (logged as a rustc FAIL that had nothing to do with the code). Fixed
by using a real temp directory under `REPO/tmp/` for `-o` (never system
`/tmp`, never `/dev/null`, per project rules) in both `rustc_check` and
`clippy_check`.

**Result after the fix: 3/3 files cleared rustc+clippy in 1–2 rounds.**
`bcd` and `base64` needed one retry (both times: model correctly fixed
the exact reported error, nothing else); `argv_split` cleared first try.

**But clearing the free gates is necessary, not sufficient — and the
gap is exactly where it was predicted to be.** Inspecting the
gate-cleared drafts:

- **`argv_split.draft.rs` compiles clean and is still wrong**: it
  reinvents the function using `std::alloc`/`Layout`/`Vec::from_raw_parts`
  instead of the kernel's `kmalloc_array`/`kfree` — a direct violation of
  rule 0018 (C-ABI allocator contract), which *was* in its matched-rule
  context (10/18 rules matched). `argv_free`'s `dealloc` computes its
  `Layout` from the pointee via `Layout::for_value(&*p)` — this does not
  match the original allocation's layout, which is undefined behavior on
  real allocators (host `std::alloc` happened not to crash on this
  input; that is not a guarantee). The function signatures also changed
  from raw-pointer/C-ABI to `Option<&mut usize>`/`Option<Vec<..>>` —
  **not callable from C at all**, which defeats the entire point of a
  kernel-symbol translation.
- **`base64.draft.rs` is not a translation**: it discarded both real
  functions (`base64_encode`, `base64_decode`, the 3-variant table
  lookup, the decode path) and wrote a demo `main()` that base64-encodes
  a hardcoded `"Hello, World!"` string. Compiles perfectly. Answers a
  completely different question than the one asked.

**Conclusion: rustc/clippy are a valid, free, worthwhile filter — they
reliably catch "doesn't compile," including the class of error the
Ollama review call missed entirely in Part 1. They provide ZERO
protection against "compiles but is not the same function," "abandoned
the required rule," or "solved a different problem."** That failure
class needs either (a) a real diff-oracle comparing behavior against the
C original on many inputs — exactly the tier-2.5 pattern this project
already has for `base64`/`win_minmax`/etc., which WOULD have caught the
`base64` demo-program case immediately (wrong function signature, can't
even run the harness) — or (b) real review, human or Claude, that checks
"is this still the function I asked for."

## Revised verdict

1. Free-gate retry cycle: **works as designed**, cheap, worth keeping as
   a mandatory first stage for any future offload attempt at any scale.
2. It does not make offloaded *drafting* trustworthy on its own — the
   model, unconstrained by a passing test, will happily substitute a
   different (simpler, wrong) implementation that happens to compile.
   The missing piece is not more compiler gates, it's **held to the
   original function's behavior**, which only a diff-oracle or content
   review provides.
3. Next real step: wire tier-2.5-style diff-oracle generation as gate 3
   (after rustc, after clippy) for any C file that's pure enough for it
   — this would reject `base64`'s demo-program draft immediately (wrong
   signature) and give `argv_split` a real behavioral check the
   allocator-swap bug would likely also fail (different crash/UB profile
   under stress inputs vs the C original). Not yet built.
4. The earlier Part-1 finding about `qwen2.5-coder:14b`'s 4096-token
   context window truncating larger prompts is unresolved and still a
   live confound — not isolated in this run either, since the cycle
   prompts are similarly sized. Worth fixing (`num_ctx` override) before
   any further conclusions about this specific model's ceiling.

## Token cost, updated

| TU | round 1→pass tokens | rounds | cleared free gates | actually correct |
|---|---:|---:|---|---|
| `bcd` | 2,788 | 2 | yes | yes (matches landed translation in substance) |
| `argv_split` | 4,711 | 1 | yes | **no** — wrong allocator, non-C-ABI signature |
| `base64` | 10,036 | 2 | yes | **no** — not a translation, demo program |

Only 1 of 3 gate-cleared drafts was actually usable. The free-gate pass
rate (3/3) and the correctness rate (1/3) are different numbers and must
be reported separately — conflating them would overstate how close this
harness is to production-usable.
