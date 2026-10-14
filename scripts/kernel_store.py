#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""kernel_store — kernel trees as worktrees of the machine-wide Linux store (#118).

- Store: one full Linux repo shared by every project (all upstreams as remotes, awto work as
  branches `awto/linux-rs/<topic>-<base>`). Reached via the gitignored symlink `tmp/linux-store`;
  override with `--store PATH` or env LINUX_RS_STORE. Never `--depth` into it, never `git gc` in it.
- `setup --store PATH`   create/refresh the tmp/linux-store symlink (refuses a shallow repo)
- `survey [--sizes]`     every kernel tree in this repo: git dir, shallow, HEAD, dirty/untracked, busy
- `adopt TREE... [--branch B | --detach]`  re-point an existing tree onto the store IN PLACE:
  working tree, build output, uncommitted edits and index stay; only `.git` changes.
  Refuses: process cwd inside, merge/rebase/bisect in progress, staged changes.
- Log: tmp/logs/kernel_store.log
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import REPO, TMP, make_log, rel  # noqa: E402

log = make_log("kernel_store")
STORE_LINK = TMP / "linux-store"
MAINLINE = "origin/master"  # store remote origin = torvalds/linux
# status/rev-parse ~0.2 s warm, cold kernel-tree status ~10 s; fetch from the old shallow clone
# ~100 s CPU (rev-walk, #118). x1.25 rounded up; on expiry the command and limit are logged.
GIT_TIMEOUT, FETCH_TIMEOUT = 15, 125
# du -s of one kernel tree with build output ~20-60 s cold; per tree.
DU_TIMEOUT = 90
IN_PROGRESS = ("MERGE_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG", "CHERRY_PICK_HEAD",
               "REVERT_HEAD")


def store_path(arg: str | None) -> Path:
    return Path(arg or os.environ.get("LINUX_RS_STORE") or STORE_LINK)


def git(*args: str, cwd: Path, timeout: int = GIT_TIMEOUT, check: bool = True) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT git {' '.join(args)} in {rel(cwd)}: no result after {timeout} s (limit)")
        sys.exit(1)
    if check and r.returncode:
        log(f"git {' '.join(args)} in {rel(cwd)} -> {r.returncode}: {r.stderr.strip()}")
        sys.exit(1)
    return r.stdout.rstrip("\n")  # keep leading blanks: porcelain status columns


def busy_pids(tree: Path) -> list[str]:
    """PIDs (readable ones) whose cwd is inside tree."""
    t, out = str(tree.resolve()), []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cwd = os.readlink(p / "cwd")
        except OSError:
            continue
        if cwd == t or cwd.startswith(t + "/"):
            out.append(p.name)
    return out


def kernel_trees() -> list[Path]:
    """Top-level kernel checkouts in this repo plus every worktree they list."""
    seen: dict[str, Path] = {}
    for top in (REPO / "linux", REPO / "linux-riscv"):
        if not (top / ".git").exists():
            continue
        seen[str(top)] = top
        for line in git("worktree", "list", "--porcelain", cwd=top).splitlines():
            if line.startswith("worktree "):
                p = Path(line[9:])
                if str(p).startswith(str(REPO) + "/"):
                    seen[str(p)] = p
    for d in sorted((REPO / "linux-riscv-worktrees").glob("*/.git")):
        seen.setdefault(str(d.parent), d.parent)
    return sorted(seen.values())


