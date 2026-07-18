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
import csv
import difflib
import html
import logging
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kunit_oracle import INIT_REACHED, TS_PREFIX_RE  # noqa: E402 — see module doc

LOG = REPO / "tmp" / "render_boot_log.log"
BOOT_HISTORY_CSV = REPO / "docs" / "status" / "boot-history.csv"
BOOT_HISTORY_DIR = REPO / "docs" / "status" / "boot-logs"
BROWSE_DIR = REPO / "tmp" / "boot-log-browse"

OK_RE = re.compile(rf"^{TS_PREFIX_RE}ok \d+ ")
NOTOK_RE = re.compile(rf"^{TS_PREFIX_RE}\s*not ok ")
TOTALS_RE = re.compile(rf"^{TS_PREFIX_RE}# Totals:")
MILESTONE = INIT_REACHED
PANIC_RE = re.compile(r"Kernel panic")

# Split a rendered line into its (elapsed-time prefix, rest) for display —
# distinct from TS_PREFIX_RE's use above (which only needs "is it there")
# because here the timestamp text itself needs to survive into the HTML
# as its own styled column. Old archived logs with no prefix at all still
# render fine: LEADING_TS_RE just doesn't match and ts_part is "".
LEADING_TS_RE = re.compile(r"^(\d{5}\.\d{3}) (.*)$", re.S)

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


def render_row(line_no: int, line: str) -> str:
    """One <tr> for a single log line, with the elapsed-time prefix (if
    present — old pre-timestamp archived logs won't have one) split into
    its own muted-color <td> ahead of the actual content, rather than left
    as inert text baked into the same cell. classify() still runs against
    the FULL original line (prefix included) so ok/not-ok/milestone/panic
    detection is unaffected by this purely-visual split."""
    cls = classify(line)
    cls_attr = f' class="{cls}"' if cls else ""
    m = LEADING_TS_RE.match(line)
    if m:
        ts_html = f'<td class="elapsed">{html.escape(m.group(1))}</td>'
        text_html = html.escape(m.group(2)) or "&nbsp;"
    else:
        ts_html = '<td class="elapsed"></td>'
        text_html = html.escape(line) or "&nbsp;"
    return (f'<tr{cls_attr}><td class="ln">{line_no}</td>{ts_html}'
            f'<td class="tx">{text_html}</td></tr>')


