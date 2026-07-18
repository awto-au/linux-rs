# Rust-for-Linux upstream contribution survey — 2026-07-18

Issue: [awto-au/linux-rs#12](https://github.com/awto-au/linux-rs/issues/12)

## Purpose

Dan asked for a dedicated, continuing research stream to answer: where can
`linux-rs` contribute back to Rust-for-Linux upstream?

This is the first concrete survey. It is not a claim that `linux-rs` should
upstream machine-translated kernel code directly. The useful contribution
unit is smaller: docs fixes, tests, safe wrappers/abstractions, and evidence
from this project's translation/oracle pipeline that helps upstream decide
which Rust kernel APIs are missing or under-tested.

## Sources checked

- Current Rust-in-kernel docs: <https://docs.kernel.org/rust/index.html>
- Current generated kernel crate docs: <https://rust.docs.kernel.org/>
- Old docs URL requested in the original task, now archived:
  <https://rust-for-linux.github.io/docs/kernel/>
- Rust-for-Linux contribution guide:
  <https://rust-for-linux.com/contributing>
- Public Rust-for-Linux GitHub issue search, sampled 2026-07-18:
  <https://github.com/Rust-for-Linux/linux/issues>
- Rust-for-Linux `linux` good-first-issue page:
  <https://github.com/Rust-for-Linux/linux/contribute>
- Kernel patch-submission process docs:
  <https://docs.kernel.org/process/submitting-patches.html>

## Upstream stance relevant to linux-rs

The current `kernel` crate docs say the crate contains kernel APIs ported or
wrapped for Rust code and that consumers needing a C kernel API that is not
wrapped should port/wrap it first instead of bypassing the crate. That fits
`linux-rs` well: our translation work repeatedly discovers exactly which C
APIs are missing Rust surfaces, but the upstreamable artifact is the
well-scoped wrapper/test/doc, not the whole translated TU.

The contribution guide and current good-first issues also make clear that
real Rust-for-Linux contributions still need normal kernel submission
discipline: LKML/Rust-for-Linux mailing list patch flow, a justified commit
message, Developer Certificate of Origin sign-off, and appropriate `Link:` /
`Suggested-by:` tags where requested.

## Current contribution lanes

### 1. Documentation and rustdoc fixes — best first upstream lane

**Why this fits linux-rs:** low risk, fast review, no claim about translation
correctness, and it builds familiarity with the Rust-for-Linux patch flow.

Concrete current examples from `Rust-for-Linux/linux`:

- [#1246](https://github.com/Rust-for-Linux/linux/issues/1246): fix two
  incorrect Rustdoc source-tree paths (`print.rs` and `irq.rs`). This is the
  cleanest first patch: it is docs-only and directly checkable by regenerating
  docs.
- [#1240](https://github.com/Rust-for-Linux/linux/issues/1240): improve `Arc`
  docs style/links.
- [#1242](https://github.com/Rust-for-Linux/linux/issues/1242): improve
  generated docs for `build_assert`.
- [#1244](https://github.com/Rust-for-Linux/linux/issues/1244): update an
  `impl_flags!` example to use `bits`.

**Recommendation:** take one docs-only issue first, preferably #1246, from a
separate Rust-for-Linux worktree. Success criterion is not code volume; it is
one properly submitted patch that proves this project can follow upstream
process.

### 2. Missing abstractions surfaced by linux-rs translations

**Why this fits linux-rs:** every translated TU that has to add a C shim,
raw binding call, or local wrapper is evidence of a missing or insufficient
Rust kernel API. These should be triaged into upstreamable abstraction
requests only after a minimal safe API and test can be stated.

Candidate areas already surfaced locally:

- **MMIO / IO memory helpers:** `iomem_copy` and 8250 work exercise raw
  memory/register access. Upstreaming should focus on safe wrapper shape and
  invariants, not the translated driver fragment.
- **Userspace-copy typed buffers:** rule 0015 already names a stricter
  type-safety story for userspace copy. This can become an upstream
  discussion only when accompanied by a narrow example and compile-time
  reject/accept cases.
- **VFS/filesystem abstractions:** local tmpfs scoping found the vendored
  tree lacked enough VFS registration surface for a Rust filesystem. Upstream
  [PR #1037](https://github.com/Rust-for-Linux/linux/pull/1037) (`vfs
  abstractions and tarfs`) is the relevant place to watch/evaluate rather
  than inventing a competing local API.

**Recommendation:** for each future translated TU, record "upstream API gap?"
as a yes/no row in the scoping doc. Only promote to an upstream contribution
candidate when the gap recurs or blocks a real in-tree Rust integration path.

### 3. Tests and oracle methodology

**Why this fits linux-rs:** this project has unusually strong differential
testing discipline: C/Rust byte-identical oracles, KUnit boot gates,
compile-pass-rate regression checks, and explicit provenance checks. Some of
that methodology can become upstream tests or documentation even when the
translated implementation is not upstreamable.

Potential contribution shapes:

- Add small KUnit or doctest coverage around an existing Rust abstraction
  when a linux-rs translation exposes a boundary condition.
- Upstream docs patches that show how to validate a wrapper against the C API
  it replaces.
- File Rust-for-Linux issues with minimal reproducer cases when c2rust/raw
  translation uncovers a missing kernel crate API or unclear safety contract.

**Recommendation:** do not upstream the linux-rs harness wholesale. Extract
small, reviewer-friendly tests tied to one upstream abstraction.

### 4. Tooling / linting / generated docs support

Rust-for-Linux has adjacent tooling repositories such as `klint`, but the
current public issue search did not show open `good first issue` / `help
wanted` items there on 2026-07-18. Keep this as a lower-priority watch lane.

## Near-term task list

1. **First upstream patch attempt:** take
   [Rust-for-Linux/linux#1246](https://github.com/Rust-for-Linux/linux/issues/1246)
   or another docs-only issue if #1246 closes first.
2. **Evaluate PR #1037:** rebase/read the VFS abstractions work against this
   project’s tmpfs-blocking notes and write a short delta report: which APIs
   would unblock a Rust tmpfs experiment, which are still missing, and what
   local translation evidence supports the request.
3. **Add a contribution-candidate subsection to future TU scoping docs:**
   each translation should list missing wrappers, local shims, safety
   invariants discovered, and whether any are upstreamable.
4. **Monthly resurvey cadence:** rerun the GitHub issue/doc scan once per
   month, or immediately after a Rust-for-Linux release/rebase, and append a
   new `docs/rust-for-linux-contribution-YYYY-MM-DD.md`.

## Verdict

The best immediate upstream contribution is not a translated Linux TU. It is
a small docs/process patch that gets `linux-rs` into the Rust-for-Linux
submission loop, followed by focused abstraction/test proposals backed by
translation evidence. The continuous stream should watch three queues:

1. current Rust-for-Linux docs/good-first issues,
2. recurring local translation shims/API gaps,
3. upstream VFS and driver-abstraction work that could unblock future
   linux-rs targets.
