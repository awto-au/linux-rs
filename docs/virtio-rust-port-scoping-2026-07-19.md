# virtio Rust port — scoping (block-mq glue vs. virtqueue core)

Status: **research only.** No changes to `linux-riscv/drivers/block/virtio_blk.c`
or `linux-riscv/drivers/virtio/*`. Follow-up to
`docs/block-layer-enable-2026-07-19.md` (real virtio-blk device boots and is
visible; C driver only). Same method/rigor as
`docs/serial-8250-translation-scoping-2026-07-18.md` and
`docs/tmpfs-rust-scoping-2026-07-18.md`: real function tiers, real grep
evidence, no speculation presented as fact.

**Config note found in passing:** `linux-riscv/.config` (main tree) has
`CONFIG_VIRTIO_MENU is not set` — the virtio config from
`docs/block-layer-enable-2026-07-19.md` lives only in the
`linux-riscv-worktrees/block-layer-enable` worktree, never merged into
main's `.config`. `virtio_blk.c` and all of `drivers/virtio/*.c` are
therefore **absent from `linux-riscv/compile_commands.json`** (grep for
`virtio` in it: 0 hits) — these files are not currently built by this
project's default kernel and are not in c2rust's baseline corpus (which is
generated directly from `compile_commands.json`, confirmed by reading
`scripts/run_c2rust_baseline.py`'s `main()`).

## Layer 1: `drivers/block/virtio_blk.c` — block-mq glue

1732 lines, 0 `readl`/`writel`/`inb`/`outb` (previously confirmed, re-verified
here). Confirmed by grep: `#include <linux/virtio.h>`,
`<linux/virtio_blk.h>`, `<linux/blk-mq.h>`, `<linux/scatterlist.h>` — no
`<linux/io.h>` or arch MMIO headers. The file genuinely has no raw
register access; it is 100% "call into the virtqueue/blk-mq/DMA-mapping
APIs" logic.

### Function tier pass (47 top-level functions)

Full list via `grep -n "^static\|^int \|^void "` etc., cross-checked
against the file:

**Tier A — pure/small helpers, no virtqueue/DMA call, isolable:**
- `virtblk_result()` (113) — status byte -> `blk_status_t` switch.
- `get_virtio_blk_vq()` (131) — array index, one line.
- `virtblk_vbr_status()` (329), `index_to_minor()`/`minor_to_index()`
  (882/887) — pure arithmetic.
- `virtblk_getgeo()` (835) — fabricated CHS geometry, pure arithmetic.
- `virtblk_name_format()` (1046) — `sprintf`-family only.
- `virtblk_get_cache_mode()` (1070, partial) — feature-bit read + switch.

About 8-10 of the 47 are this shape. Directly comparable to 8250's Tier A
(`serial8250_compute_lcr` etc.) — good diff-oracle candidates in isolation,
but none of them are the interesting part of this driver.

**Tier B — calls into virtqueue core (`vring_*`/`virtqueue_*`) or DMA/sg
APIs, narrow but stateful:**
Grep for `vring_|virtqueue_` calls in the file: 13 call sites, using
exactly 7 distinct functions: `virtqueue_add_sgs`, `virtqueue_kick`,
`virtqueue_kick_prepare`, `virtqueue_notify`, `virtqueue_get_buf`,
`virtqueue_disable_cb`, `virtqueue_enable_cb`. This is the whole
virtqueue-facing API surface of the file — no `vring_*` symbol is called
directly (the driver only ever goes through the `virtqueue_*` wrapper
layer in `virtio_ring.c`, never touches `struct vring`/descriptor tables
itself). Functions in this tier: `virtblk_add_req` (139),
`virtblk_setup_discard_write_zeroes_erase` (160, sg-table construction),
`virtblk_unmap_data`/`virtblk_cleanup_cmd` (206/232, `sg_free_table_chained`
+ `dma_unmap`-adjacent cleanup — 4 total sg/dma calls in the file, all
here), `virtblk_done` (350, the IRQ-context completion callback —
`virtqueue_get_buf` loop), `init_vq` (960, one-time queue setup — the
only place `virtqueue_notify`/queue affinity is wired).

**Tier C — blk-mq orchestration / device lifecycle, the majority:**
`virtblk_probe` (1438, ~125 lines — device bring-up, feature negotiation,
`gendisk` construction), `virtblk_remove` (1564), freeze/restore/reset
(1590-1656, 6 functions, PM/live-migration paths), `virtio_queue_rqs`/
`virtblk_prep_rq_batch` (465/501, batch submission fast path),
`virtblk_map_queues` (1165, IRQ-affinity-to-hctx mapping),
`virtblk_poll`/`virtblk_complete_batch` (1193/1204, poll-queue path),
`virtblk_config_changed*` (945/953, hot-resize via `struct
work_struct`), module init/exit (1695/1721).

### blk_mq_* API surface actually used vs. what `Operations` exposes

`grep -oE '\bblk_mq_[a-zA-Z_]+\s*\('` over the file: **23 distinct
`blk_mq_*` C functions** called directly: `blk_mq_alloc_tag_set`,
`blk_mq_free_tag_set`, `blk_mq_alloc_disk`, `blk_mq_alloc_request`,
`blk_mq_free_request`, `blk_mq_start_request`, `blk_mq_requeue_request`,
`blk_mq_end_request`, `blk_mq_end_request_batch`,
`blk_mq_complete_request`, `blk_mq_complete_request_remote`,
`blk_mq_add_to_batch`, `blk_mq_map_queues`, `blk_mq_map_hw_queues`,
`blk_mq_num_possible_queues`, `blk_mq_stop_hw_queue`,
`blk_mq_start_stopped_hw_queues`, `blk_mq_freeze_queue`,
`blk_mq_unfreeze_queue`, `blk_mq_quiesce_queue_nowait`,
`blk_mq_unquiesce_queue`, `blk_mq_rq_to_pdu`, `blk_mq_rq_from_pdu`.

`rust/kernel/block/mq/operations.rs` (286 lines) defines `trait
Operations` with exactly **4 driver-supplied methods**: `queue_rq`,
`commit_rqs`, `complete`, `poll` (+ init/exit hctx/request, which are
framework-internal, not driver-supplied). It maps cleanly onto
`virtblk_probe`'s eventual `queue_rq`/`commit_rqs`/`complete`/`poll`
callback registrations (`virtio_commit_rqs` at line 377 and
`virtblk_poll` at 1204 correspond 1:1 to `Operations::commit_rqs`/
`Operations::poll`) — **but it has no equivalent for**:
`blk_mq_freeze_queue`/`unfreeze_queue`/`quiesce_queue_nowait`/
`unquiesce_queue` (PM/reset/live-migration control-plane calls,
exercised by `virtblk_freeze`/`virtblk_restore`/`virtblk_reset_prepare`/
`virtblk_reset_done`), `blk_mq_map_queues`/`map_hw_queues` (IRQ-affinity
queue mapping, `virtblk_map_queues`), or `blk_mq_stop_hw_queue`/
`start_stopped_hw_queues` (used in the OOM/EIO retry path inside
`virtblk_fail_to_queue`, line 391). `TagSet`/`GenDiskBuilder`
(`rust/kernel/block/mq/{tag_set,gen_disk}.rs`, 85+241 lines) cover the
alloc/build side (`blk_mq_alloc_tag_set`/`blk_mq_alloc_disk`-equivalent)
reasonably, confirmed by reading both files.

**Verdict on the trait-shape question:** the *hot path* (`queue_rq`,
completion, poll, commit) maps cleanly 1:1. The *device lifecycle*
(freeze/quiesce/reset for suspend and virtio device-reset support, hctx
IRQ-affinity mapping) has **no trait hook today** — `rnull.rs` (100
lines, `drivers/block/rnull/rnull.rs`) never needs these because it's a
synthetic single-queue device with no underlying transport to
freeze/reset/re-map; virtio_blk genuinely does, because the virtio
device itself can be reset/migrated independently of the block layer.
This is a real, not hypothetical, capability gap: a virtio_blk-in-Rust
port would need `rust/kernel/block/mq/operations.rs` extended before
`virtblk_freeze`/`virtblk_reset_prepare`/`virtblk_map_queues` have
anywhere to attach.

### c2rust dry-run (single-TU, outside the normal corpus)

`virtio_blk.c` is not in `compile_commands.json` (see config note above),
so `scripts/investigate_c2rust_failure.py`/`run_c2rust_baseline.py`
cannot be pointed at it directly (`rerun_isolated()` requires a
pre-existing per-file `compile_commands.json` from a prior baseline run).
Built a synthetic single-entry `compile_commands.json` from an existing
`drivers/base/bus.c` corpus entry's flags (same `-target riscv64-linux-gnu`
Clang invocation, swapped file/output paths) and ran the project's actual
c2rust fork (`/mnt/2tb/git/github.com/awtoau/c2rust/target/release/c2rust
transpile ... --enable-rule=all`) directly against it:

