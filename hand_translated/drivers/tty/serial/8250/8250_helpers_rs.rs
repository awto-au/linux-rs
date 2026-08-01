// SPDX-License-Identifier: GPL-2.0+
//! 8250/16550 pure register-bit-manipulation helpers — Rust translation
//! of a narrow slice of `drivers/tty/serial/8250/8250_port.c`.
//!
//! Scope: `serial8250_compute_lcr()` (termios cflag -> LCR byte),
//! `fcr_get_rxtrig_bytes()` and `bytes_to_fcr_rxtrig()` (FCR RX-trigger
//! level <-> byte-count lookup). This is Tier A per
//! docs/serial-8250-translation-scoping-2026-07-18.md: pure arithmetic,
//! zero register I/O, zero locking, zero interrupt-context involvement —
//! the lowest-risk possible slice of a driver that is otherwise unusually
//! high-stakes because it *is* the console this project's own test harness
//! reads to decide pass/fail (see that doc for the full risk discussion).
//!
//! `fcr_get_rxtrig_bytes`/`bytes_to_fcr_rxtrig` in the C original index the
//! driver-wide `static const struct serial8250_config uart_config[]` table
//! (`8250_port.c`, ~25 entries keyed by the `PORT_*` enum, including a
//! `const char *name` field) to obtain a `const unsigned char
//! rxtrig_bytes[UART_FCR_R_TRIG_MAX_STATE]` slice for the port's detected
//! UART type. Porting that whole table to Rust was considered (issue #3)
//! and rejected for this pass: it is data owned by and read throughout
//! `8250_port.c` (`.name`/`.fifo_size`/`.flags` are used well outside these
//! two functions), so a full port would mean either duplicating the table
//! in two languages (a correctness hazard of its own) or a much larger
//! follow-on TU to migrate every reader. Instead, per the issue's
//! alternative, `uart_config[]` stays exactly as-is in C, and the C-side
//! wrapper resolves `&uart_config[up->port.type].rxtrig_bytes[0]` (the same
//! expression the original C body used) and passes that 4-byte slice by
//! pointer to Rust — the function *bodies* change but the outer C
//! signatures (still `struct uart_8250_port *up`) and both call sites
//! (`do_get_rxtrig`, `do_set_rxtrig`) are untouched, same "swap the body
//! only" shape as `serial8250_compute_lcr` below.
//!
//! Faithful port of the C originals; oracle-verified byte-identical against
//! `bench/diff_8250_helpers.c` over 7500 generated cases (2500 cflag
//! combinations for LCR, 2500 each of (cfg, fcr) / (cfg, bytes) pairs for
//! the trigger-table functions, across 3 representative trigger tables) via
//! `scripts/diff_oracle.py 8250_helpers` (see that doc, "Verification
//! gate"). `tty_get_char_size()` stays a real exported C symbol
//! (`drivers/tty/tty_ioctl.c`, `EXPORT_SYMBOL_GPL`) — declared here rather
//! than reimplemented, since duplicating it would defeat the point of a
//! narrow slice and it has no further translation-relevant dependencies.

use kernel::ffi::{c_int, c_uchar, c_uint};

const UART_LCR_SPAR: c_uchar = 0x20;
const UART_LCR_EPAR: c_uchar = 0x10;
const UART_LCR_PARITY: c_uchar = 0x08;
const UART_LCR_STOP: c_uchar = 0x04;

const CSTOPB: c_uint = 0x0000_0040;
const PARENB: c_uint = 0x0000_0100;
const PARODD: c_uint = 0x0000_0200;
const CMSPAR: c_uint = 0x4000_0000;

/// `include/uapi/linux/serial_reg.h`
const UART_FCR_TRIGGER_MASK: c_uchar = 0xc0;
const UART_FCR_R_TRIG_SHIFT: u32 = 6;
const UART_FCR_R_TRIG_00: c_uchar = 0x00;
const UART_FCR_R_TRIG_11: c_uchar = 0xc0;
const UART_FCR_R_TRIG_MAX_STATE: usize = 4;
/// `include/uapi/asm-generic/errno-base.h:EOPNOTSUPP`
const EOPNOTSUPP: c_int = 95;

/// `include/uapi/linux/serial_reg.h:UART_FCR_R_TRIG_BITS(x)`
#[inline]
fn uart_fcr_r_trig_bits(x: c_uchar) -> usize {
    ((x & UART_FCR_TRIGGER_MASK) >> UART_FCR_R_TRIG_SHIFT) as usize
}

unsafe extern "C" {
    /// `drivers/tty/tty_ioctl.c:tty_get_char_size()` — real exported C
    /// symbol (`EXPORT_SYMBOL_GPL`), not translated (see module docs).
    fn tty_get_char_size(cflag: c_uint) -> c_uchar;
}

