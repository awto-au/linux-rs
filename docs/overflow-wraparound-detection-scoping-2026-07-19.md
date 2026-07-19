# Signed-overflow-wraparound detection scoping — 2026-07-19

Scoping for [awto-au/linux-rs#36](https://github.com/awto-au/linux-rs/issues/36)
(`lib/tests/test_sort.c` PRNG overflow panic — first runtime-behavior gap
class in the #28 combined-boot series, all 17 prior classes were
compile/link-time). Research only, no code changes. c2rust rev checked:
`d111918fb4` (HEAD, `awtoau/c2rust`), `operators.rs` logic unchanged since
the `6065eaf19` rev the issue was filed against.

## Bottom line

1. **No cheap, reliable static/mechanical detector exists or is buildable
   with current tooling.** Signedness-driven overflow-reachability requires
   real value-range analysis, not a syntactic pattern — confirmed both by
   reading c2rust's own translator internals (no such analysis exists) and
   by this task's own corpus sample (81.5% of files match a naive syntactic
   grep; true positive rate on manual review was 2/25 = 8%, both non-obvious
   without reading the surrounding logic). Not worth building as a
   `dev.py check-*` tool.
2. **c2rust's existing wrapping-arithmetic machinery is real but
   signedness-gated, not overflow-gated** — it already emits
   `wrapping_add`/`wrapping_mul`/etc unconditionally for *unsigned* C
   arithmetic (rule 0009, landed), and has zero equivalent path for
   *signed* arithmetic. This is a real, scoped upstream/fork patch
   opportunity (see "Fix candidate" below) — arguably a better lever than
   any detector, since it fixes the semantics at the source instead of
   flagging them for human triage.
3. **The existing run-and-see-what-panics boot/KUnit methodology is the
   only real, already-functioning detector for this bug class**, but it
   has a coverage gap: only 2/603 corpus files (test_sort.c and one other
   `lib/tests/*.c` file) are themselves KUnit test files exercising their
   own logic. Everything else's signed-arithmetic correctness is exercised
   only incidentally, if a currently-wired boot/KUnit path happens to call
   it — same shape of gap as #30/#31's fabricated-register-static "boots
   clean but latent" finding. Recommendation is a brief-language fix (see
   part 3), not a new tool.
4. **Found a second, more consequential live instance of this exact bug
   class while sampling**, not hypothetical: `include/linux/refcount.h`'s
   overflow-*detection* logic (`old + i < 0`) is present, un-wrapped, in
   63/603 (10.4%) of the baseline corpus (any TU that pulls in
   `refcount_t`) — see "Severity" below. This is corpus-wide, not a
   one-off test file.

---

## 1. c2rust's actual arithmetic translation policy

`c2rust-transpile/src/translator/operators.rs`, `convert_binary_operator`
(line 505) and its `convert_addition`/`convert_subtraction` helpers (line
541, 570):

```rust
// convert_binary_operator, line 521-538
Ok(WithStmts::new_val(match op {
    CBinOp::Add => return self.convert_addition(lhs_type, rhs_type, lhs, rhs),
    CBinOp::Subtract => {
        return self.convert_subtraction(ctx, expr_type_id, lhs_type, rhs_type, lhs, rhs)
    }
    op if op.is_arithmetic() && is_unsigned_integral_type => {
        mk().method_call_expr(lhs, op.wrapping_method(), vec![rhs])   // <-- unsigned-only
    }
    op if op.is_arithmetic() || op.is_bitwise() || op.is_bitshift() => {
        mk().binary_expr(BinOp::from(op), lhs, rhs)                   // <-- signed falls here, plain op
    }
    ...
```

