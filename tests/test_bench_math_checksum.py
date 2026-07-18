"""Tests for bench_math.py checksum-agreement gating (a5, awto-au/linux-rs#15).

Verifies that main() returns 1 on a MISMATCH and 0 when all checksums agree,
without requiring clang or rustc (the CSV parsing and agreement check are
exercised in isolation using a monkeypatched `run` helper).
"""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_math.py"
spec = importlib.util.spec_from_file_location("bench_math", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


# Minimal fake stdout output from a benchmark binary:
# format: impl,func,ns_per_op,acc
# "c" and "rust-faithful" both output the same acc for gcd => AGREE
_CSV_AGREE = "\n".join([
    "c,gcd,100.0,12345678",
    "rust-faithful,gcd,95.0,12345678",
])

# "c" and "rust-faithful" produce different checksums for gcd => MISMATCH
_CSV_MISMATCH = "\n".join([
    "c,gcd,100.0,12345678",
    "rust-faithful,gcd,95.0,99999999",
])


class BenchMathChecksumTest(unittest.TestCase):
    def _run_with_csv(self, c_output, rs_output):
        """Drive bench_math.main() with fake subprocess output, return exit code."""
        # main() calls run() 4 times in order:
        #   1. clang build of cref.c       -> "" (don't care)
        #   2. rustc build of bench.rs     -> "" (don't care)
        #   3. execute cref binary         -> c_output
        #   4. execute benchrs binary      -> rs_output
        outputs = iter(["", "", c_output, rs_output])

        def fake_run(cmd):
            return next(outputs)

        with patch.object(mod, "run", side_effect=fake_run):
            return mod.main()

    def test_agree_returns_zero(self):
        """All checksums agree => exit code 0."""
        # Both executables emit the same acc value for gcd
        rc = self._run_with_csv(_CSV_AGREE, "")
        self.assertEqual(rc, 0)

    def test_mismatch_returns_one(self):
        """A checksum MISMATCH => exit code 1."""
        rc = self._run_with_csv(_CSV_MISMATCH, "")
        self.assertEqual(rc, 1)

    def test_mixed_agree_and_mismatch_returns_one(self):
        """If any function mismatches, the whole run fails (exit code 1)."""
        csv_mixed = "\n".join([
            "c,gcd,100.0,SAME",
            "rust-faithful,gcd,95.0,SAME",       # gcd AGREE
            "c,int_sqrt,37.0,AAAA",
            "rust-faithful,int_sqrt,20.0,BBBB",  # int_sqrt MISMATCH
        ])
        rc = self._run_with_csv(csv_mixed, "")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
