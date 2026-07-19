# Block layer (blk-mq Rust) — sync scoping

Status: **research only.** No changes to `linux-riscv/`. Read-only commands
(`git log`, `git show`, `cat`, `grep`) only, per instruction. Matches the
rigor bar of `docs/tmpfs-rust-scoping-2026-07-18.md`.

**Headline finding: the task's stated premises are stale.** Real
block-mq Rust support (`rust/kernel/block/mq.rs` + `drivers/block/rnull/`)
is **already present in this project's `linux-riscv/` tree today**, fully
wired into `Kconfig`/`Makefile`. There is no version gap to close. The
`rust/kernel/block.rs` "537-byte stub" is real (537 bytes, confirmed) but
it is not a stub missing `mq` — it's a thin re-export module that does
`pub mod mq;` and nothing else needs adding at that layer. The actual gap
is 100% `.config`, not code.

## 1. Current state, verified directly

```
$ git -C linux-riscv log -1
commit 04312ea1ff7e1ccd53db1c62edb5929f2b4daad5
Date:   Sun Jul 19 13:05:05 2026 +1000
    riscv: wire riscv-march-y's Zacas/Zabha into KBUILD_RUSTFLAGS (closes linux-rs#29)

$ git -C linux-riscv describe --tags
v7.2-rc3-454-g04312ea1ff7e

$ git -C linux-riscv log -1 --format="%H %ci %s" v7.2-rc3
a13c140cc289c0b7b3770bce5b3ad42ab35074aa 2026-07-12 14:16:39 -0700 Linux 7.2-rc3
```

HEAD is 454 commits past the real `v7.2-rc3` mainline tag (2026-07-12),
dated 2026-07-19 — a **7-day-old sync**, not a stale fork. `git remote -v`
shows `upstream = torvalds/linux.git`; recent merge commits in that
454-commit range are dated up to 2026-07-17/18 (`bpf-fixes`,
`selinux-pr-20260717`, `net-7.2-rc4`) — this tree tracks mainline
directly and continuously, it isn't behind a slow-moving downstream fork.

