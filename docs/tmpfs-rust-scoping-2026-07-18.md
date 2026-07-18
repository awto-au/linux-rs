# tmpfs-in-Rust — scoping

Status: **research only.** No code written, no changes to `linux-riscv/` of
any kind. This doc exists to answer one question before any translation
work starts: is "write a Rust tmpfs for this kernel" a realistically
scoped task today, and if not, what would need to be true first.

**Short answer: no, not yet.** The kernel crate vendored in this project's
tree has no VFS filesystem-registration abstractions at all — there is
nowhere for a Rust tmpfs to attach, regardless of how well the C logic is
translated. This is a harder gap than any TU landed or scoped so far,
including the 8250 driver (see
`docs/serial-8250-translation-scoping-2026-07-18.md`), because 8250 at
least has a `struct uart_ops`-shaped boundary and a live analog in
`rust/kernel/`'s conventions; a filesystem has neither.

## 1. Confirmed VFS-abstraction gap

Re-verified directly against `linux-riscv/rust/kernel/` (HEAD
`5c1e05432402`, Linux 7.2.0-rc3, synced today) rather than trusting the
prior summary.

### `rust/kernel/fs.rs` and `rust/kernel/fs/`

```
$ cat linux-riscv/rust/kernel/fs.rs
// SPDX-License-Identifier: GPL-2.0
//! Kernel file systems.
//! C headers: [`include/linux/fs.h`](srctree/include/linux/fs.h)

pub mod file;
pub use self::file::{File, LocalFile};

mod kiocb;
pub use self::kiocb::Kiocb;
```

11 lines. `rust/kernel/fs/` contains exactly two files:

- `file.rs` (19K) — `File`/`LocalFile`, wrapping `struct file` (an
  **already-open** file descriptor: refcounting via `fget`/`fput`,
  `FileDescriptorReservation` for fd-table slot management, `O_*` flag
  constants, file position/credential accessors). This is entirely
  consumer-side (something that opens/holds/reads an fd) — nothing here
  creates, backs, or registers a filesystem.
- `kiocb.rs` (2.4K) — `Kiocb<T>`, a thin wrapper around `struct kiocb`
  (position + typed private data) used for read/write callback
  implementations on an already-attached file. Also consumer-side.

Neither file contains, references, or implies `struct super_block`,
`struct inode` (as something you'd *implement*, as opposed to read
`f_cred`/`private_data` off of), `struct dentry` (as something you'd
*create*), `struct address_space`, `struct file_system_type`, or
`register_filesystem`/`kill_litter_super`-equivalent lifecycle hooks.

### Broad grep across the whole crate

```
$ grep -rln "super_block\|SuperBlock\|struct inode\|dentry\|Dentry\|address_space\|AddressSpace\|file_system_type\|register_filesystem" linux-riscv/rust/kernel/
linux-riscv/rust/kernel/debugfs.rs
linux-riscv/rust/kernel/error.rs
linux-riscv/rust/kernel/debugfs/entry.rs
linux-riscv/rust/kernel/debugfs/file_ops.rs
```

Checked each hit — none is real VFS-registration infrastructure:
- `debugfs/entry.rs`, `debugfs/file_ops.rs`: `Entry` wraps a raw
  `*mut bindings::dentry` **pointer**, used only because `debugfs_create_dir`/
  `debugfs_create_file` (debugfs's own creation API, not the generic VFS)
  happen to return dentries. The `struct inode` mentions are safety-comment
  prose about `simple_open()`'s private-data contract, not an `Inode` type.
  Debugfs is a special-purpose pseudo-filesystem with its own narrow C API;
  wrapping it does not require (and this wrapper does not provide) generic
  inode/dentry/superblock lifecycle management.
- `error.rs`: a single doc-comment for the `EOPENSTALE` error code
  ("Open found a stale dentry") — just prose, not an abstraction.

### Adjacent modules checked for reusable groundwork

Went beyond the grep to check whether any *other* fs-adjacent module could
serve as a foundation even without full VFS registration:
- `rust/kernel/mm.rs` + `rust/kernel/mm/` (`virt.rs`, `mmput_async.rs`) —
  this is `struct mm_struct`/VMA management for a **userspace process's
  address space** (page tables, `mmap` regions). It has nothing to do with
  a filesystem's page cache (`struct address_space` on an inode) despite
  the name collision with "memory management." Not reusable for tmpfs.
- `rust/kernel/page.rs` — raw page allocation/`PAGE_SIZE` constants only
  (`alloc_page`-style wrappers). No `folio`, no page-cache insertion, no
  `struct address_space` operations (`readpage`/`writepage`/etc.).
- `rust/kernel/block.rs` + `rust/kernel/block/mq.rs` — block-layer
  (`struct request_queue`, blk-mq) abstractions for **block device
  drivers**, not filesystems. tmpfs doesn't sit on a block device (it's
  purely memory/swap-backed), so this wouldn't apply even if it were
  filesystem-shaped, which it isn't.
