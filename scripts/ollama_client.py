#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Shared Ollama /api/generate call: offload_translate.py and
offload_review.py each independently inlined the identical 5-line
request-building/read pattern in their main(), and offload_cycle.py had
its own near-identical ollama_generate() helper (same shape, model as an
explicit param instead of an argparse default) — three copies of one
HTTP call to keep in sync. Consolidated 2026-07-31 as part of the
code-review action plan's duplicated-helper-logic cleanup.

offload_measure.py had a fourth variant, ollama_call() (module-const
model, returned a (resp, dt) timing tuple) — confirmed to have zero
callers anywhere in that script (it shells out to offload_translate.py/
offload_review.py as subprocesses and reads their real token-count
output instead, per its own docstring), so it was dead code, not a
genuine fourth live duplicate. Deleted rather than carried forward;
timing, if ever needed again, belongs at the call site (time.monotonic()
around ollama_generate()), not baked into the shared primitive every
caller pays for.

Usage: from ollama_client import OLLAMA, ollama_generate
"""
import json
import urllib.request

OLLAMA = "http://localhost:11434/api/generate"


def ollama_generate(prompt, model, timeout=300):
    """Returns the full parsed response dict — callers read resp["response"]
    for the generated text and resp.get("prompt_eval_count")/"eval_count"/
    etc. for Ollama's own real token/timing accounting, not estimated."""
    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
