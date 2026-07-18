#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""dev.py — the one entry point for all common linux-rs operations.

Standardised, terse, agent-friendly. Every subcommand logs to
tmp/<sub>.log and prints only the outcome lines that matter.

  dev.py build                  # make riscv kernel (LLVM=1 -j32)
  dev.py boot                   # boot QEMU -> tmp/qemu-boot.log, KUnit summary
  dev.py check                  # build + boot + fail on any 'not ok'
  dev.py report                 # regenerate docs/STATUS.md + status.png only
  dev.py config -e OPT [-e ..]  # scripts/config -e + olddefconfig
  dev.py integrate --obj lib/foo.o --header linux/foo.h --kunit CONFIG_X --suite s
  dev.py readiness [glob]       # rank untranslated TUs
  dev.py bench                  # host benchmark (pinned methodology)
  dev.py diff <target>          # tier-2.5 differential oracle (needs bench/diff_<target>.{c,rs})
  dev.py c2rust-build [--fork-dir DIR]   # full workspace build + verify every binary the dispatcher needs is fresh
  dev.py c2rust-baseline [--limit N]     # full-corpus c2rust triage -> patterns.db directly
  dev.py c2rust-regress BEFORE AFTER [--file-issue]  # per-decl regression diff between 2 baselined revs
  dev.py c2rust-clippy [--limit N]       # clippy-check c2rust clean outputs -> patterns.db
  dev.py db                     # rebuild rulesdb/patterns.db (ephemeral, rebuild-not-migrate)
  dev.py q <subcommand> ...     # quick SQL checks against patterns.db (see query_db.py --help)
  dev.py patch N                # format-patch HEAD -> patches/ start-number N
  dev.py land KMSG [REPOMSG]    # kcommit + patch + report + push, one shot
  dev.py push "msg"             # commit -A + push project repo
  dev.py kcommit "msg"          # commit staged files in kernel worktree

Tree default: linux-riscv (override with LINUXRS_TREE env).
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kunit_oracle import INIT_REACHED, verify_kunit_ok  # noqa: E402 — see module doc


def print(*args, **kw):  # noqa: A001 — awto rule: all output also to tmp/dev.log
    __builtins__.print(*args, **kw)
    logging.info(" ".join(str(a) for a in args))
TREE_REL = os.environ.get("LINUXRS_TREE", "linux-riscv")
TREE = REPO / TREE_REL
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


INIT_REACHED_MARKER = INIT_REACHED

# Check-registry, not a hand-wired sequence — see the "why does this new
# check feel bolted-on" pattern check_spdx_provenance.py ran into
# (2026-07-18) that motivated this. Split into two stages so a check that
# CAN fail without a build/boot fails fast, before paying for either:
# cheapest-first is this project's own stated oracle principle (PLAN.md's
# "Layered, cheapest first"), and dev.py check previously ran the full
# kmake()+boot() cycle before check_spdx_provenance.py even though that
# check only reads source files — a pure-source SPDX/idiom violation was
# only discovered after minutes of build+boot, when it could have been
# caught in seconds. PRE_BUILD_CHECKS run before kmake()/boot() (must
# have no build/boot dependency); POST_BOOT_CHECKS run after (may read
# tmp/qemu-boot.log or similar boot-produced state, like report.py does).
#
# Adding a new check: append one entry to whichever list matches its real
# dependency, script name relative to scripts/. sh() already treats any
# nonzero exit as a hard failure (sys.exit()s) — every check registered
# here is a gate by construction. A genuinely non-gating (warn-only)
# check would need its own explicit handling when one is actually added;
# not built speculatively ahead of a real need.
PRE_BUILD_CHECKS = ["check_spdx_provenance.py"]
POST_BOOT_CHECKS = ["report.py"]


def run_pre_build_checks():
    for script in PRE_BUILD_CHECKS:
        sh(["python3", str(S / script)], quiet_ok=False)


def run_post_boot_checks():
    for script in POST_BOOT_CHECKS:
        sh(["python3", str(S / script)], quiet_ok=False)


