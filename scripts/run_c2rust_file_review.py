#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Mechanical runner for per-file c2rust review workflow.

This is a maintained, evolving workflow script (not a one-off helper).
As the review checklist grows, this runner should absorb new mechanical
checks so day-to-day review remains repeatable.

Automates checklist steps 2/3/4/7 for one or more C files:
- fresh c2rust reference generation
- translated_tus DB mapping lookup
- provenance diff artifact generation
- tier-2.5 differential oracle check when bench/diff_<stem>.{c,rs} exists
- TODO marker sync/check enforcement

It can also discover target files from existing provenance artifacts
(`tmp/*_provenance.diff`) so previously-reviewed files can be replayed.

Usage examples:
  run_c2rust_file_review.py lib/base64.c
  run_c2rust_file_review.py --from-existing-provenance
  run_c2rust_file_review.py --from-existing-provenance --limit 2

Log:
  tmp/run_c2rust_file_review.log

Summary:
  tmp/run_c2rust_file_review-summary.md
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import sqlite3
import datetime
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "run_c2rust_file_review.log"
SUMMARY = REPO / "tmp" / "run_c2rust_file_review-summary.md"
DB = REPO / "rulesdb" / "patterns.db"
TMP = REPO / "tmp"
EXTERN_FN_RE = re.compile(
    r'^\s*pub\s+(?:unsafe\s+)?extern\s+"C"\s+fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(',
    re.MULTILINE,
)
EXTERN_SIG_RE = re.compile(
    r'pub\s+(?:unsafe\s+)?extern\s+"C"\s+fn\s+'
    r'([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)\s*'
    r'(?:->\s*([^\{;]+))?',
    re.MULTILINE | re.DOTALL,
)
TOP_FN_RE = re.compile(
    r'^\s*(?:pub\s+)?(?:unsafe\s+)?fn\s+'
    r'([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?:->\s*[^\{;]+)?\s*\{',
    re.MULTILINE | re.DOTALL,
)
PUB_CONST_RE = re.compile(r'^\s*pub\s+const\s+[A-Za-z_][A-Za-z0-9_]*\s*[:=]', re.MULTILINE)
PUB_TYPE_RE = re.compile(r'^\s*pub\s+type\s+[A-Za-z_][A-Za-z0-9_]*\s*=', re.MULTILINE)

# Known benign top-level helper/scaffolding drift seen in c2rust output.
# Keep this list small and explicit; expand only with reviewed evidence.
KNOWN_BENIGN_TOP_FN_MISSING_IN_LANDED = {
    "__must_check_overflow",
    "_bin2bcd",
    "c2rust_run_static_initializers",
    "_printk",
    "bunzip2",
    "do_csum",
    "gunzip",
    "memcmp",
    "simple_strtol",
    "simple_strtoull",
    "sized_strscpy",
    "skip_spaces",
    "strlen",
    "strncmp",
    "unlz4",
    "unlzma",
    "unlzo",
    "unxz",
}
KNOWN_BENIGN_TOP_FN_EXTRA_IN_LANDED = {
    "from64to32",
    "get_range",
    "is_space",
    "ptr_align",
}
KNOWN_BENIGN_EXTERN_SIG_PARAM_MISMATCH = {
    "_bcd2bin",
    "_bin2bcd",
    "base64_decode",
    "base64_encode",
    "csum_partial",
    "csum_tcpudp_nofold",
    "decompress_method",
    "find_cpio_data",
    "get_option",
    "get_options",
    "ip_compute_csum",
    "memparse",
    "next_arg",
    "parse_option_str",
}

# Tier-2.5 oracle targets are usually stem-aligned (lib/base64.c -> base64),
# but some translated slices intentionally map to a differently named oracle.
ORACLE_TARGET_BY_C_FILE = {
    "drivers/tty/serial/8250/8250_port.c": "8250_helpers",
}

# Existing in-tree tests we can transpile-check for each translated file.
TEST_TUS_BY_C_FILE = {
    "lib/base64.c": ["lib/tests/base64_kunit.c"],
    "lib/cmdline.c": ["lib/tests/cmdline_kunit.c"],
    "lib/list_sort.c": ["lib/tests/test_list_sort.c"],
    "lib/kstrtox.c": ["lib/test-kstrtox.c"],
    "lib/decompress.c": ["init/initramfs_test.c"],
    "lib/earlycpio.c": ["init/initramfs_test.c"],
}
# Keep in sync with c2rust_reference_check.py's TEST_CONFIGS_BY_TU — same
# test TU -> CONFIG_..._KUNIT_TEST mapping, duplicated here (not imported)
# to match this file's existing subprocess-not-import convention for that
# sibling script. Found 2026-07-31: integrate_tu.py step1 previously ran
# with no --kunit at all, so a file's KUnit suite only actually ran when
# its CONFIG_TEST_* happened to already be enabled in the baseline
# .config (true for cmdline/list_sort by luck, false for kstrtox) —
# "pass (via integrate_tu)" silently meant "booted", not "suite ran".
TEST_CONFIGS_BY_TU = {
    "lib/tests/base64_kunit.c": ["CONFIG_BASE64_KUNIT"],
    "lib/tests/cmdline_kunit.c": ["CONFIG_CMDLINE_KUNIT_TEST"],
    "lib/tests/test_list_sort.c": ["CONFIG_TEST_LIST_SORT"],
    "lib/test-kstrtox.c": ["CONFIG_TEST_KSTRTOX"],
    "init/initramfs_test.c": ["CONFIG_INITRAMFS_TEST"],
}
C2RUST_REF_OUT_ROOT = TMP / "c2rust-reference-check"
UNSAFE_TEST_RE = re.compile(r"\bunsafe\s*(?:\{|fn\b|impl\b|trait\b|extern\b)")

