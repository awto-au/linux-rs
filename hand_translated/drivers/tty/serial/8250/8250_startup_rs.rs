// SPDX-License-Identifier: GPL-2.0+
//! `serial8250_do_startup()`/`serial8250_do_shutdown()` — Rust translation of
//! device bring-up/teardown control flow from
//! `drivers/tty/serial/8250/8250_port.c`.
//!
//! Scope: Tier C per docs/serial-8250-translation-scoping-2026-07-18.md and
//! awto-au/linux-rs#25 — the "least-bad of the four" Tier C candidates per
//! docs/8250-tier-c-blocker-2026-07-18.md's ranking (`autoconfig` is
//! order/timing-fragile probing; `serial8250_do_set_termios` has the same
//! entanglement and stays explicitly deferred; the IRQ handler runs on every
//! live interrupt with zero margin).
//!
//! # NO KUNIT COVERAGE — BY DESIGN, NOT AN OMISSION
//!
//! Every other Rust TU in this project (30+ `lib/`-style translations, plus
//! Tier A/B in this same driver) is either diff-oracle-verified host-side or
//! KUnit-verified against a fake/real buffer. This TU has neither, and that
//! is a deliberate, explicitly-approved exception (Dan, 2026-07-18; see
//! awto-au/linux-rs#25 and docs/8250-tier-c-blocker-2026-07-18.md), not a
//! lapse:
//!
//! - `serial8250_do_startup_rs` calls (via the C shim below)
//!   `up->ops->setup_irq(up)`, which for this project's actual driver
//!   (`univ8250_driver_ops`, `8250_core.c`) resolves to
//!   `univ8250_setup_irq -> serial_link_irq_chain -> request_irq()` against
//!   a live IRQ line with real shared-IRQ bookkeeping.
//! - `serial8250_do_shutdown_rs` calls `synchronize_irq(port->irq)` — blocks
//!   until any in-flight interrupt handler completes, meaningless without a
//!   real interrupt controller — then (via the shim)
//!   `up->ops->release_irq(up) -> univ8250_release_irq -> free_irq`.
//! - Both take the real port spinlock (`guard(uart_port_lock_irqsave)`,
//!   real `spin_lock_irqsave`/`spin_unlock_irqrestore` under the hood).
//!
//! A KUnit fake can stand in for *data* (Tier B's `[u8; N]` register-file
//! buffer — a plain byte array behaves identically to real MMIO for
//! byte-shuffling correctness). It cannot stand in for "the interrupt
//! subsystem accepted this registration" or "no interrupt is currently in
//! flight": faking those would mean reimplementing genuine IRQ-core
//! chain-walking/shared-IRQ bookkeeping/completion-wait semantics inside the
//! test, which would verify the fake against itself, not the driver against
//! anything real — exactly the interrupt-context/locking-semantics condition
//! the blocker doc identified as a legitimate stop trigger, now instead
//! accepted as a scoped, documented risk trade-off rather than a blocker.
//! This exception is scoped to exactly these two functions; it does not
//! extend to any other function/file without being separately re-confirmed.
//!
//! In place of KUnit, this TU is gated on a byte-for-byte side-by-side
//! boot-transcript comparison against the unmodified C path across multiple
//! repeat boots — the verification tier
//! docs/serial-8250-translation-scoping-2026-07-18.md always required before
//! any live-console-adjacent swap, kept here even though KUnit itself is
//! skipped. See docs/8250-tier-c-startup-shutdown-2026-07-18.md (linux-rs
//! repo) for the actual comparison results.
//!
//! # Why `struct uart_port`/`struct uart_8250_port` stay opaque
//!
//! Neither struct has bindgen bindings anywhere in this tree (confirmed:
//! neither type appears in `rust/bindings/bindings_helper.h`). Both are
//! large and deeply nested (embedded spinlock, function-pointer op tables,
//! `list_head`, DMA/RS485 sub-structs) — generating full bindgen coverage
//! for a first driver-control-flow TU would be a separate, much larger
//! undertaking with its own correctness risk, and contrary to this
//! project's established discipline of never letting Rust assume
//! ownership/layout of a struct it doesn't own (see
//! docs/8250-tier-b-scoping-2026-07-18.md's `Mmio`-vs-`membase` discussion
//! for the same reasoning applied one tier down).
//!
//! Instead, `port` stays an opaque `*mut c_void` on this side. Every field
//! read/write and every subsystem call the C originals perform is exposed as
//! a narrow, individually-named, individually-auditable `extern "C"` shim
//! function in `8250_port.c` (`serial8250_startup_rs_*` — see that file's
//! `CONFIG_RUST_8250_STARTUP` block) — a one-for-one mechanical mirror of
//! each line of the original C bodies, not a reimplementation or
//! simplification. This Rust file therefore carries the *control
//! flow*—branching, ordering, early-return error handling—while every
//! register access, lock, and subsystem call happens on the C side exactly
//! as it always did. `upf_t` flag-bit tests (`UPF_BUGGY_UART`,
//! `UPF_SHARE_IRQ`, `UPF_FOURPORT`) are deliberately evaluated in the C shim
//! rather than ported as Rust constants: `upf_t` is a project-internal
//! bitflag type (some bits `BIT_ULL`-defined), not a stable UAPI this
//! project pins elsewhere the way Tier A pins `tcflag_t` bits — evaluating
//! the test in C means its width/encoding can never silently drift between
//! the two languages. Only the resulting `bool` crosses the FFI boundary.
//!
//! # Faithfulness
//!
//! Every branch, early return, and call ordering below matches
//! `serial8250_do_startup()`/`serial8250_do_shutdown()` in `8250_port.c`
//! line-for-line (see that file's `#else` arm, kept verbatim as the
//! non-Rust build path, for direct comparison). No behavior was added,
//! removed, or reordered relative to the C original.

