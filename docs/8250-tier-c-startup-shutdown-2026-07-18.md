# 8250 Tier C: serial8250_do_startup/serial8250_do_shutdown — landed, live-wired — 2026-07-18

Status: **achieved**, a first real slice of Tier C for
[awto-au/linux-rs#25](https://github.com/awto-au/linux-rs/issues/25). Issue
#25 stays **open** (its own text: standing issue for ongoing stream-2 work;
`set_termios`, the IRQ RX/TX path, and `autoconfig` remain, each its own
future scope). Kernel commit: `b5f563ef3ad8` on `linux-rs/phase2-gcd`,
pushed to `awtoau/linux`. TU 38.

## Why this attempt succeeded where the prior one stopped

`docs/8250-tier-c-blocker-2026-07-18.md` (the immediately prior attempt at
this issue) read all five Tier C candidates, ranked
`serial8250_do_startup`/`serial8250_do_shutdown` as least-bad, and then
correctly stopped: both call real `request_irq()`/`synchronize_irq()`/
`free_irq()` with no meaningful KUnit fake possible — a fake can stand in
for *data* (Tier B's register buffer) but not for "the interrupt subsystem
accepted this registration," and building that fake was explicitly out of
scope for that pass.

Dan revisited that verdict on 2026-07-18 and made a deliberate, scoped,
explicitly-documented exception for exactly this pair of functions: they
may be translated and wired into the LIVE `.startup`/`.shutdown` path
*without* KUnit coverage, provided (1) the lack of coverage is prominently
documented in the code, citing the blocker doc's reasoning, and (2) the
byte-for-byte side-by-side boot-transcript comparison gate from
`docs/serial-8250-translation-scoping-2026-07-18.md` — the one KUnit can't
replace — is kept and actually run, multiple times. This doc records that
translation and that comparison. The exception is scoped to this pair of
functions only; it does not extend to `set_termios`, the IRQ path, or
`autoconfig` without being separately re-confirmed for each.

## What was translated

`serial8250_do_startup()`/`serial8250_do_shutdown()` control flow —
branching, call ordering, early-return error handling — to
`drivers/tty/serial/8250/8250_startup_rs.rs`. Every branch and call
ordering matches the C originals line-for-line (verified by direct
side-by-side reading against the `#else` arm, which keeps the original C
bodies verbatim for exactly this comparison).

### Why `struct uart_port`/`struct uart_8250_port` stay opaque

Neither struct has bindgen bindings anywhere in this tree (confirmed:
neither type appears in `rust/bindings/bindings_helper.h`). Both are large,
deeply nested (embedded spinlock, function-pointer op tables, `list_head`,
DMA/RS485 sub-structs). Generating full bindgen coverage for a first
driver-control-flow TU would be a separate, materially larger undertaking
with its own correctness risk, and contrary to this project's established
discipline of never letting Rust assume ownership/layout of a struct it
doesn't own (the same reasoning `docs/8250-tier-b-scoping-2026-07-18.md`
applied to `uart_port.membase` vs. `kernel::io::Mmio`).

Instead, `port` stays an opaque `*mut c_void` in
`8250_startup_rs.rs`. Every field read/write and every subsystem call the C
originals perform is exposed as a narrow, individually-named,
individually-auditable `extern "C"` shim function in `8250_port.c`
(`serial8250_startup_rs_*`, 44 functions, under the same
`CONFIG_RUST_8250_STARTUP` block) — a one-for-one mechanical mirror of each
line of the C bodies, not a reimplementation or simplification. The Rust
side carries the control flow only; every register access, lock
acquisition, and subsystem call happens on the C side exactly as it always
did. `upf_t` flag-bit tests (`UPF_BUGGY_UART`, `UPF_SHARE_IRQ`,
`UPF_FOURPORT`) are deliberately evaluated in the C shim rather than ported
as Rust constants: unlike Tier A's `tcflag_t` bits, `upf_t` is a
project-internal bitflag type (some bits `BIT_ULL`-defined) with no stable
UAPI pin, so evaluating the test in C means its width/encoding can never
silently drift between the two languages — only the resulting `bool`
crosses the FFI boundary.

