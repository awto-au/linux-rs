# Research: pipeline & foundation review — 2026-07-18

Deep, foundational research (not implementation) after a full day of finding
and fixing systematic bugs in the `awtoau/c2rust` transpiler fork. The
question behind all four sections: **is c2rust the right foundation, or are we
fighting an architecture that wasn't built for this?** Every claim below is
grounded in code actually read (file:line cited) or a tool actually run, not
in reassurance.

Scope note: this project never pushes branches or opens PRs against
`immunant/c2rust`. All c2rust references are context, and issue #9 is being
fixed concurrently by another agent — the analysis below was formed by reading
the code independently, not by waiting for that fix.

---

## Section 1 — c2rust / Clang-interface soundness

**Verdict: the Clang interface is sound. c2rust consumes Clang's own resolved
AST and does not hand-roll type resolution. The systematic bugs are not
"c2rust disagrees with Clang" — they are "c2rust under-uses what Clang already
told it, and does so asymmetrically." That is a fixable class of gap, not a
wrong foundation.**

### The exporter is a faithful, read-only consumer of Clang's resolved AST

`c2rust-ast-exporter/src/AstExporter.cpp` (3389 lines) is a classic
`ASTConsumer` + `RecursiveASTVisitor`/`TypeVisitor` that runs *after* parsing
and Sema are complete (`HandleTranslationUnit`, AstExporter.cpp:2991;
traversal at :3012-3014). It pulls in no `clang/Sema/*` header at all; the only
two Sema mentions in the file are comments pointing at where Clang's own logic
lives (:1882, :2811). It never calls `getSema()`. This is the correct design:
Clang does name lookup, overload resolution, and type checking; c2rust
serialises the finished result. It leans on Clang for everything semantic —
type identity is Clang's raw `Type*` pointer with qualifier bits
(`encodeQualType`, :225-245), desugaring is Clang's `desugar()` throughout the
`TypeEncoder`, layout is `getASTRecordLayout` (:2388, :2508). There is **no
hand-rolled canonicaliser or type-unifier** that could drift from Clang.

For the specific "is this tag forward-declared-only or a real definition"
decision, c2rust **does** use Clang's own machinery:
- Records: `auto def = D->getDefinition();` (AstExporter.cpp:2365), and the
  presence bit is exported to Rust as `cbor_encode_boolean(local, !!def)`
  (:2407). Non-canonical redeclarations are collapsed to the canonical decl
  and emitted only as lightweight `TagNonCanonicalDecl` comment/attribute
  anchors (:2346-2363).
- Enums: `TypeEncoder::VisitEnumType` calls `getDefinition()` and falls back to
  `getCanonicalDecl()` for the illegal-but-real bare `enum Foo;` forward
  declaration (:2843-2851, with a comment citing immunant PR #1743).

So the theory that "Clang has a single canonical resolved decl and c2rust picks
the wrong one" is **not** what is happening. c2rust asks Clang the right
question. The bug is downstream of the answer.

### The real root cause of issue #9 is a Rust-side asymmetry, not a Clang gap

`c2rust-transpile/src/c_ast/conversion.rs` (the CBOR → Rust-side C-AST
reconstructor) does **no** tag-identity dedup of its own — it trusts the
exporter's canonical IDs and converts each importer ID once
(conversion.rs:482-497). That is fine *as long as every decl path carries the
definition-vs-forward-decl discipline*. It does not:

- **Structs/unions**: the Rust side reads the `has_def` boolean
  (conversion.rs:2417 struct, :2458 union) and only populates fields when it is
  true, yielding `fields: None` for a forward decl. There is a
  `TagNonCanonicalDecl` back-patch path — **but it exists only for
  Struct/Union** (`matches!` at :2553).
- **Enums**: the enum arm (conversion.rs:2319-2343) reads **no `has_def` /
  `is_complete` field at all** and unconditionally builds `variants` from
  whatever children Clang provided. There is no enum equivalent of the
  struct back-patch and no coalescing.

This asymmetry is deliberate on the exporter side — `VisitEnumDecl` explicitly
*cannot* early-exit on `!isCompleteDefinition()` the way records do, because
ISO C has no forward-declared enums (AstExporter.cpp:2442-2445) — but the Rust
side never compensates. Result, exactly as issue #9 documents it: a bare
`enum kobj_ns_type;` in one header becomes an opaque `extern { pub type
kobj_ns_type_0; }` (correct in isolation), while the real definition's
enumerators in another header are emitted as `pub const KOBJ_NS_TYPE_NONE:
kobj_ns_type_0 = 0;` — a `const` whose type is an unsized extern type, which is
`E0277`. 100 of 228 clean-outcome files fail on this one shape (72 `kobj_ns_type`
+ 22 `pid_type` + 6 `hrtimer_restart`, per `tmp/c2rust-output-compile-report.md`).

**This confirms the "architectural gap in HOW c2rust consumes the AST" theory,
but relocates it**: the gap is not in the Clang query, it is that the
record-path's rigor (`has_def` gate + non-canonical back-patch) was never
replicated on the enum path. The fix lives at conversion.rs:2319-2343 (add a
completeness gate / coalesce the forward decl into the definition using the
already-present `IdMapper::merge_old`, conversion.rs:107-117, which no tag path
currently uses). As of this writing no fix has landed on the branch — the
verdict above is from independent code reading.

### The wider lesson: use Clang's precomputed analyses instead of patching output

The five bug classes found today split cleanly into two kinds:

1. **"Under-used Clang answer"** — #9 (enum, above) and #12 (bool/int cast:
   `convert_warn_on` re-casts an already-`bool` condition `as c_int`, E0308,
   mismatching the `warn_on!` bool param). These are places where Clang already
   has the type/definition fact and c2rust either ignores it or re-derives it
   wrongly. **Fix the consumption, not the symptom.**
