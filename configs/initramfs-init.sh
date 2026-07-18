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

# hybrid-boot-backwards (docs/streams.md stream 3): drop to a real
# interactive shell instead of powering off immediately, so there is
# finally a way to sit at a live console on this kernel. ash itself
# (CONFIG_SH_IS_ASH=y) is /bin/sh, so no extra applet/binary is needed.
#
# Bounded, not open-ended: boot_qemu.py's own QEMU subprocess.run() call
# (scripts/boot_qemu.py) has no timeout parameter, so a shell that waits
# forever for input would hang that subprocess.run() indefinitely any
# time nothing is typed — which is every run through dev.py check, the
# automated pipeline this project's boot-verification gate depends on.
# This busybox build has no line-editing support (CONFIG_FEATURE_EDITING
# is off — see build_initramfs.py's minimal BUSYBOX_OPTS list), so ash's
# own bash-like $TMOUT idle-auto-logout (ENABLE_ASH_IDLE_TIMEOUT) isn't
# available without a busybox rebuild: its read path only honours TMOUT
# through the line-editing reader (shell/ash.c's preadfd(), guarded by
# `if (!iflag || g_parsefile->pf_fd != STDIN_FILENO)` falling back to a
# plain blocking read with no timeout at all when editing is disabled).
# Rather than pull in that extra dependency, the bound is done directly
# in this script with a single `read -t N` — a real ash builtin
# (shell/ash.c's readcmd(), always compiled in, not gated behind any of
# the ASH_* Kconfig toggles this busybox build turns off) that blocks
# for real input but gives up after N seconds. Only one reader ever
# touches /dev/console at a time (unlike a background-watchdog-plus-
# foreground-shell design, which would race two readers over the same
# fd with no line-discipline arbitration between them and could steal a
# human's keystrokes into the wrong reader) — this first `read` owns the
# fd alone, and only after it returns does anything else read from
# /dev/console:
#   - input arrives before the timeout: a real interactive `sh -i` takes
#     over for everything after — genuinely open-ended, no further
#     timeout anywhere in that path, for a human driving
#     qemu-system-riscv64 by hand and watching -nographic's stdio
#     directly. (The one line already consumed by this gate's own `read`
#     is intentionally not replayed into that shell — piping a captured
#     first command into a fresh shell's stdin generically is future
#     work, not needed for "the console is real and stays up.")
#   - nothing arrives within N seconds (every unattended dev.py check
#     run — nothing feeds QEMU's stdin there): `read` times out and
#     falls through to the poweroff below, so the automated pipeline
#     still terminates on its own exactly as it did before this change,
#     just N seconds later instead of immediately.
#
# N=15: this project's own fresh-boot timings (kernel decompress through
# all 16 KUnit suites through INIT_REACHED) complete in well under a
# second of wall-clock QEMU execution — see docs/status/boot-history.csv.
# 15s is two orders of magnitude of slack on top of that for the prompt
# to be flushed and for a human to notice it and start typing, while
# staying comfortably below dev.py's own outer subprocess timeout
# (scripts/dev.py's sh() calls boot_qemu.py with timeout=600s) — so
# `dev.py check` still returns in seconds, not minutes, on the common
# no-input case, and never hangs indefinitely either way.
# printf is an ash *builtin* here (CONFIG_ASH_PRINTF=y), not a standalone
# applet (CONFIG_PRINTF is off in this minimal busybox build — see
# build_initramfs.py's BUSYBOX_OPTS) — must be called bare, the same way
# `read` below is, not as "/bin/busybox printf" (which 404s: "applet not
# found").
/bin/busybox echo "linux-rs: dropping to shell (/ #), 15s idle timeout then poweroff"
printf '/ # '
if read -t 15 _unused; then
	exec /bin/sh -i
fi
/bin/busybox echo
/bin/busybox echo "linux-rs: no console input within 15s, powering off"

# Clean shutdown via RISC-V SBI SRST (arch/riscv/kernel/sbi.c registers
# sbi_shutdown() as the platform power-off handler unconditionally when
# CONFIG_RISCV_SBI is on, independent of CONFIG_PM/CONFIG_POWER_RESET —
# neither is enabled in this project's slim config) — QEMU's virt
# machine honours the SBI shutdown call and exits the process, which is
# more deterministic than relying on boot_qemu.py's subprocess timeout.
/bin/busybox poweroff -f