use kernel::ffi::{c_int, c_void};

const ENODEV: c_int = 19; // include/uapi/asm-generic/errno-base.h

unsafe extern "C" {
    fn serial8250_startup_rs_port_buggy_uart(port: *mut c_void) -> bool;
    fn serial8250_startup_rs_port_share_irq(port: *mut c_void) -> bool;
    fn serial8250_startup_rs_port_set_shared_irq(port: *mut c_void);
    fn serial8250_startup_rs_port_fourport(port: *mut c_void) -> bool;

    fn serial8250_startup_rs_uart_config_fifo_size(port: *mut c_void) -> u32;
    fn serial8250_startup_rs_uart_config_tx_loadsz(port: *mut c_void) -> u32;
    fn serial8250_startup_rs_uart_config_flags(port: *mut c_void) -> u32;

    fn serial8250_startup_rs_get_fifosize(port: *mut c_void) -> u32;
    fn serial8250_startup_rs_set_fifosize(port: *mut c_void, v: u32);
    fn serial8250_startup_rs_get_tx_loadsz(port: *mut c_void) -> u32;
    fn serial8250_startup_rs_set_tx_loadsz(port: *mut c_void, v: u32);
    fn serial8250_startup_rs_get_capabilities(port: *mut c_void) -> u32;
    fn serial8250_startup_rs_set_capabilities(port: *mut c_void, v: u32);
    fn serial8250_startup_rs_set_mcr(port: *mut c_void, v: u8);

    fn serial8250_startup_rs_iotype_changed(port: *mut c_void) -> bool;
    fn serial8250_startup_rs_set_io_from_upio(port: *mut c_void);

    fn serial8250_startup_rs_rpm_get(port: *mut c_void);
    fn serial8250_startup_rs_rpm_put(port: *mut c_void);

    fn serial8250_startup_rs_startup_special(port: *mut c_void);
    fn serial8250_startup_rs_clear_fifos(port: *mut c_void);
    fn serial8250_startup_rs_clear_interrupts(port: *mut c_void);
    fn serial8250_startup_rs_lsr_in(port: *mut c_void) -> u8;
    fn serial8250_startup_rs_lsr_safety_warn(port: *mut c_void);
    fn serial8250_startup_rs_set_TRG_levels(port: *mut c_void);

    fn serial8250_startup_rs_setup_irq(port: *mut c_void) -> c_int;
    fn serial8250_startup_rs_release_irq(port: *mut c_void);
    fn serial8250_startup_rs_THRE_test(port: *mut c_void);
    fn serial8250_startup_rs_setup_timer(port: *mut c_void);
    fn serial8250_startup_rs_initialize(port: *mut c_void);
    fn serial8250_startup_rs_clear_saved_flags(port: *mut c_void);

    fn serial8250_startup_rs_has_dma(port: *mut c_void) -> bool;
    fn serial8250_startup_rs_is_console(port: *mut c_void) -> bool;
    fn serial8250_startup_rs_request_dma(port: *mut c_void) -> bool;
    fn serial8250_startup_rs_dma_forbidden_console(port: *mut c_void);
    fn serial8250_startup_rs_dma_request_failed(port: *mut c_void);
    fn serial8250_startup_rs_clear_dma(port: *mut c_void);

    fn serial8250_startup_rs_set_ier_rx_shadow(port: *mut c_void);
    fn serial8250_startup_rs_fourport_enable_irq(port: *mut c_void);

    fn serial8250_startup_rs_ier_off_locked(port: *mut c_void);
    fn serial8250_startup_rs_synchronize_irq(port: *mut c_void);
    fn serial8250_startup_rs_release_dma(port: *mut c_void);
    fn serial8250_startup_rs_mctrl_reset_locked(port: *mut c_void);
    fn serial8250_startup_rs_rsa_disable(port: *mut c_void);
    fn serial8250_startup_rs_rx_in(port: *mut c_void);
}

