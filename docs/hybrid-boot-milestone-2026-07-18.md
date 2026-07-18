# Hybrid boot-path milestone — 2026-07-18

Status: **achieved**. Rust code now executes on the real boot path via a
mechanism other than the 30 whole-file `lib/*_rs.o` TU swaps: a single
function inside a live device driver (`drivers/tty/serial/8250/8250_port.c`)
now calls into a Rust implementation.

## What changed

`linux-riscv/drivers/tty/serial/8250/8250_port.c`'s `serial8250_compute_lcr()`
(termios cflag → UART LCR byte) is now, under `CONFIG_RUST`, a thin C wrapper
around `serial8250_compute_lcr_rs()`, defined in new file
`linux-riscv/drivers/tty/serial/8250/8250_helpers_rs.rs`. The original C body
is kept verbatim in the `#else` arm as a non-Rust fallback. Commit:
`32634c06c557` on `linux-rs/phase2-gcd`.

This is deliberately the narrowest possible slice of the plan scoped in
`docs/serial-8250-translation-scoping-2026-07-18.md`:

- Of the three functions oracle-verified in `bench/diff_8250_helpers.{c,rs}`
  (`serial8250_compute_lcr`, `fcr_get_rxtrig_bytes`, `bytes_to_fcr_rxtrig`,
  7500 cases, byte-identical), only `serial8250_compute_lcr` was wired in.
  The other two index the driver-wide `uart_config[]` static table
  (`struct serial8250_config[]`, ~10 entries of name/fifo-size/fcr/
  rxtrig_bytes/flags); wiring them in would mean either porting that whole
  table to Rust (disproportionate for this pass) or changing the two
  functions' call signatures to take the already-resolved slice (which
  means also editing their two call sites in `8250_port.c` — more invasive
  than "swap the function body only"). Left for a follow-up TU.
- `tty_get_char_size()` (the one real dependency `serial8250_compute_lcr`
  has outside itself) was **not** reimplemented — it's a plain
  `EXPORT_SYMBOL_GPL` C function in `drivers/tty/tty_ioctl.c` with no
  pointer/struct arguments, so the Rust side just declares it
  `unsafe extern "C"` and calls it, same as the project's existing
  `kernel::bindings::*` FFI pattern elsewhere.
- Everything else in the driver (register I/O, IRQ handling, startup/
  shutdown, `set_termios` control flow, the console write path) is
  untouched C, exactly as scoped.

## How the mechanism differs from `integrate_tu.py`

`scripts/integrate_tu.py` (used for all 30 prior TUs) mechanises a
*whole-file* swap: it moves `<name>.o` into a `CONFIG_RUST` switch in a
directory's `Makefile` and expects `<name>_rs.rs` to fully replace
`<name>.c`. That doesn't fit a single function inside a 3472-line driver
file. This TU used a hand-adapted variant of the same underlying Kbuild
mechanism instead:

1. New Rust file `8250_helpers_rs.rs` in the driver's own directory
   (not `lib/`) — Kbuild's generic `$(obj)/%.o: $(obj)/%.rs` pattern rule
   (`scripts/Makefile.build`) compiles it with no extra registration
   needed, same as any `lib/*_rs.rs`.
2. `drivers/tty/serial/8250/Makefile`: `8250_base-$(CONFIG_RUST) +=
   8250_helpers_rs.o`, added alongside (not instead of) `8250_port.o` —
   the C file keeps compiling either way, it just gains a call into the
   new object under `CONFIG_RUST`.
3. `8250_port.c`: `#ifdef CONFIG_RUST` / `#else` around
   `serial8250_compute_lcr`'s body, with an `extern` declaration for the
   Rust symbol in the `CONFIG_RUST` arm.
4. The Rust function uses plain `#[no_mangle]` (matching the existing
   precedent in `rust/kernel/iommu/pgtable.rs`), not `#[export]` — that
   macro requires a matching `kernel::bindings::<name>` entry (i.e. a
   bindgen-visible C declaration), which would mean adding this
   single driver-internal helper to `rust/bindings/bindings_helper.h`,
   disproportionate for one non-public function called from exactly one
   C file.

