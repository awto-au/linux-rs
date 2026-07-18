# VFS abstraction rebase attempt (PR #1037) — genuinely not viable right now

Status: **attempted and evaluated, outcome (3) — not viable.** A real
rebase was attempted in an isolated worktree (`git worktree`, branch
`agent-vfs-rebase-eval`, off `linux-riscv` HEAD `600a755847485510c92f640b20f67aba0cc44dc2`).
It hit real, substantive conflicts on the first two of 29 commits and a
disjoint/partially-obsolete Rust unstable-feature set that would hard-fail
compilation even if every textual conflict were resolved. No build was
attempted because the rebase did not get far enough to produce something
worth compiling — forcing one through by brute-force conflict resolution
across all 29 commits was judged not worth attempting in this session (see
"Why stop here," below). This corrects a specific factual error in
`docs/tmpfs-rust-scoping-2026-07-18.md` (§2(b)): the "PR's base is a direct,
~1-month-old ancestor of this project's HEAD" finding was based on a
misread of GitHub's PR API and does not hold once the actual commits are
inspected directly.

## What was done

1. Isolated worktree created: `scripts/linux_riscv_worktree.py create
   vfs-rebase-eval --base linux-rs/phase2-gcd`, producing
   `linux-riscv-worktrees/vfs-rebase-eval/` on branch
   `agent-vfs-rebase-eval`, off `linux-riscv` HEAD
   `600a75584748` (2026-07-18, the current tip, same commit the 8250
   IRQ-handler TU landed on).
2. Added `rfl` remote (`https://github.com/Rust-for-Linux/linux.git`) and
   fetched PR #1037's actual head directly:
   `git fetch rfl pull/1037/head:pr-1037-vfs`. Confirmed via `gh api
   repos/Rust-for-Linux/linux/pulls/1037` that `head.sha ==
   71891773e80ae6c8523cb39f4ced7cb08ecf0ec9` matches what was fetched —
   this is genuinely the PR's current commit set, not a stale mirror.
3. Attempted `git rebase --onto agent-vfs-rebase-eval <fork-point>
   pr-1037-vfs` and worked through the first two commits' conflicts by
   hand before stopping to assess the pattern (see below).
4. Aborted the rebase cleanly (`git rebase --abort`); `agent-vfs-rebase-eval`
   is untouched at the original HEAD, no changes leaked into it.
5. No changes were made to the shared `linux-riscv/` tree at any point —
   all of this happened in the isolated worktree, as instructed.

## Finding 1: the "~1 month old ancestor" claim in the scoping doc is wrong

The scoping doc's §2(b) checked `git merge-base --is-ancestor
43a393185e33e573a374c1d4f7ddf6481484ef8d HEAD` and got `true`, then
concluded the PR's base was "a direct git ancestor of this project's
current `linux-riscv` HEAD (~1 month back)". That check is correct as far
as it goes — `43a393185e33` genuinely is an ancestor of our HEAD — but it
answers the wrong question. `43a393185e33` is **not** the commit PR #1037's
branch was forked from; it's whatever `rust-next`'s tip happened to be at
the moment `gh api .../pulls/1037` was queried. GitHub recomputes
`base.sha` dynamically for open PRs against the base branch's *current*
tip, not the historical fork point — and for a long-open, never-rebased PR
like this one, those are wildly different commits.

Verified directly, in-worktree, against the actually-fetched PR commits
(not API metadata):

```
$ git log -5 --format='%H %ci %s' pr-1037-vfs
71891773e80a 2023-10-17 -0300 tarfs: introduce tar fs
80fda666dab3 2023-10-17 -0300 rust: fs: export file type from mode constants
b40e37be5eb0 2023-10-17 -0300 rust: fs: allow per-inode data
0605dba93970 2023-10-17 -0300 rust: fs: allow file systems backed by a block device
516d0e402d41 2023-10-17 -0300 rust: fs: add basic support for fs buffer heads

$ git merge-base --is-ancestor 43a393185e33e573a374c1d4f7ddf6481484ef8d pr-1037-vfs
$ echo $?
1   # NO — the "base" GitHub reports is not even reachable from the PR branch

$ git merge-base HEAD pr-1037-vfs
a7135d10754760f0c038497b44c2c2f2b0fb5651

$ git log -1 --format='%H %ci %s' a7135d10754760f0c038497b44c2c2f2b0fb5651
a7135d107547 2023-10-15 21:56:26 +0200 rust: Use grep -Ev rather than relying on GNU grep