```
$ wc -c linux-riscv/rust/kernel/block.rs
537 linux-riscv/rust/kernel/block.rs

$ cat linux-riscv/rust/kernel/block.rs
pub mod mq;
pub const SECTOR_MASK: u32 = bindings::SECTOR_MASK;
pub const SECTOR_SHIFT: u32 = bindings::SECTOR_SHIFT;
pub const SECTOR_SIZE: u32 = bindings::SECTOR_SIZE;
pub const PAGE_SECTORS_SHIFT: u32 = bindings::PAGE_SECTORS_SHIFT;
```
(comments elided; full file is exactly this small, and that's normal —
it's a re-export shim, not where the logic lives.)

```
$ ls linux-riscv/rust/kernel/block/mq/
gen_disk.rs (8.7K)  operations.rs (12K)  request.rs (10K)  tag_set.rs (2.8K)
$ wc -l linux-riscv/rust/kernel/block/mq.rs
... (module doc + re-exports: Operations, Request, TagSet, gen_disk)
```

`rust/kernel/block/mq/` has the full blk-mq abstraction: `TagSet`,
`GenDisk`/`GenDiskBuilder`, `Request`, the `Operations` vtable trait —
matching exactly what the task description expected to find *missing*.

```
$ find linux-riscv/drivers/block/rnull -type f
drivers/block/rnull/rnull.rs    (2.8K)
drivers/block/rnull/configfs.rs (6.8K)
drivers/block/rnull/Kconfig     (423B)
drivers/block/rnull/Makefile    (71B)

$ grep -n rnull linux-riscv/drivers/block/Kconfig linux-riscv/drivers/block/Makefile
drivers/block/Kconfig:20:source "drivers/block/rnull/Kconfig"
drivers/block/Makefile:38:obj-$(CONFIG_BLK_DEV_RUST_NULL) += rnull/
```

`rnull.rs` imports from this tree's own `kernel::block::mq` module
(`gen_disk::GenDisk`, `Operations`, `TagSet`) — the driver and the
abstraction it depends on are the same commit-family, already coherent.
Both `Kconfig` and `Makefile` wiring are present and correct.

## 2. Where this landed in history — not "ahead," already absorbed

```
$ git -C linux-riscv log --oneline --diff-filter=A -- rust/kernel/block/mq.rs
3253aba3408a rust: block: introduce `kernel::block::mq` module

$ git -C linux-riscv log -1 --format=%ci 3253aba3408a
2024-06-14 07:45:04 -0600

$ git -C linux-riscv log --oneline --diff-filter=A -- drivers/block/rnull/rnull.rs
edd8650691c3 rnull: move driver to separate directory   (2025-09-02)

$ git -C linux-riscv log --oneline -- rust/kernel/block.rs rust/kernel/block/ drivers/block/rnull/ | head -5
2957771379fa rust: block: fix GenDisk cleanup paths
6b2f3e4970e4 rust: block: mq: align init_request numa_node arg with C signature
961b72d45ae4 rust: block: update `const_refs_to_static` MSRV TODO comment
8c0901b6f9c8 Merge tag 'configfs-for-v7.0' of .../a.hindborg/linux
b2b2ce870651 block: rnull: remove imports available via prelude
```

blk-mq's Rust module was introduced upstream **2024-06-14** (over a year
before this project's current `v7.2-rc3` base). It has been part of
mainline `torvalds/linux` — not just `Rust-for-Linux/linux` — for that
entire time, and this project's tree has carried it since whenever it
first synced past that point. There is no commit/tag distance to
measure between "this project's base" and "block-mq lands" because the
former is already downstream of the latter by over a year.

## 3. Divergence / entanglement check (§2 of the task)

Not a version-bump feasibility question — there's no bump needed. But
checked anyway in case the *concept* generalizes to "how entangled is
this project's own work with core-tree churn":

```
$ git -C linux-riscv rev-list --count HEAD
1463084
$ git -C linux-riscv rev-list --count v7.2-rc3..HEAD
454
$ git -C linux-riscv log --oneline v7.2-rc3..HEAD --author="dan@awto.au" | wc -l
40
```

Of the 454 commits since `v7.2-rc3`, 40 are this project's own
(dan@awto.au-authored translation/build-plumbing commits — 8250 Tier
B/C, `lib/` TUs, RISC-V Zacas/Zabha wiring); the remaining 414 are
upstream fix/merge commits pulled in by the ongoing sync (bpf-fixes,
selinux, net, mtd, mmc, soc, powerpc, can, bluetooth — routine `-rc`
churn, none touching `rust/kernel/block*` or `drivers/block/rnull*`).
None of this project's own 40 commits touch block/blk-mq/rnull paths
(confirmed via the file-scoped `git log` above — only upstream commits
appear in that history). **Zero entanglement**, because there is nothing
to sync — the files are already at current mainline shape.

## 4. Kconfig gap — the real (only) blocker

```
$ grep -E "CONFIG_BLOCK|CONFIG_MQ_IOSCHED|CONFIG_VIRTIO" linux-riscv/.config
CONFIG_BLK_DEV_INITRD=y
# CONFIG_BLOCK is not set
# CONFIG_VIRTIO_CONSOLE is not set
# CONFIG_VIRTIO_MENU is not set
# CONFIG_RPMSG_VIRTIO is not set
```

Dependency chain, read directly from Kconfig sources (not inferred):

```
$ head -12 linux-riscv/drivers/block/Kconfig
menuconfig BLK_DEV
	bool "Block devices"
	depends on BLOCK
	...
if BLK_DEV
source "drivers/block/null_blk/Kconfig"
source "drivers/block/rnull/Kconfig"
...

$ cat linux-riscv/drivers/block/rnull/Kconfig
config BLK_DEV_RUST_NULL
	tristate "Rust null block driver (Experimental)"
	depends on RUST && CONFIGFS_FS
	...

$ grep -n "^config VIRTIO_BLK" -A4 linux-riscv/drivers/block/Kconfig
config VIRTIO_BLK
	tristate "Virtio block driver"
	depends on VIRTIO
	select SG_POOL

$ grep -n "^config VIRTIO_MMIO" -A6 linux-riscv/drivers/virtio/Kconfig
config VIRTIO_MMIO
	tristate "Platform bus driver for memory mapped virtio devices"
	depends on HAS_IOMEM && HAS_DMA
	select VIRTIO
```

`CONFIG_BLOCK is not set` → `BLK_DEV` menu (and everything under it,
including `BLK_DEV_RUST_NULL`) is invisible/unselectable regardless of
Rust code being present. `CONFIG_VIRTIO_MENU is not set` → `VIRTIO_MMIO`
also currently off, and `VIRTIO_MMIO` is what QEMU's riscv64 `virt`
machine actually exposes (not PCI — see §5). `CONFIG_RUST=y` is already
on, so no toolchain gap.

**This is purely a `.config` change, zero Rust code involved**:
`CONFIG_BLOCK=y` → `CONFIG_BLK_DEV=y` → `CONFIG_VIRTIO=y` +
`CONFIG_VIRTIO_MMIO=y` → `CONFIG_VIRTIO_BLK=y`, and optionally
`CONFIG_BLK_DEV_RUST_NULL=y` + `CONFIG_CONFIGFS_FS=y` (rnull's other
dependency) for the pure-Rust test device instead of/alongside
virtio-blk. `CONFIG_MQ_IOSCHED_DEADLINE`/`CONFIG_MQ_IOSCHED_KYBER` exist
in `block/Kconfig.iosched`, gated the same way (under `if BLOCK`),
optional (blk-mq works with `none` scheduler).

This directly confirms the task's §3 question: **yes, turning on the
block layer is fully orthogonal to whether the Rust wrappers exist.**
The wrappers have existed in-tree since before this project forked its
current base; nobody has flipped the Kconfig bits yet.

## 5. QEMU riscv64 `virt` machine — virtio-blk support

```
$ qemu-system-riscv64 --version
QEMU emulator version 10.2.2 (qemu-10.2.2-1.fc44)

$ qemu-system-riscv64 -M virt -device help 2>&1 | grep -i virtio-blk
name "virtio-blk-device", bus virtio-bus
name "virtio-blk-pci", bus PCI, alias "virtio-blk"
name "virtio-blk-pci-non-transitional", bus PCI
name "virtio-blk-pci-transitional", bus PCI
```

`virtio-blk-device` (the MMIO-transport variant, matching
`CONFIG_VIRTIO_MMIO` above — riscv64 `virt` wires virtio via MMIO slots
by convention, not PCI) is available in this installed QEMU. Confirmed
current boot invocation has no block device at all:

```
$ grep -n "virtio\|-drive\|-device" scripts/boot_qemu.py
(no matches)

$ sed -n '183,189p' scripts/boot_qemu.py
cmd = [
    "qemu-system-riscv64", "-M", "virt", "-m", "256M",
    "-nographic", "-no-reboot",
    "-kernel", str(image),
    "-initrd", str(initrd),
    "-append", cmdline.strip(),
]
```

Adding a testable block device is a `scripts/boot_qemu.py` change
(`-drive file=...,if=none,id=hd0 -device virtio-blk-device,drive=hd0`)
plus a backing file — orthogonal to and independent of both the Kconfig
change and any kernel rebuild. Not attempted here (task is read-only
scoping); flagged as the concrete follow-up command shape.

## 6. Recommendation

**No version sync is needed or possible to "attempt" — there is nothing
to sync.** The task's premise (this tree is behind upstream's real
block-mq work) does not hold under direct inspection; block-mq has been
present, complete, and Kconfig-wired in this tree the entire time this
project has been building against `v7.2-rc3`+. Whatever produced the
537-byte/no-mq.rs impression either checked a stale snapshot or
misread `block.rs`'s small size as "stub" without checking `block/mq/`
next to it.

