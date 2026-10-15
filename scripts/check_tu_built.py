#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Gate: every translated TU (rulesdb/tu_provenance.json entry) is in the build.

- "Built" = its `<name>_rs.o` is a member of vmlinux.a or a lib.a (kbuild thin
  archives, `ar t`). A landed *_rs.rs with no Makefile wiring fails here (#122).
- Run after a build of the same tree (dev.py check runs it between build and boot).

Usage: check_tu_built.py [--tree linux-riscv]
Log: tmp/logs/check_tu_built.log
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import REPO, make_log  # noqa: E402

MANIFEST = REPO / "rulesdb" / "tu_provenance.json"
# `ar t` on a thin archive reads only its index: ms locally; 30 s = cold disk on a ~1 MB index.
AR_TIMEOUT_S = 30

log = make_log("check_tu_built")


def members(archive: Path, tree: Path) -> set[str]:
    out = subprocess.run(["ar", "t", str(archive)], capture_output=True, text=True,
                         check=True, timeout=AR_TIMEOUT_S).stdout
    res = set()
    for line in out.splitlines():
        p = Path(line.strip())
        p = p if p.is_absolute() else archive.parent / p
        try:
            res.add(str(p.resolve().relative_to(tree)))
        except ValueError:
            res.add(line.strip())
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="linux-riscv")
    args = ap.parse_args()
    tree = (REPO / args.tree).resolve()

    archives = [tree / "vmlinux.a", *sorted((tree / "lib").rglob("lib.a")),
                *sorted((tree / "arch").rglob("lib.a"))]
    if not (tree / "vmlinux.a").exists():
        log(f"FAIL: {args.tree}/vmlinux.a missing -- build the tree first")
        return 1
    built: set[str] = set()
    for a in archives:
        built |= members(a, tree)

    rs_files = sorted(json.loads(MANIFEST.read_text())["entries"])
    missing = [rs for rs in rs_files if rs.removesuffix(".rs") + ".o" not in built]
    for rs in missing:
        log(f"NOT BUILT: {rs} (no {rs.removesuffix('.rs')}.o in vmlinux.a / lib.a)")
    if missing:
        log(f"TU BUILT FAIL: {len(missing)}/{len(rs_files)} translated TUs not in the build")
        return 1
    log(f"TU BUILT OK: {len(rs_files)}/{len(rs_files)} translated TUs in the build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
