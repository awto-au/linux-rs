#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Quick checks against rulesdb/patterns.db (SQLite over the kernel census +
rule DB) — the "quick checks against the whole kernel" idea from the
original proposal. SQL beats grep here specifically for structural/
aggregate questions (how many, which rule covers, top-N by count);
plain grep remains the right tool for "find this exact string".

The DB is ephemeral/derived (see .gitignore) — run `dev.py db` (or
scripts/build_db.py directly) to build/refresh it before querying; this
script does not build it.

Usage:
  query_db.py rule <keyword>        # which rule(s) mention <keyword>
  query_db.py callers <fn_name>     # how many functions call <fn_name>, sample
  query_db.py uncovered [N]         # top-N hot statement families with no rule
  query_db.py sql "<query>"         # raw SQL, table-printed
  query_db.py stats                 # summary counts
"""
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "rulesdb" / "patterns.db"


def decode_fp(fp):
    try:
        import clang.cindex as ci
    except ImportError:
        return fp
    out = []
    for tok in fp.split("\x00")[0].split(" "):
        if tok.isdigit():
            try:
                out.append(ci.CursorKind.from_id(int(tok)).name)
                continue
            except ValueError:
                pass
        out.append(tok)
    return " ".join(out)


def main() -> int:
    if not DB.exists():
        print(f"no {DB} — run: python3 scripts/build_db.py", file=sys.stderr)
        return 1
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    conn = sqlite3.connect(DB)
    cmd, rest = sys.argv[1], sys.argv[2:]

    if cmd == "rule":
        kw = rest[0] if rest else ""
        for rid, mc, mf in conn.execute(
            "SELECT id, match_c, match_family FROM rules "
            "WHERE match_c LIKE ? OR match_family LIKE ? OR id LIKE ?",
            (f"%{kw}%", f"%{kw}%", f"%{kw}%"),
        ):
            print(f"{rid}\n  match.c: {(mc or '')[:100]}\n  family: {mf or '-'}\n")

    elif cmd == "callers":
        name = rest[0] if rest else ""
        n = conn.execute(
            "SELECT COUNT(DISTINCT function_id) FROM callees WHERE callee_name=?", (name,)
        ).fetchone()[0]
        print(f"{n} functions call {name}")
        for f, ln, fname in conn.execute(
            "SELECT f.file, f.line, f.name FROM functions f JOIN callees c "
            "ON c.function_id=f.id WHERE c.callee_name=? LIMIT 10", (name,)
        ):
            print(f"  {f}:{ln}  {fname}")

    elif cmd == "uncovered":
        n = int(rest[0]) if rest else 20
        for fp, cnt, ef, el, snip in conn.execute(
            "SELECT fp, instance_count, exemplar_file, exemplar_line, exemplar_snippet "
            "FROM uncovered_hot_families LIMIT ?", (n,)
        ):
            print(f"{cnt:7d}  {decode_fp(fp)[:60]:60s}  {ef}:{el}  {(snip or '')[:50]}")

    elif cmd == "stats":
        for label, q in [
            ("rules", "SELECT COUNT(*) FROM rules"),
            ("rules (deferred)", "SELECT COUNT(*) FROM rules WHERE deferred=1"),
            ("functions (census)", "SELECT COUNT(*) FROM functions"),
            ("statement families (census)", "SELECT COUNT(*) FROM statement_families"),
            ("translated TUs", "SELECT COUNT(*) FROM translated_tus"),
        ]:
            print(f"{label}: {conn.execute(q).fetchone()[0]}")

    elif cmd == "sql":
        q = rest[0] if rest else ""
        cur = conn.execute(q)
        cols = [d[0] for d in cur.description] if cur.description else []
        if cols:
            print("\t".join(cols))
        for row in cur:
            print("\t".join(str(x) for x in row))

    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
