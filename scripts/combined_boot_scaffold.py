#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Mechanical scaffolding for a new combined-image boot-screening candidate
(issue awto-au/linux-rs#28). Automates the parts of the process that are
pure mechanics, so an agent/human can start directly at "is anything
genuinely broken" instead of re-deriving worktree/Kconfig/Makefile
boilerplate and re-running the same 3 mandatory checks by hand every time.

What this does NOT do (needs real judgement, stays manual):
  - Deciding whether a build/boot failure is a genuine gap vs already
    covered by an existing fix
  - Writing the actual fix for anything broken
  - Deciding whether a file's build model even fits this pattern (e.g.
    vdso/ files may not belong in vmlinux the normal way — check first)
  - Writing the doc section / filing issues / committing

Usage:
  combined_boot_scaffold.py lib/foo.c [--worktree-name NAME]

Steps:
  1. Verify c2rust binary is fresh (dev.py c2rust-build)
  2. Create an isolated worktree (linux_riscv_worktree.py create)
  3. Copy the c2rust baseline output in, wire lib/Kconfig + lib/Makefile
     with the standard RUST_C2RUST_BOOT_TEST swap (or extend the existing
     gate if a prior candidate already added it to this worktree)
  4. Run the 3 mandatory checks: check-register-statics, -1 as usize grep,
     __init/#[link_section] presence check
  5. Build + boot, report pass/fail with real evidence paths

