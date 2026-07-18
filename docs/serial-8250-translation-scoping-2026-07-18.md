# 8250/16550 serial driver translation — scoping

Status: planning only. No changes to `linux-riscv/drivers/tty/serial/8250/*`
in this pass. A standalone diff-oracle harness for the first proposed slice
(`bench/diff_8250_helpers.{c,rs}`) has been built and passes byte-identical
over 7500 generated cases — see "Verification gate" below.

## Why this TU is different from the 30 landed so far

Every hand-translated TU to date (`kstrtox_rs.rs`, `bitmap_rs.rs`,
`string_rs.rs`, `cmdline_rs.rs`, etc. — see `linux-riscv/lib/*_rs.rs`) is a
`lib/`-style utility file: pure(-ish) functions operating on caller-supplied
buffers/integers, testable in complete isolation, and — critically — **not
on the critical path for observing the test result itself**. If one of them
had a subtle bug, `dev.py check`'s KUnit gate (zero `not ok` lines) would
catch it directly.

8250 is different on two axes at once:

1. **Scale.** `8250_port.c` alone is 3472 lines — roughly 20x the largest
   `lib/` TU landed so far (`bitmap_rs.rs` translates from a ~1000-line
   source and is itself the biggest Rust file in `linux-riscv/lib/` at 27K).
   A "translate the file" task is not realistically scoped for one pass.
2. **Self-referential risk.** This driver *is* the console. QEMU's virt
   board exposes an ns16550a-compatible UART; `console=ttyS0` on the kernel
   cmdline (see `scripts/boot_qemu.py`) routes all boot/kernel output through
   it; every KUnit result this project has ever read came through this exact
   C driver. `dev.py check`'s pass/fail gate (grep for `not ok` in captured
   serial output) is a *consumer* of this driver's correctness. A
   translation bug that garbles, drops, or reorders bytes on the TX path
   could still let the kernel boot — while silently producing a serial log
   that no longer means what the test harness assumes it means. That is a
   failure mode none of the previous 30 TUs could produce: this is the one
   place where "boot-verified, zero not-ok KUnit lines" is not sufficient
   reassurance on its own, because the thing being checked and the thing
   doing the checking would be the same component.

This changes the integration discipline required: the live console driver
must not be swapped in one shot, and any verification plan has to include a
tier that does not depend on the driver-under-test to report its own result.

## Driver family structure

`linux-riscv/drivers/tty/serial/8250/` is 28.5K lines across 47 files.
Almost all of it is irrelevant to this project:

| File | Lines | Relevance |
|---|---:|---|
| `8250_port.c` | 3472 | **Core.** Register-level UART logic + `struct uart_port` op-table implementations. The only file with genuine 16550-hardware content. |
| `8250_core.c` | 900 | Driver registration/module init, `uart_driver` boilerplate, `/proc` interface glue. Framework, not hardware. |
| `8250_of.c` | 374 | OF/device-tree probe — **this is the actual entry point for QEMU's virt board.** |
| `8250_dma.c` | 342 | DMA-capable variants. Not used by QEMU virt (plain PIO ns16550a). Out of scope entirely. |
| `8250.h` | 471 | Shared internal declarations/inline helpers across the family. |
| `8250_early.c`, `8250_platform.c` | 211, 386 | Early console / ACPI-platform-device paths. QEMU virt uses OF probing, not these. Out of scope. |
| `8250_pci.c` | 6330 | PCI 16550 variants (largest file in the directory). Zero relevance — QEMU virt's UART is a plain MMIO platform device, no PCI enumeration involved. |
| `8250_dw.c`, `8250_omap.c`, `8250_exar.c`, `8250_bcm7271.c`, `8250_fintek.c`, `8250_mtk.c`, and ~25 more vendor files | ~15K combined | Sub-drivers for specific SoC/vendor 16550 variants (DesignWare, TI OMAP, Exar, Broadcom, Fintek, MediaTek, Ingenic, Loongson, Tegra, uniphier, ...). **None of these are compiled into this project's kernel** (`CONFIG_SERIAL_8250_DW` etc. are unset in `linux-riscv/.config` except `CONFIG_SERIAL_8250_16550A_VARIANTS=y`, which only affects `uart_config[]` table entries inside `8250_port.c` itself, not a separate sub-driver). |

