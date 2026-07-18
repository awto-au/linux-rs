"""Tests for c2rust_regression_check.py compile-pass-rate gating (a4, awto-au/linux-rs#15).

Exercises compile_rate() and the regression-detection logic via an in-memory
SQLite database, without touching the real patterns.db or running any
c2rust/rustc commands.
"""
import importlib.util
import sqlite3
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "c2rust_regression_check.py"
spec = importlib.util.spec_from_file_location("c2rust_regression_check", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _make_db(rows_by_rev: dict) -> sqlite3.Connection:
    """Create an in-memory DB with c2rust_compile_outcomes rows.

    rows_by_rev: {rev: [(rs_file, outcome), ...]}
    run_at is fixed per rev so compile_rate() returns the full per-rev set.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE c2rust_compile_outcomes ("
        "id INTEGER PRIMARY KEY, c2rust_rev TEXT NOT NULL, run_at TEXT NOT NULL, "
        "rs_file TEXT NOT NULL, outcome TEXT NOT NULL)"
    )
    for rev, rows in rows_by_rev.items():
        run_at = f"2026-01-01T00:00:00+00:00"
        conn.executemany(
            "INSERT INTO c2rust_compile_outcomes (c2rust_rev, run_at, rs_file, outcome) "
            "VALUES (?,?,?,?)",
            [(rev, run_at, f, o) for f, o in rows],
        )
    conn.commit()
    return conn


class CompileRateTest(unittest.TestCase):
    def test_returns_none_for_missing_rev(self):
        conn = _make_db({"rev_a": [("a.rs", "ok")]})
        result = mod.compile_rate(conn, "rev_x")
        self.assertIsNone(result)

    def test_returns_none_when_table_absent(self):
        conn = sqlite3.connect(":memory:")
        # table does not exist — should return None gracefully
        result = mod.compile_rate(conn, "rev_a")
        self.assertIsNone(result)

    def test_returns_correct_outcomes(self):
        conn = _make_db({"rev_a": [("a.rs", "ok"), ("b.rs", "error"), ("c.rs", "timeout")]})
        result = mod.compile_rate(conn, "rev_a")
        self.assertEqual(result, {"a.rs": "ok", "b.rs": "error", "c.rs": "timeout"})

    def test_latest_run_at_wins(self):
        """When multiple run_at values exist for a rev, the most recent wins."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE c2rust_compile_outcomes ("
            "id INTEGER PRIMARY KEY, c2rust_rev TEXT NOT NULL, run_at TEXT NOT NULL, "
            "rs_file TEXT NOT NULL, outcome TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO c2rust_compile_outcomes (c2rust_rev, run_at, rs_file, outcome) VALUES (?,?,?,?)",
            [
                ("rev_a", "2026-01-01T00:00:00+00:00", "a.rs", "error"),  # old run: error
                ("rev_a", "2026-01-02T00:00:00+00:00", "a.rs", "ok"),     # newer run: ok
            ],
        )
        conn.commit()
        result = mod.compile_rate(conn, "rev_a")
        self.assertEqual(result, {"a.rs": "ok"})


class CompileRateRegressionTest(unittest.TestCase):
    """Tests for the compile-pass-rate regression-detection logic."""

    def _check_regression(self, before_rows, after_rows):
        """
        Simulate the regression check: given per-rev outcome rows, return True
        if after_rate < before_rate (i.e. a regression is detected).
        """
        conn = _make_db({
            "rev_before": before_rows,
            "rev_after": after_rows,
        })
        before_compile = mod.compile_rate(conn, "rev_before")
        after_compile = mod.compile_rate(conn, "rev_after")
        if before_compile is None or after_compile is None:
            return False  # no data => no block
        before_ok = sum(1 for o in before_compile.values() if o == "ok")
        after_ok = sum(1 for o in after_compile.values() if o == "ok")
        before_rate = before_ok / len(before_compile) if before_compile else 0.0
        after_rate = after_ok / len(after_compile) if after_compile else 0.0
        return after_rate < before_rate

    def test_no_regression_returns_false(self):
        """Same pass rate => no regression detected."""
        rows = [(f"f{i}.rs", "ok") for i in range(5)]
        self.assertFalse(self._check_regression(rows, rows))

    def test_compile_rate_drop_detected(self):
        """Compile pass-rate drops => regression detected."""
        before = [(f"f{i}.rs", "ok") for i in range(10)]
        after = (
            [(f"f{i}.rs", "ok") for i in range(7)]
            + [(f"f{i}.rs", "error") for i in range(7, 10)]
        )
        self.assertTrue(self._check_regression(before, after))

    def test_compile_rate_improves_not_regression(self):
        """Compile pass-rate improves => no regression."""
        before = (
            [(f"f{i}.rs", "ok") for i in range(6)]
            + [(f"f{i}.rs", "error") for i in range(6, 10)]
        )
        after = [(f"f{i}.rs", "ok") for i in range(10)]
        self.assertFalse(self._check_regression(before, after))

    def test_missing_compile_data_does_not_block(self):
        """If compile-check data is absent for a rev, no regression is flagged."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE c2rust_compile_outcomes ("
            "id INTEGER PRIMARY KEY, c2rust_rev TEXT NOT NULL, run_at TEXT NOT NULL, "
            "rs_file TEXT NOT NULL, outcome TEXT NOT NULL)"
        )
        # before has data; after has none
        conn.executemany(
            "INSERT INTO c2rust_compile_outcomes (c2rust_rev, run_at, rs_file, outcome) VALUES (?,?,?,?)",
            [("rev_before", "2026-01-01T00:00:00", f"f{i}.rs", "ok") for i in range(5)],
        )
        conn.commit()
        before_compile = mod.compile_rate(conn, "rev_before")
        after_compile = mod.compile_rate(conn, "rev_after")
        # When either side is None, the regression check should be skipped (no block)
        self.assertIsNotNone(before_compile)
        self.assertIsNone(after_compile)
        # Regression detection: None => False (no block)
        compile_regressed = False
        if before_compile is not None and after_compile is not None:
            before_rate = sum(1 for o in before_compile.values() if o == "ok") / len(before_compile)
            after_rate = sum(1 for o in after_compile.values() if o == "ok") / len(after_compile)
            compile_regressed = after_rate < before_rate
        self.assertFalse(compile_regressed)


if __name__ == "__main__":
    unittest.main()
