#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Tier-2.5b riscv64-emulated differential oracle: cross-compile the same
bench/diff_<name>.{c,rs} pair used by diff_oracle.py for real riscv64
(rustc's self-contained rust-lld for the Rust side, the project's cached
musl cross-gcc for the C side), execute both under qemu-riscv64-static
usermode emulation, diff outputs byte-for-byte, and report side by side
with diff_oracle.py's existing host-native verdict.

Per docs/target-compile-test-scoping-2026-07-18.md §5-6: this is NOT a
kernel boot and does NOT touch linux-riscv/ or boot_qemu.py's guest-boot
path. It cross-compiles two standalone host-side binaries and runs them
as single riscv64 ELF processes under usermode QEMU (qemu-riscv64-static)
— sub-second per binary, no kernel image, no initramfs, no 256MB budget.

Toolchain (verified live, §5):
  Rust: rustc --target riscv64gc-unknown-linux-musl -C target-feature=
        +crt-static -C link-self-contained=on -C linker-flavor=ld.lld
        (self-contained via rustc's own bundled rust-lld — sidesteps a
        real ISA-version link incompatibility between rustc's prebuilt
        riscv64gc-unknown-linux-musl static libs and the cached musl.cc
        cross toolchain's older binutils; do not switch this to the cross
        gcc as the linker without re-reading §5).
  C:    <riscv64-linux-musl-cross>/bin/riscv64-linux-musl-gcc, static —
        unaffected by the above, ordinary musl-gcc cross build.

Usage: target_compile_test.py <target>  (e.g. bcd, win_minmax — same
                                          <target> diff_oracle.py takes)
Log: tmp/target_compile_test.log
"""
import logging
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = REPO / "tmp"
LOG = TMP / "target_compile_test.log"
N = "5000"
SEED = "424242"

RISCV_GCC = REPO / "tmp" / "initramfs" / "riscv64-linux-musl-cross" / "bin" / "riscv64-linux-musl-gcc"
QEMU_RISCV64 = "qemu-riscv64-static"


def sh(cmd):
    logging.info("$ %s", " ".join(map(str, cmd)))
    return subprocess.run(cmd, check=True, text=True, capture_output=True).stdout


def diff_lines(c_out: str, rs_out: str):
    c_lines = c_out.splitlines()
    rs_lines = rs_out.splitlines()
    mismatches = []
    for i, (c, r) in enumerate(zip(c_lines, rs_lines)):
        if c != r:
            mismatches.append((i, c, r))
    if len(c_lines) != len(rs_lines):
        mismatches.append(("length", len(c_lines), len(rs_lines)))
    return c_lines, rs_lines, mismatches


def run_host_native(target: str, c_src: Path, rs_src: Path):
    """Reuses diff_oracle.py's own backend so both verdicts come from one
    process/run rather than shelling out to a second script and re-parsing
    its output."""
    sh(["clang", "-O1", "-o", str(TMP / f"diff_{target}_c"), str(c_src)])
    sh(["rustc", "-O", "--edition=2021", "-o", str(TMP / f"diff_{target}_rs"), str(rs_src)])
    c_out = sh([str(TMP / f"diff_{target}_c"), N, SEED])
    rs_out = sh([str(TMP / f"diff_{target}_rs"), N, SEED])
    return diff_lines(c_out, rs_out)


def run_riscv64_emulated(target: str, c_src: Path, rs_src: Path):
    if not RISCV_GCC.exists():
        logging.error(
            "riscv64 musl cross-gcc not found at %s — run scripts/build_initramfs.py "
            "first to fetch/cache it (see its ensure_toolchain())", RISCV_GCC)
        raise FileNotFoundError(str(RISCV_GCC))
    if shutil.which(QEMU_RISCV64) is None:
        logging.error("%s not found on PATH — expected from qemu-user-static-riscv package", QEMU_RISCV64)
        raise FileNotFoundError(QEMU_RISCV64)

    c_bin = TMP / f"target_{target}_c_riscv64"
    rs_bin = TMP / f"target_{target}_rs_riscv64"

    sh([str(RISCV_GCC), "-O1", "-static", "-o", str(c_bin), str(c_src)])
    sh([
        "rustc", "--target", "riscv64gc-unknown-linux-musl",
        "-C", "target-feature=+crt-static",
        "-C", "link-self-contained=on",
        "-C", "linker-flavor=ld.lld",
        "-O", "--edition=2021",
        "-o", str(rs_bin), str(rs_src),
    ])

    c_out = sh([QEMU_RISCV64, str(c_bin), N, SEED])
    rs_out = sh([QEMU_RISCV64, str(rs_bin), N, SEED])
    return diff_lines(c_out, rs_out)


def report(label: str, c_lines, rs_lines, mismatches) -> bool:
    logging.info("%s: C: %d output lines, Rust: %d output lines", label, len(c_lines), len(rs_lines))
    if mismatches:
        logging.error("%s FAIL: %d mismatches (of %d cases)", label, len(mismatches), len(c_lines) // 2)
        for i, c, r in mismatches[:10]:
            logging.error("  #%s\n    C:  %s\n    Rs: %s", i, c, r)
        return False
    logging.info("%s PASS: %d cases, %d output lines, byte-identical",
                 label, len(c_lines) // 2, len(c_lines))
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: target_compile_test.py <target>  (e.g. bcd, win_minmax)")
        return 1
    target = sys.argv[1]
    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    c_src = REPO / "bench" / f"diff_{target}.c"
    rs_src = REPO / "bench" / f"diff_{target}.rs"
    if not c_src.exists() or not rs_src.exists():
        logging.error("missing bench/diff_%s.{c,rs}", target)
        return 1

    host_c, host_rs, host_mismatches = run_host_native(target, c_src, rs_src)
    host_ok = report("ORACLE 2.5 (host-native)", host_c, host_rs, host_mismatches)

    riscv_c, riscv_rs, riscv_mismatches = run_riscv64_emulated(target, c_src, rs_src)
    riscv_ok = report("ORACLE 2.5b (riscv64-emulated)", riscv_c, riscv_rs, riscv_mismatches)

    logging.info("SUMMARY %s: host-native=%s (%d cases), riscv64-emulated=%s (%d cases)",
                 target,
                 "PASS" if host_ok else "FAIL", len(host_c) // 2,
                 "PASS" if riscv_ok else "FAIL", len(riscv_c) // 2)

    if host_ok != riscv_ok:
        logging.error(
            "BACKEND DISAGREEMENT: host-native and riscv64-emulated verdicts differ for "
            "%s — this is a genuinely new, actionable signal (possible architecture-"
            "dependent bug); investigate rather than treat as a harness flake.", target)
        return 1

    return 0 if (host_ok and riscv_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
