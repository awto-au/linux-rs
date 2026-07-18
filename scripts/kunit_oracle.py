#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""KUnit boot-oracle: the shared pass/fail primitive for "did this kernel
build+boot cycle actually work".

Why this exists: dev.py's boot() and integrate_tu.py's main() each
independently hand-rolled the same regex scan of a QEMU boot log — any
"not ok" KUnit line is a hard fail, and zero "ok" lines at all is also a
hard fail (no KUnit output ran is not a pass by default). Two independent
implementations of the project's single most load-bearing invariant was a
real bug on its own: any future tweak to the log format or the fail
condition risked silently drifting the two apart. See rulesdb/rules/
0028-kunit-boot-oracle-gate.toml for the gate itself as a rule.

This module owns ONLY the log-text -> verdict logic, not log discovery
(each caller already knows its own log path — dev.py's boot() always
reads tmp/qemu-boot.log via boot_qemu.py's default --run-id-less path;
integrate_tu.py reads the same file after its own boot_qemu.py
invocation) and not printing (each caller keeps its own existing
output format — "ORACLE PASS (N suites)" / "ORACLE FAIL" text differs
slightly between the two callers already and that's preserved, only the
underlying scan is shared).

Log-line-format contract (mirrored in the rule's [emit] text — if this
ever needs to change, update both together):
  - a KUnit test PASS line matches  ^ok \\d+ .*$
  - a KUnit test FAIL line matches  ^\\s*not ok .*$
  - "not ok" lines may be indented (KUnit subtests); "ok" lines are not.
  - since 2026-07-18, every line in a freshly-generated raw log (see
    boot_qemu.py) carries a leading elapsed-time prefix of the exact
    form "NNNNN.NNN " (5-digit zero-padded whole seconds, '.', 3-digit
    milliseconds, one space) before the original content — e.g.
    "00003.847 ok 1 rust_kernel_str". TS_PREFIX_RE matches that prefix
    (optionally — old archived logs under docs/status/boot-logs/ have
    no prefix at all and must keep parsing identically) and every
    line-anchored regex below, plus every other module's regex over
    this same log format (render_boot_log.py, report.py,
    integrate_tu.py's --suite check), embeds it so a raw QEMU boot log
    is the single source of truth all five consumers agree on. Import
    TS_PREFIX_RE from here rather than redefining it — it must change
    in exactly one place if the timestamp format ever does.

Usage: verify_kunit_ok(log_text) -> (passed, ok_lines, bad_lines)
"""
import re

# Optional leading "NNNNN.NNN " elapsed-time prefix — see module doc.
# Non-capturing so callers that embed this fragment inside their own
# capturing regex (e.g. integrate_tu.py's per-suite name group) don't
# have their group numbering shifted by it.
TS_PREFIX_RE = r"(?:\d{5}\.\d{3} )?"

OK_RE = re.compile(rf"^{TS_PREFIX_RE}ok \d+ .*$", re.M)
NOT_OK_RE = re.compile(rf"^{TS_PREFIX_RE}\s*not ok .*$", re.M)


def verify_kunit_ok(log_text: str) -> tuple[bool, list[str], list[str]]:
    """Scan a QEMU boot log's text for KUnit result lines and return the
    oracle verdict. Returns (passed, ok_lines, bad_lines):
      - ok_lines: every line matching ^ok \\d+ .*$ (one per KUnit suite)
      - bad_lines: every line matching ^\\s*not ok .*$ (any is a hard fail)
      - passed: True iff bad_lines is empty AND ok_lines is non-empty
        (no KUnit output at all is NOT a pass — see module doc)."""
    ok_lines = OK_RE.findall(log_text)
    bad_lines = NOT_OK_RE.findall(log_text)
    passed = not bad_lines and bool(ok_lines)
    return passed, ok_lines, bad_lines
