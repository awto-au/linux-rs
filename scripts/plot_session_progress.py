#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Plot the c2rust-transpiler-fork track's real progress for a session
report (currently: 2026-07-17/18's crash-elimination + real-compile-check
work). Companion to scripts/report.py (which covers the hand-translation
track's TU/KUnit/rules status); this one is c2rust-track-specific and not
part of the routine dev.py check loop — run it manually when a session's
c2rust numbers are worth capturing as a durable chart.

Data sources (all queried live, nothing hand-entered):
  - rulesdb/patterns.db: c2rust_attempts (outcome counts per run_at) for
    the transpile-clean time series.
  - tmp/c2rust-output-compile-report.md's "Results: {...}" line for the
    latest real-rustc-compile-check ok/error counts (that script,
    check_c2rust_output_compiles.py, is the source of truth; this just
    reads its output rather than re-running the expensive check).
  - gh issue list --repo awtoau/c2rust for the open bug classes'
    affected-file counts (parsed from each issue title's "N/228" tag).

Chart style matches scripts/report.py: light surface baked into the PNG
(GitHub dark mode safe), single-hue-per-series marks, direct labels.

Usage: plot_session_progress.py
Output: docs/status/c2rust-session-progress.png,
        docs/status/c2rust-issue-impact.png (if gh is available)
Log: tmp/plot_session_progress.log
"""
import json
import logging
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "rulesdb" / "patterns.db"
COMPILE_REPORT = REPO / "tmp" / "c2rust-output-compile-report.md"
OUT = REPO / "docs" / "status"
LOG = REPO / "tmp" / "plot_session_progress.log"

# dataviz reference palette (light surface) — matches scripts/report.py
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"
BLUE = "#2a78d6"
LIGHT_BLUE = "#6da7ec"
AMBER = "#c9822a"
GREEN = "#3c8a5c"


def clean_outcome_timeline():
    """Transpile-outcome counts per run_at, from c2rust_attempts. Real,
    unedited history: 24 clean/51 crash at the day's first run, through
    228 clean/0 crash after the crash-elimination commits, then 226 clean
    once the corpus moved under the kernel resync (a9b7a8dc1 -> 3bf2aca5b
    c2rust revs, 0eff989556b6 -> 5c1e05432402 corpus revs)."""
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT run_at, c2rust_rev, outcome, COUNT(*) FROM c2rust_attempts "
        "GROUP BY run_at, outcome ORDER BY run_at"
    ).fetchall()
    conn.close()
    runs = {}
    for run_at, rev, outcome, n in rows:
        runs.setdefault(run_at, {"rev": rev, "clean": 0, "crash": 0, "dropped_decls": 0})
        runs[run_at][outcome] = n
    return runs


def real_compile_check_result():
    """Latest ok/error split from check_c2rust_output_compiles.py's report."""
    if not COMPILE_REPORT.exists():
        return None
    txt = COMPILE_REPORT.read_text()
    m = re.search(r"Results: (\{.*\})", txt)
    if not m:
        return None
    counts = json.loads(m.group(1).replace("'", '"'))
    checked_m = re.search(r"Checked: (\d+) files", txt)
    return {
        "ok": counts.get("ok", 0),
        "error": counts.get("error", 0),
        "checked": int(checked_m.group(1)) if checked_m else sum(counts.values()),
    }


def open_issue_impact():
    """Open c2rust bug-class issues (#9-#12) with their affected-file
    counts, parsed from each issue title's 'N/228' impact tag. Falls back
    to [] (skip the panel) if gh isn't available — this is best-effort
    supplementary data, not the primary series."""
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--repo", "awtoau/c2rust", "--state", "open",
             "--limit", "30", "--json", "number,title"],
            text=True, capture_output=True, check=True, timeout=30,
        ).stdout
    except Exception as e:
        logging.warning("gh issue list failed (skipping issue-impact panel): %s", e)
        return []
    issues = json.loads(out)
    results = []
    for issue in issues:
        n = issue["number"]
        if n < 8:
            continue  # pre-existing transpile-crash issues, not compile-check bug classes
        m = re.search(r"(\d+)/228", issue["title"])
        if m:
            results.append((n, int(m.group(1)), issue["title"]))
    return sorted(results, key=lambda r: r[0])


