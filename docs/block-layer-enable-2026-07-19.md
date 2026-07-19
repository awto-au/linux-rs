# Block layer enable — CONFIG_BLOCK + virtio-blk, 2026-07-19

Follow-up to `docs/block-layer-sync-scoping-2026-07-19.md` (confirmed:
Rust block-mq + rnull already in-tree, gap was Kconfig-only). This turns
it on and proves a real block device is reachable.

Worktree: `linux-riscv-worktrees/block-layer-enable` (branch
`agent-block-layer-enable`, from `linux-rs/phase2-gcd`), uncommitted
per convention — matches `combined-c2rust-boot-*` pattern.

## 1. Kconfig

```
$ LINUXRS_TREE=linux-riscv-worktrees/block-layer-enable dev.py config \
    -e CONFIG_BLOCK -e CONFIG_VIRTIO -e CONFIG_VIRTIO_MMIO -e CONFIG_VIRTIO_BLK
```

First pass left `VIRTIO_MMIO`/`VIRTIO_BLK` off — both live under
`menuconfig VIRTIO_MENU` (`drivers/virtio/Kconfig`), which was also
off, so `olddefconfig` dropped them despite the direct `-e`. Second
pass added `-e CONFIG_VIRTIO_MENU`. Final `.config`:

```
CONFIG_BLOCK=y
CONFIG_BLK_DEV=y
CONFIG_VIRTIO=y
CONFIG_VIRTIO_MENU=y
CONFIG_VIRTIO_MMIO=y
CONFIG_VIRTIO_BLK=y
# CONFIG_BLK_DEV_RUST_NULL not set   (base step; enabled later, see §4)
```

## 2. Build

`LINUXRS_TREE=linux-riscv-worktrees/block-layer-enable dev.py build` ->
`BUILD OK`. Rust still linked (92 `_rs`-suffixed symbols via
`llvm-nm vmlinux`); `virtio_blk_init`/`virtio_blk_fini`/`virtio_blk`
present (C driver).

## 3. Tooling: `boot_qemu.py --qemu-extra`

`boot_qemu.py` had `--append` (kernel cmdline) but no raw-QEMU-args
passthrough. Added `--qemu-extra "..."` (shlex-split, appended to the
`qemu-system-riscv64` argv) — additive only, unused by every existing
caller (`dev.py boot()`/`check()` don't pass it), so default argv is
byte-identical. Verified before committing: plain
`boot_qemu.py --tree linux-riscv-worktrees/combined-c2rust-boot-11
--run-id qemu-extra-regression-check` (no `--qemu-extra`) against an
already-built sibling worktree -> 17 ok / 0 not-ok / INIT REACHED,
identical to prior boot-history rows. Committed to main linux-rs
directly (scripts/boot_qemu.py).

## 4. Boot evidence — virtio-blk

Disk: `qemu-img create -f raw tmp/block-test.img 16M`. Boot:

```
boot_qemu.py --tree linux-riscv-worktrees/block-layer-enable \
  --run-id block-layer-enable \
  --qemu-extra "-drive file=tmp/block-test.img,format=raw,if=none,id=blk0 \
                -device virtio-blk-device,drive=blk0"
```

dmesg:

```
00000.166 virtio_blk virtio0: 1/0/0 default/read/poll queues
00000.169 virtio_blk virtio0: [vda] 32768 512-byte logical blocks (16.8 MB/16.0 MiB)
```

32768 * 512 = 16 MiB — exact match to the backing image. Oracle:
17 ok / 0 not-ok / INIT REACHED, identical suite count to baseline
(`rust_kernel_str`, `rust_kernel_kunit`, `rust_kernel_bitmap`,
`rust_kernel_bitfield`, `rust_kvec`, `rust_allocator`, `rust_atomics`,
`math-gcd`, `math-int_log`, `math-int_pow`, `math-int_sqrt`,
`rational`, `bitops`, `cmdline`, `list_sort`, `lib_sort`,
`rust_8250_mem_serial_io`). `CONFIG_BLOCK=y` did not regress the
existing boot/KUnit/initramfs oracle. Archived log:
`docs/status/boot-logs/20260719T133829+1000-block-layer-enable.log`.

## 5. Stretch goal — CONFIG_BLK_DEV_RUST_NULL: reached

```
dev.py config -e CONFIG_CONFIGFS_FS -e CONFIG_BLK_DEV_RUST_NULL
```

(`CONFIGFS_FS` is rnull's other hard dependency, was off.) Both
landed `=y` (built-in, not module) in one pass — no menu-gate surprise
this time. Rebuild -> `BUILD OK`. Reboot (same virtio-blk `-drive`
attached):

```
00000.174 virtio_blk virtio0: 1/0/0 default/read/poll queues
00000.177 virtio_blk virtio0: [vda] 32768 512-byte logical blocks (16.8 MB/16.0 MiB)
00000.186 rnull_mod: Rust null_blk loaded
```

`rnull_mod: Rust null_blk loaded` is `rnull.rs`'s own
`pr_info!("Rust null_blk loaded\n")` in `NullBlkModule::init` —
confirms the Rust block-mq module actually probed at kernel init, not
just compiled. `llvm-nm vmlinux` also shows mangled `rnull`-crate
symbols (`NullBlkDevice`, `GenDiskBuilder::build`, `configfs::*`).
No default disk is created at init (rnull builds devices on-demand via
configfs, not exercised here — out of scope, base probe message is
the evidence requested). Oracle unchanged: 17 ok / 0 not-ok / INIT
REACHED. Archived log:
`docs/status/boot-logs/20260719T133921+1000-block-layer-enable-rnull.log`.

## 6. Conclusion

Both the C virtio-blk path and the Rust block-mq (`rnull`) path are
now provably reachable in this tree, purely via `.config` + one
additive tooling flag — zero Rust/C code changes needed, matching the
scoping doc's finding that the abstraction layer was already
complete. This retires the block-layer abstraction-layer risk for the
ext4-in-Rust roadmap; ext4 itself still needs its own scoping (far
larger surface than `rnull`).

Nothing broken/unexpected found — no issue filed.