- No `folio.rs`, no `fs/buffer.rs`, no `mem_cache.rs` anywhere in this
  project's vendored crate (see §2 — these three specifically exist in the
  unmerged upstream RFC and don't exist here).

**Conclusion of §1:** the earlier summary holds up under direct
re-verification. There is no partial inode/address_space/superblock
scaffolding anywhere in this crate to build on. A Rust tmpfs has no VFS
object to construct that the crate knows how to hand back to the kernel.

### CONFIG_SHMEM status

```
$ grep CONFIG_SHMEM linux-riscv/.config
# CONFIG_SHMEM is not set
```

tmpfs's own dependency (`CONFIG_SHMEM`, gating `mm/shmem.c` registering
`shmem_fs_type` and the `tmpfs` filesystem) is disabled in this project's
current kernel config. Note `mm/shmem.o` still builds today — `mm/Makefile`
line 54 puts `shmem.o` in the **unconditional** `obj-y` list (its
`shmem_zero_setup`/`vm_ops`-style helpers back anonymous/`MAP_SHARED`
memory unconditionally, independent of `CONFIG_SHMEM`), and
`CONFIG_TMPFS_QUOTA` gates only the separate `shmem_quota.o`. The actual
`register_filesystem(&shmem_fs_type)` call and `tmpfs` mount type are
compiled out. Even a hypothetical complete Rust VFS layer would need this
flipped on before a Rust tmpfs could be exercised end-to-end.

## 2. Three options, assessed

### (a) Port the missing VFS abstractions into `rust/kernel/` first

This project's own standing rule is "if you need a kernel C API not
wrapped yet, wrap it first instead of bypassing the crate" — the pattern
behind small additions like `kernel::warn_on!`. Applying that rule
literally here means: write `SuperBlock`, `Inode`, `Dentry`,
`AddressSpace`, `file_system_type` registration, and the associated
op-table traits (`inode_operations`, `super_operations`,
`address_space_operations` equivalents) as new modules in
`rust/kernel/fs/`, from scratch, against this project's own Linux
7.2.0-rc3 tree.

**Effort/risk:** this is not comparable in scope to any wrapper landed so
far. It is kernel-architecture-level work — designing safe Rust
abstractions over some of the most aliasing-heavy, lifetime-entangled,
lock-ordering-sensitive data structures in the entire kernel (a `dentry`
alone participates in RCU-walked lookup caches, per-superblock LRU lists,
and mount-point overlay logic). Getting the safety invariants right is
exactly the multi-year problem the real Rust-for-Linux VFS RFC (below) has
been iterating on since **2023** without landing. Attempting this from
scratch, independently, with less domain context than the RFC's author
(Wedson Almeida Filho, a Rust-for-Linux maintainer), is a bad bet: high
probability of producing either an unsound abstraction or one narrow
enough it doesn't actually generalize to tmpfs's needs (tmpfs is
swap-aware, seal-aware, quota-aware — see §3). Not recommended as a
from-scratch undertaking.

### (b) Adopt/adapt the unmerged upstream RFC (PR #1037)

Checked directly via `gh api`, not assumed:

```
$ gh api repos/Rust-for-Linux/linux/pulls/1037 \
    --jq '{title,state,base_ref:.base.ref,base_sha:.base.sha,created_at,updated_at,commits,changed_files}'
{
  "title": "vfs abstractions and tarfs",
  "state": "open",
  "base_ref": "rust-next",
  "base_sha": "43a393185e33e573a374c1d4f7ddf6481484ef8d",
  "created_at": "2023-09-29T21:54:09Z",
  "updated_at": "2026-06-16T23:33:45Z",
  "commits": 29,
  "changed_files": 26
}
```

