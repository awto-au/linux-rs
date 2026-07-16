#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Add SPDX GPL-2.0-only headers to our own tooling (scripts/, rulesdb/)
where missing — license issue #1 checklist item."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TAG = "SPDX-License-Identifier"


def main() -> int:
    changed = []
    for pat in ("scripts/*.py", "rulesdb/rules/*.toml", "rulesdb/cocci/*.cocci"):
        for f in sorted(REPO.glob(pat)):
            text = f.read_text()
            if TAG in text.split("\n", 3)[0] or TAG in text[:200]:
                continue
            lines = text.splitlines(keepends=True)
            hdr = ("# SPDX-License-Identifier: GPL-2.0-only\n"
                   if f.suffix in (".py", ".toml")
                   else "// SPDX-License-Identifier: GPL-2.0-only\n")
            if lines and lines[0].startswith("#!"):
                lines.insert(1, hdr)
            else:
                lines.insert(0, hdr)
            f.write_text("".join(lines))
            changed.append(str(f.relative_to(REPO)))
    print("\n".join(changed) if changed else "nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