The shim functions initially used `static` linkage (matching most of this
file's other internal helpers) but needed removing: the Rust object file
and `8250_port.o` are separate compilation units, so a `static` shim is
invisible to the linker from the Rust side — this was caught immediately by
the first link attempt (`undefined symbol: serial8250_startup_rs_*`) and
fixed before any boot testing.

## NO KUNIT COVERAGE — by design, documented prominently in-code

Both `8250_port.c`'s `CONFIG_RUST_8250_STARTUP` block and
`8250_startup_rs.rs`'s module doc carry the same explanation, matching this
task's explicit requirement: `serial8250_do_startup` calls (via the shim)
`up->ops->setup_irq(up)`, which for this project's actual driver
(`univ8250_driver_ops`, `8250_core.c`) resolves through
`serial_link_irq_chain` to a real `request_irq()` against a live IRQ line
with real shared-IRQ bookkeeping; `serial8250_do_shutdown` calls
`synchronize_irq(port->irq)` — blocks on in-flight interrupt completion,
meaningless without a real interrupt controller — then `up->ops->release_irq`
through `serial_unlink_irq_chain` to `free_irq`. Both take the real port
spinlock. Per `docs/8250-tier-c-blocker-2026-07-18.md`, faking any of this
would mean reimplementing genuine IRQ-core semantics inside the test,
verifying the fake against itself rather than the driver against anything
real.

## The gate that replaces KUnit here: byte-for-byte transcript comparison

Two separate kernel images were built from the isolated worktrees
`linux-riscv-worktrees/8250-tier-c` (C path, `CONFIG_RUST_8250_STARTUP`
unset — the new Kconfig option's default) and `8250-tier-c-rust`
(`CONFIG_RUST_8250_STARTUP=y`, wiring the Rust path into
`.startup`/`.shutdown`), created via `linux_riscv_worktree.py` per the
task's explicit instruction given the elevated risk (this is the live
console driver).

**Why a new Kconfig option, not `CONFIG_RUST`:** `CONFIG_RUST` already
gates the landed, already-verified Tier A/B translations in this same
driver (`8250_helpers_rs.o`, `8250_io_rs.o`). Toggling it off to get a
"pure C" comparison build would have also reverted Tier A/B, comparing the
wrong thing. `CONFIG_RUST_8250_STARTUP` (new, `depends on SERIAL_8250 &&
RUST`, default `n`) is independently toggleable, added to
`drivers/tty/serial/8250/Kconfig`.

**Both `serial8250_do_startup`/`serial8250_do_shutdown` themselves** — the
`EXPORT_SYMBOL_GPL` functions that are the actual `.startup`/`.shutdown`
targets via `serial8250_startup`/`serial8250_shutdown` — are wrapped
`#ifdef CONFIG_RUST_8250_STARTUP` / `#else` in `8250_port.c`, same pattern
Tier A used for `serial8250_compute_lcr`. The `#else` arm is the untouched
original C bodies, kept verbatim.

Each image was booted **3 times** with distinct `--run-id`s
(`tierc-c-{1,2,3}`, `tierc-rust-{1,2,3}`), all archived under
`docs/status/boot-logs/`. All 6 boots passed independently: 17/17 KUnit
suites, `ORACLE PASS`, `INIT REACHED`, zero `not ok` lines.

### Diff methodology

Per-line `TS_PREFIX_RE` (from `scripts/kunit_oracle.py`:
`r"(?:\d{5}\.\d{3} )?"`) stripped from every log before comparison — the
per-line elapsed-time prefix this project's boot logs have carried since
earlier the same day. All 15 pairwise combinations were diffed (3 within
C-path, 3 within Rust-path, 9 cross C-vs-Rust).

### Result: clean

**From the `KTAP version 1` line (start of KUnit output) through the end of
every transcript — all 17 KUnit suite results, the `# Totals` line, and the
`linux-rs: initramfs init reached, PID 1 alive` marker — all 6 logs are
byte-for-byte identical**, 362 lines each, zero deviation, across all 6
logs including every C-vs-Rust cross-comparison. This is the region that
actually reflects what happens *after* `serial8250_do_startup` runs (the
KUnit suites execute post-console-bring-up), so it's the strongest possible
signal that the two startup/shutdown paths produce indistinguishable
downstream behavior.

Before that point (early boot — OpenSBI banner, memory probe, initramfs
unpack), the diffs fall into exactly three categories, each confirmed
benign by checking whether it *also* appears **within** repeated boots of
the *same* binary (proving it's boot-to-boot jitter, not something this
change introduced):

1. **Line-order jitter**: `Unpacking initramfs...` / `riscv-plic: ...
   mapped 96 interrupts` / `Freeing initrd memory: 108K` / `printk: legacy
   console [ttyS0] disabled` / `10000000.serial: ttyS0 at MMIO ...`
   reorder relative to each other between runs. Confirmed present in
   C-path-vs-C-path and Rust-path-vs-Rust-path comparisons at the same
   rate as C-vs-Rust — pure QEMU early-boot interleaving timing, the exact
   phenomenon `docs/hybrid-boot-milestone-8250-trigger-2026-07-18.md`
   already documented for the Tier A landing.
2. **`cpu0: scalar unaligned word access speed is N.NNx byte access
   speed`** — a runtime-measured micro-benchmark value (`7.07x` to
   `8.14x` observed across all 6 boots). Varies within-variant exactly as
   much as across-variant; not a function of which binary is running.
3. **Two artifacts expected from comparing genuinely different binaries**:
   the `-kernel <path>` line in the recorded QEMU invocation (each build
   lives in its own worktree path) and the `Linux version ... <build
   timestamp>` banner line (each `make` run has its own wall-clock
   timestamp). `Memory: ... (1771K kernel code ...)` vs `(1772K kernel
   code ...)` is a deterministic, real 1KB `.text` size difference between
   the two binaries — the Rust-wired build genuinely contains more code —
   confirmed stable across all 3 repeats of each variant (never varies
   within a variant, only across).

No diff touches anything downstream of `KTAP version 1`, no diff drops,
corrupts, or reorders any line that carries real information (KUnit
results, error messages, the init-reached marker). Per the task's explicit
instruction, a dirty diff here would have meant reverting to
compiled-but-inert; this diff is clean, so the live wiring stands.

## Verification: `dev.py check`

Run on the canonical shared `linux-riscv/` tree after fast-forwarding
`linux-rs/phase2-gcd` to the landed commit and enabling
`CONFIG_RUST_8250_STARTUP=y` (the live-wired, landed configuration):

```
SPDX provenance: 37 pass, 0 fail, 0 warn (of 37 translated files)
SPDX PROVENANCE PASS
REPORT OK: 37 TUs, 17 suites, 147 vectors, 29 rules
```

(`dev.py check`'s `boot()` step, which gates on zero `not ok` KUnit lines
and the `INIT REACHED` marker, ran as part of this and passed — `sh()`
`sys.exit()`s on any nonzero return code, so a failure here would have
aborted before the SPDX/report output above.) `8250_startup_rs.rs` needed a
new `check_spdx_provenance.py` `EXCEPTIONS` entry (same mechanism as
`8250_helpers_rs.rs`/`8250_io_rs.rs`: it translates from `8250_port.c`, not
a same-named `.c` file).

Symbol linkage confirmed the same way prior Tier A/B milestones did:
`nm vmlinux.unstripped` shows `serial8250_do_startup_rs`/
`serial8250_do_shutdown_rs` as global text (`T`); `nm
drivers/tty/serial/8250/8250_port.o` shows both as undefined (`U`)
references — the C wrapper genuinely calls out to them, not a dead
declaration.

## What this does and doesn't establish

**Does:** a first Tier C slice is now live-wired, not just
compiled-in-and-inert — the strongest integration state any translation in
this project has reached for a function with zero KUnit coverage,
justified by the transcript-comparison gate this doc documents. Confirms
the "opaque pointer + narrow extern shim" pattern as a viable way to
translate control-flow-heavy driver code without bindgen-ing large C
structs this project doesn't own.

**Doesn't:** issue #25 stays open. `serial8250_do_set_termios` (explicitly
deferred by the issue's own text, same real-lock/register entanglement),
the IRQ RX/TX path (`serial8250_handle_irq_locked` — runs on every live
interrupt, zero margin), and `autoconfig` (order/timing-fragile probing,
the worst-ranked candidate) remain entirely untouched, in C, for future
slices of this same standing issue. This task's KUnit exception is scoped
to exactly `serial8250_do_startup`/`serial8250_do_shutdown`; it is not a
precedent that any other function in this driver — or elsewhere — gets to
skip KUnit without its own explicit re-confirmation from Dan.

## Worktree cleanup

Both isolated worktrees (`linux-riscv-worktrees/8250-tier-c`, C-path
baseline; `8250-tier-c-rust`, the landed Rust-path build) are removed after
landing — the commit lives on `linux-rs/phase2-gcd`, `awtoau/linux`, and
`patches/0030-*.patch`.
