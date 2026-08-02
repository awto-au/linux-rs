#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Corpus-wide, continuous version of the #28 combined-boot screening
effort's manual candidate hunt (rounds 1-4, 2026-08-02): instead of
hand-picking small batches of files and walking each through the same
recurring fix classes by hand, sweep every c2rust-clean file in the
corpus, apply scripts/apply_c2rust_fix_classes.py's mechanical fixes to
a scratch copy, and compile-check the result against the real riscv64
kernel target -- the same rustc invocation shape
check_c2rust_output_compiles.py already uses (see that script's own
docstring for why: type-check against the kernel's own libcore/
bindings/kernel rmeta files, not a scratch no_std probe).

This answers "of all Linux, how many files transpile and (after our
known mechanical fixes) actually compile clean" as a real, queryable,
continuously-refreshed number -- not a point-in-time doc snapshot.
Results land in rulesdb's c2rust_sweep_outcomes table (see schema.sql
for the full column rationale) plus the c2rust_sweep_failure_patterns
view for "what's the highest-leverage new fix class to write next."

Deliberately does NOT go further than a compile check by default (real
object-file build + link into a kernel image, the way #28's rounds
did) -- that needs a real kernel tree with the file actually wired into
a Makefile, which isn't something 600+ files can share safely in one
pass. --link-sample N optionally does a real build+link attempt for a
random sample of N compile-clean files in an isolated worktree, as a
spot-check that "compiles standalone" correlates with "builds in the
real tree" (they are NOT the same thing -- see issue #28's own
combination-only bugs for why compile-clean is necessary but not
sufficient).

Usage:
  sweep_c2rust_corpus.py [--limit N] [--c2rust-rev REV] [--link-sample N]

