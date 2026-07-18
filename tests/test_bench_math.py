import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_math.py"
spec = importlib.util.spec_from_file_location("bench_math", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ChecksumMismatchTest(unittest.TestCase):
    def test_checksum_mismatches_accepts_agreement(self):
        funcs = {"gcd": {"c": 1.0, "rust-faithful": 1.1, "rust-opt": 0.9}}
        accs = {
            ("gcd", "c"): "abc",
            ("gcd", "rust-faithful"): "abc",
            ("gcd", "rust-opt"): "abc",
        }

        self.assertEqual(mod.checksum_mismatches(funcs, accs), [])

    def test_checksum_mismatches_reports_disagreement(self):
        funcs = {"gcd": {"c": 1.0, "rust-faithful": 1.1, "rust-opt": 0.9}}
        accs = {
            ("gcd", "c"): "abc",
            ("gcd", "rust-faithful"): "abc",
            ("gcd", "rust-opt"): "def",
        }

        self.assertEqual(mod.checksum_mismatches(funcs, accs), [("gcd", ["abc", "def"])])


if __name__ == "__main__":
    unittest.main()