Confirmed via `linux-riscv/.config`:
```
CONFIG_SERIAL_8250=y
CONFIG_SERIAL_8250_16550A_VARIANTS=y
CONFIG_SERIAL_8250_CONSOLE=y
CONFIG_SERIAL_8250_NR_UARTS=4
CONFIG_SERIAL_8250_RUNTIME_UARTS=4
CONFIG_SERIAL_OF_PLATFORM=y
# CONFIG_SERIAL_8250_FINTEK is not set
# CONFIG_SERIAL_8250_EXTENDED is not set
# CONFIG_SERIAL_8250_DW is not set
# CONFIG_SERIAL_8250_RT288X is not set
```
So the real dependency surface for this project is exactly:
**`8250_port.c` (core logic) + `8250_of.c` (probe/registration) + `8250.h`
(shared decls)** — roughly 4300 lines, not 28,500. `8250_core.c` is also
linked in (module init / `uart_driver` registration) but contributes no
16550-specific logic of its own.

### Within `8250_port.c`: hardware logic vs framework plumbing

Grepping the ~150 top-level functions in `8250_port.c` splits cleanly into
three tiers by translation risk:

**Tier A — pure, register-map-shaped, no I/O, no locking, no interrupts.**
Genuinely 16550-specific arithmetic with zero control-flow risk:
- `serial8250_compute_lcr()` (line 2517) — termios cflag -> LCR byte.
- `fcr_get_rxtrig_bytes()` / `bytes_to_fcr_rxtrig()` (lines 2973, 2983) —
  FCR RX-trigger-level lookup against the per-UART-type `rxtrig_bytes[]`
  table in `uart_config[]` (line ~45).
- `serial8250_do_get_divisor()`'s pure-arithmetic core (line 2453, though it
  calls the non-static `uart_get_divisor()` from `serial_core.c` — that call
  would need to be a stub/trait boundary, not translated in this slice).
- The `serial8250_config` / `uart_config[]` static table itself (line 44) —
  data, not logic, but worth carrying alongside whichever functions index it.

**Tier B — real register I/O, still narrowly scoped, but now `unsafe` and
untestable by pure diff-oracle (needs actual MMIO or a KUnit mock):**
- `mem_serial_in`/`mem_serial_out` and siblings (`mem16_*`, `mem32_*`,
  `mem32be_*`, `io_serial_in/out`, `hub6_*`, `no_serial_in/out`) — lines
  334-417. These are the actual `readb`/`writeb`-family register accessors.
  For QEMU virt's ns16550a device specifically, only the `UPIO_MEM` variant
  (`mem_serial_in`/`mem_serial_out`, using `readb`/`writeb`) is ever
  selected — confirmed via `set_io_from_upio()` (line 421), which is what
  the `8250_of.c` probe path resolves through `uart_read_and_validate_port_properties()`.
  The other five iotype variants (hub6, 16-bit, 32-bit, 32-bit-BE, no-op)
  are dead code for this project's single target device.
- `default_serial_dl_read`/`default_serial_dl_write` (line 317) — divisor
  latch read/write, built on top of the above.

**Tier C — stateful control flow, interrupt-context code, tty-layer
integration. This is the large, high-risk majority of the file:**
`serial8250_do_startup`/`serial8250_do_shutdown` (device bring-up/teardown,
~150 lines combined, touches IRQ registration, DMA setup, RS-485 timers),
`serial8250_handle_irq_locked`/`serial8250_rx_chars`/`serial8250_tx_chars`
(the actual interrupt-driven RX/TX path — this is what's live every time
any test output is captured), `serial8250_do_set_termios` (~90 lines,
orchestrates baud/LCR/FCR/IER programming with hardware-timing-sensitive
ordering), `autoconfig()`/`autoconfig_16550a()` (hardware probing/detection
via register read-back sequences, order-dependent and timing-sensitive),
console write path (`serial8250_console_write`, `serial8250_console_restore`
— the exact code that would replace the live console driver), and all
`struct uart_ops`-table wiring back into the generic tty/serial_core
framework. None of this belongs in a first slice.

## What QEMU's virt board actually exercises

Traced via `8250_of.c` (`of_platform_serial_probe` -> `of_platform_serial_setup`):
compatible string `"ns16550a"` matches `of_platform_serial_table[]`
(`8250_of.c` line ~296) -> `port_type = PORT_16550A` -> generic path (no
vendor `.setup` callback fires; the `switch (type)` in
`of_platform_serial_setup` only special-cases `PORT_RT2880`/`PORT_NPCM`,
neither of which is `PORT_16550A`) -> `port->iotype` resolves to `UPIO_MEM`
from the device-tree `reg` property being a `mapbase`, not `iobase` ->
`set_io_from_upio()` wires `p->serial_in = mem_serial_in`,
`p->serial_out = mem_serial_out` -> `serial8250_register_8250_port()`
(`8250_port.c`) hands off into the generic startup/IRQ/termios machinery in
Tier C above.

