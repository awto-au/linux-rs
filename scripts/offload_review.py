#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Fresh-context conformance review of an offloaded translation draft.

Independent Ollama call (no memory of drafting) given: the original C,
the matched rules, and the candidate Rust. Asked to list every deviation
from literal C semantics and say whether a rule licenses it. This is the
second stage of the two-stage harness — 2026-07-16 testing showed a
tightened rule alone did not stop the model from freelancing unlicensed
"improvements" (e.g. saturating_sub where C had plain wrapping
subtraction); an independent review pass caught it.

Usage: offload_review.py <path/to/file.c> [--model qwen2.5-coder:14b]
  (reads tmp/offload/<name>.draft.rs + .rules.txt written by
  offload_translate.py — run that first)
Output: tmp/offload/<name>.review.txt
Log: tmp/offload_review.log
"""
import argparse
import json
import logging
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RULES_DIR = REPO / "rulesdb" / "rules"
OUT = REPO / "tmp" / "offload"
LOG = REPO / "tmp" / "offload_review.log"
OLLAMA = "http://localhost:11434/api/generate"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("c_file")
    ap.add_argument("--model", default="qwen2.5-coder:14b")
    ap.add_argument("--rust-file", help="override: review this file instead of the .draft.rs")
    args = ap.parse_args()

    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="a"), logging.StreamHandler(sys.stdout)],
    )

    c_path = Path(args.c_file)
    if not c_path.is_absolute():
        c_path = REPO / c_path
    name = c_path.stem
    c_source = c_path.read_text()

    rust_path = Path(args.rust_file) if args.rust_file else OUT / f"{name}.draft.rs"
    if not rust_path.exists():
        logging.error("no draft at %s — run offload_translate.py first (or pass --rust-file)",
                      rust_path)
        return 1
    rust_source = rust_path.read_text()

    rules_list_path = OUT / f"{name}.rules.txt"
    matched_ids = []
    if rules_list_path.exists():
        matched_ids = [line.split(" (hits:")[0] for line in
                      rules_list_path.read_text().splitlines() if line.strip()]
    rules_blob = "\n\n".join(
        (RULES_DIR / f"{rid}.toml").read_text()
        for rid in matched_ids
        if (RULES_DIR / f"{rid}.toml").exists()
    )

    prompt = f"""You are reviewing a candidate C-to-Rust kernel translation for a
project with a strict rule: the translation must be semantically
FAITHFUL to the C, with NO added safety/behavior changes (no
saturating/checked arithmetic, no early returns, no bounds checks, no
"improvements") unless an explicit rule below licenses it, or Rust
FORCES the change (e.g. explicit wrapping_* for C's implicit unsigned
wraparound, since plain +/-/* would panic-on-overflow in debug builds).

ORIGINAL C ({c_path.name}):
```c
{c_source}
```

RULES MATCHED FOR THIS FILE:
{rules_blob if rules_blob else "(none matched)"}

CANDIDATE RUST TRANSLATION:
```rust
{rust_source}
```

List every deviation from the C semantics in the candidate, whether or
not it is licensed by a rule. For each: (1) what changed, (2) whether a
rule or Rust's type system forces it, (3) VERDICT: OK or BUG. Be
exhaustive — check every arithmetic operator, every conditional, every
early return, not just the constructs the rules mention. End with a
single line: "OVERALL: PASS" or "OVERALL: FAIL (n bugs)"."""

    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps({"model": args.model, "prompt": prompt, "stream": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    logging.info("requesting review from %s (%d chars prompt)", args.model, len(prompt))
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
    review = resp["response"]

    usage = {
        "prompt_eval_count": resp.get("prompt_eval_count"),
        "eval_count": resp.get("eval_count"),
        "prompt_eval_duration_ns": resp.get("prompt_eval_duration"),
        "eval_duration_ns": resp.get("eval_duration"),
        "total_duration_ns": resp.get("total_duration"),
        "prompt_chars": len(prompt),
    }

    (OUT / f"{name}.review.txt").write_text(review)
    (OUT / f"{name}.review_usage.json").write_text(json.dumps(usage, indent=2))
    verdict = "FAIL" if "OVERALL: FAIL" in review else (
        "PASS" if "OVERALL: PASS" in review else "UNCLEAR")
    logging.info("%s: review verdict = %s, tokens: prompt=%s eval=%s",
                 name, verdict, usage["prompt_eval_count"], usage["eval_count"])
    print(review)
    return 0


if __name__ == "__main__":
    sys.exit(main())
