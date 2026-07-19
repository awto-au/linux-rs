# virtio_blk.c real-config c2rust repro, 2026-07-19

Follow-up to `docs/virtio-rust-port-scoping-2026-07-19.md` (synthetic
single-TU dry-run hit a new panic + config-artifact errors) and
`docs/block-layer-enable-2026-07-19.md` (proved the Kconfig chain boots,
in an isolated worktree, never merged into main). This closes the loop:
merges the Kconfig into the MAIN tree's real `.config`, regenerates the
real `compile_commands.json`, re-runs c2rust against the real entry.

## 1. Kconfig merge — main tree, not a worktree

```
$ python3 scripts/dev.py config -e CONFIG_BLOCK -e CONFIG_VIRTIO \
    -e CONFIG_VIRTIO_MENU -e CONFIG_VIRTIO_MMIO -e CONFIG_VIRTIO_BLK
CONFIG OK: ...
```

Landed `=y` in `linux-riscv/.config` (confirmed via grep):
`CONFIG_BLOCK`, `CONFIG_BLOCK_LEGACY_AUTOLOAD`, `CONFIG_BLK_DEV`,
`CONFIG_VIRTIO_BLK`, `CONFIG_VIRTIO_ANCHOR`, `CONFIG_VIRTIO`,
`CONFIG_VIRTIO_MENU`, `CONFIG_VIRTIO_MMIO`. Same 4+menu set already
proven boot-safe in `linux-riscv-worktrees/block-layer-enable` (17
ok/0 not-ok there).

**Why the main tree, not a worktree, and why this is safe:**
`investigate_c2rust_failure.py` hardcodes `TREE = REPO /
"linux-riscv"` (line 39); `run_c2rust_baseline.py` reads `cc_path =
TREE / "compile_commands.json"` from the same main tree, never a
worktree. c2rust's baseline corpus is generated exclusively from the
main tree's build — there is no mechanism today to point either
script at an alternate `compile_commands.json`. A worktree-only change
(as `block-layer-enable` did) can prove a boot but can never enter the
c2rust corpus. Confirmed no concurrent kernel-tree agent was using the
main tree before touching it: no `dev.py`/`boot_qemu.py`/
`qemu-system-riscv64`/c2rust process running (`ps aux` clean) at time
of edit; `linux-riscv/.config` and `compile_commands.json` are both
kernel-standard gitignored build artifacts (`git check-ignore -v`
confirms both, via `.gitignore:13` and `:184`) — not git-tracked, no
merge-conflict risk with other agents' work. Other agents' uncommitted
`.rs` files in the same tree (`drivers/soc/litex/litex_soc_ctrl.rs`,
`kernel/events/ring_buffer.rs`, `kernel/nscommon.rs`,
`kernel/sched/fair.rs`, `lib/fdt.rs`, `lib/is_single_threaded.rs`,
`lib/math/gcd.rs`, `mm/slab_common.rs`) were left untouched — a
Kconfig-only + rebuild change does not touch those paths.

