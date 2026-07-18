# Parallel work streams

Established 2026-07-18. This project runs (at least) 6 continuous, parallel
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

**Ollama offload for small/mechanical fixes (2026-07-18):** local coder
models (via Ollama, this machine's RTX 5060 Ti) are a viable *first-drafter*
for well-scoped, single-root-cause c2rust bugs — but ONLY behind a
multi-stage gate. Confirmed by real testing this session (not synthetic):
"compiles clean" is necessary, never sufficient — a model will silently
substitute a simpler/wrong implementation that still passes rustc+clippy.
Required stages, none optional:
1. Draft with a local coder model (tested: `qwen2.5-coder:14b`; this host
   also has `qwen2.5-coder:32b`, `deepseek-coder:33b`, `codellama:34b` for
   harder cases).
2. Free compiler-retry gate: rustc + clippy, feed the exact diagnostic back
   for a fix-only retry, repeat up to N rounds (`scripts/offload_cycle.py`
   pattern). This reliably converges to *compiling* code.
3. Real diff-oracle or independent content review before trusting the
   result — the gap free compiler gates cannot close. A rustc/clippy pass
   is not evidence of correctness; report pass-rate and correctness-rate as
   separate numbers.
4. Only escalate to a paid/independent model review once free gates pass.

**Attribution, mandatory (2026-07-18):** any commit whose draft came from
an Ollama-run model must credit it — a trailer line naming the tool and
model (e.g. `Drafted-By: Ollama (qwen2.5-coder:14b)`), in addition to the
usual `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` for the
agent that supervised/verified/finished the fix. Both are real
contributors to the result and both get named — see README.md's
"Tooling & credits" section for the standing project-level credit
(Ollama itself, the specific model projects used). Don't let "the agent
committed it" erase that a local model produced the first draft.

**Standing policy (2026-07-18): keep Ollama fed off the open c2rust-breadth
queue, not just a one-off.** Every open `awtoau/c2rust` issue that is a real
bug with a fix to draft (small, single-root-cause, mechanical-shaped) gets
dispatched through the gate above as agents free up — don't let idle local
GPU capacity sit unused while the queue has candidates. NOT every open issue
qualifies: P4-labeled items in this tracker are so far all investigations /
known-limitations already closed out with a negative or informational
result (see #2 concurrency scaling, #3 speedup investigation, #4
locate_comments profiling, #5 stability-exclusion limitation) — there is no
fix to draft for those, so don't dispatch Ollama at them; they stay P4/open
as documented findings, not fix targets. Re-check this list's shape each
time a new issue lands, since "investigation vs. fixable bug" isn't a
priority-label distinction (fixable bugs span the same P0-P4 range) and has
to be judged from the issue body each time.

Dispatched so far: #11 (goto-to-labeled-block lowering, P2, 6/228 files —
two code paths disagree on a synthesized label's name), #12 (`convert_warn_on`
emits a stray `as c_int` cast against a `bool`-typed macro parameter, P3,
1/228 files but recurs at every call site in the affected file). Both are
the right shape: one clearly-stated root cause, no cross-cutting ambiguity.

**Workspace isolation, mandatory (2026-07-18):** `awtoau/c2rust` lives
outside this repo (`/mnt/2tb/git/github.com/awtoau/c2rust`) — the Agent
tool's `isolation: "worktree"` option only isolates linux-rs itself, so
an agent told to `cd` into the c2rust checkout gets NO isolation there.
Found the hard way: two agents fixing #11 and #12 concurrently both
worked directly in the one shared checkout and collided (a branch switch
left the other agent's staged-but-uncommitted changes stranded on the
wrong branch; one agent improvised its own ad-hoc clone mid-task trying
to self-recover, which happened to contain a real, correct fix but was
unmanaged and nearly got lost). Every future c2rust-track agent MUST be
given its own worktree via `scripts/c2rust_worktree.py create <name>`
(creates `awtoau/c2rust-worktrees/<name>/` on branch `agent-<name>`,
sibling to the main checkout, never mixed into `awtoau/`'s other
unrelated repos) — never `cd` an agent straight into the shared
`awtoau/c2rust` checkout, and never let an agent improvise its own
clone/worktree by hand. `c2rust_worktree.py list` shows every worktree
and flags anything not under the managed dir as stray; `remove <name>
[--delete-branch]` cleans up after a fix lands.

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

## 3. hybrid-boot-backwards

**Goal:** the inverse strategy to stream 2. Stream 2 works *forward* — take
a compile-clean file, try to wire it into the live build. This stream works
*backwards* — start from a known-good, minimal, boot-to-**interactive
console** baseline (zero Rust-conversion content beyond what's already
boot-verified) and add landed translations back in one at a time, with a
full `dev.py check` pass after each addition, so there's always a verified
checkpoint to roll back to. Same end goal as stream 2 (real Rust executing
in the live boot path), different, more conservative methodology — run
both, since a forward compile-clean candidate and a backward known-good
checkpoint are complementary evidence, not redundant.

**Why this is a distinct stream, not a duplicate of stream 2:** stream 2
answers "does this file work if I add it". This stream answers "what's the
smallest thing that's *definitely* correct, and what's the very next safe
step from there" — it establishes the baseline stream 2's candidates get
layered onto, and catches regressions stream 2 might not (a forward
candidate can compile clean and boot-pass in isolation while still being
wrong in combination with something else already landed; working backwards
from a real checkpoint after every single addition is what catches that).

**First concrete gap found (2026-07-18):** the current minimal initramfs
`/init` (`configs/initramfs-init.sh`) never drops to an interactive shell —
it mounts devtmpfs/proc/sys, prints the `INIT REACHED` milestone, and
immediately powers off (`busybox poweroff -f`). There is no way to actually
sit at a console and poke at the running kernel today. That's the real
stream-0 baseline this stream needs before "add translations back in" means
anything: a genuinely minimal kernel (matching today's exact boot-verified
config, no Rust beyond what's already landed) that boots to a live `sh`
prompt on the serial console instead of powering off.

**Where the work lives:** `configs/initramfs-init.sh` + `linux-riscv/`
commits, staged as a sequence of individually-tagged, individually-verified
checkpoints (e.g. git tags or a checkpoint log) rather than one big change.
**Verification gate:** same as stream 2 (`dev.py check`, 16/16 suites,
`ORACLE PASS`, `INIT REACHED`) plus confirming a real shell prompt string
appears in the boot log.

**Deliberately sequenced, not bundled** (2026-07-18): re-running a KUnit
suite from the live console (via debugfs, `/sys/kernel/debug/kunit/
<suite>/run`, if `CONFIG_KUNIT_DEBUGFS` is enabled) as stronger, load-bearing
evidence the console is genuinely interactive is a REAL next step, but is
explicitly deferred to its own follow-up task once the console milestone
itself is landed and verified — get linux booting to a working console
first, add the in-kernel test-running capability as a later, separate step.
Don't attempt both in one pass.

**Current top item:** get the interactive-console milestone landed (just
the shell prompt, nothing else) — that's the only thing blocking this
stream right now.

## 4. hand-translation

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

## 5. tooling/infra

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

## 6. research

**Goal:** open-ended investigation that doesn't map to a single fix — is the
c2rust/Clang foundation sound, should `KernelIdiomRule` be redesigned, what
test harnesses exist and are worth adopting, is a Rust filesystem realistic
yet, where can `linux-rs` contribute back to Rust-for-Linux upstream, etc.
Feeds the other 4 streams with new, evidence-backed candidates rather than
producing commits itself.

**Where the work lives:** `docs/<topic>-<date>.md`, one doc per question.
**Verification gate:** none in the usual sense — the bar is "real evidence
cited, not asserted" (direct code reads, `gh api` queries, sourced web
research), and an honest verdict even when it's "not tractable yet, here's
why."
**Current output:** `docs/research-pipeline-improvements-2026-07-18.md`,
`docs/kernel-test-harness-research-2026-07-18.md`,
`docs/tmpfs-rust-scoping-2026-07-18.md`,
`docs/rust-for-linux-contribution-2026-07-18.md`.

**Continuous Rust-for-Linux upstream-contribution substream
(awto-au/linux-rs#12):** resurvey current Rust-for-Linux docs/issues at
least monthly, or immediately after a Rust-for-Linux rebase/release, and
write a new `docs/rust-for-linux-contribution-YYYY-MM-DD.md`. Track three
queues: (1) upstream docs/good-first issues suitable for learning the kernel
patch flow, (2) recurring `linux-rs` translation shims/API gaps that could
become upstream abstractions/tests, and (3) VFS/driver-abstraction work that
could unblock future translated targets.

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