def describe(tree: Path, store: Path, sizes: bool) -> dict:
    gd = Path(git("rev-parse", "--absolute-git-dir", cwd=tree))
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=tree))
    head = git("rev-parse", "HEAD", cwd=tree)
    st = git("status", "--porcelain", cwd=tree).splitlines()
    info = {
        "tree": rel(tree),
        "git_common_dir": "store" if common.resolve() == (store.resolve() / ".git") else rel(common),
        "shallow": git("rev-parse", "--is-shallow-repository", cwd=tree),
        "head": head[:12],
        "branch": git("branch", "--show-current", cwd=tree) or "(detached)",
        "staged": sum(1 for s in st if s[0] not in " ?"),
        "unstaged": sum(1 for s in st if s[1] not in " ?"),
        "untracked": sum(1 for s in st if s.startswith("??")),
        "in_progress": [n for n in IN_PROGRESS if (gd / n).exists()],
        "head_in_store": subprocess.run(["git", "cat-file", "-e", f"{head}^{{commit}}"], cwd=store,
                                        capture_output=True).returncode == 0,
        "not_in_remotes": int(git("rev-list", "--count", "HEAD", "--not", "--remotes", cwd=tree)),
        "busy": busy_pids(tree),
    }
    if sizes:
        try:
            du = subprocess.run(["du", "-s", "--block-size=1M", str(tree)], capture_output=True,
                                text=True, timeout=DU_TIMEOUT).stdout.split()
            info["size_mb"] = int(du[0]) if du else None
        except subprocess.TimeoutExpired:
            log(f"TIMEOUT du -s {rel(tree)} after {DU_TIMEOUT} s (limit)")
            info["size_mb"] = None
    return info


def survey(store: Path, sizes: bool) -> int:
    rows = [describe(t, store, sizes) for t in kernel_trees()]
    out = TMP / "kernel_store_survey.json"
    out.write_text(json.dumps(rows, indent=1) + "\n")
    for r in rows:
        log(" ".join(f"{k}={v}" for k, v in r.items()))
    log(f"{len(rows)} trees; JSON {rel(out)}")
    return 0


def base_of(rev: str, store: Path) -> str:
    """Mainline tag the series sits on, e.g. v7.2-rc5 (store tags come from origin)."""
    mb = git("merge-base", rev, MAINLINE, cwd=store)
    return git("describe", "--tags", "--abbrev=0", mb, cwd=store)


