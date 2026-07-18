#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Auto-generate the status report: docs/status/status.png + docs/STATUS.md.

Run after each validated boot (dev.py check does this automatically).
Data sources: kernel worktree git log (TU timeline), tmp/qemu-boot.log
(KUnit results), rulesdb/rules/*.toml (rule tiers), scripts/readiness.py
(top candidates). History accumulates in docs/status/history.csv.

Chart style follows the dataviz method: light surface baked into the PNG
(GitHub dark mode safe), single-hue marks, direct labels as contrast
relief, tables in STATUS.md as the accessible view.
Log: tmp/report.log
"""
import csv
import datetime
import logging
import re
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TREE = REPO / "linux-riscv"
OUT = REPO / "docs" / "status"
LOG = REPO / "tmp" / "report.log"

# dataviz reference palette (light surface)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"
BLUE = "#2a78d6"
TIER_STEPS = {1: "#6da7ec", 2: "#2a78d6", 3: "#104281"}


def sh(cmd):
    return subprocess.run(cmd, text=True, capture_output=True, check=True).stdout


def tu_timeline():
    out = sh(["git", "-C", str(TREE), "log", "--reverse", "--diff-filter=A",
              "--date=iso-strict", "--format=C|%ad", "--name-only",
              "linux-rs/phase2-gcd", "--", "*_rs.rs"])
    times, cum, count = [], [], 0
    when = None
    for line in out.splitlines():
        if line.startswith("C|"):
            when = datetime.datetime.fromisoformat(line[2:])
        elif line.endswith("_rs.rs"):
            count += 1
            times.append(when)
            cum.append(count)
    return times, cum


IFDEF_CONFIG_RUST_RE = re.compile(
    r"#ifdef\s+CONFIG_RUST\b(.*?)(?:\n#else\b|\n#endif\b)", re.S)
CALL_RS_FN_RE = re.compile(r"\b(\w+)_rs\s*\(")


def boot_path_wiring_status():
    """Live-computed distinction between the two real integration patterns
    this project uses (docs/streams.md stream 2, "c2rust-boot-blocker"):

    - "in-place wired": a *.c file has a genuine `#ifdef CONFIG_RUST`
      block (exact token, not e.g. CONFIG_RUST_INLINE_HELPERS) whose body
      calls a `*_rs`-suffixed function — the original C function becomes a
      thin wrapper into Rust, kept alive in the `#else` arm. This is real
      code executing at a live, pre-existing C call site (see
      docs/hybrid-boot-milestone-2026-07-18.md and the *-8250-trigger
      companion doc for the two known examples).
    - "whole-file lib/ swap": the Makefile points straight at a `*_rs.rs`
      TU, no C wrapper needed (the whole TU IS the Rust file) — includes
      both files with no sibling *.c at all (drivers/8250_helpers_rs.rs,
      extracted out of 8250_port.c) and files with a sibling *.c that has
      no `#ifdef CONFIG_RUST` call-site wrapper (e.g. lib/bitmap.c: real
      CONFIG_RUST guards exist, but they're `#ifndef` — dropping the C
      function so the Rust object supplies the symbol directly, not a C
      call into a `_rs` function).

    Deliberately regex-based over the raw *.c text rather than a Makefile
    parse: Makefile conditionals are harder to parse correctly than the
    #ifdef/#ifndef text itself, and the wrapper-call shape is the actual
    thing "wired into a live call site" means. Re-derived from
    linux-riscv/'s current state every run — never hand-maintained.
    """
    wired_files = {}  # Path -> set of base function names (no _rs suffix)
    for f in TREE.rglob("*.c"):
        try:
            text = f.read_text(errors="replace")
        except (UnicodeDecodeError, OSError):
            continue
        if "#ifdef CONFIG_RUST" not in text:
            continue
        fns = set()
        for m in IFDEF_CONFIG_RUST_RE.finditer(text):
            fns.update(CALL_RS_FN_RE.findall(m.group(1)))
        if fns:
            wired_files[f] = fns

    wired_file_count = len(wired_files)
    wired_fn_count = sum(len(fns) for fns in wired_files.values())
    wired_detail = sorted(
        (str(f.relative_to(TREE)), sorted(fns)) for f, fns in wired_files.items())

    rs_files = sorted(TREE.glob("lib/**/*_rs.rs")) + sorted(TREE.glob("drivers/**/*_rs.rs"))
    whole_file_swaps = 0
    for rs in rs_files:
        stem = rs.name[: -len("_rs.rs")]
        c_file = rs.with_name(stem + ".c")
        if not c_file.exists():
            whole_file_swaps += 1
            continue
        try:
            text = c_file.read_text(errors="replace")
        except (UnicodeDecodeError, OSError):
            whole_file_swaps += 1
            continue
        has_wrapper_call = any(
            CALL_RS_FN_RE.search(m.group(1)) for m in IFDEF_CONFIG_RUST_RE.finditer(text))
        if not has_wrapper_call:
            whole_file_swaps += 1

    return {
        "wired_files": wired_file_count,
        "wired_fns": wired_fn_count,
        "wired_detail": wired_detail,
        "tus_total": len(rs_files),
        "whole_file_swaps": whole_file_swaps,
        "in_place_wired_tus": len(rs_files) - whole_file_swaps,
    }