def ensure_review_tracking_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_file_review_tracking ("
        "c_file TEXT PRIMARY KEY, "
        "step1_status TEXT NOT NULL, "
        "step1_detail TEXT, "
        "oracle_status TEXT NOT NULL, "
        "oracle_target TEXT, "
        "issue_note TEXT, "
        "updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_file_review_steps ("
        "id INTEGER PRIMARY KEY, "
        "c_file TEXT NOT NULL, "
        "run_at TEXT NOT NULL, "
        "step_name TEXT NOT NULL, "
        "step_status TEXT NOT NULL, "
        "detail TEXT, "
        "artifact_path TEXT, "
        "duration_s REAL, "
        "created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_file_review_state ("
        "c_file TEXT PRIMARY KEY, "
        "latest_run_at TEXT NOT NULL, "
        "fresh_output_status TEXT NOT NULL, "
        "mapping_status TEXT NOT NULL, "
        "diff_status TEXT NOT NULL, "
        "static_status TEXT NOT NULL, "
        "step1_status TEXT NOT NULL, "
        "oracle_status TEXT NOT NULL, "
        "test_probe_status TEXT NOT NULL, "
        "semantic_review_status TEXT NOT NULL, "
        "todo_status TEXT NOT NULL, "
        "issue_update_status TEXT NOT NULL, "
        "disposition TEXT NOT NULL, "
        "note TEXT, "
        "updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE VIEW IF NOT EXISTS c2rust_function_review_state AS "
        "SELECT "
        "f.file AS c_file, "
        "f.name, "
        "f.line, "
        "s.latest_run_at, "
        "s.fresh_output_status, "
        "s.mapping_status, "
        "s.diff_status, "
        "s.static_status, "
        "s.step1_status, "
        "s.oracle_status, "
        "s.test_probe_status, "
        "s.semantic_review_status, "
        "s.todo_status, "
        "s.issue_update_status, "
        "s.disposition, "
        "s.note "
        "FROM functions f "
        "LEFT JOIN c2rust_file_review_state s "
        "ON s.c_file = f.file OR s.c_file = replace(f.file, 'linux-riscv/', '')"
    )


