# Parallel work streams

Established 2026-07-18. This project runs (at least) 5 continuous, parallel
work streams rather than one linear task queue. This doc is the durable
definition of what they are; `rulesdb/patterns.db`'s `work_items` table
(synced via `scripts/sync_work_items.py`) is the live, queryable priority
index *within* each stream — query it, don't hand-maintain a duplicate list
here.

Stream management (deciding what's next in each stream, dispatching agents,
verifying and closing out their work, keeping this doc and the DB in sync)
is an ongoing responsibility, not a one-off task — see "Administration" below.

## 1. c2rust-breadth

**Goal:** fix real bugs in the `awtoau/c2rust` transpiler fork, prioritized by
how many corpus files a fix unblocks.

**Where the work lives:** GitHub issues on `awtoau/c2rust`, labeled `P0`-`P4`.
**Where priority comes from:** `files_affected` in the issue body + the real
`rustc --emit=metadata` compile-check (`scripts/check_c2rust_output_compiles.py`).
**Verification gate:** `dev.py c2rust-regress <before> <after>` must show
`VERDICT: OK`, 0 regressed, *and* the real compile-check count must not
regress. Both checked before an issue is closed.
**Current top item:** query `work_items_active WHERE track='c2rust'`.

Landed so far (2026-07-17/18): issues #1, #6, #7, #8, #9, #10 — see
`gh issue list --repo awtoau/c2rust --state closed`.

## 2. c2rust-boot-blocker

**Goal:** get real Rust code (hand-translated or c2rust-transpiled) actually
*executing* in linux-riscv's live boot path — not just compiling in
isolation. A file compiling clean in the standalone `rustc` check
(stream 1's gate) does **not** mean it's wired into the kernel build; this
stream is specifically about closing that gap.

**Where the work lives:** `docs/<topic>-scoping-<date>.md` docs +
`linux-riscv/` commits directly (kernel-tree changes, boot-tested).
**Verification gate:** `dev.py check` — 16/16 (or current count) KUnit
suites, `ORACLE PASS`, `INIT REACHED`, no regression from baseline. This is
the *hardest* gate in the project: a subtly wrong change here can corrupt
the very serial output the test harness reads, so changes are staged
(diff-oracle first, narrow C-ABI-called Rust functions before whole-file
swaps) — see `docs/serial-8250-translation-scoping-2026-07-18.md` and
`docs/hybrid-boot-milestone-2026-07-18.md`.
**Current top item:** query `work_items WHERE track='kernel' AND
blocks_boot_path=1 AND status='open'`, or check the 8250 P2 item (next
slice: `serial_in`/`serial_out` register-access shims) and the tmpfs P3 item
(blocked on upstream VFS abstractions, not a live candidate yet).

Landed so far: TU 31 (`serial8250_compute_lcr` wired into the live 8250
driver — the first non-`lib/` Rust code in the actual boot path).

## 3. hand-translation

**Goal:** the project's original mission — translate `lib/`-style C files to
Rust by hand, one TU at a time, each individually oracle-verified and
boot-verified. High-confidence, slower than the c2rust streams.

**Where the work lives:** `linux-riscv/lib/*_rs.rs` + `bench/diff_*.{c,rs}`
pairs + `patches/*.patch`.
**Where priority comes from:** `dev.py readiness` — ranks untranslated TUs
by how much of their required vocabulary (called functions, statement
shapes) is already covered by landed translations, so each new TU is the
cheapest real next step, not an arbitrary pick.
**Verification gate:** diff-oracle byte-identical (where feasible) +
`dev.py check` full boot pass.
**Current top item:** `dev.py readiness` and take the top row.

Landed so far: 32 TUs (30 original + TU 31 8250-helper + TU 32
`iomem_copy`).

## 4. tooling/infra

**Goal:** the scripts, DB schema, and dashboards that make the other 4
streams observable and administrable — not corpus/translation work itself.

**Where the work lives:** `scripts/*.py`, `rulesdb/schema.sql`,
`docs/status/dashboard.html`.
**Verification gate:** varies by script; DB-schema changes go through
`build_db.py` + a check for the loud `WARNING: persistent data DROPPED`
line (added 2026-07-18 after a real silent-data-loss incident).
**Current top item:** ad hoc — dispatched when a real gap is found (e.g.
the boot-log-history gap found today), not readiness-ranked like streams
1-3.

Landed so far (non-exhaustive — see `git log --oneline` for the full list):
`work_items`/`doc_sources` tables, the live dashboard, `sync_linux_kernel.py`
for tracking upstream, `render_boot_log.py`, boot-history archiving,
parallel-QEMU-boot support, two repo-hygiene passes.

## 5. research

**Goal:** open-ended investigation that doesn't map to a single fix — is the
c2rust/Clang foundation sound, should `KernelIdiomRule` be redesigned, what
test harnesses exist and are worth adopting, is a Rust filesystem realistic
yet, etc. Feeds the other 4 streams with new, evidence-backed candidates
rather than producing commits itself.

**Where the work lives:** `docs/<topic>-<date>.md`, one doc per question.
**Verification gate:** none in the usual sense — the bar is "real evidence
cited, not asserted" (direct code reads, `gh api` queries, sourced web
research), and an honest verdict even when it's "not tractable yet, here's
why."
**Current output:** `docs/research-pipeline-improvements-2026-07-18.md`,
`docs/kernel-test-harness-research-2026-07-18.md`,
`docs/tmpfs-rust-scoping-2026-07-18.md`.

## Administration

Keeping these streams moving is an ongoing responsibility:

1. **Dispatch**: when a stream's current top item is clear (readiness
   ranking, `work_items_active`, or a research doc's stated next step),
   dispatch an agent with a self-contained brief citing the exact file/issue/
   commit context — not a vague "keep going."
2. **Verify before trusting**: every fix gets its own regression check
   (`c2rust-regress`, `dev.py check`, or the diff-oracle) run independently,
   not just accepted from an agent's own report — this project has caught
   real mistakes (stale binaries, mislabeled DB revisions, agents stalling
   before their final close-out step) by re-checking rather than trusting.
3. **Sync after every landing**: `crawl_c2rust_upstream.py --repo
   awtoau/c2rust --issues-only` → `sync_work_items.py` →
   `generate_dashboard.py`, so the DB and dashboard never drift from GitHub's
   real state.
4. **Merge worktrees promptly**: agents doing risky/large work run in
   isolated `git worktree`s — merge and delete them as soon as verified, so
   `git worktree list` stays a true "what's live right now" signal (the
   dashboard's "active workers" panel reads this directly).
5. **Re-rank, don't just append**: when a stream's top item closes, check
   what's actually next (`work_items_active` re-sorts automatically) rather
   than assuming yesterday's second-priority item is still second.
