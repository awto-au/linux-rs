#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Shared helpers for check_c2rust_output_compiles.py and
check_c2rust_output_clippy.py: both scripts independently defined
byte-identical copies of git_rev(), find_clean_outputs(), and
build_support_crates() (plus a dead, unused current_c2rust_rev() only
in the clippy script — a leftover of the same import-time-crash bug
check_c2rust_output_compiles.py's C2RUST_REV had, fixed there in
026995b, deleted here rather than carried forward). One real function,
two copies to keep in sync — consolidated 2026-07-31 as part of the
code-review action plan's duplicated-helper-logic cleanup.

Takes its paths as explicit parameters rather than closing over each
caller's module-level globals, so the two callers stay decoupled from
each other's internals (the original literal-copy design's stated
intent — see check_c2rust_output_clippy.py's pre-refactor
build_support_crates() docstring) while still sharing one
implementation to keep in sync.
"""
import logging
import sqlite3
import subprocess


def git_rev(repo_dir):
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True, check=True,
            timeout=30,
        ).stdout.strip()
    except Exception:
        return None


def find_clean_outputs(db, baseline, c2rust_rev):
    """Authoritative list: c2rust_attempts WHERE outcome='clean' AND
    c2rust_rev=c2rust_rev. Globbing output/ dirs directly would also
    pick up leftover output/ dirs from earlier failed/non-clean
    attempts."""
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT DISTINCT c_file FROM c2rust_attempts WHERE outcome='clean' AND c2rust_rev=?",
        (c2rust_rev,),
    ).fetchall()
    conn.close()

    files = []
    missing = []
    for (c_file,) in rows:
        slug = c_file.replace("/", "_")
        matches = sorted((baseline / slug / "output" / "src").glob("*.rs"))
        if not matches:
            missing.append(c_file)
            continue
        files.extend((m, c_file) for m in matches)
    if missing:
        logging.warning("%d clean DB rows had no output/src/*.rs on disk: %s",
                         len(missing), missing[:10])
    return sorted(files, key=lambda pair: pair[0])


def build_support_crates(rust_dir, c2rust_src, target, host_dir, target_dir,
                          bitfields_derive_so, bitfields_rlib, asm_casts_rlib):
    """Build c2rust-bitfields (+ its derive proc-macro) and c2rust-asm-casts
    for `target`, linked against the kernel's own libcore.rmeta. Idempotent —
    skips crates that already exist and are newer than libcore.rmeta.
    Returns True on success.

    c2rust-bitfields depends on a proc-macro (c2rust-bitfields-derive),
    which in turn depends on syn/quote/proc-macro2 at exact pinned
    versions (=2.0.106 / =1.0.40 / =1.0.103). Rather than a separate
    `cargo build -Zbuild-std=core` (which links c2rust-bitfields against
    a *different*, incompatible libcore build than the kernel's own —
    confirmed via E0460 "found possibly newer version of crate `core`",
    an SVH/crate-hash mismatch since the two core builds come from
    independent rustc invocations), we reuse the kernel's own
    already-built host-target libproc_macro2.rlib / libquote.rlib /
    libsyn.rlib (linux-riscv/rust/, built by the kernel's own Rust
    proc-macro pipeline for `macros`/`pin_init_internal`, same versions,
    same nightly toolchain, syn built with the `full` feature which is a
    superset of what bitfields-derive needs) and link everything against
    the kernel's real libcore.rmeta."""
    host_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    # bitfields_rlib/asm_casts_rlib are --extern-linked against
    # libcore.rmeta (bitfields_derive_so is a host proc-macro and isn't),
    # so a rebuilt kernel tree that regenerates libcore.rmeta with a new
    # SVH must force these two to rebuild too, or the caller's compile
    # check links every candidate file against a stale rlib and reports
    # a whole-corpus "found possibly newer version of crate `core`"
    # mismatch as if it were a per-file compile error.
    libcore_mtime = (rust_dir / "libcore.rmeta").stat().st_mtime

    if not bitfields_derive_so.exists():
        cmd = [
            "rustc", "+nightly",
            "--edition=2021",
            "--crate-type", "proc-macro",
            "--crate-name", "c2rust_bitfields_derive",
            "-O",
            "--out-dir", str(host_dir),
            "-L", str(rust_dir),
            "--extern", "proc_macro",
            "--extern", "proc_macro2=" + str(rust_dir / "libproc_macro2.rlib"),
            "--extern", "quote=" + str(rust_dir / "libquote.rlib"),
            "--extern", "syn=" + str(rust_dir / "libsyn.rlib"),
            str(c2rust_src / "c2rust-bitfields-derive" / "src" / "lib.rs"),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            logging.error("failed to build c2rust_bitfields_derive:\n%s", p.stderr)
            return False
        logging.info("built %s", bitfields_derive_so)

    if not bitfields_rlib.exists() or bitfields_rlib.stat().st_mtime < libcore_mtime:
        cmd = [
            "rustc", "+nightly",
            "--edition=2021",
            "--target", target,
            "--crate-type", "rlib",
            "--crate-name", "c2rust_bitfields",
            "--cfg", 'feature="no_std"',
            "-O",
            "--sysroot=/dev/null",
            "-Cpanic=abort",
            "--out-dir", str(target_dir),
            "-L", str(rust_dir),
            "--extern", "core=" + str(rust_dir / "libcore.rmeta"),
            "--extern", "c2rust_bitfields_derive=" + str(bitfields_derive_so),
            "-Zunstable-options",
            str(c2rust_src / "c2rust-bitfields" / "src" / "lib.rs"),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            logging.error("failed to build c2rust_bitfields:\n%s", p.stderr)
            return False
        logging.info("built %s", bitfields_rlib)

    if not asm_casts_rlib.exists() or asm_casts_rlib.stat().st_mtime < libcore_mtime:
        cmd = [
            "rustc", "+nightly",
            "--edition=2021",
            "--target", target,
            "--crate-type", "rlib",
            "--crate-name", "c2rust_asm_casts",
            "-O",
            "--sysroot=/dev/null",
            "-Cpanic=abort",
            "--out-dir", str(target_dir),
            "-L", str(rust_dir),
            "--extern", "core=" + str(rust_dir / "libcore.rmeta"),
            "-Zunstable-options",
            str(c2rust_src / "c2rust-asm-casts" / "src" / "lib.rs"),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            logging.error("failed to build c2rust_asm_casts:\n%s", p.stderr)
            return False
        logging.info("built %s", asm_casts_rlib)

    return True
