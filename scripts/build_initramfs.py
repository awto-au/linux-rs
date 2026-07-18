#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the minimal riscv64 initramfs that boot_qemu.py passes to QEMU
via -initrd, so init/do_mounts.c and the early-userspace-transition path
(prepare_namespace() -> exec /init) get exercised by a real PID 1 instead
of the deliberate no-rootfs panic this project boots into today.

Three cached, independently-rebuildable layers under tmp/initramfs/
(mirrors import_sparse.py's ensure_sparse_binary() "build once, reuse,
--rebuild to refresh" pattern — each layer is regenerable from its
inputs, so none of it is committed):

  1. riscv64-linux-musl-cross/  — a musl libc + gcc cross toolchain for
     riscv64, fetched from musl.cc. Needed because the host's own
     busybox (/usr/bin/busybox, /usr/bin/busybox.musl.static) is
     statically linked but built for x86_64 — it cannot run under the
     riscv64 QEMU guest. No riscv64 sysroot ships with the distro's
     riscv64-linux-gnu-gcc package (glibc, not statically-linkable
     without one), and no prebuilt riscv64 busybox binary exists on
     busybox.net's binaries mirror (only i686/x86_64), so cross-building
     from source against musl (trivially static, no sysroot hunting) is
     the simplest path that actually reaches a working binary.
  2. busybox (riscv64, static)  — built from busybox.net source against
     an allnoconfig + only the applets init actually calls (ash, mount,
     umount, poweroff, echo, cat, ls) rather than the full defconfig
     applet set: the defconfig pulls in x86-specific SHA-NI intrinsics
     (CONFIG_SHA1_HWACCEL) that don't exist for riscv64 and fail the
     build, and a minimal init has no use for the other ~300 applets
     anyway.
  3. initramfs.cpio.gz           — /init (from configs/initramfs-init.sh)
     plus /bin/busybox, packed as a newc-format cpio and gzipped
     (CONFIG_RD_GZIP=y is already on in linux-riscv/.config, alongside
     the other CONFIG_RD_* decompressors olddefconfig enables by default
     once CONFIG_BLK_DEV_INITRD=y is set).

-initrd (boot-time, kept separate from the kernel Image) rather than
CONFIG_INITRAMFS_SOURCE (baked in at kernel build time) because this
project's whole iterate-fast loop is "rebuild kernel" and "rebuild
rootfs" as separate, independently-cached steps (dev.py build vs. this
script) — baking the cpio into the kernel image would force a full
kernel rebuild any time /init's shell script changes, for no benefit.

