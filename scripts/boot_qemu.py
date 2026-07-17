#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Boot the riscv64 kernel in QEMU with the boot log at a stable path.

Always writes the full serial log to tmp/qemu-boot.log (truncated per run),
so `tail -f tmp/qemu-boot.log` works for every run. Exits when the kernel
panics (expected: no init) or after QEMU exits.

Passes -initrd so the kernel actually reaches a real /init (see
scripts/build_initramfs.py) instead of the deliberate no-rootfs panic —
this is the primary way init/do_mounts.c's mount/rootfs-discovery path
gets exercised at all in this project. -initrd (boot-time flag) rather
than baking the cpio into the kernel image via CONFIG_INITRAMFS_SOURCE:
keeps kernel and rootfs rebuilds independent, matching how dev.py
already treats `build` and `boot` as separate steps.

Usage: boot_qemu.py [--tree linux-riscv] [--append "extra args"]
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "qemu-boot.log"
INITRD = REPO / "tmp" / "initramfs" / "initramfs.cpio.gz"

# Printed by configs/initramfs-init.sh once /init actually runs as PID 1 —
# the milestone that real userspace (not just KUnit-in-kernel-space) was
# reached. Kept distinct from the "ok N ..." KUnit line shape so the two
# detectors can never collide.
INIT_REACHED = "linux-rs: initramfs init reached, PID 1 alive"


def ensure_initramfs() -> Path:
    """Build tmp/initramfs/initramfs.cpio.gz if missing or stale (see
    build_initramfs.py's own mtime check against configs/initramfs-init.sh
    and the cached busybox binary) — cheap no-op on the common case where
    nothing under configs/initramfs-init.sh changed since the last boot."""
    subprocess.run([sys.executable, str(REPO / "scripts" / "build_initramfs.py")],
                   check=True)
    return INITRD


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="linux-riscv")
    ap.add_argument("--append", default="")
    args = ap.parse_args()

    image = REPO / args.tree / "arch/riscv/boot/Image"
    if not image.exists():
        print(f"no kernel image at {image}", file=sys.stderr)
        return 1
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    initrd = ensure_initramfs()
    # console=ttyS0: registers the QEMU virt board's ns16550a-compatible
    # UART (CONFIG_SERIAL_8250, enabled alongside CONFIG_BLK_DEV_INITRD)
    # as the actual system console — earlycon=sbi alone only covers early
    # boot messages and is torn down once a real console driver probes,
    # so without this /init's busybox echo has nowhere to go and the
    # kernel logs "unable to open an initial console".
    cmdline = "earlycon=sbi console=ttyS0 panic=-1 " + args.append
    cmd = [
        "qemu-system-riscv64", "-M", "virt", "-m", "256M",
        "-nographic", "-no-reboot",
        "-kernel", str(image),
        "-initrd", str(initrd),
        "-append", cmdline.strip(),
    ]
    print(f"booting {image}\ninitrd: {initrd}\nlog: {LOG}")
    with open(LOG, "w") as log:
        log.write(f"# {' '.join(cmd)}\n")
        log.flush()
        rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
    # Summarise the KUnit result lines for the terminal — this remains the
    # primary pass/fail signal (dev.py's boot()/check() parse these same
    # "ok "/"not ok " lines from tmp/qemu-boot.log; nothing here changes
    # that gate). The init-reached line is additional, supplementary
    # confirmation that userspace was reached, printed alongside it.
    text = LOG.read_text(errors="replace")
    for line in text.splitlines():
        if line.startswith(("ok ", "not ok ")) or "# Totals:" in line \
                or "Kernel panic" in line:
            print(line)
    if INIT_REACHED in text:
        print(INIT_REACHED)
    else:
        print("WARNING: init-reached milestone not seen in boot log "
              "(userspace/initramfs coverage did not run this boot)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