/// `8250_port.c:serial8250_do_startup()`.
///
/// # Safety
/// `port` must be a valid, non-null `struct uart_port *` for the duration of
/// the call, satisfying the same preconditions the C original required of
/// its caller (a port that has completed probe/registration but not yet
/// been started) — this function performs no additional validation beyond
/// what the C original performed, matching it branch-for-branch.
#[no_mangle]
pub unsafe extern "C" fn serial8250_do_startup_rs(port: *mut c_void) -> c_int {
    // SAFETY: shim calls below all share the single caller contract above —
    // `port` valid and non-null for the duration of this function — and each
    // shim touches only the fields/subsystems its C body already touched.
    unsafe {
        if serial8250_startup_rs_get_fifosize(port) == 0 {
            let v = serial8250_startup_rs_uart_config_fifo_size(port);
            serial8250_startup_rs_set_fifosize(port, v);
        }
        if serial8250_startup_rs_get_tx_loadsz(port) == 0 {
            let v = serial8250_startup_rs_uart_config_tx_loadsz(port);
            serial8250_startup_rs_set_tx_loadsz(port, v);
        }
        if serial8250_startup_rs_get_capabilities(port) == 0 {
            let v = serial8250_startup_rs_uart_config_flags(port);
            serial8250_startup_rs_set_capabilities(port, v);
        }
        serial8250_startup_rs_set_mcr(port, 0);

        if serial8250_startup_rs_iotype_changed(port) {
            serial8250_startup_rs_set_io_from_upio(port);
        }

        // guard(serial8250_rpm)(up) — RAII in C, get-now/put-on-every-return
        // here since Rust has no equivalent scope-exit guard for an opaque
        // extern type without introducing a new abstraction for a single
        // call site.
        serial8250_startup_rs_rpm_get(port);

        serial8250_startup_rs_startup_special(port);

        // Clear the FIFO buffers and disable them (re-enabled in
        // set_termios()).
        serial8250_startup_rs_clear_fifos(port);

        serial8250_startup_rs_clear_interrupts(port);

        // At this point, there's no way the LSR could still be 0xff; if it
        // is, bail out, because there's likely no UART here.
        if !serial8250_startup_rs_port_buggy_uart(port) && serial8250_startup_rs_lsr_in(port) == 0xff {
            serial8250_startup_rs_lsr_safety_warn(port);
            serial8250_startup_rs_rpm_put(port);
            return -ENODEV;
        }

        serial8250_startup_rs_set_TRG_levels(port);

        // Check if we need to have shared IRQs.
        if serial8250_startup_rs_port_share_irq(port) {
            serial8250_startup_rs_port_set_shared_irq(port);
        }

        let retval = serial8250_startup_rs_setup_irq(port);
        if retval != 0 {
            serial8250_startup_rs_rpm_put(port);
            return retval;
        }

        serial8250_startup_rs_THRE_test(port);

        serial8250_startup_rs_setup_timer(port);

        serial8250_startup_rs_initialize(port);

        // Clear the interrupt registers again for luck, and clear the saved
        // flags to avoid getting false values from polling routines or the
        // previous session.
        serial8250_startup_rs_clear_interrupts(port);
        serial8250_startup_rs_clear_saved_flags(port);

        // Request DMA channels for both RX and TX.
        if serial8250_startup_rs_has_dma(port) {
            if serial8250_startup_rs_is_console(port) {
                serial8250_startup_rs_dma_forbidden_console(port);
                serial8250_startup_rs_clear_dma(port);
            } else if serial8250_startup_rs_request_dma(port) {
                serial8250_startup_rs_dma_request_failed(port);
                serial8250_startup_rs_clear_dma(port);
            }
        }

        // Set the IER shadow for rx interrupts but defer actual interrupt
        // enable until after the FIFOs are enabled; otherwise, an
        // already-active sender can swamp the interrupt handler with "too
        // much work".
        serial8250_startup_rs_set_ier_rx_shadow(port);

        if serial8250_startup_rs_port_fourport(port) {
            // Enable interrupts on the AST Fourport board.
            serial8250_startup_rs_fourport_enable_irq(port);
        }

        serial8250_startup_rs_rpm_put(port);
        0
    }
}

/// `8250_port.c:serial8250_do_shutdown()`.
///
/// # Safety
/// Same contract as `serial8250_do_startup_rs`: `port` must be a valid,
/// non-null `struct uart_port *`, previously started, for the duration of
/// the call.
#[no_mangle]
pub unsafe extern "C" fn serial8250_do_shutdown_rs(port: *mut c_void) {
    // SAFETY: see serial8250_do_startup_rs — identical caller contract, same
    // shim-boundary reasoning.
    unsafe {
        serial8250_startup_rs_rpm_get(port);

        // Disable interrupts from this port. Synchronize UART_IER access
        // against the console (real port lock, taken in the C shim).
        serial8250_startup_rs_ier_off_locked(port);

        serial8250_startup_rs_synchronize_irq(port);

        serial8250_startup_rs_release_dma(port);
        serial8250_startup_rs_clear_dma(port);

        serial8250_startup_rs_mctrl_reset_locked(port);

        serial8250_startup_rs_clear_fifos(port);

        serial8250_startup_rs_rsa_disable(port);

        // Read data port to reset things, and then unlink from the IRQ
        // chain.
        serial8250_startup_rs_rx_in(port);

        // LCR writes on DW UART can trigger late (unmaskable) IRQs. Handle
        // them before releasing the handler.
        serial8250_startup_rs_synchronize_irq(port);

        serial8250_startup_rs_rpm_put(port);

        serial8250_startup_rs_release_irq(port);
    }
}
