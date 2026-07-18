#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify every hand-translated *_rs.rs file's SPDX-License-Identifier
exactly matches its C original's.

Why this exists: on 2026-07-18 a licensing audit found two translated
files whose SPDX line silently drifted from the C original during
hand-translation (lib/iomem_copy_rs.rs said GPL-2.0 when
lib/iomem_copy.c says GPL-2.0-only; 8250_helpers_rs.rs said GPL-2.0
when 8250_port.c says GPL-2.0+). Both were caught only by manual
review — nothing in the pipeline checked this. This script is that
check, made permanent and wired into `dev.py check`.

Mapping from .rs to .c original:
  - Default (all but one file): 1:1 same-directory, strip the `_rs`
    suffix — lib/bcd_rs.rs -> lib/bcd.c. This covers every current
    translated file except the one exception below.
  - Exception table (EXCEPTIONS below): files that are a PARTIAL
    translation of a C file with a different name/stem, so the 1:1
    rule doesn't apply. Currently one entry:
    drivers/tty/serial/8250/8250_helpers_rs.rs -> 8250_port.c (only
    serial8250_compute_lcr() is translated, not the whole TU).
    Deliberately a hardcoded table, not a parse of the module doc's
    "Rust translation of `X`" phrasing — that phrasing wraps across
    multiple `//!` lines in roughly a third of current files (e.g.
    argv_split_rs.rs, cmdline_rs.rs, memweight_rs.rs), so a regex
    would be fragile. A one-line table entry is more honest than a
    parser that silently mis-extracts on the next multi-line doc
    comment.

Matching is EXACT string equality on the identifier only — e.g.
GPL-2.0, GPL-2.0-only and GPL-2.0+ are three different real SPDX
identifiers with different legal meaning and are never treated as
equivalent.

Any file whose C original can't be determined (not in the exception
table, and no same-named .c file next to it) is reported WARN, not
silently skipped — it means either a new partial-translation file
needs an exception-table entry, or the naming convention broke.

Usage: check_spdx_provenance.py [--tree linux-riscv]
Output: PASS/FAIL/WARN per file on stdout; exit 1 if any FAIL.
Log: tmp/check_spdx_provenance.log
"""
import argparse
import logging
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / os.environ.get("LINUXRS_TREE", "linux-riscv")
LOG = REPO / "tmp" / "check_spdx_provenance.log"
SPDX_TAG = "SPDX-License-Identifier:"

# Files that are not a 1:1 <name>_rs.rs <-> <name>.c same-directory
# translation. Path keys are relative to the kernel tree root.
EXCEPTIONS = {
    "drivers/tty/serial/8250/8250_helpers_rs.rs": "drivers/tty/serial/8250/8250_port.c",
}


def spdx_of(path: Path) -> str | None:
    """First SPDX-License-Identifier value found in the file's opening
    lines (checked near the top, not strictly line 1, since some files
    have a shebang or other preamble before it)."""
    try:
        head = path.read_text(errors="replace").split("\n", 10)
    except OSError:
        return None
    for line in head[:10]:
        if SPDX_TAG in line:
            return line.split(SPDX_TAG, 1)[1].strip()
    return None


def c_original_for(rs_path: Path, tree: Path) -> Path | None:
    rel = str(rs_path.relative_to(tree))
    if rel in EXCEPTIONS:
        return tree / EXCEPTIONS[rel]
    candidate = rs_path.with_name(rs_path.name.removesuffix("_rs.rs") + ".c")
    return candidate if candidate.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(TREE))
    args = ap.parse_args()

    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    tree = Path(args.tree)
    rs_files = sorted(tree.glob("**/*_rs.rs"))
    if not rs_files:
        logging.warning("no *_rs.rs files found under %s", tree)

    passes, fails, warns = [], [], []
    for rs in rs_files:
        rel_rs = rs.relative_to(tree)
        c = c_original_for(rs, tree)
        if c is None:
            logging.warning("WARN %s — could not determine C original "
                            "(no same-named .c, no exception-table entry)",
                            rel_rs)
            warns.append(rel_rs)
            continue
        rel_c = c.relative_to(tree)
        rs_spdx = spdx_of(rs)
        c_spdx = spdx_of(c)
        if rs_spdx is None:
            logging.warning("WARN %s — no SPDX-License-Identifier found", rel_rs)
            warns.append(rel_rs)
            continue
        if c_spdx is None:
            logging.warning("WARN %s — C original %s has no "
                            "SPDX-License-Identifier", rel_rs, rel_c)
            warns.append(rel_rs)
            continue
        if rs_spdx == c_spdx:
            logging.info("PASS %s == %s (%s)", rel_rs, rel_c, rs_spdx)
            passes.append(rel_rs)
        else:
            logging.error("FAIL %s: %r != %s: %r", rel_rs, rs_spdx, rel_c, c_spdx)
            fails.append((rel_rs, rs_spdx, rel_c, c_spdx))

    logging.info("SPDX provenance: %d pass, %d fail, %d warn (of %d translated files)",
                 len(passes), len(fails), len(warns), len(rs_files))

    if fails:
        logging.error("SPDX PROVENANCE FAIL: %d mismatch(es)", len(fails))
        return 1
    logging.info("SPDX PROVENANCE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