def record_review_step(
    c_file: str,
    *,
    run_at: str,
    step_name: str,
    step_status: str,
    detail: str,
    artifact_path: str = "",
    duration_s: float | None = None,
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(DB)
    try:
        ensure_review_tracking_schema(conn)
        conn.execute(
            "INSERT INTO c2rust_file_review_steps "
            "(c_file, run_at, step_name, step_status, detail, artifact_path, duration_s, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (c_file, run_at, step_name, step_status, detail, artifact_path, duration_s, now),
        )
        conn.commit()
    finally:
        conn.close()


def record_review_state(
    c_file: str,
    *,
    run_at: str,
    fresh_output_status: str,
    mapping_status: str,
    diff_status: str,
    static_status: str,
    step1_status: str,
    oracle_status: str,
    test_probe_status: str,
    semantic_review_status: str,
    todo_status: str,
    issue_update_status: str,
    disposition: str,
    note: str,
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(DB)
    try:
        ensure_review_tracking_schema(conn)
        conn.execute(
            "INSERT INTO c2rust_file_review_state "
            "(c_file, latest_run_at, fresh_output_status, mapping_status, diff_status, static_status, "
            "step1_status, oracle_status, test_probe_status, semantic_review_status, todo_status, "
            "issue_update_status, disposition, note, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(c_file) DO UPDATE SET "
            "latest_run_at=excluded.latest_run_at, "
            "fresh_output_status=excluded.fresh_output_status, "
            "mapping_status=excluded.mapping_status, "
            "diff_status=excluded.diff_status, "
            "static_status=excluded.static_status, "
            "step1_status=excluded.step1_status, "
            "oracle_status=excluded.oracle_status, "
            "test_probe_status=excluded.test_probe_status, "
            "semantic_review_status=excluded.semantic_review_status, "
            "todo_status=excluded.todo_status, "
            "issue_update_status=excluded.issue_update_status, "
            "disposition=excluded.disposition, "
            "note=excluded.note, "
            "updated_at=excluded.updated_at",
            (
                c_file,
                run_at,
                fresh_output_status,
                mapping_status,
                diff_status,
                static_status,
                step1_status,
                oracle_status,
                test_probe_status,
                semantic_review_status,
                todo_status,
                issue_update_status,
                disposition,
                note,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def normalize_step_status(value: str) -> str:
    if not value:
        return "not-run"
    if value == "pass":
        return "pass"
    if value in {"fail", "blocked", "error", "not-mapped", "not-requested"}:
        return value
    if value.startswith("pass"):
        return "pass"
    if value.startswith("fail"):
        return "fail"
    if value.startswith("missing"):
        return "missing"
    if value.startswith("skipped"):
        return "skipped"
    return value


def record_review_tracking(
    c_file: str,
    *,
    step1_status: str,
    step1_detail: str,
    oracle_status: str,
    oracle_target: str,
    issue_note: str,
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(DB)
    try:
        ensure_review_tracking_schema(conn)
        conn.execute(
            "INSERT INTO c2rust_file_review_tracking "
            "(c_file, step1_status, step1_detail, oracle_status, oracle_target, issue_note, updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(c_file) DO UPDATE SET "
            "step1_status=excluded.step1_status, "
            "step1_detail=excluded.step1_detail, "
            "oracle_status=excluded.oracle_status, "
            "oracle_target=excluded.oracle_target, "
            "issue_note=excluded.issue_note, "
            "updated_at=excluded.updated_at",
            (c_file, step1_status, step1_detail, oracle_status, oracle_target, issue_note, now),
        )
        conn.commit()
    finally:
        conn.close()


def ensure_test_probe_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_file_test_probe ("
        "c_file TEXT PRIMARY KEY, "
        "probe_status TEXT NOT NULL, "
        "probe_detail TEXT, "
        "updated_at TEXT NOT NULL)"
    )
    # Backfill newer counters when upgrading an existing DB in-place.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(c2rust_file_test_probe)")}
    if "unsafe_hits" not in existing_cols:
        conn.execute("ALTER TABLE c2rust_file_test_probe ADD COLUMN unsafe_hits INTEGER NOT NULL DEFAULT 0")
    if "unsafe_unapproved_hits" not in existing_cols:
        conn.execute(
            "ALTER TABLE c2rust_file_test_probe "
            "ADD COLUMN unsafe_unapproved_hits INTEGER NOT NULL DEFAULT 0"
        )
    if "unsafe_exception_hits" not in existing_cols:
        conn.execute(
            "ALTER TABLE c2rust_file_test_probe "
            "ADD COLUMN unsafe_exception_hits INTEGER NOT NULL DEFAULT 0"
        )

    conn.execute(
        "CREATE TABLE IF NOT EXISTS c2rust_test_probe_exceptions ("
        "c_file TEXT NOT NULL, "
        "test_tu TEXT NOT NULL, "
        "rationale TEXT NOT NULL, "
        "approved_by TEXT, "
        "approved_at TEXT NOT NULL, "
        "PRIMARY KEY(c_file, test_tu))"
    )


def record_test_probe(
    c_file: str,
    *,
    probe_status: str,
    probe_detail: str,
    unsafe_hits: int,
    unsafe_unapproved_hits: int,
    unsafe_exception_hits: int,
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(DB)
    try:
        ensure_test_probe_schema(conn)
        conn.execute(
            "INSERT INTO c2rust_file_test_probe "
            "(c_file, probe_status, probe_detail, unsafe_hits, unsafe_unapproved_hits, "
            "unsafe_exception_hits, updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(c_file) DO UPDATE SET "
            "probe_status=excluded.probe_status, "
            "probe_detail=excluded.probe_detail, "
            "unsafe_hits=excluded.unsafe_hits, "
            "unsafe_unapproved_hits=excluded.unsafe_unapproved_hits, "
            "unsafe_exception_hits=excluded.unsafe_exception_hits, "
            "updated_at=excluded.updated_at",
            (
                c_file,
                probe_status,
                probe_detail,
                unsafe_hits,
                unsafe_unapproved_hits,
                unsafe_exception_hits,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def load_test_unsafe_exceptions(c_file: str) -> dict[str, str]:
    conn = sqlite3.connect(DB)
    try:
        ensure_test_probe_schema(conn)
        rows = conn.execute(
            "SELECT test_tu, rationale FROM c2rust_test_probe_exceptions "
            "WHERE c_file IN (?, '*')",
            (c_file,),
        ).fetchall()
        return {str(test_tu): str(rationale) for test_tu, rationale in rows}
    finally:
        conn.close()


def transpiled_rs_outputs_for_tu(test_tu: str) -> list[Path]:
    safe = test_tu.replace("/", "_")
    out_src = C2RUST_REF_OUT_ROOT / safe / "output" / "src"
    if not out_src.exists():
        return []
    return sorted(out_src.glob("*.rs"))


def count_unsafe_hits(rs_file: Path) -> int:
    text = rs_file.read_text(errors="replace")
    return len(UNSAFE_TEST_RE.findall(text))


def run_existing_test_transpile_probe(c_file: str) -> tuple[str, str, int, int, int]:
    log = logging.getLogger(__name__)
    tests = TEST_TUS_BY_C_FILE.get(c_file, [])
    if not tests:
        log.info("[%s] test-probe: no mapped existing tests", c_file)
        return ("not-mapped", "no mapped existing tests", 0, 0, 0)

    approved_exceptions = load_test_unsafe_exceptions(c_file)
    log.info(
        "[%s] test-probe: mapped tests=%d approved-exceptions=%d",
        c_file,
        len(tests),
        len(approved_exceptions),
    )

    failed: list[str] = []
    no_output: list[str] = []
    unsafe_unapproved: list[str] = []
    unsafe_approved: list[str] = []
    unsafe_hits = 0
    unsafe_unapproved_hits = 0
    unsafe_exception_hits = 0

    for test_tu in tests:
        test_start = time.monotonic()
        log.info("[%s] test-probe: transpile-check start %s", c_file, test_tu)
        p = run_cmd_no_raise_in_repo([
            "python3",
            "scripts/c2rust_reference_check.py",
            test_tu,
        ])
        if p.returncode != 0:
            log.info(
                "[%s] test-probe: transpile-check fail %s rc=%d (%.2fs)",
                c_file,
                test_tu,
                p.returncode,
                time.monotonic() - test_start,
            )
            failed.append(test_tu)
            continue

        rs_files = transpiled_rs_outputs_for_tu(test_tu)
        if not rs_files:
            log.info(
                "[%s] test-probe: no rust output %s (%.2fs)",
                c_file,
                test_tu,
                time.monotonic() - test_start,
            )
            no_output.append(test_tu)
            continue

        tu_unsafe_hits = sum(count_unsafe_hits(rs_file) for rs_file in rs_files)
        unsafe_hits += tu_unsafe_hits
        if tu_unsafe_hits <= 0:
            log.info(
                "[%s] test-probe: transpile-check pass %s unsafe_hits=0 (%.2fs)",
                c_file,
                test_tu,
                time.monotonic() - test_start,
            )
            continue
        if test_tu in approved_exceptions:
            unsafe_exception_hits += tu_unsafe_hits
            log.info(
                "[%s] test-probe: unsafe approved %s hits=%d (%.2fs)",
                c_file,
                test_tu,
                tu_unsafe_hits,
                time.monotonic() - test_start,
            )
            unsafe_approved.append(
                f"{test_tu} ({tu_unsafe_hits} unsafe hit(s), approved: {approved_exceptions[test_tu]})"
            )
        else:
            unsafe_unapproved_hits += tu_unsafe_hits
            log.info(
                "[%s] test-probe: unsafe unapproved %s hits=%d (%.2fs)",
                c_file,
                test_tu,
                tu_unsafe_hits,
                time.monotonic() - test_start,
            )
            unsafe_unapproved.append(f"{test_tu} ({tu_unsafe_hits} unsafe hit(s))")

    if failed or no_output:
        reasons: list[str] = []
        if failed:
            reasons.append("transpile failed: " + ", ".join(failed))
        if no_output:
            reasons.append("no rust output: " + ", ".join(no_output))
        if unsafe_approved:
            reasons.append("unsafe with approved exception: " + ", ".join(unsafe_approved))
        return (
            "fail",
            "; ".join(reasons),
            unsafe_hits,
            unsafe_unapproved_hits,
            unsafe_exception_hits,
        )

    if unsafe_approved:
        return (
            "pass",
            "transpile ok with approved unsafe exception(s): " + ", ".join(unsafe_approved),
            unsafe_hits,
            unsafe_unapproved_hits,
            unsafe_exception_hits,
        )
    if unsafe_unapproved:
        return (
            "pass",
            "transpile ok with unsafe output (gate disabled): " + ", ".join(unsafe_unapproved),
            unsafe_hits,
            unsafe_unapproved_hits,
            unsafe_exception_hits,
        )
    return ("pass", "transpile ok: " + ", ".join(tests), unsafe_hits, 0, 0)


def setup_logging() -> logging.Logger:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger(__name__)


def sh(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    log = logging.getLogger(__name__)
    log.info("+ %s", " ".join(cmd))
    p = subprocess.run(cmd, cwd=REPO, text=True, capture_output=True)
    if p.stdout:
        log.info(p.stdout.rstrip())
    if p.stderr:
        log.info(p.stderr.rstrip())
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed rc={p.returncode}: {' '.join(cmd)}")
    return p


def query_one(sql: str) -> str | None:
    conn = sqlite3.connect(DB)
    try:
        row = conn.execute(sql).fetchone()
        if not row:
            return None
        return str(row[0]) if row[0] is not None else None
    finally:
        conn.close()


def rs_file_for_c_file(c_file: str) -> str | None:
    safe = c_file.replace("'", "''")
    return query_one(
        f"SELECT rs_file FROM translated_tus WHERE c_file='{safe}' LIMIT 1"
    )


def c_file_for_stem(stem: str) -> str | None:
    safe = stem.replace("'", "''")
    return query_one(
        "SELECT c_file FROM translated_tus "
        f"WHERE substr(c_file, 1, length(c_file) - 2) LIKE '%/{safe}' "
        "ORDER BY c_file LIMIT 1"
    )


def find_fresh_rs(c_file: str) -> Path | None:
    safe = c_file.replace("/", "_")
    out_dir = TMP / "c2rust-reference-check" / safe / "output" / "src"
    if not out_dir.exists():
        return None
    files = sorted(out_dir.glob("*.rs"))
    if not files:
        return None
    return files[0]


def clear_fresh_reference_output(c_file: str) -> None:
    """Invalidate any previous reference before launching the generator."""
    safe = c_file.replace("/", "_")
    out_dir = C2RUST_REF_OUT_ROOT / safe / "output"
    if out_dir.exists():
        shutil.rmtree(out_dir)


def extract_extern_c_fns(path: Path) -> set[str]:
    text = path.read_text(errors="replace")
    return set(EXTERN_FN_RE.findall(text))


def normalize_ws(s: str) -> str:
    return " ".join(s.split())


def split_top_level_commas(s: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    depth_paren = 0
    depth_brack = 0
    depth_brace = 0
    depth_angle = 0

    for ch in s:
        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren = max(0, depth_paren - 1)
        elif ch == "[":
            depth_brack += 1
        elif ch == "]":
            depth_brack = max(0, depth_brack - 1)
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace = max(0, depth_brace - 1)
        elif ch == "<":
            depth_angle += 1
        elif ch == ">":
            depth_angle = max(0, depth_angle - 1)

        if (
            ch == ","
            and depth_paren == 0
            and depth_brack == 0
            and depth_brace == 0
            and depth_angle == 0
        ):
            part = "".join(cur).strip()
            if part:
                out.append(part)
            cur = []
            continue

        cur.append(ch)

    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def param_type_shape(param: str) -> str:
    p = normalize_ws(param)
    if p in ("", "..."):
        return p
    if ":" in p:
        # Keep only the type side: "name: T" -> "T"
        return normalize_ws(p.split(":", 1)[1])
    return p


def extract_extern_c_signature_shapes(path: Path) -> dict[str, tuple[int, list[str], str]]:
    """Map fn name -> (arity, param_type_shapes, return_type_shape)."""
    text = path.read_text(errors="replace")
    out: dict[str, tuple[int, list[str], str]] = {}
    for m in EXTERN_SIG_RE.finditer(text):
        name = m.group(1)
        params_blob = normalize_ws(m.group(2) or "")
        ret_blob = normalize_ws(m.group(3) or "")

        if params_blob == "":
            params: list[str] = []
        else:
            params = split_top_level_commas(params_blob)
        shapes = [param_type_shape(p) for p in params if p != ""]
        out[name] = (len(shapes), shapes, ret_blob)
    return out


def extract_top_level_fns(path: Path) -> set[str]:
    """Return top-level non-extern Rust function definitions."""
    text = path.read_text(errors="replace")
    return set(TOP_FN_RE.findall(text))


def count_pub_consts(path: Path) -> int:
    return len(PUB_CONST_RE.findall(path.read_text(errors="replace")))


def count_pub_types(path: Path) -> int:
    return len(PUB_TYPE_RE.findall(path.read_text(errors="replace")))


def run_cmd_no_raise(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO, text=True, capture_output=True)


def run_shell_no_raise(cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO, text=True, capture_output=True, shell=True)


def run_cmd_no_raise_in_repo(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO, text=True, capture_output=True)


def obj_for_c_file(c_file: str) -> str:
    p = Path(c_file)
    return str(p.with_suffix(".o"))


def diff_oracle_target_for_c_file(c_file: str) -> str | None:
    target = ORACLE_TARGET_BY_C_FILE.get(c_file, Path(c_file).stem)
    c_src = REPO / "bench" / f"diff_{target}.c"
    rs_src = REPO / "bench" / f"diff_{target}.rs"
    if c_src.exists() and rs_src.exists():
        return target
    return None


def partition_benign(names: list[str], benign_set: set[str]) -> tuple[list[str], list[str]]:
    benign: list[str] = []
    actionable: list[str] = []
    for name in names:
        if name in benign_set:
            benign.append(name)
        else:
            actionable.append(name)
    return benign, actionable


def format_head_with_overflow(items: list[str], max_items: int = 8) -> str:
    head = ", ".join(items[:max_items])
    if len(items) > max_items:
        return f"{head} (+{len(items) - max_items} more)"
    return head


def static_checks_for_pair(fresh: Path, landed: Path) -> list[str]:
    """Return a list of static-check findings. Empty means all checks passed."""
    findings: list[str] = []

    # Linter-ish check: formatting/syntax check without rewriting files.
    for label, path in (("fresh", fresh), ("landed", landed)):
        p = run_cmd_no_raise(["rustfmt", "--edition", "2021", "--check", str(path)])
        if p.returncode != 0:
            findings.append(f"{label}: rustfmt --check informational-drift")

    # AST-like structural surface check: extern "C" function names.
    fresh_fns = extract_extern_c_fns(fresh)
    landed_fns = extract_extern_c_fns(landed)
    missing = sorted(fresh_fns - landed_fns)
    extra = sorted(landed_fns - fresh_fns)
    if missing:
        findings.append(f"extern-fn missing-in-landed: {', '.join(missing[:8])}")
    if extra:
        findings.append(f"extern-fn extra-in-landed: {', '.join(extra[:8])}")

    # AST-like structural check: extern "C" signature shape (arity/params/return).
    fresh_sig = extract_extern_c_signature_shapes(fresh)
    landed_sig = extract_extern_c_signature_shapes(landed)
    shared = sorted(set(fresh_sig) & set(landed_sig))
    benign_sig_mismatches: list[str] = []
    actionable_sig_mismatches: list[str] = []
    for name in shared:
        f_arity, f_params, f_ret = fresh_sig[name]
        l_arity, l_params, l_ret = landed_sig[name]
        if f_arity != l_arity:
            actionable_sig_mismatches.append(f"{name}: arity {f_arity}!={l_arity}")
            continue
        if f_params != l_params:
            msg = f"{name}: param-shape mismatch"
            if name in KNOWN_BENIGN_EXTERN_SIG_PARAM_MISMATCH:
                benign_sig_mismatches.append(msg)
            else:
                actionable_sig_mismatches.append(msg)
            continue
        if normalize_ws(f_ret) != normalize_ws(l_ret):
            actionable_sig_mismatches.append(f"{name}: return-shape mismatch")
            continue
    if benign_sig_mismatches:
        findings.append(
            "extern-fn ignored-benign-signature: "
            + format_head_with_overflow(benign_sig_mismatches)
        )
    if actionable_sig_mismatches:
        findings.append(
            "extern-fn signature drift: "
            + format_head_with_overflow(actionable_sig_mismatches)
        )

    # AST-like structural check: top-level function surface.
    fresh_top = extract_top_level_fns(fresh)
    landed_top = extract_top_level_fns(landed)
    top_missing = sorted(fresh_top - landed_top)
    top_extra = sorted(landed_top - fresh_top)
    benign_missing, top_missing = partition_benign(
        top_missing, KNOWN_BENIGN_TOP_FN_MISSING_IN_LANDED
    )
    benign_extra, top_extra = partition_benign(
        top_extra, KNOWN_BENIGN_TOP_FN_EXTRA_IN_LANDED
    )

    if benign_missing:
        findings.append(
            "top-fn ignored-benign-missing: "
            + format_head_with_overflow(benign_missing)
        )
    if benign_extra:
        findings.append(
            "top-fn ignored-benign-extra: "
            + format_head_with_overflow(benign_extra)
        )

    if top_missing:
        findings.append(f"top-fn missing-in-landed: {format_head_with_overflow(top_missing)}")
    if top_extra:
        findings.append(f"top-fn extra-in-landed: {format_head_with_overflow(top_extra)}")

    # Declaration-surface checks: pub const/type counts should stay near shape.
    fresh_consts = count_pub_consts(fresh)
    landed_consts = count_pub_consts(landed)
    if fresh_consts != landed_consts:
        findings.append(
            f"pub-const informational-metric-drift: fresh={fresh_consts}, landed={landed_consts}"
        )

    fresh_types = count_pub_types(fresh)
    landed_types = count_pub_types(landed)
    if fresh_types != landed_types:
        findings.append(
            f"pub-type informational-metric-drift: fresh={fresh_types}, landed={landed_types}"
        )

    return findings


def is_blocking_static_finding(finding: str) -> bool:
    non_blocking_prefixes = (
        "fresh: rustfmt --check informational-drift",
        "landed: rustfmt --check informational-drift",
        "extern-fn ignored-benign-signature:",
        "top-fn ignored-benign-",
        "pub-const informational-metric-drift:",
        "pub-type informational-metric-drift:",
    )
    return not finding.startswith(non_blocking_prefixes)


def discover_from_existing_provenance() -> list[str]:
    c_files: list[str] = []
    for diff in sorted(TMP.glob("*_provenance.diff")):
        stem = diff.name.removesuffix("_provenance.diff")
        c_file = c_file_for_stem(stem)
        if c_file:
            c_files.append(c_file)
    # Preserve order while deduplicating.
    seen: set[str] = set()
    out: list[str] = []
    for c in c_files:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def run_one(c_file: str, *, do_static_checks: bool, strict_static: bool) -> dict[str, str]:
    log = logging.getLogger(__name__)
    result: dict[str, str] = {
        "c_file": c_file,
        "status": "ok",
        "fresh": "",
        "landed": "",
        "diff": "",
        "static": "",
        "note": "",
        "fresh_output_status": "not-run",
        "mapping_status": "not-run",
        "diff_status": "not-run",
        "static_status": "not-requested",
    }

    # Step 3: DB mapping
    log.info("[%s] step3: lookup translated_tus mapping", c_file)
    rs_file = rs_file_for_c_file(c_file)
    if not rs_file:
        result["mapping_status"] = "fail"
        result["status"] = "blocked"
        result["note"] = "no translated_tus mapping"
        log.info("[%s] step3: blocked (%s)", c_file, result["note"])
        return result
    result["mapping_status"] = "pass"

    landed = REPO / "linux-riscv" / rs_file
    if not landed.exists():
        result["status"] = "blocked"
        result["note"] = f"landed Rust file missing: {landed}"
        log.info("[%s] step3: blocked (%s)", c_file, result["note"])
        return result

    fresh = find_fresh_rs(c_file)
    if fresh is None:
        result["fresh_output_status"] = "fail"
        result["status"] = "blocked"
        result["note"] = "fresh Rust output missing"
        log.info("[%s] step3: blocked (%s)", c_file, result["note"])
        return result
    result["fresh_output_status"] = "pass"

    # Step 4: provenance diff artifact
    log.info("[%s] step4: generate provenance diff", c_file)
    stem = Path(c_file).stem
    diff_path = TMP / f"{stem}_provenance.diff"
    p_diff = subprocess.run(
        ["diff", "-u", str(fresh), str(landed)],
        cwd=REPO,
        text=True,
        capture_output=True,
    )
    result["fresh"] = str(fresh.relative_to(REPO))
    result["landed"] = str(landed.relative_to(REPO))
    if p_diff.returncode not in (0, 1):
        # diff(1) means the files differ; any larger status is an execution or
        # I/O error and must not create a supposedly valid provenance artifact.
        diff_path.unlink(missing_ok=True)
        stderr_tail = (p_diff.stderr or "").strip().splitlines()
        detail = stderr_tail[-1] if stderr_tail else "no diagnostic"
        result["diff_status"] = "fail"
        result["status"] = "blocked"
        result["note"] = f"provenance diff failed rc={p_diff.returncode}: {detail}"
        log.info("[%s] step4: blocked (%s)", c_file, result["note"])
        return result

    diff_path.write_text(p_diff.stdout)
    result["diff"] = str(diff_path.relative_to(REPO))
    result["diff_status"] = "pass"

    if do_static_checks:
        findings = static_checks_for_pair(fresh, landed)
        if findings:
            result["static"] = "; ".join(findings)
            result["static_status"] = "findings"
            if strict_static and any(is_blocking_static_finding(f) for f in findings):
                result["static_status"] = "fail"
                result["status"] = "blocked"
                result["note"] = "static checks failed"
        else:
            result["static"] = "pass"
            result["static_status"] = "pass"

    return result


def run_one_with_optional_qemu(
    c_file: str,
    *,
    do_static_checks: bool,
    strict_static: bool,
    oracle_check: bool,
    integrate: bool,
    qemu_test: bool,
    strict_qemu: bool,
    qemu_cmd_template: str,
) -> dict[str, str]:
    log = logging.getLogger(__name__)
    file_start = time.monotonic()
    run_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    log.info(
        "[%s] file-review start static=%s strict_static=%s oracle=%s integrate=%s qemu=%s",
        c_file,
        do_static_checks,
        strict_static,
        oracle_check,
        integrate,
        qemu_test,
    )

    precheck_start = time.monotonic()
    result = run_one(
        c_file,
        do_static_checks=do_static_checks,
        strict_static=strict_static,
    )
    precheck_duration = time.monotonic() - precheck_start
    log.info(
        "[%s] prechecks done status=%s note=%s (%.2fs)",
        c_file,
        result.get("status", ""),
        result.get("note", ""),
        precheck_duration,
    )

    result["integrate"] = "not-requested"
    result["qemu"] = "not-requested"
    result["oracle"] = "not-requested"
    result["tests"] = "not-requested"

    # Step 1: current gates first (prechecks + integrate + qemu), without
    # requiring any new tier-2.5 oracle additions.
    integrate_duration: float | None = None
    qemu_duration: float | None = None
    if integrate and result["status"] == "ok":
        integrate_start = time.monotonic()
        obj = obj_for_c_file(c_file)
        # Enable this file's own KUnit config(s) if it has a mapped test —
        # without this, the boot below can succeed and the whole kernel
        # stay green while this file's specific suite silently never ran
        # (true of any CONFIG_TEST_* not already on in the baseline
        # .config; see TEST_CONFIGS_BY_TU's module-doc comment above).
        kunit_configs: list[str] = []
        for test_tu in TEST_TUS_BY_C_FILE.get(c_file, []):
            kunit_configs.extend(TEST_CONFIGS_BY_TU.get(test_tu, []))
        log.info("[%s] step1: integrate_tu start obj=%s kunit=%s", c_file, obj, kunit_configs)
        p_int = run_cmd_no_raise_in_repo([
            "python3",
            "scripts/integrate_tu.py",
            "--obj",
            obj,
            *(["--kunit", *kunit_configs] if kunit_configs else []),
        ])
        if p_int.returncode == 0:
            result["integrate"] = f"pass ({obj})"
            # integrate_tu already includes build + boot oracle.
            result["qemu"] = "pass (via integrate_tu)"
            log.info(
                "[%s] step1: integrate_tu pass rc=0 (%.2fs)",
                c_file,
                time.monotonic() - integrate_start,
            )
            integrate_duration = time.monotonic() - integrate_start
        else:
            stderr_tail = (p_int.stderr or "").strip().splitlines()
            tail = stderr_tail[-1] if stderr_tail else ""
            result["integrate"] = f"fail rc={p_int.returncode}{(': ' + tail) if tail else ''}"
            result["qemu"] = "skipped: integration failed"
            result["status"] = "blocked"
            if result.get("note"):
                result["note"] = result["note"] + "; integration failed"
            else:
                result["note"] = "integration failed"
            log.info(
                "[%s] step1: integrate_tu fail rc=%d (%.2fs)",
                c_file,
                p_int.returncode,
                time.monotonic() - integrate_start,
            )
            integrate_duration = time.monotonic() - integrate_start
    elif integrate and result["status"] != "ok":
        result["integrate"] = "skipped: prechecks failed"
        log.info("[%s] step1: integrate_tu skipped (prechecks failed)", c_file)

    if qemu_test and result["status"] == "ok" and result["qemu"] == "not-requested":
        qemu_start = time.monotonic()
        cmd = qemu_cmd_template.format(
            c_file=c_file,
            fresh=result.get("fresh", ""),
            landed=result.get("landed", ""),
        )
        log.info("[%s] step1: direct qemu test start", c_file)
        p = run_shell_no_raise(cmd)
        if p.returncode == 0:
            result["qemu"] = "pass"
            qemu_duration = time.monotonic() - qemu_start
            log.info("[%s] step1: direct qemu test pass (%.2fs)", c_file, qemu_duration)
        else:
            stderr_tail = (p.stderr or "").strip().splitlines()
            tail = stderr_tail[-1] if stderr_tail else ""
            result["qemu"] = f"fail rc={p.returncode}{(': ' + tail) if tail else ''}"
            # Keep legacy behavior: only strict mode blocks on direct qemu failure.
            if strict_qemu:
                result["status"] = "blocked"
                if result.get("note"):
                    result["note"] = result["note"] + "; qemu test failed"
                else:
                    result["note"] = "qemu test failed"
            log.info(
                "[%s] step1: direct qemu test fail rc=%d (%.2fs)",
                c_file,
                p.returncode,
                time.monotonic() - qemu_start,
            )
            qemu_duration = time.monotonic() - qemu_start
    elif not qemu_test:
        result["qemu"] = "not-requested"
        log.info("[%s] step1: qemu phase disabled", c_file)

    step1_passed = result["status"] == "ok"
    step1_status = "pass" if step1_passed else "fail"
    step1_detail = "all current tests passed" if step1_passed else result.get("note", "step1 failed")

    # Step 2: tier-2.5 oracle workflow. Missing/failed oracle is logged and
    # does not block file progression.
    oracle_target = ""
    issue_note = ""
    oracle_duration: float | None = None
    if step1_passed and oracle_check:
        oracle_start = time.monotonic()
        target = diff_oracle_target_for_c_file(c_file)
        if target is None:
            result["oracle"] = "missing: bench diff pair"
            issue_note = "missing bench diff pair"
            log.info("[%s] step2: oracle missing target mapping", c_file)
            oracle_duration = time.monotonic() - oracle_start
        else:
            oracle_target = target
            log.info("[%s] step2: oracle start target=%s", c_file, target)
            p_oracle = run_cmd_no_raise_in_repo([
                "python3",
                "scripts/diff_oracle.py",
                target,
            ])
            if p_oracle.returncode == 0:
                result["oracle"] = f"pass ({target})"
                log.info(
                    "[%s] step2: oracle pass target=%s (%.2fs)",
                    c_file,
                    target,
                    time.monotonic() - oracle_start,
                )
                oracle_duration = time.monotonic() - oracle_start
            else:
                stderr_tail = (p_oracle.stderr or "").strip().splitlines()
                tail = stderr_tail[-1] if stderr_tail else ""
                result["oracle"] = (
                    f"fail rc={p_oracle.returncode}{(': ' + tail) if tail else ''}"
                )
                issue_note = f"oracle failed ({target})"
                log.info(
                    "[%s] step2: oracle fail target=%s rc=%d (%.2fs)",
                    c_file,
                    target,
                    p_oracle.returncode,
                    time.monotonic() - oracle_start,
                )
                oracle_duration = time.monotonic() - oracle_start
    elif step1_passed and not oracle_check:
        result["oracle"] = "not-requested"
        log.info("[%s] step2: oracle disabled", c_file)
    else:
        result["oracle"] = "skipped: step1 failed"
        issue_note = f"step1 failed: {step1_detail}"
        log.info("[%s] step2: oracle skipped (step1 failed)", c_file)

    db_stage_start = time.monotonic()
    record_review_tracking(
        c_file,
        step1_status=step1_status,
        step1_detail=step1_detail,
        oracle_status=result["oracle"],
        oracle_target=oracle_target,
        issue_note=issue_note,
    )
    log.info("[%s] db: review tracking recorded (%.2fs)", c_file, time.monotonic() - db_stage_start)

    # Existing-test transpile probe is advisory: log and keep moving.
    probe_start = time.monotonic()
    (
        test_status,
        test_detail,
        unsafe_hits,
        unsafe_unapproved_hits,
        unsafe_exception_hits,
    ) = run_existing_test_transpile_probe(c_file)
    if test_status == "pass":
        result["tests"] = "pass"
    elif test_status == "fail":
        result["tests"] = "fail"
    else:
        result["tests"] = "not-mapped"
    record_test_probe(
        c_file,
        probe_status=test_status,
        probe_detail=test_detail,
        unsafe_hits=unsafe_hits,
        unsafe_unapproved_hits=unsafe_unapproved_hits,
        unsafe_exception_hits=unsafe_exception_hits,
    )
    log.info(
        "[%s] step3(test-probe): status=%s unsafe_hits=%d unapproved=%d exceptions=%d (%.2fs)",
        c_file,
        test_status,
        unsafe_hits,
        unsafe_unapproved_hits,
        unsafe_exception_hits,
        time.monotonic() - probe_start,
    )
    probe_duration = time.monotonic() - probe_start
    total_duration = time.monotonic() - file_start

    record_review_step(
        c_file,
        run_at=run_at,
        step_name="reference_output",
        step_status=result.get("fresh_output_status", "not-run"),
        detail=result.get("note", "") if result.get("fresh_output_status") == "fail" else result.get("fresh", ""),
        artifact_path=result.get("fresh", ""),
        duration_s=precheck_duration,
    )
    record_review_step(
        c_file,
        run_at=run_at,
        step_name="mapping",
        step_status=result.get("mapping_status", "not-run"),
        detail=result.get("landed", "") or result.get("note", ""),
        artifact_path=result.get("landed", ""),
    )
    record_review_step(
        c_file,
        run_at=run_at,
        step_name="diff_artifact",
        step_status=result.get("diff_status", "not-run"),
        detail=result.get("diff", "") or result.get("note", ""),
        artifact_path=result.get("diff", ""),
    )
    record_review_step(
        c_file,
        run_at=run_at,
        step_name="static_checks",
        step_status=result.get("static_status", "not-requested"),
        detail=result.get("static", ""),
    )
    record_review_step(
        c_file,
        run_at=run_at,
        step_name="integrate",
        step_status=normalize_step_status(result.get("integrate", "")),
        detail=result.get("integrate", ""),
        duration_s=integrate_duration,
    )
    record_review_step(
        c_file,
        run_at=run_at,
        step_name="qemu",
        step_status=normalize_step_status(result.get("qemu", "")),
        detail=result.get("qemu", ""),
        duration_s=qemu_duration,
    )
    record_review_step(
        c_file,
        run_at=run_at,
        step_name="oracle",
        step_status=normalize_step_status(result.get("oracle", "")),
        detail=result.get("oracle", ""),
        duration_s=oracle_duration,
    )
    record_review_step(
        c_file,
        run_at=run_at,
        step_name="test_probe",
        step_status=test_status,
        detail=test_detail,
        duration_s=probe_duration,
    )
    record_review_step(
        c_file,
        run_at=run_at,
        step_name="semantic_review",
        step_status="pending-manual",
        detail="manual semantic review not yet recorded",
    )

    disposition = "blocked" if result["status"] != "ok" else "pending-semantic-review"
    record_review_state(
        c_file,
        run_at=run_at,
        fresh_output_status=result.get("fresh_output_status", "not-run"),
        mapping_status=result.get("mapping_status", "not-run"),
        diff_status=result.get("diff_status", "not-run"),
        static_status=result.get("static_status", "not-requested"),
        step1_status=step1_status,
        oracle_status=normalize_step_status(result.get("oracle", "")),
        test_probe_status=test_status,
        semantic_review_status="pending-manual",
        todo_status="pending-batch",
        issue_update_status="not-run",
        disposition=disposition,
        note=result.get("note", ""),
    )

    log.info("[%s] file-review end status=%s total=%.2fs", c_file, result["status"], total_duration)

    return result


def write_summary(results: list[dict[str, str]], selected: list[str]) -> None:
    lines = [
        "# c2rust file review runner summary",
        "",
        f"Selected files ({len(selected)}):",
    ]
    for c in selected:
        lines.append(f"- {c}")
    lines.append("")
    lines.append("Results:")
    lines.append("")
    lines.append("| c_file | status | diff artifact | static | oracle | tests | integrate | qemu | note |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r['c_file']} | {r['status']} | {r.get('diff','')} | {r.get('static','')} | {r.get('oracle','')} | {r.get('tests','')} | {r.get('integrate','')} | {r.get('qemu','')} | {r.get('note','')} |"
        )
    SUMMARY.write_text("\n".join(lines) + "\n")


def main() -> int:
    log = setup_logging()

    ap = argparse.ArgumentParser()
    ap.add_argument("c_files", nargs="*", help="C files to process (e.g. lib/base64.c)")
    ap.add_argument(
        "--from-existing-provenance",
        action="store_true",
        help="discover c_files from tmp/*_provenance.diff stems",
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--static-checks",
        action="store_true",
        help="run Rust-side static checks (rustfmt lint + AST-like structural surface diffs)",
    )
    ap.add_argument(
        "--strict-static",
        action="store_true",
        help="treat static-check findings as blocking failures",
    )
    ap.add_argument(
        "--no-oracle",
        action="store_true",
        help="disable tier-2.5 differential oracle check",
    )
    ap.add_argument(
        "--no-integrate",
        action="store_true",
        help="disable integrate_tu build+boot phase",
    )
    ap.add_argument(
        "--qemu-test",
        action="store_true",
        help="deprecated alias; QEMU test phase is now enabled by default",
    )
    ap.add_argument(
        "--no-qemu-test",
        action="store_true",
        help="disable post-static QEMU test phase",
    )
    ap.add_argument(
        "--strict-qemu",
        action="store_true",
        help="treat QEMU test failures as blocking",
    )
    ap.add_argument(
        "--qemu-cmd",
        default="python3 scripts/dev.py boot",
        help="shell command used for QEMU test phase; supports {c_file}, {fresh}, {landed}",
    )
    args = ap.parse_args()

    integrate_enabled = not args.no_integrate
    oracle_check_enabled = not args.no_oracle
    qemu_test_enabled = not args.no_qemu_test

    selected: list[str] = []
    if args.from_existing_provenance:
        selected.extend(discover_from_existing_provenance())
    selected.extend(args.c_files)

    # Deduplicate while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for c in selected:
        if c in seen:
            continue
        seen.add(c)
        deduped.append(c)
    selected = deduped

    if args.limit is not None:
        selected = selected[: args.limit]

    if not selected:
        log.error("No c_files selected. Pass c_files or --from-existing-provenance.")
        return 2

    log.info("selected %d file(s)", len(selected))
    for c in selected:
        log.info("- %s", c)

    # Step 2 once per batch: fresh c2rust output for all selected files.
    # Do this in the parent runner as well as the generator so even an import,
    # setup, or worktree-preparation failure cannot leave stale output usable.
    for c_file in selected:
        clear_fresh_reference_output(c_file)
    p_ref = sh(["python3", "scripts/c2rust_reference_check.py", *selected], check=False)
    if p_ref.returncode != 0:
        log.error("reference generation failed for one or more selected files")

    results: list[dict[str, str]] = []
    for c_file in selected:
        try:
            r = run_one_with_optional_qemu(
                c_file,
                do_static_checks=args.static_checks,
                strict_static=args.strict_static,
                oracle_check=oracle_check_enabled,
                integrate=integrate_enabled,
                qemu_test=qemu_test_enabled,
                strict_qemu=args.strict_qemu,
                qemu_cmd_template=args.qemu_cmd,
            )
            if p_ref.returncode != 0 and r["status"] == "ok":
                # Defensive: mark uncertain results as blocked when batch ref failed.
                if not r["fresh"]:
                    r["status"] = "blocked"
                    r["note"] = "fresh reference generation failed"
            results.append(r)
        except Exception as exc:  # defensive: keep loop running
            results.append(
                {
                    "c_file": c_file,
                    "status": "blocked",
                    "fresh": "",
                    "landed": "",
                    "diff": "",
                    "note": str(exc),
                }
            )

    # Step 7 enforcement once per batch.
    todo_status = "pass"
    todo_detail = "TODO sync/check passed"
    try:
        sh(["python3", "scripts/sync_todo_linux_rs.py"])
        sh(["python3", "scripts/check_todo_linux_rs.py"])
    except Exception as exc:
        todo_status = "fail"
        todo_detail = str(exc)
        raise
    finally:
        for r in results:
            c_file = r["c_file"]
            run_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            record_review_step(
                c_file,
                run_at=run_at,
                step_name="todo_sync_check",
                step_status=todo_status,
                detail=todo_detail,
            )
            record_review_state(
                c_file,
                run_at=run_at,
                fresh_output_status=r.get("fresh_output_status", "not-run"),
                mapping_status=r.get("mapping_status", "not-run"),
                diff_status=r.get("diff_status", "not-run"),
                static_status=r.get("static_status", "not-requested"),
                step1_status="pass" if r.get("status") == "ok" else "fail",
                oracle_status=normalize_step_status(r.get("oracle", "")),
                test_probe_status=normalize_step_status(r.get("tests", "")),
                semantic_review_status="pending-manual",
                todo_status=todo_status,
                issue_update_status="not-run",
                disposition="blocked" if r.get("status") != "ok" else "pending-semantic-review",
                note=r.get("note", ""),
            )

    write_summary(results, selected)
    blocked = sum(1 for r in results if r["status"] != "ok")
    print(
        f"RUNNER: {len(results) - blocked}/{len(results)} ok, {blocked} blocked "
        f"(summary: {SUMMARY.relative_to(REPO)})"
    )
    return 0 if blocked == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
