// SPDX-License-Identifier: GPL-2.0+
//! `serial8250_handle_irq_locked()` — Rust translation of the live IRQ
//! RX/TX dispatch control flow from `drivers/tty/serial/8250/8250_port.c`.
//!
//! Scope: Tier C per docs/serial-8250-translation-scoping-2026-07-18.md and
//! awto-au/linux-rs#25 — the third Tier C slice, after
//! `serial8250_do_startup`/`serial8250_do_shutdown` (see
//! docs/8250-tier-c-startup-shutdown-2026-07-18.md, linux-rs repo). Per
//! docs/8250-tier-c-blocker-2026-07-18.md's evidence-based ranking, this
//! function is HIGHER RISK than that prior slice: it runs on *every* live
//! interrupt (not just device bring-up/teardown), asserts
//! `lockdep_assert_held_once(&port->lock)` on the real port lock held by the
//! caller, and has zero test margin — a bug here is immediately live on the
//! console path the moment it's wired, with no graceful degradation.
//!
//! # NO KUNIT COVERAGE — BY DESIGN, NOT AN OMISSION
//!
//! Same class of blocker as the startup/shutdown slice, explicitly
//! re-confirmed for this function specifically (not inherited automatically
//! — the prior landing doc says in so many words that its KUnit exception
//! "does not extend to any other function ... without being separately
//! re-confirmed"):
//!
//! - The caller holds the real port spinlock
//!   (`guard(uart_port_lock_irqsave)` / `scoped_guard(...)` at this
//!   function's own call sites in `serial8250_handle_irq` and elsewhere) —
//!   `lockdep_assert_held_once(&port->lock)` below only makes sense against
//!   the real lock, not a fake.
//! - `serial8250_rx_chars`/`serial8250_tx_chars`/`serial8250_modem_status`/
//!   `__stop_tx`/`handle_rx_dma`/`serial8250_clear_and_reinit_fifos` (all
//!   still C, unmodified, called via the shim below) themselves touch real
//!   UART hardware registers, real DMA state, and (via `pm_wakeup_event`)
//!   the real power-management wakeup subsystem — none of this is
//!   meaningfully fakeable without reimplementing genuine subsystem
//!   behavior inside the test, which would verify the fake against itself,
//!   not the driver against anything real.
//!
//! In place of KUnit, this TU is gated on a byte-for-byte side-by-side
//! boot-transcript comparison against the unmodified C path — MORE repeat
//! boots than the startup/shutdown slice used (8 total here vs. 6 there),
//! given the elevated risk class — see docs/8250-tier-c-irq-2026-07-18.md
//! (linux-rs repo) for the actual comparison results.
//!
//! # Why `struct uart_port`/`struct uart_8250_port` stay opaque
//!
//! Same reasoning as the startup/shutdown slice (neither struct has bindgen
//! bindings anywhere in this tree; both are large and deeply nested).
//! `port` stays an opaque `*mut c_void` on this side. Every field
//! read/write and every subsystem call the C original performs is exposed
//! as a narrow, individually-named, individually-auditable `extern "C"`
//! shim function in `8250_port.c` (`serial8250_irq_rs_*` — see that file's
//! `CONFIG_RUST_8250_IRQ` block) — a one-for-one mechanical mirror of each
//! line of the original C body, not a reimplementation or simplification.
//! `UART_LSR_*`/`UPSTAT_*`/`UART_IER_*` bit tests are deliberately
//! evaluated in the C shim rather than ported as Rust constants, same
//! discipline the startup/shutdown slice applied to `upf_t`: these are
//! project-internal register-flag encodings, not a stable UAPI this
//! project pins elsewhere the way Tier A pins `tcflag_t` bits, so
//! evaluating the test in C means the encoding can never silently drift
//! between the two languages — only the resulting `bool`/`u16` crosses the
//! FFI boundary.
//!
//! `serial8250_clear_and_reinit_fifos`, `serial8250_rx_chars`,
//! `serial8250_modem_status`, `serial8250_tx_chars`, `__stop_tx`, and
//! `handle_rx_dma` are NOT translated by this TU (confirmed via grep: none
//! of the 8250 Rust files reference them) — they remain C functions called
//! via the shim, exactly as scoped. `handle_rx_dma` is additionally
//! `static` in `8250_port.c`, so it cannot be called directly from another
//! translation unit at all; the shim function wrapping it must itself live
//! in `8250_port.c`.
//!
//! # Faithfulness
//!
//! Every branch, condition, and call ordering below matches
//! `serial8250_handle_irq_locked()` in `8250_port.c` line-for-line (see
//! that file's `#else` arm, kept verbatim as the non-Rust build path, for
//! direct comparison). No behavior was added, removed, or reordered
//! relative to the C original.