def adopt(tree: Path, store: Path, branch: str | None, detach: bool) -> int:
    tree = tree.resolve()
    st = store.resolve()
    if git("rev-parse", "--is-shallow-repository", cwd=st) != "false":
        sys.exit(f"{store}: not a full store")
    before = describe(tree, store, False)
    if before["git_common_dir"] == "store":
        log(f"{rel(tree)}: already on the store")
        return 0
    if before["busy"] or before["in_progress"] or before["staged"]:
        log(f"REFUSE {rel(tree)}: busy={before['busy']} in_progress={before['in_progress']} "
            f"staged={before['staged']}")
        return 1
    if (tree / ".git").is_dir():
        linked = [ln[9:] for ln in git("worktree", "list", "--porcelain", cwd=tree).splitlines()
                  if ln.startswith("worktree ") and Path(ln[9:]) != tree]
        if linked:
            log(f"REFUSE {rel(tree)}: own clone still has {len(linked)} linked worktrees; adopt those first")
            return 1
    head = git("rev-parse", "HEAD", cwd=tree)
    status_before = git("status", "--porcelain", cwd=tree)
    old_gd = Path(git("rev-parse", "--absolute-git-dir", cwd=tree))
    old_common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=tree))
    if not detach:
        if not branch:
            topic = before["branch"].removeprefix("agent-").removeprefix("linux-rs/")
            branch = f"awto/linux-rs/{topic}-{base_of(head, store) if before['head_in_store'] else 'tmp'}"
        if not before["head_in_store"]:
            src = f"refs/heads/{before['branch']}" if before["branch"] != "(detached)" else head
            git("fetch", "--no-tags", str(old_common), f"{src}:refs/heads/{branch}", cwd=st,
                timeout=FETCH_TIMEOUT)
        if branch.endswith("-tmp"):  # base unknown until the commits were in the store
            final = branch.removesuffix("-tmp") + "-" + base_of(head, store)
            git("branch", "-m", branch, final, cwd=st)
            branch = final
        elif not git("branch", "--list", branch, cwd=st):
            git("branch", branch, head, cwd=st)
        if git("rev-parse", branch, cwd=st) != head:
            sys.exit(f"store branch {branch} exists at a different commit; pass --branch")
    elif not before["head_in_store"]:
        sys.exit(f"{head[:12]} not in the store; adopt with a branch instead of --detach")
    # placeholder name becomes the store's worktree admin id
    ph = TMP / "store-adopt" / f"linux-rs-{tree.name}"
    ph.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "--no-checkout", *(["--detach", str(ph), head] if detach else [str(ph), branch]),
        cwd=st)
    new_gd = Path(git("rev-parse", "--absolute-git-dir", cwd=ph))
    shutil.copy2(old_gd / "index", new_gd / "index")  # keeps stat cache; no staged blobs (checked)
    dotgit = tree / ".git"
    if dotgit.is_dir():
        aside = ph.parent / f"{tree.name}.git-old-clone"
        dotgit.rename(aside)
        log(f"old clone git dir moved aside: {rel(aside)} (retire it once no tree points into it)")
    else:
        dotgit.unlink()
        # old clone's admin dir for this worktree: kept (HEAD/index/logs) but out of its worktree list
        (ph.parent / "old-admin").mkdir(exist_ok=True)
        shutil.move(str(old_gd), str(ph.parent / "old-admin" / old_gd.name))
    dotgit.write_text(f"gitdir: {new_gd}\n")
    (ph / ".git").unlink()
    ph.rmdir()
    git("worktree", "repair", str(tree), cwd=st)
    after_head = git("rev-parse", "HEAD", cwd=tree)
    status_after = git("status", "--porcelain", cwd=tree)
    ok = after_head == head and status_after == status_before
    log(f"{'OK' if ok else 'MISMATCH'} adopt {rel(tree)} -> store {branch or 'detached'} @ {head[:12]}"
        f" (status lines {len(status_before.splitlines())} -> {len(status_after.splitlines())})")
    return 0 if ok else 1


def setup(store: str) -> int:
    st = Path(store).expanduser().resolve()
    if git("rev-parse", "--is-shallow-repository", cwd=st) != "false" or not (st / "MAINTAINERS").exists():
        sys.exit(f"{st}: not a full (non-shallow) Linux checkout")
    if STORE_LINK.exists() and not STORE_LINK.is_symlink():
        sys.exit(f"{rel(STORE_LINK)} exists and is not a symlink")
    STORE_LINK.unlink(missing_ok=True)
    STORE_LINK.symlink_to(st)
    log(f"{STORE_LINK.relative_to(REPO)} -> store")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", help="Linux store path (default: env LINUX_RS_STORE, else tmp/linux-store)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="point tmp/linux-store at --store")
    p = sub.add_parser("survey")
    p.add_argument("--sizes", action="store_true", help="also du -s each tree (slow)")
    p = sub.add_parser("adopt")
    p.add_argument("trees", type=Path, nargs="+")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--branch", help="store branch (default awto/linux-rs/<topic>-<base>)")
    g.add_argument("--detach", action="store_true", help="detached HEAD (pristine tag checkouts)")
    a = ap.parse_args()
    if a.cmd == "setup":
        if not a.store:
            sys.exit("setup needs --store PATH")
        return setup(a.store)
    store = store_path(a.store)
    if not (store / ".git").exists():
        sys.exit(f"{store}: no Linux store; run `kernel_store.py --store PATH setup` (#118)")
    if a.cmd == "survey":
        return survey(store, a.sizes)
    if a.branch and len(a.trees) > 1:
        sys.exit("--branch names one tree's branch; adopt trees one at a time with it")
    rc = 0
    for t in a.trees:
        rc |= adopt(t if t.is_absolute() else REPO / t, store, a.branch, a.detach)
    return rc


if __name__ == "__main__":
    sys.exit(main())