So the **concrete boot-critical function set** for this project's actual
target device is: `8250_of.c`'s OF-match table + probe/setup (already
small, 374 lines total, of which maybe 60 lines are exercised for a plain
`ns16550a` match) — plus, from `8250_port.c`, exactly the `UPIO_MEM` branch
of Tier B, all of Tier A (used by `do_set_termios`/`do_startup` internally),
and the full Tier C control-flow (startup, IRQ, RX/TX, termios, console
write). No PCI, no DMA, no vendor sub-driver, no hub6/16-bit/32-bit iotype
variants, no ACPI/platform-device path, no RS-485, no runtime PM beyond the
no-op `pm_runtime_*` calls OF invokes.

## Risk assessment

**Why this is unlike every prior TU:** the previous 30 translations could
be wrong and the test harness would tell you. This one can be wrong and the
test harness might not — because a console-output bug undermines the very
channel `dev.py check` reads to decide pass/fail. Concretely:
- A bug that **drops bytes** on TX could make a real KUnit failure line
  silently disappear from the captured log, turning a red build green.
- A bug that **corrupts bytes** (off-by-one in FIFO trigger levels, wrong
  LCR word-length bit, wrong baud divisor) could produce garbled but still
  `grep`-matchable output that happens to not contain `not ok` by accident,
  again false-passing.
- A bug in the **RX path** (irrelevant to KUnit output capture, but relevant
  if this project ever adds interactive/scripted stdin-driven testing) has
  lower immediate risk since nothing currently reads kernel stdin.
- A bug that hangs or corrupts **early boot output** could be mistaken for
  an unrelated boot failure, costing debugging time even if eventually
  caught (rather than silently passing).

