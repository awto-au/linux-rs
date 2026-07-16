#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offload a C-to-Rust kernel translation draft to a local Ollama model.

Matches rulesdb/rules/*.toml against the target C file by keyword overlap
(cheap but honest — the rule DB has no structural matcher yet), pastes
every matched rule's full TOML into the prompt (not just the one under
test — the fresh finding from 2026-07-16 is that partial rule context
lets the model freelance on anything not covered), and asks for a
faithful, no-optimisation translation per the project's stated discipline
(README "Translation discipline" section, restated in the prompt).

This produces a DRAFT ONLY. It is not integrated, not built, not trusted.
Pair with offload_review.py before using any of it.

Usage: offload_translate.py <path/to/file.c> [--model qwen2.5-coder:14b]
Output: tmp/offload/<name>.draft.rs, tmp/offload/<name>.rules.txt (which
rules were matched, for the review step and for a human to audit)
Log: tmp/offload_translate.log
"""
import argparse
import json
import logging
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    import tomllib
except ImportError:  # py<3.11 fallback not needed here, but fail clearly
    print("Python 3.11+ required (tomllib)", file=sys.stderr)
    sys.exit(1)

REPO = Path(__file__).resolve().parent.parent
RULES_DIR = REPO / "rulesdb" / "rules"
OUT = REPO / "tmp" / "offload"
LOG = REPO / "tmp" / "offload_translate.log"
OLLAMA = "http://localhost:11434/api/generate"

DISCIPLINE = """TRANSLATION DISCIPLINE (mandatory, from the project README):
- Construct-by-construct conversion, NO OPTIMISATION. Output must be
  behaviourally identical to the C, not improved.
- Do not add safety checks, saturating/checked arithmetic, early returns,
  bounds checks, or any other change UNLESS an explicit rule below
  licenses it, or the C itself has that exact behavior.
- C unsigned arithmetic wraps; if a rule doesn't say otherwise, preserve
  that with wrapping_add/wrapping_sub/wrapping_mul, never plain operators
  that would panic-on-overflow in debug builds, and never saturating_*.
- Every deviation from literal C semantics must be forced by Rust (e.g.
  explicit wrapping) or explicitly licensed by a cited rule — never a
  spontaneous "improvement".
- If unsure whether a rule applies, say so in a comment rather than
  guessing."""


def load_rules():
    rules = []
    for f in sorted(RULES_DIR.glob("*.toml")):
        d = tomllib.load(open(f, "rb"))
        rules.append((f, d))
    return rules


def extract_code(text: str) -> str:
    """Strip markdown fences if present; models reliably wrap output in
    ```rust ... ``` even when told not to. Returns the fenced block if
    found, else the text as-is."""
    m = re.search(r"```(?:rust)?\n(.*?)```", text, re.S)
    return m.group(1) if m else text


def rustc_check(rust_source: str):
    """Free, 100%-reliable pre-filter: does this even compile as a bare
    lib (no kernel crate available host-side, so #[export]/bindings-using
    code will still fail — this only usefully filters drafts requested
    without that plumbing, i.e. offload_translate's output). Returns
    (ok, diagnostic_text).

    Uses a real temp FILE for -o, not /dev/null: rustc derives its
    metadata output path from -o, and -o /dev/null intermittently makes
    it try to create a temp dir under /dev (permission denied, spurious
    failure unrelated to the source) — found 2026-07-16 when it broke a
    retry-cycle round that had nothing wrong with the draft.

    Scratch files live under REPO/tmp/, never system /tmp (project rule)."""
    import shutil
    import uuid
    scratch = REPO / "tmp" / f"rustc_check_{uuid.uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        src = scratch / "check.rs"
        out = scratch / "check.out"
        src.write_text(rust_source)
        r = subprocess.run(
            ["rustc", "--edition=2021", "--crate-type", "lib", "-o", str(out), str(src)],
            capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0, r.stderr
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def match_rules(c_source: str, rules):
    """Keyword-overlap matcher: a rule matches if any sufficiently
    distinctive word from its match.c field appears in the source.
    Coarse by design — false positives cost a few extra tokens in the
    prompt; false negatives cost real correctness. Bias toward recall."""
    matched = []
    for f, d in rules:
        match_c = d.get("match", {}).get("c", "")
        family = d.get("match", {}).get("family", "")
        # pull identifier-like tokens (function/macro names) out of match.c
        idents = set(re.findall(r"\b[a-z_][a-z0-9_]{3,}\b", match_c.lower()))
        idents |= set(re.findall(r"\b[A-Z][A-Z0-9_]{3,}\b", match_c))
        idents.discard("const")
        if family:
            idents.add(family.split(":")[-1].lower())
        src_lower = c_source.lower()
        hits = [i for i in idents if i.lower() in src_lower]
        if hits:
            matched.append((f, d, hits))
    return matched


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("c_file")
    ap.add_argument("--model", default="qwen2.5-coder:14b")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="a"), logging.StreamHandler(sys.stdout)],
    )

    c_path = Path(args.c_file)
    if not c_path.is_absolute():
        c_path = REPO / c_path
    if not c_path.exists():
        logging.error("not found: %s", c_path)
        return 1
    c_source = c_path.read_text()
    name = c_path.stem

    rules = load_rules()
    matched = match_rules(c_source, rules)
    logging.info("%s: matched %d/%d rules: %s", name, len(matched), len(rules),
                 ", ".join(d["id"] for _, d, _ in matched) or "(none)")

    rules_blob = "\n\n".join(
        f"### Rule {d['id']} (matched on: {', '.join(hits)})\n{f.read_text()}"
        for f, d, hits in matched
    )

    prompt = f"""You are translating a Linux kernel C file to Rust for the linux-rs
project. Follow the discipline and rules below exactly.

{DISCIPLINE}

MATCHED RULES FOR THIS FILE ({len(matched)} of {len(rules)} total in the DB):

{rules_blob if rules_blob else "(no rules matched — translate straightforwardly, flag anything uncertain)"}

C SOURCE ({c_path.name}):
```c
{c_source}
```

Output ONLY the translated Rust code (no kernel-crate #[export]/bindings
plumbing needed — just the function bodies with plain Rust types), no
explanation before or after."""

    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps({"model": args.model, "prompt": prompt, "stream": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    logging.info("requesting draft from %s (%d chars prompt)", args.model, len(prompt))
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
    draft_raw = resp["response"]
    draft = extract_code(draft_raw)

    compiles, diag = rustc_check(draft)
    logging.info("%s: rustc pre-filter = %s", name, "PASS" if compiles else "FAIL")
    if not compiles:
        logging.warning("%s: draft does NOT compile — free pre-filter caught this before "
                        "spending any review tokens:\n%s", name, diag[:2000])

    # Real token counts from Ollama's own accounting (prompt_eval_count =
    # tokens the model had to read; eval_count = tokens it generated) —
    # not estimated from character length.
    usage = {
        "prompt_eval_count": resp.get("prompt_eval_count"),
        "eval_count": resp.get("eval_count"),
        "prompt_eval_duration_ns": resp.get("prompt_eval_duration"),
        "eval_duration_ns": resp.get("eval_duration"),
        "total_duration_ns": resp.get("total_duration"),
        "prompt_chars": len(prompt),
        "n_rules_matched": len(matched),
        "n_rules_total": len(rules),
    }

    usage["rustc_compiles"] = compiles

    (OUT / f"{name}.draft.rs").write_text(draft)
    (OUT / f"{name}.draft_raw.txt").write_text(draft_raw)
    if not compiles:
        (OUT / f"{name}.rustc_errors.txt").write_text(diag)
    (OUT / f"{name}.rules.txt").write_text(
        "\n".join(f"{d['id']} (hits: {', '.join(hits)})" for _, d, hits in matched)
    )
    (OUT / f"{name}.translate_usage.json").write_text(json.dumps(usage, indent=2))
    logging.info("wrote %s, %s, %s", OUT / f"{name}.draft.rs", OUT / f"{name}.rules.txt",
                 OUT / f"{name}.translate_usage.json")
    logging.info("tokens: prompt=%s eval=%s", usage["prompt_eval_count"], usage["eval_count"])
    print(draft)
    return 0 if compiles else 2  # distinct exit code: ran fine, draft is just broken


if __name__ == "__main__":
    sys.exit(main())