Key finding, updating the task's framing: **the version gap is much
smaller than expected, and the PR is actively maintained, not
abandoned.** It's been open since 2023 but was last force-pushed/rebased
2026-06-16 — about a month before this project's current sync. Its base
commit (`43a39318`, "rust: prelude: add `zerocopy::IntoBytes`", dated
2026-06-16) is **a direct git ancestor of this project's `linux-riscv`
HEAD** (`git merge-base --is-ancestor 43a393185e33e573a374c1d4f7ddf6481484ef8d HEAD`
returns true). This project's tree is not some divergent fork the RFC
predates — the RFC's base is literally in this project's own history,
just ~1 month and however many commits back. This is a *far* better
starting position than "unmerged RFC vs. latest master" usually implies.

What the PR actually contains (26 files, mostly additive):
- `rust/kernel/fs.rs`: **+1290 lines** (vs. this project's 11-line
  stub) — the real VFS abstraction layer: superblock, inode, dentry,
  registration, op-tables.
- `rust/kernel/folio.rs`: **+214 lines**, new file — page-cache folio
  wrapper, a prerequisite for any `address_space`-backed filesystem
  (tmpfs is exactly this).
- `rust/kernel/fs/buffer.rs`: **+60 lines**, new file — buffer-head glue.
- `rust/kernel/mem_cache.rs`: **+62 lines**, new file — slab cache wrapper
  for inode allocation.
- `fs/tarfs/{defs.rs,tar.rs,Kconfig,Makefile}`: **+426 lines** — a
  complete example filesystem (read-only tar-backed) built on the above,
  serving the same role a worked example serves: proof the abstraction is
  usable end-to-end, and a template for what a tmpfs port would need to
  look like.
- `samples/rust/rust_rofs.rs`: **+154 lines** — a second, simpler example
  (read-only fs sample).
- Supporting edits to `rust/kernel/{error,time,types,lib}.rs`,
  `rust/bindings/*`, `rust/helpers.c`, `rust/macros/module.rs`,
  `scripts/Makefile.build` — the usual plumbing a new subsystem needs
  wired through the build.

None of these files/paths exist in this project's tree today (confirmed:
`fs/tarfs`, `rust/kernel/fs/buffer.rs`, `rust/kernel/folio.rs`,
`samples/rust/rust_rofs.rs` all `ls`-fail). So this would be a genuinely
new import, not a merge of overlapping work — but the target it would
apply against (the crate's existing `error.rs`/`types.rs`/`fs.rs`/`fs/`
layout) is structurally the same crate at almost the same point in time,
which is the condition that makes a rebase/port tractable rather than a
rewrite.

**Caveats:** `mergeable_state: "dirty"` against the PR's *current* declared
base (meaning it doesn't cleanly `git am`/merge onto `rust-next` HEAD as
of query time) — normal for an almost-3-year-old draft carrying 29 commits
across a fast-moving base, not a red flag on its own, but it does mean
whoever picks this up should expect **some** conflict resolution, not a
clean cherry-pick. It's also still explicitly a draft/RFC never merged
even into `rust-next` (the staging branch, one step below mainline) — so
even upstream Rust-for-Linux maintainers have not signed off on its API
shape. Building on it means this project would be depending on
still-evolving, not-yet-reviewed-to-completion kernel infra.

