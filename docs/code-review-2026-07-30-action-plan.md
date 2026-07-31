# Full code review action plan — 2026-07-30

Source: a Workflow-orchestrated review of all 56 `scripts/*.py` files +
`rulesdb/schema.sql`, plus a reproducibility test re-running
`c2rust_reference_check.py` fresh against the same 34-file corpus 10 days
apart. Debris-candidate and PERSISTENT_TABLES claims were independently
re-verified by a second agent before being trusted.

## Already actioned (commit `12cad18`)

- Archived `scripts/add_spdx.py`, `scripts/diff_c2rust_comments.py` to
  `debris/code/` — both one-shot scripts for completed work, zero callers
  confirmed by two independent passes.
- Fixed `scripts/sync_file_oracle_status.py`'s `MANUAL_TIER4_EVIDENCE`:
  was writing a dangling doc path into `patterns.db` on every run (the
  8250 tier-c docs it pointed at were archived to
  `debris/docs/8250-deferred-20260720/` earlier in the same session).
- Fixed the same dangling paths in `scripts/check_spdx_provenance.py`'s
  `EXCEPTIONS` comments (cosmetic, but misleading).
- Filed `awtoau/c2rust#31`: a real translator nondeterminism bug —
  `HashMap<CTypeId, CType>` iteration order (`c2rust-transpile/src/c_ast/mod.rs:78`)
  flips one cast-elision decision ~50/50 across repeated runs on
  `lib/string.c`. Semantically harmless in this instance, but breaks
  reproducibility guarantees as a mechanism.

## Reproducibility test result (headline finding)

**32/34 files byte-identical, 1/34 differs (the nondeterminism bug above,
filed), 1/34 had no prior baseline to compare (`lib/base64.c`, never
successfully transpiled before this run — new data, not a discrepancy).**

Conclusion: the linux-rs pipeline itself (worktree state,
`compile_commands.json` recovery, script logic) is fully deterministic
across a 10-day gap. The one real difference is a c2rust bug, not a
linux-rs bug.

## Not yet actioned — real bugs, by script (with line numbers)

Pick off individually; none are urgent/blocking, all are real.

- **`scripts/build_db.py:243`** — `DB.unlink(missing_ok=True)` doesn't
  remove `-wal`/`-shm` sidecars despite `PRAGMA journal_mode=WAL`. A
  crashed prior run can leave stale WAL/SHM files that reattach to the
  freshly recreated DB — matches an already-observed "database disk
  image is malformed" incident (script's own comment, lines 298-301).
  **Fix: unlink the sidecars too.**
- **`scripts/check_c2rust_output_compiles.py:56`** — module-level
  `C2RUST_REV = current_c2rust_rev()` runs at import time, `check=True`,
  and is never referenced again (dead, `main()` recomputes its own rev
  differently at line 295). Crashes ugly at import if `C2RUST_SRC` is
  broken instead of the friendly fallback path the rest of the script
  offers. Also missing the "file vanished during a concurrent baseline
  run" race handling that its sibling `check_c2rust_output_clippy.py`
  already fixed — a regression that didn't get ported over.
- **`scripts/boot_qemu.py:92`** — archive filename timestamp has no
  sub-second/PID disambiguator; two boots finishing within the same
  wall-clock second under the same `--run-id` silently clobber a
  previously-archived log, contradicting `archive_boot()`'s own "never
  overwritten" docstring guarantee.
