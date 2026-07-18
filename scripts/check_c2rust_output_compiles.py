#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile-check every c2rust-clean-outcome .rs file directly with rustc
against the real riscv64 kernel target (core crate only — c2rust's raw
output is self-contained, references core::ffi::* directly rather than
the bindings crate) to see how many survive real compilation, not just
c2rust's own "clean" transpile outcome.

"clean" from c2rust means no dropped decls / no crash at transpile time —
it says nothing about whether the emitted Rust is valid Rust. This is the
next real signal: rustc --emit=metadata (type-check only, no codegen)
against the kernel's own libcore.rmeta, riscv64 target and cfg flags.

Usage: check_c2rust_output_compiles.py [--limit N] [--c2rust-rev REV]
Inputs: tmp/c2rust-baseline/*/output/src/*.rs, linux-riscv/rust/libcore.rmeta
Output: tmp/c2rust-output-compile-report.md
Log: tmp/check_c2rust_output_compiles.log
"""
import argparse
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
BASELINE = REPO / "tmp" / "c2rust-baseline"
RUST_DIR = TREE / "rust"
PROCESSED_DIR = REPO / "tmp" / "c2rust-compile-check"
SUPPORT_DIR = REPO / "tmp" / "c2rust-support-crates"
DB = REPO / "rulesdb" / "patterns.db"
LOG = REPO / "tmp" / "check_c2rust_output_compiles.log"
REPORT = REPO / "tmp" / "c2rust-output-compile-report.md"

TARGET = "riscv64imac-unknown-none-elf"
PER_FILE_TIMEOUT_S = 60
C2RUST_SRC = Path("/mnt/2tb/git/github.com/awtoau/c2rust")


def current_c2rust_rev() -> str:
    """HEAD of the real awtoau/c2rust checkout — a hardcoded rev here
    would silently go stale every time the fork advances, checking old
    DB rows without anyone noticing."""
    out = subprocess.run(["git", "rev-parse", "--short=9", "HEAD"],
                         cwd=C2RUST_SRC, capture_output=True, text=True, check=True)
    return out.stdout.strip()


C2RUST_REV = current_c2rust_rev()

HOST_DIR = SUPPORT_DIR / "host"
TARGET_DIR = SUPPORT_DIR / "target"
BITFIELDS_DERIVE_SO = HOST_DIR / "libc2rust_bitfields_derive.so"
BITFIELDS_RLIB = TARGET_DIR / "libc2rust_bitfields.rlib"
ASM_CASTS_RLIB = TARGET_DIR / "libc2rust_asm_casts.rlib"

# c2rust-bitfields depends on a proc-macro (c2rust-bitfields-derive), which
# in turn depends on syn/quote/proc-macro2 at exact pinned versions
# (=2.0.106 / =1.0.40 / =1.0.103). Rather than a separate `cargo build
# -Zbuild-std=core` (which links c2rust-bitfields against a *different*,
# incompatible libcore build than the kernel's own — confirmed via E0460
# "found possibly newer version of crate `core`", an SVH/crate-hash
# mismatch since the two core builds come from independent rustc
# invocations), we reuse the kernel's own already-built host-target
# libproc_macro2.rlib / libquote.rlib / libsyn.rlib (linux-riscv/rust/,
# built by the kernel's own Rust proc-macro pipeline for `macros`/
# `pin_init_internal`, same versions, same nightly toolchain, syn built
# with the `full` feature which is a superset of what bitfields-derive
# needs) and link everything against the kernel's real libcore.rmeta.


def build_support_crates():
    """Build c2rust-bitfields (+ its derive proc-macro) and c2rust-asm-casts
    for TARGET, linked against the kernel's own libcore.rmeta. Idempotent —
    skips crates that already exist. Returns True on success."""
    HOST_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    if not BITFIELDS_DERIVE_SO.exists():
        cmd = [
            "rustc", "+nightly",
            "--edition=2021",
            "--crate-type", "proc-macro",
            "--crate-name", "c2rust_bitfields_derive",
            "-O",
            "--out-dir", str(HOST_DIR),
            "-L", str(RUST_DIR),
            "--extern", "proc_macro",
            "--extern", "proc_macro2=" + str(RUST_DIR / "libproc_macro2.rlib"),
            "--extern", "quote=" + str(RUST_DIR / "libquote.rlib"),
            "--extern", "syn=" + str(RUST_DIR / "libsyn.rlib"),
            str(C2RUST_SRC / "c2rust-bitfields-derive" / "src" / "lib.rs"),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            logging.error("failed to build c2rust_bitfields_derive:\n%s", p.stderr)
            return False
        logging.info("built %s", BITFIELDS_DERIVE_SO)

    if not BITFIELDS_RLIB.exists():
        cmd = [
            "rustc", "+nightly",
            "--edition=2021",
            "--target", TARGET,
            "--crate-type", "rlib",
            "--crate-name", "c2rust_bitfields",
            "--cfg", 'feature="no_std"',
            "-O",
            "--sysroot=/dev/null",
            "-Cpanic=abort",
            "--out-dir", str(TARGET_DIR),
            "-L", str(RUST_DIR),
            "--extern", "core=" + str(RUST_DIR / "libcore.rmeta"),
            "--extern", "c2rust_bitfields_derive=" + str(BITFIELDS_DERIVE_SO),
            "-Zunstable-options",
            str(C2RUST_SRC / "c2rust-bitfields" / "src" / "lib.rs"),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            logging.error("failed to build c2rust_bitfields:\n%s", p.stderr)
            return False
        logging.info("built %s", BITFIELDS_RLIB)

    if not ASM_CASTS_RLIB.exists():
        cmd = [
            "rustc", "+nightly",
            "--edition=2021",
            "--target", TARGET,
            "--crate-type", "rlib",
            "--crate-name", "c2rust_asm_casts",
            "-O",
            "--sysroot=/dev/null",
            "-Cpanic=abort",
            "--out-dir", str(TARGET_DIR),
            "-L", str(RUST_DIR),
            "--extern", "core=" + str(RUST_DIR / "libcore.rmeta"),
            "-Zunstable-options",
            str(C2RUST_SRC / "c2rust-asm-casts" / "src" / "lib.rs"),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            logging.error("failed to build c2rust_asm_casts:\n%s", p.stderr)
            return False
        logging.info("built %s", ASM_CASTS_RLIB)

    return True

# c2rust's raw output uses nightly-only #![feature(...)] attributes
# (raw_ref_op, extern_types, core_intrinsics, ...) — the kernel tree
# itself now builds with nightly Rust too (see `rustup override set
# nightly` in linux-riscv/, done to keep this check on the exact same
# toolchain/libcore/libbindings the real kernel build produces, rather
# than a disposable scratch-dir core build).
#
# c2rust never emits #![no_std] itself, so every file needs it injected
# as the first line before rustc will type-check it against a no_std
# libcore (otherwise rustc immediately fails looking for the std crate).
# We write processed copies under PROCESSED_DIR rather than touching
# tmp/c2rust-baseline/ in place, since that tree is the raw transpile
# record other tooling (rulesdb) treats as authoritative.


def git_rev(repo_dir):
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True, check=True,
            timeout=30,
        ).stdout.strip()
    except Exception:
        return None


def find_clean_outputs(c2rust_rev):
    """Authoritative list: c2rust_attempts WHERE outcome='clean' AND
    c2rust_rev=c2rust_rev. Globbing output/ dirs directly would also
    pick up leftover output/ dirs from earlier failed/non-clean attempts."""
    import sqlite3

    # c2rust_attempts is append-only (one row per baseline run, no
    # dedup on c_file+c2rust_rev — see run_c2rust_baseline.py), so
    # re-running the baseline more than once at the same rev leaves
    # multiple identical rows behind. DISTINCT keeps this function's
    # file list (and the "Checked: N files" figure derived from it)
    # accurate regardless of how many times the baseline happened to
    # be (re-)run at this rev.
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT DISTINCT c_file FROM c2rust_attempts WHERE outcome='clean' AND c2rust_rev=?",
        (c2rust_rev,),
    ).fetchall()
    conn.close()

    files = []
    missing = []
    for (c_file,) in rows:
        slug = c_file.replace("/", "_")
        matches = sorted((BASELINE / slug / "output" / "src").glob("*.rs"))
        if not matches:
            missing.append(c_file)
            continue
        files.extend((m, c_file) for m in matches)
    if missing:
        logging.warning("%d clean DB rows had no output/src/*.rs on disk: %s",
                         len(missing), missing[:10])
    return sorted(files, key=lambda pair: pair[0])


def inject_no_std(rs_path, dest_path):
    text = rs_path.read_text()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text("#![no_std]\n" + text)


def rustc_check(rs_path):
    processed = PROCESSED_DIR / rs_path.relative_to(BASELINE)
    inject_no_std(rs_path, processed)
    # -o /dev/null breaks --emit=metadata crate loading in ways that
    # surface as spurious "can't find crate" errors for *other* --extern
    # crates (confirmed by bisecting: the same command against a trivial
    # probe file only succeeds once -o points at a real writable path).
    # Emit to a real per-file scratch path instead and discard it after.
    out_rmeta = processed.with_suffix(".rmeta")

    cmd = [
        "rustc", "+nightly",
        "--edition=2021",
        "--target", TARGET,
        "--crate-type", "rlib",
        "--emit=metadata",
        "-o", str(out_rmeta),
        "--sysroot=/dev/null",
        "-L", str(RUST_DIR),
        "-L", str(HOST_DIR),
        "-L", str(TARGET_DIR),
        "--extern", "core=" + str(RUST_DIR / "libcore.rmeta"),
        "--extern", "bindings=" + str(RUST_DIR / "libbindings.rmeta"),
        "--extern", "kernel=" + str(RUST_DIR / "libkernel.rmeta"),
        "--extern", "c2rust_bitfields=" + str(BITFIELDS_RLIB),
        "--extern", "c2rust_bitfields_derive=" + str(BITFIELDS_DERIVE_SO),
        "--extern", "c2rust_asm_casts=" + str(ASM_CASTS_RLIB),
        "-Cpanic=abort",
        "-Zunstable-options",
        str(processed),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=PER_FILE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    finally:
        out_rmeta.unlink(missing_ok=True)
    if p.returncode == 0:
        return "ok", ""
    return "error", p.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--c2rust-rev",
        default=None,
        help="c2rust revision to compile-check (default: current HEAD of "
             f"{C2RUST_SRC}; use this to inspect an older baseline snapshot)",
    )
    args = ap.parse_args()

    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="a"), logging.StreamHandler(sys.stdout)],
    )

    if not (RUST_DIR / "libcore.rmeta").exists():
        logging.error("libcore.rmeta not found at %s — run a real kernel build first", RUST_DIR)
        return 1

    c2rust_rev = args.c2rust_rev or git_rev(C2RUST_SRC)
    if not c2rust_rev:
        logging.error("could not determine c2rust revision from %s; pass --c2rust-rev", C2RUST_SRC)
        return 1
    logging.info("compile-checking c2rust_rev=%s", c2rust_rev)

    if not build_support_crates():
        logging.error("failed to build c2rust support crates (bitfields/asm-casts)")
        return 1

    files = find_clean_outputs(c2rust_rev)
    if args.limit:
        files = files[: args.limit]
    logging.info("checking %d c2rust output files against real riscv64 target", len(files))

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_spdx_provenance import check_pair as spdx_check_pair
    from check_spdx_provenance import check_export_gpl_upgrades

    spdx_fails = []
    export_gpl_warnings = []
    results = {}
    error_samples = {}
    error_first_lines = {}
    for i, (rs_path, c_file) in enumerate(files, 1):
        outcome, stderr = rustc_check(rs_path)
        rel = str(rs_path.relative_to(REPO))
        results[rel] = outcome

        # Same SPDX-provenance check the hand-translation cycle runs
        # (scripts/check_spdx_provenance.py) — c2rust mechanically
        # carries the source header through by default, but that's an
        # assumption, not a guarantee (e.g. a future c2rust flag or
        # idiom-rewrite pass could touch the header), so every clean
        # output file is checked here too, not just files that survive
        # to a real hand-verified landing.
        c_path = TREE / c_file
        if c_path.exists():
            spdx_status, spdx_detail = spdx_check_pair(rs_path, c_path)
            if spdx_status == "fail":
                logging.error("SPDX FAIL %s", spdx_detail)
                spdx_fails.append(spdx_detail)
            # Same rule 0001 EXPORT_SYMBOL->_GPL WARN check the
            # hand-translation cycle runs (check_spdx_provenance.py) —
            # c2rust's raw output never emits #[export] itself (it has
            # no concept of linux-rs's proc-macro, see
            # check_c2rust_rule_conformance.py's check_0001 stub), so
            # this only ever fires on a c2rust output file someone has
            # since hand-patched to add #[export], not on raw
            # transpiled output — included here for completeness/
            # symmetry with the hand-translation cycle regardless.
            for symbol, gpl_detail in check_export_gpl_upgrades(rs_path, c_path):
                logging.warning("WARN (export-gpl) %s", gpl_detail)
                export_gpl_warnings.append(gpl_detail)
        if outcome == "error":
            # Scan the FULL stderr (not the truncated sample below) for the
            # first genuine `error[...]`/`error: ...` line, skipping the
            # `error: aborting due to N previous errors` summary line that
            # rustc always appends last (it's not itself a distinct error
            # class, just a footer) and warning lines (files can emit
            # hundreds of style warnings before their real errors).
            first_error = next(
                (l for l in stderr.splitlines()
                 if l.startswith("error") and not l.startswith("error: aborting due to")),
                "error: (unknown)",
            )
            error_first_lines[rel] = first_error
            error_samples[rel] = stderr[:4000]
        if i % 20 == 0 or i == len(files):
            logging.info("%d/%d checked", i, len(files))

    from collections import Counter
    counts = Counter(results.values())
    logging.info("DONE: %s", dict(counts))
    logging.info("SPDX provenance (rulesdb/rules/0029-spdx-provenance.toml): "
                 "%d fail, %d checked", len(spdx_fails), len(files))
    logging.info("EXPORT_SYMBOL->_GPL silent upgrades (rule 0001): %d warning(s)",
                 len(export_gpl_warnings))

    # First-line error signature counts, for prioritization
    sig_counts = Counter(error_first_lines.values())

    lines = [
        "# c2rust raw output: real rustc compile-check (metadata only, riscv64 target)",
        "",
        f"Checked: {len(files)} files (c2rust 'clean' outcome)",
        f"Results: {dict(counts)}",
        f"SPDX provenance (rule 0029): {len(spdx_fails)} mismatch(es) of {len(files)} checked",
        f"EXPORT_SYMBOL->_GPL silent upgrades (rule 0001): {len(export_gpl_warnings)} warning(s)",
        "",
    ]
    if spdx_fails:
        lines.append("## SPDX provenance failures")
        lines.append("")
        for detail in spdx_fails:
            lines.append(f"- {detail}")
        lines.append("")
    if export_gpl_warnings:
        lines.append("## EXPORT_SYMBOL->_GPL silent upgrade warnings (rule 0001)")
        lines.append("")
        for detail in export_gpl_warnings:
            lines.append(f"- {detail}")
        lines.append("")
    lines += [
        "## Top error signatures (first `error:` line, one sample file each)",
        "",
    ]
    for sig, n in sig_counts.most_common(30):
        lines.append(f"- [{n}] `{sig}`")
    lines.append("")
    lines.append("## Full error output per failing file")
    for rs_path, outcome in sorted(results.items()):
        if outcome == "error":
            lines.append(f"\n### {rs_path}\n```\n{error_samples.get(rs_path, '')}\n```")

    REPORT.write_text("\n".join(lines))
    logging.info("wrote %s", REPORT)
    print(f"CHECK OK: {dict(counts)} -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