```
drivers/block/virtio_blk.c:209:3: error: call to undeclared function
  'sg_free_table_chained'
drivers/block/virtio_blk.c:222:8: error: call to undeclared function
  'sg_alloc_table_chained'
drivers/block/virtio_blk.c:1516:22: error: passing 'const char[3]' to
  parameter of type 'char *' discards qualifiers
3 errors generated.
Error while processing .../virtio_blk.c.
Transpiling virtio_blk.c
warning: Falling back to an extern declaration for
  '__riscv_has_extension_likely': body failed to translate: Cannot
  translate GNU asm goto (extended asm with label operands)

thread 'main' panicked at 'assertion failed: arg_tys.len() == exprs.len()',
  c2rust-transpile/src/translator/functions.rs:666:17
```

Two distinct findings:
1. The `sg_alloc_table_chained`/`sg_free_table_chained` "undeclared
   function" errors are an artifact of the synthetic compile_commands.json
   (built against this tree's current non-virtio `include/generated/
   autoconf.h`; `include/linux/scatterlist.h:550` gates those declarations
   behind `#ifndef CONFIG_ARCH_NO_SG_CHAIN`, which needs the real
   virtio-enabled `.config`/autoconf state) — **not** a c2rust gap, an
   artifact of not having a real build tree with CONFIG_VIRTIO_BLK=y
   generating its own `compile_commands.json` (that would require a full
   `dev.py build` in a worktree, out of scope for this research-only
   pass).