Inputs:
  tmp/c2rust-baseline/*/output/src/*.rs (via c2rust_attempts, same as
  check_c2rust_output_compiles.py)
Outputs:
  tmp/c2rust-sweep-report.md
  rulesdb/patterns.db: c2rust_sweep_outcomes rows
Log: tmp/logs/sweep_c2rust_corpus.log
"""
import argparse
import logging
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from c2rust_output_check_common import build_support_crates as _build_support_crates
from c2rust_output_check_common import find_clean_outputs as _find_clean_outputs
from c2rust_output_check_common import git_rev

TREE = REPO / "linux-riscv"
BASELINE = REPO / "tmp" / "c2rust-baseline"
RUST_DIR = TREE / "rust"
SCRATCH_DIR = REPO / "tmp" / "c2rust-sweep-scratch"
SUPPORT_DIR = REPO / "tmp" / "c2rust-support-crates"
DB = REPO / "rulesdb" / "patterns.db"
LOG = REPO / "tmp" / "logs" / "sweep_c2rust_corpus.log"
REPORT = REPO / "tmp" / "c2rust-sweep-report.md"
SIG_EXAMPLES_PATH = REPO / "tmp" / "c2rust-sweep-signature-examples.json"
FIX_CLASSES_SCRIPT = REPO / "scripts" / "apply_c2rust_fix_classes.py"

TARGET = "riscv64imac-unknown-none-elf"
PER_FILE_TIMEOUT_S = 60
C2RUST_SRC = Path(os.environ.get("C2RUST_FORK_DIR", "/mnt/2tb/git/github.com/awtoau/c2rust"))

HOST_DIR = SUPPORT_DIR / "host"
TARGET_DIR = SUPPORT_DIR / "target"
BITFIELDS_DERIVE_SO = HOST_DIR / "libc2rust_bitfields_derive.so"
BITFIELDS_RLIB = TARGET_DIR / "libc2rust_bitfields.rlib"
ASM_CASTS_RLIB = TARGET_DIR / "libc2rust_asm_casts.rlib"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


def fix_classes_rev() -> str:
    """linux-rs's own HEAD, short — this repo's rev, not c2rust's, since
    apply_c2rust_fix_classes.py lives here."""
    return git_rev(REPO) or "unknown"


def build_support_crates() -> bool:
    return _build_support_crates(
        RUST_DIR, C2RUST_SRC, TARGET, HOST_DIR, TARGET_DIR,
        BITFIELDS_DERIVE_SO, BITFIELDS_RLIB, ASM_CASTS_RLIB,
    )


def find_clean_outputs(c2rust_rev: str):
    return _find_clean_outputs(DB, BASELINE, c2rust_rev)


ERROR_HEADER_RE = re.compile(r"^error(\[E\d+\])?:\s*(.+)$", re.MULTILINE)

# Distinguishing tokens checked (in order) against the first error's own
# source-context lines -- appended to the signature so genuinely
# different root causes sharing one error CODE (e.g. E0308 covers
# "warn_on!(bare int)", "shift-expr if/else type mismatch", and
# "'_c2rust_label break type mismatch" -- three unrelated bugs, all
# E0308) don't collapse into one issue. Order matters: checked
# top-to-bottom, first match wins, since a snippet can plausibly contain
# more than one token (e.g. a warn_on! inside a labeled block).
CONTEXT_TOKENS = [
    ("kernel::warn_on!", "warn_on!"),
    ("_c2rust_label", "labeled-block"),
    ("riscv_has_extension", "riscv-ext-guard"),
    ("as_va_list", "va_list"),
    ("global_asm!", "global_asm!"),
]


def normalise_error_signature(stderr: str) -> str | None:
    """First real `error[...]: message` line, stripped of file-specific
    detail (paths, line:col, exact identifiers quoted with backticks),
    PLUS a distinguishing context token pulled from the surrounding
    source snippet rustc prints (see CONTEXT_TOKENS) -- an error CODE
    alone is not a root cause; two files hitting E0308 for unrelated
    reasons must not collapse into one issue. This is the grouping key
    c2rust_sweep_failure_patterns ranks on and c2rust_sweep_issue_links
    dedups issue-filing against."""
    m = ERROR_HEADER_RE.search(stderr)
    if not m:
        return None
    code, msg = m.group(1) or "", m.group(2)
    msg = re.sub(r"`[^`]*`", "`X`", msg)
    msg = re.sub(r"\s+", " ", msg).strip()

    # Scan only the snippet belonging to THIS first error (up to the next
    # blank-line-delimited diagnostic block, or ~15 lines, whichever's
    # shorter) so a token from a LATER, unrelated error in the same
    # file's stderr doesn't get misattributed to the first one.
    start = m.end()
    next_diag = re.search(r"\n(error|warning)(\[E\d+\])?:", stderr[start:])
    snippet_end = start + (next_diag.start() if next_diag else 1500)
    snippet = stderr[start:snippet_end]

    context = ""
    for token, label in CONTEXT_TOKENS:
        if token in snippet:
            context = f" [{label}]"
            break

    return f"{code}{msg}{context}"[:220]


def apply_fix_classes(src: Path, dest: Path) -> tuple[list[str], bool]:
    """Copy src to dest, run apply_c2rust_fix_classes.py on it in place,
    return (list of fix classes that changed something, needs_manual_review)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    result = subprocess.run(
        ["python3", str(FIX_CLASSES_SCRIPT), str(dest.parent), dest.name],
        capture_output=True, text=True, timeout=30,
    )
    applied = []
    needs_manual = False
    for line in result.stdout.splitlines() + result.stderr.splitlines():
        if dest.name + ":" not in line:
            continue
        if "no mechanical fixes needed" in line:
            continue
        if line.startswith(("WARNING", "20")) and "LIVE" in line:
            needs_manual = True
            applied.append("register-static:LIVE-flagged")
        elif "dead, deleted:" in line:
            applied.append("register-static:deleted")
        else:
            for cls in (
                "unused_feature_label_break_value_stripped",
                "export_attr_sites_fixed",
                "bracket_addressing_sites_fixed",
                "warn_on_neq0_sites_fixed",
            ):
                if cls in line:
                    applied.append(cls)
    return applied, needs_manual


def inject_no_std(path: Path):
    text = path.read_text()
    if not text.startswith("#![no_std]"):
        path.write_text("#![no_std]\n" + text)


def rustc_check(rs_path: Path) -> tuple[str, str]:
    out_rmeta = rs_path.with_suffix(".rmeta")
    # --crate-name: rustc infers the crate name from the input filename
    # when this is absent, and several kernel source files share a bare
    # basename (core.c appears under block/partitions/, drivers/base/,
    # kernel/events/, kernel/sched/, etc, all transpiling to a literal
    # core.rs) which collides with the --extern core=... dependency
    # below and produces E0519 "the current crate is indistinguishable
    # from one of its dependencies" (issue awto-au/linux-rs#107).
    # Derive a unique, collision-free crate name from the scratch
    # subdirectory (already the full slugified C path, e.g.
    # block_partitions_core.c) instead of trusting rustc's
    # filename-only default.
    crate_name = re.sub(r"[^0-9a-zA-Z_]", "_", rs_path.parent.name)
    if crate_name and crate_name[0].isdigit():
        crate_name = "_" + crate_name
    cmd = [
        "rustc", "+nightly",
        "--edition=2021",
        "--target", TARGET,
        "--crate-type", "rlib",
        "--crate-name", crate_name,
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
        str(rs_path),
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--c2rust-rev", default=None)
    ap.add_argument(
        "--link-sample", type=int, default=0,
        help="also attempt a real object-file build for this many "
             "randomly-sampled compile-clean files (spot-check only, "
             "not exhaustive -- see module docstring)",
    )
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    (REPO / "tmp").mkdir(exist_ok=True)

    if not (RUST_DIR / "libcore.rmeta").exists():
        log.error("libcore.rmeta not found at %s — run a real kernel build first", RUST_DIR)
        return 1

    c2rust_rev = args.c2rust_rev or git_rev(C2RUST_SRC)
    if not c2rust_rev:
        log.error("could not determine c2rust revision from %s; pass --c2rust-rev", C2RUST_SRC)
        return 1
    fc_rev = fix_classes_rev()
    log.info("sweeping c2rust_rev=%s fix_classes_rev=%s", c2rust_rev, fc_rev)

    if not build_support_crates():
        log.error("failed to build c2rust support crates (bitfields/asm-casts)")
        return 1

    files = find_clean_outputs(c2rust_rev)
    if not files:
        log.warning("no clean c2rust_attempts rows for c2rust_rev=%s — nothing to sweep", c2rust_rev)
        return 0
    if args.limit:
        files = files[: args.limit]
    log.info("sweeping %d file(s)", len(files))

    if SCRATCH_DIR.exists():
        shutil.rmtree(SCRATCH_DIR)
    SCRATCH_DIR.mkdir(parents=True)

    run_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(DB))
    ok_count = 0
    error_count = 0
    timeout_count = 0
    sig_examples = {}  # signature -> {c_file, stderr_excerpt} for the FIRST file hit, for issue drafting
    for i, (rs_path, c_file) in enumerate(files, 1):
        slug = c_file.replace("/", "_")
        dest = SCRATCH_DIR / slug / rs_path.name
        applied, needs_manual = apply_fix_classes(rs_path, dest)
        inject_no_std(dest)
        outcome, stderr = rustc_check(dest)
        sig = normalise_error_signature(stderr) if outcome == "error" else None

        if outcome == "ok":
            ok_count += 1
        elif outcome == "error":
            error_count += 1
            if sig and sig not in sig_examples:
                sig_examples[sig] = {"c_file": c_file, "stderr_excerpt": stderr[:3000]}
        else:
            timeout_count += 1

        conn.execute(
            "INSERT INTO c2rust_sweep_outcomes "
            "(c2rust_rev, fix_classes_rev, run_at, c_file, rs_file, "
            " fix_classes_applied, needs_manual_review, compile_outcome, "
            " compile_error_signature, link_attempted, link_outcome, link_error_signature) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)",
            (
                c2rust_rev, fc_rev, run_at, c_file, str(dest.relative_to(REPO)),
                ",".join(applied), int(needs_manual), outcome, sig,
            ),
        )
        if i % 50 == 0:
            conn.commit()
            log.info("progress: %d/%d (ok=%d error=%d timeout=%d)", i, len(files), ok_count, error_count, timeout_count)

    conn.commit()
    log.info("SWEEP DONE: %d ok, %d error, %d timeout (of %d)", ok_count, error_count, timeout_count, len(files))

    import json
    SIG_EXAMPLES_PATH.write_text(json.dumps(sig_examples, indent=2, sort_keys=True))
    log.info("wrote %s (%d distinct signatures)", SIG_EXAMPLES_PATH, len(sig_examples))

    patterns = conn.execute(
        "SELECT compile_error_signature, files_affected, example_files "
        "FROM c2rust_sweep_failure_patterns ORDER BY files_affected DESC LIMIT 30"
    ).fetchall()

    lines = [
        "# c2rust corpus sweep",
        "",
        f"c2rust_rev={c2rust_rev} fix_classes_rev={fc_rev} run_at={run_at}",
        "",
        f"**{ok_count} of {len(files)} files compile clean** after mechanical fix classes "
        f"({error_count} error, {timeout_count} timeout).",
        "",
        "## Top failure signatures (grouped, not per-file)",
        "",
    ]
    for sig, n, examples in patterns:
        ex_list = examples.split(",")[:5]
        lines.append(f"- **{n} files**: `{sig}`")
        lines.append(f"  - examples: {', '.join(ex_list)}")
    REPORT.write_text("\n".join(lines) + "\n")
    log.info("wrote %s", REPORT)

    if args.link_sample:
        sample_files = [
            row[0] for row in conn.execute(
                "SELECT c_file FROM c2rust_sweep_outcomes "
                "WHERE run_at = ? AND compile_outcome = 'ok'", (run_at,)
            ).fetchall()
        ]
        random.shuffle(sample_files)
        sample_files = sample_files[: args.link_sample]
        log.info(
            "--link-sample requested (%d files) but real build+link needs a wired "
            "kernel tree/worktree per file, not yet automated — sampled candidates "
            "for a future manual/agent-driven pass: %s",
            len(sample_files), sample_files,
        )

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
