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

Each QEMU invocation is already a fully isolated process (own PID, own
serial pipe) — the only actual barrier to running boots in parallel was
this script hardcoding tmp/qemu-boot.log, so N concurrent runs would
clobber the same file. --run-id gives each caller its own
tmp/qemu-boot-<id>.log instead; omit it (the default) for the existing
single-run behavior and path, unchanged for every caller that doesn't
opt in (dev.py boot()/check() included — they still read plain
tmp/qemu-boot.log and are not parallel-safe on their own yet, a caller
wanting parallel runs must pass distinct --run-id values itself, e.g.
one per kernel image variant being boot-compared).

Every run is also archived to tmp/boot-history/<ISO-timestamp>-<run-id
or "default">.log (untouched raw copy, gitignored scratch same as
everything else under tmp/) and gets one row appended to the tracked
docs/status/boot-history.csv — the same "keep every run, not just the
latest" pattern docs/status/history.csv already uses for dev.py check's
KUnit summary, applied to raw boot logs specifically. tmp/qemu-boot.log
itself still gets truncated per run (nothing that reads that stable
path changes); the archive is additive, not a replacement.

Usage: boot_qemu.py [--tree linux-riscv] [--append "extra args"] [--run-id NAME]
"""
import argparse
import csv
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INITRD = REPO / "tmp" / "initramfs" / "initramfs.cpio.gz"
BOOT_HISTORY_DIR = REPO / "tmp" / "boot-history"
BOOT_HISTORY_CSV = REPO / "docs" / "status" / "boot-history.csv"

# Printed by configs/initramfs-init.sh once /init actually runs as PID 1 —
# the milestone that real userspace (not just KUnit-in-kernel-space) was
# reached. Kept distinct from the "ok N ..." KUnit line shape so the two
# detectors can never collide.
INIT_REACHED = "linux-rs: initramfs init reached, PID 1 alive"


def archive_boot(log_path: Path, run_id: str | None, n_ok: int, n_notok: int,
                  init_reached: bool, rc: int) -> Path:
    """Copy this run's raw log to tmp/boot-history/ (never overwritten,
    unlike tmp/qemu-boot.log itself) and append one row to the tracked
    docs/status/boot-history.csv, mirroring history.csv's existing
    per-run-append pattern."""
    BOOT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    archived = BOOT_HISTORY_DIR / f"{stamp}-{run_id or 'default'}.log"
    shutil.copyfile(log_path, archived)

    BOOT_HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    is_new = not BOOT_HISTORY_CSV.exists()
    with open(BOOT_HISTORY_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "run_id", "ok", "not_ok",
                              "init_reached", "returncode", "log_file"])
        writer.writerow([
            datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            run_id or "default", n_ok, n_notok, int(init_reached), rc,
            str(archived.relative_to(REPO)),
        ])

    commit_and_push_history(run_id, n_ok, n_notok, init_reached)
    return archived


def commit_and_push_history(run_id: str | None, n_ok: int, n_notok: int, init_reached: bool):
    """Every boot commits+pushes docs/status/boot-history.csv immediately
    (explicit choice: full automation over batching, so the history is
    never more than one boot stale on the remote). tmp/boot-history/*.log
    itself is NOT committed (gitignored, regenerable/large) — only the
    tracked CSV row. Fails LOUD (prints + re-raises) rather than silently
    swallowing a push failure, since a push touches shared state and a
    caller relying on this project's rule that shared-state actions are
    never silent needs to see it fail, not lose it in stdout noise."""
    try:
        subprocess.run(["git", "add", str(BOOT_HISTORY_CSV.relative_to(REPO))],
                       cwd=REPO, check=True, capture_output=True, text=True)
        status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO)
        if status.returncode == 0:
            return  # nothing staged (e.g. re-running with an identical row somehow) — no-op
        msg = (f"boot-history: {run_id or 'default'} — {n_ok} ok / {n_notok} not ok"
               f"{', INIT REACHED' if init_reached else ''}\n\n"
               f"Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>")
        subprocess.run(["git", "commit", "-m", msg], cwd=REPO, check=True,
                       capture_output=True, text=True)
        subprocess.run(["git", "push"], cwd=REPO, check=True, capture_output=True, text=True)
        print("boot-history: committed + pushed")
    except subprocess.CalledProcessError as e:
        print(f"WARNING: boot-history auto-commit/push failed: {e}\n{e.stderr}",
              file=sys.stderr)
        # Don't fail the whole boot over a push hiccup (e.g. transient
        # network) — the boot's own pass/fail result is what matters most
        # and is already returned/printed by the caller; this is a
        # best-effort convenience layered on top, not the primary gate.


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
    ap.add_argument("--run-id", default=None,
                     help="isolate this run's log to tmp/qemu-boot-<id>.log instead of "
                          "the shared tmp/qemu-boot.log, so multiple boots can run "
                          "concurrently without clobbering each other's output")
    args = ap.parse_args()

    log_name = f"qemu-boot-{args.run_id}.log" if args.run_id else "qemu-boot.log"
    LOG = REPO / "tmp" / log_name

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
    n_ok = n_notok = 0
    for line in text.splitlines():
        if line.startswith("ok "):
            n_ok += 1
        elif line.startswith("not ok "):
            n_notok += 1
        if line.startswith(("ok ", "not ok ")) or "# Totals:" in line \
                or "Kernel panic" in line:
            print(line)
    init_reached = INIT_REACHED in text
    if init_reached:
        print(INIT_REACHED)
    else:
        print("WARNING: init-reached milestone not seen in boot log "
              "(userspace/initramfs coverage did not run this boot)")

    archived = archive_boot(LOG, args.run_id, n_ok, n_notok, init_reached, rc)
    print(f"archived: {archived.relative_to(REPO)}")

    return rc


if __name__ == "__main__":
    sys.exit(main())