2. The `__riscv_has_extension_likely` GNU-asm-goto warning is the
   **already-known, already-closed** `awtoau/c2rust#14` ("GNU asm goto
   (label operands) unconditionally rejected... incl. RISC-V cpufeature
   fast-path + 8250 serial + sched/fair") — expected, handled as a
   fallback (extern decl), not fatal on its own.
3. The **panic** (`assertion failed: arg_tys.len() == exprs.len()` in
   `c2rust-transpile/src/translator/functions.rs:666`,
   `convert_call_args` -> `convert_function_call`) is a **new crash
   signature**, not matching any of the 22 issues in `awtoau/c2rust`
   (checked via `gh issue list --repo awtoau/c2rust --state all`, 22
   issues, none mention `arg_tys` or `convert_call_args`). It fires while
   converting a call reached transitively from `virtio_blk.c`'s include
   chain into `arch/riscv/include/asm/cpufeature-macros.h` (same header
   file as the asm-goto warning immediately preceding it in the log —
   likely a builtin/variadic call adjacent to the asm-goto fallback path,
   not yet root-caused further in this pass). This is real, reportable
   c2rust-gap evidence for a file c2rust has never been run against
   before — genuinely new information, not a re-discovery of #14.

**Conclusion for layer 1 (near-term translation TU?): partial yes, with a
real gap identified.** The Tier A/B split is 8250-shaped (a handful of
pure helpers, then a narrow well-defined virtqueue-call surface), and the
`Operations` trait's hot path (`queue_rq`/`commit_rqs`/`complete`/`poll`)
maps cleanly onto it — better attachment story than 8250 had at this
stage, because the abstraction and a proof-of-life driver (`rnull`)
already exist and boot. But it is **not** "ready today" the way the 8250
Tier-A slice was: (a) the C file has never actually been compiled in this
project's default config (needs the worktree's Kconfig merged into main
first), (b) c2rust hits a new, unreported crash on it, (c) the device
*lifecycle* half of `virtblk_probe`/`_remove`/freeze/reset/map_queues has
no Rust trait surface to land in yet — porting only the hot path would
produce a driver that can't suspend/resize/reset, which real virtio_blk
must support.