**Effort/risk:** substantially lower than (a) — porting/adapting ~2100
lines of already-designed, already-partially-working Rust against a
same-family, ancestor-related base is a real port, not a research
project. Still nontrivial: expect conflicts in `error.rs`/`types.rs`
(both PR-touched and touched independently by this project's own recent
commits, e.g. `error.rs` is also in the `debugfs` module's touch history),
and the PR's own `folio`/`mem_cache`/`buffer` additions would need their
own soundness review before trusting them in this project's boot path.
Meaningfully more tractable than (a), but still a multi-session
infrastructure project in its own right, not a quick pull-in.

### (c) Standalone, not-yet-integrated `shmem.c` translation

Checked `mm/shmem.c` directly: **5963 lines, ~197 top-level functions**,
and heavily entangled with core mm machinery — a grep for
`swap_|struct inode|struct address_space|struct page|folio` inside the
file returns **641 hits**. This is not a `lib/`-style pure-function file;
it's core kernel plumbing that assumes the surrounding VFS/mm/swap
subsystems exist and is deeply interleaved with them (swap-out paths,
`address_space_operations` callbacks, seal/quota bookkeeping per
`struct shmem_inode_info` in `include/linux/shmem_fs.h`). Translating it
"standalone" the way the 8250 scoping doc translated three pure
register-bit helper functions is much harder here: 8250 had isolable
Tier-A pure functions inside an otherwise-stateful file; a first skim of
`shmem.c` does not show an obviously equivalent pure-arithmetic island —
most of its logic is inode/folio/swap state manipulation by construction
(it *is* the state manipulation). A first slice here would likely be
something narrow like: the seal-flag bitmask logic (`SHMEM_F_*` constants
and their transition rules), or a small parsing helper (e.g. mount-option
parsing, if `shmem.c` has one separable from `fs_parser` framework calls)
— genuinely worth a **separate, dedicated** scoping pass (this doc's
budget didn't extend to fully enumerating shmem.c's ~197 functions
function-by-function the way the 8250 doc did for its ~150), not a
guess made here.

**Effort/risk:** lowest risk of the three (produces no boot-path change,
same "prove the C logic translates faithfully" value as the diff-oracle
pattern used elsewhere), but also lowest direct value toward an actual
mountable tmpfs — it's translation practice on logic that has nowhere to
attach regardless of (a)/(b)'s outcome, and unlike 8250 (which has a clear
integration target the moment Tier A/B/C are done), a `shmem.c`
translation's integration target does not exist until (a) or (b) lands.
It also risks being a bigger undertaking than it looks: 197 functions
across 5963 lines with 641 mm-internal touchpoints is a large first slice
to even scope, let alone translate — this doc recommends a follow-up
scoping pass in the same style as the 8250 doc (tier the functions by
translation risk/isolation) before committing to it, rather than treating
"translate shmem.c" as pre-scoped by this document.

## 3. Recommendation

**Not ready for a translation TU today.** Do not start "translate and
wire in tmpfs" — the scaffolding it would attach to does not exist in
this project's kernel crate, and building that scaffolding from scratch
(option a) is a multi-year-scale undertaking judging by upstream's own
timeline (PR #1037 has been open since 2023 and still hasn't landed even
in `rust-next`).

**Recommended next step if this track is picked up: option (b), narrowly
scoped as an evaluation, not immediately as a full port.** The version-gap
concern that motivated checking option (b) turned out to be smaller than
expected — PR #1037's base is a direct ancestor of this project's current
`linux-riscv` HEAD, about a month back, not a multi-year divergent fork.
That makes "adapt the RFC" a genuinely different risk profile than "port
something built against an old, incompatible kernel." A reasonable first
TU in this direction would be: fetch PR #1037's current diff, attempt a
mechanical rebase onto this project's HEAD in an isolated branch (no
`.config` changes, no boot-path involvement), and report what actually
conflicts and how large the resolution is — that single data point would
turn most of this doc's estimates from "assessed from the PR's metadata"
into "verified by attempting it," and would produce a much better-informed
go/no-go than anything achievable without touching the code.

**Do not pursue (c) as currently scoped** — it's low-risk but its value
is speculative until (a)/(b) gives it somewhere to attach, and 5963
lines/197 functions is too large a surface to hand-wave as "a first
slice" the way this doc's §2(c) had to. If (c) is wanted anyway (e.g. as
parallel, independent-of-(a)/(b) translation practice), it needs its own
8250-style tiering pass first, scoped separately from this doc.

**Priority relative to other open work:** lower than both the 8250 driver
work (P2, which at least has a clear existing attachment point in
`struct uart_ops`) and any open c2rust-track fixes. This is infrastructure
research, not incremental progress on a boot-critical or already-attached
subsystem — appropriately P3/P4 (see work_items entry added alongside
this doc).
