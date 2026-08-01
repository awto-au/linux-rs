// SPDX-License-Identifier: GPL-2.0+
//! 8250/16550 memory-mapped register I/O accessors — Rust translation of a
//! narrow slice of `drivers/tty/serial/8250/8250_port.c`.
//!
//! Scope: `mem_serial_in()`/`mem_serial_out()` ONLY, of the six
//! `struct uart_port.serial_in`/`.serial_out` variants the C original
//! provides (`mem_*`, `mem16_*`, `mem32_*`, `mem32be_*`, `io_*`, `hub6_*`,
//! `no_*`). This is Tier B per
//! docs/serial-8250-translation-scoping-2026-07-18.md: real register I/O,
//! genuinely `unsafe`, one step up in risk from the Tier A pure-arithmetic
//! slice already landed in `8250_helpers_rs.rs` (issue #3).
//!
//! # Why only the `UPIO_MEM` variant
//!
//! Confirmed by tracing `8250_of.c`'s OF-match probe path (the scoping
//! doc's "What QEMU's virt board actually exercises" section): QEMU virt's
//! `"ns16550a"`-compatible UART is probed via `of_platform_serial_setup`,
//! resolves `port->iotype = UPIO_MEM` (device-tree `reg` is a `mapbase`,
//! not `iobase`), and `set_io_from_upio()` (`8250_port.c`) wires
//! `p->serial_in = mem_serial_in` / `p->serial_out = mem_serial_out`
//! accordingly. That is the *only* iotype this project's actual boot
//! target ever produces. The other five variants:
//! - `mem16_*`/`mem32_*`/`mem32be_*` — different register width, same
//!   shift/offset shape, but zero real coverage: no config in this
//!   project's `.config` selects a 16/32-bit-register 8250 variant.
//! - `io_serial_in`/`io_serial_out`/`hub6_*` — port-mapped I/O
//!   (`inb`/`outb`), gated on `CONFIG_HAS_IOPORT`; riscv64 QEMU virt has no
//!   legacy ISA I/O port space wired to this driver.
//! - `no_serial_in`/`no_serial_out` — the `WARN`-and-stub fallback for an
//!   unrecognized iotype; trivial (`~0U` / no-op) and not hardware I/O at
//!   all.
//!
//! Translating five more variants nobody boots would add real risk (more
//! `unsafe` surface, more to keep in lockstep with the C originals) for
//! zero additional coverage on this target — per the task's own explicit
//! license to scope down and document the decision. If a future TU
//! targets a board that actually selects one of these, translate it then,
//! against that board's own boot-verification, not speculatively here.
//!
//! # MMIO primitive used
//!
//! `include/asm-generic/io.h`'s `readb`/`writeb` are `static inline`
//! wrappers (ordering barriers `__io_br`/`__io_ar`/`__io_bw`/`__io_aw`
//! around `__raw_readb`/`__raw_writeb`) — invisible to bindgen, same as
//! every other header-inline accessor in this project (rule 0016). They
//! are, however, already shimmed in `rust/helpers/io.c` as
//! `rust_helper_readb`/`rust_helper_writeb` and exposed as
//! `bindings::readb`/`bindings::writeb` (`rust/bindings/
//! bindings_helpers_generated.rs`) for the `kernel::io::Mmio` abstraction
//! to use internally. This TU calls those same `bindings::readb`/`writeb`
//! functions directly rather than going through `kernel::io::{Io, Mmio}`:
//! `Mmio` models a *bounds-checked, Rust-owned* MMIO region constructed via
//! `MmioRaw::new`/`ioremap` (see that module's docs). `uart_port.membase`
//! is a bare `unsigned char __iomem *` owned and validated entirely by C
//! code elsewhere (`8250_of.c`'s probe path, well outside this TU's scope);
//! wrapping it in an owning `Mmio` here would require either unsound
//! pretend-ownership or a much larger refactor of the C-side port
//! lifecycle, neither appropriate for a narrowly-scoped accessor swap.
//! Calling `bindings::readb`/`writeb` on a caller-computed address is the
//! same primitive `Mmio` itself is built on (see `impl_mmio_io_capable!`
//! in `rust/kernel/io.rs`) and matches the C original's own directness
//! (`readb(p->membase + offset)`) — genuine unsafe MMIO, not raw pointer
//! dereference, because `readb`/`writeb` carry the ordering-barrier
//! semantics the C original relies on (irrelevant for the KUnit fake-RAM
//! backing below, but correct and necessary for the real device path).
//!
//! # Verification
//!
//! Per the scoping doc's explicit Tier B gate, this is NOT diff-oracle
//! verified (Tier 2.5, host-side, was Tier A's gate) — there is no
//! meaningful host-side reference for `readb`/`writeb` against a real
//! `__iomem` pointer. Instead: KUnit coverage (`tests` module below)
//! against a fake register backing — a real, ordinary kernel-owned
//! `[u8; N]` buffer, NOT actual device MMIO. `readb`/`writeb`'s ordering
//! barriers (`__io_br`/`__io_ar`/`__io_bw`/`__io_aw`, see
//! `arch/riscv/include/asm/io.h`) are well-defined (and inert from a
//! correctness standpoint — at most a memory-barrier instruction) against
//! plain readable/writable RAM, so calling the exact same
//! `mem_serial_in_rs`/`mem_serial_out_rs` functions used by the real path
//! against a fake buffer's address genuinely exercises this TU's
//! offset/shift arithmetic and the real `bindings::readb`/`writeb` calls —
//! not a reimplementation or a mock trait. This is NOT wired into
//! `8250_port.c`'s `serial_in`/`serial_out` function-pointer table: the C
//! originals (`mem_serial_in`/`mem_serial_out`) remain live on the actual
//! `console=` boot path. These Rust functions are compiled in and
//! unit-tested, but unreachable from any real driver call site — see
//! `docs/8250-tier-b-scoping-2026-07-18.md` for the full record.

