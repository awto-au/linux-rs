#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Boot the riscv64 kernel in QEMU with the boot log at a stable path.

Always writes the full serial log to tmp/qemu-boot.log (truncated per run),
so `tail -f tmp/qemu-boot.log` works for every run. Exits when the kernel
panics (expected: no init) or after QEMU exits.

Usage: boot_qemu.py [--tree linux-riscv] [--append "extra args"]
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "qemu-boot.log"


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
    cmdline = "earlycon=sbi panic=-1 " + args.append
    cmd = [
        "qemu-system-riscv64", "-M", "virt", "-m", "256M",
        "-nographic", "-no-reboot",
        "-kernel", str(image),
        "-append", cmdline.strip(),
    ]
    print(f"booting {image}\nlog: {LOG}")
    with open(LOG, "w") as log:
        log.write(f"# {' '.join(cmd)}\n")
        log.flush()
        rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
    # Summarise the KUnit result lines for the terminal.
    for line in LOG.read_text(errors="replace").splitlines():
        if line.startswith(("ok ", "not ok ")) or "# Totals:" in line \
                or "Kernel panic" in line:
            print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
