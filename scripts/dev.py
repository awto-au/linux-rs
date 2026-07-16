#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""dev.py — the one entry point for all common linux-rs operations.

Standardised, terse, agent-friendly. Every subcommand logs to
tmp/<sub>.log and prints only the outcome lines that matter.

  dev.py build                  # make riscv kernel (LLVM=1 -j32)
  dev.py boot                   # boot QEMU -> tmp/qemu-boot.log, KUnit summary
  dev.py check                  # build + boot + fail on any 'not ok'
  dev.py config -e OPT [-e ..]  # scripts/config -e + olddefconfig
  dev.py integrate --obj lib/foo.o --header linux/foo.h --kunit CONFIG_X --suite s
  dev.py readiness [glob]       # rank untranslated TUs
  dev.py bench                  # host benchmark (pinned methodology)
  dev.py patch N                # format-patch HEAD -> patches/ start-number N
  dev.py push "msg"             # commit -A + push project repo
  dev.py kcommit "msg"          # commit staged files in kernel worktree

Tree default: linux-riscv (override with LINUXRS_TREE env).
"""
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def print(*args, **kw):  # noqa: A001 — awto rule: all output also to tmp/dev.log
    __builtins__.print(*args, **kw)
    logging.info(" ".join(str(a) for a in args))
TREE = REPO / os.environ.get("LINUXRS_TREE", "linux-riscv")
S = REPO / "scripts"
TRAILER = "\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"


def sh(cmd, log=None, timeout=3600, quiet_ok=True):
    lf = open(REPO / "tmp" / log, "w") if log else None
    p = subprocess.run(cmd, text=True, stdout=lf or subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout)
    if lf:
        lf.close()
    if p.returncode != 0:
        if log:
            tail = (REPO / "tmp" / log).read_text(errors="replace")[-3000:]
            print(tail)
        elif p.stdout:
            print(p.stdout[-3000:])
        print(f"FAIL rc={p.returncode}: {' '.join(map(str, cmd))}")
        sys.exit(p.returncode)
    if not quiet_ok and p.stdout:
        print(p.stdout)
    return p


def kmake(*targets):
    sh(["make", "-C", str(TREE), "ARCH=riscv", "LLVM=1", "-j32", *targets],
       log="dev-build.log")


def boot():
    sh(["python3", str(S / "boot_qemu.py"), "--tree", TREE.name],
       log="dev-boot.log", timeout=600)
    txt = (REPO / "tmp/qemu-boot.log").read_text(errors="replace")
    ok = re.findall(r"^ok \d+ .*$", txt, re.M)
    bad = re.findall(r"^\s*not ok .*$", txt, re.M)
    for line in ok:
        print(line)
    if bad:
        print("\n".join(bad))
        print("ORACLE FAIL")
        sys.exit(1)
    if not ok:
        print("ORACLE FAIL: no KUnit output found")
        sys.exit(1)
    print(f"ORACLE PASS ({len(ok)} suites)")


def main() -> int:
    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        handlers=[logging.FileHandler(REPO / "tmp" / "dev.log", mode="a")],
    )
    logging.info("dev.py %s", " ".join(sys.argv[1:]))
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd, rest = sys.argv[1], sys.argv[2:]

    if cmd == "build":
        kmake()
        print("BUILD OK")
    elif cmd == "boot":
        boot()
    elif cmd == "check":
        kmake()
        boot()
        sh(["python3", str(S / "report.py")], quiet_ok=False)
    elif cmd == "report":
        sh(["python3", str(S / "report.py")], quiet_ok=False)
    elif cmd == "config":
        opts = [a for a in rest if a != "-e"]
        sh([str(TREE / "scripts/config"), "--file", str(TREE / ".config"),
            *sum((["-e", o.removeprefix("CONFIG_")] for o in opts), [])])
        sh(["make", "-C", str(TREE), "ARCH=riscv", "LLVM=1", "olddefconfig"],
           log="dev-config.log")
        print("CONFIG OK:", " ".join(opts))
    elif cmd == "integrate":
        sh(["python3", str(S / "integrate_tu.py"), *rest], quiet_ok=False)
    elif cmd == "readiness":
        args = ["--glob", rest[0]] if rest else []
        sh(["python3", str(S / "readiness.py"), *args], quiet_ok=False)
    elif cmd == "bench":
        sh(["python3", str(S / "bench_math.py")], quiet_ok=False)
    elif cmd == "patch":
        p = sh(["git", "-C", str(TREE), "format-patch", "-1",
                "--start-number", rest[0], "-o", str(REPO / "patches")])
        print("PATCH OK")
    elif cmd == "push":
        sh(["git", "-C", str(REPO), "add", "-A"])
        sh(["git", "-C", str(REPO), "commit", "-m", rest[0] + TRAILER])
        sh(["git", "-C", str(REPO), "push"])
        print("PUSHED")
    elif cmd == "kcommit":
        sh(["git", "-C", str(TREE), "commit", "-m", rest[0] + TRAILER])
        print("KCOMMIT OK")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
