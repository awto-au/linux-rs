#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Diff the FULL rustc error set per file between two versions of
apply_c2rust_fix_classes.py (a git rev vs the working tree).

- sweep_c2rust_corpus.py records only the FIRST error per file; a fix
  class that clears one error and introduces another shows up there as
  a signature shift, not a regression. This shows what is actually new.
- Reuses the sweep's rustc invocation and corpus lookup.

Usage:
  diff_fix_class_errors.py --base REV --c2rust-rev REV C_FILE [C_FILE ...]
Log: tmp/logs/diff_fix_class_errors.log
"""
import argparse
import collections
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import REPO  # noqa: E402

LOG = REPO / "tmp" / "logs" / "diff_fix_class_errors.log"
WORK = REPO / "tmp" / "fix-class-diff"
LOG.parent.mkdir(parents=True, exist_ok=True)
# Configure before importing the sweep module so its basicConfig is a no-op.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG), logging.StreamHandler()],
)
log = logging.getLogger(__name__)
import sweep_c2rust_corpus as sweep  # noqa: E402

# error[E0308]: msg \n --> path:line:col  -> (code+msg, line)
ERR_RE = re.compile(r"^error(\[E\d+\])?: (.+)\n\s*--> [^:]+:(\d+):\d+", re.MULTILINE)


def errors(stderr: str, rs: Path) -> collections.Counter:
    """Keyed on message + offending source line text, not line number:
    a fix that adds/removes a line must not show every later error as new."""
    src = rs.read_text().splitlines()
    return collections.Counter(
        f"{code or ''}{msg} @ {src[int(line) - 1].strip()[:120]}"
        for code, msg, line in ERR_RE.findall(stderr)
    )


def run(script: Path, src: Path, dest: Path) -> collections.Counter:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    # Timeout: same 30 s bound the sweep uses for one fix-classes pass.
    # Nonzero exit = "something flagged", not failure (sweep ignores it too).
    subprocess.run(
        ["python3", str(script), str(dest.parent), dest.name],
        capture_output=True, text=True, timeout=30,
    )
    sweep.inject_no_std(dest)
    outcome, stderr = sweep.rustc_check(dest)
    if outcome == "timeout":
        log.error("%s: rustc timed out (%d s)", dest, sweep.PER_FILE_TIMEOUT_S)
    return errors(stderr, dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="git rev of the 'before' fix-classes script")
    ap.add_argument("--c2rust-rev", required=True)
    ap.add_argument("c_files", nargs="+")
    args = ap.parse_args()

    base_script = WORK / "base" / "scripts" / "apply_c2rust_fix_classes.py"
    base_script.parent.mkdir(parents=True, exist_ok=True)
    blob = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{args.base}:scripts/apply_c2rust_fix_classes.py"],
        capture_output=True, text=True, check=True,
    ).stdout
    base_script.write_text(blob)
    (WORK / "base" / "tmp" / "logs").mkdir(parents=True, exist_ok=True)  # its file-relative log dir

    by_file = {c: rs for rs, c in sweep.find_clean_outputs(args.c2rust_rev)}
    rc = 0
    for c_file in args.c_files:
        rs = by_file.get(c_file)
        if rs is None:
            log.error("%s: no clean c2rust output for rev %s", c_file, args.c2rust_rev)
            rc = 1
            continue
        slug = c_file.replace("/", "_")
        before = run(base_script, rs, WORK / "before" / slug / rs.name)
        after = run(sweep.FIX_CLASSES_SCRIPT, rs, WORK / "after" / slug / rs.name)
        new, gone = sorted((after - before).elements()), sorted((before - after).elements())
        log.info("%s: before=%d after=%d new=%d gone=%d", c_file, before.total(), after.total(), len(new), len(gone))
        for e in new:
            log.info("  NEW  %s", e)
    return rc


if __name__ == "__main__":
    sys.exit(main())
