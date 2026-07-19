# virtio_blk.c const-qualifier fix, 2026-07-19

Fixes `awto-au/linux-rs#35`, filed from
`docs/virtio-blk-real-config-repro-2026-07-19.md`.

## Bug

`virtblk_name_format`'s `prefix` parameter was `char *`; its only call
site (line 1516) passes the string literal `"vd"` (`const char[3]`).
c2rust's clang front-end runs with `-Werror` and flags the qualifier
discard.

## Function body check (which fix is correct)

Read `virtblk_name_format` (`drivers/block/virtio_blk.c:1046-1068`):
`prefix` is only read — `strlen(prefix)` and `memcpy(buf, prefix,
strlen(prefix))` (source arg). Never written through. Confirms the
call site isn't the mistake; the parameter type is too narrow. Fix:
widen to `const char *` (not a call-site fix).

## Before (isolated re-transpile, `investigate_c2rust_failure.py --rerun --full-log`)

```
drivers/block/virtio_blk.c:1516:22: error: passing 'const char[3]' to
  parameter of type 'char *' discards qualifiers [-Werror,-Wincompatible-pointer-types-discards-qualifiers]
 1516 |         virtblk_name_format("vd", index, vblk->disk->disk_name, DISK_NAME_LEN);
      |                             ^~~~
drivers/block/virtio_blk.c:1046:38: note: passing argument to parameter 'prefix' here
 1046 | static int virtblk_name_format(char *prefix, int index, char *buf, int buflen)
      |                                      ^
1 error generated.
Error while processing .../virtio_blk.c.
```

Note: the kernel's own build flags (`compile_commands.json` entry,
plain `clang -c`) do NOT error on this — exit 0, no diagnostic at that
line. `-Wincompatible-pointer-types-discards-qualifiers` as `-Werror`
is specific to c2rust's own frontend invocation, not the kernel's
build.

## Fix

`drivers/block/virtio_blk.c:1046`:

```
-static int virtblk_name_format(char *prefix, int index, char *buf, int buflen)
+static int virtblk_name_format(const char *prefix, int index, char *buf, int buflen)
```

One line. Commit `048523222ea8` in `linux-riscv`.

## After (same isolated re-transpile)

```
Transpiling virtio_blk.c
warning: Falling back to an extern declaration for '__riscv_has_extension_likely': ...
warning: ignoring static assert during translation  (x6)

--- returncode 0 ---
```

Zero occurrences of "discards qualifiers" in the fresh rerun log
(`grep -c` confirms 0). Only the pre-existing, unrelated asm-goto/
static-assert warnings remain (same as the clean baseline documented
in `virtio-blk-real-config-repro-2026-07-19.md`).

## Regression check

`dev.py build`: `BUILD OK`.

`dev.py check --run-id virtio-blk-const-fix-verify`:

```
ORACLE PASS (17 suites)
INIT REACHED (initramfs userspace boot verified)
REPORT OK: 38 TUs, 17 suites, 147 vectors, 31 rules
```

Identical to pre-fix baseline (17 ok/0 not-ok, 38 TUs, INIT REACHED).
Zero regression.

## Upstream status

Checked live `torvalds/linux` master
(`drivers/block/virtio_blk.c`, fetched via raw.githubusercontent.com):
same signature (`static int virtblk_name_format(char *prefix, ...)`,
line 1046) and same call site (`virtblk_name_format("vd", ...)`, line
1516) — **the bug is present in real upstream Linux, not something
introduced by or specific to this tree.** Genuine latent upstream bug.
Not filed against `torvalds/linux` — out of scope for this pass, needs
separate human authorization to send a patch upstream.

## Commit trail

- `linux-riscv` commit `048523222ea8` ("drivers/block/virtio_blk:
  widen virtblk_name_format prefix to const char * (closes
  linux-rs#35)"), via `dev.py kcommit`. Only `virtio_blk.c` staged;
  other agents' untracked `.rs` files in the same tree
  (`drivers/soc/litex/litex_soc_ctrl.rs`,
  `kernel/events/ring_buffer.rs`, `kernel/nscommon.rs`,
  `kernel/sched/fair.rs`, `lib/fdt.rs`, `lib/is_single_threaded.rs`,
  `lib/math/gcd.rs`, `mm/slab_common.rs`) left untouched.
- `awto-au/linux-rs#35` closed with this evidence.