## Verification

- `bench/diff_8250_helpers.{c,rs}` — pre-existing, unchanged, still the
  Tier-2.5 oracle gate (`scripts/diff_oracle.py 8250_helpers`): 7500 cases,
  byte-identical. The Rust body wired into the kernel is the same logic
  verified there (only the C-callable wrapper/signature differs: the bench
  harness's Rust side drops the unused `up` argument entirely, matching
  what the kernel-side function now also does internally).
- Full incremental kernel build (`make ARCH=riscv LLVM=1 -j32`): clean,
  zero warnings from the new `.rs` file, links successfully.
- `nm vmlinux.unstripped | grep serial8250_compute_lcr_rs` →
  `ffffffff8017ca64 T serial8250_compute_lcr_rs` — confirms the Rust
  symbol is present as global text in the final linked kernel image, not
  dead-stripped.
- `python3 scripts/dev.py check`: **same 16/16 KUnit suites pass, ORACLE
  PASS, INIT REACHED** — identical to the pre-change baseline.

This driver is unusually high-stakes to verify (see the scoping doc): it
backs `ttyS0`, which is this project's own `dev.py check` output channel,
so "zero `not ok` lines" alone doesn't fully rule out a console-corrupting
bug. Additional reassurance specific to this change: `serial8250_compute_lcr`
runs inside `serial8250_do_set_termios()`, which executes during console
bring-up — a wrong LCR byte (wrong word length, stop bits, or parity
config) would produce a UART framing mismatch and garble every byte on the
wire, including every `ok N <suite>` line `dev.py` greps for. The boot
producing clean, correctly-parsed KUnit output is therefore itself
execution evidence that the Rust path ran and computed the right LCR byte
for this kernel's own boot-time termios configuration — not just "didn't
crash."

## What this milestone does and doesn't establish

**Does:** proves the mechanism end-to-end — a Rust function inside a real
device driver directory, called from unmodified C control flow via a
hand-written `extern "C"` boundary (not a whole-file swap, not a `lib/`
utility), compiles, links, and executes correctly on the actual boot path
of this project's kernel.

**Doesn't:** this is still exactly one pure, zero-I/O, zero-side-effect
function. It says nothing yet about Tier B (register I/O, needs `unsafe`
MMIO handling and a KUnit-mock rather than diff-oracle strategy) or Tier C
(interrupt-context RX/TX, `set_termios` control flow, the console write
path itself) from the scoping doc — those remain a materially different,
larger undertaking, gated as documented there (in particular: the console
write path should never be swapped without an external, non-self-referential
verification tier, e.g. side-by-side C-vs-Rust transcript diffing).

## Follow-up candidates

- `fcr_get_rxtrig_bytes` / `bytes_to_fcr_rxtrig`: next TU, either port
  `uart_config[]`'s `rxtrig_bytes[]` table to Rust `const` data (small,
  ~4 bytes × ~10 entries) or take the slice as an argument (touches 2 call
  sites).
- Tier B (`mem_serial_in`/`mem_serial_out` MMIO accessors) as the next
  real risk-tier step, per the scoping doc's own staging.

## Note on concurrent work in this tree

While this TU was in progress, another session was concurrently landing
unrelated work in the same `linux-riscv/` working tree (an `iomem_copy`
TU touching `lib/Makefile`, `rust/bindings/bindings_helper.h`,
`rust/helpers/io.c`, `lib/iomem_copy_rs.rs`) and in the top-level repo
(a `--run-id` parallel-boot feature in `scripts/boot_qemu.py`). None of
that work is included in this milestone's commit — it was left untouched
in the working tree for its owning session. The build and `dev.py check`
runs described above were confirmed to reflect only this TU's own change
(verified via `git diff --stat` before staging, and by checking that the
foreign files' Makefile wiring hadn't actually been picked up by the
build that was boot-tested).
