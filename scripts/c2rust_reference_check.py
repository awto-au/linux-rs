#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run current c2rust against every file in the 38-file hand-translated
corpus and report whether the output is faithful/complete — NOT to
replace the hand-translated files (they stay as the reference/oracle
per README.md's "Two tracks" design), but because c2rust has never
actually been run against most of them: their C originals were dropped
from compile_commands.json the moment the Makefile switched to the
Rust version, so there was never a compile-commands entry to transpile
from.

This is read-only investigation. It NEVER edits a *_rs.rs file, never
lands anything, never hand-fixes c2rust's output. A real gap here (a
crash, a silently dropped function, a translation that doesn't compile)
is a c2rust bug — file it and fix c2rust itself, never patch around it
in this script or by hand.

Mechanism: the 38 files' C originals aren't in the main tree's
compile_commands.json (Makefile already points at *_rs.o). This script
uses an isolated worktree with CONFIG_RUST=n to get a real C build of
every file, generates a real compile_commands.json from that, then
transpiles each of the 38 files' *C originals* against current c2rust.

Usage:
  c2rust_reference_check.py                    # all 38 files
  c2rust_reference_check.py --limit N
  c2rust_reference_check.py lib/math/gcd.c      # just one

Log: tmp/c2rust_reference_check.log
Output: tmp/c2rust-reference-check/<safe_name>/output/src/*.rs
        tmp/c2rust-reference-check-report.md
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "c2rust_reference_check.log"
DB = REPO / "rulesdb" / "patterns.db"
C2RUST_BIN = REPO.parent / "github.com" / "awtoau" / "c2rust" / "target" / "release" / "c2rust"
WORKTREE_NAME = "c2rust-recheck-baseline"
WORKTREE = REPO / "linux-riscv-worktrees" / WORKTREE_NAME
OUT_ROOT = REPO / "tmp" / "c2rust-reference-check"
REPORT = REPO / "tmp" / "c2rust-reference-check-report.md"

TEST_CONFIGS_BY_TU = {
    "lib/tests/base64_kunit.c": ["CONFIG_BASE64_KUNIT"],
    "lib/tests/cmdline_kunit.c": ["CONFIG_CMDLINE_KUNIT_TEST"],
    "lib/tests/test_list_sort.c": ["CONFIG_TEST_LIST_SORT"],
    "lib/test-kstrtox.c": ["CONFIG_TEST_KSTRTOX"],
    "init/initramfs_test.c": ["CONFIG_INITRAMFS_TEST"],
}


def sh(cmd, cwd=None, timeout=1800, check=True):
    log = logging.getLogger(__name__)
    log.info("+ %s (cwd=%s)", " ".join(map(str, cmd)), cwd or "")
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if p.stdout:
        log.info(p.stdout[-4000:])
    if p.stderr:
        log.info(p.stderr[-4000:])
    if check and p.returncode != 0:
        raise RuntimeError(f"rc={p.returncode}: {' '.join(map(str, cmd))}\n{p.stderr[-2000:]}")
    return p


def get_translated_tus():
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT c_file FROM translated_tus ORDER BY c_file").fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_all_c_from_compile_commands(cc_path: Path) -> list[str]:
    data = json.loads(cc_path.read_text())
    out: list[str] = []
    seen: set[str] = set()
    for e in data:
        file_path = Path(e.get("file", ""))
        if file_path.suffix != ".c":
            continue
        try:
            rel = str(file_path.resolve().relative_to(WORKTREE.resolve()))
        except Exception:
            # Skip entries outside the expected worktree.
            continue
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
    return sorted(out)


def build_compile_commands_index(cc_path: Path) -> dict[str, dict]:
    data = json.loads(cc_path.read_text())
    idx: dict[str, dict] = {}
    wt = WORKTREE.resolve()
    for e in data:
        file_raw = e.get("file")
        if not isinstance(file_raw, str):
            continue
        p = Path(file_raw)
        if p.suffix != ".c":
            continue
        try:
            rel = str(p.resolve().relative_to(wt))
        except Exception:
            continue
        idx.setdefault(rel, e)
    return idx


def normalize_requested_tus(tus: list[str], log: logging.Logger) -> list[str]:
    out: list[str] = []
    for t in tus:
        if t not in out:
            out.append(t)
    return out


def clear_transpile_output(c_file: str) -> None:
    """Remove output from an earlier run before attempting this TU.

    A failed or missing compile-command lookup must never leave an older Rust
    file looking like the output of the current reference-check run.
    """
    safe = c_file.replace("/", "_")
    out_dir = OUT_ROOT / safe / "output"
    if out_dir.exists():
        shutil.rmtree(out_dir)


def required_config_symbols_for_tus(requested_tus: list[str]) -> list[str]:
    symbols: list[str] = []
    for tu in requested_tus:
        for symbol in TEST_CONFIGS_BY_TU.get(tu, []):
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols


def ensure_c_side_worktree(log, requested_tus: list[str]):
    """Isolated worktree with CONFIG_RUST=n, fully built, so every one
    of the 38 files' C originals gets a real .cmd file and thus a real
    compile_commands.json entry."""
    if WORKTREE.exists():
        log.info("reusing existing worktree %s", WORKTREE)
    else:
        sh(["python3", str(REPO / "scripts" / "linux_riscv_worktree.py"),
            "create", WORKTREE_NAME])

    config_script = WORKTREE / "scripts" / "config"
    sh([str(config_script), "--file", str(WORKTREE / ".config"), "-d", "CONFIG_RUST"])
    extra_symbols = required_config_symbols_for_tus(requested_tus)
    if extra_symbols:
        log.info("enabling %d test config symbol(s) for requested TUs: %s",
                 len(extra_symbols), extra_symbols)
        sh([
            str(config_script),
            "--file",
            str(WORKTREE / ".config"),
            *[item for symbol in extra_symbols for item in ("-e", symbol)],
        ])
    sh(["make", "-C", str(WORKTREE), "ARCH=riscv", "LLVM=1", "olddefconfig"])

    cc_path = WORKTREE / "compile_commands.json"
    if cc_path.exists():
        log.info("compile_commands.json already present, checking it actually covers "
                 "the requested TU set before reusing")
        data = json.loads(cc_path.read_text())
        covered = {Path(e["file"]).name for e in data}
        missing = [t for t in requested_tus if Path(t).name not in covered]
        if not missing:
            log.info("existing compile_commands.json covers all %d requested TUs, reusing",
                     len(requested_tus))
            return cc_path
        log.info("existing compile_commands.json missing %d TU(s), rebuilding: %s",
                  len(missing), missing[:5])

    log.info("building full C-only kernel in %s (this takes a while)", WORKTREE)
    sh(["make", "-C", str(WORKTREE), "ARCH=riscv", "LLVM=1", "-j32"], timeout=3600)
    sh(["make", "-C", str(WORKTREE), "ARCH=riscv", "LLVM=1", "compile_commands.json"])
    return cc_path


def transpile_one(c_file: str, entry: dict, log) -> dict:
    safe = c_file.replace("/", "_")
    iso_dir = OUT_ROOT / safe / "cc_isolated"
    iso_dir.mkdir(parents=True, exist_ok=True)
    cc_file = iso_dir / "compile_commands.json"
    cc_file.write_text(json.dumps([entry], indent=2))

    out_dir = OUT_ROOT / safe / "output"
    p = subprocess.run(
        [str(C2RUST_BIN), "transpile", str(cc_file), "-o", str(out_dir),
         "--overwrite-existing", "--enable-rule=all"],
        capture_output=True, text=True, timeout=300,
    )
    # c2rust can leave partial output behind on failure. It is not valid
    # reference material, so remove it before reporting the failed result.
    if p.returncode != 0 and out_dir.exists():
        shutil.rmtree(out_dir)
    result = {
        "c_file": c_file,
        "returncode": p.returncode,
        "stderr_tail": p.stderr[-2000:] if p.stderr else "",
    }
    rs_files = list((out_dir / "src").glob("*.rs")) if (out_dir / "src").exists() else []
    result["rs_files"] = [str(f) for f in rs_files]
    if p.returncode != 0:
        result["status"] = "CRASH_OR_ERROR"
    elif not rs_files:
        result["status"] = "NO_OUTPUT"
    else:
        result["status"] = "OK"
    log.info("%s: %s (rc=%d, %d output files)",
              c_file, result["status"], p.returncode, len(rs_files))
    return result


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger(__name__)

    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="specific c_file(s) to check; default: all 38")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--jobs",
        type=int,
        default=min(4, max(1, (os.cpu_count() or 1))),
        help="parallel c2rust transpile jobs (default: min(4, nproc))",
    )
    ap.add_argument(
        "--all-c-from-compile-commands",
        action="store_true",
        help="check every .c file present in worktree compile_commands.json",
    )
    args = ap.parse_args()

    if args.all_c_from_compile_commands and args.files:
        log.error("cannot combine positional files with --all-c-from-compile-commands")
        return 2

    requested_tus: list[str]
    if args.all_c_from_compile_commands:
        cc_path = ensure_c_side_worktree(log, [])
        requested_tus = get_all_c_from_compile_commands(cc_path)
        if args.limit:
            requested_tus = requested_tus[: args.limit]
        log.info("checking %d compile_commands .c TU(s) against fresh c2rust", len(requested_tus))
    else:
        requested_tus = get_translated_tus()
        if args.files:
            requested_tus = normalize_requested_tus(args.files, log)
        else:
            requested_tus = normalize_requested_tus(requested_tus, log)
        if args.limit:
            requested_tus = requested_tus[: args.limit]
        log.info("checking %d translated TU(s) against fresh c2rust", len(requested_tus))
        # Clear before worktree/config preparation, since those operations can
        # fail too. A failed invocation must never expose output from an older
        # successful invocation as though it were fresh.
        for c_file in requested_tus:
            clear_transpile_output(c_file)
        cc_path = ensure_c_side_worktree(log, requested_tus)

    log.info("transpile worker jobs=%d", args.jobs)
    cc_index = build_compile_commands_index(cc_path)

    results = []
    work_items: list[tuple[str, dict]] = []
    for c_file in requested_tus:
        clear_transpile_output(c_file)
        entry = cc_index.get(c_file)
        if entry is None:
            results.append({"c_file": c_file, "status": "NO_COMPILE_COMMANDS_ENTRY"})
            log.warning("%s: no compile_commands.json entry found", c_file)
            continue
        work_items.append((c_file, entry))

    if work_items:
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            futures = {
                pool.submit(transpile_one, c_file, entry, log): c_file
                for c_file, entry in work_items
            }
            done = 0
            total = len(futures)
            for fut in as_completed(futures):
                c_file = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as exc:
                    log.exception("%s: worker failure", c_file)
                    results.append(
                        {
                            "c_file": c_file,
                            "status": "CRASH_OR_ERROR",
                            "returncode": -1,
                            "stderr_tail": str(exc),
                            "rs_files": [],
                        }
                    )
                done += 1
                if done % 25 == 0 or done == total:
                    log.info("progress: %d/%d transpiles complete", done, total)

    lines = ["# c2rust reference-check: 38-file hand-translated corpus", ""]
    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r["c_file"])
    for status, files in sorted(by_status.items()):
        lines.append(f"## {status} ({len(files)})")
        for f in files:
            lines.append(f"- {f}")
        lines.append("")
    REPORT.write_text("\n".join(lines))
    log.info("wrote %s", REPORT)

    ok = len(by_status.get("OK", []))
    total = len(results)
    print(f"REFERENCE-CHECK: {ok}/{total} OK, "
          f"{total - ok} need investigation (see {REPORT})")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
