#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Render tmp/qemu-boot.log as a standalone HTML viewer — KUnit ok/not-ok
lines and the init-reached milestone get a left-edge stripe and light
highlight so the pass/fail sequence is scannable without reading every
SBI/kernel banner line in between.

Usage: render_boot_log.py [--log tmp/qemu-boot.log] [--out FILE]
Inputs: the named boot log (see scripts/boot_qemu.py; --run-id produces
        tmp/qemu-boot-<id>.log for a non-default run)
Output: tmp/boot-log-viewer.html by default
Log: tmp/render_boot_log.log
"""
import argparse
import html
import logging
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "render_boot_log.log"

OK_RE = re.compile(r"^ok \d+ ")
NOTOK_RE = re.compile(r"^\s*not ok ")
TOTALS_RE = re.compile(r"^# Totals:")
MILESTONE = "linux-rs: initramfs init reached, PID 1 alive"
PANIC_RE = re.compile(r"Kernel panic")

FONT_STACK = ("ui-monospace, 'SF Mono', 'Cascadia Code', 'Consolas', "
              "'Liberation Mono', monospace")


def classify(line: str) -> str:
    if OK_RE.match(line):
        return "ok"
    if NOTOK_RE.match(line):
        return "notok"
    if MILESTONE in line:
        return "milestone"
    if TOTALS_RE.match(line):
        return "totals"
    if PANIC_RE.search(line):
        return "panic"
    return ""


def render(log_path: Path) -> str:
    text = log_path.read_text(errors="replace")
    lines = text.splitlines()

    n_ok = sum(1 for l in lines if OK_RE.match(l))
    n_notok = sum(1 for l in lines if NOTOK_RE.match(l))
    reached_init = MILESTONE in text
    verdict_ok = n_ok > 0 and n_notok == 0

    rows = []
    for i, line in enumerate(lines, 1):
        cls = classify(line)
        cls_attr = f' class="{cls}"' if cls else ""
        rows.append(
            f'<tr{cls_attr}><td class="ln">{i}</td>'
            f'<td class="tx">{html.escape(line) or "&nbsp;"}</td></tr>'
        )

    verdict_label = "ORACLE PASS" if verdict_ok else "ORACLE FAIL"
    verdict_cls = "pass" if verdict_ok else "fail"
    init_label = "INIT REACHED" if reached_init else "init milestone not seen"
    init_cls = "pass" if reached_init else "warn"

    return f"""<title>linux-rs — boot log — {log_path.name}</title>
<meta name="description" content="QEMU riscv64 boot serial console, {n_ok} KUnit suites, {'INIT REACHED' if reached_init else 'no init milestone'}">
<style>
:root {{
  --bg: #faf8f4; --panel: #f3f0e9; --border: #ddd6c7;
  --text: #14120f; --text-dim: #8a8477;
  --amber: #b97a1f; --amber-bg: #f6e9d3;
  --red: #b23327; --red-bg: #fbe4e1;
  --line-h: 1.45;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14120f; --panel: #1c1a15; --border: #34301f;
    --text: #f2ede1; --text-dim: #8a8477;
    --amber: #d99a3d; --amber-bg: #362a12;
    --red: #e2685c; --red-bg: #3a1712;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #14120f; --panel: #1c1a15; --border: #34301f;
  --text: #f2ede1; --text-dim: #8a8477;
  --amber: #d99a3d; --amber-bg: #362a12;
  --red: #e2685c; --red-bg: #3a1712;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}}
header {{
  padding: 1.1rem 1.5rem; border-bottom: 1px solid var(--border);
  display: flex; align-items: baseline; gap: 1.1rem; flex-wrap: wrap;
}}
h1 {{
  font-size: 0.95rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; margin: 0; color: var(--text-dim);
}}
.src {{ font-family: {FONT_STACK}; font-size: 0.85rem; color: var(--text-dim); }}
.badges {{ display: flex; gap: 0.5rem; margin-left: auto; }}
.badge {{
  font-size: 0.78rem; font-weight: 700; padding: 0.28rem 0.7rem; border-radius: 5px;
  text-transform: uppercase; letter-spacing: 0.03em;
}}
.badge.pass {{ background: var(--amber-bg); color: var(--amber); }}
.badge.fail, .badge.warn {{ background: var(--red-bg); color: var(--red); }}
.stats {{ padding: 0.6rem 1.5rem; font-size: 0.82rem; color: var(--text-dim); }}
.stats b {{ color: var(--text); font-variant-numeric: tabular-nums; }}
main {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-family: {FONT_STACK}; font-size: 0.83rem; }}
td {{ padding: 0.08rem 0.9rem; white-space: pre; vertical-align: top; line-height: var(--line-h); }}
td.ln {{
  color: var(--text-dim); text-align: right; user-select: none; width: 3.5rem;
  border-right: 1px solid var(--border); opacity: 0.55;
}}
td.tx {{ border-left: 3px solid transparent; }}
tr.ok td.tx {{ background: var(--amber-bg); border-left-color: var(--amber); font-weight: 600; }}
tr.milestone td.tx {{ background: var(--amber-bg); border-left-color: var(--amber); font-weight: 700; }}
tr.totals td.tx {{ color: var(--text-dim); }}
tr.notok td.tx, tr.panic td.tx {{ background: var(--red-bg); border-left-color: var(--red); font-weight: 700; }}
footer {{ padding: 1rem 1.5rem 2.5rem; color: var(--text-dim); font-size: 0.8rem; }}
</style>
<header>
  <h1>linux-rs boot log</h1>
  <span class="src">{html.escape(log_path.name)}</span>
  <div class="badges">
    <span class="badge {verdict_cls}">{verdict_label}</span>
    <span class="badge {init_cls}">{init_label}</span>
  </div>
</header>
<div class="stats">{n_ok} KUnit suites passed, {n_notok} failed &middot; {len(lines)} lines &middot; riscv64 / QEMU virt</div>
<main>
<table>
<tbody>
{"".join(rows)}
</tbody>
</table>
</main>
<footer>Generated by <code>scripts/render_boot_log.py</code> from the real serial console capture — every line above is unmodified QEMU output, not reformatted.</footer>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(REPO / "tmp" / "qemu-boot.log"))
    ap.add_argument("--out", default=str(REPO / "tmp" / "boot-log-viewer.html"))
    args = ap.parse_args()

    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.FileHandler(LOG, mode="a"), logging.StreamHandler(sys.stdout)],
    )

    log_path = Path(args.log)
    if not log_path.exists():
        logging.error("no boot log at %s — run dev.py boot/check first", log_path)
        return 1

    out_path = Path(args.out)
    out_path.write_text(render(log_path))
    logging.info("wrote %s", out_path)
    print(f"RENDER OK: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
