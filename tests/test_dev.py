import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev.py"
spec = importlib.util.spec_from_file_location("dev", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class BootOracleParsingTest(unittest.TestCase):
    def test_parse_boot_oracle_requires_init_reached_separately(self):
        ok, bad, init_reached = mod.parse_boot_oracle(
            "ok 1 lib_math\n"
            "ok 2 lib_sort\n"
        )

        self.assertEqual(ok, ["ok 1 lib_math", "ok 2 lib_sort"])
        self.assertEqual(bad, [])
        self.assertFalse(init_reached)

    def test_parse_boot_oracle_detects_full_success(self):
        ok, bad, init_reached = mod.parse_boot_oracle(
            "ok 1 lib_math\n"
            f"{mod.INIT_REACHED_MARKER}\n"
        )

        self.assertEqual(ok, ["ok 1 lib_math"])
        self.assertEqual(bad, [])
        self.assertTrue(init_reached)

    def test_parse_boot_oracle_detects_kunit_failure(self):
        ok, bad, init_reached = mod.parse_boot_oracle(
            "ok 1 lib_math\n"
            "not ok 2 lib_sort\n"
            f"{mod.INIT_REACHED_MARKER}\n"
        )

        self.assertEqual(ok, ["ok 1 lib_math"])
        self.assertEqual(bad, ["not ok 2 lib_sort"])
        self.assertTrue(init_reached)


if __name__ == "__main__":
    unittest.main()
