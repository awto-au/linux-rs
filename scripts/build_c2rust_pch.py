#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a Clang precompiled header (PCH) for the dominant compile-flag
group in compile_commands.json, so c2rust transpile (and any other
Clang-based tool) can skip re-lexing/re-parsing the kernel's own headers
(compiler-version.h/kconfig.h/compiler_types.h and everything they
transitively pull in) on every single-TU invocation.

Every kernel .c file in this build compiles with its own combination of
per-subsystem flags (-I search paths, -ffreestanding vs hosted, -fpic,
target CPU tuning, etc) — a PCH is only valid for TUs whose *stable*
flags (everything except the per-file KBUILD_* identity macros and -o/-c/
file path) match exactly what it was built with; Clang enforces this at
load time (see -include-pch's ABI/flag validity check) and refuses a
mismatched PCH rather than silently producing different output. Grouping
compile_commands.json by stable flag set finds the single largest group
worth building a shared PCH for; the remaining minority groups fall back
to plain per-TU -include (see run_c2rust_baseline.py).

Usage: build_c2rust_pch.py
Output: tmp/c2rust-pch/preamble.pch, tmp/c2rust-pch/dominant_flags.json
        (the exact flag list the PCH was built with, consumed by
        run_c2rust_baseline.py to detect per-file group membership)
Log: tmp/build_c2rust_pch.log
"""
import json
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
TMP = REPO / "tmp"
LOG = TMP / "build_c2rust_pch.log"
OUT_DIR = TMP / "c2rust-pch"
PCH_FILE = OUT_DIR / "preamble.pch"
FLAGS_FILE = OUT_DIR / "dominant_flags.json"

# Per-file identity macros KBUILD injects (module path/name for
# __FILE__-style diagnostics and MODULE_* macros) — these are the only
# things that differ between TUs that otherwise share every other flag,
# so they're stripped before grouping by "stable" flag set. Also strips
# -c/-o <obj>/the trailing .c path (the TU-specific compile action) and
# the -Wp,-MMD,<dep> flag (per-file dep-file path).
PER_FILE_PREFIXES = (
    "-DKBUILD_MODFILE=",
    "-DKBUILD_BASENAME=",
    "-DKBUILD_MODNAME=",
    "-D__KBUILD_MODNAME=",
    "-Wp,-MMD,",
)


def strip_per_file_flags(tokens):
    """tokens is a compile command already split on whitespace, with the
    leading compiler name and trailing .c file path already removed.
    Returns the stable subset as a tuple (hashable, for grouping)."""
    out = []
    skip_next = False
    for t in tokens:
        if skip_next:
            skip_next = False
            continue
        if t == "-o":
            skip_next = True
            continue
        if t == "-c":
            continue
        if any(t.startswith(p) for p in PER_FILE_PREFIXES):
            continue
        out.append(t)
    return tuple(out)


def split_command(command):
    """Split a compile_commands.json command string into
    (compiler, flags_without_trailing_file, file_token)."""
    toks = command.split()
    file_tok = toks[-1]
    compiler = toks[0]
    body = toks[1:-1]
    return compiler, body, file_tok


def dominant_flag_group(entries):
    """Group deduped .c entries by stable flag set, return
    (flags_tuple, member_file_list) for the largest group."""
    from collections import defaultdict

    groups = defaultdict(list)
    for e in entries:
        _compiler, body, _file_tok = split_command(e["command"])
        stripped = strip_per_file_flags(body)
        groups[stripped].append(e["file"])

    sizes = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    for i, (flags, files) in enumerate(sizes[:5]):
        logging.info("flag-group %d: %d files (e.g. %s)", i, len(files), files[0])
    return sizes[0]


# The 3 kernel-internal headers every real per-TU compile command
# -includes ahead of the .c file itself (compiler identity, Kconfig
# macro plumbing, then the __GNUC__/__clang__ compiler_types shims those
# depend on) — confirmed from a real compile_commands.json entry, not
# assumed. A PCH built over anything less wouldn't actually skip the
# expensive part (these headers transitively pull in most of the
# preprocessor-macro-heavy kernel header tree).
REQUIRED_INCLUDES = (
    "./include/linux/compiler-version.h",
    "./include/linux/kconfig.h",
    "./include/linux/compiler_types.h",
)


def build_pch(flags):
    """flags is the stable-flag tuple (dominant group), still containing
    the 3 -include flags real per-TU commands use. Builds a PCH from an
    empty translation unit compiled with those same flags: the -include
    triple does all the real work (pulling in the shared header tree),
    an empty root file just gives Clang something to open."""
    flags = list(flags)
    for inc in REQUIRED_INCLUDES:
        if inc not in flags:
            raise RuntimeError(
                f"expected -include {inc} in dominant flag set, not found — "
                f"the dominant group's flags may not match what "
                f"run_c2rust_baseline.py's real per-TU commands use"
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    preamble = OUT_DIR / "preamble.h"
    preamble.write_text("")

    # -emit-pch is a -cc1 (frontend) flag in this Clang build, not
    # exposed at the driver level directly (confirmed: bare -emit-pch
    # gives "unknown argument", -Xclang -emit-pch works) — same
    # underlying PCH format either way, -include-pch (used at transpile
    # time) is a driver-level flag and doesn't need -Xclang.
    #
    # -fconst-strings: a bare per-TU compile of a .c file in C mode
    # defaults string-literal constness off (plain C: string literals are
    # char*, not const char*), and a PCH built without this flag matches
    # that default. But c2rust's ast-exporter unconditionally injects
    # -Wwrite-strings into every translation unit it processes (see
    # augment_argv() in c2rust-ast-exporter/src/AstExporter.cpp) — and in
    # C mode -Wwrite-strings doesn't just warn, it flips string literals
    # to const-qualified, same as -fconst-strings. So c2rust's own
    # consuming compile always has const-strings *on*, and the PCH must
    # be built to match that (not the bare-clang default) or Clang's
    # PCH-validity check refuses to load it ("const-qualified string
    # support was disabled in precompiled file ... but is currently
    # enabled") — same class of bug as a -ffreestanding mismatch, just a
    # different flag, flipped by a tool-injected warning rather than by
    # -emit-pch itself.
    cmd = (
        ["clang", "-x", "c-header"]
        + flags
        + ["-Xclang", "-fconst-strings", "-Xclang", "-emit-pch",
           "-o", str(PCH_FILE), str(preamble)]
    )
    logging.info("building PCH: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=TREE, capture_output=True, text=True)
    if proc.returncode != 0:
        logging.error("PCH build failed (rc=%d)\nstdout:\n%s\nstderr:\n%s",
                       proc.returncode, proc.stdout, proc.stderr)
        raise RuntimeError("clang -emit-pch failed")
    logging.info("PCH written: %s (%d bytes)", PCH_FILE, PCH_FILE.stat().st_size)


def main():
    TMP.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    cc_path = TREE / "compile_commands.json"
    if not cc_path.exists():
        logging.error("no %s", cc_path)
        return 1

    entries = [
        e for e in json.load(open(cc_path))
        if e["file"].endswith(".c") and "/scripts/" not in e["file"]
    ]
    seen = {}
    for e in entries:
        seen[e["file"]] = e
    entries = sorted(seen.values(), key=lambda e: e["file"])
    logging.info("%d deduped non-scripts/ .c TUs", len(entries))

    flags, files = dominant_flag_group(entries)
    logging.info("dominant flag group: %d/%d files (%.0f%%)",
                  len(files), len(entries), 100 * len(files) / len(entries))

    build_pch(flags)

    FLAGS_FILE.write_text(json.dumps({
        "flags": list(flags),
        "member_count": len(files),
        "total_count": len(entries),
    }, indent=1))
    logging.info("dominant flags recorded at %s", FLAGS_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