$ git rev-list --count a7135d10754760f0c038497b44c2c2f2b0fb5651..HEAD
247018
```

The real fork point is `a7135d1`, dated **2023-10-15** — not 2026-06.
All 29 of the PR's actual commits carry author/committer dates of
2023-10-17/18; none were rebased or re-dated since. The `updated_at:
2026-06-16` timestamp `gh api` reports is a PR-metadata touch (label,
base-branch-tracking recompute, or similar), not evidence of a code
update — the commit hashes and dates are unchanged since 2023.

**Corrected finding: PR #1037's actual fork point is ~247,018 commits and
2.75 years behind this project's current `linux-riscv` HEAD**, not "~1
month back" as previously reported. This is a materially different risk
profile than the scoping doc assessed, and it explains everything found
below.

## Finding 2: real conflicts start on commit 1 of 29

`git rebase --onto agent-vfs-rebase-eval a7135d10754760f0c038497b44c2c2f2b0fb5651 pr-1037-vfs`:

**Commit 1/29** (`a3fe8d85ed51`, "xattr: make the xattr array itself
const") conflicted in `fs/xattr.c` (3 hunks) and `include/linux/fs.h` (1
hunk). On inspection, this was the *good* case: purely cosmetic
(`const struct xattr_handler * const *handlers` vs `*const *handlers` —
clang-format spacing only) plus a large apparent conflict in `fs.h` around
`struct super_block` that turned out to be pure line-drift (the struct
still exists on HEAD's side, just ~290 lines further down the file after
2.75 years of upstream growth — `fs.h` is 3669 lines on HEAD vs 3381 at
the PR's fork point). Root cause confirmed via `git log -S`: mainline
independently landed the exact same semantic change as this commit, under
a different hash (`e346fb6d774a`), already present in our HEAD. **This
commit's content is entirely redundant with what's already in the tree**
— correct resolution was `git rebase --skip`, not a merge.

**Commit 2/29** (`484ec70025ff`, "rust: introduce `InPlaceModule`")
conflicted in three files: `rust/kernel/lib.rs`, `rust/macros/module.rs`
(proc-macro internals), and `scripts/Makefile.build` (Kbuild). Unlike
commit 1, this is not cosmetic. The `lib.rs` conflict is the crate's
unstable-feature-gate list, and the two sides are **entirely disjoint**:

```
HEAD (2026-07-18):
  #![feature(generic_arg_infer)]
  #![feature(arbitrary_self_types)]
  #![feature(derive_coerce_pointee)]
  #![feature(used_with_arg)]
  #![cfg_attr(CONFIG_RUSTC_HAS_FILE_WITH_NUL, feature(file_with_nul))]

PR commit 2 (2023-10-17):
  #![feature(allocator_api)]
  #![feature(coerce_unsized)]
  #![feature(dispatch_from_dyn)]
  #![feature(new_uninit)]
  #![feature(receiver_trait)]
  #![feature(return_position_impl_trait_in_trait)]
  #![feature(unsize)]
```

Zero overlap. This is not a case of "merge both lists" — several of the
PR-side features have since been **stabilized and their `#![feature(...)]`
gates removed from rustc entirely**, which is a hard compile error on a
current toolchain (rustc refuses `#![feature(x)]` for a feature that's
already stable, not just a warning). Confirmed by grepping the PR's full
29-commit diff for every `feature(...)` line touching `rust/kernel/lib.rs`:
it also introduces `#![feature(offset_of)]` (stabilized Rust 1.77, mid-2024)
and reintroduces `return_position_impl_trait_in_trait` (stabilized as RPITIT
in Rust 1.75, late 2023) as unstable gates — both now-invalid attributes on
any 2025+ toolchain.

## Finding 3: the toolchain gap is the real blocker, confirmed against actual installed compilers

```
$ rustc --version
rustc 1.97.0 (2d8144b78 2026-07-07)

$ rustup show
installed toolchains
--------------------
stable-x86_64-unknown-linux-gnu (active, default)
nightly-x86_64-unknown-linux-gnu
nightly-2022-11-03-x86_64-unknown-linux-gnu
nightly-2023-04-15-x86_64-unknown-linux-gnu
nightly-2026-03-03-x86_64-unknown-linux-gnu
nightly-2026-07-09-x86_64-unknown-linux-gnu
```

This project's actual kernel build toolchain is `nightly-2026-07-09` (9
days old at HEAD's date). PR #1037's Rust code, dated 2023-10-17, targets
something in the `nightly-2023-04` to `nightly-2023-10` range — the two
old pinned toolchains still installed locally (`2022-11-03`, `2023-04-15`)
bracket it. That is **~3 years and multiple rustc unstable-feature
churn cycles** away from the toolchain this project actually compiles
with. `allocator_api`/`new_uninit`/`receiver_trait` in particular are
exactly the kind of nightly-only APIs that get reshaped (not just
stabilized-in-place) release over release — `new_uninit` specifically was
split/renamed multiple times between 2023 and its eventual stabilization
path. Getting this code to compile would not be "resolve the merge
conflicts and it builds" — it would require rewriting every commit's use
of these APIs against whatever their 2026 nightly equivalents are, which
is translation work in its own right, not rebase work.

## Why stop here (not attempt all 29 commits by brute force)

Two commits in, the evidence already answers the question the task set
out to answer, on two independent axes:

- **Structural:** commit 1 showed the "clean rebase, just resolve some
  markers" case does exist but only because the PR's change was already
  independently redundant with mainline — not a pattern that generalizes
  to the other 28 commits, which is exactly what commit 2 confirmed.
- **Toolchain:** commit 2 showed a disjoint, partially-stabilized-away
  unstable-feature set. This isn't a per-commit conflict that gets easier
  as more commits land — every one of the remaining 27 commits was written
  against the same 2023-era nightly and crate-internal API shape
  (`ForeignOwnable`, `Opaque::try_ffi_init`, pin-init macros — all evolving
  fast in this exact period upstream), so the same category of problem
  should be expected throughout, not just at the start.

Per the task's own instruction ("Do NOT spend the whole session trying to
make outcome 3 into outcome 1 through brute force — if it's genuinely not
viable, say so with evidence and stop"), grinding through the remaining 27
commits by hand would mean rewriting non-trivial chunks of Rust against a
current nightly with no upstream reference for what the 2026-correct
version of this code looks like (PR #1037 itself hasn't been updated to
work with any toolchain newer than late 2023, upstream included) — that is
a translation/porting project, not a rebase, and not boundable in this
session.

## Verdict: not viable right now, with a concrete precondition for revisiting

**This is not "PR #1037 is bad" or "the VFS abstraction design doesn't
fit."** The abstraction shape (`SuperBlock`, `INode<T>`, `FileSystem`
trait, folio/mem_cache/buffer-head support) is still exactly what
`docs/tmpfs-rust-scoping-2026-07-18.md` §1 found missing, and nothing
found here contradicts that it's the right shape to eventually adopt.

What's blocking adoption **today** is narrower and more concrete than "the
PR is unmerged": **the PR has not been rebased since October 2023**, and
in the 2.75 years since, both (a) mainline's C-side VFS/`fs.h` surface has
drifted (manageable — Finding 2 showed this alone is often just
line-drift or already-redundant, as in commit 1) and, more seriously, (b)
the Rust nightly toolchain and `rust/kernel/` crate's own conventions
(unstable feature gates, `ForeignOwnable`, pin-init patterns) have moved
far enough that the PR's Rust code would need semantic rewriting, not
conflict-marker resolution, to compile.

**Concrete precondition for revisiting:** either (i) upstream
Rust-for-Linux rebases PR #1037 (or a successor) onto a `rust-next` within
the last few months of whatever this project's HEAD is at the time, or
(ii) someone treats "port PR #1037's ~2100 lines onto a 2026 nightly and
current `rust/kernel/` conventions" as its own multi-session translation
project — comparable in scope to (a) in the original scoping doc's §2, not
(b). Given PR #1037 has been open since 2023 without landing even in
`rust-next` and shows no sign of being actively rebased (all commits still
dated 2023-10-17, `mergeStateStatus: DIRTY`/`CONFLICTING` against current
`rust-next` per `gh api`), condition (i) should not be assumed to arrive on
any predictable timeline.

**Recommendation: re-close this as blocked**, same status as before, but
with the corrected evidence — the "good starting position" framing from
the prior scoping doc no longer holds, and any future attempt should
budget for full-rewrite-scale effort (multi-session, comparable to a
from-scratch VFS port) rather than a rebase.

## Effort estimate for landing this properly

Based on the two commits actually attempted plus the toolchain-gap
analysis:

- Per-commit conflict resolution, even for the "easy" cosmetic cases like
  commit 1, requires cross-checking against mainline history (`git log
  -S`) to tell "already redundant, skip" from "genuinely needs merging" —
  budget non-trivial time even for clean-looking commits.
- Every commit touching `rust/kernel/{lib,fs,folio,mem_cache}.rs` or
  `rust/macros/*` should be assumed to need a rewrite against current
  nightly unstable-feature availability and current `rust/kernel/`
  idioms, not a mechanical merge — this is the majority of the PR's 26
  changed files.
- No build was reached, so compile-time surprises beyond the
  feature-gate mismatch (trait signature changes in `core`/`alloc`,
  `ForeignOwnable` shape changes in this project's own crate since 2023)
  are not yet enumerated and should be expected to add further work once
  rewriting starts.
- Rough order of magnitude: **not a single session**; more realistically
  comparable to a from-scratch multi-session translation effort, closer
  to option (a) in the original scoping doc than option (b)'s original
  "adapt, don't rewrite" framing.

## What was NOT done, per task constraints

- Nothing was pushed to `awtoau/linux` — the rebase attempt lives only in
  the local worktree (`linux-riscv-worktrees/vfs-rebase-eval`,
  branch `agent-vfs-rebase-eval`) and was aborted, not landed.
- No changes were made to the shared `linux-riscv/` tree.
- No code was contributed back to Rust-for-Linux or torvalds/linux.
- No `.config` changes were made (`CONFIG_SHMEM` remains unset, unrelated
  to this doc's findings — the VFS abstraction layer itself doesn't build
  regardless of that flag).