def boot():
    # Pass the full relative path (e.g. "linux-riscv-worktrees/8250-tier-b"),
    # not TREE.name — TREE.name silently strips any subdirectory prefix,
    # which broke boot_qemu.py's `REPO / args.tree` join for any tree that
    # isn't a direct child of REPO (i.e. every linux_riscv_worktree.py
    # worktree, which lives under linux-riscv-worktrees/<name>/). Found
    # while boot-testing a Tier B worktree per that script's own guidance.
    sh(["python3", str(S / "boot_qemu.py"), "--tree", TREE_REL],
       log="dev-boot.log", timeout=600)
    txt = (REPO / "tmp/qemu-boot.log").read_text(errors="replace")
    # Primary gate: unchanged from before initramfs support existed —
    # any 'not ok' KUnit line is a hard fail, no KUnit output at all is a
    # hard fail. Do not weaken this; it's the project's main correctness
    # signal. Shared with integrate_tu.py via kunit_oracle.verify_kunit_ok
    # — see rulesdb/rules/0028-kunit-boot-oracle-gate.toml.
    passed, ok, bad = verify_kunit_ok(txt)
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
    # Hard gate since 2026-07-18 (awto-au/linux-rs#7): missing initramfs
    # reachability now fails the run, not just a warning. Streams 2/3
    # depend on proving the boot actually reached PID 1 userspace, not
    # merely that in-kernel KUnit ran before a later boot-path regression
    # — a warning-only signal was easy to silently ignore.
    if INIT_REACHED_MARKER not in txt:
        print("ORACLE FAIL: initramfs init milestone not seen")
        sys.exit(1)
    print("INIT REACHED (initramfs userspace boot verified)")


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
        run_pre_build_checks()
        kmake()
        boot()
        run_post_boot_checks()
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
    elif cmd == "diff":
        sh(["python3", str(S / "diff_oracle.py"), *rest], quiet_ok=False)
    elif cmd == "c2rust-build":
        # `cargo build --release --bin c2rust` alone only builds the
        # dispatcher, not the sibling c2rust-transpile binary it shells
        # out to for `c2rust transpile` — see build_c2rust.py's module
        # doc for the real regression this caused. Always go through
        # this subcommand, never a hand-typed cargo invocation.
        sh(["python3", str(S / "build_c2rust.py"), *rest], quiet_ok=False)
    elif cmd == "c2rust-baseline":
        # Full-corpus c2rust triage loop: run, writing straight into
        # patterns.db's c2rust_attempts/c2rust_decl_outcomes (persisted
        # across rebuilds, see build_db.py's PERSISTENT_TABLES). Jobs
        # count is adaptive (see run_c2rust_baseline.py's
        # adaptive_job_count()), scaling with whatever RAM is free.
        sh(["python3", str(S / "run_c2rust_baseline.py"), *rest], log="c2rust-baseline.log", timeout=1800)
    elif cmd == "c2rust-regress":
        # Per-declaration regression check between two awtoau/c2rust
        # revisions already baselined (dev.py c2rust-baseline at each
        # rev first). See c2rust_regression_check.py's module doc for
        # why file-level outcomes (clean/dropped_decls) are too noisy to
        # gate on directly.
        sh(["python3", str(S / "c2rust_regression_check.py"), *rest], quiet_ok=False)
    elif cmd == "c2rust-clippy":
        # Second quality gate on top of c2rust-build's plain rustc
        # compile-check (check_c2rust_output_compiles.py) — same
        # kernel-toolchain-linked rmeta approach, clippy-driver in place
        # of rustc. See check_c2rust_output_clippy.py's module doc for
        # the SVH-mismatch pitfall this avoids.
        sh(["python3", str(S / "check_c2rust_output_clippy.py"), *rest], quiet_ok=False)
    elif cmd == "db":
        sh(["python3", str(S / "build_db.py")], quiet_ok=False)
        sh(["python3", str(S / "import_cscope.py")], quiet_ok=False)
        sh(["python3", str(S / "import_sparse.py")], quiet_ok=False, timeout=600)
    elif cmd == "q":
        sh(["python3", str(S / "query_db.py"), *rest], quiet_ok=False)
    elif cmd == "patch":
        n = rest[0] if rest else str(len(list((REPO / "patches").glob("*.patch"))) + 1)
        sh(["git", "-C", str(TREE), "format-patch", "-1",
            "--start-number", n, "-o", str(REPO / "patches")])
        print(f"PATCH OK ({n})")
    elif cmd == "land":
        # Post-integration chores in one shot: kernel commit + auto-numbered
        # patch + report + project commit/push. args: <kernel-msg> [repo-msg]
        sh(["git", "-C", str(TREE), "add", "-A"])
        sh(["git", "-C", str(TREE), "commit", "-m", rest[0] + TRAILER])
        n = str(len(list((REPO / "patches").glob("*.patch"))) + 1)
        sh(["git", "-C", str(TREE), "format-patch", "-1",
            "--start-number", n, "-o", str(REPO / "patches")])
        sh(["python3", str(S / "report.py")], quiet_ok=False)
        repo_msg = rest[1] if len(rest) > 1 else rest[0].splitlines()[0]
        sh(["git", "-C", str(REPO), "add", "-A"])
        sh(["git", "-C", str(REPO), "commit", "-m", repo_msg + TRAILER])
        sh(["git", "-C", str(REPO), "push"])
        print(f"LANDED (patch {n}, report, pushed)")
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