/// `include/linux/serial.h:UART_LCR_WLEN(x)`
#[inline]
fn uart_lcr_wlen(x: c_uchar) -> c_uchar {
    x - 5
}

/// `8250_port.c:serial8250_compute_lcr()`. The `up` (`struct
/// uart_8250_port *`) parameter of the C original is dropped: the C body
/// never dereferences it, only `c_cflag` is used (confirmed by reading the
/// original body; the sole caller passes `up` only so this function has
/// the same shape as its neighbours). The C-side wrapper left in
/// `8250_port.c` keeps the original signature so the call site is
/// untouched.
///
/// # Safety
/// `tty_get_char_size` is called with a plain `c_uint`, no pointers
/// involved; safe to call from any context the C original could be
/// called from.
///
/// Not `#[export]`: that macro requires a matching `kernel::bindings::`
/// entry (i.e. a C declaration bindgen already saw), which would mean
/// adding this driver-internal helper to `bindings_helper.h` — out of
/// proportion for a single non-public function called from exactly one
/// C file. Plain `#[no_mangle]` (same pattern as
/// `rust/kernel/iommu/pgtable.rs`) plus a hand-written `extern` decl on
/// the C side achieves the same C-ABI linkage without that.
#[no_mangle]
pub unsafe extern "C" fn serial8250_compute_lcr_rs(c_cflag: c_uint) -> c_uchar {
    // SAFETY: tty_get_char_size takes a plain integer and returns a plain
    // integer, no aliasing/lifetime concerns.
    let mut lcr = uart_lcr_wlen(unsafe { tty_get_char_size(c_cflag) });

    if c_cflag & CSTOPB != 0 {
        lcr |= UART_LCR_STOP;
    }
    if c_cflag & PARENB != 0 {
        lcr |= UART_LCR_PARITY;
    }
    if c_cflag & PARODD == 0 {
        lcr |= UART_LCR_EPAR;
    }
    if c_cflag & CMSPAR != 0 {
        lcr |= UART_LCR_SPAR;
    }

    lcr
}

/// `8250_port.c:fcr_get_rxtrig_bytes()`. The C original indexes
/// `uart_config[up->port.type].rxtrig_bytes` itself; here the C-side
/// wrapper resolves that lookup and passes a pointer to the resulting
/// 4-byte (`UART_FCR_R_TRIG_MAX_STATE`) array instead, so `uart_config[]`
/// stays untouched C data (see module docs for why). `fcr` is `up->fcr`
/// from the C original.
///
/// # Safety
/// `rxtrig_bytes` must be non-null and point to a valid, initialized
/// `[c_uchar; UART_FCR_R_TRIG_MAX_STATE]` for the duration of the call —
/// the C wrapper passes `&uart_config[up->port.type].rxtrig_bytes[0]`,
/// which satisfies this (the array is a fixed-size field of a `static
/// const` table entry, never mutated, always fully initialized).
#[no_mangle]
pub unsafe extern "C" fn fcr_get_rxtrig_bytes_rs(
    rxtrig_bytes: *const c_uchar,
    fcr: c_uchar,
) -> c_int {
    // SAFETY: caller contract above; we only read exactly
    // UART_FCR_R_TRIG_MAX_STATE bytes at valid indices (uart_fcr_r_trig_bits
    // masks to 0..=3).
    let table = unsafe { core::slice::from_raw_parts(rxtrig_bytes, UART_FCR_R_TRIG_MAX_STATE) };
    let bytes = table[uart_fcr_r_trig_bits(fcr)];

    if bytes != 0 {
        bytes as c_int
    } else {
        -EOPNOTSUPP
    }
}

/// `8250_port.c:bytes_to_fcr_rxtrig()`. Same slice-passing shape as
/// `fcr_get_rxtrig_bytes_rs` above; `bytes` is the caller's requested
/// RX-trigger byte count.
///
/// # Safety
/// Same contract as `fcr_get_rxtrig_bytes_rs`.
#[no_mangle]
pub unsafe extern "C" fn bytes_to_fcr_rxtrig_rs(
    rxtrig_bytes: *const c_uchar,
    bytes: c_uchar,
) -> c_int {
    // SAFETY: caller contract above.
    let table = unsafe { core::slice::from_raw_parts(rxtrig_bytes, UART_FCR_R_TRIG_MAX_STATE) };

    if table[uart_fcr_r_trig_bits(UART_FCR_R_TRIG_00)] == 0 {
        return -EOPNOTSUPP;
    }

    for (i, &trig) in table.iter().enumerate().skip(1) {
        if bytes < trig {
            // Use the nearest lower value.
            return ((i - 1) as c_int) << UART_FCR_R_TRIG_SHIFT;
        }
    }

    UART_FCR_R_TRIG_11 as c_int
}
