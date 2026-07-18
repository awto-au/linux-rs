#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Clippy-check every c2rust-clean-outcome .rs file directly with
clippy-driver against the real riscv64 kernel target (core crate only —
c2rust's raw output is self-contained, references core::ffi::* directly
rather than the bindings crate) to surface idiom/style signal on top of
the plain rustc compile-check in check_c2rust_output_compiles.py.

A standalone clippy-driver invocation on a transpiled file hits an
E0514 SVH (strict version hash) mismatch against the kernel's own
libcore.rmeta: the kernel's libcore.rmeta is built by whatever
concrete nightly build the `nightly` rustup toolchain override in
linux-riscv/ currently resolves to (a *floating* toolchain — its
underlying build changes over time, e.g. commit da80ed070 dated
2026-07-14 as of this writing), not by any of the separately-pinned
dated nightly-YYYY-MM-DD toolchains installed alongside it. Those
dated toolchains build their own independent libcore with a different
SVH, so `clippy-driver` (or `rustc`) from nightly-2026-07-09 will
never match a libcore.rmeta the floating `nightly` toolchain produced,
no matter how close the dates are. The fix is the same one
check_c2rust_output_compiles.py already uses for rustc: invoke the
`+nightly` toolchain selector so cargo/rustup picks the *exact* binary
next to the rustc that built libcore.rmeta, and (since clippy is a
separately-installed rustup component, unlike rustc itself) first
ensure that component is actually installed on that floating
toolchain (`rustup component add --toolchain nightly clippy`) rather
than assuming any nightly on the machine already has it.