`convert_addition`/`convert_subtraction` (line 541-601) follow the exact
same shape: `if lhs_type.is_unsigned_integral_type() { wrapping_add/sub }
else { plain BinOp::Add/Sub }`. Compound assignment (`*=`, `+=`, `<<=`,
`convert_assignment_operator_aux`, line 380-479) re-derives
`is_unsigned_arith` from `compute_lhs_type_kind.is_unsigned_integral_type()`
and takes the same branch — so `r *= 725861` on a signed `int` would hit
the identical bug even though the issue's actual repro (`r = r * 725861 %
6599`) is a simple assignment of a binop, not compound-assignment.

`wrapping_method()` (`c2rust-transpile/src/c_ast/mod.rs:2338`) maps
`Add/Subtract/Multiply/Divide/Modulus` to
`wrapping_add/sub/mul/div/rem` — **the machinery to fix this for signed
types already exists and is fully wired**, it's just never invoked because
every call site gates on `is_unsigned_integral_type()`.

No `is_signed_integral_type` gate, no value-range check, no
`-ftrapv`/UBSan awareness anywhere in `operators.rs` or `mod.rs`
(`grep -rn "ftrapv\|signed-integer-overflow\|fsanitize\|is_signed" operators.rs mod.rs`
= zero hits).

**Distinct from, and already solved:** explicit `__builtin_add_overflow` /
`__builtin_sub_overflow` / `__builtin_mul_overflow` (used by the kernel's
`check_mul_overflow()`-family macros) — `builtins.rs:699`
`convert_overflow_arith` translates these to `overflowing_*` correctly.
Rule 0009's own `negative` field already flags this as a different case.
This scoping is about *implicit* silent-wrap C semantics on plain
`*`/`+`/`-`/`<<`, not the explicit-overflow-checking-builtin path.

### Upstream prior art: same bug class hit before, patched narrowly, never generalized

`awtoau/c2rust` git history (inherited from upstream `immunant/c2rust`):

- `167a7f618` (2018): "Translate += -= ++ -- on unsigned types to use
  wrapping operations" — the origin of today's unsigned-only gate.
- `f091f45cf`/`e50d48ee9` (2023, upstream issue
  [immunant/c2rust#795](https://github.com/immunant/c2rust/issues/795)):
  "attempt to negate with overflow" panic found by literally running
  c2rust against systemd's real corpus in a debug build — same discovery
  mechanism as #36 (run it, watch it panic), fixed by special-casing
  `(-value) as U` → `value.unsigned_abs()`. Narrow, pattern-specific fix,
  not a general signed-overflow pass.
- `gh search issues/prs "signed overflow"` / `"wrapping_mul signed"`
  against `immunant/c2rust`: **zero hits**. General signed `*`/`+`/`<<`
  wraparound has never been raised upstream at all.

Consistent picture: this bug class has recurred at least three times
across the tool's history (unsigned compound-assign 2018, negation-to-cast
2023, this project's signed-multiply 2026), each time discovered by
running real code and patched narrowly for the exact pattern found — never
generalized. No reason to expect the next occurrence to be caught any
other way either, absent a policy change.

## 2. Mechanical detection signal — none feasible; here's why each option was rejected

**Static value-range analysis in c2rust's clang frontend**: no such
analysis exists anywhere in the transpiler (confirmed by full-text search
of `translator/`). Building one from scratch (interval/range analysis
across function boundaries, loop-carried accumulator detection) is a
compiler-construction project on the scale of a symbolic executor, not a
`dev.py check-*` script — explicitly out of proportion to the actual
finding rate (see Severity below).

**Pre-translation UBSan/`-ftrapv` triage on the C original**: `clang
-fsanitize=signed-integer-overflow` works and was verified locally
(`clang -fsanitize=signed-integer-overflow -fno-sanitize-recover=all`
correctly traps a standalone `INT_MAX * 2` repro). But kernel `.c` files
are not standalone translation units runnable under a userspace harness —
they need the full kernel header/config environment
(`-ffreestanding`, arch defines, no libc) to even parse, and running them
"as C" would mean synthesizing a userspace fuzz harness per file with
concrete inputs, which is real per-file engineering work on par with
writing a KUnit test — at which point you should just write the KUnit
test and run the real translated Rust, which is strictly better signal
(catches translation bugs too, not just source-level UB). Not a cheap
pre-triage step; as expensive as the thing it would be triaging *for*.

**Miri on the translated corpus**: `cargo miri` is not installed
(`rustup component add miri` needed on a nightly toolchain; a nightly is
present locally but Miri was not installed/tested further because the
deeper blocker is architectural, not toolchain availability) — these are
`no_std` kernel-style crates with inline `asm!`, raw MMIO, and FFI to
kernel symbols that don't exist outside the kernel link (e.g. `get_current()`
reading the `tp` register per rule 0031). Miri interprets a single Rust
binary/test harness; there is no host-executable entry point for
`lib/tests/test_sort.c`'s translated form outside the actual kernel/KUnit
runtime. Not usable without building a from-scratch mock-kernel harness
per function — again, real per-file engineering, not a corpus-wide sweep.

**Syntactic grep** (`grep '* CONST) %'`-shaped, or broader `* / + / <<` on
signed-typed operands): tested directly, see Severity below. 81.5%
false-positive rate at the file level makes it useless as a gate; it would
need to become a triage worklist reviewed by hand per hit, which is not
meaningfully cheaper than the existing per-file combined-boot review this
project already does.

**Conclusion for part 2**: no new `dev.py check-*` tool is being
recommended. The nearest existing analogue,
`check_c2rust_rule_conformance.py`'s `check_0009_unsigned_wrap_mul`
(line 354-379), already documents this exact limitation in its own
docstring — *"can't reliably tell signedness from text alone for locals
without types in scope"* — and reports `STATUS_AMBIGUOUS` rather than a
confident violation for exactly this reason. Extending it to signed types
would inherit the identical unreliability, just inverted (can't reliably
prove the *absence* of an unsigned-wrap-style fix is a bug rather than
provably-safe plain arithmetic).

## 3. Severity/scope — real corpus numbers, not a guess

Corpus: 603 dirs under `tmp/c2rust-baseline/*/output/src/*.rs` (585-588
have generated `.rs` output; a handful are compile-commands-only with no
translated file yet).

### Syntactic grep false-positive rate (why grep isn't a detector)

Scripted scan (`re` over all 585 `.rs` files) for lines containing a plain
`*`/`+`/`<<` (excluding `wrapping_*`/`checked_*`/`saturating_*`/
`overflowing_*` and raw-pointer contexts) co-located with a signed-type
token (`c_int`/`c_long`/`c_short`/`i8..i64`/`isize`):

```
total files: 585
files with candidate signed plain-arith lines: 477   (81.5%)
total candidate lines: 27635
```

Top hits (`kernel_ucount.c` 1067 lines, `fs_fcntl.c` 938,
`mm_vmalloc.c` 844, ...) are dominated by loop-index arithmetic, pointer
computations mis-flagged by the heuristic's own imprecision, and small
enum-bounded offsets — none of which are real overflow risks. Confirms
the issue's own prediction: naive grep is useless as a filter.

### Manual review sample (25 files, random seed, 2 independent reviewer passes)

25 files sampled at random (`random.seed(42)`, uniform over all 585)
manually read (not grepped) by two parallel reviewers, judging genuine
overflow *plausibility* (bounded loop counters / small-enum shifts /
fixed-constant multiplies excluded; unbounded accumulators, PRNG/hash/
checksum-shaped expressions, and saturation-detection logic counted):

| Verdict | Count | Files |
|---|---|---|
| PLAUSIBLE | 2/25 (8%) | `fs_fs-writeback.c`, `lib/kunit/try-catch.c` (via inlined `refcount.h`) |
| LOW-plausibility | 4/25 (16%) | `virtio_blk.c` (`index << PART_BITS`), `mm_vma.c`, `lib/fdt_wip.c`, `drivers/of/fdt.c` |
| NONE | 18/25 (72%) | remainder — unsigned-typed operands, fixed constants, or provably-bounded loop counters |
| n/a (no generated output) | 1/25 | `mm_page_vma_mapped.c` (compile-commands only) |

Both PLAUSIBLE hits independently re-verified by direct grep against the
generated files (not taken on reviewer say-so):

- **`fs/fs-writeback.c`** (`tmp/c2rust-baseline/fs_fs-writeback.c/output/src/fs_writeback.rs:8186-8193`):
  `(*work).nr_pages -= write_chunk - wbc.nr_to_write;` /
  `total_wrote += wrote;`, `c_long` page counters accumulated across an
  unbounded `while` loop over all dirty inodes in a sync. C original
  (`linux-riscv/fs/fs-writeback.c:2087-2089`) computes `wrote = write_chunk
  - wbc.nr_to_write - wbc.pages_skipped;  wrote = wrote < 0 ? 0 : wrote;`
  — **the C code's own negative-clamp is direct evidence the original
  author expected this arithmetic could go negative/wrap**, i.e. this is
  the same "load-bearing wraparound" shape as test_sort.c's PRNG, just
  less obviously so.
- **`lib/kunit/try-catch.c`** (via `#include <linux/refcount.h>` inlined
  into the TU — not actually kunit-specific code):
  `tmp/c2rust-baseline/lib_kunit_try-catch.c/output/src/try_catch.rs:4063,4098`:
  `old + i < 0 as ::core::ffi::c_int` / `old - i < 0 as ::core::ffi::c_int`
  in `__refcount_add`/`__refcount_sub_and_test`. `refcount.h`'s own header
  comment (`linux-riscv/include/linux/refcount.h:8-38`) documents this as
  **deliberate**: `refcount_t` intentionally lets the underlying counter
  wrap past `INT_MAX` into negative, then detects that wraparound via
  `old < 0`, as its overflow-saturation mechanism — "we temporarily allow
  the counter to take on an unchecked value". Rust's checked `+`/`-`
  panics on exactly the input this code exists to detect and handle
  safely, which is worse than doing nothing: it turns a designed
  safety mechanism into a crash.

### Corpus-wide reach of the `refcount.h` case specifically

Because `refcount_t` is the kernel's standard reference-counting
primitive, its overflow-detection logic is re-inlined into *every*
translation unit that includes `refcount.h`, independent of the file
being reviewed for its own logic:

```
$ grep -l "__refcount_add\|__refcount_sub_and_test" tmp/c2rust-baseline/*/output/src/*.rs | wc -l
63
```

**63/603 (10.4%) of the entire baseline corpus** carries this exact
un-wrapped signed-comparison-after-wrap pattern, not as a one-off but by
construction (anything using `refcount_inc`/`refcount_dec`/
`kref_get`/etc.). Spot-checked one (`drivers/base/core.c`, real
boot-path device-link refcounting, same subsystem as #30's live boot
hang): `refcount_inc()` is genuinely called there
(`tmp/c2rust-baseline/drivers_base_core.c/output/src/core.rs:13904,14044`),
not dead code. None of the 63 files are yet landed in `linux-riscv/`
(still baseline-corpus-only, not yet triggered in any boot) —
**latent, not yet fired**, same status as #31's `is_single_threaded.c`
finding before it was checked.

### Reading

Not "rare, one weird test file." Two real, load-bearing patterns found
in a 25-file sample (8%), one of which (`refcount_t`) is structural and
present in over a tenth of the whole corpus by simple grep, because it's
inlined from a near-universally-included header rather than being
file-specific logic. Whether it becomes a *boot-observed* bug the way
`test_sort.c` and `klist.c` did depends only on which of those 63 files
gets landed and wired into a reachable call path next — same "latent
until landed" risk shape as rule 0031's register-static bug.

## Recommendation

**No new detector tool.** Instead:

1. **Fix candidate worth raising against `awtoau/c2rust` directly** (not
   scoped further here, flagging for a follow-up issue): extend the
   existing `is_unsigned_integral_type()` gate in `convert_binary_operator`
   /`convert_addition`/`convert_subtraction`/`convert_assignment_operator_aux`
   to also emit `wrapping_*` for signed arithmetic. Per fork policy
   (permanent fork, patches additive/opt-in only — see project memory),
   this would need to be an opt-in flag (e.g.
   `--wrap-signed-arithmetic`), not a default-behavior change, since it
   changes translated semantics for the whole corpus at once and needs
   its own conformance-rule review (would every landed file's rule
   0009-adjacent conformance check need re-running?). Real effort, but
   fixes the root cause corpus-wide in one place instead of per-file
   whack-a-mole.
2. **`refcount.h`'s pattern (63 files) is worth a standing note in
   rulesdb** even without a mechanical check — same shape as rule 0031's
   `[status]` entry documenting a known-real, not-yet-mechanized risk.
   Any future combined-boot candidate that includes `refcount.h`
   (`grep -l refcount_t` / `grep -l "__refcount_add\|__refcount_sub_and_test"`
   against the candidate's `_rs.rs`) should get this flagged explicitly in
   the review, same as rule 0031 requires checking `get_current()`
   call-liveness before trusting "boots clean."
3. **Future combined-boot agent briefs should explicitly instruct:
   "if the file (or a header it transitively pulls in) has a matching
   KUnit suite that calls the translated logic, confirm the suite
   actually ran and passed — don't just confirm build+boot+`INIT
   REACHED`."** This project's own `docs/combined-boot-attempt-2026-07-18.md`
   already flags this gap for `lzo1x_decompress_safe.c` ("no dedicated
   ... KUnit suite exists ... so the function's runtime correctness wasn't
   directly exercised") — it's a known, previously-noted limitation, not
   a new one. Only 2/603 corpus files are themselves KUnit test files
   (`test_sort.c` + one other `lib/tests/*.c`), so self-test coverage is
   the exception, not the rule — most files' signed-arithmetic
   correctness depends entirely on whichever *other* code happens to call
   them along the currently-wired boot path, same incidental-coverage
   shape that let #31's bug sit latent on `origin/main` uncaught.
