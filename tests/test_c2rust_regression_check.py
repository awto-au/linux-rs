import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "c2rust_regression_check.py"
spec = importlib.util.spec_from_file_location("c2rust_regression_check", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class CompileOutcomeDiffTest(unittest.TestCase):
    def test_compile_outcome_diff_flags_ok_to_error(self):
        before = {
            ("lib/foo.c", "tmp/c2rust-baseline/lib_foo.c/output/src/foo.rs"): "ok",
            ("lib/bar.c", "tmp/c2rust-baseline/lib_bar.c/output/src/bar.rs"): "error",
        }
        after = {
            ("lib/foo.c", "tmp/c2rust-baseline/lib_foo.c/output/src/foo.rs"): "error",
            ("lib/bar.c", "tmp/c2rust-baseline/lib_bar.c/output/src/bar.rs"): "ok",
        }

        regressed, fixed, unchanged_ok, unchanged_bad, new_output, removed_output = (
            mod.compile_outcome_diff(before, after)
        )

        self.assertEqual(
            regressed,
            [(("lib/foo.c", "tmp/c2rust-baseline/lib_foo.c/output/src/foo.rs"), "ok", "error")],
        )
        self.assertEqual(
            fixed,
            [(("lib/bar.c", "tmp/c2rust-baseline/lib_bar.c/output/src/bar.rs"), "error", "ok")],
        )
        self.assertEqual(unchanged_ok, 0)
        self.assertEqual(unchanged_bad, 0)
        self.assertEqual(new_output, [])
        self.assertEqual(removed_output, [])


if __name__ == "__main__":
    unittest.main()