def kunit_results():
    log = REPO / "tmp" / "qemu-boot.log"
    suites, vectors = [], 0
    if not log.exists():
        return suites, vectors
    txt = log.read_text(errors="replace")
    for m in re.finditer(r"^ok \d+ (\S+)$", txt, re.M):
        suites.append(m.group(1))
    for m in re.finditer(r"^# Totals: pass:(\d+)", txt, re.M):
        vectors += int(m.group(1))
    return suites, vectors


def suite_vectors():
    txt = (REPO / "tmp" / "qemu-boot.log").read_text(errors="replace")
    out = []
    pending = None
    for line in txt.splitlines():
        m = re.match(r"^# Totals: pass:(\d+)", line)
        if m:
            pending = int(m.group(1))
        m = re.match(r"^ok \d+ (\S+)$", line)
        if m:
            out.append((m.group(1), pending if pending is not None else 1))
            pending = None
    return out


def rules_by_tier():
    tiers = {1: 0, 2: 0, 3: 0}
    for f in (REPO / "rulesdb" / "rules").glob("*.toml"):
        m = re.search(r"^tier = (\d)", f.read_text(), re.M)
        if m:
            tiers[int(m.group(1))] += 1
    return tiers


def readiness_top(n=10):
    out = sh(["python3", str(REPO / "scripts" / "readiness.py")])
    rows = []
    for line in out.splitlines():
        m = re.search(r"(\S+\.c)\s+(\d+\.\d)%\s+(\d+)\s+(\d+)$",
                      line.replace("INFO ", ""))
        if m:
            rows.append((m.group(1), float(m.group(2))))
    return rows[:n]


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
    times, cum = tu_timeline()
    suites, vectors = kunit_results()
    sv = suite_vectors()
    tiers = rules_by_tier()
    ready = readiness_top()
    wiring = boot_path_wiring_status()
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    logging.info("boot-path wiring: %d functions across %d files wired in-place "
                 "(vs %d whole-file/partial-swap TUs, %d total)",
                 wiring["wired_fns"], wiring["wired_files"],
                 wiring["whole_file_swaps"], wiring["tus_total"])
    for path, fns in wiring["wired_detail"]:
        logging.info("  wired: %s -> %s", path, ", ".join(fns))

    # history
    hist = OUT / "history.csv"
    new = not hist.exists()
    with open(hist, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "tus", "suites", "vectors", "rules"])
        w.writerow([now, cum[-1] if cum else 0, len(suites), vectors,
                    sum(tiers.values())])

    fig, axs = plt.subplots(2, 2, figsize=(11, 7.5), facecolor=SURFACE)
    fig.subplots_adjust(hspace=0.55, wspace=0.35, top=0.88, bottom=0.08,
                        left=0.22, right=0.97)

    ax = axs[0][0]
    style_axes(ax, "Translated TUs (cumulative, kernel branch)")
    if times:
        ax.step(times, cum, where="post", color=BLUE, linewidth=2)
        ax.plot(times[-1], cum[-1], "o", color=BLUE, markersize=6)
        ax.annotate(f"{cum[-1]}", (times[-1], cum[-1]), textcoords="offset points",
                    xytext=(8, -2), color=INK, fontsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(axis="x", rotation=30)

    ax = axs[0][1]
    style_axes(ax, "KUnit vectors passing per suite (latest boot)")
    if sv:
        names = [s for s, _ in sv][::-1]
        vals = [v for _, v in sv][::-1]
        ax.barh(names, vals, color=BLUE, height=0.55)
        for i, v in enumerate(vals):
            ax.annotate(str(v), (v, i), textcoords="offset points",
                        xytext=(4, -3), color=MUTED, fontsize=8)
    ax.grid(axis="x", color=GRID, linewidth=0.8)

    ax = axs[1][0]
    style_axes(ax, "Translation readiness — top candidates (%)")
    if ready:
        names = [r[0].split("/")[-1] for r in ready][::-1]
        vals = [r[1] for r in ready][::-1]
        ax.barh(names, vals, color=BLUE, height=0.55)
        ax.set_xlim(0, 100)
        for i, v in enumerate(vals):
            ax.annotate(f"{v:.0f}%", (v, i), textcoords="offset points",
                        xytext=(4, -3), color=MUTED, fontsize=8)
    ax.grid(axis="x", color=GRID, linewidth=0.8)

    ax = axs[1][1]
    style_axes(ax, "Rules by tier (1 mechanical → 3 context-gated)")
    labels = [f"tier {t}" for t in tiers]
    vals = list(tiers.values())
    ax.bar(labels, vals, color=[TIER_STEPS[t] for t in tiers], width=0.5)
    for i, v in enumerate(vals):
        ax.annotate(str(v), (i, v), textcoords="offset points", xytext=(0, 4),
                    ha="center", color=INK, fontsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)

    fig.suptitle(f"linux-rs status — {now}", color=INK, fontsize=13, x=0.02,
                 ha="left")
    fig.savefig(OUT / "status.png", dpi=160, facecolor=SURFACE)
    logging.info("wrote %s", OUT / "status.png")

    md = [
        f"# Status — {now}", "",
        "![status](status/status.png)", "",
        f"- Translated TUs: **{cum[-1] if cum else 0}**   ·   KUnit: "
        f"**{len(suites)} suites, {vectors} vectors** green   ·   Rules: "
        f"**{sum(tiers.values())}** (t1 {tiers[1]} / t2 {tiers[2]} / t3 {tiers[3]})",
        f"- Wired into live boot path: **{wiring['wired_fns']} functions across "
        f"{wiring['wired_files']} file(s)** (vs. **{wiring['tus_total']}** total TUs "
        f"landed) — see [docs/streams.md](streams.md)'s stream 2 "
        "(\"c2rust-boot-blocker\") for why this is the harder, more important "
        "milestone: a standalone `lib/` swap compiling clean is not the same "
        "as real Rust executing at a live, pre-existing C call site.",
        "",
        "## Boot-path integration patterns (live-derived, not hand-maintained)",
        "",
        "| pattern | count | detection |", "|---|---|---|",
        f"| In-place wired (`#ifdef CONFIG_RUST` C wrapper calls a `*_rs` fn) "
        f"| **{wiring['wired_fns']} fns / {wiring['wired_files']} file(s)** "
        "| regex scan of `linux-riscv/**/*.c` for `#ifdef CONFIG_RUST` blocks "
        "calling a `*_rs(...)` function |",
        f"| Whole-file `lib/` swap (Makefile points straight at `*_rs.rs`, "
        f"no call-site wrapper) | **{wiring['whole_file_swaps']}** "
        "| `*_rs.rs` TUs whose sibling `.c` (if any) has no `#ifdef CONFIG_RUST` "
        "wrapper calling into it |",
        *(["", "<details><summary>Wired functions (detail)</summary>", "",
           "| file | functions |", "|---|---|",
           *[f"| `{path}` | {', '.join(fns)} |" for path, fns in wiring["wired_detail"]],
           "", "</details>"] if wiring["wired_detail"] else []),
        "", "## KUnit (latest boot)", "", "| suite | vectors |", "|---|---|",
        *[f"| {s} | {v} |" for s, v in sv],
        "", "## Next candidates by readiness", "", "| TU | readiness |", "|---|---|",
        *[f"| {r[0]} | {r[1]:.1f}% |" for r in ready],
        "", "_Auto-generated by `scripts/report.py` (via `dev.py check`); "
        "history in [status/history.csv](status/history.csv)._",
    ]
    (REPO / "docs" / "STATUS.md").write_text("\n".join(md) + "\n")
    logging.info("wrote docs/STATUS.md")
    print(f"REPORT OK: {cum[-1] if cum else 0} TUs, {len(suites)} suites, "
          f"{vectors} vectors, {sum(tiers.values())} rules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
