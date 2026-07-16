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

## Usage

```
dev.py db                          # rebuild
dev.py q stats                     # summary counts
dev.py q rule spin_lock            # which rule(s) mention spin_lock
dev.py q callers spin_lock         # which functions call spin_lock, sample
dev.py q uncovered 20              # top-20 hot uncovered statement families
dev.py q sql "SELECT ..."          # raw SQL, table-printed
```

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
