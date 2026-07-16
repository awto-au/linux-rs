#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Mechanise the TU-integration loop (Dan, 2026-07-16).

Given a translated <name>_rs.rs already written next to its C original,
this performs every mechanical step that used to be done by hand:

  1. Makefile: move <name>.o into the `ifdef CONFIG_RUST` switch
     (creates the switch if the dir doesn't have one).
  2. bindings_helper.h: add the header (idempotent, sorted position).
  3. Kconfig: enable the KUnit test option(s).
  4. olddefconfig + build (LLVM=1, riscv).
  5. Boot via boot_qemu.py; parse KUnit totals; FAIL loudly on any
     'not ok' or missing suite.
  6. Optionally format-patch the kernel commit into patches/ (--patch N
     after you commit in the worktree).

Usage:
  integrate_tu.py --obj lib/foo.o --header linux/foo.h \
                  --kunit CONFIG_FOO_KUNIT_TEST [--suite foo] [--tree linux-riscv]

The Rust file must already exist (<dir>/<name>_rs.rs). Translation itself
stays human/agent work; everything around it is this script.
Log: tmp/integrate_tu.log
"""
import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "integrate_tu.log"


def run(cmd, timeout=1200):
    logging.info("$ %s", " ".join(map(str, cmd)))
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        logging.error("FAILED (%d):\n%s", p.returncode, p.stdout[-4000:] + p.stderr[-4000:])
        raise SystemExit(1)
    return p.stdout


def patch_makefile(tree: Path, obj: str):
    """Move <obj> from its current rule into the CONFIG_RUST switch."""
    objpath = Path(obj)
    mk = tree / objpath.parent / "Makefile"
    name = objpath.stem
    text = mk.read_text()
    if f"{name}_rs.o" in text:
        logging.info("Makefile already switched for %s", name)
        return
    # Remove the C object from whatever obj-/lib- rule holds it, including
    # backslash-continuation lines: find the token, walk back to the rule.
    lines = text.splitlines(keepends=True)
    tok = re.compile(rf"(\s*)\b{re.escape(name)}\.o\b")
    idx = next((i for i, l in enumerate(lines)
                if tok.search(l) and not l.lstrip().startswith("#")), None)
    if idx is None:
        logging.error("could not find %s.o in %s — patch by hand", name, mk)
        raise SystemExit(1)
    start = idx
    while start > 0 and not re.match(r"^(obj|lib)-", lines[start]):
        start -= 1
    cond = re.split(r"[+:]?=", lines[start])[0].strip()
    lines[idx] = tok.sub("", lines[idx], count=1)
    text = "".join(lines)
    switch = (f"\n# linux-rs: translated TU — Rust when Rust is available.\n"
              f"ifdef CONFIG_RUST\n{cond} += {name}_rs.o\nelse\n"
              f"{cond} += {name}.o\nendif\n")
    text = text.rstrip("\n") + "\n" + switch
    mk.write_text(text)
    logging.info("Makefile switched: %s (%s)", mk, cond)


def patch_bindings(tree: Path, header: str):
    bh = tree / "rust/bindings/bindings_helper.h"
    text = bh.read_text()
    inc = f"#include <{header}>"
    if inc in text:
        logging.info("bindings_helper already has %s", header)
        return
    lines = text.splitlines(keepends=True)
    idx = max(i for i, l in enumerate(lines) if l.startswith("#include <linux/"))
    for i, l in enumerate(lines):
        if l.startswith("#include <linux/") and l.strip() > inc:
            idx = i
            break
    lines.insert(idx, inc + "\n")
    bh.write_text("".join(lines))
    logging.info("bindings_helper: added %s", header)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--obj", required=True, help="e.g. lib/foo.o")
    ap.add_argument("--header", help="e.g. linux/foo.h")
    ap.add_argument("--kunit", nargs="*", default=[], help="CONFIG_..._KUNIT_TEST")
    ap.add_argument("--suite", nargs="*", default=[], help="expected KUnit suite names")
    ap.add_argument("--tree", default="linux-riscv")
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()

    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    tree = REPO / args.tree
    rs = tree / Path(args.obj).parent / (Path(args.obj).stem + "_rs.rs")
    if not rs.exists():
        logging.error("translation missing: %s", rs)
        return 1

    patch_makefile(tree, args.obj)
    if args.header:
        patch_bindings(tree, args.header)
    for opt in args.kunit:
        run([str(tree / "scripts/config"), "--file", str(tree / ".config"),
             "-e", opt.removeprefix("CONFIG_")])
    run(["make", "-C", str(tree), "ARCH=riscv", "LLVM=1", "olddefconfig"])
    if args.skip_build:
        return 0
    run(["make", "-C", str(tree), "ARCH=riscv", "LLVM=1", "-j32"], timeout=3600)

    out = run(["python3", str(REPO / "scripts/boot_qemu.py"), "--tree", args.tree],
              timeout=600)
    boot_log = (REPO / "tmp/qemu-boot.log").read_text(errors="replace")
    bad = [l for l in boot_log.splitlines() if l.lstrip().startswith("not ok")]
    for suite in args.suite:
        if not re.search(rf"^ok \d+ {re.escape(suite)}$", boot_log, re.M):
            bad.append(f"suite missing/failed: {suite}")
    if bad:
        logging.error("ORACLE FAIL:\n%s", "\n".join(bad))
        return 1
    logging.info("ORACLE PASS — all KUnit suites green%s",
                 f" (incl. {', '.join(args.suite)})" if args.suite else "")
    print(out[-400:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
