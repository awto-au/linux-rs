"""Regression tests for the release-blocking c2rust review workflow gates."""

import importlib.util
import logging
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    script = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reference = load_script("c2rust_reference_check")
review = load_script("run_c2rust_file_review")
build_db = load_script("build_db")


class FreshReferenceOutputTest(unittest.TestCase):
    def test_clear_transpile_output_removes_stale_reference(self):
        with tempfile.TemporaryDirectory() as td, patch.object(reference, "OUT_ROOT", Path(td)):
            stale = Path(td) / "lib_base64.c" / "output" / "src" / "base64.rs"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale")

            reference.clear_transpile_output("lib/base64.c")

            self.assertFalse(stale.parent.parent.exists())

    def test_review_runner_also_invalidates_stale_reference(self):
        with tempfile.TemporaryDirectory() as td, patch.object(
            review, "C2RUST_REF_OUT_ROOT", Path(td)
        ):
            stale = Path(td) / "lib_base64.c" / "output" / "src" / "base64.rs"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale")

            review.clear_fresh_reference_output("lib/base64.c")

            self.assertFalse(stale.parent.parent.exists())

    def test_failed_transpile_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as td, patch.object(reference, "OUT_ROOT", Path(td)):
            out = Path(td) / "lib_base64.c" / "output" / "src" / "base64.rs"

            def failed_run(*args, **kwargs):
                out.parent.mkdir(parents=True)
                out.write_text("partial")
                return subprocess.CompletedProcess(args[0], 2, "", "failed")

            with patch.object(reference.subprocess, "run", side_effect=failed_run):
                result = reference.transpile_one(
                    "lib/base64.c", {"file": "lib/base64.c"}, logging.getLogger(__name__)
                )

            self.assertEqual(result["status"], "CRASH_OR_ERROR")
            self.assertEqual(result["rs_files"], [])
            self.assertFalse(out.parent.parent.exists())


class ProvenanceDiffTest(unittest.TestCase):
    def test_diff_execution_error_blocks_without_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            landed = repo / "linux-riscv" / "lib" / "base64_rs.rs"
            fresh = repo / "fresh" / "base64.rs"
            artifact = repo / "tmp" / "base64_provenance.diff"
            landed.parent.mkdir(parents=True)
            fresh.parent.mkdir(parents=True)
            artifact.parent.mkdir(parents=True)
            landed.write_text("landed")
            fresh.write_text("fresh")
            artifact.write_text("stale artifact")

            failed = subprocess.CompletedProcess(["diff"], 2, "partial", "read error")
            with (
                patch.object(review, "REPO", repo),
                patch.object(review, "TMP", repo / "tmp"),
                patch.object(review, "rs_file_for_c_file", return_value="lib/base64_rs.rs"),
                patch.object(review, "find_fresh_rs", return_value=fresh),
                patch.object(review.subprocess, "run", return_value=failed),
            ):
                result = review.run_one(
                    "lib/base64.c", do_static_checks=False, strict_static=False
                )

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["diff_status"], "fail")
            self.assertIn("rc=2", result["note"])
            self.assertFalse(artifact.exists())


class ReviewDatabaseSchemaTest(unittest.TestCase):
    TABLES = {
        "c2rust_file_review_steps",
        "c2rust_file_review_state",
        "c2rust_file_review_tracking",
        "c2rust_file_test_probe",
        "c2rust_test_probe_exceptions",
    }

    def test_all_review_tables_are_in_schema_and_persistent(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript((ROOT / "rulesdb" / "schema.sql").read_text())
        actual = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

        self.assertTrue(self.TABLES <= actual)
        self.assertTrue(self.TABLES <= set(build_db.PERSISTENT_TABLES))


if __name__ == "__main__":
    unittest.main()
