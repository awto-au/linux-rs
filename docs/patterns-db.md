# patterns.db — the SQLite pattern database

Added 2026-07-16, from the original project proposal ("the database
becomes the real product") plus Dan's request to actually build it: a
queryable SQLite index over the rule DB + Phase 1 kernel census +
current translation status, for quick relational checks that would
otherwise mean grepping across three different data sources by hand.

## It is ephemeral — rebuild, don't migrate

`rulesdb/patterns.db` is **derived and gitignored**, per the project's
own "derived artifacts have no legacy" rule: it is regenerated from
source (`rulesdb/rules/*.toml`, `tmp/functions.jsonl`,
`tmp/region_census.pkl`, `linux-riscv/lib/**/*_rs.rs` + git log) every
time `scripts/build_db.py` (`dev.py db`) runs. Never hand-edit it, never
commit it, never write a migration for a schema change — change
`rulesdb/schema.sql` and rebuild. If the census pickles are missing, the
build logs a warning and continues with just the rule DB (never fails
silently on a partial import).

## Schema shape

Two data classes, explicitly cross-referenced both directions:

- **Rules** (`rules`, `rule_constraints`, `rule_negatives`,
  `rule_evidence`, `rule_validation_instances`) — the authored TOMLs,
  fully normalised so e.g. "which rules mention `spin_lock`" is one query.
- **Kernel census** (`functions`, `callees`, `statement_families`) — the
  Phase 1 fingerprint/clustering data, previously only reachable via
  Python pickles in `tmp/`.
- **Translation status** (`translated_tus`) — derived from the kernel
  worktree's `*_rs.rs` files + `git log`, so "is this already translated"
  is a join, not a manual check.

Reverse-checkable views (the actual point, per Dan: "everything should be
relational and reverse check where it matters"):

- `functions_with_status` — every census function, joined to whether its
  own source file has a landed translation.
- `call_edges` — caller → definition site(s), for non-static callee
  names. **Static names are deliberately excluded from auto-resolution**
  (a `static` function name can be reused across TUs — silently picking
  one definition site would be a wrong answer wearing a right answer's
  clothes; disambiguate by file yourself when a name is static).
- `tu_dependency_status` — for a translated TU, which of its callees are
  themselves already translated vs still C-only. This is a **real**
  dependency check (follows actual call edges), unlike
  `scripts/readiness.py`'s vocabulary-overlap heuristic (which measures
  AST-token similarity, not actual call-graph readiness) — the two serve
  different purposes and both stay.
- `uncovered_hot_families` — statement families with many instances and
  no matching rule (rough `LIKE` match on `match_family`; a real matcher
  is `scripts/offload_translate.py`'s job, this view is for eyeballing).

## cscope integration (closes the static-name gap)

`scripts/import_cscope.py` (`dev.py db` runs it automatically after
`build_db.py`) builds `tmp/cscope.out` over the same 2,996-TU pinned
corpus as the census, then resolves exactly the gap `call_edges`
documents: **static function name collisions across TUs**, which an
AST fingerprinter has no whole-program knowledge to disambiguate.

Scoped for real, not exhaustively: of 55,612 static function names in
the corpus, only **471** are shared by more than one TU — those are the
only ones where cscope's answer is new information over the census
(seeded directly, no query needed, for the other 55,141 unique ones).
Full-corpus querying was measured at ~52 minutes (every static name) or
~79 minutes (every function); the properly-scoped version runs in
**~26 seconds**.

New table `cscope_symbols` (raw refs) and view `cscope_call_edges`
(caller→resolved-definition-site, with a `definition_ambiguous` flag
when a name still has >1 candidate definition even after resolution —
surfaced honestly, never silently guessed).

Real example (verified 2026-07-16): `skip_atoi` is defined in three
different files (`arch/x86/boot/printf.c`, `lib/vsprintf.c`,
`drivers/firmware/efi/libstub/vsprintf.c`) — `cscope_call_edges` reports
all three with `definition_ambiguous=1` rather than picking one; a
caller in `arch/x86/boot/printf.c` resolves via file-scope match if you
filter further, but the view itself does not silently do that filtering.

## sparse (locally built, not vendored)

`sparse` 0.6.4 (Fedora's latest available package) cannot parse this
kernel version's headers at all — rejects `__typeof_unqual__`, a builtin
`include/asm-generic/rwonce.h` and several `atomic.h`/`list.h` headers
now require. Genuine tool/kernel-version incompatibility (the kernel's
own `make C=1` checker-valid gate independently rejects the same binary
for the same reason), not a flags-passing bug.

**Fixed by building from upstream instead of the Fedora package.**
Mirrored the canonical repo locally (`/mnt/2tb/git_mirror/sparse/`, from
`git.kernel.org/pub/scm/devel/sparse/sparse.git`); its Dec 2025 HEAD
(`3715683`) has `__typeof_unqual__` support and parses this corpus
cleanly. `scripts/import_sparse.py` (`dev.py db` runs it) clones+builds
sparse from the LOCAL mirror into `tmp/sparse-build/` every run (a few
seconds once the mirror is cloned) — **the compiled binary is never
committed or vendored into this repo** (Dan's explicit call, 2026-07-16):
only the build recipe is checked in, the binary itself is gitignored
scratch, same as any other `tmp/` artifact.

Full-corpus run: 2,998 TUs, **214,271 diagnostics, 0 timeouts, ~3.5
minutes.**

**Honest read of the data — mostly noise, with one genuinely valuable
vein.** 214,043 of 214,271 rows are `warning` severity, and 79% of ALL
rows are a single message (`mixing declarations and code`, a C89-style
check this codebase doesn't care about); a further large chunk
(`non-constant initializer for static object`) looks like a false
positive this sparse build emits on plain constant-expression
initializers — do not treat the raw table as a bug list. The genuinely
useful subset is **651 rows** of `__user`/address-space misuse
(`cast removes address space '__user'`, `incorrect type in argument —
different address spaces`) — exactly the kernel-specific semantic class
(no generic tool, including everything else in this DB, models
`__user`/`__iomem` crossing at all) the original tool survey predicted
sparse would add. **Query `sparse_address_space_findings`, not
`sparse_diagnostics` directly**, for anything meant to inform a
translation decision — directly relevant to rule 0015
(`USERSPACE_TYPED_COPY`) if a future TU handles raw `__user` pointers.

## Usage

```
dev.py db                          # rebuild everything: rules+census, cscope, sparse
dev.py q stats                     # summary counts
dev.py q rule spin_lock            # which rule(s) mention spin_lock
dev.py q callers spin_lock         # which functions call spin_lock, sample
dev.py q uncovered 20              # top-20 hot uncovered statement families
dev.py q sql "SELECT ..."          # raw SQL, table-printed
dev.py q sql "SELECT * FROM sparse_address_space_findings LIMIT 20"
dev.py q sql "SELECT * FROM cscope_call_edges WHERE definition_ambiguous=1 LIMIT 20"
```

`dev.py db` takes ~4 minutes total (build_db ~2s, cscope ~30s, sparse
~3.5min — sparse's first run also clones+builds the compiler, add ~10s).
Rebuild whenever the census, rules, or translated-TU set changes; the
cscope/sparse DBs are cached under `tmp/` between runs (`--rebuild` flags
on the individual scripts to force a refresh).

Direct sqlite3 also works for anything not wrapped: `sqlite3
rulesdb/patterns.db` after a build.

## What this replaces vs. what it doesn't

Replaces: ad-hoc grepping across `rulesdb/rules/*.toml` +
`linux-riscv/lib/**/*_rs.rs` + census pickles for any question that's
inherently relational ("which rule covers X", "is X translated", "what
does X call that isn't translated yet").

Does not replace: `scripts/readiness.py` (a different, heuristic
vocabulary-overlap ranking used to pick the next TU — still useful,
still separate), `scripts/fingerprint.py`/`region_census.py` (the
census's actual source of truth — the DB only imports their output), or
plain grep for "find this exact string" (still the right tool for that).