use kernel::ffi::{c_uchar, c_uint};
#[cfg(CONFIG_KUNIT = "y")]
use kernel::prelude::kunit_tests;

/// `8250_port.c:mem_serial_in()`. `p->membase + (offset << p->regshift)`,
/// `readb()`'d and widened to `u32` (matching the C original's `u32`
/// return type — `struct uart_port.serial_in`'s signature — even though
/// only the low 8 bits are ever non-zero for this accessor).
///
/// # Safety
/// `membase` must be a valid, non-null `__iomem` pointer (as established by
/// the port's own `ioremap`/OF-probe setup in `8250_of.c`, entirely outside
/// this TU) such that `membase + (offset << regshift)` is a byte-readable
/// MMIO (or, for the KUnit test below, plain-RAM) address for the duration
/// of the call. The C original performs the identical pointer arithmetic
/// with the identical lack of an explicit bounds check — this is a direct,
/// non-strengthened translation, not a new safety contract.
#[no_mangle]
pub unsafe extern "C" fn mem_serial_in_rs(
    membase: *const c_uchar,
    regshift: c_uchar,
    offset: c_uint,
) -> u32 {
    let shifted = offset << regshift;
    // SAFETY: caller contract above; `membase.add(shifted)` stays within
    // the caller-guaranteed valid region, matching the C original's
    // `p->membase + offset` with no additional bounds check either side.
    let addr = unsafe { membase.add(shifted as usize) };
    // SAFETY: `addr` is a valid byte-readable address per the contract
    // above; `bindings::readb` is the direct FFI shim for the kernel's
    // `readb()` (ordered MMIO byte read), the exact primitive the C
    // original calls.
    unsafe { kernel::bindings::readb(addr.cast()) as u32 }
}

/// `8250_port.c:mem_serial_out()`. Same offset/shift computation as
/// `mem_serial_in_rs`; `value` is truncated to `u8` by `writeb()` exactly
/// as the C original's implicit `u32 -> u8` argument conversion does.
///
/// # Safety
/// Same contract as `mem_serial_in_rs`, for writes: `membase + (offset <<
/// regshift)` must be a valid, byte-writable `__iomem` (or plain-RAM, for
/// the KUnit test) address for the duration of the call.
#[no_mangle]
pub unsafe extern "C" fn mem_serial_out_rs(
    membase: *mut c_uchar,
    regshift: c_uchar,
    offset: c_uint,
    value: u32,
) {
    let shifted = offset << regshift;
    // SAFETY: caller contract above.
    let addr = unsafe { membase.add(shifted as usize) };
    // SAFETY: `addr` is a valid byte-writable address per the contract
    // above; `bindings::writeb` is the direct FFI shim for the kernel's
    // `writeb()` (ordered MMIO byte write), the exact primitive the C
    // original calls. `value as u8` matches the C original's implicit
    // truncation when passing a `u32 value` parameter to `writeb(u8, ...)`.
    unsafe { kernel::bindings::writeb(value as u8, addr.cast()) };
}

/// KUnit coverage against a fake register backing — a real, ordinary
/// kernel-owned byte buffer, not actual device MMIO. Exercises exactly the
/// two functions above (not a reimplementation) to confirm the
/// offset/shift/mask byte-shuffling matches the C originals'
/// `p->membase + (offset << p->regshift)` semantics. Per the Tier B gate
/// in docs/serial-8250-translation-scoping-2026-07-18.md: "KUnit coverage
/// exercising the accessor functions against a mock/fake register backing
/// (not real MMIO — no hardware side effects to fake, just byte-shuffling
/// correctness)".
#[cfg(CONFIG_KUNIT="y")]
#[kunit_tests(rust_8250_mem_serial_io)]
mod tests {
    use super::*;

