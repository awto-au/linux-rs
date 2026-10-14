#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Per-TU dependency derivation for drift tracking (#119 step 7). Library, no CLI.

- header deps: `deps_<obj>` in kbuild `<dir>/.<c_basename>.o.cmd` (last C build of c_source_file).
  Kept: tracked *.h under include/, arch/ or quoted-local to the C file; generated/ and config/ dropped.
- rust deps (from the Rust TU source, resolved against the tree's rust/):
  - `kernel::a::b::Item` → longest rust/kernel module file; else the file defining Item
  - `name!` / `#[name]` → rust/kernel file with `macro_rules! name`, or rust/macros lib.rs + impl module
  - `bindings::x` → rust/bindings/bindings_helper.h + rust/helpers/*.c defining `rust_helper_x`
- Only paths present at the record rev are kept (series-only files cannot drift upstream).
- inline_names(): static inlines / #defines touched by a header diff (re-implemented inlines, rule 0016).
"""
import os
import re
import subprocess
from pathlib import Path

ITEM_DEF = r"(?:pub(?:\([^)]*\))?\s+)?(?:unsafe\s+)?(?:const\s+)?(?:fn|struct|enum|trait|type|const|static|union)\s+{}\b"


def git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args],
                          capture_output=True, text=True, check=True).stdout


def blobs_at(tree: Path, rev: str, paths) -> dict[str, str]:
    """{path: blob sha} at rev for the paths that exist there (one ls-tree call)."""
    paths = sorted(set(paths))
    if not paths:
        return {}
    out = {}
    for line in git(tree, "ls-tree", rev, "--", *paths).splitlines():
        meta, path = line.split("\t", 1)
        out[path] = meta.split()[2]
    return out


def dep_kind(path: str) -> str:
    return "rust" if path.startswith("rust/") else "header"


def header_deps(tree: Path, c_file: str) -> list[str] | None:
    """Headers listed in the kbuild .cmd of c_file's object; None if never built."""
    d, base = c_file.rsplit("/", 1)
    cmd = tree / d / f".{base[:-2]}.o.cmd"
    if not cmd.exists():
        return None
    text = cmd.read_text()
    m = re.search(r"^deps_\S+ := \\\n(.*?)\n\n", text, re.S | re.M)
    if not m:
        return None
    out = set()
    for line in m.group(1).splitlines():
        p = line.strip().rstrip("\\").strip()
        if not p or p.startswith("$(") or not p.endswith(".h"):
            continue
        local = p.startswith(d + "/")  # quoted include relative to the TU, may contain ../
        p = os.path.normpath(p)
        if "/generated/" in p or p.startswith("include/config/"):
            continue
        if p.startswith(("include/", "arch/")) or local:
            out.add(p)
    return sorted(out)


class RustIndex:
    """Lazy index over the tree's rust/kernel, rust/macros, rust/helpers sources."""

    def __init__(self, tree: Path):
        self.tree = tree
        self._files: dict[str, str] | None = None
        self._helpers: dict[str, str] | None = None

    def files(self) -> dict[str, str]:
        if self._files is None:
            self._files = {str(p.relative_to(self.tree)): p.read_text(errors="replace")
                           for p in sorted((self.tree / "rust" / "kernel").rglob("*.rs"))}
        return self._files

    def helpers(self) -> dict[str, str]:
        """rust_helper_<name> → helper .c file."""
        if self._helpers is None:
            self._helpers = {}
            for p in sorted((self.tree / "rust" / "helpers").glob("*.c")):
                for name in re.findall(r"\brust_helper_(\w+)\s*\(", p.read_text(errors="replace")):
                    self._helpers.setdefault(name, str(p.relative_to(self.tree)))
        return self._helpers

    def module_file(self, segs: list[str]) -> str | None:
        rel = "/".join(segs)
        for cand in (f"rust/kernel/{rel}.rs", f"rust/kernel/{rel}/mod.rs"):
            if (self.tree / cand).exists():
                return cand
        return None

    def defining(self, name: str, under: str = "rust/kernel/", macro: bool = False) -> list[str]:
        pat = re.compile(rf"macro_rules!\s+{name}\b" if macro else ITEM_DEF.format(name))
        return [f for f, src in self.files().items() if f.startswith(under) and pat.search(src)]

    def proc_macro(self, name: str) -> list[str]:
        lib = self.tree / "rust/macros/lib.rs"
        src = lib.read_text()
        m = re.search(rf"#\[proc_macro(?:_attribute|_derive)?[^\]]*\]\s*(?:#\[[^\]]*\]\s*)*pub fn {name}\b(.*?)(?=\n#\[|\Z)", src, re.S)
        if not m:
            return []
        out = ["rust/macros/lib.rs"]
        for mod in sorted(set(re.findall(r"\b(\w+)::\w+\s*\(", m.group(1)))):
            if (self.tree / f"rust/macros/{mod}.rs").exists():
                out.append(f"rust/macros/{mod}.rs")
        return out

    def resolve_path(self, segs: list[str]) -> list[str]:
        """kernel::<segs...> → rust/ file(s)."""
        if segs[0] == "bindings":
            return self.resolve_binding(segs[1]) if len(segs) > 1 else ["rust/bindings/bindings_helper.h"]
        for n in range(len(segs), 0, -1):
            f = self.module_file(segs[:n])
            if f:
                if n < len(segs) and segs[n] not in ("*", "self"):
                    # item re-exported from a submodule: find its definition under the module dir
                    under = f.rsplit("/", 1)[0] + "/" if f.endswith("mod.rs") else f[:-3] + "/"
                    hits = self.defining(segs[n], under)
                    return [f, *hits]
                return [f]
        if (self.tree / f"rust/{segs[0]}.rs").exists():  # re-exported crate (ffi, ...)
            return [f"rust/{segs[0]}.rs"]
        hits = self.defining(segs[0], macro=True) or self.defining(segs[0])
        return hits or ["rust/kernel/lib.rs"]

    def resolve_binding(self, name: str) -> list[str]:
        out = ["rust/bindings/bindings_helper.h"]
        if name in self.helpers():
            out.append(self.helpers()[name])
        return out


def _use_paths(src: str) -> list[list[str]]:
    """Expand `use kernel::{a, b::{c, d}};` into segment lists rooted at kernel/bindings."""
    out = []

    def expand(prefix: list[str], body: str):
        depth, start, parts = 0, 0, []
        for i, ch in enumerate(body):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(body[start:i])
                start = i + 1
        parts.append(body[start:])
        for part in parts:
            part = re.sub(r"\s+as\s+\w+\s*$", "", part.strip())
            if not part:
                continue
            m = re.match(r"([\w:]*?)::\{(.*)\}$", part, re.S)
            if m:
                expand(prefix + [s for s in m.group(1).split("::") if s], m.group(2))
            elif part.startswith("{") and part.endswith("}"):
                expand(prefix, part[1:-1])
            else:
                out.append(prefix + [s for s in part.split("::") if s])

    for m in re.finditer(r"\buse\s+([^;]+);", src):
        expand([], m.group(1))
    return [p for p in out if p]


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def rust_deps(tree: Path, rs_file: str, idx: RustIndex) -> list[str]:
    src = strip_comments((tree / rs_file).read_text())
    uses = _use_paths(src)
    foreign = {p[-1] for p in uses if p[0] not in ("kernel", "bindings")}  # e.g. core::arch::asm
    paths = [p for p in uses if p[0] in ("kernel", "bindings")]
    paths += [m.group(0).split("::") for m in re.finditer(r"\b(?:kernel|bindings)(?:::\w+)+", src)]
    out = set()
    for segs in paths:
        if segs[0] == "bindings":
            segs = ["kernel", *segs]
        if len(segs) > 1:
            out.update(idx.resolve_path(segs[1:]))
    macros = set(re.findall(r"(?<![\w:])(\w+)!\s*[\(\[\{]", src))
    attrs = set(re.findall(r"#\[(\w+)", src))
    for name in sorted((macros | attrs) - foreign):
        hits = idx.proc_macro(name) or (idx.defining(name, macro=True) if name in macros else [])
        out.update(hits)
    return sorted(out)


def derive(tree: Path, rs_file: str, c_file: str, idx: RustIndex) -> list[str] | None:
    hdrs = header_deps(tree, c_file)
    if hdrs is None:
        return None
    return sorted(set(hdrs) | set(rust_deps(tree, rs_file, idx)))


# ---- re-implemented inline detection ----

INLINE_RE = re.compile(r"^\s*(?:static\s+)?(?:__always_inline|inline|__inline__|__inline)\b")


def _symbol_ranges(lines: list[str]) -> list[tuple[int, int, str]]:
    """(first, last, name) 1-based line ranges of #defines and static inline functions."""
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        m = re.match(r"\s*#\s*define\s+(\w+)", line)
        if m:
            j = i
            while lines[j].rstrip().endswith("\\") and j + 1 < n:
                j += 1
            out.append((i + 1, j + 1, m.group(1)))
            i = j + 1
            continue
        if INLINE_RE.match(line) or re.match(r"^static\s.*\binline\b", line):
            j, decl = i, line
            while "{" not in lines[j] and ";" not in lines[j] and j + 1 < n:
                j += 1
                decl += " " + lines[j]
            names = [x for x in re.findall(r"(\w+)\s*\(", decl.split("{")[0])
                     if not x.startswith("__attribute") and x not in ("__aligned", "__section")]
            if "{" in lines[j] and names:
                depth = 0
                k = j
                while k < n:
                    depth += lines[k].count("{") - lines[k].count("}")
                    if depth <= 0 and "}" in lines[k]:
                        break
                    k += 1
                out.append((i + 1, k + 1, names[0]))
                i = k + 1
                continue
        i += 1
    return out


def inline_names(tree: Path, old: str, new: str) -> set[str]:
    """Names of #defines / static inlines whose lines a blob diff old→new touches."""
    old_l = git(tree, "cat-file", "-p", old).splitlines()
    new_l = git(tree, "cat-file", "-p", new).splitlines()
    old_r, new_r = _symbol_ranges(old_l), _symbol_ranges(new_l)
    names = set()
    for h in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", git(tree, "diff", "-U0", old, new), re.M):
        for start, cnt, ranges in ((int(h[1]), int(h[2] or 1), old_r), (int(h[3]), int(h[4] or 1), new_r)):
            if cnt == 0:
                continue
            last = start + cnt - 1
            names.update(nm for a, b, nm in ranges if a <= last and start <= b)
    return names


def identifiers(src: str) -> set[str]:
    return set(re.findall(r"\b[A-Za-z_]\w*\b", src))