## Layer 2: `drivers/virtio/` — virtqueue/transport core

**17,076 lines total** (not ~16.7k — recount via `wc -l *.c *.h`), across
25 files. Real per-file breakdown:

| File | Lines | Relevance to this project |
|---|---:|---|
| `virtio_ring.c` | 3983 | **Core.** Descriptor-ring management (split + packed ring formats), DMA mapping, the `virtqueue_*` API `virtio_blk.c` calls into. |
| `virtio_mem.c` | 3158 | Memory hot-plug balloon variant. Not used by this project's `-device virtio-blk-device` boot path. Out of scope. |
| `virtio_rtc_driver.c` | 1419 | RTC clock device class. Unrelated subsystem. Out of scope. |
| `virtio_balloon.c` | 1202 | Memory balloon driver. Not virtio-blk-relevant. Out of scope. |
| `virtio_pci_modern.c` | 1301 | PCI transport (modern). This project uses MMIO transport (`-device virtio-blk-device`, confirmed in `docs/block-layer-enable-2026-07-19.md`), not PCI (`virtio-blk-pci`). Out of scope. |
| `virtio_pci_common.c` | 864 | PCI transport shared glue. Out of scope (no PCI transport in use). |
| `virtio_mmio.c` | 829 | **Transport, in scope.** The actual MMIO transport this project's QEMU invocation uses. |
| `virtio.c` | 739 | **Core.** Device/driver registration, feature negotiation, `struct virtio_device` bus glue — transport-independent. |
| `virtio_pci_modern_dev.c` | 758 | PCI transport low-level. Out of scope. |
| `virtio_vdpa.c` | 518 | vDPA transport (hardware-offload virtqueues). Out of scope. |
| `virtio_input.c` | 421 | Input device class (keyboard/mouse over virtio). Not virtio-blk. Out of scope. |
| `virtio_rtc_ptp.c` | 347 | RTC/PTP glue. Out of scope. |
| `virtio_pci_legacy.c` | 236 | Legacy PCI transport. Out of scope. |
| `virtio_pci_admin_legacy_io.c` | 244 | PCI admin/legacy IO. Out of scope. |
| `virtio_rtc_class.c` | 262 | RTC class registration. Out of scope. |
| `virtio_pci_legacy_dev.c` | 222 | Legacy PCI transport low-level. Out of scope. |
| `virtio_pci_common.h` | 201 | PCI transport shared decls. Out of scope. |
| `virtio_rtc_internal.h` | 122 | RTC internal decls. Out of scope. |
| `virtio_debug.c` | 117 | Debugfs instrumentation, optional. |
| `virtio_dma_buf.c` | 92 | dma-buf export helper for virtio-gpu-family devices. Not blk-relevant. |
| `virtio_rtc_arm.c` | 23 | ARM RTC arch glue. Out of scope. |
| `virtio_anchor.c` | 18 | Weak-symbol anchor for `virtio_check_mem_acc_cb`. Trivial. |

**Real dependency chain, verified via `#include` and call sites (not
assumed):** `virtio_blk.c` includes `<linux/virtio.h>` (declares
`struct virtio_device`/`virtqueue_*` prototypes) and
`<uapi/linux/virtio_ring.h>`. `virtio.c` and `virtio_ring.c` both include
`<linux/virtio.h>`+`<linux/virtio_config.h>`; `virtio_mmio.c` additionally
includes `<linux/io.h>`, `<linux/dma-mapping.h>`, `<linux/of.h>`,
`<linux/platform_device.h>`, `<uapi/linux/virtio_mmio.h>`,
`<linux/virtio_ring.h>` directly — i.e. `virtio_mmio.c` is the transport
that turns MMIO register content into calls against the `virtio_ring.c`
API and registers a `struct virtio_device` via `virtio.c`'s
`register_virtio_device()`. This confirms the task's premise: **the real
dependency chain for this project's boot path is exactly `virtio.c` +
`virtio_ring.c` + `virtio_mmio.c`** — 739 + 3983 + 829 = **5551 lines**,
not the full 17,076. None of PCI/vDPA/balloon/mem/rtc/input/dma_buf is
reachable from a `virtio-blk-device` (MMIO) boot.