Usage: check_c2rust_output_clippy.py [--limit N] [--c2rust-rev REV]
Inputs: tmp/c2rust-baseline/*/output/src/*.rs, linux-riscv/rust/libcore.rmeta
Output: tmp/c2rust-output-clippy-report.md
Log: tmp/check_c2rust_output_clippy.log
"""
import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
BASELINE = REPO / "tmp" / "c2rust-baseline"
RUST_DIR = TREE / "rust"
PROCESSED_DIR = REPO / "tmp" / "c2rust-clippy-check"
SUPPORT_DIR = REPO / "tmp" / "c2rust-support-crates"
DB = REPO / "rulesdb" / "patterns.db"
LOG = REPO / "tmp" / "check_c2rust_output_clippy.log"
REPORT = REPO / "tmp" / "c2rust-output-clippy-report.md"

TARGET = "riscv64imac-unknown-none-elf"
PER_FILE_TIMEOUT_S = 60
# C2RUST_FORK_DIR override — see check_c2rust_output_compiles.py's module
# doc for why a hardcoded default here would silently go stale.
C2RUST_SRC = Path(os.environ.get(
    "C2RUST_FORK_DIR", "/mnt/2tb/git/github.com/awtoau/c2rust"))

HOST_DIR = SUPPORT_DIR / "host"
TARGET_DIR = SUPPORT_DIR / "target"
BITFIELDS_DERIVE_SO = HOST_DIR / "libc2rust_bitfields_derive.so"
BITFIELDS_RLIB = TARGET_DIR / "libc2rust_bitfields.rlib"
ASM_CASTS_RLIB = TARGET_DIR / "libc2rust_asm_casts.rlib"


def current_c2rust_rev() -> str:
    out = subprocess.run(["git", "rev-parse", "--short=9", "HEAD"],
                         cwd=C2RUST_SRC, capture_output=True, text=True, check=True)
    return out.stdout.strip()


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
    """Same authoritative source as check_c2rust_output_compiles.py:
    c2rust_attempts WHERE outcome='clean' AND c2rust_rev=c2rust_rev, not a
    directory glob (would also pick up leftover output/ dirs from earlier
    non-clean attempts)."""
    import sqlite3

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


def ensure_clippy_component():
    """clippy is a separately-installed rustup component, unlike rustc
    itself — a fresh machine (or a `nightly` toolchain that was last
    updated before clippy was added) can have `rustc +nightly` working
    fine while `clippy-driver +nightly` is simply not installed. Installs
    it onto the exact toolchain the `nightly` override resolves to (not
    any dated nightly-YYYY-MM-DD toolchain — see module doc). Idempotent:
    rustup component add is a no-op if already installed."""
    p = subprocess.run(
        ["rustup", "component", "add", "--toolchain", "nightly", "clippy"],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        logging.error("failed to install clippy component on nightly toolchain:\n%s", p.stderr)
        return False
    return True


def build_support_crates():
    """Identical to check_c2rust_output_compiles.py's function of the same
    name — reused rather than re-derived (see that file's module doc for
    the full rationale: c2rust-bitfields' proc-macro deps get linked
    against the kernel's own already-built host-target syn/quote/
    proc-macro2 rlibs, and the target-side rlibs against the kernel's
    real libcore.rmeta, so nothing here uses a separate, SVH-incompatible
    -Zbuild-std=core). Kept as a literal copy (not an import) so this
    script has no fragile cross-script coupling if the compile-check
    script's internals change independently."""
    HOST_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    libcore_mtime = (RUST_DIR / "libcore.rmeta").stat().st_mtime

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

    if not BITFIELDS_RLIB.exists() or BITFIELDS_RLIB.stat().st_mtime < libcore_mtime:
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

    if not ASM_CASTS_RLIB.exists() or ASM_CASTS_RLIB.stat().st_mtime < libcore_mtime:
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


def inject_no_std(rs_path, dest_path):
    """Raises FileNotFoundError if rs_path has vanished since it was
    globbed — tmp/c2rust-baseline/ is live, mutable state that a
    concurrent `dev.py c2rust-baseline --overwrite-existing` run can be
    rewriting (deleting and re-emitting output/src/*.rs) while this
    script is mid-corpus-scan, not a static snapshot. Caller decides
    how to treat that (skip, not crash the whole run)."""
    text = rs_path.read_text()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text("#![no_std]\n" + text)


def clippy_check(rs_path):
    """Same rmeta-linking argv as check_c2rust_output_compiles.py's
    rustc_check(), with clippy-driver in place of rustc and
    --error-format=json so each diagnostic can be persisted individually
    rather than scraped from human-readable text. Returns (outcome,
    diagnostics, raw_stderr) where outcome is 'ok' (ran, 0+ warnings),
    'error' (didn't reach the lint stage — e.g. a genuine compile error),
    'timeout', or 'vanished' (rs_path was globbed earlier but no longer
    exists — tmp/c2rust-baseline/ is live state a concurrent baseline
    run can be rewriting mid-scan, see inject_no_std). diagnostics is a
    list of dicts with level/lint/message/line/col, already filtered to
    warning+error level entries."""
    processed = PROCESSED_DIR / rs_path.relative_to(BASELINE)
    try:
        inject_no_std(rs_path, processed)
    except FileNotFoundError:
        return "vanished", [], ""
    # Same /dev/null-breaks-crate-loading pitfall as rustc_check() — emit
    # to a real per-file scratch path and discard it after.
    out_rmeta = processed.with_suffix(".rmeta")

    cmd = [
        "clippy-driver", "+nightly",
        "--edition=2021",
        "--target", TARGET,
        "--crate-type", "rlib",
        "--emit=metadata",
        "--error-format=json",
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
        return "timeout", [], ""
    finally:
        out_rmeta.unlink(missing_ok=True)

    diagnostics = []
    for line in p.stderr.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("$message_type") != "diagnostic":
            continue
        level = d.get("level")
        if level not in ("warning", "error"):
            continue
        code = (d.get("code") or {}).get("code") or "unknown"
        message = d.get("message", "")
        span = next(iter(d.get("spans") or []), None)
        diagnostics.append({
            "lint": code,
            "level": level,
            "message": message,
            "line": (span or {}).get("line_start"),
            "col": (span or {}).get("column_start"),
        })

    # clippy-driver's own returncode reflects whether it reached and
    # completed the lint pass, not whether warnings were found — a file
    # that lints clean still exits 0, and a file with only warnings (no
    # errors) also exits 0. A nonzero exit with zero parsed diagnostics
    # means it never got that far (e.g. a genuine E0514/E0*** compile
    # error before clippy's lints even ran) — that's the failure mode
    # this script cares about distinguishing from "ran fine, N warnings".
    if p.returncode != 0 and not diagnostics:
        return "error", [], p.stderr
    return "ok", diagnostics, p.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--c2rust-rev",
        default=None,
        help="c2rust revision to clippy-check (default: current HEAD of "
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
    logging.info("clippy-checking c2rust_rev=%s", c2rust_rev)

    if not ensure_clippy_component():
        logging.error("failed to ensure clippy component on the nightly toolchain")
        return 1

    if not build_support_crates():
        logging.error("failed to build c2rust support crates (bitfields/asm-casts)")
        return 1

    files = find_clean_outputs(c2rust_rev)
    if not files:
        # See check_c2rust_output_compiles.py's main() for why this is a
        # loud print, not just a log line: a c2rust_rev mismatch here
        # would otherwise silently produce a "Checked: 0 files" report.
        print(f"WARNING: no clean c2rust_attempts rows found for c2rust_rev={c2rust_rev} "
              f"in {DB} — the report will show 'Checked: 0 files'. Pass --c2rust-rev "
              f"to match the revision the baseline was actually run against.")
        logging.warning("no clean outputs found for c2rust_rev=%s — proceeding anyway "
                        "with an empty file list", c2rust_rev)
    if args.limit:
        files = files[: args.limit]
    logging.info("clippy-checking %d c2rust output files against real riscv64 target", len(files))

    results = {}          # rel path -> outcome
    all_diagnostics = {}  # rel path -> list of diagnostic dicts
    error_samples = {}
    for i, (rs_path, c_file) in enumerate(files, 1):
        outcome, diagnostics, stderr = clippy_check(rs_path)
        rel = str(rs_path.relative_to(REPO))
        results[rel] = outcome
        all_diagnostics[rel] = diagnostics
        if outcome == "error":
            error_samples[rel] = stderr[:4000]
        if i % 20 == 0 or i == len(files):
            logging.info("%d/%d checked", i, len(files))

    from collections import Counter
    counts = Counter(results.values())
    total_warnings = sum(len(d) for d in all_diagnostics.values())
    lint_counts = Counter(
        d["lint"] for diags in all_diagnostics.values() for d in diags
    )
    logging.info("DONE: %s, total warnings/errors: %d", dict(counts), total_warnings)

    lines = [
        "# c2rust raw output: clippy check (kernel-toolchain-linked, riscv64 target)",
        "",
        f"Checked: {len(files)} files (c2rust 'clean' outcome)",
        f"Results: {dict(counts)}",
        f"Total clippy diagnostics: {total_warnings}",
        "",
        "## Top lint types",
        "",
    ]
    for lint, n in lint_counts.most_common(30):
        lines.append(f"- [{n}] `{lint}`")
    lines.append("")
    lines.append("## Per-file warning counts (nonzero only)")
    lines.append("")
    for rel in sorted(all_diagnostics):
        n = len(all_diagnostics[rel])
        if n:
            lines.append(f"- {rel}: {n}")
    if error_samples:
        lines.append("")
        lines.append("## Files that failed to reach the lint stage")
        for rel, sample in sorted(error_samples.items()):
            lines.append(f"\n### {rel}\n```\n{sample}\n```")

    REPORT.write_text("\n".join(lines))
    logging.info("wrote %s", REPORT)

    # Persist to patterns.db — c2rust_clippy_runs (one row per file, incl.
    # clean files, so "checked but 0 warnings" is distinguishable from
    # "never checked") and c2rust_clippy_outcomes (one row per warning).
    if DB.exists():
        import sqlite3
        from datetime import datetime, timezone
        run_at = datetime.now(timezone.utc).isoformat()
        db_conn = sqlite3.connect(str(DB))
        db_conn.execute(
            "CREATE TABLE IF NOT EXISTS c2rust_clippy_runs ("
            "id INTEGER PRIMARY KEY, c2rust_rev TEXT NOT NULL, run_at TEXT NOT NULL, "
            "rs_file TEXT NOT NULL, warning_count INTEGER NOT NULL, outcome TEXT NOT NULL)"
        )
        db_conn.execute(
            "CREATE TABLE IF NOT EXISTS c2rust_clippy_outcomes ("
            "id INTEGER PRIMARY KEY, c2rust_rev TEXT NOT NULL, run_at TEXT NOT NULL, "
            "rs_file TEXT NOT NULL, lint_name TEXT NOT NULL, level TEXT NOT NULL, "
            "message TEXT NOT NULL, line INTEGER, col INTEGER)"
        )
        db_conn.executemany(
            "INSERT INTO c2rust_clippy_runs (c2rust_rev, run_at, rs_file, warning_count, outcome) "
            "VALUES (?,?,?,?,?)",
            [(c2rust_rev, run_at, rel, len(all_diagnostics[rel]), outcome)
             for rel, outcome in results.items()],
        )
        db_conn.executemany(
            "INSERT INTO c2rust_clippy_outcomes "
            "(c2rust_rev, run_at, rs_file, lint_name, level, message, line, col) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(c2rust_rev, run_at, rel, d["lint"], d["level"], d["message"], d["line"], d["col"])
             for rel, diags in all_diagnostics.items() for d in diags],
        )
        db_conn.commit()
        db_conn.close()
        logging.info("persisted %d file runs / %d diagnostics to %s (rev=%s)",
                     len(results), total_warnings, DB, c2rust_rev)
    else:
        logging.warning("patterns.db not found at %s — clippy outcomes not persisted "
                        "(run scripts/build_db.py first)", DB)

    print(f"CHECK OK: {dict(counts)}, {total_warnings} diagnostics -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