Log: tmp/combined_boot_scaffold.log
"""
import argparse
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
S = REPO / "scripts"
LOG = REPO / "tmp" / "combined_boot_scaffold.log"
BASELINE = REPO / "tmp" / "c2rust-baseline"
WORKTREES = REPO / "linux-riscv-worktrees"


def sh(cmd, cwd=None, check=True, timeout=1200):
    log = logging.getLogger(__name__)
    log.info("+ %s", " ".join(map(str, cmd)))
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if p.stdout:
        log.info(p.stdout[-4000:])
    if p.stderr:
        log.info(p.stderr[-4000:])
    if check and p.returncode != 0:
        raise RuntimeError(f"FAILED (rc={p.returncode}): {' '.join(map(str, cmd))}")
    return p


def rel_path_to_safe_name(rel_path: str) -> str:
    return rel_path.replace("/", "_").replace("-", "-")


def baseline_output_dir(c_file: str) -> Path:
    safe = rel_path_to_safe_name(c_file)
    d = BASELINE / safe / "output" / "src"
    if not d.exists():
        raise RuntimeError(
            f"no baseline output at {d} — run `dev.py c2rust-baseline` first, "
            f"or this file isn't in the corpus yet"
        )
    return d


def find_rs_file(out_dir: Path, c_file: str) -> Path:
    stem = Path(c_file).stem
    candidates = list(out_dir.glob(f"{stem}.rs"))
    if not candidates:
        candidates = list(out_dir.glob("*.rs"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one .rs in {out_dir}, found {len(candidates)}: {candidates}"
        )
    return candidates[0]


def step_verify_c2rust_fresh(log):
    log.info("=== 1. verify c2rust binary is fresh ===")
    sh(["python3", str(S / "dev.py"), "c2rust-build"])


def step_create_worktree(name: str, log) -> Path:
    log.info("=== 2. create/reuse isolated worktree ===")
    target = WORKTREES / name
    if target.exists():
        log.info("worktree %s already exists, reusing", target)
        return target
    sh(["python3", str(S / "linux_riscv_worktree.py"), "create", name])
    return target


KCONFIG_BLOCK = """
config RUST_C2RUST_BOOT_TEST
\tbool "Build c2rust-translated lib/ files instead of the C originals"
\tdepends on RUST
\tdefault n
\thelp
\t  Combined-image boot screening (issue linux-rs#28): swaps in a
\t  raw c2rust transpile of a chosen lib/ source file in place of
\t  its C original, for a single agent worktree at a time.
"""


def step_wire_file(worktree: Path, c_file: str, log) -> Path:
    log.info("=== 3. copy baseline output + wire Kconfig/Makefile ===")
    out_dir = baseline_output_dir(c_file)
    rs_src = find_rs_file(out_dir, c_file)

    c_rel = Path(c_file)
    rs_dest_name = c_rel.stem + "_rs.rs"
    rs_dest = worktree / c_rel.parent / rs_dest_name
    rs_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rs_src, rs_dest)
    log.info("copied %s -> %s", rs_src, rs_dest)

    kconfig_path = worktree / c_rel.parent / "Kconfig"
    if kconfig_path.exists():
        text = kconfig_path.read_text()
        if "RUST_C2RUST_BOOT_TEST" not in text:
            text = text.replace("\nendmenu\n", KCONFIG_BLOCK + "\nendmenu\n", 1)
            if "RUST_C2RUST_BOOT_TEST" not in text:
                text += KCONFIG_BLOCK
            kconfig_path.write_text(text)
            log.info("added RUST_C2RUST_BOOT_TEST to %s", kconfig_path)
        else:
            log.info("%s already has RUST_C2RUST_BOOT_TEST", kconfig_path)
    else:
        log.warning(
            "no Kconfig at %s — this file's directory may need a different "
            "gating approach, check by hand", kconfig_path
        )

    makefile_path = worktree / c_rel.parent / "Makefile"
    if not makefile_path.exists():
        raise RuntimeError(f"no Makefile at {makefile_path} — can't wire the swap")
    mk_text = makefile_path.read_text()
    obj_stem = c_rel.stem
    if f"{obj_stem}_rs.o" in mk_text:
        log.info("%s already wired in Makefile", obj_stem)
    else:
        # Simple single-target line, e.g. "obj-$(CONFIG_X) += foo.o" or
        # "lib-y += foo.o" alone on its own line — auto-splittable.
        # Bundled multi-file lines ("lib-y := a.o b.o c.o") are NOT
        # touched here; flagged for manual splitting instead, matching
        # the real pattern this project's agents have hit repeatedly
        # (see docs/combined-boot-attempt-2026-07-18.md's Makefile diffs).
        single_line_re = re.compile(
            rf"^((?:obj|lib)-(?:y|\$\(CONFIG_\w+\)))\s*\+=\s*{re.escape(obj_stem)}\.o\s*$",
            re.MULTILINE,
        )
        m = single_line_re.search(mk_text)
        if m:
            var = m.group(1)
            # Preserve whether the original line ended the file without a
            # trailing newline — don't silently introduce or drop one.
            had_trailing_newline = m.end() < len(mk_text) or mk_text.endswith("\n")
            replacement = (
                f"ifdef CONFIG_RUST_C2RUST_BOOT_TEST\n"
                f"{var} += {obj_stem}_rs.o\n"
                f"else\n"
                f"{var} += {obj_stem}.o\n"
                f"endif"
            )
            if had_trailing_newline:
                replacement += "\n"
            mk_text = mk_text[: m.start()] + replacement + mk_text[m.end():]
            makefile_path.write_text(mk_text)
            log.info("auto-wired %s.o -> ifdef CONFIG_RUST_C2RUST_BOOT_TEST swap in %s",
                      obj_stem, makefile_path)
        else:
            log.warning(
                "Makefile at %s doesn't have %s.o on its own simple "
                "obj-y/lib-y line — it's likely bundled on a multi-file "
                "line (e.g. \"foo.o bar.o baz.o\") that needs manual "
                "splitting into the ifdef CONFIG_RUST_C2RUST_BOOT_TEST "
                "swap by hand — see docs/combined-boot-attempt-2026-07-18.md",
                makefile_path, obj_stem,
            )
    return rs_dest


def step_mandatory_checks(worktree: Path, rs_file: Path, log) -> dict:
    log.info("=== 4. mandatory checks ===")
    results = {}

    p = sh(["python3", str(S / "dev.py"), "check-register-statics"], check=False)
    results["register_statics_live_count"] = None
    for line in (p.stdout or "").splitlines():
        if "SCAN OK" in line:
            log.info(line)
    live_hit = str(rs_file) in (p.stdout or "")
    results["this_file_flagged_live"] = live_hit
    if live_hit:
        log.warning("THIS FILE APPEARS IN THE LIVE (needs-fix) LIST — investigate")

    text = rs_file.read_text(errors="replace")
    neg_one = "-1 as usize" in text
    results["neg_one_as_usize_present"] = neg_one
    if neg_one:
        log.warning("'-1 as usize' found in %s — should be fully fixed by issue #38, "
                     "this may be a regression, investigate", rs_file)

    init_fns = re.findall(r"fn (\w+)", text)
    has_link_section = "#[link_section" in text
    results["has_init_marked_fn_heuristic"] = bool(init_fns) and "init" in text.lower()
    results["has_link_section_attr"] = has_link_section
    log.info("mandatory checks: %s", results)
    return results


def step_build_boot(worktree_name: str, run_id: str, log) -> bool:
    log.info("=== 5. build + boot ===")
    import os
    env = dict(**{**__import__("os").environ, "LINUXRS_TREE": f"linux-riscv-worktrees/{worktree_name}"})
    p = subprocess.run(
        ["python3", str(S / "dev.py"), "build"], cwd=REPO, env=env,
        text=True, capture_output=True, timeout=1200,
    )
    log.info(p.stdout[-2000:] if p.stdout else "")
    if p.returncode != 0:
        log.error("BUILD FAILED (rc=%d)", p.returncode)
        log.error(p.stderr[-4000:] if p.stderr else "")
        return False
    log.info("BUILD OK")

    p = subprocess.run(
        ["python3", str(S / "dev.py"), "boot", "--run-id", run_id], cwd=REPO, env=env,
        text=True, capture_output=True, timeout=600,
    )
    log.info(p.stdout[-3000:] if p.stdout else "")
    if p.returncode != 0:
        log.error("BOOT FAILED (rc=%d) — check tmp/qemu-boot-%s.log", p.returncode, run_id)
        return False
    log.info("BOOT OK — INIT REACHED")
    return True


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger(__name__)

    ap = argparse.ArgumentParser()
    ap.add_argument("c_file", help="e.g. lib/foo.c, relative to linux-riscv/")
    ap.add_argument("--worktree-name", default=None)
    ap.add_argument("--skip-build-boot", action="store_true",
                     help="stop after wiring + mandatory checks, don't build/boot yet")
    args = ap.parse_args()

    worktree_name = args.worktree_name or f"combined-boot-{Path(args.c_file).stem}"

    step_verify_c2rust_fresh(log)
    worktree = step_create_worktree(worktree_name, log)
    rs_file = step_wire_file(worktree, args.c_file, log)
    checks = step_mandatory_checks(worktree, rs_file, log)

    if args.skip_build_boot:
        log.info("--skip-build-boot set, stopping after wiring + checks")
        print(f"SCAFFOLD OK (no build/boot): worktree={worktree}, checks={checks}")
        return 0

    ok = step_build_boot(worktree_name, worktree_name, log)
    print(f"SCAFFOLD {'OK' if ok else 'BUILD/BOOT FAILED'}: worktree={worktree}, "
          f"checks={checks}, run_id={worktree_name}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