use kernel::ffi::c_void;

unsafe extern "C" {
    fn serial8250_irq_rs_lockdep_assert_held(port: *mut c_void);
    fn serial8250_irq_rs_lsr_in(port: *mut c_void) -> u16;
    fn serial8250_irq_rs_dr_clear_fifoe_set(port: *mut c_void, status: u16) -> bool;
    fn serial8250_irq_rs_clear_and_reinit_fifos(port: *mut c_void);
    fn serial8250_irq_rs_skip_rx_check(port: *mut c_void, status: u16) -> bool;
    fn serial8250_irq_rs_dr_or_bi(port: *mut c_void, status: u16) -> bool;
    fn serial8250_irq_rs_wakeup_event(port: *mut c_void);
    fn serial8250_irq_rs_has_dma(port: *mut c_void) -> bool;
    fn serial8250_irq_rs_handle_rx_dma(port: *mut c_void, iir: u32) -> bool;
    fn serial8250_irq_rs_rx_chars(port: *mut c_void, status: u16) -> u16;
    fn serial8250_irq_rs_modem_status(port: *mut c_void);
    fn serial8250_irq_rs_thre_and_thri(port: *mut c_void, status: u16) -> bool;
    fn serial8250_irq_rs_dma_tx_err(port: *mut c_void) -> bool;
    fn serial8250_irq_rs_tx_chars(port: *mut c_void);
    fn serial8250_irq_rs_dma_tx_running(port: *mut c_void) -> bool;
    fn serial8250_irq_rs_stop_tx(port: *mut c_void);
}

/// `8250_port.c:serial8250_handle_irq_locked()`.
///
/// # Safety
/// `port` must be a valid, non-null `struct uart_port *` for the duration of
/// the call, with the caller already holding `port->lock` (the same
/// precondition the C original documents: "Context: port's lock must be
/// held by the caller"). This function performs no additional validation
/// beyond what the C original performed, matching it branch-for-branch.
#[no_mangle]
pub unsafe extern "C" fn serial8250_handle_irq_locked_rs(port: *mut c_void, iir: u32) {
    // SAFETY: shim calls below all share the single caller contract above —
    // `port` valid, non-null, and locked for the duration of this function —
    // and each shim touches only the fields/subsystems its C body already
    // touched.
    unsafe {
        serial8250_irq_rs_lockdep_assert_held(port);

        let mut status = serial8250_irq_rs_lsr_in(port);

        // Recover from no-data-ready and FIFO error condition to avoid
        // getting stuck in the ISR.
        if serial8250_irq_rs_dr_clear_fifoe_set(port, status) {
            serial8250_irq_rs_clear_and_reinit_fifos(port);
        }

        // If port is stopped and there are no error conditions in the FIFO,
        // then don't drain the FIFO, as this may lead to TTY buffer
        // overflow. Not servicing the RX FIFO would trigger auto HW flow
        // control when FIFO occupancy reaches the preset threshold, thus
        // halting RX. This only works when auto HW flow control is
        // available.
        let skip_rx = serial8250_irq_rs_skip_rx_check(port, status);

        if serial8250_irq_rs_dr_or_bi(port, status) && !skip_rx {
            serial8250_irq_rs_wakeup_event(port);
            if !serial8250_irq_rs_has_dma(port) || serial8250_irq_rs_handle_rx_dma(port, iir) {
                status = serial8250_irq_rs_rx_chars(port, status);
            }
        }

        serial8250_irq_rs_modem_status(port);

        if serial8250_irq_rs_thre_and_thri(port, status) {
            if !serial8250_irq_rs_has_dma(port) || serial8250_irq_rs_dma_tx_err(port) {
                serial8250_irq_rs_tx_chars(port);
            } else if !serial8250_irq_rs_dma_tx_running(port) {
                serial8250_irq_rs_stop_tx(port);
            }
        }
    }
}
