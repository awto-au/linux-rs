import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply_c2rust_fix_classes.py"
spec = importlib.util.spec_from_file_location("apply_c2rust_fix_classes", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class WarnOnNeq0Test(unittest.TestCase):
    def fix(self, text):
        return mod.fix_warn_on_neq0(text)

    def test_plain_neq0_dropped(self):
        self.assertEqual(
            self.fix("if kernel::warn_on!(ret != 0) != 0 {"),
            ("if kernel::warn_on!(ret != 0) {", 1),
        )

    def test_nested_call_inner_eq0_kept(self):
        # C WARN_ON(!f(x)): the inner `== 0` is the condition, only the outer goes.
        self.assertEqual(
            self.fix("if kernel::warn_on!(f(x) == 0) != 0 {"),
            ("if kernel::warn_on!(f(x) == 0) {", 1),
        )

    def test_nested_method_call(self):
        self.assertEqual(
            self.fix("if kernel::warn_on!(x.is_none()) != 0 {"),
            ("if kernel::warn_on!(x.is_none()) {", 1),
        )

    def test_eq0_negated(self):
        # C !WARN_ON(a)
        self.assertEqual(
            self.fix("if kernel::warn_on!(a) == 0 {"),
            ("if !kernel::warn_on!(a) {", 1),
        )

    def test_nested_call_eq0_negated(self):
        self.assertEqual(
            self.fix("if kernel::warn_on!(f(g(x)) != 0) == 0 {"),
            ("if !kernel::warn_on!(f(g(x)) != 0) {", 1),
        )

    def test_no_outer_comparison_untouched(self):
        for s in (
            "kernel::warn_on!(f(x) == 0);",
            "kernel::warn_on!(x.is_none());",
            "kernel::warn_on!(a != 0);",
        ):
            self.assertEqual(self.fix(s), (s, 0))

    def test_comparison_to_nonzero_literal_untouched(self):
        s = "if kernel::warn_on!(a) != 0x10 {"
        self.assertEqual(self.fix(s), (s, 0))

    def test_multiline_and_multiple_sites(self):
        src = (
            "if kernel::warn_on!(\n    f(x) == 0\n) != 0 {\n}\n"
            "if kernel::warn_on!(g(y)) == 0 {\n}\n"
        )
        out, n = self.fix(src)
        self.assertEqual(n, 2)
        self.assertEqual(
            out,
            "if kernel::warn_on!(\n    f(x) == 0\n) {\n}\n"
            "if !kernel::warn_on!(g(y)) {\n}\n",
        )

    def test_nested_warn_on_inside_argument(self):
        self.assertEqual(
            self.fix("if kernel::warn_on!(kernel::warn_on!(a) != 0 && b) != 0 {"),
            ("if kernel::warn_on!(kernel::warn_on!(a) && b) {", 2),
        )


class LibcSizeTMemcpyArgTest(unittest.TestCase):
    def test_size_arg_cast_to_usize(self):
        self.assertEqual(
            mod.fix_libc_size_t_memcpy_arg("bindings::memcpy(d, s, n as ::libc::size_t);"),
            ("bindings::memcpy(d, s, n as usize);", 1),
        )

    def test_other_size_t_untouched(self):
        s = "let x = n as ::libc::size_t;"
        self.assertEqual(mod.fix_libc_size_t_memcpy_arg(s), (s, 0))


class ParamShadowsTupleStructTest(unittest.TestCase):
    DECL = "pub struct klist(pub C2Rust_klist_Inner);\n"

    def test_renamed_in_own_body(self):
        src = self.DECL + "unsafe extern \"C\" fn f(mut klist: *mut klist) {\n    g(klist);\n}\n"
        out, n, flagged = mod.fix_param_shadows_tuple_struct(src)
        self.assertEqual((n, flagged), (1, []))
        self.assertIn("fn f(mut klist_: *mut klist) {\n    g(klist_);\n}", out)

    def test_turbofish_or_field_flagged(self):
        for body in ("size_of::<klist>()", "(*p).klist"):
            src = self.DECL + f"fn f(mut klist: *mut klist) {{ {body}; }}\n"
            out, n, flagged = mod.fix_param_shadows_tuple_struct(src)
            self.assertEqual((out, n, len(flagged)), (src, 0, 1))


if __name__ == "__main__":
    unittest.main()