def render(log_path: Path) -> str:
    text = log_path.read_text(errors="replace")
    lines = text.splitlines()

    n_ok = sum(1 for l in lines if OK_RE.match(l))
    n_notok = sum(1 for l in lines if NOTOK_RE.match(l))
    reached_init = MILESTONE in text
    verdict_ok = n_ok > 0 and n_notok == 0

    rows = [render_row(i, line) for i, line in enumerate(lines, 1)]

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
td.elapsed {{
  color: var(--text-dim); text-align: right; user-select: none; width: 4.6rem;
  font-variant-numeric: tabular-nums; opacity: 0.7;
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


def render_history() -> str:
    """One page, every archived boot as a collapsible <details> section —
    timestamped, newest first, each embedding its own full log rendered
    the same way render() does for a single log. One shareable URL shows
    the whole history instead of a separate artifact per boot."""
    if not BOOT_HISTORY_CSV.exists():
        rows = []
    else:
        with open(BOOT_HISTORY_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
    rows.reverse()  # newest first

    sections = []
    for i, row in enumerate(rows):
        log_path = REPO / row["log_file"]
        if not log_path.exists():
            continue
        text = log_path.read_text(errors="replace")
        lines = text.splitlines()
        body_rows = [render_row(ln, line) for ln, line in enumerate(lines, 1)]
        ok = int(row["ok"])
        notok = int(row["not_ok"])
        init_ok = row["init_reached"] == "1"
        verdict_cls = "pass" if (ok > 0 and notok == 0) else "fail"
        init_cls = "pass" if init_ok else "warn"
        sections.append(f"""
<details{' open' if i == 0 else ''}>
  <summary>
    <span class="ts">{html.escape(row['timestamp'])}</span>
    <span class="src">{html.escape(row['run_id'])}</span>
    <span class="badge {verdict_cls}">{ok} ok / {notok} not ok</span>
    <span class="badge {init_cls}">{'INIT REACHED' if init_ok else 'no init'}</span>
  </summary>
  <div class="scroll-x">
  <table>
  <tbody>
  {"".join(body_rows)}
  </tbody>
  </table>
  </div>
</details>""")

    return f"""<title>linux-rs — boot log history</title>
<meta name="description" content="{len(rows)} archived QEMU riscv64 boot runs, newest first">
<style>
:root {{
  --bg: #faf8f4; --panel: #f3f0e9; --border: #ddd6c7;
  --text: #14120f; --text-dim: #8a8477;
  --amber: #b97a1f; --amber-bg: #f6e9d3;
  --red: #b23327; --red-bg: #fbe4e1;
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
  margin: 0; padding: 1.5rem; background: var(--bg); color: var(--text);
  font: 15px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}}
h1 {{ font-size: 1.1rem; margin: 0 0 0.3rem; }}
.subtitle {{ color: var(--text-dim); font-size: 0.85rem; margin: 0 0 1.5rem; }}
details {{
  border: 1px solid var(--border); border-radius: 8px; margin-bottom: 0.7rem;
  background: var(--panel); overflow: hidden;
}}
summary {{
  padding: 0.7rem 1rem; cursor: pointer; display: flex; align-items: center;
  gap: 0.8rem; flex-wrap: wrap; list-style: none;
}}
summary::-webkit-details-marker {{ display: none; }}
summary::before {{ content: "▸"; color: var(--text-dim); font-size: 0.8em; }}
details[open] summary::before {{ content: "▾"; }}
.ts {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
.src {{
  font-family: ui-monospace, 'SF Mono', 'Cascadia Code', monospace; font-size: 0.82rem;
  color: var(--text-dim);
}}
.badge {{
  font-size: 0.72rem; font-weight: 700; padding: 0.2rem 0.55rem; border-radius: 5px;
  text-transform: uppercase; letter-spacing: 0.02em; margin-left: auto;
}}
.badge.pass {{ background: var(--amber-bg); color: var(--amber); }}
.badge.fail, .badge.warn {{ background: var(--red-bg); color: var(--red); }}
.scroll-x {{ overflow-x: auto; border-top: 1px solid var(--border); }}
table {{
  border-collapse: collapse; width: 100%;
  font-family: ui-monospace, 'SF Mono', 'Cascadia Code', monospace; font-size: 0.8rem;
}}
td {{ padding: 0.06rem 0.9rem; white-space: pre; vertical-align: top; line-height: 1.4; }}
td.ln {{ color: var(--text-dim); text-align: right; width: 3.2rem; opacity: 0.5; }}
td.elapsed {{
  color: var(--text-dim); text-align: right; width: 4.4rem;
  font-variant-numeric: tabular-nums; opacity: 0.65;
}}
td.tx {{ border-left: 3px solid transparent; }}
tr.ok td.tx {{ background: var(--amber-bg); border-left-color: var(--amber); font-weight: 600; }}
tr.milestone td.tx {{ background: var(--amber-bg); border-left-color: var(--amber); font-weight: 700; }}
tr.notok td.tx, tr.panic td.tx {{ background: var(--red-bg); border-left-color: var(--red); font-weight: 700; }}
footer {{ color: var(--text-dim); font-size: 0.8rem; margin-top: 1.5rem; }}
</style>
<h1>linux-rs boot log history</h1>
<p class="subtitle">{len(rows)} archived runs, newest first &middot; from docs/status/boot-history.csv</p>
{"".join(sections)}
<footer>Generated by <code>scripts/render_boot_log.py --history</code>. Every log is an unmodified QEMU serial capture archived by <code>scripts/boot_qemu.py</code>.</footer>
"""


def load_history_rows():
    if not BOOT_HISTORY_CSV.exists():
        return []
    with open(BOOT_HISTORY_CSV, newline="") as f:
        return list(csv.DictReader(f))


def find_row(rows, ref: str):
    """Match by run_id (last occurrence, i.e. most recent boot with that
    id) or by exact timestamp — whichever the caller passed."""
    matches = [r for r in rows if r["run_id"] == ref or r["timestamp"] == ref]
    return matches[-1] if matches else None


def render_diff(row_a, row_b) -> str:
    """Unified diff between two archived boot logs — e.g. a stream-3
    c-baseline checkpoint vs a stream-2 rust-forward candidate, to see
    exactly what changed in the serial console output between them."""
    text_a = (REPO / row_a["log_file"]).read_text(errors="replace").splitlines()
    text_b = (REPO / row_b["log_file"]).read_text(errors="replace").splitlines()
    label_a = f"{row_a['run_id']} @ {row_a['timestamp']}"
    label_b = f"{row_b['run_id']} @ {row_b['timestamp']}"
    diff_lines = list(difflib.unified_diff(text_a, text_b, fromfile=label_a,
                                            tofile=label_b, lineterm=""))

    rows_html = []
    for line in diff_lines:
        cls = ""
        if line.startswith("+") and not line.startswith("+++"):
            cls = "add"
        elif line.startswith("-") and not line.startswith("---"):
            cls = "del"
        elif line.startswith("@@"):
            cls = "hunk"
        cls_attr = f' class="{cls}"' if cls else ""
        rows_html.append(f'<tr{cls_attr}><td class="tx">{html.escape(line) or "&nbsp;"}</td></tr>')

    identical = len(diff_lines) == 0
    return f"""<title>linux-rs — boot log diff — {html.escape(row_a['run_id'])} vs {html.escape(row_b['run_id'])}</title>
<meta name="description" content="Diff between two archived QEMU boot logs">
<style>
:root {{
  --bg: #faf8f4; --panel: #f3f0e9; --border: #ddd6c7;
  --text: #14120f; --text-dim: #8a8477;
  --amber: #b97a1f; --amber-bg: #f6e9d3;
  --green: #2e7d32; --green-bg: #e3f2e1;
  --red: #b23327; --red-bg: #fbe4e1;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14120f; --panel: #1c1a15; --border: #34301f;
    --text: #f2ede1; --text-dim: #8a8477;
    --amber: #d99a3d; --amber-bg: #362a12;
    --green: #6bc06f; --green-bg: #16261a;
    --red: #e2685c; --red-bg: #3a1712;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #14120f; --panel: #1c1a15; --border: #34301f;
  --text: #f2ede1; --text-dim: #8a8477;
  --amber: #d99a3d; --amber-bg: #362a12;
  --green: #6bc06f; --green-bg: #16261a;
  --red: #e2685c; --red-bg: #3a1712;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 1.5rem; background: var(--bg); color: var(--text);
  font: 15px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}}
h1 {{ font-size: 1.05rem; margin: 0 0 0.3rem; }}
.subtitle {{ color: var(--text-dim); font-size: 0.85rem; margin: 0 0 1.2rem; }}
.badge {{
  display: inline-block; font-size: 0.75rem; font-weight: 700; padding: 0.25rem 0.6rem;
  border-radius: 5px; text-transform: uppercase; letter-spacing: 0.02em;
  background: var(--amber-bg); color: var(--amber); margin-bottom: 1rem;
}}
.scroll-x {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }}
table {{
  border-collapse: collapse; width: 100%;
  font-family: ui-monospace, 'SF Mono', 'Cascadia Code', monospace; font-size: 0.83rem;
}}
td {{ padding: 0.06rem 0.9rem; white-space: pre; }}
td.tx {{ border-left: 3px solid transparent; }}
tr.add td.tx {{ background: var(--green-bg); border-left-color: var(--green); }}
tr.del td.tx {{ background: var(--red-bg); border-left-color: var(--red); }}
tr.hunk td.tx {{ background: var(--amber-bg); color: var(--amber); font-weight: 700; }}
footer {{ color: var(--text-dim); font-size: 0.8rem; margin-top: 1.2rem; }}
</style>
<h1>Boot log diff</h1>
<p class="subtitle"><b>{html.escape(row_a['run_id'])}</b> ({html.escape(row_a['timestamp'])}) vs
  <b>{html.escape(row_b['run_id'])}</b> ({html.escape(row_b['timestamp'])})</p>
{'<div class="badge">identical — no diff</div>' if identical else ''}
<div class="scroll-x">
<table><tbody>
{"".join(rows_html)}
</tbody></table>
</div>
<footer>Generated by <code>scripts/render_boot_log.py --diff</code>.</footer>
"""


def write_browse_dir():
    """One standalone HTML per archived boot under tmp/boot-log-browse/,
    plus an index — a genuine browsable directory alongside the single
    collapsible --history page (redundant, but a real directory of files
    is sometimes more useful than one big page)."""
    rows = load_history_rows()
    BROWSE_DIR.mkdir(parents=True, exist_ok=True)
    index_rows = []
    for row in reversed(rows):
        log_path = REPO / row["log_file"]
        if not log_path.exists():
            continue
        stamp = row["timestamp"].replace(":", "").replace("+", "_")
        out_name = f"{stamp}-{row['run_id']}.html"
        (BROWSE_DIR / out_name).write_text(render(log_path))
        ok, notok = row["ok"], row["not_ok"]
        init_ok = "INIT REACHED" if row["init_reached"] == "1" else "no init"
        index_rows.append(
            f'<li><a href="{out_name}">{html.escape(row["timestamp"])} — '
            f'{html.escape(row["run_id"])}</a> — {ok} ok / {notok} not ok, {init_ok}</li>'
        )
    (BROWSE_DIR / "index.html").write_text(
        "<title>linux-rs boot log directory</title>"
        "<style>body{font:15px system-ui;padding:1.5rem;max-width:60rem}"
        "li{margin:0.3rem 0}</style>"
        f"<h1>Boot log directory</h1><ul>{''.join(index_rows)}</ul>"
    )
    return BROWSE_DIR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(REPO / "tmp" / "qemu-boot.log"))
    ap.add_argument("--out", default=str(REPO / "tmp" / "boot-log-viewer.html"))
    ap.add_argument("--history", action="store_true",
                     help="render every archived boot from docs/status/boot-history.csv "
                          "as one collapsible timeline page instead of a single log")
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"),
                     help="unified-diff two archived boots by run_id or timestamp")
    ap.add_argument("--browse", action="store_true",
                     help="write one standalone HTML per archived boot + an index under "
                          "tmp/boot-log-browse/ (a real directory, alongside --history's "
                          "single collapsible page)")
    args = ap.parse_args()

    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(message)s",
        handlers=[logging.FileHandler(LOG, mode="a"), logging.StreamHandler(sys.stdout)],
    )

    out_path = Path(args.out)

    if args.diff:
        rows = load_history_rows()
        row_a = find_row(rows, args.diff[0])
        row_b = find_row(rows, args.diff[1])
        if not row_a or not row_b:
            logging.error("could not find boot(s) for %s (checked run_id and timestamp "
                          "against docs/status/boot-history.csv)", args.diff)
            return 1
        diff_out = REPO / "tmp" / f"boot-diff-{args.diff[0]}-vs-{args.diff[1]}.html"
        diff_out.write_text(render_diff(row_a, row_b))
        logging.info("wrote %s", diff_out)
        print(f"RENDER OK: {diff_out}")
        return 0

    if args.browse:
        out_dir = write_browse_dir()
        logging.info("wrote %s", out_dir / "index.html")
        print(f"RENDER OK: {out_dir / 'index.html'} (+ {len(list(out_dir.glob('*.html'))) - 1} logs)")
        return 0

    if args.history:
        out_path = Path(args.out) if args.out != str(REPO / "tmp" / "boot-log-viewer.html") \
            else REPO / "tmp" / "boot-log-history.html"
        out_path.write_text(render_history())
        logging.info("wrote %s", out_path)
        print(f"RENDER OK: {out_path}")
        return 0

    log_path = Path(args.log)
    if not log_path.exists():
        logging.error("no boot log at %s — run dev.py boot/check first", log_path)
        return 1

    out_path.write_text(render(log_path))
    logging.info("wrote %s", out_path)
    print(f"RENDER OK: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
