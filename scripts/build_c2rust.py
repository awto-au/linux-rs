#!/usr/bin/env python3
"""Build the awtoau/c2rust fork correctly and verify the result is usable.

Exists because `cargo build --release --bin c2rust` alone only builds the
`c2rust` dispatcher binary (c2rust/src/main.rs) — it does NOT build
`c2rust-transpile`, the separate binary the dispatcher shells out to for
`c2rust transpile ...`. A `--bin c2rust`-only build leaves any existing
`c2rust-transpile` untouched, so a rebuild after a source change can
silently keep running the OLD transpile binary while `c2rust` itself
looks freshly built — `run_c2rust_baseline.py`'s binary_stale_warning()
only checks the dispatcher's mtime, not this sibling, so it doesn't catch
this class of staleness either. Hit this for real 2026-07-18: a
`--bin c2rust` build after merging two transpiler fixes reported a fresh
mtime while `c2rust transpile` was still running the pre-merge binary,
which regressed an already-verified fix back to its broken state on a
full-corpus baseline (317 dropped_decls instead of 7) before anyone
noticed the binary itself was stale, not the fix.

Fix: always `cargo build --release` (the whole workspace, no --bin
filter), then verify every binary the dispatcher can reach — c2rust and
c2rust-transpile at minimum — exists and is newer than every tracked
source file in the fork.
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_FORK = Path("/mnt/2tb/git/github.com/awtoau/c2rust")

# The only c2rust subcommand binaries this project actually invokes.
# c2rust's own SubCommand::known() lists more (refactor, instrument, pdg,
# postprocess) but nothing here calls them — checking their freshness too
# would just make every build here fail on unrelated crates.
REQUIRED_BINS = ["c2rust", "c2rust-transpile"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def stale_sources(fork_dir, bin_path):
    bin_mtime = bin_path.stat().st_mtime
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=fork_dir,
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.splitlines()
    return [
        f for f in tracked
        if (fork_dir / f).exists() and (fork_dir / f).stat().st_mtime > bin_mtime
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fork-dir", type=Path, default=DEFAULT_FORK)
    args = ap.parse_args()
    fork_dir = args.fork_dir

    logging.info("cargo build --release in %s (full workspace, not --bin-filtered)", fork_dir)
    proc = subprocess.run(
        ["cargo", "build", "--release"], cwd=fork_dir,
        capture_output=True, text=True, timeout=900,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        logging.error("cargo build failed (returncode %d)", proc.returncode)
        return 1

    release_dir = fork_dir / "target" / "release"
    bad = False
    for name in REQUIRED_BINS:
        bin_path = release_dir / name
        if not bin_path.exists():
            logging.error("%s missing after build — expected at %s", name, bin_path)
            bad = True
            continue
        newer = stale_sources(fork_dir, bin_path)
        if newer:
            logging.error(
                "%s is OLDER than %d tracked source file(s) (e.g. %s) despite "
                "just running cargo build — investigate before trusting this binary",
                name, len(newer), newer[0],
            )
            bad = True
        else:
            logging.info("%s: fresh (newer than all tracked sources)", name)

    if bad:
        return 1
    logging.info("BUILD OK: %s ready at %s", ", ".join(REQUIRED_BINS), release_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
