#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Retro-test the offload harness against already-translated TUs (ground
truth) and measure real token cost — not estimated.

For each C file that already has a landed _rs.rs translation in the
riscv worktree: run offload_translate.py + offload_review.py, record
Ollama's own prompt_eval_count/eval_count (real token counts, from the
API response, not guessed), and compare the draft's line count against
the landed translation's line count.

This is a MEASUREMENT tool. It does not replace or modify any landed
translation.

Usage: offload_measure.py [--limit N]
Output: tmp/offload_measure_report.md (token counts, pass/fail per TU,
line-count comparison) + the per-TU artifacts in tmp/offload/
Log: tmp/offload_measure.log
"""
import argparse
import json
import logging
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
OUT = REPO / "tmp" / "offload"
LOG = REPO / "tmp" / "offload_measure.log"
REPORT = REPO / "tmp" / "offload_measure_report.md"
OLLAMA = "http://localhost:11434/api/generate"
MODEL = "qwen2.5-coder:14b"


def find_translated_pairs():
    """Every <name>_rs.rs in the worktree with a matching <name>.c
    sibling still on disk (the C original, kept for !CONFIG_RUST)."""
    pairs = []
    for rs in sorted(TREE.glob("lib/**/*_rs.rs")):
        c = rs.with_name(rs.name.replace("_rs.rs", ".c"))
        if c.exists():
            pairs.append((c, rs))
    return pairs


def ollama_call(prompt, timeout=300):
    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps({"model": MODEL, "prompt": prompt, "stream": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    dt = time.monotonic() - t0
    return resp, dt


def run_stage(script, c_file, extra=()):
    cmd = ["python3", str(REPO / "scripts" / script), str(c_file), "--model", MODEL, *extra]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    pairs = find_translated_pairs()
    if args.limit:
        pairs = pairs[: args.limit]
    logging.info("retro-testing %d already-translated TUs", len(pairs))

    rows = []
    for c_file, landed_rs in pairs:
        name = c_file.stem
        logging.info("=== %s ===", name)

        r1 = run_stage("offload_translate.py", c_file)
        t_usage = json.loads((OUT / f"{name}.translate_usage.json").read_text()) \
            if (OUT / f"{name}.translate_usage.json").exists() else {}
        if r1.returncode not in (0, 2):
            logging.error("%s: translate stage crashed:\n%s", name, r1.stderr[-2000:])
            rows.append(dict(name=name, status="TRANSLATE_FAIL"))
            continue
        if r1.returncode == 2:
            # Free rustc pre-filter already found the draft broken — skip
            # the (token-costing) review call entirely. This is the actual
            # savings the pre-filter buys: real bugs caught at zero token
            # cost instead of an expensive review that (per the first run
            # of this harness) rubber-stamped them anyway.
            logging.info("%s: rustc pre-filter FAILED — skipping review call (0 review tokens)",
                         name)
            rows.append(dict(
                name=name, status="RUSTC_FAIL", review_verdict="N/A (pre-filtered)",
                landed_lines=len(landed_rs.read_text().splitlines()),
                draft_lines=len((OUT / f"{name}.draft.rs").read_text().splitlines()),
                translate_prompt_tok=t_usage.get("prompt_eval_count"),
                translate_eval_tok=t_usage.get("eval_count"),
                review_prompt_tok=0, review_eval_tok=0,
            ))
            continue

        r2 = run_stage("offload_review.py", c_file)
        if r2.returncode != 0:
            logging.error("%s: review stage crashed:\n%s", name, r2.stderr[-2000:])
            rows.append(dict(name=name, status="REVIEW_FAIL"))
            continue
        review_text = (OUT / f"{name}.review.txt").read_text()
        verdict = ("FAIL" if "OVERALL: FAIL" in review_text else
                  "PASS" if "OVERALL: PASS" in review_text else "UNCLEAR")

        landed_lines = len(landed_rs.read_text().splitlines())
        draft_lines = len((OUT / f"{name}.draft.rs").read_text().splitlines())

        r_usage = json.loads((OUT / f"{name}.review_usage.json").read_text()) \
            if (OUT / f"{name}.review_usage.json").exists() else {}

        rows.append(dict(
            name=name, status="OK", review_verdict=verdict,
            landed_lines=landed_lines, draft_lines=draft_lines,
            translate_prompt_tok=t_usage.get("prompt_eval_count"),
            translate_eval_tok=t_usage.get("eval_count"),
            review_prompt_tok=r_usage.get("prompt_eval_count"),
            review_eval_tok=r_usage.get("eval_count"),
        ))
        logging.info("%s: review=%s landed=%dL draft=%dL translate_tok=%s/%s review_tok=%s/%s",
                     name, verdict, landed_lines, draft_lines,
                     t_usage.get("prompt_eval_count"), t_usage.get("eval_count"),
                     r_usage.get("prompt_eval_count"), r_usage.get("eval_count"))

    (OUT / "_retro_summary.json").write_text(json.dumps(rows, indent=2))

    ok = [r for r in rows if r["status"] == "OK"]
    n_pass = sum(1 for r in ok if r["review_verdict"] == "PASS")
    n_fail = sum(1 for r in ok if r["review_verdict"] == "FAIL")
    n_unclear = sum(1 for r in ok if r["review_verdict"] == "UNCLEAR")
    total_ollama_tok = sum((r.get("translate_prompt_tok") or 0) +
                           (r.get("translate_eval_tok") or 0) +
                           (r.get("review_prompt_tok") or 0) +
                           (r.get("review_eval_tok") or 0) for r in ok)

    md = ["# Offload harness retro-test (ground truth = landed translations)", "",
          f"- TUs tested: {len(rows)}  ·  review PASS: {n_pass}  FAIL: {n_fail}  "
          f"UNCLEAR: {n_unclear}",
          f"- Total Ollama tokens spent (translate+review, prompt+eval, all TUs): "
          f"**{total_ollama_tok:,}**",
          "",
          "Token cost is Ollama's own `prompt_eval_count`/`eval_count` from each API "
          "response — real counts, not estimated. There is no directly comparable "
          "'Claude cost for the same drafts' number without re-running the same "
          "prompts through the Claude API standalone (not done here — this run "
          "measures the offload side only, honestly, rather than guess the other "
          "side's cost).",
          "",
          "| TU | review | landed L | draft L | translate tok (prompt/eval) | "
          "review tok (prompt/eval) |",
          "|---|---|---|---|---|---|"]
    for r in rows:
        if r["status"] != "OK":
            md.append(f"| {r['name']} | {r['status']} | - | - | - | - |")
            continue
        md.append(f"| {r['name']} | {r['review_verdict']} | {r['landed_lines']} | "
                  f"{r['draft_lines']} | {r['translate_prompt_tok']}/{r['translate_eval_tok']} | "
                  f"{r['review_prompt_tok']}/{r['review_eval_tok']} |")
    REPORT.write_text("\n".join(md) + "\n")
    logging.info("wrote %s", REPORT)
    print(f"\n{n_pass} PASS / {n_fail} FAIL / {n_unclear} UNCLEAR of {len(ok)} — "
         f"{total_ollama_tok:,} total Ollama tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
