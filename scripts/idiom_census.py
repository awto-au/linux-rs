#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Phase 0 evaluation: rough idiom frequency counts over the pinned corpus.

Counts textual occurrences of ~12 kernel idiom markers across exactly the C
translation units in linux/compile_commands.json (i.e. the pinned config's
corpus, not the whole tree). This is NOT the Phase 1 census — it's the cheap
sanity check that the corpus is idiom-dense enough to justify it.

Output: tmp/idiom_census.log (progress + results), tmp/idiom_census.json.
"""
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "tmp" / "idiom_census.log"
OUT = REPO / "tmp" / "idiom_census.json"

IDIOMS = {
    "container_of": r"\bcontainer_of\s*\(",
    "list_for_each_entry*": r"\blist_for_each_entry\w*\s*\(",
    "list_add/del": r"\blist_(add|add_tail|del|del_init)\s*\(",
    "spin_lock family": r"\bspin_lock(_irqsave|_irq|_bh)?\s*\(",
    "mutex_lock": r"\bmutex_lock(_interruptible|_killable)?\s*\(",
    "rcu_read_lock": r"\brcu_read_lock\s*\(",
    "rcu_dereference*": r"\brcu_dereference\w*\s*\(",
    "READ_ONCE/WRITE_ONCE": r"\b(READ_ONCE|WRITE_ONCE)\s*\(",
    "atomic ops": r"\batomic(64|_long)?_(read|set|inc|dec|add|sub|cmpxchg|xchg|fetch_\w+|try_cmpxchg)\w*\s*\(",
    "refcount ops": r"\brefcount_(inc|dec|set|read|add|sub|dec_and_test|inc_not_zero)\w*\s*\(",
    "kref get/put": r"\bkref_(get|put|init)\s*\(",
    "ERR_PTR/IS_ERR/PTR_ERR": r"\b(ERR_PTR|IS_ERR|PTR_ERR|IS_ERR_OR_NULL)\s*\(",
    "EXPORT_SYMBOL": r"\bEXPORT_SYMBOL(_GPL|_NS|_NS_GPL)?\s*\(",
    "module_init/exit": r"\b(module_init|module_exit|module_platform_driver|module_pci_driver|module_usb_driver)\s*\(",
    "ioread/iowrite/readl/writel": r"\b(io(read|write)(8|16|32|64)|read[bwlq]|write[bwlq])(_relaxed)?\s*\(",
    "wait_event*": r"\bwait_event\w*\s*\(",
    "goto err/out": r"\bgoto\s+(err|out|fail|unlock|free|cleanup)\w*\s*;",
}

def main() -> int:
    REPO.joinpath("tmp").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w"), logging.StreamHandler(sys.stdout)],
    )
    cc = json.load(open(REPO / "linux" / "compile_commands.json"))
    files = sorted({e["file"] for e in cc if e["file"].endswith(".c")})
    logging.info("corpus: %d C translation units", len(files))

    pats = {k: re.compile(v) for k, v in IDIOMS.items()}
    totals: Counter = Counter()
    files_with: Counter = Counter()
    total_lines = 0
    for i, f in enumerate(files):
        try:
            text = Path(f).read_text(errors="replace")
        except OSError as e:
            logging.warning("unreadable: %s (%s)", f, e)
            continue
        total_lines += text.count("\n")
        for name, pat in pats.items():
            n = len(pat.findall(text))
            if n:
                totals[name] += n
                files_with[name] += 1
        if (i + 1) % 500 == 0:
            logging.info("scanned %d/%d files", i + 1, len(files))

    logging.info("total corpus lines: %d", total_lines)
    width = max(len(k) for k in IDIOMS)
    for name, n in totals.most_common():
        logging.info("%-*s %7d occurrences in %4d/%d files",
                     width, name, n, files_with[name], len(files))
    json.dump(
        {"tu_count": len(files), "total_lines": total_lines,
         "occurrences": dict(totals), "files_with": dict(files_with)},
        open(OUT, "w"), indent=2)
    logging.info("wrote %s", OUT)
    return 0

if __name__ == "__main__":
    sys.exit(main())