2. **"Genuinely unmapped construct"** — #10 (RISC-V inline-asm GCC constraint
   letters A/I/J/K passed through raw as invalid Rust register classes, 61
   files), #11 (goto/label lowering emitting undeclared `'___UNIQUE_ID_label_N`
   break targets, E0426), #8 (now closed: stale nightly stdlib API names like
   `from_exposed_addr`/`VaListImpl` emitted regardless of toolchain). These are
   real per-construct translation work, not AST-consumption bugs.

Concrete "use more of Clang" opportunities that would eliminate classes rather
than patch instances:

- **Clang's own `-Wall`/`-Wextra` diagnostics on the input** would flag several
  input shapes that translate badly (e.g. implicit int/enum conversions that
  become #12) *before* transpile, letting the pipeline route them to
  hand-translation instead of shipping broken Rust. Cheap: it's a compile flag.
- **The inline-asm register/constraint mapping (#10)** is the one place c2rust
  genuinely hand-rolls something Clang models better — Clang's own asm operand
  classification (`SimplifyConstraint`, AstExporter.cpp:1417, which carries a
  `// TODO: handle more cases`) is where the A/I/J/K letters are dropped. This
  is target-specific and will need real per-arch work regardless of tooling.
- c2rust does *not* need Clang's static analyzer or its LLVM IR — those operate
  below the AST level c2rust translates from, and using them would mean
  translating optimised IR rather than source structure, which is the wrong
  altitude for a readable-Rust goal.

**Bottom line for Section 1: the foundation is sound. Keep c2rust. The bug
classes are tractable and mostly one-way (consume Clang more faithfully), not a
symptom of building on the wrong abstraction.**

---

## Section 2 — KernelIdiomRule generalisation

**Verdict: narrow-but-fine *for now*, but the hardcoded-enum form will not
scale to dozens/hundreds of idioms, and this project already owns the better
design. The two systems solve genuinely different problems and should stay
separate as runtime artifacts — but the TOML rule format should become the
*authoring source of truth*, with `kernel_idioms.rs` variants generated from
or validated against it.**

### What `kernel_idioms.rs` actually is

`c2rust-transpile/src/kernel_idioms.rs` (121 lines) is a `#[derive(EnumString)]`
enum with one variant per idiom (`WarnOn`, `FlsFamily`, `SwapMemSwap`,
`AddrLabelPlaceholder`) plus an `All`. Each variant is opt-in via
`--enable-rule` and — critically — **the actual detection and emission code
lives elsewhere, at "whatever call site recognizes the corresponding
macro/function"** (kernel_idioms.rs:23-27). The enum is just a toggle registry;
the real per-idiom logic is a bespoke Rust match-arm somewhere in the
translator for each one. Adding idiom N+1 means (a) a new enum variant and (b)
new hand-written detection+emission code at a call site. The doc comments on
each variant are excellent and precise (they encode *why* the transform is
sound), but they are prose, not machine-checkable.

This is fine at n=4. At n=40 or n=400 it is a maintenance and correctness
problem: every idiom is a separate code path with no shared validation harness,
no declarative record of what it matches or what its known-wrong forms are, and
no way to mine the corpus for *candidate* idioms — a human/agent has to notice
each one, one file at a time.

### The project already has the more general design

`rulesdb/rules/*.toml` (27 rules) is a declarative format with structured
`[match]`, `[emit]`, `[provenance]`, and `[validation]` sections. Compare
`0006-fls-family.toml` (the TOML for the *same* idiom `kernel_idioms.rs` hard-
codes as `FlsFamily`): it carries the exact arithmetic transform, an explicit
`deviations` note (defined-at-0 where C is UB), `validation.instances` with
oracle tier, and — the part no Rust enum variant can hold — a `negative`
section recording a *specific known-wrong translation seen in practice*
("translating fls(x) as bare `x.leading_zeros()` ... compiles, looks plausible,
and is silently off by a constant + inverted ... reject on sight"). That is a
machine-queryable regression guard. `0020-goto-shared-label-distinct-value.toml`
similarly encodes a translation *checklist item* with a `constraints` array and
a `negative` set of shapes it must not fire on.

The TOMLs are already normalised into `patterns.db`
(`rules`/`rule_constraints`/`rule_negatives`/`rule_evidence`/
`rule_validation_instances`, per docs/patterns-db.md), so "which rules mention
`spin_lock`" or "which hot statement families have no covering rule"
(`uncovered_hot_families` view) are already one query. This is strictly more
general than `kernel_idioms.rs`'s enum+match-arm-per-idiom.

### Are they the same problem? Partly — and the overlap is the opportunity

They are **not** identical: `kernel_idioms.rs` rules fire *inside the c2rust
transpiler* at AST-conversion time (they need access to Clang macro-origin
data, the `StmtExpr` shape, etc.); the TOML rules today drive *hand-translation
and conformance-checking* (`check_c2rust_rule_conformance.py` reads them to
verify c2rust output). A TOML file cannot, by itself, execute a Rust AST
rewrite inside c2rust. So they should stay separate **as runtime artifacts**.

But the *authoring* is redundant and drift-prone: `0006-fls-family.toml` and
the `FlsFamily` variant describe the same transform in two hand-maintained
places. The real opportunity is to make the TOML the **single source of
truth**, and have `kernel_idioms.rs` either (a) generate its variant list +
help text from the TOML set at build time, or (b) carry a test that asserts
every `--enable-rule` name has a matching `rulesdb/rules/*.toml` with a
non-empty `[emit]`. That kills the drift issue #5 already flags ("routine-run
stability exclusion can't detect idiom-rule content changes") and gives every
in-transpiler idiom the TOML's `negative`/`validation` guards for free.

The genuinely missing capability — **automatic idiom-candidate discovery** —
is also already half-built and lives on the TOML/DB side, not the enum side.
`patterns.db`'s `uncovered_hot_families` view ranks statement families with
many corpus instances and no covering rule; combined with a "these files
translated badly" signal (the c2rust compile-check + clippy, Section 3), the
pipeline could *mine* recurring translated-badly patterns instead of waiting
for a human to notice one. That is a DB query plus a clustering pass, not new
transpiler code.

**Bottom line for Section 2: the enum form is fine at n=4 and does not need an
emergency rewrite, but it is the wrong long-term home. Unify on the TOML format
as the authoring source of truth; keep the two execution paths separate; invest
the generalisation effort in corpus-mining for new idiom candidates, which the
DB already supports.**

---

## Section 3 — pipeline tool additions (ranked, cost/benefit)

Current pipeline: libclang (AST export) → c2rust (transpile) → rustc (real
compile-check, added today) → sparse + cscope (imported into `patterns.db`) →
tier-2.5 diff-oracle + KUnit boot (correctness). The ranking below is by
value-per-cost, split into "cheap, do soon" and "expensive, needs its own
investigation first."

### Tier A — cheap, high-value, do soon

1. **cargo clippy / clippy-driver on transpiled output.** *Empirically
   confirmed today: c2rust raw output does NOT lint clean.* A clean-compiling
   file (`lib_zstd_common_error_private`) produces **74 clippy warnings**
   (`needless_return`, `wildcard_in_or_patterns`, `manual_c_str_literals`,
   `unnecessary parentheses`, …) — c2rust even emits blanket
   `#![allow(clippy::missing_safety_doc)]` itself, so it is clippy-aware but
   suppresses. Cost: low, but *not* zero — clippy needs the same
   rmeta-linking plumbing as `check_c2rust_output_compiles.py` (the standalone
   `nightly-2026-07-09` clippy-driver hit an `E0514` SVH mismatch against the
   kernel's `libcore.rmeta`; it must run through the kernel's exact toolchain
   the way the compile-check does). Benefit: a rich, ready-made,
   deny-driven signal for the Section-4 idiom-narrowing pipeline and a second
   quality gate on hand-translations. **Do this next; it is the highest-value
   cheap add.**

2. **Clang `-Wall`/`-Wextra` on the *input* C, imported into `patterns.db`.**
   Cost: one compile flag + an importer like `import_sparse.py`. Benefit: flags
   input shapes that translate badly (implicit enum/int conversions → #12,
   etc.) before transpile, so readiness ranking can route them to hand-
   translation. Complements sparse (which is kernel-semantic:
   `__user`/`__iomem`) with generic-C hygiene.

3. **Run c2rust's OWN output through the existing tier-2.5 diff-oracle.** Cost:
   low — the harness (`scripts/diff_oracle.py`, `bench/diff_*.{c,rs}`) exists;
   this reuses it with the c2rust `.rs` as the Rust side instead of a
   hand-translation. Benefit: catches *semantic* bugs beyond "does it compile"
   — exactly the class the goto/label bug (#11) and the enum bug produce
   silently. The 21 real-compile-clean files are the natural first candidates
   (several are pure algorithms: `inftrees`, `gcd_key`, zstd/xz decoders).
   Strong value; gated only on those files also passing the enum/asm fixes.

### Tier B — valuable but needs its own investigation first

4. **bindgen/cbindgen cross-check of c2rust's `extern "C"` re-declarations.**
   c2rust manually re-declares kernel types (`atomic_t`, `static_key`, … — see
   `gcd_key.rs`) rather than deriving them from headers; if any drift from what
   bindgen produces from the same headers, that is a *silent ABI/layout
   correctness risk*, not just a lint. Cost: medium (needs a bindgen run over
   the same header closure and a structural diff). Benefit: high-severity but
   probably low-frequency; worth a scoped spike, not an immediate build.

5. **miri on unsafe blocks.** The pinned `nightly-2026-07-09` toolchain *has*
   the miri component installed (verified today). Cost: medium-high — miri
   needs an executable harness per function and does not run kernel code
   (no_std + kernel bindings + inline asm are out of miri's scope). Benefit:
   real UB detection, but only on the *portable pure-logic* subset (the same
   files the diff-oracle targets). Investigate as a per-function UB check on
   extracted pure functions, not on kernel-integrated code.

### Tier C — already present, under-used (harvest before adding anything)

6. **sparse's `__user`/address-space findings.** Already imported: **651 rows**
   of genuine `__user`/address-space misuse
   (`sparse_address_space_findings` view) — the one kernel-semantic class no
   other tool models. Directly feeds rule 0015 (`USERSPACE_TYPED_COPY`) and any
   raw-pointer-safety decision in Section 4. **Query this view, not the raw
   214k-row `sparse_diagnostics` table** (79% is C89 `mixing declarations and
   code` noise). Under-used today; no new tool needed, just wire it into
   readiness/idiom-candidate selection.

7. **cscope call-edges with ambiguity flags.** Already imported
   (`cscope_call_edges`, with `definition_ambiguous`). Real dependency-order
   information for deciding translation order; currently only lightly consumed.

**Do-soon shortlist: (1) clippy on output, (3) c2rust output through the
diff-oracle, (6) harvest sparse `__user` findings. Investigate-first: (4)
bindgen cross-check, (5) miri on extracted pure functions.**

---

## Section 4 — the unsafe-to-safe pipeline (architecture sketch)

A genuinely new, parallel initiative — not a modification of the c2rust work.
Once C is Rust (however it got there), it is unsafe-everything: the c2rust
clean-outcome corpus contains **199,687 `unsafe` occurrences**. A second stage
should progressively narrow `unsafe` / convert raw pointers to references /
apply idioms, driven by lint+unsafe-count signals, and — the key move —
**verified for real by the existing QEMU boot oracle at every increment**, not
just compile-checked.

### What already exists in the ecosystem (and why none of it replaces the plan)

- **`c2rust-analyze/`** (read firsthand): a real, substantial Polonius-based
  pointer-permission analysis — 100 KB `analyze.rs`, a `borrowck/` dir using
  Polonius `Origin`s (`labeled_ty.rs:2`, `context.rs:234`), `dataflow/`,
  `pointee_type/`, and a full `rewrite/` subsystem (`apply.rs`, `ty.rs`,
  `expr/`) that can `--rewrite-in-place`. **Not a toy.** But its own README is
  candid: rewrites "only apply to a small subset of unsafe Rust code," it
  "does not remove the `unsafe` keyword from function definitions" even when it
  empties the body, and — critically for us — in non-amalgamated cross-module
  builds it "may rewrite the signature of the `#[no_mangle]` function ... in a
  way that's incompatible with the corresponding `extern "C"` declaration in
  another module ... segfaults or other undefined behavior at run time." That
  last sentence is the whole argument for a **boot oracle**: a compile-passing
  c2rust-analyze rewrite can still be runtime-UB. This project's QEMU gate
  catches exactly that; c2rust-analyze alone cannot. This tool was marked
  "out of scope, not transpile/ast-exporter" in earlier upstream-review
  passes — **that verdict should be revisited for this new idea**: it is the
  best available candidate-generator for the narrowing pipeline.
- **`c2rust-refactor/`** (read firsthand): explicitly "aimed at removing
  unsafety from automatically-generated Rust code," mark-driven (`select` +
  command, marks not preserved across invocations). Older and less automated
  than c2rust-analyze; useful as a source of targeted rewrite commands
  (`func_to_method`, `rename_struct`, …) but not a batch driver.
- **Ecosystem unsafe-counting**: `cargo-geiger` (counts unsafe usage per crate;
  a reporting signal, does not rewrite) is the natural "driving signal" tool —
  but for `no_std` kernel code it needs the same toolchain plumbing as clippy,
  and clippy's unnecessary-`unsafe`-block lints plus a raw `grep -c unsafe` per
  file are a cheaper first signal that already works.
- **Academic**: the Laertes / "ownership-guided C-to-Rust" / "aliasing limits
  on translating C to safe Rust" line of work (Emre et al.) is the research
  foundation c2rust-analyze descends from — useful as a design reference for
  *which* pointer patterns are provably safe to lift, not as drop-in tooling.
  (A fuller ecosystem survey was commissioned via web search alongside this
  report; the firsthand-read `c2rust-analyze`/`c2rust-refactor` findings above
  are the load-bearing part and stand on their own.)

**Conclusion: c2rust-analyze is the best available *candidate-generator* for
unsafe-narrowing rewrites, but it is prototype-grade and can produce
runtime-UB rewrites that compile. It must be gated by a runtime oracle. This
project already has that oracle. That is the whole opportunity.**

### Architecture sketch (reusing existing infrastructure, not rebuilding it)

```
  translated Rust TU (unsafe-first, boot-verified baseline)
        │
        ▼
  [1] MEASURE      grep/clippy unsafe-count + clippy::all warnings
                   → row in patterns.db (new: unsafe_metrics table)
        │
        ▼
  [2] PROPOSE      one narrowing candidate at a time, from:
                     • c2rust-analyze --rewrite-in-place (subset it handles)
                     • TOML safe-lift rules 0023/0024/0025 (already authored)
                     • clippy machine-applicable suggestions (needless_return …)
                     • sparse __user findings → typed-copy lift (rule 0015)
        │
        ▼
  [3] COMPILE      rustc --emit=metadata gate (reuse
                   check_c2rust_output_compiles.py's exact extern/target setup)
        │  pass
        ▼
  [4] BOOT-VERIFY  dev.py check  → ORACLE PASS (KUnit) + INIT REACHED
                   the candidate is ACCEPTED only if the kernel still boots
                   and every KUnit vector is still green
        │  pass                              │ fail
        ▼                                     ▼
  [5] COMMIT the narrowing              REJECT, log why (patterns.db),
      + record new unsafe-count         move to next candidate
```

**What it reuses (almost everything):**
- `dev.py check` (build + boot + `ORACLE PASS`/`INIT REACHED` gate, dev.py:71-101)
  is the correctness oracle unchanged — every unsafe-reduction is a real boot
  test, exactly as the brief specifies.
- `check_c2rust_output_compiles.py`'s rmeta/extern/target plumbing is the
  compile gate (already solved the hard toolchain problem).
- `patterns.db` holds the metrics/provenance (new `unsafe_metrics` +
  `narrowing_attempts` tables, persistent like `c2rust_attempts`); the TOML
  safe-lift rules 0023 (lock→Guard), 0024 (refcount→Refcount), 0025 (aref
  ownership) are *already authored and deferred* precisely for this stage —
  their `[status]` note says "DEFERRED — Stage-3 safety lift ... done as a
  separate reviewed pass." This pipeline is that pass.
- The tier-2.5 diff-oracle (`diff_oracle.py`) is an *additional* per-change
  semantic gate for pure functions, cheaper than a full boot.

**What is genuinely new:**
- The narrowing *driver loop* (steps 1/2/5): candidate generation, ordering,
  and the accept/reject bookkeeping. Small — it orchestrates existing gates.
- The `unsafe_metrics` signal and its trend over commits (the "is this working"
  measure).
- Glue to invoke `c2rust-analyze --rewrite-in-place` on a single TU and capture
  its proposed diff as one candidate (it emits `===== BEGIN/END =====` markers).

### Realistic first milestone

**Narrow `unsafe` on the 30 already-hand-translated, already-boot-verified TUs
first — not on untested c2rust output.** Rationale, in priority order:

1. Those 30 TUs already pass `dev.py check` (15 KUnit suites, 136 vectors
   green), so the *baseline is known-good* — any regression from a narrowing
   step is unambiguously the narrowing step's fault, not a pre-existing c2rust
   bug. c2rust output, by contrast, mostly doesn't even compile yet (only
   21/228), so it is the wrong place to start a *safety*-narrowing pass.
2. Start with the **lint-driven, mechanical** narrowings (clippy
   machine-applicable: `needless_return`, unnecessary parens/casts) as
   milestone 1a — zero semantic risk, proves the loop end-to-end (measure →
   apply → compile → boot → commit) on a trivial change class.
3. Milestone 1b: apply the **already-authored TOML safe-lift rules** 0023/0024
   to any of the 30 TUs that hold a lock/refcount field matching the rule's
   `[match]` (each rule's `negative` section already encodes the audit
   conditions — e.g. "field accessed outside the lock bracket → do not lift").
   Each lift is one boot-verified commit.
4. Only after the loop is proven on known-good TUs, extend it to consume
   `c2rust-analyze` proposals — and only on c2rust files that *already compile
   and boot*, i.e. downstream of the Section-1 enum/asm fixes.

First milestone success criterion: a measurable, monotonic drop in the
`unsafe_metrics` count across the 30 TUs, with **every** intermediate commit
still passing `dev.py check` — proving the "narrow + reboot-verify" loop works
before any of it touches untested transpiler output.

---

## Top-line verdicts

1. **c2rust/Clang interface: sound — keep it.** c2rust consumes Clang's own
   resolved AST and hand-rolls no type resolution. The bug classes are
   "under-uses Clang's answer, asymmetrically" (#9 enum path lacks the struct
   path's `has_def` gate) plus genuinely-unmapped constructs (RISC-V asm,
   goto). Fixable, not foundational.
2. **KernelIdiomRule: narrow-but-fine now, wrong long-term home.** Unify on the
   existing TOML format as authoring source of truth; keep execution paths
   separate; invest in DB-driven corpus-mining for new idiom candidates.
3. **Pipeline additions: clippy-on-output (empirically 74 warns/file), c2rust
   output through the diff-oracle, and harvesting sparse's 651 `__user`
   findings are the cheap high-value do-soon set; bindgen cross-check and miri
   need their own spikes first.**
4. **Unsafe-to-safe pipeline: buildable and mostly reuse.** `c2rust-analyze`
   (real Polonius rewriter, but prototype-grade and can compile-pass a
   runtime-UB rewrite) is the candidate-generator; the project's QEMU boot
   oracle is exactly the runtime gate it needs. First milestone: narrow unsafe
   on the 30 already-boot-verified hand-translated TUs, lint-driven changes
   first, using the already-authored deferred safe-lift rules 0023-0025.
