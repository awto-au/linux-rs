# Kernel trees

All kernel checkouts are worktrees of the machine-wide Linux store — no per-project clone (#118).

## Layout

| Path (gitignored) | What | Git |
|---|---|---|
| `tmp/linux-store` | symlink to the store | full clone; remotes `origin` (torvalds), `next`, `stable`, `awto` (awto-au/linux), others |
| `linux/` | pristine v7.1 corpus (x86_64 lab, `compile_commands.json`) | detached at `v7.1` |
| `linux-riscv/` | actively-built riscv tree | branch `awto/linux-rs/phase2-gcd-<base>` |
| `linux-riscv-worktrees/<name>/` | per-agent trees | branch `awto/linux-rs/<name>-<base>` |

- `<base>` = mainline tag under the series (`git describe --tags --abbrev=0 $(git merge-base HEAD origin/master)`).
- Store path: `--store PATH` or env `LINUX_RS_STORE`, default `tmp/linux-store`. Tracked files never hold host paths.

## Commands

- New machine / fresh checkout: `scripts/kernel_store.py --store PATH setup` (refuses a shallow repo).
- State of every tree: `scripts/kernel_store.py survey [--sizes]` → `tmp/kernel_store_survey.json`.
- New agent tree: `scripts/linux_riscv_worktree.py create <name> [--base REV]`; `list`; `remove <name> [--delete-branch]`.
- Move a tree that still has its own `.git` onto the store, in place (keeps build output and uncommitted edits): `scripts/kernel_store.py adopt <tree> [--branch B | --detach]`.
- Rebase onto mainline: `scripts/sync_linux_kernel.py` (fetches store `origin master`).

## Rules

- Never `git clone` a kernel and never `--depth` into the store.
- Never `git gc` / `git repack` in the store or its worktrees: a machine timer does it (`gc.auto 0`).
- awto series are store branches `awto/linux-rs/<topic>-<base>`; pushing is a separate, approved step.
- `linux-riscv/` series is published as `linux-rs/phase2-gcd` on awtoau/linux (not the store's `awto` remote, awto-au/linux).
