#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Render tmp/c2rust-sweep-chart.png — ok/error/timeout counts across
every scripts/sweep_c2rust_corpus.py run recorded in
rulesdb/patterns.db's c2rust_sweep_outcomes table, one point per
distinct run_at. The point of the whole sweep+fix-class-script pipeline
is "run it, fix the biggest failure class, re-run, watch the ok count
climb" — this is that trend as a real chart, not a one-off snapshot.

Same visual style as scripts/generate_dashboard.py's
render_progress_chart (same palette constants, same axis styling) so
this reads as part of the same project dashboard family rather than a
one-off.

Usage: render_sweep_chart.py
Output: tmp/c2rust-sweep-chart.png
"""
import datetime
import sqlite3
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "rulesdb" / "patterns.db"
OUT = REPO / "tmp" / "c2rust-sweep-chart.png"

SURFACE = "#fcfcfb"
INK = "#1c1b1a"
MUTED = "#52514e"
GRID = "#e1e0d9"
GREEN = "#008300"
RED = "#e34948"
YELLOW = "#eda100"


def style_axes(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=11, loc="left", pad=10)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8)


def fetch_series(conn):
    rows = conn.execute(
        "SELECT run_at, "
        "  SUM(CASE WHEN compile_outcome='ok' THEN 1 ELSE 0 END) AS ok, "
        "  SUM(CASE WHEN compile_outcome='error' THEN 1 ELSE 0 END) AS err, "
        "  SUM(CASE WHEN compile_outcome='timeout' THEN 1 ELSE 0 END) AS timeout, "
        "  fix_classes_rev "
        "FROM c2rust_sweep_outcomes GROUP BY run_at ORDER BY run_at"
    ).fetchall()
    return rows


def render(rows, out_path):
    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=SURFACE)
    fig.subplots_adjust(top=0.85, bottom=0.28, left=0.09, right=0.97)
    style_axes(
        ax,
        "c2rust corpus sweep: compile outcomes per run "
        "(after apply_c2rust_fix_classes.py, real rustc --emit=metadata check)",
    )
    if rows:
        labels = []
        for run_at, ok, err, timeout, fc_rev in rows:
            t = datetime.datetime.fromisoformat(run_at)
            labels.append(f"{fc_rev}\n{t.strftime('%m-%d %H:%M')}")
        ok_vals = [r[1] for r in rows]
        err_vals = [r[2] for r in rows]
        timeout_vals = [r[3] for r in rows]
        x = range(len(rows))
        ax.plot(x, ok_vals, "-o", color=GREEN, linewidth=2, markersize=5, label="ok")
        ax.plot(x, err_vals, "-o", color=RED, linewidth=2, markersize=5, label="error")
        if any(timeout_vals):
            ax.plot(x, timeout_vals, "-o", color=YELLOW, linewidth=2, markersize=5, label="timeout")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=7, color=MUTED)
        ax.annotate(str(ok_vals[-1]), (x[-1], ok_vals[-1]), textcoords="offset points",
                    xytext=(6, 4), color=GREEN, fontsize=9)
        ax.annotate(str(err_vals[-1]), (x[-1], err_vals[-1]), textcoords="offset points",
                    xytext=(6, -10), color=RED, fontsize=9)
        ax.legend(loc="center left", frameon=False, fontsize=8, labelcolor=MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    fig.savefig(out_path, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def main() -> int:
    if not DB.exists():
        print(f"no db at {DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(DB))
    rows = fetch_series(conn)
    conn.close()
    if not rows:
        print("no c2rust_sweep_outcomes rows yet — run sweep_c2rust_corpus.py first", file=sys.stderr)
        return 1
    render(rows, OUT)
    print(f"wrote {OUT} ({len(rows)} run(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