def style_axes(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=11, loc="left", pad=10)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )

    if not DB.exists():
        logging.error("rulesdb/patterns.db not found — run build_db.py first")
        return 1

    runs = clean_outcome_timeline()
    compile_result = real_compile_check_result()
    issues = open_issue_impact()

    if not runs:
        logging.error("no rows in c2rust_attempts — run dev.py c2rust-baseline first")
        return 1

    run_ats = sorted(runs.keys())
    clean_series = [runs[r]["clean"] for r in run_ats]
    crash_series = [runs[r]["crash"] for r in run_ats]
    # short x labels: HH:MM, plus the c2rust rev when it changes
    labels = []
    prev_rev = None
    for r in run_ats:
        hhmm = r[11:16]
        rev = runs[r]["rev"][:7]
        labels.append(hhmm if rev == prev_rev else f"{hhmm}\n{rev}")
        prev_rev = rev

    fig, axs = plt.subplots(1, 2, figsize=(12, 5), facecolor=SURFACE)
    fig.subplots_adjust(wspace=0.3, top=0.85, bottom=0.16, left=0.08, right=0.97)

    # Panel 1: clean vs crash outcome over the day's runs
    ax = axs[0]
    style_axes(ax, "c2rust transpile outcome per baseline run (2026-07-17/18)")
    x = range(len(run_ats))
    ax.plot(x, clean_series, "-o", color=BLUE, linewidth=2, markersize=5, label="clean")
    ax.plot(x, crash_series, "-o", color=AMBER, linewidth=2, markersize=5, label="crash")
    ax.annotate(f"{clean_series[0]}", (0, clean_series[0]), textcoords="offset points",
                xytext=(-4, 10), color=INK, fontsize=9, ha="right")
    ax.annotate(f"{clean_series[-1]}", (len(x) - 1, clean_series[-1]),
                textcoords="offset points", xytext=(6, 4), color=INK, fontsize=9)
    ax.annotate(f"{crash_series[0]}", (0, crash_series[0]), textcoords="offset points",
                xytext=(-4, -12), color=AMBER, fontsize=9, ha="right")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=6.5, color=MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.legend(frameon=False, fontsize=8, labelcolor=MUTED, loc="center right")
    ax.set_ylabel("files (of 552-file corpus)", color=MUTED, fontsize=8)

    # Panel 2: transpile-clean vs REAL rustc-compile-clean, latest state
    ax = axs[1]
    style_axes(ax, "\"clean\" transpile != compiles: latest corpus")
    last = runs[run_ats[-1]]
    cats = ["transpile\nclean", "+ real rustc\ncompile-clean"]
    vals = [last["clean"], compile_result["ok"] if compile_result else 0]
    colors = [LIGHT_BLUE, GREEN]
    bars = ax.bar(cats, vals, color=colors, width=0.5)
    for b, v in zip(bars, vals):
        ax.annotate(str(v), (b.get_x() + b.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 4), ha="center",
                    color=INK, fontsize=10)
    if compile_result:
        ax.annotate(
            f"{compile_result['ok']}/{compile_result['checked']} of "
            f"transpile-clean files\nsurvive --emit=metadata against\n"
            f"the real kernel target",
            (1, vals[1]), textcoords="offset points", xytext=(-95, 35),
            color=MUTED, fontsize=7.5,
        )
    ax.grid(axis="y", color=GRID, linewidth=0.8)

    fig.suptitle("linux-rs c2rust-transpiler-fork track — session progress",
                 color=INK, fontsize=13, x=0.02, ha="left")
    out_path = OUT / "c2rust-session-progress.png"
    fig.savefig(out_path, dpi=160, facecolor=SURFACE)
    logging.info("wrote %s", out_path)

    if issues:
        fig2, ax2 = plt.subplots(figsize=(9, 4.5), facecolor=SURFACE)
        fig2.subplots_adjust(top=0.85, bottom=0.15, left=0.12, right=0.95)
        style_axes(ax2, "Open c2rust real-compile bug classes, by files affected (of 228)")
        nums = [f"#{n}" for n, _, _ in issues][::-1]
        counts = [c for _, c, _ in issues][::-1]
        bars = ax2.barh(nums, counts, color=BLUE, height=0.55)
        for i, v in enumerate(counts):
            ax2.annotate(str(v), (v, i), textcoords="offset points",
                         xytext=(4, -3), color=MUTED, fontsize=8)
        ax2.grid(axis="x", color=GRID, linewidth=0.8)
        out2 = OUT / "c2rust-issue-impact.png"
        fig2.savefig(out2, dpi=160, facecolor=SURFACE)
        logging.info("wrote %s", out2)
    else:
        logging.warning("no open issues with 'N/228' tags found — skipped issue-impact panel")

    print(f"PLOT OK: {len(run_ats)} runs, clean {clean_series[0]}->{clean_series[-1]}, "
          f"compile-clean {compile_result['ok'] if compile_result else '?'}"
          f"/{compile_result['checked'] if compile_result else '?'} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