**Smallest safe first step, in order:**
1. `.config` only: `CONFIG_BLOCK=y`, `CONFIG_BLK_DEV=y`, `CONFIG_VIRTIO=y`,
   `CONFIG_VIRTIO_MMIO=y`, `CONFIG_VIRTIO_BLK=y`. Rebuild, confirm boot
   still reaches init (regression check — enabling the block layer
   touches core init paths like `mm/shmem.o`'s neighbors and
   `init/do_mounts.c`, worth a dedicated boot-history row).
2. Add `-drive .../-device virtio-blk-device` to `boot_qemu.py` (behind
   a flag, not the default invocation, to keep existing boot-history
   rows comparable) and confirm the device enumerates
   (`/sys/block/vda` or equivalent, or a dmesg probe line) — this is
   the "does the block layer actually see a disk" checkpoint, achievable
   with **zero Rust work**, pure C block layer + virtio-blk (the C
   driver, not rnull).
3. Only after (1)+(2) prove the block layer boots clean: optionally
   enable `CONFIG_BLK_DEV_RUST_NULL=y` + `CONFIG_CONFIGFS_FS=y` to
   exercise the already-present Rust `rnull` driver specifically — this
   is the first point where Rust block-mq code actually runs, and it's
   gated behind two purely-C prerequisite steps, not the other way
   round.

None of steps 1-3 involve touching `rust/kernel/block*` or
`drivers/block/rnull/*` — both are already correct and current. This
significantly *de-risks* the ext4-in-Rust path relative to the tmpfs
scoping doc's findings: block-mq's Rust abstraction layer is a solved,
already-in-tree problem, unlike VFS/superblock/inode (still an unmerged
RFC per `docs/tmpfs-rust-scoping-2026-07-18.md`). ext4 itself would
still need its own scoping (a real filesystem is far larger than
`rnull`), but the abstraction-layer risk this doc was asked to assess is
already retired.
