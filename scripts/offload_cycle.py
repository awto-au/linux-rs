#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Full offload cycle: draft -> free gates -> retry-with-diagnostic ->
escalate to independent review only once free gates pass.

Gate order (cheapest/most-reliable first, per the 2026-07-16 finding that
rustc alone caught 3/3 real bugs a paid review call missed):
  1. rustc --crate-type lib   (compiles at all)
  2. cargo clippy equivalent: clippy-driver (lints, catches more classes
     of "technically compiles but wrong", e.g. suspicious casts)
  3. (not yet wired: tier-2.5 diff-oracle equivalence, if a matching
     bench/diff_<name>.c reference exists — see --oracle)

On gate failure: the diagnostic text is fed back into a NEW Ollama call
("here is your draft, here is the compiler error, fix it") — NOT a
fresh-context retry, deliberately: the model needs the error in context
to fix it, this is debugging not re-drafting. Retries up to --max-rounds.

Only a draft that clears every free gate should be passed on to
offload_review.py for the paid independent-review stage — the caller
is responsible for invoking it; this script does not chain into it.
If no draft ever clears the
gates, the cycle reports FAILED — this is the intended terminal state
until the underlying model/prompting is improved ("if not good we still
review, but we do the whole cycle again until we work out how to get
ollama to do the work" — the loop is for iterating on the HARNESS, not
for shipping an unclean draft).

Usage: offload_cycle.py <path/to/file.c> [--max-rounds 3] [--model ...]
Output: tmp/offload/<name>.cycle.json (full round-by-round record)
Log: tmp/offload_cycle.log
"""
import argparse
import json
import logging
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "tmp" / "offload"
LOG = REPO / "tmp" / "offload_cycle.log"
OLLAMA = "http://localhost:11434/api/generate"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from offload_translate import (DISCIPLINE, extract_code,  # noqa: E402
                               load_rules, match_rules, rustc_check)


def clippy_check(rust_source: str):
    """clippy-driver on the same standalone file. Returns (clean, text)
    — clean means no warnings/errors at default lint level. Real temp
    output path under REPO/tmp/ (never system /tmp — project rule; also
    avoids the /dev/null spurious-failure bug — see rustc_check)."""
    import shutil
    import uuid
    scratch = REPO / "tmp" / f"clippy_check_{uuid.uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        src = scratch / "check.rs"
        out = scratch / "check.out"
        src.write_text(rust_source)
        r = subprocess.run(
            ["clippy-driver", "--edition=2021", "--crate-type", "lib",
             "-o", str(out), str(src)],
            capture_output=True, text=True, timeout=60,
        )
        has_errors = "error[" in r.stderr or "error:" in r.stderr or "warning:" in r.stderr
        return (r.returncode == 0 and not has_errors), r.stderr
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def ollama_generate(prompt, model, timeout=300):
    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return resp


def build_initial_prompt(c_source, c_name, matched):
    rules_blob = "\n\n".join(
        f"### Rule {d['id']} (matched on: {', '.join(hits)})\n{f.read_text()}"
        for f, d, hits in matched
    )
    return f"""You are translating a Linux kernel C file to Rust for the linux-rs
project. Follow the discipline and rules below exactly.

{DISCIPLINE}

MATCHED RULES FOR THIS FILE ({len(matched)} total):

{rules_blob if rules_blob else "(no rules matched)"}

C SOURCE ({c_name}):
```c
{c_source}
```

Output ONLY the translated Rust code as a single ```rust code block,
using plain std/core types (no kernel-crate #[export]/bindings needed —
this draft will be compiled standalone with `rustc --crate-type lib` to
check it), no explanation before or after."""


def build_retry_prompt(c_source, c_name, prev_draft, diagnostic, gate_name):
    return f"""Your previous Rust translation of this C file failed the
{gate_name} check. Fix ONLY the reported problem(s) — do not change
anything else, do not "improve" unrelated code, keep every other line
identical to your previous draft.

C SOURCE ({c_name}):
```c
{c_source}
```

YOUR PREVIOUS DRAFT:
```rust
{prev_draft}
```

{gate_name.upper()} OUTPUT:
```
{diagnostic}
```

Output ONLY the corrected Rust code as a single ```rust code block, no
explanation."""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("c_file")
    ap.add_argument("--model", default="qwen2.5-coder:14b")
    ap.add_argument("--max-rounds", type=int, default=3)
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
    logging.info("%s: matched %d/%d rules", name, len(matched), len(rules))

    prompt = build_initial_prompt(c_source, c_path.name, matched)
    rounds = []
    draft = None
    total_tok = 0

    for round_n in range(1, args.max_rounds + 1):
        logging.info("=== %s round %d/%d ===", name, round_n, args.max_rounds)
        resp = ollama_generate(prompt, args.model)
        draft_raw = resp["response"]
        draft = extract_code(draft_raw)
        tok = (resp.get("prompt_eval_count") or 0) + (resp.get("eval_count") or 0)
        total_tok += tok

        rustc_ok, rustc_diag = rustc_check(draft)
        round_record = dict(round=round_n, prompt_tok=resp.get("prompt_eval_count"),
                            eval_tok=resp.get("eval_count"), rustc_ok=rustc_ok)
        logging.info("round %d: %d tokens, rustc=%s", round_n, tok,
                     "OK" if rustc_ok else "FAIL")

        if not rustc_ok:
            round_record["gate_failed"] = "rustc"
            round_record["diagnostic"] = rustc_diag[:3000]
            rounds.append(round_record)
            prompt = build_retry_prompt(c_source, c_path.name, draft, rustc_diag, "rustc")
            continue

        clippy_ok, clippy_diag = clippy_check(draft)
        round_record["clippy_ok"] = clippy_ok
        logging.info("round %d: clippy=%s", round_n, "OK" if clippy_ok else "FAIL")

        if not clippy_ok:
            round_record["gate_failed"] = "clippy"
            round_record["diagnostic"] = clippy_diag[:3000]
            rounds.append(round_record)
            prompt = build_retry_prompt(c_source, c_path.name, draft, clippy_diag,
                                       "clippy (hard error)")
            continue

        # Both free gates clear.
        round_record["gate_failed"] = None
        rounds.append(round_record)
        logging.info("%s: CLEARED free gates after %d round(s), %d total tokens",
                     name, round_n, total_tok)
        break
    else:
        logging.warning("%s: did NOT clear free gates within %d rounds (%d tokens spent)",
                        name, args.max_rounds, total_tok)

    cleared = rounds[-1]["gate_failed"] is None
    (OUT / f"{name}.draft.rs").write_text(draft or "")
    (OUT / f"{name}.rules.txt").write_text(
        "\n".join(f"{d['id']} (hits: {', '.join(hits)})" for _, d, hits in matched)
    )
    record = dict(name=name, cleared_free_gates=cleared, rounds=len(rounds),
                 total_tokens=total_tok, round_detail=rounds)
    (OUT / f"{name}.cycle.json").write_text(json.dumps(record, indent=2))
    logging.info("wrote %s", OUT / f"{name}.cycle.json")

    print(json.dumps(dict(name=name, cleared=cleared, rounds=len(rounds),
                          total_tokens=total_tok)))
    return 0 if cleared else 3


if __name__ == "__main__":
    sys.exit(main())