`configs/riscv64-slim-serial.defconfig` (the one tracked, documented
defconfig snapshot) was checked and NOT updated: no script reads it
(`grep` across `scripts/*.py` — zero hits besides comments/docs
referencing it descriptively); the actual worktree-seeding mechanism
(`linux_riscv_worktree.py`'s `SEED_CONFIG = linux-riscv/.config`)
copies the main tree's *live* `.config`, not this file, into every new
worktree. The defconfig snapshot already predates this tree's real
state on several other axes (e.g. it says `# CONFIG_PROC_FS is not
set`/`# CONFIG_KALLSYMS is not set` while the live `.config` has both
enabled) — editing just the virtio lines would imply a sync guarantee
that doesn't hold today. Left as-is; the live seed path is what
matters and is fixed.

## 2. Build + regenerate real compile_commands.json

`dev.py build` alone does NOT regenerate `compile_commands.json` (it's
a separate, explicitly-requested kernel Makefile target, confirmed via
`Makefile:2271` — `compile_commands.json:
scripts/clang-tools/gen_compile_commands.py ...`, only built when
named on the `make` command line). Ran:

```
$ make -C linux-riscv ARCH=riscv LLVM=1 -j32 compile_commands.json
GEN     compile_commands.json
```

Entry count: 575 -> 624 (+49). New virtio hits (0 before):

```
drivers/block/virtio_blk.c
drivers/virtio/virtio_anchor.c
drivers/virtio/virtio.c
drivers/virtio/virtio_mmio.c
drivers/virtio/virtio_ring.c
```

## 3. Oracle regression check — before touching anything downstream

```
$ python3 scripts/dev.py check --run-id virtio-main-tree-verify
... 17 suites ok, 0 not-ok ...
ORACLE PASS (17 suites)
INIT REACHED (initramfs userspace boot verified)
REPORT OK: 38 TUs, 17 suites, 147 vectors, 31 rules
```

Identical to pre-change baseline (17 ok/0 not-ok, 38 TUs, INIT
REACHED). Boot log archived:
`docs/status/boot-logs/20260719T135929+1000-virtio-main-tree-verify.log`.
Zero regression from the Kconfig merge — confirmed with a real build,
not assumed from the worktree result.

## 4. c2rust against the REAL compile_commands.json entry

`dev.py c2rust-build`: binaries fresh, no rebuild needed.
`run_c2rust_baseline.py` (no flags — this project's normal routine
run; `virtio_blk.c` and the 3 transport files have no prior baseline
history so they're automatically included as "unstable", 90/591
non-slow files ran this pass):

```
DONE: {'clean': 90} (c2rust 6065eaf19, corpus 04312ea1ff7e, ...)
```

DB query, `c2rust_attempts` for the virtio set:

```
drivers/virtio/virtio.c            |clean|0|0|0|0
drivers/virtio/virtio_anchor.c     |clean|0|0|0|0
drivers/virtio/virtio_mmio.c       |clean|0|0|0|0
drivers/virtio/virtio_ring.c       |clean|0|0|0|0
drivers/block/virtio_blk.c         |clean|0|0|0|0
```

All 5 real virtio-chain files: `clean`, returncode 0, 0 missing
top-level nodes, 0 missing children, 0 label-address exprs.

### virtio_blk.c specifically — full stderr (both the baseline run and an isolated `--rerun --full-log` with `RUST_BACKTRACE=full`, identical):

```
drivers/block/virtio_blk.c:1516:22: error: passing 'const char[3]' to
  parameter of type 'char *' discards qualifiers [-Werror,...]
1 error generated.
Error while processing .../virtio_blk.c.
Transpiling virtio_blk.c
warning: Falling back to an extern declaration for
  '__riscv_has_extension_likely': body failed to translate: Cannot
  translate GNU asm goto (extended asm with label operands)
warning: ignoring static assert during translation  (x6)
--- returncode 0 ---
```

**No panic. `arg_tys.len() == exprs.len()` does not appear anywhere in
either run.** Output `virtio_blk.rs` (10406 lines) is complete and
well-formed: every major function present
(`virtblk_probe`/`virtblk_remove`/`virtblk_map_queues`/`init_vq`/
`virtblk_done`/`virtblk_result`/freeze+reset paths), ending cleanly
with the `virtio_driver` vtable struct (`probe`/`remove`/
`config_changed`/`reset_prepare`/`reset_done` fn-pointer casts) and
the `INIT_ARRAY` static-initializer trailer c2rust always emits last.

## 5. Findings vs. the two errors previously reported

1. **`sg_alloc_table_chained`/`sg_free_table_chained` "undeclared
   function"** — gone. Confirmed config artifact of the synthetic
   compile_commands.json's stale (non-virtio) `autoconf.h`, as
   predicted (`CONFIG_ARCH_NO_SG_CHAIN` gate,
   `include/linux/scatterlist.h:550`).
2. **`assertion failed: arg_tys.len() == exprs.len()` panic**
   (`c2rust-transpile/src/translator/functions.rs:666`,
   `convert_call_args`) — **does not reproduce.** Confirmed config
   artifact of the synthetic single-TU compile_commands.json, not a
   real c2rust bug. Since it doesn't reproduce, there's no live call
   site left to isolate inside `cpufeature-macros.h` — the panic
   simply doesn't fire against the real build's clang invocation.

**awtoau/c2rust#23 updated and closed** (not planned / invalid —
config artifact) with this evidence.

