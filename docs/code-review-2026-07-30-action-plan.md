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

## Debris candidates — needs your call

Two verification passes agreed the first two below are genuinely dead
(candidates for archiving, same treatment as `add_spdx.py`); the rest
were flagged by only one pass and need a real decision, not a guess:

**Confirmed by both passes, not yet archived:**
- `scripts/ollama_queue_watch.py` — built to serve "Standing Orders #1"
  in `docs/streams.md`, but that doc never actually names the script,
  and nothing automated invokes it. *Possibly still-intended standing
  tooling you run manually* — before archiving, confirm whether you
  actually run this periodically.
- `scripts/run_c2rust_pch_compare.py` — standalone investigation script
  for a historical PCH/Clang-ABI bug (`awtoau/c2rust#1`/`#2`). Zero
  callers. Worth checking those issues are genuinely closed/stable
  before archiving.

**Flagged by only one pass — unresolved, pick a side:**
- `scripts/c2rust_baseline_watch.py` — real, correct, cron-oriented, but
  never actually wired into cron/systemd/`dev.py`.
- `scripts/plot_session_progress.py` — has the real hardcoded-date bug
  above, plus only one historical doc citation, no live caller.
- `scripts/region_report.py` — zero callers per the original pass; not
  independently re-confirmed.
- `scripts/target_compile_test.py` — zero callers per the original pass,
  plus the un-consolidated `diff_oracle.py` duplication above; not
  independently re-confirmed.
- `scripts/compose_census.py` / `scripts/idiom_census.py` — flagged as
  "completed one-time investigation, findings already captured in docs"
  by one pass; neither pass called this definitive.

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

## Suggested order of operations

1. **This doc's "needs your call" debris items** — quick decisions,
   unblocks a small cleanup.
2. **`build_db.py`'s WAL/SHM fix** — cheapest, prevents a real recurring
   failure mode this project has already hit once.
3. **`boot_qemu.py`'s two bugs** — the log-clobbering one is a real data-
   loss risk for the `#28`/`#44` review work currently in flight.
4. **The rest of the per-script bug list** — no urgency, batch whenever
   convenient.
5. **The style sweep** — lowest priority, cosmetic/consistency only.
