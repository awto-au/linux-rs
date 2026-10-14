"""Shared repo-root resolution for scripts/ (standard: awto-dan#35).

Resolution order, first hit wins:
  1. $LINUX_RS_REPO                   explicit override (CI, odd checkout)
  2. git rev-parse --show-toplevel    authoritative, works from any subdir
  3. file-relative                    no git, no env

Usage (scripts/ is on sys.path when a script in it runs):
    from _repo import REPO, TMP, LOGS, log_path, make_log, rel
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ENV_OVERRIDE = "LINUX_RS_REPO"


def _resolve() -> Path:
    env = os.environ.get(ENV_OVERRIDE)
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return p
    here = Path(__file__).resolve().parent
    try:
        # local .git read, ~1 ms warm; 0.25 s covers a cold first exec.
        # On expiry/failure: file-relative fallback below.
        out = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=0.25)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return here.parent if here.name == "scripts" else here


REPO = _resolve()
TMP = REPO / "tmp"
LOGS = TMP / "logs"


def log_path(name: str) -> Path:
    """tmp/logs/<name>.log, directory created."""
    LOGS.mkdir(parents=True, exist_ok=True)
    return LOGS / f"{name}.log"


def make_log(name: str, *, stamped: bool = True):
    """log(msg): print and append to tmp/logs/<name>.log, ISO 8601 stamped."""
    path = log_path(name)

    def log(msg: str) -> None:
        if stamped:
            now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            msg = f"{now}  {msg}"
        print(msg, flush=True)
        with path.open("a") as fh:
            fh.write(msg + "\n")

    return log


def rel(p: Path | str) -> str:
    """Path relative to REPO, for logs/docs that must not leak an absolute path."""
    try:
        return str(Path(p).resolve().relative_to(REPO))
    except ValueError:
        return str(p)
