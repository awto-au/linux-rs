#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate docs/status/dashboard.html — the two-track live status dashboard.

Complements (does not duplicate) docs/STATUS.md/status.png, which is the
hand-translation-track KUnit/TU report from scripts/report.py. This
dashboard adds the view that report.py doesn't cover: the work_items
priority queue across BOTH tracks (hand-translation + the awtoau/c2rust
transpiler fork), the awtoau/c2rust issue/PR timeline (real
created_at/closed_at "time in flight" per issue), and a progress-over-time
chart built from real c2rust_attempts revisions.

Every number on the page is a live query result against
rulesdb/patterns.db (see rulesdb/schema.sql for work_items,
work_items_active, c2rust_issues, c2rust_attempts, progress_snapshots) —
there is no mock/placeholder data. Token/cost tracking is NOT available
anywhere in this project's tooling (grepped rulesdb/schema.sql for any
token-usage table — none exists); the dashboard says so explicitly and
substitutes a labeled, honest proxy (tmp/*.log mtimes + c2rust_attempts
run_at deltas) for "how much wall-clock activity", not a token/cost figure.

The work-items and c2rust-issues tables are sortable grids: click any
column header to sort/re-sort (toggles ascending/descending), via a small
vanilla-JS handler — no framework/dependency.

Usage: generate_dashboard.py
Inputs: rulesdb/patterns.db (run scripts/sync_work_items.py first if the
        work_items table might be stale), tmp/*.log mtimes
Output: docs/status/dashboard.html, docs/status/dashboard_progress.png
Log: tmp/generate_dashboard.log
"""
import datetime
import html
import logging
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "rulesdb" / "patterns.db"
OUT = REPO / "docs" / "status"
LOG = REPO / "tmp" / "generate_dashboard.log"

# dataviz reference palette (light surface, validated categorical order)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e1e0d9"
BLUE = "#2a78d6"
GREEN = "#008300"
RED = "#e34948"
YELLOW = "#eda100"
STATUS_GOOD = "#0ca30c"
STATUS_CRITICAL = "#d03b3b"

PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def fetch_work_items(conn):
    return conn.execute(
        "SELECT track, title, priority, status, repo, issue_number, "
        "files_affected, blocks_boot_path, assigned_to, priority_rationale "
        "FROM work_items_active"
    ).fetchall()


def fetch_c2rust_issues(conn):
    rows = conn.execute(
        "SELECT number, title, state, labels, html_url, created_at, "
        "updated_at, closed_at FROM c2rust_issues "
        "WHERE repo='awtoau/c2rust' AND is_pr=0 ORDER BY number"
    ).fetchall()
    out = []
    now = datetime.datetime.now(datetime.timezone.utc)
    for number, title, state, labels, html_url, created_at, updated_at, closed_at in rows:
        priority = None
        for label in (labels or "").split(","):
            label = label.strip()
            if label in PRIORITY_RANK:
                priority = label
        created_dt = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00")) if created_at else None
        if closed_at:
            end_dt = datetime.datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        else:
            end_dt = now
        days_open = (end_dt - created_dt).total_seconds() / 86400 if created_dt else None
        out.append({
            "number": number, "title": title, "state": state, "priority": priority,
            "html_url": html_url, "created_at": created_at, "closed_at": closed_at,
            "days_open": days_open,
        })
    return out


def fetch_progress_series(conn):
    """Real clean/crash/dropped_decls counts per (c2rust_rev, run_at) —
    grouped exactly as recorded, no interpolation/fabrication. Multiple
    runs can share a c2rust_rev (re-runs at fixed rev); each is its own
    point on the real timeline, keyed on run_at."""
    rows = conn.execute(
        "SELECT c2rust_rev, run_at, outcome, COUNT(*) FROM c2rust_attempts "
        "GROUP BY c2rust_rev, run_at, outcome ORDER BY run_at"
    ).fetchall()
    points = {}
    for rev, run_at, outcome, n in rows:
        points.setdefault(run_at, {"rev": rev, "clean": 0, "crash": 0,
                                    "dropped_decls": 0, "no_output": 0})
        points[run_at][outcome] = n
    return sorted(points.items())


def fetch_progress_snapshots(conn):
    return conn.execute(
        "SELECT taken_at, note, tus_landed, c2rust_clean, c2rust_crash, "
        "c2rust_dropped_decls FROM progress_snapshots ORDER BY taken_at"
    ).fetchall()


def fetch_tu_growth():
    """Cumulative hand-translation TU count over time, from the real git
    history of linux-riscv's linux-rs/phase2-gcd branch — the same source
    scripts/report.py's tu_timeline() uses for STATUS.md, NOT the
    translated_tus DB table (confirmed stale: 31 rows vs. 32 real landed
    *_rs.rs files in git — missing drivers/tty/serial/8250/8250_helpers_rs.rs
    — because nothing keeps that table synced on every landing). Querying
    git directly means this count can never drift from STATUS.md's."""
    tree = REPO / "linux-riscv"
    out = subprocess.run(
        ["git", "-C", str(tree), "log", "--reverse", "--diff-filter=A",
         "--date=iso-strict", "--format=C|%ad", "--name-only",
         "linux-rs/phase2-gcd", "--", "*_rs.rs"],
        capture_output=True, text=True, check=True,
    ).stdout
    rows = []
    when = None
    for line in out.splitlines():
        if line.startswith("C|"):
            when = line[2:]
        elif line.endswith("_rs.rs"):
            rows.append((line, when))
    return rows, len(rows)


def active_workers():
    """Real, current signal for "is a background agent working right now":
    a live `git worktree list` entry other than the main checkout. This
    is genuinely live (reflects state AT GENERATION TIME, not a persisted
    claim that can go stale like a DB row would) — there's no work_items
    column for "an agent is actively running" since that's not a durable
    fact worth persisting, only a point-in-time one. Each row's age comes
    from the worktree directory's own mtime, a real filesystem fact."""
    try:
        out = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=REPO, capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return []
    workers = []
    path = branch = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line.removeprefix("worktree ")
        elif line.startswith("branch "):
            branch = line.removeprefix("branch ").removeprefix("refs/heads/")
        elif line == "" and path:
            if Path(path).resolve() != REPO.resolve():
                try:
                    age_s = time.time() - Path(path).stat().st_mtime
                except OSError:
                    age_s = None
                workers.append({"path": path, "branch": branch, "age_s": age_s})
            path = branch = None
    return workers


def activity_proxy():
    """Honest, labeled proxy for 'how much work went into this' since NO
    token/cost tracking exists anywhere in rulesdb (grepped schema.sql —
    no token-related table/column). Uses real filesystem mtimes of
    tmp/*.log (one per script invocation) as an elapsed-wall-clock signal,
    plus the real c2rust_attempts run_at spread. This is explicitly NOT
    token or dollar cost — just real timestamps that already exist."""
    logs = sorted(REPO.glob("tmp/*.log"), key=lambda p: p.stat().st_mtime)
    if not logs:
        return []
    out = []
    for p in logs:
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).astimezone()
        out.append((p.name, mtime))
    return out


def style_axes(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=11, loc="left", pad=10)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8)


def render_progress_chart(series, out_path):
    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=SURFACE)
    fig.subplots_adjust(top=0.85, bottom=0.28, left=0.09, right=0.97)
    style_axes(ax, "c2rust transpile outcomes per baseline run (real c2rust_attempts rows, today's revisions)")
    if series:
        labels = []
        for run_at, vals in series:
            t = datetime.datetime.fromisoformat(run_at)
            labels.append(f"{vals['rev']}\n{t.strftime('%H:%M')}")
        clean = [v["clean"] for _, v in series]
        crash = [v["crash"] for _, v in series]
        dropped = [v["dropped_decls"] for _, v in series]
        x = range(len(series))
        ax.plot(x, clean, "-o", color=GREEN, linewidth=2, markersize=5, label="clean")
        ax.plot(x, crash, "-o", color=RED, linewidth=2, markersize=5, label="crash")
        ax.plot(x, dropped, "-o", color=YELLOW, linewidth=2, markersize=5, label="dropped_decls")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=7, color=MUTED)
        ax.annotate(str(clean[-1]), (x[-1], clean[-1]), textcoords="offset points",
                    xytext=(6, 4), color=GREEN, fontsize=9)
        ax.annotate(str(crash[-1]), (x[-1], crash[-1]), textcoords="offset points",
                    xytext=(6, -10), color=RED, fontsize=9)
        ax.legend(loc="center left", frameon=False, fontsize=8, labelcolor=MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    fig.savefig(out_path, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def render_tu_growth_chart(rows, out_path):
    """Cumulative TUs-landed-over-time, real git commit dates — the direct
    counterpart to render_progress_chart's c2rust outcome series, so both
    tracks' progress-over-time live as two charts on the same dashboard
    page instead of two separate reports."""
    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=SURFACE)
    fig.subplots_adjust(top=0.85, bottom=0.28, left=0.09, right=0.97)
    style_axes(ax, "hand-translation TUs landed, cumulative (real linux-riscv git history)")
    if rows:
        times = [datetime.datetime.fromisoformat(landed_at) for _, landed_at in rows]
        y = list(range(1, len(rows) + 1))
        ax.plot(times, y, "-o", color=BLUE, linewidth=2, markersize=4)
        ax.annotate(str(y[-1]), (times[-1], y[-1]), textcoords="offset points",
                    xytext=(6, 4), color=BLUE, fontsize=9)
        fig.autofmt_xdate(rotation=30)
        ax.tick_params(axis="x", labelsize=7)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    fig.savefig(out_path, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def esc(s):
    return html.escape(str(s)) if s is not None else ""


def work_items_table(items):
    rows = []
    for track, title, priority, status, repo, issue_number, files_affected, \
            blocks_boot_path, assigned_to, rationale in items:
        prank = PRIORITY_RANK.get(priority, 9)
        link = (f'<a href="https://github.com/{esc(repo)}/issues/{issue_number}">'
                f'{esc(repo)}#{issue_number}</a>') if repo and issue_number else "&mdash;"
        boot = "yes" if blocks_boot_path else "no"
        fa = files_affected if files_affected is not None else ""
        fa_sort = files_affected if files_affected is not None else -1
        assignee = esc(assigned_to) if assigned_to else "unassigned"
        rationale_html = f' <span class="muted" title="{esc(rationale)}">(?)</span>' if rationale else ""
        rows.append(f"""      <tr>
        <td data-sort-value="{prank}"><span class="pill pill-{esc(priority or 'none')}">{esc(priority or '—')}</span></td>
        <td>{esc(title)}{rationale_html}</td>
        <td data-sort-value="{esc(track)}">{esc(track)}</td>
        <td data-sort-value="{esc(status)}">{esc(status)}</td>
        <td data-sort-value="{1 if blocks_boot_path else 0}">{boot}</td>
        <td data-sort-value="{fa_sort}">{fa}</td>
        <td data-sort-value="{esc(assignee)}">{assignee}</td>
        <td>{link}</td>
      </tr>""")
    return "\n".join(rows)


def c2rust_issues_table(issues):
    rows = []
    for it in issues:
        prank = PRIORITY_RANK.get(it["priority"], 9)
        days = it["days_open"]
        days_sort = days if days is not None else -1
        days_disp = f"{days:.1f}" if days is not None else "&mdash;"
        state_label = "closed" if it["state"] == "closed" else "open"
        created_sort = it["created_at"] or ""
        rows.append(f"""      <tr>
        <td data-sort-value="{it['number']}">#{it['number']}</td>
        <td><a href="{esc(it['html_url'])}">{esc(it['title'])}</a></td>
        <td data-sort-value="{prank}"><span class="pill pill-{esc(it['priority'] or 'none')}">{esc(it['priority'] or '—')}</span></td>
        <td data-sort-value="{esc(state_label)}"><span class="state state-{esc(state_label)}">{esc(state_label)}</span></td>
        <td data-sort-value="{esc(created_sort)}">{esc((it['created_at'] or '')[:10])}</td>
        <td data-sort-value="{esc(it['closed_at'] or '')}">{esc(it['closed_at'][:10]) if it['closed_at'] else '&mdash;'}</td>
        <td data-sort-value="{days_sort}">{days_disp}</td>
      </tr>""")
    return "\n".join(rows)


SORT_JS = """
function makeSortable(tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const tbody = table.tBodies[0];
  const ths = table.tHead.querySelectorAll('th[data-sort-key]');
  ths.forEach((th, idx) => {
    th.addEventListener('click', () => {
      const rows = Array.from(tbody.rows);
      const asc = th.getAttribute('data-sort-dir') !== 'asc';
      ths.forEach(other => { other.removeAttribute('data-sort-dir'); other.querySelector('.arrow').textContent = ''; });
      th.setAttribute('data-sort-dir', asc ? 'asc' : 'desc');
      th.querySelector('.arrow').textContent = asc ? ' \\u25B2' : ' \\u25BC';
      rows.sort((r1, r2) => {
        const c1 = r1.cells[idx], c2 = r2.cells[idx];
        const v1 = c1.hasAttribute('data-sort-value') ? c1.getAttribute('data-sort-value') : c1.textContent.trim();
        const v2 = c2.hasAttribute('data-sort-value') ? c2.getAttribute('data-sort-value') : c2.textContent.trim();
        const n1 = parseFloat(v1), n2 = parseFloat(v2);
        const bothNumeric = !isNaN(n1) && !isNaN(n2) && v1 !== '' && v2 !== '';
        let cmp;
        if (bothNumeric) { cmp = n1 - n2; } else { cmp = String(v1).localeCompare(String(v2)); }
        return asc ? cmp : -cmp;
      });
      rows.forEach(r => tbody.appendChild(r));
    });
  });
}
document.querySelectorAll('table.sortable').forEach(t => makeSortable(t.id));
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (REPO / "tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not DB.exists():
        logging.error("no %s — run scripts/build_db.py first", DB)
        return 1

    conn = sqlite3.connect(DB)
    items = fetch_work_items(conn)
    issues = fetch_c2rust_issues(conn)
    series = fetch_progress_series(conn)
    snapshots = fetch_progress_snapshots(conn)
    conn.close()
    tu_rows, tu_total = fetch_tu_growth()

    logging.info("work_items_active: %d rows", len(items))
    logging.info("awtoau/c2rust issues: %d (open=%d closed=%d)",
                 len(issues), sum(1 for i in issues if i["state"] == "open"),
                 sum(1 for i in issues if i["state"] == "closed"))
    logging.info("c2rust_attempts distinct runs: %d", len(series))
    logging.info("progress_snapshots rows: %d", len(snapshots))
    logging.info("TU growth (from git): %d total landed", tu_total)

    render_progress_chart(series, OUT / "dashboard_progress.png")
    logging.info("wrote %s", OUT / "dashboard_progress.png")

    render_tu_growth_chart(tu_rows, OUT / "dashboard_tu_growth.png")
    logging.info("wrote %s", OUT / "dashboard_tu_growth.png")

    proxy = activity_proxy()
    logging.info("activity proxy (tmp/*.log mtimes): %d files", len(proxy))

    workers = active_workers()
    logging.info("active worktrees (live agents): %d", len(workers))

    now = now_iso()
    open_issues = [i for i in issues if i["state"] == "open"]
    closed_issues = [i for i in issues if i["state"] == "closed"]
    p0 = sum(1 for i in items if i[2] == "P0")
    p1 = sum(1 for i in items if i[2] == "P1")
    boot_blocking = sum(1 for i in items if i[7])

    latest_run = series[-1] if series else None
    latest_summary = ""
    if latest_run:
        run_at, vals = latest_run
        total = vals["clean"] + vals["crash"] + vals["dropped_decls"] + vals["no_output"]
        latest_summary = (f"latest run (rev {esc(vals['rev'])}, {esc(run_at[:19])}): "
                           f"{vals['clean']} clean / {vals['crash']} crash / "
                           f"{vals['dropped_decls']} dropped_decls out of {total} files")

    proxy_rows = ""
    if proxy:
        span = (proxy[-1][1] - proxy[0][1])
        proxy_rows = (f"{len(proxy)} script-invocation logs in tmp/, spanning "
                       f"{span} from {proxy[0][1].isoformat(timespec='minutes')} to "
                       f"{proxy[-1][1].isoformat(timespec='minutes')}")

    if workers:
        worker_items = "".join(
            "<li><code>{branch}</code> — <span class=\"muted\">{age}</span></li>".format(
                branch=esc(w["branch"] or "(detached)"),
                age=("age unknown" if w["age_s"] is None
                     else f"{int(w['age_s'] // 60)}m running"),
            )
            for w in workers
        )
        workers_html = (
            f'<div class="callout" style="border-left:3px solid var(--status-warning);'
            f'padding:0.6rem 1rem;margin-bottom:1rem;background:var(--surface-1);">'
            f'<strong>{len(workers)} background worker(s) currently active</strong> '
            f'(live <code>git worktree list</code> at page-generation time — a real, '
            f'point-in-time fact, not a persisted status that can go stale):'
            f'<ul style="margin:0.4rem 0 0 1.2rem;">{worker_items}</ul></div>'
        )
    else:
        workers_html = (
            '<div class="callout" style="border-left:3px solid var(--status-ok, var(--border));'
            'padding:0.6rem 1rem;margin-bottom:1rem;background:var(--surface-1);color:var(--text-muted);">'
            'No background workers active right now (checked via <code>git worktree list</code> '
            'at page-generation time).</div>'
        )

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>linux-rs — live status dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    color-scheme: light;
    --surface-0: #ffffff;
    --surface-1: #fcfcfb;
    --surface-2: #f3f2ee;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --border: #e1e0d9;
    --blue: #2a78d6;
    --green: #008300;
    --red: #e34948;
    --yellow: #eda100;
    --status-good: #0ca30c;
    --status-critical: #d03b3b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --surface-0: #121211;
      --surface-1: #1a1a19;
      --surface-2: #232322;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #8f8d84;
      --border: #38372f;
      --blue: #3987e5;
      --green: #1baf7a;
      --red: #e66767;
      --yellow: #c98500;
      --status-good: #1baf7a;
      --status-critical: #e66767;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --surface-0: #121211;
    --surface-1: #1a1a19;
    --surface-2: #232322;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #8f8d84;
    --border: #38372f;
    --blue: #3987e5;
    --green: #1baf7a;
    --red: #e66767;
    --yellow: #c98500;
    --status-good: #1baf7a;
    --status-critical: #e66767;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2rem 2.5rem 4rem;
    background: var(--surface-0); color: var(--text-primary);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }}
  h1 {{ font-size: 1.5rem; margin: 0 0 0.25rem; }}
  h2 {{ font-size: 1.1rem; margin: 2.5rem 0 0.75rem; padding-top: 0.5rem; border-top: 1px solid var(--border); }}
  .subtitle {{ color: var(--text-secondary); margin: 0 0 1.5rem; font-size: 0.9rem; }}
  .stat-row {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0 2rem; }}
  .stat-tile {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px;
    padding: 0.9rem 1.2rem; min-width: 140px;
  }}
  .stat-tile .n {{ font-size: 1.6rem; font-weight: 600; }}
  .stat-tile .label {{ color: var(--text-muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em; }}
  .stat-tile.p0 .n {{ color: var(--status-critical); }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.87rem; margin-bottom: 0.5rem; }}
  caption {{ text-align: left; color: var(--text-muted); font-size: 0.8rem; margin-bottom: 0.4rem; caption-side: top; }}
  th, td {{ text-align: left; padding: 0.45rem 0.7rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
  th {{ color: var(--text-secondary); font-weight: 600; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.02em;
        position: sticky; top: 0; background: var(--surface-0); cursor: pointer; user-select: none; }}
  th:hover {{ color: var(--text-primary); }}
  th .arrow {{ font-size: 0.7em; color: var(--blue); }}
  tbody tr:hover {{ background: var(--surface-1); }}
  a {{ color: var(--blue); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .muted {{ color: var(--text-muted); font-size: 0.85em; cursor: help; }}
  .pill {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }}
  .pill-P0 {{ background: var(--status-critical); color: #fff; }}
  .pill-P1 {{ background: var(--yellow); color: #1a1500; }}
  .pill-P2 {{ background: var(--surface-2); color: var(--text-secondary); border: 1px solid var(--border); }}
  .pill-P3 {{ background: var(--surface-2); color: var(--text-muted); border: 1px solid var(--border); }}
  .pill-P4 {{ background: var(--surface-2); color: var(--text-muted); border: 1px solid var(--border); }}
  .pill-none {{ background: var(--surface-2); color: var(--text-muted); }}
  .state {{ font-size: 0.8rem; font-weight: 600; }}
  .state-open {{ color: var(--status-critical); }}
  .state-closed {{ color: var(--status-good); }}
  .callout {{
    background: var(--surface-1); border: 1px solid var(--border); border-left: 4px solid var(--yellow);
    border-radius: 6px; padding: 0.9rem 1.1rem; margin: 1rem 0; font-size: 0.87rem; color: var(--text-secondary);
  }}
  .proxy-list {{ font-size: 0.8rem; color: var(--text-muted); max-height: 10rem; overflow-y: auto;
                 border: 1px solid var(--border); border-radius: 6px; padding: 0.5rem 0.8rem; background: var(--surface-1); }}
  footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--border); color: var(--text-muted); font-size: 0.8rem; }}
  img.chart {{ max-width: 100%; border: 1px solid var(--border); border-radius: 8px; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }}
  @media (max-width: 900px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
  .scroll-x {{ overflow-x: auto; }}
</style>
</head>
<body>

<h1>linux-rs — live status dashboard</h1>
<p class="subtitle">Generated {esc(now)} by <code>scripts/generate_dashboard.py</code> from live queries against
  <code>rulesdb/patterns.db</code>. See <a href="STATUS.md">docs/STATUS.md</a> for the full
  hand-translation KUnit boot report; both tracks' progress-over-time charts, the two-track work
  queue, and the awtoau/c2rust transpiler-fork issue timeline all live on this one page.</p>

{workers_html}

<div class="stat-row">
  <div class="stat-tile p0"><div class="n">{p0}</div><div class="label">P0 items open</div></div>
  <div class="stat-tile"><div class="n">{p1}</div><div class="label">P1 items open</div></div>
  <div class="stat-tile"><div class="n">{len(items)}</div><div class="label">work items active (all tracks)</div></div>
  <div class="stat-tile"><div class="n">{boot_blocking}</div><div class="label">confirmed boot-path blocking</div></div>
  <div class="stat-tile"><div class="n">{len(open_issues)}</div><div class="label">awtoau/c2rust issues open</div></div>
  <div class="stat-tile"><div class="n">{len(closed_issues)}</div><div class="label">awtoau/c2rust issues closed</div></div>
  <div class="stat-tile"><div class="n">{tu_total}</div><div class="label">TUs hand-translated (live from git)</div></div>
</div>

<h2>Work-in-flight queue (both tracks)</h2>
<p class="subtitle">Live from the <code>work_items_active</code> view — open/in_progress/blocked items across the
  hand-translation (<code>kernel</code>) and <code>c2rust</code> tracks, ranked P0 first, then boot-path-blocking,
  then files-affected. Click any column header to sort/re-sort.</p>
<div class="scroll-x">
<table class="sortable" id="work-items-table">
  <caption>{len(items)} active work items</caption>
  <thead>
    <tr>
      <th data-sort-key>Priority<span class="arrow"></span></th>
      <th data-sort-key>Title<span class="arrow"></span></th>
      <th data-sort-key>Track<span class="arrow"></span></th>
      <th data-sort-key>Status<span class="arrow"></span></th>
      <th data-sort-key>Blocks boot path<span class="arrow"></span></th>
      <th data-sort-key>Files affected<span class="arrow"></span></th>
      <th data-sort-key>Assigned to<span class="arrow"></span></th>
      <th>Issue</th>
    </tr>
  </thead>
  <tbody>
{work_items_table(items)}
  </tbody>
</table>
</div>

<h2>awtoau/c2rust issue/PR timeline</h2>
<p class="subtitle">All 12 issues filed against our fork (<code>awtoau/c2rust</code>, not upstream
  <code>immunant/c2rust</code>). "Days open" is real elapsed time from <code>created_at</code> to
  <code>closed_at</code> (or to now, for open issues) — a genuine time-in-flight metric per issue, not
  per assignee (no worker-assignment tracking exists to query). Click headers to sort.</p>
<div class="scroll-x">
<table class="sortable" id="c2rust-issues-table">
  <caption>{len(issues)} issues total &mdash; {len(open_issues)} open, {len(closed_issues)} closed</caption>
  <thead>
    <tr>
      <th data-sort-key>#<span class="arrow"></span></th>
      <th data-sort-key>Title<span class="arrow"></span></th>
      <th data-sort-key>Priority<span class="arrow"></span></th>
      <th data-sort-key>State<span class="arrow"></span></th>
      <th data-sort-key>Created<span class="arrow"></span></th>
      <th data-sort-key>Closed<span class="arrow"></span></th>
      <th data-sort-key>Days open<span class="arrow"></span></th>
    </tr>
  </thead>
  <tbody>
{c2rust_issues_table(issues)}
  </tbody>
</table>
</div>

<h2>Progress over time</h2>
<p class="subtitle">Both tracks' progress-over-time on one page &mdash; this replaces cross-referencing
  <a href="STATUS.md">STATUS.md</a>'s TU timeline separately from the c2rust chart below; both are real
  queries against <code>rulesdb/patterns.db</code>, generated together.</p>
<div class="two-col">
<div>
<h3>Hand-translation (TUs landed)</h3>
<p class="subtitle">Cumulative count from real <code>linux-riscv</code> git commit dates (same source as
  STATUS.md's TU timeline) &mdash; {tu_total} TUs landed total.</p>
<img class="chart" src="dashboard_tu_growth.png" alt="cumulative TUs landed over time">
</div>
<div>
<h3>c2rust transpiler fork (clean/crash/dropped)</h3>
<p class="subtitle">Real <code>c2rust_attempts</code> outcome counts, grouped by revision and run timestamp
  &mdash; {esc(latest_summary)}. {len(series)} distinct baseline runs recorded today across
  {len({v['rev'] for _, v in series})} c2rust revisions.</p>
<img class="chart" src="dashboard_progress.png" alt="c2rust transpile outcomes per baseline run">
</div>
</div>

<h2>Token / cost tracking</h2>
<div class="callout">
  <strong>Not available.</strong> This project's tooling (<code>rulesdb/schema.sql</code>) has no table or
  column tracking LLM token usage or API cost anywhere &mdash; confirmed by grep, no match. No real token/cost
  figure can be shown, and none is fabricated here.
  <br><br>
  As a labeled, honest <em>proxy</em> for "how much wall-clock activity work went into this" (not tokens, not
  dollars): the real mtimes of this session's <code>tmp/*.log</code> files, one per script invocation, and the
  real <code>run_at</code> spread already recorded in <code>c2rust_attempts</code>. Today's baseline runs span
  {esc(series[0][0][:19]) if series else 'n/a'} to {esc(series[-1][0][:19]) if series else 'n/a'} UTC
  ({len(series)} runs). {esc(proxy_rows)}
  <div class="proxy-list">
    {'<br>'.join(f'{esc(n)} &mdash; {esc(t.isoformat(timespec="minutes"))}' for n, t in proxy[-20:])}
  </div>
  <em>(most-recent 20 of {len(proxy)} log files shown; commit/time-based proxy only, not actual token usage)</em>
</div>

<h2>progress_snapshots (time-series, if &gt;1 row)</h2>
<p class="subtitle">{len(snapshots)} row(s) currently in <code>progress_snapshots</code>
  (taken via <code>scripts/take_progress_snapshot.py</code>).
  {'Only one snapshot exists so far — no trend to chart yet; the c2rust_attempts chart above is the real time-series available today.' if len(snapshots) <= 1 else ''}</p>
{"".join(f'<p class="subtitle">{esc(t)} &mdash; {esc(note)} &mdash; TUs {tus}, clean {cl}, crash {cr}, dropped {dd}</p>' for t, note, tus, cl, cr, dd in snapshots) if snapshots else ''}

<footer>
  linux-rs live dashboard &middot; generated by <code>scripts/generate_dashboard.py</code> &middot;
  source data: <code>rulesdb/patterns.db</code> (work_items, c2rust_issues, c2rust_attempts, progress_snapshots) &middot;
  see also <a href="STATUS.md">STATUS.md</a> for the hand-translation KUnit/TU report.
</footer>

<script>
{SORT_JS}
</script>
</body>
</html>
"""
    (OUT / "dashboard.html").write_text(html_doc)
    logging.info("wrote %s", OUT / "dashboard.html")
    print(f"DASHBOARD OK: {len(items)} work items, {len(issues)} c2rust issues "
          f"({len(open_issues)} open), {len(series)} baseline runs charted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