### Unsafe-surface characterization (not "lots of unsafe stuff" — counted)

Grepped each of the three in-scope files for concrete unsafe-shaped C
patterns:

| Pattern | `virtio.c` | `virtio_ring.c` | `virtio_mmio.c` |
|---|---:|---:|---:|
| `readl`/`writel`/`readb`/`writeb`/`readw`/`writew`/`ioread32` | 0 | 0 | **59** (19 readl, 33 writel, 2 readb/writeb, 1 readw/writew, 1 ioread32) |
| `virtio_mb`/`virtio_rmb`/`virtio_wmb` (memory barriers) | 0 | **14** (4 mb, 4 rmb, 6 wmb) | **1** (wmb) |
| `dma_map_*`/`dma_unmap_*`/`dma_alloc_*`/`dma_free_*` | 0 | **4** (`dma_alloc_coherent`, `dma_free_coherent`, `dma_map_page_attrs`, `dma_unmap_page_attrs`) | 0 |
| `volatile` | 0 | 0 | 0 |
| `READ_ONCE`/`WRITE_ONCE`/`smp_load_acquire`/`smp_store_release` | 0 | **23** | 0 |
| Raw descriptor-table indexing (`desc[...]`, `vring_*_addr`) | 0 | **33** occurrences of desc-array/addr patterns; ~40 incl. `cpu_to_virtio`/`virtio_to_cpu` byte-order shims | 0 |
| kmalloc/list/lock primitives (general kernel-object unsafe surface) | 12 | (not separately counted — subsumed by desc-table figure) | 0 |

