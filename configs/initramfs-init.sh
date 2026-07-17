#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
#
# PID 1 for the QEMU riscv64 boot's minimal initramfs. Packaged into
# initramfs.cpio.gz by scripts/build_initramfs.py as /init, which the
# kernel execs directly (do_mounts.c's prepare_namespace() looks for
# /init in the initramfs before falling back to /sbin/init, /etc/init,
# /bin/init, /bin/sh on a real root device) — this is what actually
# exercises init/do_mounts.c's early-userspace-transition path for the
# first time in this project, rather than the deliberate no-rootfs panic.
#
# busybox is statically linked (built via a riscv64-linux-musl-cross
# toolchain — the host's own busybox is x86-64 and can't run under the
# riscv64 guest), so no dynamic loader or /lib is needed in the image.
# No symlink forest is installed (would need writes at cpio-build time
# for every applet); instead every call below spells out
# "busybox <applet>", which busybox's own argv[0] dispatch handles
# identically to a symlink invocation.

# devtmpfs mounted first so /dev/console (needed below) exists: the
# kernel's own console_on_rootfs() (init/main.c) opens /dev/console for
# PID 1's stdin/stdout/stderr *before* running /init, but CONFIG_
# DEVTMPFS_MOUNT's automatic mount only fires from prepare_namespace()'s
# real-rootdev path — which an initramfs-satisfied boot
# (init_eaccess("/init") succeeds) skips entirely, do_mounts.c never
# runs it. So /dev/console doesn't exist yet when console_on_rootfs()
# runs ("Warning: unable to open an initial console" is expected and
# harmless here, not a bug to chase), and PID 1 is exec'd with fds
# 0/1/2 already closed. devtmpfs's initial population creates
# /dev/console synchronously as part of the mount() call itself.
/bin/busybox mount -t devtmpfs devtmpfs /dev

# `exec` with only redirections (no command) replaces *this* shell's own
# fds in place, fixing stdin/stdout/stderr for every command below.
# Needs CONFIG_SERIAL_OF_PLATFORM=y to actually work: without it the
# 8250 driver core loads but never binds to QEMU virt's devicetree-
# described ns16550a UART, ttyS0 never registers as a console, and
# opening /dev/console (which just redirects to whatever the registered
# console device is) fails with ENODEV ("No such device") — a real gap
# found via this exact boot path, not present in the original
# riscv64-slim-serial.defconfig because nothing before this ever needed
# a userspace-writable console.
exec 0<>/dev/console 1>/dev/console 2>&1

# /proc and /sys mounted here (not left to userspace convention) because
# this init has no further userspace to hand off to — this line IS the
# do_mounts.c coverage the boot verification is after.
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys

# Grepped by boot_qemu.py as the milestone that real userspace was
# reached — distinct text from any KUnit output so it can't collide with
# the existing "ok N ..." suite-line matching.
/bin/busybox echo "linux-rs: initramfs init reached, PID 1 alive"

# Clean shutdown via RISC-V SBI SRST (arch/riscv/kernel/sbi.c registers
# sbi_shutdown() as the platform power-off handler unconditionally when
# CONFIG_RISCV_SBI is on, independent of CONFIG_PM/CONFIG_POWER_RESET —
# neither is enabled in this project's slim config) — QEMU's virt
# machine honours the SBI shutdown call and exits the process, which is
# more deterministic than relying on boot_qemu.py's subprocess timeout.
/bin/busybox poweroff -f