    /// Round-trips a single byte through `mem_serial_out_rs`/`mem_serial_in_rs`
    /// at `offset` 0 with `regshift` 0 — the common case (no register-stride
    /// shift), confirms a plain write is read back unchanged and that
    /// neighbouring bytes in the fake register file are untouched.
    #[test]
    fn round_trip_no_shift() {
        let mut regs: [u8; 8] = [0; 8];
        let base = regs.as_mut_ptr();

        // SAFETY: `regs` is a real, live 8-byte buffer; offset 0 with
        // regshift 0 stays in bounds.
        unsafe { mem_serial_out_rs(base, 0, 3, 0xAB) };
        assert_eq!(regs[3], 0xAB);
        // Neighbours untouched.
        assert_eq!(regs[2], 0);
        assert_eq!(regs[4], 0);

        // SAFETY: same buffer, same in-bounds offset.
        let read_back = unsafe { mem_serial_in_rs(base.cast_const(), 0, 3) };
        assert_eq!(read_back, 0xAB);
    }

    /// `regshift` > 0 is how the real driver spaces out registers that are
    /// wider than a byte in the bus's address space (e.g. regshift=2 means
    /// each logical register occupies 4 physical byte lanes). Confirms
    /// `offset << regshift` is applied, not `offset` alone.
    #[test]
    fn regshift_scales_offset() {
        let mut regs: [u8; 16] = [0; 16];
        let base = regs.as_mut_ptr();

        // offset=2, regshift=2 -> byte index 2 << 2 == 8.
        // SAFETY: 16-byte buffer, index 8 in bounds.
        unsafe { mem_serial_out_rs(base, 2, 2, 0x5A) };
        assert_eq!(regs[8], 0x5A);
        for (i, &b) in regs.iter().enumerate() {
            if i != 8 {
                assert_eq!(b, 0);
            }
        }

        // SAFETY: same buffer, same in-bounds computed offset.
        let read_back = unsafe { mem_serial_in_rs(base.cast_const(), 2, 2) };
        assert_eq!(read_back, 0x5A);
    }

    /// `mem_serial_out_rs` takes `value: u32` (matching the C original's
    /// `struct uart_port.serial_out` signature) but only the low 8 bits
    /// are ever written (`writeb` truncates) — confirms that truncation,
    /// not a widened/misaligned write.
    #[test]
    fn value_truncated_to_byte() {
        let mut regs: [u8; 4] = [0; 4];
        let base = regs.as_mut_ptr();

        // SAFETY: 4-byte buffer, offset 0 in bounds.
        unsafe { mem_serial_out_rs(base, 0, 0, 0xDEAD_BEEF) };
        // Only the low byte (0xEF) is written; writeb() ignores the rest.
        assert_eq!(regs[0], 0xEF);

        // SAFETY: same buffer, same offset.
        let read_back = unsafe { mem_serial_in_rs(base.cast_const(), 0, 0) };
        // mem_serial_in_rs widens the read-back u8 to u32 with zero-extend.
        assert_eq!(read_back, 0xEF);
    }

    /// Multiple distinct (offset, regshift) pairs mapping to distinct byte
    /// indices, written in one order and read back in another, confirm no
    /// cross-talk between registers — the property the real driver depends
    /// on to address UART_RBR/UART_IER/UART_FCR/UART_LCR/... independently
    /// within the same `membase` region.
    #[test]
    fn independent_registers_no_crosstalk() {
        let mut regs: [u8; 8] = [0; 8];
        let base = regs.as_mut_ptr();

        // SAFETY: 8-byte buffer; offsets 0..=7 all in bounds with regshift 0.
        unsafe {
            mem_serial_out_rs(base, 0, 0, 0x11); // UART_RBR/THR-ish slot
            mem_serial_out_rs(base, 0, 1, 0x22); // UART_IER-ish slot
            mem_serial_out_rs(base, 0, 2, 0x33); // UART_FCR-ish slot
            mem_serial_out_rs(base, 0, 5, 0x44); // UART_LSR-ish slot
        }

        // SAFETY: same buffer, in-bounds offsets, reads only.
        unsafe {
            assert_eq!(mem_serial_in_rs(base.cast_const(), 0, 0), 0x11);
            assert_eq!(mem_serial_in_rs(base.cast_const(), 0, 1), 0x22);
            assert_eq!(mem_serial_in_rs(base.cast_const(), 0, 2), 0x33);
            assert_eq!(mem_serial_in_rs(base.cast_const(), 0, 5), 0x44);
        }
        // Untouched slots stay zero.
        assert_eq!(regs[3], 0);
        assert_eq!(regs[4], 0);
        assert_eq!(regs[6], 0);
        assert_eq!(regs[7], 0);
    }
}
