import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_c2rust_rule_conformance.py"
spec = importlib.util.spec_from_file_location("check_c2rust_rule_conformance", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class SafeNameMappingTest(unittest.TestCase):
    def test_safe_name_candidates_cover_new_directory_families(self):
        rels = [
            "drivers/base/core.c",
            "kernel/fork.c",
            "lib/math/gcd.c",
        ]

        self.assertEqual(
            mod.safe_name_candidates("drivers_base_core.c", rels),
            ["drivers/base/core.c"],
        )
        self.assertEqual(mod.safe_name_candidates("kernel_fork.c", rels), ["kernel/fork.c"])

    def test_safe_name_candidates_preserve_underscore_ambiguity(self):
        rels = [
            "lib/crc/crc32-main.c",
            "drivers/foo_bar/baz.c",
            "drivers/foo/bar_baz.c",
        ]

        self.assertEqual(
            mod.safe_name_candidates("lib_crc_crc32-main.c", rels),
            ["lib/crc/crc32-main.c"],
        )
        self.assertEqual(
            mod.safe_name_candidates("drivers_foo_bar_baz.c", rels),
            [
                "drivers/foo/bar_baz.c",
                "drivers/foo_bar/baz.c",
            ],
        )


if __name__ == "__main__":
    unittest.main()
