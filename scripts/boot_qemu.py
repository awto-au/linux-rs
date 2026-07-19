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

Every run is also archived to docs/status/boot-logs/<ISO-timestamp>-
<run-id or "default">.log (untouched raw copy, TRACKED in git — see
2026-07-18 fix: this used to live under tmp/boot-history/, which is
gitignored, so every row in the tracked boot-history.csv pointed at a
log file that was never actually committed; a fresh clone had a
completely dangling history) and gets one row appended to the tracked
docs/status/boot-history.csv — the same "keep every run, not just the
latest" pattern docs/status/history.csv already uses for dev.py check's
KUnit summary, applied to raw boot logs specifically. tmp/qemu-boot.log
itself still gets truncated per run (nothing that reads that stable
path changes); the archive is additive, not a replacement.

Every line written to the log is prefixed with elapsed time since this
QEMU process started ("NNNNN.NNN ", 5-digit zero-padded whole seconds +
3-digit milliseconds — see TS_PREFIX_RE in kunit_oracle.py, the single
shared definition every downstream parser embeds). The raw serial log is
genuinely hard to read without knowing how far apart events are in
wall-clock time, and this project's whole boot is well under 1 second of
QEMU time so plain second-granularity wasn't enough —
milliseconds make the OpenSBI-banner-vs-KUnit-results gap legible.
QEMU's stdout is streamed line-by-line via Popen rather than captured
in one blocking subprocess.run() specifically so each line can be
timestamped as it actually arrives, not after the whole process exits.

Usage: boot_qemu.py [--tree linux-riscv] [--append "extra args"] [--run-id NAME]
                     [--qemu-extra "raw qemu args"]

--qemu-extra (awto-au/linux-rs#34, block-layer bring-up): the QEMU
invocation itself was previously fixed — no way to attach a -drive/
-device (e.g. virtio-blk-device) without editing this file. Additive
only: shlex-split and appended to the end of the qemu-system-riscv64
argv, so a caller that never passes it (every existing caller: dev.py
boot()/check(), boot-history rows to date) gets byte-identical argv to
before this flag existed.
"""
import argparse
import csv
import datetime
import fcntl
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kunit_oracle import INIT_REACHED, NOT_OK_RE, OK_RE  # noqa: E402 — see module doc

REPO = Path(__file__).resolve().parent.parent
INITRD = REPO / "tmp" / "initramfs" / "initramfs.cpio.gz"
BOOT_HISTORY_DIR = REPO / "docs" / "status" / "boot-logs"
BOOT_HISTORY_CSV = REPO / "docs" / "status" / "boot-history.csv"
BOOT_HISTORY_LOCK = REPO / "tmp" / ".boot-history.lock"


def archive_boot(log_path: Path, run_id: str | None, n_ok: int, n_notok: int,
                  init_reached: bool, rc: int) -> Path:
    """Copy this run's raw log to docs/status/boot-logs/ (never
    overwritten, unlike tmp/qemu-boot.log itself) and append one row to
    the tracked docs/status/boot-history.csv, mirroring history.csv's
    existing per-run-append pattern. Both the log and the CSV row are
    real, tracked git content — see commit_and_push_history()."""
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

    commit_and_push_history(archived, run_id, n_ok, n_notok, init_reached)
    return archived


def commit_and_push_history(archived_log: Path, run_id: str | None, n_ok: int,
                             n_notok: int, init_reached: bool):
    """Every boot commits+pushes both docs/status/boot-history.csv AND
    the archived log file itself immediately (explicit choice: full
    automation over batching, so the history is never more than one
    boot stale on the remote, and — since 2026-07-18 — never has a
    dangling log reference either: the whole point of the diff/history/
    browse tooling in render_boot_log.py is a real, portable artifact
    history, not one only usable on the machine that generated it).
    On failure, prints a loud WARNING to stderr rather than silently
    swallowing it — but does NOT re-raise: the boot's own pass/fail
    result (returned/printed separately by the caller) is the primary
    gate and must not be masked by a transient push failure (e.g.
    network hiccup). The whole git add/commit/push sequence is
    serialized across concurrent boot_qemu.py runs (--run-id) via an
    flock on tmp/.boot-history.lock — without it, two runs finishing
    around the same time race on .git/index.lock and the loser's row
    is silently never committed, not just delayed."""
    BOOT_HISTORY_LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(BOOT_HISTORY_LOCK, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            subprocess.run(["git", "add", str(BOOT_HISTORY_CSV.relative_to(REPO)),
                            str(archived_log.relative_to(REPO))],
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
    ap.add_argument("--qemu-extra", default="",
                     help="raw extra args appended verbatim to the qemu-system-riscv64 "
                          "invocation (shlex-split), e.g. "
                          "'-drive file=test.img,format=raw,if=none,id=blk0 "
                          "-device virtio-blk-device,drive=blk0'. Additive only — "
                          "omitting it (the default) leaves the argv unchanged.")
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
    if args.qemu_extra:
        cmd += shlex.split(args.qemu_extra)
    print(f"booting {image}\ninitrd: {initrd}\nlog: {LOG}")
    # Popen + line-by-line streaming (not a single blocking subprocess.run)
    # so each line can be stamped with real elapsed time as it arrives —
    # a post-hoc timestamp after the process exits would collapse the
    # whole boot to one instant. t0 is this process's own start, so
    # "00000.xxx " on the first real output line means "QEMU had been
    # running this long already", which is the elapsed-time-since-launch
    # semantic requested ("00000 start is fine").
    with open(LOG, "w") as log:
        log.write(f"# {' '.join(cmd)}\n")
        log.flush()
        t0 = time.monotonic()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            elapsed = time.monotonic() - t0
            log.write(f"{elapsed:09.3f} {line}")
        proc.stdout.close()
        rc = proc.wait()
    # Summarise the KUnit result lines for the terminal — this remains the
    # primary pass/fail signal (dev.py's boot()/check() parse these same
    # "ok "/"not ok " lines from tmp/qemu-boot.log; nothing here changes
    # that gate). The init-reached line is additional, supplementary
    # confirmation that userspace was reached, printed alongside it.
    text = LOG.read_text(errors="replace")
    n_ok = n_notok = 0
    for line in text.splitlines():
        is_ok = bool(OK_RE.match(line))
        is_notok = bool(NOT_OK_RE.match(line))
        if is_ok:
            n_ok += 1
        elif is_notok:
            n_notok += 1
        if is_ok or is_notok or "# Totals:" in line or "Kernel panic" in line:
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