Usage: build_initramfs.py [--rebuild-busybox] [--rebuild-toolchain]
Output: tmp/initramfs/initramfs.cpio.gz (consumed by boot_qemu.py)
Log: tmp/build_initramfs.log
"""
import argparse
import gzip
import hashlib
import logging
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = REPO / "tmp"
WORK = TMP / "initramfs"
LOG = TMP / "build_initramfs.log"

INIT_SRC = REPO / "configs" / "initramfs-init.sh"
CPIO_GZ = WORK / "initramfs.cpio.gz"

TOOLCHAIN_DIR = WORK / "riscv64-linux-musl-cross"
TOOLCHAIN_URL = "https://musl.cc/riscv64-linux-musl-cross.tgz"
TOOLCHAIN_TGZ = WORK / "riscv64-linux-musl-cross.tgz"
CROSS_PREFIX = "riscv64-linux-musl-"

BUSYBOX_VERSION = "1.37.0"
BUSYBOX_URL = f"https://busybox.net/downloads/busybox-{BUSYBOX_VERSION}.tar.bz2"
BUSYBOX_TARBALL = WORK / f"busybox-{BUSYBOX_VERSION}.tar.bz2"
BUSYBOX_SRC = WORK / f"busybox-{BUSYBOX_VERSION}"
BUSYBOX_BIN = WORK / "busybox"
BUSYBOX_OPTS_HASH = WORK / "busybox.opts-hash"

# Applets /init actually calls (see configs/initramfs-init.sh) plus the
# shell that execs it. Everything else stays off — see module docstring
# for why defconfig's full applet set fails to cross-build for riscv64.
BUSYBOX_OPTS = [
    "STATIC",              # no dynamic loader needed inside the initramfs
    "ASH",                 # CONFIG_SH_IS_ASH=y already makes this /bin/sh
    "ASH_ECHO", "ASH_PRINTF", "ASH_TEST", "ASH_GETOPTS",
    "MOUNT", "UMOUNT",
    "POWEROFF", "REBOOT", "HALT",
    "ECHO", "CAT", "LS",
]


def fetch(url: str, dest: Path):
    logging.info("fetching %s -> %s", url, dest)
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def ensure_toolchain(rebuild: bool) -> Path:
    cc = TOOLCHAIN_DIR / "bin" / f"{CROSS_PREFIX}gcc"
    if cc.exists() and not rebuild:
        logging.info("reusing existing toolchain at %s (pass --rebuild-toolchain to refresh)", cc)
        return TOOLCHAIN_DIR
    if TOOLCHAIN_DIR.exists():
        shutil.rmtree(TOOLCHAIN_DIR)
    WORK.mkdir(parents=True, exist_ok=True)
    if not TOOLCHAIN_TGZ.exists() or rebuild:
        fetch(TOOLCHAIN_URL, TOOLCHAIN_TGZ)
    logging.info("extracting toolchain")
    subprocess.run(["tar", "xzf", str(TOOLCHAIN_TGZ), "-C", str(WORK)], check=True)
    # musl.cc tarballs unpack as riscv64-linux-musl-cross/ already, but
    # guard against upstream layout changes rather than assume.
    if not cc.exists():
        raise RuntimeError(f"toolchain extracted but {cc} not found — layout changed?")
    return TOOLCHAIN_DIR


def opts_hash() -> str:
    return hashlib.sha1(
        (BUSYBOX_VERSION + "\x00" + "\x00".join(BUSYBOX_OPTS)).encode()
    ).hexdigest()


def ensure_busybox(toolchain_dir: Path, rebuild: bool) -> Path:
    if (BUSYBOX_BIN.exists() and not rebuild
            and BUSYBOX_OPTS_HASH.exists()
            and BUSYBOX_OPTS_HASH.read_text().strip() == opts_hash()):
        logging.info("reusing existing busybox at %s (pass --rebuild-busybox to refresh)", BUSYBOX_BIN)
        return BUSYBOX_BIN

    WORK.mkdir(parents=True, exist_ok=True)
    if not BUSYBOX_TARBALL.exists() or rebuild:
        fetch(BUSYBOX_URL, BUSYBOX_TARBALL)
    if BUSYBOX_SRC.exists():
        shutil.rmtree(BUSYBOX_SRC)
    logging.info("extracting busybox %s", BUSYBOX_VERSION)
    subprocess.run(["tar", "xjf", str(BUSYBOX_TARBALL), "-C", str(WORK)], check=True)

    env_prefix = f"PATH={toolchain_dir / 'bin'}:$PATH"
    cross_env = {"CROSS_COMPILE": CROSS_PREFIX}
    import os
    env = {**os.environ, **cross_env,
           "PATH": f"{toolchain_dir / 'bin'}{os.pathsep}{os.environ['PATH']}"}

    make_common = ["make", "-C", str(BUSYBOX_SRC), "ARCH=riscv", f"CROSS_COMPILE={CROSS_PREFIX}"]

    logging.info("busybox allnoconfig")
    subprocess.run([*make_common, "allnoconfig"], check=True, env=env,
                    capture_output=True, text=True)

    config_path = BUSYBOX_SRC / ".config"
    lines = config_path.read_text().splitlines(keepends=True)
    enabled = []
    out = []
    for line in lines:
        stripped = line.rstrip("\n")
        hit = next((o for o in BUSYBOX_OPTS
                    if stripped == f"# CONFIG_{o} is not set"), None)
        if hit:
            out.append(f"CONFIG_{hit}=y\n")
            enabled.append(hit)
        else:
            out.append(line)
    config_path.write_text("".join(out))
    logging.info("enabled busybox applets: %s", enabled)
    missing = [o for o in BUSYBOX_OPTS if o not in enabled]
    if missing:
        # Not necessarily fatal (e.g. CONFIG_SH_IS_ASH defaults on and
        # never appears as "not set"), but worth surfacing if the
        # busybox Kconfig ever renames one of these options.
        logging.warning("expected options already on or renamed: %s", missing)

    logging.info("busybox oldconfig (resolve deps of the enabled applets)")
    subprocess.run([*make_common, "oldconfig"], check=True, env=env,
                    input="\n" * 50, capture_output=True, text=True)

    logging.info("building busybox (static, riscv64)")
    r = subprocess.run([*make_common, "-j", str(__import__("os").cpu_count() or 4)],
                        env=env, capture_output=True, text=True)
    if r.returncode != 0:
        logging.error("busybox build failed:\n%s", r.stdout[-4000:])
        raise RuntimeError("busybox build failed")

    built = BUSYBOX_SRC / "busybox"
    shutil.copy2(built, BUSYBOX_BIN)
    BUSYBOX_BIN.chmod(0o755)
    BUSYBOX_OPTS_HASH.write_text(opts_hash())
    logging.info("built %s", BUSYBOX_BIN)
    return BUSYBOX_BIN


def stale(cpio: Path, *inputs: Path) -> bool:
    if not cpio.exists():
        return True
    cpio_mtime = cpio.stat().st_mtime
    return any(i.stat().st_mtime > cpio_mtime for i in inputs if i.exists())


def build_cpio(busybox_bin: Path) -> Path:
    stage = WORK / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "bin").mkdir(parents=True)
    (stage / "proc").mkdir()
    (stage / "sys").mkdir()
    # Mountpoint for /init's own devtmpfs mount (see configs/
    # initramfs-init.sh) — needs to pre-exist as a directory, same as
    # proc/ and sys/ above; devtmpfs itself populates it at mount time,
    # nothing is staged inside it here.
    (stage / "dev").mkdir()

    shutil.copy2(busybox_bin, stage / "bin" / "busybox")
    (stage / "bin" / "busybox").chmod(0o755)
    # /init's #!/bin/sh shebang needs an actual /bin/sh path to resolve
    # (CONFIG_BINFMT_SCRIPT execs the interpreter path literally) — a
    # symlink to busybox, whose argv[0]/basename dispatch already
    # recognises "sh" as the ash applet (CONFIG_SH_IS_ASH=y), same as a
    # real busybox install's symlink forest, just for this one applet
    # rather than all of them (see configs/initramfs-init.sh's own note
    # on why every other call spells out "busybox <applet>" instead).
    (stage / "bin" / "sh").symlink_to("busybox")
    init_dst = stage / "init"
    shutil.copy2(INIT_SRC, init_dst)
    init_dst.chmod(0o755)

    logging.info("packing cpio from %s", stage)
    # newc is the format the kernel's initramfs unpacker
    # (init/initramfs.c) expects; find | cpio -o -H newc is the standard
    # recipe. -R 0:0 pins uid/gid so the archive is reproducible
    # regardless of the building user's own uid.
    find = subprocess.run(["find", ".", "-mindepth", "0"], cwd=stage,
                          capture_output=True, text=True, check=True)
    # cpio's -o output is a binary archive, not text — must stay bytes
    # end to end (text=True would try to UTF-8-decode the packed
    # busybox binary embedded in the archive and raise on the first
    # non-UTF-8 byte, which any real ELF is full of).
    cpio = subprocess.run(
        ["cpio", "-o", "-H", "newc", "-R", "0:0"],
        cwd=stage, input=find.stdout.encode(), capture_output=True,
    )
    if cpio.returncode != 0:
        logging.error("cpio failed:\n%s", cpio.stderr.decode(errors="replace"))
        raise RuntimeError("cpio pack failed")

    WORK.mkdir(parents=True, exist_ok=True)
    with gzip.open(CPIO_GZ, "wb") as f:
        f.write(cpio.stdout)
    logging.info("wrote %s (%d bytes)", CPIO_GZ, CPIO_GZ.stat().st_size)
    return CPIO_GZ


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild-busybox", action="store_true",
                     help="rebuild busybox even if a cached binary exists")
    ap.add_argument("--rebuild-toolchain", action="store_true",
                     help="re-fetch and re-extract the musl cross toolchain")
    args = ap.parse_args()

    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not stale(CPIO_GZ, INIT_SRC, BUSYBOX_BIN) and not args.rebuild_busybox and not args.rebuild_toolchain:
        logging.info("cpio up to date at %s, nothing to do", CPIO_GZ)
        print(f"INITRAMFS OK (cached): {CPIO_GZ}")
        return 0

    toolchain_dir = ensure_toolchain(rebuild=args.rebuild_toolchain)
    busybox_bin = ensure_busybox(toolchain_dir, rebuild=args.rebuild_busybox)
    cpio = build_cpio(busybox_bin)
    print(f"INITRAMFS OK: {cpio}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