**This directly contradicts a naive "virtio is all safe shared-memory, no
register-poking" framing for the transport layer**: `virtio_mmio.c` is
genuinely `readl`/`writel`-shaped hardware-register code — 59 raw MMIO
accessor calls, the same character class as 8250's Tier B, just against a
smaller/simpler QEMU-virtual register block (`uapi/linux/virtio_mmio.h`'s
fixed offsets) instead of a real 16550. `virtio.c` (registration/feature
negotiation) is comparatively clean — no barriers, no DMA, no raw
descriptor manipulation, mostly refcounting/list/device-model glue (12
kmalloc/list/lock-shaped calls). `virtio_ring.c` is where the actual
unsafe density concentrates: 14 explicit memory barriers, 4 DMA
map/unmap call sites, 23 `READ_ONCE`/`WRITE_ONCE` (required because the
device-side write to the used-ring is concurrent with driver reads — this
is genuine lock-free producer/consumer shared-memory protocol code, not
incidental unsafety), and ~33-40 raw descriptor-table index/address
manipulations (populating `struct vring_desc`/`vring_packed_desc` fields
that get DMA'd to the device). No `volatile` anywhere in any of the three
files (Linux kernel convention uses `READ_ONCE`/`WRITE_ONCE` instead, not
raw `volatile` qualifiers — consistent with what's actually there).

**Rule 0018 (`c-abi-allocator-contract`) applicability check:** rule 0018
covers one specific pattern — a translated fn that `kmalloc`s and hands
the raw pointer across the C ABI to code that `kfree()`s it, where the
allocator is part of the fn's *ABI contract* (constraint: "some OTHER C
(or future Rust) code calls kfree() on this pointer"). `virtio_ring.c`'s
unsafe surface is a different shape: not an ownership-crossing allocator
contract, but a **live, ongoing shared-memory protocol** — descriptor
rings and available/used indices are continuously read and written by
both the driver and an external, concurrently-running device (real
hardware or QEMU's device model) for the entire lifetime of the queue,
synchronized only by memory barriers and the virtio spec's own ordering
rules, not by any lock Rust's type system could see. This is closer in
spirit to 8250's Tier B/C (real concurrent hardware interaction) than to
0018's one-shot allocate/hand-off/free pattern, but even more
concurrency-heavy: 0018 is "C allocates, C frees, Rust must not
interfere" (a single ownership handoff); `virtio_ring.c`'s pattern is
"C and an external device both touch this memory forever, synchronized
by barriers, not locks" (an ongoing bilateral relationship, no clean
handoff point at all). **A rule in 0018's spirit (permanent unsafe FFI
shim, not a translation target) applies more strongly here than 0018
itself does** — the mechanism differs (barriers/shared-mem-protocol vs.
allocator-ABI-contract) but the conclusion ("don't try to make this
Rust-safe, wrap it and move on") is the same class of call.

### Upstream Rust-for-Linux virtio prior art

Checked directly, not assumed absent:

```
$ gh api repos/Rust-for-Linux/linux/git/trees/rust-next?recursive=true \
    --jq '.tree[].path' | grep -i virtio
```

Zero `rust/kernel/virtio*` or `rust/kernel/**/virtio*` paths in the
current `rust-next` tree — confirms this project's own vendored
`rust/kernel/` (also zero virtio files) is not missing something that
exists upstream today.

But `gh search prs "virtio" --repo Rust-for-Linux/linux` surfaces real,
if stale, prior art:

- **PR #841**, "virtio: provide a rust interface of virtio_driver in
  rust" — **open**, created 2022-07-29, last updated **2024-05-28**,
  base `459035ab` on the old `rust` branch (pre-`rust-next`, i.e.
  predates the current branch structure entirely), `mergeable_state:
  dirty`. 3 files, +218/-0. Fetched the actual patch content
  (`rust/kernel/virtio.rs`, 211 lines): implements **driver
  registration only** — `Device`, `Driver` trait (`probe`/`remove`),
  `DeviceId`/`ID_TABLE`, `Adapter`/`Registration` wiring into
  `bindings::register_virtio_driver`. Uses APIs already superseded in
  current Rust-for-Linux (and absent from this project's own crate):
  `PointerWrapper` (replaced by `ForeignOwnable`), `from_kernel_result!`
  (replaced by `from_result`), `unsafe impl const driver::RawDeviceId`
  (`const` trait impls are no longer how this is done). **Does not
  touch `vring_*`/`virtqueue_*` at all** — it's the `struct
  virtio_driver` probe/remove/id-table registration layer only, one
  level up from where `virtio_blk.c`'s own logic lives, and zero levels
  into the descriptor-ring core. Not adoptable as-is; useful only as
  distant conceptual precedent that driver-registration-shaped virtio
  bindings are a tractable Rust shape.
- **PR #886**, "Rust virtio net support" — **open**, created
  2022-09-21, last updated **2022-11-15** (over 3 years stale at time of
  this check), base same era as #841, `mergeable_state: dirty`,
  +1326/-2 across 6 files. Not fetched in detail (staler than #841 and a
  net-specific consumer of the same superseded registration API) but
  its existence is itself signal: two independent stale attempts at the
  registration layer, neither reaching virtqueue/descriptor-ring
  abstractions, both abandoned mid-2022-to-2024.

Unlike the VFS case (`docs/tmpfs-rust-scoping-2026-07-18.md`'s PR #1037,
base commit a **direct ancestor** of this project's HEAD, actively
maintained through 2026-06), **neither virtio PR is in this project's
history at all** — both predate `rust-next`'s existence as the staging
branch this project's own tree descends from. This is a meaningfully
worse starting position than the VFS RFC: not a rebase-and-adapt
candidate, a from-a-different-era reference at best.

## Answers

**(1) Is `virtio_blk.c` alone a viable near-term translation TU using the
existing `rust/kernel/block/mq/` abstraction? Partial yes, with two
concrete blockers to clear first.** The hot path (`queue_rq`/
`commit_rqs`/`complete`/`poll`, ~15-20 of the 47 functions, Tier A+B
above) maps cleanly onto `Operations` — better-attached than 8250 was at
its equivalent scoping stage, because the trait and a working reference
driver (`rnull`) already boot in this tree. But: (a) `CONFIG_VIRTIO*`
needs merging from the `block-layer-enable` worktree into main so the
file is actually built and enters the c2rust baseline corpus; (b) a
real (non-synthetic) c2rust run against it will need to be re-attempted
once it's genuinely compiled, and the new `arg_tys.len() == exprs.len()`
panic found here needs root-causing/filing against `awtoau/c2rust` — it
blocked this pass's dry-run before reaching a clean AST export; (c) the
device-lifecycle half of the driver (freeze/restore/reset/map_queues,
Tier C, ~15 of 47 functions) has no `Operations` trait hook yet and
would need the trait extended before a *complete* port is possible —
translating only the hot path would produce a driver that can't
suspend/reset, which is not "done."

**(2) Is the virtqueue core worth porting to Rust, or is it a permanent
unsafe FFI boundary like rule 0018's allocator contract?** **Permanent
FFI boundary, not a translation target** — with a transport-layer
caveat. `virtio_ring.c` (3983 lines, the actual descriptor-ring core) is
lock-free shared-memory protocol code: 14 explicit memory barriers, 23
`READ_ONCE`/`WRITE_ONCE`, 4 DMA map/unmap sites, ~40 raw descriptor-table
manipulations, synchronizing with a concurrently-running external device
with no lock Rust's borrow checker can model. This is a stronger case
for "wrap, don't translate" than rule 0018 itself (0018 is a one-shot
allocator-ABI handoff; this is an ongoing bilateral shared-memory
relationship for the queue's entire lifetime) — the same conclusion,
better evidence. `virtio.c` (739 lines, registration/feature
negotiation) is comparatively translatable — no barriers, no DMA, mostly
device-model/list/refcount glue — but low value in isolation since it
has no meaning without the ring core it sits on top of. `virtio_mmio.c`
(829 lines) is real register-poking (59 `readl`/`writel`-family calls)
and belongs in the same "FFI shim" bucket as 8250's Tier B for the same
reason: it's the literal MMIO-register boundary, appropriately unsafe by
construction, not a Rust-safety win to chase.

**(3) Recommended next concrete step.** Not "translate virtio_ring.c" —
that's a from-scratch unsafe-abstraction-design project comparable in
risk to the tmpfs doc's option (a) (VFS-from-scratch), for a component
this pass's own evidence says should stay a wrapped FFI boundary anyway.
Recommended instead, sized like the 8250 first slice:
1. Merge `CONFIG_BLOCK`/`CONFIG_VIRTIO`/`CONFIG_VIRTIO_MMIO`/
   `CONFIG_VIRTIO_BLK` from `linux-riscv-worktrees/block-layer-enable`
   into main's `.config` (already proven to boot cleanly, 17 ok/0 not-ok,
   per `docs/block-layer-enable-2026-07-19.md`) — this alone makes
   `virtio_blk.c` and `drivers/virtio/{virtio,virtio_ring,virtio_mmio}.c`
   real corpus members for the first time, unblocking a genuine (not
   synthetic) c2rust baseline run.
2. Re-run c2rust against `virtio_blk.c` with the real compile_commands.json
   entry that produces; root-cause and file the new
   `arg_tys.len() == exprs.len()` `convert_call_args` panic found in this
   pass against `awtoau/c2rust` (22 issues currently tracked, this is a
   23rd) — it's a hard blocker for any real transpile attempt on this
   file, independent of which functions get chosen for a first slice.
3. Once clean-AST-exports, scope a first translation slice from Tier A
   only (`virtblk_result`, `index_to_minor`/`minor_to_index`,
   `virtblk_getgeo`, `virtblk_name_format` — pure, no virtqueue/DMA call,
   diff-oracle-able the same way `bench/diff_8250_helpers.{c,rs}` proved
   out `serial8250_compute_lcr`), explicitly deferring Tier B (virtqueue
   calls, needs `Operations` extended for lifecycle hooks first) and
   Tier C (device lifecycle, blocked on the trait gap identified above)
   to their own follow-up passes — same phased structure the 8250 doc
   used, same reason (large stateful file, small pure island first).

Priority: lower than 8250 (which is boot-console-critical and already
has an integration slice) — comparable to or slightly above tmpfs's
option (c) (`shmem.c` standalone translation practice), since virtio_blk
at least has somewhere real to attach (`Operations`, already proven with
`rnull`) once the Kconfig-merge and c2rust-panic blockers above are
cleared, which `shmem.c` does not.
