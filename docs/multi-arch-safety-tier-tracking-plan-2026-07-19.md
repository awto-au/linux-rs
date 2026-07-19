# Plan: tracking safety tier and arch/endian variants per function/file, and surviving kernel rebases

Status: **planning document, no code written, no schema/Kconfig files modified.**
This resolves an ambiguous verbal proposal ("get an agent to do a plan for new
broader approach to handle a move towards a safe version and also 32/64
little and big endian versions of linux... once an unsafe version of a
function and then a file is delivered attempts a the 3 versions... the data
is too track every function in kernel with conversion state... using the
linux config system but extended to use a data of options") into a concrete
design, grounded in this project's actual current state as of 2026-07-19 (38
hand-translated TUs, 1 arch, `rulesdb/patterns.db` schema at 680 lines).

## 0. Bottom line up front

- **"The 3 versions" = interpretation (a)**: three progressive *safety-tier*
  attempts per function/file (unsafe-baseline → safe-lifted → optimised-if-pure),
  which **already exist** as Phase 2 step 3 and Phase 2.5 in `PLAN.md` — this
  is not new scope, it needs tracking, not invention. The 32/64-bit and
  little/big-endian axis from the proposal is a **second, orthogonal axis**
  (target config), not one of "the 3." See §1 for the full ambiguity
  resolution and why (b)/(c) don't fit the evidence.
- **Kconfig question: hybrid, not either/or.** Real kernel Kconfig
  (`CONFIG_RUST_<slice>`-style, already proven across 5+ landed 8250 slices)
  answers "what can be built into this exact kernel image" and should gate
  **safety-tier selection** the same way it already gates translation
  presence. `rulesdb/patterns.db` answers "what has been attempted and
  verified, with what evidence" and should track **arch/config coverage**,
  because Kconfig has no concept of "verified," only "buildable." See §3.
- **Current state: single-arch, single-config, confirmed.** No rv32, no
  big-endian, no committed x86_64 config exist anywhere in this repo today.
  Building this tracking is greenfield work, not an extension of dormant
  infrastructure. See §2.
- **Extend `file_oracle_status`, don't replace it**: add a `target_id`
  column (FK to a new `translation_targets` table) to the existing
  `(c_file, population, tier)` key, making it `(c_file, population, tier,
  target_id)`. See §2 for why this is additive, not a rewrite.
- **Kernel-version drift**: no existing mechanism detects "this C file
  changed since last verified" — confirmed absent (only `c2rust_rev`/
  `corpus_rev` git-revision strings exist, no content hash). Real gap;
  concrete fix proposed in §4.
- **Pipeline shape**: arch/endian variants are the SAME c2rust translation
  attempted under a different `compile_commands.json` (different
  `-march`/`-mabi`/target-triple flags feeding the SAME AST→Rust logic);
  safe-vs-unsafe is a GENUINELY DIFFERENT translation output (different
  rule set, different target abstractions) that happens to run on top of
  whichever arch/config variant was chosen. These need separate rulesdb
  rows because they answer different questions, even though only one of
  them requires re-running c2rust/hand-translation from scratch. See §5.

---

## 1. Resolving "attempts a the 3 versions"

The raw proposal names two axes in the same breath — "a move towards a safe
version" and "32/64 little and big endian versions" — then says "attempts a
the 3 versions," without saying which axis contributes how many of the 3.
Four candidate readings, evaluated against what this project's own
documents already commit to:

**(a) 3 = safety-tier progression per function** (unsafe-baseline →
safe-lifted → optimised-if-pure), arch/endian handled as a *separate* axis
layered on top. **This is the reading adopted.** Evidence this is what the
project already means by "3 versions" even before this proposal:
`PLAN.md`'s Phase 2 step 3 ("Safe-version attempt using the kernel crate's
rules: for each translated file, attempt a second version...") and Phase
2.5 ("a third variant is permitted... 1. faithful... 2. safe-lifted...
3. optimised") **already define a three-tier per-function progression in
exactly these words**, dated 2026-07-16, three days before this proposal.
The proposal's "once an unsafe version of a function and then a file is
delivered attempts a the 3 versions" reads naturally as: after the
faithful/unsafe baseline lands, attempt the next two tiers PLAN.md already
named. The 32/64-bit and endian language is additional new scope on top of
an existing three-tier idea, not a redefinition of what "3" counts.

**(b) 3 = {32-bit, 64-bit, one endian variant} as the target-config axis,
safety separate.** Rejected as the primary reading because it requires the
proposal's "safe version" clause to be doing no numerical work at all
("get an agent to do a plan for... a safe version and ALSO 32/64 LE/BE...
attempts a the 3 versions" would then mean the safe-version clause
contributes zero to "the 3," which reads as a stretch) — but see below,
this axis is real and needed regardless of which reading of "3" is chosen.
Also weaker on evidence: this project has never at any point named "3"
target configs; `PLAN.md`'s Phase 4 says "second config / second arch...
the second arch is RISC-V rv32" (singular, not three), and endianness has
never been mentioned anywhere in this project's own docs before this
proposal.

**(c) 3 = {unsafe, safe, one specific arch/endian combo} bundled as a single
"done" bar.** Rejected: this conflates two orthogonal questions (translation
approach vs. build target) into one number, which breaks down immediately
once a second arch config is added — is a function now "done" at 4? 6?
Bundling them loses the ability to ask "is this function's *safe* version
verified on rv32" independently of "is its *unsafe* version verified on
rv64," which is exactly the kind of question the proposal's "track every
function... with conversion state" wants answered. The tracking schema in
§2 treats these as two independent dimensions specifically to avoid this
trap.

**(d) Something else, informed by current project state.** Checked and
ruled out: this project has zero rv32 infrastructure, zero big-endian
infrastructure, and no committed x86_64 config (§2) — so there is no
existing "3rd arch" already in flight that the proposal could be pointing
at. Nothing in current state suggests a reading other than (a) plus a new
orthogonal config axis.

**Adopted design**: two independent axes, not one flattened "3":

1. **Safety-tier axis** (what §0/§3/PLAN.md call "the 3 versions"):
   `unsafe-baseline` → `safe-lifted` → `optimised` (pure-leaf only, per
   Phase 2.5's own gate — this tier literally does not apply to most
   functions, by design).
2. **Target-config axis** (the 32/64/endian part of the proposal): an
   open-ended list of `(arch, bits, endian, defconfig)` tuples, starting
   at exactly one row (`riscv64-slim-serial`, the only config that exists
   today) and growing as rv32/big-endian/etc are actually pursued — NOT
   pre-populated with speculative rows for configs nobody has built yet
   (see §2's "greenfield, not dormant" finding — there is nothing to wire
   up, everything here is new).

A function's full state is a **matrix cell**: (safety-tier, target-config),
each cell independently `not_attempted | attempted | verified(tier N)`.
This is what §2's schema change encodes.

---

## 2. Current-state reality check — greenfield, not extension of dormant infra

Direct inspection (file listings, full-repo grep, schema read) plus an
independent subagent sweep, cross-checked, agree on every point:

- **rv32 / any 32-bit infra**: confirmed absent. `configs/` contains exactly
  two files (`riscv64-slim-serial.defconfig`, `initramfs-init.sh`) — no rv32
  variant. The only "rv32" mention anywhere in `configs/ scripts/ rulesdb/
  docs/` is one prose line, `docs/phase2-minimal-target.md:53` ("rv64 vs
  rv32: Cynthion's ECP5 realistically runs a VexRiscv-class rv32 core; rv64
  chosen for now... config is regenerable either way") — a noted-for-later
  open item, not infrastructure. The defconfig itself has no explicit
  `CONFIG_64BIT`/`CONFIG_32BIT` line at all; it inherits riscv's Kconfig
  default (`ARCH_RV64I`, `select 64BIT`) implicitly. Upstream riscv Kconfig
  makes rv32 a second-class, opt-in path in its own right:
  `linux-riscv/arch/riscv/Kconfig:405` — `config ARCH_RV32I` `depends on
  NONPORTABLE` (mainline itself gates rv32 behind an explicit
  "unsupported/non-default" flag).
- **Big-endian / any other arch**: confirmed absent. Zero hits for
  `big.endian|bigendian|BIG_ENDIAN` or `mips|powerpc|s390|sparc|armeb`
  across `configs/ scripts/ rulesdb/ docs/`. Nothing to build on.
- **x86_64**: never a committed config, exactly as the README's "x86_64 is
  the lab" framing implies. `docs/phase0-environment.md` describes it in
  prose only (`defconfig` + `CONFIG_RUST=y`, `olddefconfig` steps written
  as instructions, not a checked-in fragment). No file in `configs/`
  targets it. The only "x86_64" hits in scripts are unrelated (busybox
  binary-architecture mismatch comments in `build_initramfs.py`).
- **`compile_commands.json` generation**: not project-authored at all — the
  actual generator is the vendored upstream kernel tool
  `linux-riscv/scripts/clang-tools/gen_compile_commands.py` (stock
  kernel.org tooling, present verbatim in both `linux/` and `linux-riscv/`).
  Every project script that *consumes* it hardcodes a single path,
  `TREE / "compile_commands.json"`, across at least 8 scripts
  (`fingerprint.py`, `idiom_census.py`, `import_sparse.py`,
  `import_cscope.py`, `run_c2rust_baseline.py`, `build_c2rust_pch.py`,
  `check_c2rust_rule_conformance.py`, `take_progress_snapshot.py`). The
  only "which tree" knob anywhere is `dev.py`'s `LINUXRS_TREE` env var
  (default `linux-riscv`), and `dev.py` hardcodes `ARCH=riscv` directly in
  every `make` invocation (`dev.py:70,175`). There is no `--arch` or
  `--defconfig` flag on any subcommand, anywhere in this project's tooling.
- **Kernel upstream precedent for endian-gated Rust support already
  exists**, independent of this project: `linux-riscv/arch/arm/Kconfig:139`
  — `select HAVE_RUST if CPU_LITTLE_ENDIAN && CPU_32v7 && !KASAN`. Mainline
  Rust-for-Linux already treats endianness as a first-class
  `HAVE_RUST`-gating condition on at least one arch. This is real,
  load-bearing evidence that the Kconfig layer is the *correct* place to
  encode "can Rust even be built for this config" — not a novel idea this
  project would be inventing, but exactly the mechanism upstream already
  uses for the same kind of question this proposal is asking about (see
  §3).
- **rulesdb schema**: `rules` (schema.sql:105), `file_oracle_status`
  (schema.sql:650), `translated_tus` (schema.sql:143), and
  `c2rust_rule_conformance` (schema.sql:525) all key on `c_file` (and for
  `rules`, `rule_id`) with **no arch/config/safety-tier column anywhere**.
  Every table is implicitly scoped to the one riscv64 config this project
  has ever built. This is exactly what makes the extension in this
  section additive rather than a schema rewrite — there is no competing
  dimension to reconcile, only a dimension to add.
- **rule 0026 (`arch-override-dead-generic`) is not multi-arch
  infrastructure** — read in full. It is a single-target (riscv-only)
  rule for detecting when a generic C function is dead code because
  `arch/riscv/include/asm/<header>.h` `#define`s the same name and
  supplies a real riscv implementation (`lib/checksum.c`'s `do_csum`
  overridden by `arch/riscv/lib/csum.c`). Every validation instance and
  every constraint in the rule is riscv-specific. This rule would need to
  be **re-evaluated per additional target arch** if rv32/other arches were
  added (the override might not exist, or might differ, on a different
  arch) — it is evidence *for* needing the tracking this plan proposes,
  not evidence that multi-arch handling already exists.

**Conclusion**: every piece of infrastructure this plan would touch —
config files, compile_commands generation, schema, Kconfig gates — has
exactly one instance today. There is no dormant multi-arch scaffolding to
revive. This changes the practical scope: rv32/big-endian work is new
build-target bring-up (new defconfig, new `compile_commands.json`, likely
new c2rust transpile issues never seen because the corpus has never been
compiled under those flags) BEFORE any tracking-schema question is even
reachable, not a data-model change over an existing multi-target build.

### Schema extension

Add one new table and one new column, both additive:

```
translation_targets(
    id INTEGER PRIMARY KEY,
    arch TEXT NOT NULL,            -- 'riscv', 'riscv32', ... (Kconfig ARCH= value)
    bits INTEGER NOT NULL,         -- 32 | 64
    endian TEXT NOT NULL,          -- 'little' | 'big'
    defconfig TEXT NOT NULL,       -- path under configs/, e.g. 'riscv64-slim-serial'
    is_shipping_target INTEGER NOT NULL DEFAULT 0,  -- 1 for the Cynthion path
    added_at TEXT NOT NULL
)
-- seed row: (riscv, 64, little, riscv64-slim-serial, 1, <today>) — the ONLY
-- row that reflects real, already-built state; every other row is added
-- only when real defconfig/compile_commands work for that target exists,
-- never speculatively pre-populated.
```

`file_oracle_status` gains `target_id INTEGER NOT NULL REFERENCES
translation_targets(id)`, and its `UNIQUE (c_file, population, tier)`
constraint becomes `UNIQUE (c_file, population, tier, target_id)`. Every
existing row backfills to `target_id = 1` (the riscv64-slim-serial seed
row) — a pure default-value migration, not a data reshape, because every
row that exists today genuinely was checked only against that one config.

`rules` gains a **nullable** `safety_tier TEXT CHECK (safety_tier IN
('unsafe-baseline','safe-lifted','optimised') OR safety_tier IS NULL)`
column — nullable because most existing rules (fls-family, likely-unlikely,
etc.) are tier-agnostic mechanical transforms that apply regardless of
which safety tier is being produced; only rules that are *specifically*
about the safe-lift step (0023/0024/0025 — `safe-lift-lock-guard`,
`safe-lift-refcount`, `safe-lift-aref-ownership`, already tagged by name if
not by column) would get a non-null value. This avoids forcing a
tier classification onto rules where it doesn't apply.

A new `file_safety_tier_status` table, structurally parallel to
`file_oracle_status` (same tier/status/detail/evidence_ref/checked_at
shape, PLAN.md's oracle tiers still apply *within* each safety-tier
attempt — a safe-lifted version still needs its own compile/ABI/KUnit/boot
pass) but keyed on `(c_file, safety_tier, target_id, oracle_tier)` instead
of `(c_file, population, tier)`. Kept as a **separate table rather than
folding safety_tier into file_oracle_status's own key**, because
`population` (`landed_tu` vs `c2rust_corpus`) and `safety_tier`
(`unsafe-baseline` vs `safe-lifted` vs `optimised`) answer different
questions — a c2rust-corpus attempt has no meaningful "safe-lifted"
variant today (c2rust only emits unsafe-baseline output; safe-lifting is
presently a hand-translation-only step per PLAN Phase 2 step 3) — and
conflating them into one wide key would create mostly-empty cells for
every c2rust-corpus row. The two tables join on `(c_file, target_id)`.

---

## 3. Kconfig vs rulesdb — hybrid, evidence-based split

The proposal asks whether "the linux config system... extended to use a
data of options" is the right tracking mechanism. Investigated concretely
against the pattern this project has *already proven in production* five
times over (8250 Tier B and Tier C slices):

**What real Kconfig already does here, precisely** (`linux-riscv/drivers/
tty/serial/8250/Kconfig:37-72`): `CONFIG_RUST_8250_STARTUP` and
`CONFIG_RUST_8250_IRQ` are real, working `depends on SERIAL_8250 && RUST`
options that gate whether a *specific translated slice* is wired into the
live `.startup`/`.shutdown`/IRQ call sites, independently of the base
`CONFIG_RUST` gate that already covers Tier A/B translations in the same
file. This is exactly "this translation applies under these conditions,"
already working, already used to A/B two variants (C-path vs Rust-path)
of the *same* driver against each other via a real config toggle. This is
strong, direct precedent that Kconfig is a proven mechanism for "can this
translated slice be built into this exact kernel image, gated on real
kernel-level dependencies (`SERIAL_8250`, `RUST`, and by extension
`HAVE_RUST`/`RUSTC_SUPPORTS_RISCV` for arch gating, `CPU_LITTLE_ENDIAN` for
endian gating per `arch/arm/Kconfig:139`'s existing upstream precedent)."

**What real Kconfig cannot do**, by its own nature: it has no concept of
"verified" versus "merely compiles," no historical record (Kconfig
represents *current* buildable state, not "was this checked against a
KUnit differential three days ago and did it pass"), and no query surface
beyond "is this option set for this build" — nothing like
`file_oracle_summary`'s "highest tier passed" view, nothing like
`c2rust_rule_violations_summary`'s "which rules are broken right now,
ranked by files affected." `rulesdb/patterns.db` already solves exactly
this class of problem for the existing 5-tier oracle, and the schema
extension in §2 is a direct, structurally consistent extension of that
same mechanism to the new safety-tier/target axes.

**Three options evaluated**:

**(a) Extend rulesdb's TOML+SQLite tracking only, no new Kconfig options.**
Would mean safety-tier/arch selection has no real build-time switch — a
"safe-lifted, verified-on-rv32" function would still need *some* mechanism
to actually get built that way, which rulesdb alone doesn't provide (it's
a record of what happened, not a build-system input). Rejected as
incomplete: the proposal explicitly wants configs that can be selected and
built, and rulesdb has no compile-time effect on its own.

**(b) Extend real kernel Kconfig only, no rulesdb involvement.** Would mean
every safety-tier/target combination needs its own `CONFIG_RUST_<slice>_
<TIER>_<ARCH>`-style option, and "what's been attempted and verified, with
what evidence" would have to be reconstructed by scanning Kconfig files
and cross-referencing boot logs by hand — exactly the "scattered across
multiple sources with no single relational answer" problem
`sync_file_oracle_status.py`'s own module docstring already names as the
reason `file_oracle_status` was built in the first place (2026-07-18).
Rejected: this throws away a real, working, already-paid-for piece of
infrastructure to solve a problem it already solves.

**(c) Hybrid — real Kconfig for build-time selection, rulesdb for the
queryable historical/verification record. Recommended.** These are not
competing designs; they answer different questions and the existing
`CONFIG_RUST_8250_STARTUP` precedent already demonstrates the split in
practice: the Kconfig option is what makes the Rust path *buildable and
toggleable*; the boot-transcript comparison, KUnit results, and landing
doc (`docs/8250-tier-c-startup-shutdown-2026-07-18.md`) are what make it
*verified*, and that evidence lives in prose + `HISTORY.md` today
precisely because `file_oracle_status` didn't yet have a slot for
"function-level, not whole-file, verification" — a gap this plan's
`file_safety_tier_status` table (§2) closes, using the *same* evidence_ref
pattern (`file_oracle_status.evidence_ref` already points at "a boot-log
path, a commit hash, a GitHub issue/comment URL" — reuse verbatim).

**Naming convention for new Kconfig options**, extending the proven
pattern: `CONFIG_RUST_<SLICE>` (existing, unsafe-baseline, already the
default meaning of plain `CONFIG_RUST`-gated code) → `CONFIG_RUST_<SLICE>_
SAFE` for a safe-lifted variant, wired as an alternative arm (not
simultaneous — a given kernel image runs one variant of a given function),
`depends on RUST_<SLICE> `. Arch/config selection does not need new
per-slice Kconfig options at all — it's already load-bearing at the
`ARCH=` / `HAVE_RUST` / defconfig level (confirmed real precedent,
`arch/arm/Kconfig:139`'s endian gate); a given kernel image is already
arch/config-specific by construction, so "does this apply on rv32" is
answered by "was this kernel even built with `ARCH=riscv` + `ARCH_RV32I`,"
not by an additional per-function Kconfig knob. Only the safety-tier
axis needs new options, because — unlike arch, which is fixed for an
entire kernel build — safety tier is a per-slice choice within one build
(mirroring exactly how `CONFIG_RUST_8250_STARTUP` already coexists with
plain `CONFIG_RUST` inside one driver).

---

## 4. Surviving kernel version updates — the real, currently-unsolved gap

Confirmed absent by both direct schema read and independent subagent
sweep: no column anywhere carries a content hash, and no script diffs a
translated file's C source against what it looked like at verification
time. `sync_file_oracle_status.py` tracks recency only via `c2rust_rev`/
`corpus_rev` **git revision strings** (which revision of the fork/corpus a
check ran against) and admits directly in its own docstring
(`sync_file_oracle_status.py:15-21`) that tiers 2 and 3 have "no persisted
per-file record anywhere in this codebase" at all — this project's own
tooling already documents that even *current*-kernel state isn't fully
tracked yet, before any rebase question is introduced. This is a real,
distinct problem from the safety-tier/arch tracking above, and needs its
own mechanism:

**Proposed mechanism**: add `c_source_sha256 TEXT NOT NULL` to both
`file_oracle_status` and the new `file_safety_tier_status` table — the
SHA-256 of the exact C source file content at the moment a tier check was
recorded (not the whole TU's preprocessed form, just the literal file
bytes; cheap, deterministic, no libclang dependency for the check itself).
On every kernel-tag bump (`scripts/sync_linux_kernel.py` already exists
for this — its current job is presumably pulling the new pinned tag, not
yet drift detection):

1. For every `c_file` with existing oracle rows, recompute the current
   hash of that file at the new tag.
2. If it matches the stored `c_source_sha256`, the existing verification
   evidence is still valid — no re-check needed, no row changed.
3. If it differs, **do not silently keep the old status**: flag the row
   (`status = 'needs_reverification'`, a new status value alongside the
   existing `pass | fail | not_attempted | not_applicable`) rather than
   either trusting stale evidence or deleting history. The row's evidence
   stays queryable (what did we verify before, against what old hash) —
   `sync_file_oracle_status.py`'s existing "never guess backward, mark
   not_attempted rather than assume" discipline (already the documented
   policy for backfilling tiers 2/3, `sync_file_oracle_status.py:15-21`)
   extends naturally to "never assume forward" across a rebase too.
4. A dashboard view (`docs/status/dashboard.html` already exists and is
   generated from `patterns.db`; extend it, don't build a new one) surfaces
   "N files need re-verification after the vX.Y bump" as its own queue,
   analogous to the existing `c2rust_rule_violations_summary` /
   `work_items_active` "what's actionable right now" views.

This is a bounded, mechanical addition (one column, one new status value,
one new view, a hash-check step added to whatever script currently handles
the kernel-tag bump) — not a new subsystem. It directly closes a gap this
project's own tooling already flagged as unsolved, independent of whether
the safety-tier/arch tracking in §1-3 is pursued at all.

---

## 5. What the pipeline actually needs to run — same translation vs genuinely different translation

Distinguishing, concretely, what varies by *build config* (cheap, same
underlying translation) from what requires a *genuinely separate*
translation attempt (real new rulesdb-tracked output):

**Arch/endian/bit-width variants — same translation, different build
config, IF the C source's behavior doesn't itself branch on the config.**
`c2rust`/hand-translation both consume a `compile_commands.json` whose
per-TU entries already carry the exact `-march`/`-mabi`/`-D__riscv_xlen=`
etc. flags for one arch/config (today, always the one riscv64-slim-serial
config — confirmed in §2, there's no second one to compare against yet).
Running the identical translation logic against a `compile_commands.json`
regenerated for a different `defconfig`/`ARCH=` is mechanically cheap: the
vendored `gen_compile_commands.py` tool already does this per-tree, so a
second target just means a second tree checkout + defconfig + that same
vendored generator run again — no new project code needed for the
generation step itself. What's NOT cheap or automatic: **this project's
own first translated TU (`lib/math/gcd.c`, §"Zbb static-key" in
`docs/phase2-first-translation.md`) is direct, already-proven evidence
that identical C source can have config-dependent *live* code paths**
(`arch/riscv/kernel/setup.c` disables `efficient_ffs_key` when the CPU
lacks the Zbb extension — the QEMU virt target hits the "dead" fallback
path as its actual live path). This means an arch/config variant is not
just "recompile the same AST-shape with different flags" in general — the
oracle (tiers 2-4, KUnit differential and boot) must be **re-run per
target config**, because a rule validated as correct on one config's live
path can be silently exercising a different code path (or none at all) on
another. Rule 0026 (`arch-override-dead-generic`) is the sharper version
of this same risk: a function's C body might not even be *compiled* for a
different arch at all (generic code overridden by an arch-specific
implementation) — this must be re-checked per target arch, not assumed
stable, and the `file_oracle_status`/`file_safety_tier_status` extension
in §2 (keyed per `target_id`) is specifically designed to make "verified
on config X, not yet checked on config Y" a queryable, correct default
rather than an accidental false-positive carry-forward.

**Safe-vs-unsafe — genuinely different translation output, tracked as a
separate rulesdb dimension regardless of build config.** This is not a
compiler-flag difference: PLAN.md's own three-class unsafe policy
(Intrinsically unsafe / Temporarily unsafe / Safe logic) and Phase 2 step
3's "attempt a second version lifted onto Rust-for-Linux `kernel` crate
abstractions (locks→`Guard`, refcounts, `pr_*`)" describe a **different
translation strategy** — different target types, different rule set
(0023/0024/0025's `safe-lift-*` rules specifically, versus the mechanical
rules like 0002/0006/0007 that apply to any tier), producing genuinely
different Rust source, not the same AST run through a different backend
flag. c2rust itself has **no safe-lift capability at all today** — it only
emits Stage-1 literal-unsafe output (confirmed via `docs/python-
transpiler-rewrite-scoping-2026-07-18.md`'s full audit of
`c2rust-transpile`'s 31,600 lines: nothing in its scope touches
`kernel`-crate abstraction selection). This means "safe-lifted" is
presently a **hand-translation-only** lane (consistent with §2's decision
to keep `file_safety_tier_status` separate from the `population` axis
already covering `c2rust_corpus` vs `landed_tu`), and the pipeline
component that "attempts the safe version" is not a c2rust flag but a
distinct translation pass invoked per already-landed unsafe-baseline file,
exactly as Phase 2 step 3 already specifies.

**What this means for "a pipeline that looks at what needs to be done that
supports all 3"**: the work queue is a genuine cross product, but the two
axes have very different attempt costs:

| | cheap to attempt (same logic, new flags) | expensive to attempt (new translation work) |
|---|---|---|
| **safety tier** | — | unsafe→safe-lifted (Phase 2 step 3), safe-lifted→optimised (Phase 2.5, pure-leaf gated) |
| **target config** | re-run oracle tiers 2-4 against a new `compile_commands.json` IF the arch/config already has a working defconfig + boots | stand up a brand-new defconfig + first successful boot (§2: **this is where rv32/big-endian work actually starts** — there is no existing defconfig to reuse) |

A practical work-item generator (extending `sync_work_items.py`'s existing
`work_items` table, not a new mechanism) would query: for every landed
`unsafe-baseline` file at `target_id=1` (today's only real target), is
there a `safe-lifted` attempt recorded? If not, queue it (Phase 2 step 3,
real work, independent of arch). Separately: is there a second `target_id`
row at all? If not, standing one up (new defconfig, new boot) is its own
P-ranked work item, prior to and independent of any per-file translation
question — exactly matching §2's finding that arch bring-up is
greenfield, not a schema question.

---

## What this plan does NOT do

- Does not modify `rulesdb/schema.sql`, any `rulesdb/rules/*.toml` file, or
  any Kconfig file — schema/column changes above are proposed DDL, not
  applied.
- Does not stand up an rv32 or big-endian defconfig, or attempt any build
  under one — §2 establishes this is real, separate, greenfield bring-up
  work, prioritized and scheduled independently of the tracking-schema
  question this plan answers.
- Does not implement the safe-lift translation pass itself (Phase 2 step 3
  remains the owning phase for that work) — this plan defines how its
  results get tracked, not how the lift is performed.
- Does not build the hash-based drift-detection script from §4, define its
  exact CLI, or wire it into `sync_linux_kernel.py` — the column/status/
  view design is specified; the implementation is a follow-up work item.

## Relationship to the exhaustiveness-checking finding

`docs/python-transpiler-rewrite-scoping-2026-07-18.md`'s finding — that
this crate's real historical bugs are dominated by unchecked map/graph
lookups and wildcard-arm matches that already defeat Rust's exhaustiveness
checking, not missing-enum-variant bugs — bears directly on how much
weight the safety-tier axis should carry in the tracking design. It means
the `unsafe-baseline → safe-lifted` step is not primarily a "the compiler
will catch what's wrong" safety net; the compiler already wasn't catching
this project's actual bug class even in the existing all-Rust
`c2rust-transpile` codebase. What safe-lifting actually buys, concretely,
is captured by PLAN's own three-class unsafe policy: replacing raw
pointer/locking code with `kernel`-crate types (`Guard`, `Arc`,
`refcount`) closes the **unchecked-lookup and use-after-free/data-race**
bug classes the scoping doc found dominant — not by adding exhaustiveness
checking (already present and already insufficient on its own), but by
making the specific "dereference a pointer that might not be valid,"
"read a counter without the lock that protects it" shapes fail to compile
at all. This is why `file_safety_tier_status` (§2) records safety tier
**and** oracle tier together per row, rather than treating "safe-lifted"
as a boolean upgrade flag: a safe-lifted function still needs its own
independent tier 2-4 verification, because the safety lift changes *which*
bug classes are structurally prevented, not whether verification is still
required — consistent with this project's standing "the oracle certifies
equivalence, never assumed correctness from representation alone" rule
(README.md, "Translation discipline: faithful, not clever").
