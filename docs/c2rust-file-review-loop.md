# c2rust per-file review loop (issue-first, mechanical)

Established 2026-07-20 to stop ad-hoc restarts during issue #47/#44 work.
This is the standard operating loop for each translated file review.

Scope: review one translated TU at a time against the original C source,
record objective evidence, file/fix follow-up issues, and only mark the TU
done when all exit criteria are met.

## Canonical loop

1. Pick next file from the issue checklist
- Source of truth is GitHub issue state (currently #47 master, #44 child).
- Do not start from memory or chat history.

2. Generate fresh c2rust reference output for that C file
- Run `python3 scripts/dev.py c2rust-file-review <c_file>`.
- Confirm status is `OK` and capture output path under
  `tmp/c2rust-reference-check/...`.
- Treat this fresh c2rust output as the source artifact for review.
  Do not start from a previously hand-fixed Rust file.

3. Resolve landed Rust target path mechanically
- Query mapping in `rulesdb/patterns.db` `translated_tus` for `c_file`.
- Do not guess the destination file.

4. Produce and archive normalized diff artifact
- Diff fresh c2rust output vs landed Rust file and save to
  `tmp/<stem>_provenance.diff`.
- Artifact must be reproducible from committed scripts and current tree.

5. Perform semantic C-vs-Rust review (primary check)
- Compare exported/public function surface.
- Compare branch and error-path behavior (including early returns).
- Compare data-flow semantics (signedness, width, wrapping, bounds).
- Compare side effects and ordering at externally visible boundaries.
- Before introducing any new Rust-side helper, wrapper, enum conversion,
  bounded/range type, bitfield helper, or ownership abstraction, check
  existing infrastructure under `linux-riscv/rust/` first.
- Reuse existing `rust/kernel/*` abstractions when semantics match the
  original C contract; if not reused, record why the existing abstraction
  does not preserve the required behavior.
- A landed Rust port should be recreatable from the original C file,
  current c2rust, and generic committed scripts/rules/integration glue.
  File-specific manual edits are evidence the generic process is still
  incomplete.
- Treat "hand-modified vs fresh c2rust" as secondary context only.

6. Classify findings with explicit dispositions
- `No semantic delta found`: mark file review pass.
- `Intentional delta`: require rationale tied to safety/correctness and
  evidence that behavior remains compatible.
- `Unresolved delta or unclear behavior`: create or update a GitHub issue;
  do not hand-wave.
- `Infrastructure failure` (for example `/tmp` write/quota errors, toolchain
  environment breakage): do **not** classify as a file failure; fix infra and
  rerun the same file before assigning verdict.

7. Track unresolved dependencies mechanically
- Any unresolved "not yet translated" note must carry `TODO_LINUX_RS`.
- `dev.py c2rust-file-review` runs TODO sync/check automatically as part of
  the review runner.
- Standing cleanup tracker: issue #48.

8. Close the per-file loop with issue update
- Post a concise issue comment containing:
  - file reviewed,
  - artifact path(s),
  - semantic verdict,
  - follow-up issue links (if any),
  - next file.
- If blocked, comment the blocker immediately and stop that file.

9. Repeat for the next file
- Return to step 1.
- Never restart from scratch; continue from issue checklist state.

## Mechanisation map

This section classifies each loop step by automation potential.

| Step | Mechanisation level | Notes |
|---|---|---|
| 1. Pick next file from issue checklist | Partial | Issue retrieval/order can be scripted; selecting the true "next" item is deterministic only if checklist discipline is strict. |
| 2. Fresh c2rust reference output | Full | Already mechanical via `scripts/c2rust_reference_check.py`. |
| 3. Resolve landed Rust target path | Full | Deterministic DB lookup from `rulesdb/patterns.db`. |
| 4. Produce provenance diff artifact | Full | Deterministic once source and target paths are known. |
| 5. Semantic C-vs-Rust review | Partial | Some checks can be scripted (surface/signatures/symbols), but behavioral equivalence review remains human judgment. |
| 6. Classify findings/disposition | Partial | Template and labels can be scripted; final verdict assignment needs reviewer judgment. |
| 7. Track unresolved dependencies/TODO markers | Full | Already mechanised via sync/check scripts. |
| 8. Issue update | Partial | Body generation and posting are scriptable; reviewer edits/sign-off may remain manual. |
| 9. Repeat loop | Full | Driver script can iterate files until checklist exhaustion or blocker. |

## Command playbook (current manual runner)

Policy: `scripts/dev.py` is the only supported operator entrypoint for this
workflow. Callers should not invoke `scripts/run_c2rust_file_review.py`
directly in normal operation.

Use this sequence for one file review. Replace placeholders in angle brackets.

1. Pick issue context and target file

```bash
gh issue view 47 --repo awto-au/linux-rs
gh issue view 44 --repo awto-au/linux-rs
# choose next <c_file> from unchecked list in #44
```

2. Generate fresh c2rust reference output + review artifacts

```bash
python3 scripts/dev.py c2rust-file-review <c_file>
```

3. Resolve landed Rust path from DB

```bash
sqlite3 rulesdb/patterns.db \
  "SELECT rs_file FROM translated_tus WHERE c_file='<c_file>' LIMIT 1;"
```

4. Build provenance diff artifact

```bash
fresh_rs=$(find tmp/c2rust-reference-check -path "*/$(basename "<c_file>" .c).c/output/src/*.rs" | head -n 1)
landed_rs="linux-riscv/<rs_file_from_db>"
stem="$(basename "<c_file>" .c)"
diff -u "$fresh_rs" "$landed_rs" > "tmp/${stem}_provenance.diff" || true
```

5. Optional mechanical semantic pre-checks (before manual review)

```bash
rg -n "pub unsafe extern \"C\" fn |pub extern \"C\" fn " "$fresh_rs" "$landed_rs"
rg -n "TODO_LINUX_RS|not yet translated" "$landed_rs"
```

6. Enforce unresolved dependency marker policy

```bash
# already executed by dev.py c2rust-file-review
```

7. Draft issue update body and post

```bash
cat > tmp/issue44-file-review.md <<'EOF'
File: <c_file> -> <rust_file>
Fresh c2rust: <ok/fail> (<path>)
Diff artifact: <tmp/..._provenance.diff>
Semantic verdict: <pass | intentional-delta | blocked>
Notes: <1-3 factual bullets>
Follow-ups: <issue links or none>
Next file: <c_file>
EOF

gh issue comment 44 --repo awto-au/linux-rs --body-file tmp/issue44-file-review.md
```

## Command playbook (scripted runner)

Use the runner to automate steps 2/3/4/7 for a batch.

This runner is a living workflow tool, not a one-off helper.
It is expected to evolve as new review checks and issue-work patterns land.

```bash
# explicit files
python3 scripts/dev.py c2rust-file-review lib/base64.c lib/bcd.c

# replay already-reviewed set inferred from tmp/*_provenance.diff
python3 scripts/dev.py c2rust-file-review --from-existing-provenance

# include Rust-side static checks (rustfmt lint + AST-like structural surface diffs)
python3 scripts/dev.py c2rust-file-review --from-existing-provenance --static-checks

# strict mode: static-check findings block the file
python3 scripts/dev.py c2rust-file-review --from-existing-provenance --static-checks --strict-static

# integration phase is default-on (integrate_tu build+boot)
python3 scripts/dev.py c2rust-file-review --from-existing-provenance --static-checks

# opt out of integration phase when needed
python3 scripts/dev.py c2rust-file-review --from-existing-provenance --static-checks --no-integrate

# QEMU phase is default-on and runs only for files that passed prechecks
python3 scripts/dev.py c2rust-file-review --from-existing-provenance --static-checks

# opt out of QEMU phase when needed
python3 scripts/dev.py c2rust-file-review --from-existing-provenance --static-checks --no-qemu-test

# make QEMU phase blocking
python3 scripts/dev.py c2rust-file-review --from-existing-provenance --static-checks --strict-qemu

# override QEMU test command (supports {c_file}, {fresh}, {landed})
python3 scripts/dev.py c2rust-file-review --from-existing-provenance --static-checks \
  --qemu-cmd "python3 scripts/dev.py boot"
```

Strict-mode nuance:
- The runner now tags a small reviewed set of known benign top-level drift as
  `top-fn ignored-benign-*` (for example c2rust scaffolding helpers).
- The runner also tags a curated set of known benign extern signature
  param-shape wrappers as `extern-fn ignored-benign-signature`.
- Formatting drift and pub const/type count drift are informational signals in
  strict mode (visible in summary, non-blocking).
- Blocking findings in strict mode are now focused on actionable structural
  deltas (for example extern signature arity/return mismatch, or non-benign
  top-level missing/extra functions).
- Integration phase is default-on: each passing file runs through
  `scripts/integrate_tu.py --obj <derived .o>` (build + boot oracle), and this
  blocks the file when integration fails; use `--no-integrate` to opt out.
- QEMU phase is a staged gate and is default-on: it runs only after per-file
  prechecks pass; use `--no-qemu-test` to opt out for faster precheck-only runs.
- Integration/QEMU failures caused by environment problems (for example
  `Disk quota exceeded`, temp file write failures) are infra blockers, not
  semantic file failures; rerun after remediation.

Current benign sets (keep small, evidence-based):
- Missing-in-landed: `c2rust_run_static_initializers`, `_printk`, `bunzip2`,
  `do_csum`, `gunzip`, `memcmp`, `simple_strtol`, `simple_strtoull`,
  `sized_strscpy`, `skip_spaces`, `strlen`, `strncmp`, `unlz4`, `unlzma`,
  `unlzo`, `unxz`, `__must_check_overflow`, `_bin2bcd`.
- Extra-in-landed: `is_space`, `ptr_align`, `from64to32`, `get_range`.
- Extern signature param-shape (ignored-benign): `_bcd2bin`, `_bin2bcd`,
  `base64_decode`, `base64_encode`, `csum_partial`, `csum_tcpudp_nofold`,
  `decompress_method`, `find_cpio_data`, `get_option`, `get_options`,
  `ip_compute_csum`, `memparse`, `next_arg`, `parse_option_str`.

Artifacts:
- Log: `tmp/run_c2rust_file_review.log`
- Summary: `tmp/run_c2rust_file_review-summary.md`

Maintenance expectation:
- Keep `scripts/run_c2rust_file_review.py` in sync with this checklist.
- Prefer extending the runner over adding ad-hoc one-off commands.
- Keep `scripts/dev.py c2rust-file-review` as the stable documented entrypoint.
- Document every new automated check here when it is added.

## What can be scripted next

These are the highest-value automations not yet implemented:

1. Single-file runner script
- Input: `<c_file>` and optional `<issue_number>`.
- Executes steps 2/3/4/7 automatically, prints a manual-review checklist for step 5.

2. Issue-driven loop runner
- Reads unchecked files from issue body/checklist, runs until blocked.
- Stops on first `blocked` verdict and posts blocker comment.

3. Semantic pre-check pack
- Function-surface diff (symbol names/signatures).
- Structured alerts for likely semantic hotspots: signed/unsigned conversions,
  early-return changes, reordered side effects around extern calls.

## Exit criteria for "file done"

A file is done only if all are true:

- Fresh reference generation succeeds.
- Provenance diff artifact exists in `tmp/`.
- Semantic review verdict is explicit (pass, intentional delta, or blocked).
- Any unresolved items are tracked by issue reference.
- TODO marker policy checks pass.
- Issue comment is posted with evidence and next step.

## Required artifacts per file

- Fresh c2rust output directory under `tmp/c2rust-reference-check/`.
- One diff artifact under `tmp/*_provenance.diff`.
- One issue comment with verdict and links.

## Anti-patterns (do not do)

- Re-deriving "what to do next" from chat instead of issue checklist.
- Calling a file done without a saved diff artifact.
- Relying on "looks close" without semantic branch/error-path checks.
- Deferring unresolved deltas without opening/updating an issue.
- Manual TODO tracking without sync/check scripts.

## Minimal per-file template

Use this exact shape in issue comments:

```
File: <c_file> -> <rust_file>
Fresh c2rust: <ok/fail> (<path>)
Diff artifact: <tmp/..._provenance.diff>
Semantic verdict: <pass | intentional-delta | blocked>
Notes: <1-3 factual bullets>
Follow-ups: <issue links or none>
Next file: <c_file>
```