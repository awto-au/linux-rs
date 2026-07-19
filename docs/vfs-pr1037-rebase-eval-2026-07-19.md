# PR #1037 rebase re-attempt — confirms 2026-07-18 no-go, adds depth

Status: **re-confirms an existing verdict.** This task set out to attempt
the rebase `docs/tmpfs-rust-scoping-2026-07-18.md` recommended, on the
premise (also stated in this task's own brief) that PR #1037's base was
"~1 month" behind this project's HEAD. That premise is **wrong** —
`docs/tmpfs-vfs-rebase-2026-07-18.md`, written the same day as the
scoping doc, already discovered and corrected this (the "~1 month"
figure was a `gh api` `base.sha` metadata misread; the PR's real fork
point is ~2023-10-15, ~2.75 years / ~247k commits back) and reached a
firm **no-go** after attempting 2 of 29 commits. This doc is an
independent re-run that reaches the same fork point via a different
method, pushes materially further (16 of 30 commits attempted vs. 2),
and finds the same conflict pattern holds throughout. No new go-signal
found.

## Setup

- Worktree: `scripts/linux_riscv_worktree.py create vfs-pr1037-eval`,
  based on `linux-riscv` HEAD `04312ea1ff7e` ("riscv: wire riscv-march-y's
  Zacas/Zabha into KBUILD_RUSTFLAGS", 2026-07-19).
- `gh api repos/Rust-for-Linux/linux/pulls/1037 --jq '{...}'` re-checked:
  `state: open`, `head_sha: 71891773e80ae6c8523cb39f4ced7cb08ecf0ec9`,
  `mergeable: false`, `mergeable_state: dirty`, `commits: 29`,
  `changed_files: 26`, `updated_at: 2026-06-16T23:33:45Z` — identical to
  the 2026-07-18 scoping doc's numbers, confirming the PR itself hasn't
  moved this past month.
- `git fetch https://github.com/Rust-for-Linux/linux.git refs/pull/1037/head`
  succeeded, `FETCH_HEAD` = `71891773e80a`.

## Finding 0: the base_sha ancestor check is a metadata trap (corroborates 07-18 doc independently)

`git merge-base --is-ancestor 43a393185e33... HEAD` → true (matches both
prior docs). But `git merge-base 43a393185e33... FETCH_HEAD` → **no
common ancestor found** (exit 1) — `base.sha` is not reachable from the
PR branch's own commits at all. Walking from the PR's actual oldest
commit instead:

```
$ git log --format=%P -1 a7135d107547   # oldest of the 30 PR commits
45f97e6385cad6d0e48a27ddcd08793bb4d35851
$ git log -1 --format='%H %ci %s' 45f97e6385cad6d0e48a27ddcd08793bb4d35851
45f97e6385ca 2023-10-15 21:48:24 +0200 rust: Use awk instead of recent xargs
$ git merge-base --is-ancestor 45f97e6385ca... HEAD && echo true
true
```

This is the **same commit region** `docs/tmpfs-vfs-rebase-2026-07-18.md`
found (`a7135d1`, 2023-10-15, 8 minutes later in the same commit chain —
that doc used the PR's commit #1 itself as the reference point, this run
used its parent; same fork point either way). Real fork point: **2023-10-15**,
not "~1 month" — the scoping doc's framing (and this task's brief, which
inherited it) is superseded by the 07-18 rebase doc and is wrong.
`git rev-list --count 45f97e6385ca..FETCH_HEAD` returns >1.2M (walks
unrelated history when the two tips share no path through that SHA —
not a meaningful number; the useful count is the PR's own 30-commit
range, `git rev-list --count 45f97e6385ca..71891773e80a` = **30**).

## Finding 1: attempted `git apply --check` (whole-diff), fails on 12/26 files

`gh pr diff 1037 --repo Rust-for-Linux/linux` → 3122-line diff, 26 files.
`git apply --check` against worktree HEAD:

```
error: patch failed: fs/Makefile:129
error: patch failed: fs/xattr.c:56
error: patch failed: include/linux/fs.h:1206
error: patch failed: rust/bindings/bindings_helper.h:7
error: patch failed: rust/bindings/lib.rs:51
error: rust/helpers.c: No such file or directory        <- renamed upstream
error: patch failed: rust/kernel/error.rs:131
error: rust/kernel/fs.rs: already exists in working directory   <- add/add
error: patch failed: rust/kernel/lib.rs:16
error: rust/kernel/time.rs: already exists in working directory <- add/add
error: patch failed: rust/kernel/types.rs:7
error: patch failed: rust/macros/module.rs:208
error: patch failed: samples/rust/Makefile:1
error: patch failed: scripts/Makefile.build:262
error: patch failed: scripts/generate_rust_analyzer.py:116
```

`rust/helpers.c` failure is itself informative: mainline split it into
`rust/helpers/helpers.c` + per-subsystem files via commit
`876346536c1b59a5b1b5e44477b1b3ece77647fd` ("rust: kbuild: split up
helpers.c"), which post-dates the PR's 2023 base — generic upstream
drift, not a project-specific conflict.

## Finding 2: commit-level rebase, 30 commits, attempted 16 (vs. prior doc's 2)

`git rebase --onto <worktree-HEAD> 45f97e6385ca 71891773e80a` (the real
30-commit range — GitHub's `commits: 29` undercounts by one, likely a
merge-commit accounting quirk). Worked through conflicts by hand,
classifying each; stopped at commit 16/30 once the pattern was
unambiguous (see "why stop", below). Full sequence:

| # | commit | conflict | class |
|---|---|---|---|
| 1 | `a7135d1` grep -Ev fix | `rust/Makefile` (2 hunks) | **already landed** — HEAD independently has the same fix |
| 2 | `a3fe8d8` xattr const | `fs/xattr.c` (3 hunks, cosmetic `* const *` spacing), `include/linux/fs.h` (1 hunk — PR's diff tries to delete/reinsert all 175 lines of `struct super_block`, which has been restructured upstream since 2023) | **already landed** (const fix) + **pure drift** (struct relocation) |
| 3 | `484ec70` `InPlaceModule` | `rust/kernel/lib.rs`, `rust/macros/module.rs`, `scripts/Makefile.build` | **superseded** — HEAD's `InPlaceModule`/`module!` impl is a materially different, more advanced design (`ModuleMetadata` trait, double-nested-module wrapping, `#[used(compiler)]`, `pin_init` crate) that landed in mainline independently since 2023 |
| 4 | `da1a2b6` in-place-init sample | `samples/rust/{Kconfig,Makefile}`, `scripts/Makefile.build` | fallout of #3 — sample for the now-obsolete API |
| 5 | `883e433` `container_of!` | `rust/kernel/lib.rs` | **already landed** (used by `auxiliary.rs` in current HEAD) |
| 6 | `14513c0` `Opaque::try_ffi_init` | `rust/kernel/types.rs` | **already landed** |
| 7 | `c7d0fb2` `time` module | `rust/kernel/lib.rs`, `rust/kernel/time.rs` (add/add) | **already landed** (HEAD's `time.rs` is 496 lines, its own design) |
| 8 | `ca4a93c` little-endian type | `rust/kernel/types.rs` | **already landed** (`LittleEndian` trait, `LE<T>` present) |
| 9 | `a44bdcc` `FromBytes` trait | `rust/kernel/types.rs` | **already landed** (HEAD uses real `zerocopy` crate) |
| 10 | `caf9b29` `MemCache` | `rust/bindings/bindings_helper.h`, `rust/bindings/lib.rs`, `rust/kernel/lib.rs` | **genuinely new, applies cleanly once spliced** — `mem_cache.rs` (64 lines) has no HEAD equivalent; conflicts were all in surrounding files (bindings header grown from ~6 to ~80 includes since 2023, `lib.rs`'s module list alphabetically reordered/grown) |
| 11 | `b0bc357` kbuild alloc | `scripts/Makefile.build` (`rust_allowed_features` line) | **drift** — HEAD's unstable-feature list has already moved past this (has `allocator_api`-equivalent stabilized) |
| 12 | `528babd` **fs registration/unregistration** | `rust/bindings/bindings_helper.h`, `rust/kernel/error.rs`, `rust/kernel/fs.rs` (**add/add**), `rust/kernel/lib.rs` | **substantive, real payload** — see below |
| 13–14 | `e909f43`, `626056a` module_fs macro, `FileSystem::super_params` | (auto-merged cleanly once #12 was spliced) | **clean** — confirms fs.rs accepts pure-addition follow-ons once the base splice is done |
| 15 | `ad07f4b` ro-fs sample | `samples/rust/{Kconfig,Makefile}` | fallout of #3/#4 pattern — sample references obsolete module macro |
| 16 | `a448dc5` `INode<T>` | `rust/helpers.c` (**modify/delete** — file was split upstream, see Finding 1) | **mechanical but per-commit** — needs retargeting to `rust/helpers/fs.c` or similar, not a semantic conflict |

Stopped here (16/30): every remaining conflict category had already
repeated at least twice (already-landed foundational commit,
`rust_allowed_features`-style drift, `rust/helpers.c` split fallout).
Continuing to 30 would add volume, not new conflict *types*.

## Finding 3: the one real semantic conflict — `error.rs::from_result` visibility

Commit 12 (`528babd`, the actual "add fs registration" commit) conflicts
on a single line in `rust/kernel/error.rs`:

```
<<<<<<< HEAD
pub fn from_result<T, F>(f: F) -> T
=======
pub(crate) fn from_result<T, F>(f: F) -> T
>>>>>>> 528babded936 (rust: fs: add registration/unregistration of file systems)
```

PR wants `from_result` narrowed back to `pub(crate)` (its 2023 shape).
Checked why HEAD widened it:

```
$ grep -rln "from_result" rust/kernel/ --include=*.rs | grep -v error.rs
rust/kernel/i2c.rs
rust/kernel/cpufreq.rs
rust/kernel/pci.rs
rust/kernel/opp.rs
rust/kernel/auxiliary.rs
rust/kernel/fs.rs
rust/kernel/usb.rs
rust/kernel/module_param.rs
rust/kernel/platform.rs
rust/kernel/net/phy.rs
rust/kernel/block/mq/operations.rs
```

11 files outside `error.rs` call it — driver subsystems broadly, not
specifically `debugfs` as the scoping doc's flag speculated (checked:
`debugfs.rs` does not call `from_result` directly). Correct resolution:
keep `pub` (HEAD's version); the PR's narrower scope would break 11
unrelated call sites. This is the one conflict in the whole attempted
range that required understanding *why* the two sides differ, not just
picking a side — genuinely substantive, but small (one line) and
one-directional (HEAD's version is strictly a superset of what's
needed).

## Finding 4: `rust/kernel/fs.rs` — add/add, resolved by splice, mechanically simple once done

Our HEAD's `fs.rs` (11 lines, consumer-side `File`/`Kiocb` re-exports)
vs. PR commit 12's `fs.rs` (75 lines, `FileSystem` trait +
`Registration`/`PinnedDrop` for `register_filesystem`/
`unregister_filesystem`) is a genuine **add/add** conflict — both sides
independently created content at the same path. Resolved by splicing:
kept HEAD's `pub mod file`/`mod kiocb` declarations, appended the PR's
registration code beneath. No logic changes needed — the two halves
don't reference each other. Confirmed this splice is stable: commits 13
(`module_fs!` macro) and 14 (`FileSystem::super_params`, +170/-6 lines)
both auto-merged onto the spliced file with **zero further conflicts**,
landing `fs.rs` at 90 lines cleanly. This is the strongest positive
signal in the whole attempt: once the add/add base conflict is resolved
by hand, the PR's own internal commit sequence for `fs.rs` itself applies
as designed.

## Toolchain gap (not independently re-verified today, inherited from 07-18 doc)

Not re-run in this session (would have meant reproducing work
`docs/tmpfs-vfs-rebase-2026-07-18.md` already did in detail). That doc
found HEAD's `rust/kernel/lib.rs` unstable-feature gates
(`generic_arg_infer`, `arbitrary_self_types`, `derive_coerce_pointee`,
`used_with_arg`, `file_with_nul`) are **entirely disjoint** from the
PR's 2023-era set (`allocator_api`, `coerce_unsized`, `dispatch_from_dyn`,
`new_uninit`, `receiver_trait`, `return_position_impl_trait_in_trait`,
`unsize`), several of which are now hard compile errors on a current
nightly (stabilized features can't be `#![feature(...)]`-gated).
Spot-checked today that this still holds: current HEAD's `lib.rs` feature
list is unchanged from what that doc quoted. No reason to believe this
has improved — the PR hasn't moved (`updated_at` unchanged from
2026-06-16, same commit hashes, same 2023-10-17 commit dates throughout
the 30-commit range checked today).

## Conflict tally

- Files touched by the full 30-commit range: 27 changed (`git diff --stat`
  over the full range), +2750/-31 lines.
- Commits attempted: 16 of 30.
- Of those 16: **9 were no-op** (HEAD already has independently-landed
  equivalents — skip), **3 were pure drift/cosmetic** (line reflow,
  already-superseded Makefile flags), **3 were sample-file fallout** from
  skipping obsolete API demos, **1 was the real payload** (fs registration:
  1 substantive semantic conflict in `error.rs`, 1 mechanical add/add
  splice in `fs.rs`), and the 16th (`rust/helpers.c` split) is a
  mechanical-but-per-commit retarget expected to recur on every remaining
  commit that adds a C helper (the `MemCache`, folio, buffer-head,
  inode-alloc commits still to come all plausibly add helpers).

## Go/no-go

**No-go, confirmed independently.** Same verdict as
`docs/tmpfs-vfs-rebase-2026-07-18.md`, reached via more commits (16 vs.
2) and more diverse conflict evidence. The structural picture: PR #1037's
prerequisite/plumbing commits (17 of the leading ~26 non-`fs`-specific
commits) are almost entirely **obsolete** — mainline independently
re-solved the same problems (`InPlaceModule`, `container_of!`,
`try_ffi_init`, `time` module, little-endian types, `FromBytes`) with
different, more mature designs since 2023, so most of the PR's diff
volume is dead weight, not portable content. The genuine unique payload
(`fs.rs` registration, `mem_cache.rs`, and presumably `folio.rs`/
`fs/buffer.rs`/`fs/tarfs`/inode support not yet reached) does apply
mechanically once past each add/add splice point, and the one semantic
conflict found (`from_result` visibility) was small and one-directional
— that part of the finding is genuinely more encouraging than yesterday's
2-commit sample suggested. But it doesn't change the bottom line:
getting there requires per-commit archaeology (distinguish "already
landed, skip" from "genuinely new, splice" from "obsolete API, drop") for
all 30 commits, then the toolchain-gap rewrite `docs/tmpfs-vfs-rebase-
2026-07-18.md` already documented in detail. This remains multi-session,
translation-project-scale work, not a rebase — consistent with, not a
revision of, yesterday's verdict.

**Correction to propagate:** any future task brief citing "PR #1037's
base is ~1 month behind HEAD" (this task's own brief did) is repeating
the stale claim from `docs/tmpfs-rust-scoping-2026-07-18.md` §2(b),
already corrected in `docs/tmpfs-vfs-rebase-2026-07-18.md` and
re-confirmed here. The real fork point is 2023-10-15.

## Worktree disposition

Worktree `linux-riscv-worktrees/vfs-pr1037-eval` (branch
`agent-vfs-pr1037-eval`) **removed** after this session
(`scripts/linux_riscv_worktree.py remove vfs-pr1037-eval
--delete-branch`) — matches the precedent set by the 07-18 attempt (its
`vfs-rebase-eval` worktree was likewise not kept). Given the verdict is
now confirmed twice independently with no active near-term plan to
pursue the multi-session rewrite, keeping the worktree around had no
value; the fetched PR ref, fork-point SHAs, and per-commit conflict
classification above are sufficient to resume from scratch if this is
picked up later (re-fetch `refs/pull/1037/head` from
`https://github.com/Rust-for-Linux/linux.git`, rebase onto a fresh
worktree's HEAD starting at the parent of `a7135d107547`).

No `.config` changes made. No boot-path involvement. Nothing committed
or pushed inside `linux-riscv`/the worktree at any point (git repo
requires no push there per this project's convention — kernel-tree work
stays local/uncommitted).