Given this, "zero `not ok` KUnit lines" is necessary but explicitly **not
sufficient** as the gate for this TU, unlike every other TU landed so far.
Any real translation attempt needs an additional verification tier that
does not route through the translated driver to check its own output —
e.g. a host-side diff-oracle (works for Tier A, this pass's harness), a
QEMU-external observation channel (e.g. comparing byte-for-byte against a
simultaneously-run unmodified-C-driver boot log, or capturing via a QEMU
monitor/pty tap independent of the guest's own idea of "did it work"), or
staged coexistence (new Rust code compiled in and unit-tested via KUnit,
but the live `console=` path still bound to the untouched C driver) before
ever making the Rust code reachable from the boot cmdline.

## Rust-for-Linux prior art

No 8250/16550/serial/tty/UART-related code found in this project's vendored
`linux-riscv/rust/` tree (searched for `serial`, `uart`, `8250`, `tty` —
zero matches). Rust-for-Linux upstream has, as of this search, **not
landed an in-kernel 8250/16550 driver using the `kernel` crate abstractions**
— there is no `rust/kernel/serial.rs` or equivalent, and no sample/driver in
`samples/rust/` targeting a UART. This is a genuine gap, not an oversight in
the search: Rust-for-Linux's driver-porting effort to date has concentrated
on other subsystems (e.g. Android Binder, NVMe, PHY drivers, network PHY,
DRM/Nova for GPU) rather than tty/serial.

What does exist (found via DuckDuckGo, not part of the `kernel` crate and
not directly reusable as translation reference, but relevant as prior art on
*how other Rust projects have modeled a 16550*):
- `uart_16550` (rust-osdev, `crates.io`) — a `no_std` bare-metal crate
  providing register-bit-level read/write for a 16550, used by hobby/OS-dev
  kernels (e.g. Redox-adjacent projects). Standalone port-IO/MMIO wrapper,
  no Linux `kernel` crate integration, no tty-layer concepts at all — closer
  in spirit to what this project's Tier A/B slice would look like in
  isolation, but not a drop-in reference for the *driver* (startup, IRQ,
  termios) logic.
- `ns16550a` (`docs.rs`) — similar scope, another standalone register
  wrapper crate.
- `NS16550A` (`github.com/jeudine`) — another `no_std` driver, embedded
  target-focused.

None of these integrate with `struct uart_port`/`uart_driver`/tty-core
concepts, so none of them de-risk Tier C. They're mild positive signal that
the *register-level* modeling (Tier A/B, this pass's actual scope) maps
cleanly onto idiomatic Rust — multiple independent projects converged on
similar bitflag-struct-plus-accessor designs for the same hardware.

## Proposed first slice

**In scope for the first real translation pass:**
- `serial8250_compute_lcr()` — cflag -> LCR byte.
- `fcr_get_rxtrig_bytes()` / `bytes_to_fcr_rxtrig()` — FCR RX-trigger lookup.
- The `uart_config[]` static table (or at minimum the `PORT_16550A` /
  `PORT_16550` entries QEMU virt can actually produce), as Rust `const` data.
- Optionally, `tty_get_char_size()` (technically owned by `tty_ioctl.c`, not
  8250, but is a two-line dependency of `serial8250_compute_lcr` with no
  further dependencies — translate alongside it or stub it, translator's
  call).

**Explicitly NOT attempted in this first pass** (left for subsequent
slices, in rough order of what would come next): the `mem_serial_in`/
`mem_serial_out` MMIO accessors (Tier B — needs `unsafe`, needs a decision
on how `readb`/`writeb` bindings are exposed, needs KUnit rather than
diff-oracle for verification); `serial8250_do_startup`/`_do_shutdown`;
`serial8250_do_set_termios`; the IRQ path (`serial8250_handle_irq_locked`,
`_rx_chars`, `_tx_chars`); `autoconfig`/`autoconfig_16550a`; the console
write path (`serial8250_console_write` et al. — the actual code that would
ever touch the live `console=ttyS0` binding); and all of `8250_of.c`'s
probe/registration glue. None of this is wired into Cargo/Kbuild, and
nothing under `linux-riscv/drivers/tty/serial/8250/*.c` is modified.

**Verification gate for this first slice (met):**
`bench/diff_8250_helpers.c` / `bench/diff_8250_helpers.rs` — a standalone
host-side diff-oracle pair, following the exact pattern of every other
`bench/diff_*.{c,rs}` file (e.g. `diff_bcd.c`/`.rs`), run via
`scripts/diff_oracle.py 8250_helpers`. Result:

```
ORACLE 2.5 PASS: 8250_helpers — 7500 cases, 15000 output lines, byte-identical
```

7500 generated cases (5000 termios-cflag combinations biased toward
realistic `CS5`-`CS8`/`CSTOPB`/`PARENB`/`PARODD`/`CMSPAR` combinations plus
raw-fuzzed cflags for `serial8250_compute_lcr`; 5000 each for
`fcr_get_rxtrig_bytes`/`bytes_to_fcr_rxtrig` across three representative
UART-type trigger tables), byte-identical output between the C reference
(extracted verbatim from `8250_port.c`/`tty_ioctl.c`/`serial.h`/
`serial_reg.h`) and a faithful Rust port. This is Tier 2.5 only — it does
**not** touch Cargo/Kbuild integration, does not run under KUnit, and does
not go anywhere near the live console path. The harness lives at
`bench/diff_8250_helpers.{c,rs}` for whoever picks up the real translation
next to build on (its header comment documents exact provenance).

**Gate for the NEXT slice (Tier B, register I/O) before it's considered
"boot-verified" in the normal sense this project uses:** KUnit coverage
exercising the accessor functions against a mock/fake register backing
(not real MMIO — no hardware side effects to fake, just byte-shuffling
correctness) PLUS the standard `dev.py check` boot-and-KUnit gate, run
while the Rust code is compiled in but *not* bound to `console=` — i.e.
reachable and unit-testable, but inert with respect to the boot console.

**Gate before Tier C / the live console path is EVER attempted (the one
that differs from every prior TU's process):** in addition to the normal
`dev.py check` boot-verify gate, an explicit side-by-side comparison run —
boot the same kernel/initramfs twice, once with the unmodified C driver and
once with the Rust replacement bound to `console=`, and diff the two
captured serial transcripts byte-for-byte (modulo expected non-determinism
like timestamps/PIDs). Only bind the Rust driver to the live console after
that comparison is clean across multiple runs, and even then, consider
keeping the C driver as a documented fallback (e.g. a build-time or
cmdline-selectable choice) for at least one full session before treating it
as the sole console path — because this is the one component whose failure
mode is "the thing that would tell you it failed can no longer tell you
anything."

## Summary for whoever picks this up

Don't translate `8250_port.c`. Translate `serial8250_compute_lcr`,
`fcr_get_rxtrig_bytes`, and `bytes_to_fcr_rxtrig` (plus the `uart_config[]`
table), verify against `bench/diff_8250_helpers.{c,rs}` (already
passing), and stop there for this TU. Integrate via `scripts/integrate_tu.py`
as a normal `lib/`-adjacent addition — it does not need to be reachable from
`8250_port.c`'s actual call sites yet; landing it as dead-but-compiled,
KUnit-exercised code is a legitimate and appropriately cautious TU 31.
Getting from there to an actual live-console swap is a multi-TU project of
its own, gated as described above, and should be scoped fresh once Tier A
has landed and this project has a first real data point on how a
device-driver-shaped TU behaves in the existing pipeline.