- **`scripts/boot_qemu.py:138-140`** — `git diff --cached --quiet` call
  only guards `CalledProcessError`; other exceptions (e.g.
  `FileNotFoundError` if git isn't on PATH) propagate uncaught instead of
  degrading to the documented "warn, don't fail the boot" behavior.
- **`scripts/combined_boot_scaffold.py:213-216`** —
  `has_init_marked_fn_heuristic` is supposed to check for real
  `__init`/`#[link_section]` presence but is actually a whole-file
  case-insensitive substring test for `"init"` — near-vacuous, almost
  any `.rs` file passes regardless of real content.
- **`scripts/combined_boot_scaffold.py:224`** — dead code: `import os`
  locally, then uses `__import__("os").environ` on the next line instead
  of the import that was just made.
- **`scripts/crawl_c2rust_upstream.py:93,108`** — hardcoded
  `upstream_default = "master"`; if a fork's real default branch is ever
  renamed, `ahead_by` comparisons silently fail into a `-1` sentinel
  indistinguishable from a genuinely dead fork.
- **`scripts/offload_cycle.py:69`** — `clippy_check()`'s `has_errors`
  treats any `"warning:"` substring as a hard failure, contradicting the
  fix already made for the identical problem in
  `check_c2rust_output_clippy.py`. Can send a clean draft into an
  unproductive retry loop.
- **`scripts/fingerprint.py:104`** — `os.chdir()` inside a function run
  via `mp.Pool.imap_unordered`, mutating long-lived worker cwd state with
  no restore. Currently harmless (all corpus entries share one
  `directory`), but a landmine if that ever changes; also looks
  unnecessary since `index.parse(src, ...)` already takes `src` directly.
- **`scripts/plot_session_progress.py:167`** — chart title hardcodes the
  literal string `"2026-07-17/18"` instead of deriving it from the
  queried data; any future re-run mislabels the chart.
- **`scripts/plot_session_progress.py:84`** — parses another script's
  `str(dict)`-repr via naive quote-replacement into JSON; breaks if any
  label ever contains an apostrophe.
- **`scripts/query_db.py`'s `sql` subcommand (94-101)** — executes
  arbitrary raw SQL including `DELETE`/`DROP` with no read-only guard,
  despite the module's own docstring framing it as "quick checks."
- **`scripts/readiness.py:57`** — silently returns the full unmodified
  path if the tree-name string doesn't appear where expected, with no
  logged warning — that entry silently never matches `translated`.
- **`scripts/target_compile_test.py`** — no subprocess timeout (unlike
  its stated model `diff_oracle.py`), so a QEMU hang blocks forever with
  no bound. Also doesn't actually import/call `diff_oracle.py` despite
  its own docstring's explicit claim that doing so avoids logic drift —
  hand-copies the logic instead, inheriting an unverified assumption.
- **`scripts/integrate_tu.py`** — docstring documents a `--patch N` flag
  that doesn't exist in the actual code (dead/aspirational docs — the
  real patch step is `dev.py patch N`). Also: `patch_bindings()`'s
  `max()` over an empty generator raises unhandled `ValueError` for any
  header prefix other than `linux/`.
- **`scripts/diff_c2rust_comments.py`** — now archived, see above; the
  `*`-continuation heuristic collided with Rust's pointer-dereference
  syntax, a real (not hypothetical) failure mode for this corpus. Kept
  here as a note in case the archived script is ever revived.

## Style inconsistencies worth a single sweep (not urgent)

- **Logging convention split**: ~51/57 scripts use
  `logging.basicConfig` + dual `FileHandler(tmp/<name>.log)` +
  `StreamHandler(sys.stdout)`. `boot_qemu.py` and `query_db.py` use bare
  `print()` with no `tmp/` log at all.
- **Mid-function imports** that could be top-level: `build_c2rust_pch.py`
  (`defaultdict`), `build_initramfs.py` (`os`), `import_sparse.py`
  (redundant `__import__("os")` despite top-level `os` already imported),
  `c2rust_regression_check.py` (`sqlite3`), `check_c2rust_output_clippy.py`/
  `check_c2rust_output_compiles.py` (`Counter`).
- **Duplicated helper logic** across script pairs (works today, but
  copy-instead-of-import): `find_clean_outputs`/`build_support_crates`/
  `git_rev` between the two `check_c2rust_output_*.py` scripts;
  `normalize_path()` between `import_cscope.py`/`import_sparse.py`;
  Ollama HTTP-call boilerplate across all 4 `offload_*.py` scripts;
  `rustc_check()`/`clippy_check()` between `offload_translate.py`/
  `offload_cycle.py`.

## Debris candidates — resolved 2026-07-31

