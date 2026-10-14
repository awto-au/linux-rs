# Kernel trees

All kernel checkouts are worktrees of the machine-wide Linux store — no per-project clone (#118).

## Layout

| Path (gitignored) | What | Git |
|---|---|---|
| `tmp/linux-store` | symlink to the store | full clone; remotes `origin` (torvalds), `next`, `stable`, `awto` (awto-au/linux), others |
| `linux/` | pristine v7.1 corpus (x86_64 lab, `compile_commands.json`) | detached at `v7.1` |
| `linux-riscv/` | actively-built riscv tree | branch `awto/linux-rs/phase2-gcd-<base>` |
| `linux-riscv-worktrees/<name>/` | per-agent trees (create on demand; retire when done) | branch `awto/linux-rs/<name>-<base>` |

- `<base>` = mainline tag under the series (`git describe --tags --abbrev=0 $(git merge-base HEAD origin/master)`).
- Store path: `--store PATH` or env `LINUX_RS_STORE`, default `tmp/linux-store`. Tracked files never hold host paths.

## Commands

- New machine / fresh checkout: `scripts/kernel_store.py --store PATH setup` (refuses a shallow repo).
- State of every tree: `scripts/kernel_store.py survey [--sizes]` → `tmp/kernel_store_survey.json`.
- New agent tree: `scripts/linux_riscv_worktree.py create <name> [--base REV]`; `list`; `remove <name> [--delete-branch]`.
- Move a tree that still has its own `.git` onto the store, in place (keeps build output and uncommitted edits): `scripts/kernel_store.py adopt <tree> [--branch B | --detach]`.
- Rebase onto mainline: `scripts/sync_linux_kernel.py` (fetches store `origin master`).

## Upstream bump (re-translating drifted TUs)

- Source revision per TU: `rulesdb/tu_provenance.json` `c_source_file` / `c_blob_sha` / `c_base_tag` (source of truth); `dev.py db` mirrors into `patterns.db` `translated_tus`.
- Split TUs (8250 slices): `c_source_file` = `split_tu_provenance.source_c_file`.
- Gate: `check_tu_provenance.py` (in `dev.py check`) fails if `c_source_file`'s blob at the series base ≠ `c_blob_sha` → Rust is stale.
- Advisory: `check_c_drift.py` (in `dev.py check`, warn-only) lists TUs whose `c_blob_sha` ≠ blob at `origin/master` — what the next rebase will make stale.

Procedure:
1. `scripts/sync_linux_kernel.py --dry-run` → fetches, prints `REQUIRED FOLLOW-UP` drift list (also `tmp/c_drift.json`).
2. `scripts/sync_linux_kernel.py` → rebase; drift is reported, never aborts the rebase. Post-rebase `dev.py check` fails on the stale TUs until step 3–4.
3. Per drifted TU:
   - c2rust TU (`c2rust`, `c2rust+hand-fix`): re-run the c2rust + fix-class pipeline on the new C.
   - hand-bridge TU: port the C diff (`git diff <c_blob_sha> <new blob>`) by hand, tracked under its `replacement_issue`.
4. `scripts/check_c_drift.py --record <rs_file> ...` → updates `c_blob_sha`/`c_base_tag` to the new base; `dev.py db`.
5. `dev.py check` (provenance gate + build + boot/KUnit oracle).
- Never `--record` a TU that wasn't re-translated: it hides the drift.

## Rules

- Never `git clone` a kernel and never `--depth` into the store.
- Never `git gc` / `git repack` in the store or its worktrees: a machine timer does it (`gc.auto 0`).
- awto series are store branches `awto/linux-rs/<topic>-<base>`, published under the same names on awto-au/linux (store remote `awto`); pushing is a separate, approved step.
- Before pushing (public repo): `scripts/scrub_kernel_branches.py` must report 0 hits; fix a hit at a branch tip with `scripts/rewrite_branch_file.py`.
- Retiring agent trees: `scripts/audit_kernel_worktrees.py` (lists unique work), `--retire --wastebasket DIR` (snapshot-commits uncommitted work on the tree's branch, moves the dir out).
- `linux-rs/phase2-gcd` on awtoau/linux is a stale earlier publication.