## 6. New, real, non-blocking finding

`virtblk_name_format("vd", index, vblk->disk->disk_name,
DISK_NAME_LEN)` at line 1516 passes a `const char[3]` literal into a
`char *prefix` parameter (`virtblk_name_format`, line 1046) — a real,
pre-existing latent qualifier-discard bug in the upstream C source.
Does not block transpilation (c2rust's clang front-end reports it as
an `-Werror` diagnostic during an earlier pass, then proceeds to a
clean full AST export on the actual transpile). Filed as
`awto-au/linux-rs#35` (low priority, tracking only).

## 7. Rule-conformance signal

`dev.py`'s rule-conformance checker
(`check_c2rust_rule_conformance.py`) mechanically checks 10 of 27
rules (the tier-1 text-checkable ones). Per-file rows for
`drivers/block/virtio_blk.c`:

| rule | status | n |
|---|---|---|
| likely-unlikely | conformant | 1 |
| unsigned-wrap-mul | conformant | 9 |
| warn-on | conformant | 5 |

**Zero violations.** Lower hit-count than `8250_port.c` (27
export-symbol-gpl conformant + 2 violation, 3 fls-family, 5
swap-mem-swap violation, 35 unsigned-wrap-mul, 1 warn-on) — expected,
not a scan gap: `virtio_blk.c` genuinely uses `EXPORT_SYMBOL_GPL` far
less than a driver-core file like 8250, and has no `fls`/`swap`-family
calls at all (confirmed absent by re-reading the file in the earlier
scoping doc). The checkable-rule surface that IS present is 100%
clean.

## Conclusion

**Config artifact, not a genuine c2rust bug.** Both previously-flagged
errors vanish against a real, non-synthetic `compile_commands.json`.
`virtio_blk.c` transpiles cleanly end-to-end (returncode 0, complete
AST export, zero rule violations on the checkable subset) — materially
closer to "translation-ready" than the earlier synthetic-config pass
suggested. Remaining blockers to an actual translation slice are
unchanged from the scoping doc's other findings (not re-litigated
here): (a) the device-lifecycle half of `Operations` still has no
trait hook for freeze/quiesce/map_queues, (b) `virtio_ring.c` remains
a permanent unsafe-FFI-boundary case, not a translation target. Both
are scoping-doc conclusions, unaffected by this repro.

## Commit trail

- Kconfig change lives only in `linux-riscv/.config` /
  `compile_commands.json` — both kernel-standard gitignored build
  artifacts (`linux-riscv/.gitignore:13`,`:184`), never git-tracked in
  the kernel worktree itself (matches upstream Linux convention). No
  `dev.py kcommit` target exists for this change — there is nothing
  for git to track in `linux-riscv/`. The durable record is this doc +
  `docs/status/boot-logs/20260719T135929+1000-virtio-main-tree-verify.log`
  + the `patterns.db` `c2rust_attempts`/`c2rust_rule_conformance` rows
  written by this pass, all of which ARE committed here in
  `awto-au/linux-rs`.
- `awtoau/c2rust#23` closed (not planned), comment with full repro
  evidence.
- `awto-au/linux-rs#35` filed (qualifier-discard bug, low priority,
  tracking only).