- `scripts/ollama_queue_watch.py` — **kept.** Confirmed still in active
  use (Standing Orders #1's discovery half); `docs/streams.md` now
  explicitly names it.
- `scripts/run_c2rust_pch_compare.py` — **archived.** `awtoau/c2rust#1`
  (the bug it diagnoses) is closed; `#2` is unrelated shared context,
  not a blocker. Archived with an issue-reference header.
- `scripts/c2rust_baseline_watch.py` — **kept, not archived.**
  `docs/streams.md` already documents this as a deliberate standing
  TODO ("not yet wired to a real cron entry... holding off for now...
  not abandoned") — an intentional decision already recorded, not an
  oversight.
- `scripts/plot_session_progress.py` — **kept.** Real, working script;
  the hardcoded-date and dict-repr-parsing bugs above were fixed
  (`db3584d`), not archived.
- `scripts/region_report.py` — **kept.** Ran it for real against a
  freshly-reloaded census (`rulesdb/patterns.db`'s `functions`/
  `statement_families`, see #53) — it's the genuine, working report
  generator `region_census.py`'s own docstring points to, just hadn't
  been run in a while. Not dead code; a manually-run report tool, same
  category as `report.py`.
- `scripts/target_compile_test.py` — **kept.** Real Tier-2.5b riscv64-
  emulated oracle; bug-fixed (`0fceead`), not archived.
- `scripts/compose_census.py` / `scripts/idiom_census.py` — **archived.**
  Both are one-time investigations against the fixed 2026-07-16 corpus
  snapshot, findings fully captured in `docs/phase1-census-v2-composition.md`
  / `docs/phase0-evals.md` respectively, zero live callers. Archived
  with issue-reference headers (see #53).

## DB schema — no action needed

Both passes independently agreed: **no genuine `PERSISTENT_TABLES` gaps.**
Every table absent from that list (`functions`, `callees`,
`statement_families`, `cscope_symbols`, `sparse_diagnostics`, `rules` and
its child tables, `translated_tus`) is fully re-derivable from an external
source (kernel source, git log, `rulesdb/rules/*.toml`, or a dedicated
wipe-and-rebuild importer).

One separate, minor schema-hygiene note: `c2rust_fix_patterns` is listed
in `PERSISTENT_TABLES` and documented as hand-curated, but has zero
`INSERT` anywhere in the repo — permanently empty. Also: inconsistent
column naming (`rust_file` vs. `rs_file`; bare `file` vs. `c_file`) and
missing indexes on `rule_id` in four `rule_*` child tables.

## Archival convention (added 2026-07-31)

Every script moved to `debris/code/` gets an `# ARCHIVED <date>: ...` header
added at archive time, naming the issue(s) it relates to (this repo's or
`awtoau/c2rust`'s) and why it's safe to retire — some of these issues may
go upstream later, so the reference needs to survive outside this doc, not
just live in a commit message. Retrofitted onto the three scripts archived
so far (`add_spdx.py` → `awto-au/linux-rs#1`, `diff_c2rust_comments.py` →
`awtoau/c2rust#4`, `run_c2rust_pch_compare.py` → `awtoau/c2rust#1`/`#2`).

## Suggested order of operations — all done, 2026-07-31

1. ~~This doc's "needs your call" debris items~~ — done, see above.
2. ~~`build_db.py`'s WAL/SHM fix~~ — done (`24a7e0d`), verified with a
   simulated crash-leftover scenario.
3. ~~`boot_qemu.py`'s two bugs~~ — done (`0a214b8`).
4. ~~The rest of the per-script bug list~~ — done: `check_c2rust_output_compiles.py`
   (`026995b`), `combined_boot_scaffold.py` (`27eb81e`), `crawl_c2rust_upstream.py`
   (`5d41273`), `offload_cycle.py` (`9b5bc3b`), `fingerprint.py`/`region_census.py`
   (`eb9b6ce`), `plot_session_progress.py` (`db3584d`), `query_db.py`
   (`2de5437`), `readiness.py` (`f6e2ffd`), `target_compile_test.py`
   (`0fceead`), `integrate_tu.py` (`df3ece6`).
5. ~~The style sweep~~ — done: mid-function imports (`101023e`), logging
   convention (`00a28c0`), duplicated-helper-logic extraction
   (`6152e81`, `28614dd`, `6d914ca`).

Separately, this review's investigation led to filing and structurally
fixing a real, more significant finding — the rule-learning track's
Phase-1 census gate wasn't wired into the queryable DB — see
[awto-au/linux-rs#53](https://github.com/awto-au/linux-rs/issues/53) and
`scripts/check_census_gate.py` (`0d413b6`).
