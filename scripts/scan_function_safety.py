#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Per-function unsafe/safe conversion-state scanner.

Mechanically determines, for every function in a translated corpus, the
`function_safety_status` pipeline state (rulesdb/schema.sql): whether it
is currently `unsafe-baseline` (contains `unsafe` and/or a raw-pointer
type anywhere in its body) or mechanically qualifies as
`mechanically-checked-already-safe` (zero `unsafe` tokens, zero
`*const T`/`*mut T` occurrences in signature+body). This is a real static
text/brace-matched scan of the emitted `.rs`, not a guess — same
"good enough to be useful, documented limitations" text-scanning
philosophy as check_c2rust_rule_conformance.py, scoped per function
instead of per file/rule.

States 3-5 (attempted-safe-conversion / safe-verified /
safe-with-exceptions) are NOT set by this scanner — they require a real
safe-lift rewrite (0023/0024/0025-style) and a real oracle-tier pass,
neither of which exist yet for any function in this corpus (per the
design doc's own "no scanner implemented, no baseline run" status this
script closes out for state 1<->2 only). Every row this script writes is
therefore state 1 or state 2.

Two corpora, matching file_oracle_status's population vocabulary:
  landed_tu     — scripts.dev's translated_tus table (c_file, rs_file
                  pairs already in-tree under linux-riscv/); Rust fn
                  names follow this project's own `_rs` suffix
                  convention (see docs/*8250* landings) — c_func_name is
                  derived by stripping a trailing `_rs`.
  c2rust_corpus — tmp/c2rust-baseline/*/output/src/*.rs; c2rust emits
                  the C function name verbatim, no suffix.

Usage: scan_function_safety.py [--population landed_tu|c2rust_corpus|all] [--limit N]
Inputs:
  rulesdb/patterns.db: translated_tus (landed_tu population)
  tmp/c2rust-baseline/*/output/src/*.rs (c2rust_corpus population)
Outputs:
  rulesdb/patterns.db: function_safety_status table (upserted, not
  wholesale replaced — a function's state 3-5 history set by some future
  conversion tool must survive a re-run of this state-1/2 scanner)
  tmp/function-safety-report.md — human-readable summary
Log: tmp/scan_function_safety.log
"""
import argparse
import datetime
import logging
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
BASELINE = REPO / "tmp" / "c2rust-baseline"
DB = REPO / "rulesdb" / "patterns.db"
REPORT = REPO / "tmp" / "function-safety-report.md"
LOG = REPO / "tmp" / "scan_function_safety.log"

STATE_UNSAFE_BASELINE = "unsafe-baseline"
STATE_ALREADY_SAFE = "mechanically-checked-already-safe"

# ---------------------------------------------------------------------------
# Function boundary extraction
# ---------------------------------------------------------------------------

# Matches a top-level (column-0) Rust fn definition's signature start, from
# an optional visibility/unsafe/extern-ABI prefix through the fn name, up
# to (but not including) the parameter list and body. Deliberately anchored
# at column 0 (^ with re.MULTILINE): every fn this project's landed_tu and
# c2rust_corpus output actually emits is a free function at the module top
# level (confirmed: zero `impl` blocks in either corpus as of this script's
# self-test) — nested/inner fns and closures are not separately tracked,
# matching the design doc's function-grain being about C-declaration
# identity, not every Rust-level fn item ever emitted.
FN_START_RE = re.compile(
    r'^(?P<vis>pub(?:\([^)]*\))?\s+)?'
    r'(?P<unsafe>unsafe\s+)?'
    r'(?P<extern>extern\s+"C"\s+)?'
    r'fn\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)'
    r'(?P<generics><[^{]*?>)?'
    r'\s*\(',
    re.MULTILINE,
)


def find_signature_end(text: str, paren_open_idx: int) -> int | None:
    """From the index of a fn's opening '(', paren-match to its close, then
    skip an optional '-> RetType' up to the body's opening '{'. Returns the
    index of that '{', or None if unbalanced (malformed/truncated input)."""
    depth = 0
    i = paren_open_idx
    n = len(text)
    while i < n:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        return None
    # i is now at the matching ')'. Scan forward for the body's '{',
    # tolerating a '-> RetType' in between (RetType text itself never
    # contains a top-level '{' in this corpus — no generic-const/closure
    # return types observed — so the first '{' after ')' is the body).
    j = i + 1
    while j < n and text[j] != "{":
        j += 1
    return j if j < n else None


def extract_fn_body(text: str, brace_open_idx: int) -> str | None:
    """Brace-match from the opening '{' at brace_open_idx to find the full
    fn body (inclusive of both braces). None if unbalanced."""
    depth = 0
    n = len(text)
    for j in range(brace_open_idx, n):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[brace_open_idx:j + 1]
    return None


def iter_functions(text: str):
    """Yield (name, is_unsafe_sig, is_extern_c, full_span_text, start_line)
    for every top-level fn in text. full_span_text covers the signature
    (from 'fn' through the parameter list and return type) PLUS the body —
    the whole unit rule 0018/0023-0025-style safe-lift conversions and this
    scanner's unsafe/raw-pointer counts are scoped to, matching the
    schema's own 'fn signature+body' wording for raw_pointer_count."""
    for m in FN_START_RE.finditer(text):
        paren_idx = m.end() - 1  # the '(' the regex matched up to
        brace_idx = find_signature_end(text, paren_idx)
        if brace_idx is None:
            logging.warning("unbalanced signature for fn %s at offset %d — skipped",
                             m.group("name"), m.start())
            continue
        body = extract_fn_body(text, brace_idx)
        if body is None:
            logging.warning("unbalanced body for fn %s at offset %d — skipped",
                             m.group("name"), m.start())
            continue
        span = text[m.start():brace_idx] + body
        start_line = text[:m.start()].count("\n") + 1
        yield (
            m.group("name"),
            bool(m.group("unsafe")),
            bool(m.group("extern")),
            span,
            start_line,
        )


# ---------------------------------------------------------------------------
# Mechanical safety scan
# ---------------------------------------------------------------------------

# `unsafe` as a keyword: the block form (`unsafe {`) and the fn-signature
# form (`unsafe fn`/`unsafe extern "C" fn`) both count per the schema's
# "literal `unsafe` occurrences in the fn body" wording — a function whose
# *signature* says unsafe is not mechanically safe even with an empty
# unsafe-free body, because callers can still invoke it without an unsafe
# block being visible at the call site's own text (the unsafety is in the
# contract, not just the body). Word-boundaried so `unsafe_get_user_ul` (a
# real identifier in this corpus, lib/usercopy_rs.rs) does NOT false-count
# as the keyword.
UNSAFE_TOKEN_RE = re.compile(r'\bunsafe\b')

# *const T / *mut T raw-pointer types. Matches the schema's own comment
# ("*const T / *mut T occurrences") literally — deliberately NOT matching
# `&T`/`&mut T` references (safe by construction) or `NonNull<T>` (a safe
# wrapper type per the kernel crate's own convention). `core::ptr::` fn
# calls (addr_of_mut!, etc.) operating on raw pointers are also real
# unsafe-adjacent surface but are already caught by UNSAFE_TOKEN_RE since
# every real dereference/call site observed in this corpus sits inside an
# `unsafe { }` block (Rust requires this to compile) — a bare
# `*mut T`/`*const T` TYPE occurrence without any surrounding `unsafe`
# would be dead/uncallable code, not a gap this regex needs to separately
# hunt for.
RAW_POINTER_RE = re.compile(r'\*\s*(?:const|mut)\s+')


def scan_function(span: str, sig_is_unsafe: bool) -> tuple[int, int, int]:
    """Returns (unsafe_token_count, raw_pointer_count, loc)."""
    unsafe_count = len(UNSAFE_TOKEN_RE.findall(span))
    raw_ptr_count = len(RAW_POINTER_RE.findall(span))
    loc = span.count("\n") + 1
    return unsafe_count, raw_ptr_count, loc


def classify(unsafe_count: int, raw_ptr_count: int) -> str:
    if unsafe_count == 0 and raw_ptr_count == 0:
        return STATE_ALREADY_SAFE
    return STATE_UNSAFE_BASELINE


# ---------------------------------------------------------------------------
# c_func_name derivation
# ---------------------------------------------------------------------------

def landed_tu_c_func_name(rs_func_name: str) -> str:
    """This project's own landed-TU naming convention strips to a bare C
    name with a trailing '_rs' suffix on the Rust side whenever the Rust
    name would otherwise collide with the C symbol namespace at link time
    (confirmed across all 38 landed TUs' fn names: 'gcd' has no C-name
    collision risk and keeps 'gcd' verbatim; 'mem_serial_in_rs',
    'serial8250_do_startup_rs', 'bytes_to_fcr_rxtrig_rs' etc. all strip
    cleanly to their real C declaration name by removing exactly one
    trailing '_rs'). Private non-exported helpers (efficient_ffs,
    binary_gcd's ilk) never carry the suffix at all since they have no C
    symbol to collide with — stripping a suffix that isn't there is a
    no-op, so this function is safe to apply unconditionally."""
    if rs_func_name.endswith("_rs"):
        return rs_func_name[: -len("_rs")]
    return rs_func_name


# ---------------------------------------------------------------------------
# Corpus iteration
# ---------------------------------------------------------------------------

def iter_landed_tu_corpus(conn):
    rows = conn.execute("SELECT c_file, rs_file FROM translated_tus ORDER BY c_file").fetchall()
    for c_file, rs_file in rows:
        rs_path = TREE / rs_file
        if not rs_path.exists():
            logging.warning("landed_tu %s: rs_file %s not found on disk — skipped",
                             c_file, rs_file)
            continue
        yield c_file, rs_file, rs_path


def iter_c2rust_corpus():
    if not BASELINE.exists():
        return
    for d in sorted(BASELINE.iterdir()):
        if not d.is_dir():
            continue
        src_dir = d / "output" / "src"
        if not src_dir.exists():
            continue
        for rs in sorted(src_dir.glob("*.rs")):
            try:
                rs_rel = str(rs.relative_to(REPO))
            except ValueError:
                rs_rel = str(rs)
            yield d.name, rs_rel, rs


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def ensure_schema(conn):
    """The real DDL lives in rulesdb/schema.sql (applied there ahead of
    this script). CREATE TABLE IF NOT EXISTS here only so this script is
    independently runnable against a freshly-`dev.py db`-rebuilt or
    from-scratch patterns.db without a separate manual schema-apply step —
    must stay byte-for-byte in sync with schema.sql's definition (schema.sql
    is authoritative; this is a bootstrap convenience, not a second source
    of truth)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS function_safety_status (
            id INTEGER PRIMARY KEY,
            c_file TEXT NOT NULL,
            c_func_name TEXT NOT NULL,
            population TEXT NOT NULL,
            rs_func_name TEXT,
            rs_file TEXT,
            state TEXT NOT NULL CHECK (state IN (
                'unsafe-baseline',
                'mechanically-checked-already-safe',
                'attempted-safe-conversion',
                'safe-verified',
                'safe-with-exceptions'
            )),
            unsafe_token_count INTEGER,
            raw_pointer_count INTEGER,
            conversion_rule_id TEXT REFERENCES rules(id),
            oracle_tier INTEGER CHECK (oracle_tier IS NULL OR oracle_tier BETWEEN 1 AND 5),
            accepted_exception_rule_id TEXT REFERENCES rules(id),
            loc INTEGER NOT NULL,
            detail TEXT,
            evidence_ref TEXT,
            checked_at TEXT NOT NULL,
            UNIQUE (c_file, c_func_name, population)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_func_safety_cfile "
                 "ON function_safety_status(c_file)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_func_safety_state "
                 "ON function_safety_status(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_func_safety_exception "
                 "ON function_safety_status(accepted_exception_rule_id)")
    conn.commit()


def upsert(conn, row, now):
    (c_file, c_func_name, population, rs_func_name, rs_file, state,
     unsafe_count, raw_ptr_count, loc, detail) = row
    # This scanner only ever produces state 1/2 findings (see module doc).
    # A prior run of a FUTURE state-3/4/5 conversion tool may already hold
    # a more advanced state for this exact (c_file, c_func_name,
    # population) — re-running this mechanical scanner must not regress
    # that progress backward to state 1/2 just because it re-observed the
    # same unsafe/raw-pointer tokens the conversion hasn't touched yet, or
    # because the corpus file was rescanned before a state-3 attempt has
    # landed its rewritten body. Guard: only overwrite an existing row if
    # its current state is itself state 1 or state 2 (i.e. still owned by
    # this same mechanical-scan stage) or the row doesn't exist yet.
    existing = conn.execute(
        "SELECT state FROM function_safety_status WHERE c_file=? AND c_func_name=? AND population=?",
        (c_file, c_func_name, population),
    ).fetchone()
    if existing and existing[0] not in (STATE_UNSAFE_BASELINE, STATE_ALREADY_SAFE):
        logging.info("skip %s:%s (%s): existing state %r is past this scanner's "
                     "authority (state 1/2 only) — not overwritten",
                     c_file, c_func_name, population, existing[0])
        return False
    conn.execute(
        "INSERT INTO function_safety_status "
        "(c_file, c_func_name, population, rs_func_name, rs_file, state, "
        " unsafe_token_count, raw_pointer_count, loc, detail, checked_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (c_file, c_func_name, population) DO UPDATE SET "
        "rs_func_name=excluded.rs_func_name, rs_file=excluded.rs_file, "
        "state=excluded.state, unsafe_token_count=excluded.unsafe_token_count, "
        "raw_pointer_count=excluded.raw_pointer_count, loc=excluded.loc, "
        "detail=excluded.detail, checked_at=excluded.checked_at",
        (c_file, c_func_name, population, rs_func_name, rs_file, state,
         unsafe_count, raw_ptr_count, loc, detail, now),
    )
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scan_corpus(population, entries, limit, results, now):
    n_files = 0
    n_funcs = 0
    for c_file, rs_file, rs_path in entries:
        if limit and n_files >= limit:
            break
        n_files += 1
        text = rs_path.read_text(errors="replace")
        for name, sig_unsafe, is_extern_c, span, start_line in iter_functions(text):
            unsafe_count, raw_ptr_count, loc = scan_function(span, sig_unsafe)
            state = classify(unsafe_count, raw_ptr_count)
            if population == "landed_tu":
                c_func_name = landed_tu_c_func_name(name)
                rs_func_name = name if name != c_func_name else None
            else:
                c_func_name = name
                rs_func_name = None
            detail = (
                f"scanned {rs_file}:{start_line}, sig={'unsafe ' if sig_unsafe else ''}"
                f"{'extern \"C\" ' if is_extern_c else ''}fn; "
                f"{unsafe_count} unsafe token(s), {raw_ptr_count} raw-pointer type(s), "
                f"{loc} body LOC"
            )
            results.append((c_file, c_func_name, population, rs_func_name, rs_file,
                             state, unsafe_count, raw_ptr_count, loc, detail))
            n_funcs += 1
    return n_files, n_funcs


def write_report(results, populations_scanned):
    by_pop = {}
    for row in results:
        by_pop.setdefault(row[2], []).append(row)

    lines = []
    lines.append("# Per-function safety-tier coverage — mechanical scan")
    lines.append("")
    lines.append("Generated by `scripts/scan_function_safety.py`. States "
                  "`attempted-safe-conversion`/`safe-verified`/"
                  "`safe-with-exceptions` are set by future tooling, not "
                  "this scanner — every row here is `unsafe-baseline` or "
                  "`mechanically-checked-already-safe`.")
    lines.append("")

    for population in populations_scanned:
        rows = by_pop.get(population, [])
        if not rows:
            lines.append(f"## {population}")
            lines.append("")
            lines.append("_no functions scanned_")
            lines.append("")
            continue
        total_loc = sum(r[8] for r in rows)
        safe_rows = [r for r in rows if r[5] == STATE_ALREADY_SAFE]
        safe_loc = sum(r[8] for r in safe_rows)
        lines.append(f"## {population}")
        lines.append("")
        lines.append(f"{len(rows)} function(s) scanned, {total_loc} total body LOC. "
                      f"{len(safe_rows)} already-safe ({safe_loc} LOC, "
                      f"{round(100.0 * safe_loc / total_loc, 1) if total_loc else 0}% of LOC).")
        lines.append("")
        lines.append("| c_file | c_func_name | state | unsafe | raw_ptr | loc |")
        lines.append("|---|---|---|---|---|---|")
        for r in sorted(rows, key=lambda r: (r[0], r[1])):
            c_file, c_func_name, _pop, _rsf, _rsfile, state, unsafe_count, raw_ptr_count, loc, _d = r
            lines.append(f"| {c_file} | {c_func_name} | {state} | {unsafe_count} "
                         f"| {raw_ptr_count} | {loc} |")
        lines.append("")

    REPORT.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--population", choices=["landed_tu", "c2rust_corpus", "all"],
                     default="all")
    ap.add_argument("--limit", type=int, default=None,
                     help="cap number of files scanned per population (debugging)")
    args = ap.parse_args()

    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    conn = sqlite3.connect(DB)
    ensure_schema(conn)

    populations = (["landed_tu", "c2rust_corpus"] if args.population == "all"
                    else [args.population])

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    results = []

    if "landed_tu" in populations:
        entries = list(iter_landed_tu_corpus(conn))
        n_files, n_funcs = scan_corpus("landed_tu", entries, args.limit, results, now)
        logging.info("landed_tu: %d file(s), %d function(s)", n_files, n_funcs)

    if "c2rust_corpus" in populations:
        entries = [(safe_name, rs_rel, rs_path) for safe_name, rs_rel, rs_path
                   in iter_c2rust_corpus()]
        if not entries:
            print(f"WARNING: no transpiled TUs found under {BASELINE} — "
                  f"c2rust_corpus population will have zero rows. "
                  f"Run `dev.py c2rust-baseline` first if this is unexpected.")
            logging.warning("empty c2rust_corpus under %s", BASELINE)
        n_files, n_funcs = scan_corpus("c2rust_corpus", entries, args.limit, results, now)
        logging.info("c2rust_corpus: %d file(s), %d function(s)", n_files, n_funcs)

    n_written = 0
    n_skipped_advanced = 0
    for row in results:
        if upsert(conn, row, now):
            n_written += 1
        else:
            n_skipped_advanced += 1
    conn.commit()

    write_report(results, populations)

    # ---- summary from the views (real DB read-back, not just in-memory counts) ----
    for population in populations:
        summary = conn.execute(
            "SELECT fn_count, total_loc, strict_safe_loc_pct, exceptions_allowed_loc_pct "
            "FROM function_safety_overall_summary WHERE population=?",
            (population,),
        ).fetchone()
        if summary:
            fn_count, total_loc, strict_pct, exc_pct = summary
            logging.info("%s overall: %d fn, %d LOC, strict_safe_loc_pct=%s, "
                         "exceptions_allowed_loc_pct=%s",
                         population, fn_count, total_loc, strict_pct, exc_pct)
            print(f"{population}: {fn_count} functions, {total_loc} LOC, "
                  f"strict_safe_loc_pct={strict_pct}, exceptions_allowed_loc_pct={exc_pct}")

    logging.info("done: %d row(s) scanned, %d written/updated, %d skipped "
                 "(already past state 1/2), report=%s",
                 len(results), n_written, n_skipped_advanced, REPORT)
    print(f"SCAN OK: {len(results)} function(s) scanned, {n_written} written, "
          f"{n_skipped_advanced} skipped (advanced state), report={REPORT}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
