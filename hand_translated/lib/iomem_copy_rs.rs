// SPDX-License-Identifier: GPL-2.0-only
//! Generic memory-mapped I/O helpers — Rust translation of `lib/iomem_copy.c`.
//!
//! Word-at-a-time `__iomem` set/copy: handle any unaligned lead-in bytes one
//! at a time, then the bulk in `sizeof(long)`-wide chunks, then any trailing
//! bytes one at a time. Uses the RAW (unordered, no barrier, no endian swap)
//! MMIO accessors — `__raw_read*`/`__raw_write*`, wrapped as
//! `rust_helper_raw_*` (called from Rust, minus the prefix, as `bindings::
//! raw_*` — bindgen's `#[link_name]` convention for every `rust_helper_*`)
//! in `rust/helpers/io.c` since they're arch `static inline`s (riscv:
//! `arch/riscv/include/asm/mmio.h`) invisible to bindgen otherwise, same
//! header-inline shim pattern as the rest of this project (rule 0016) — NOT
//! the ordered `readb`/`writeb`/… family already wrapped in that file, which
//! have different (barrier'd, byte-swapping on some widths) semantics.
//!
//! The kernel crate's `kernel::io::{Io, Mmio}` abstraction (`rust/kernel/
//! io.rs`) is deliberately NOT used here: that trait models a *bounds-checked
//! register-width* access to a *known-size* MMIO region. These three
//! functions are the opposite — unbounded, word-at-a-time bulk copy loops
//! over a caller-supplied byte range of unknown/runtime size, i.e. the same
//! shape as `memcpy`/`memset`, just against `__iomem` memory. They're
//! lower-level primitives `Mmio` itself would be built on top of, not
//! consumers of it.
//!
//! `CONFIG_64BIT` widens the bulk-copy word from 32 to 64 bits on this
//! (riscv64) target — preserved per-function via `#[cfg(...)]`, matching
//! every branch in the C `#ifdef`/`#else`.
//!
//! All three C originals are `#ifndef <name>`-guarded (an arch can provide
//! its own) and `EXPORT_SYMBOL` (not `_GPL`) — riscv has no arch override, so
//! all three compile here; `#[export]` always emits `EXPORT_SYMBOL_GPL`, a
//! known accepted deviation for plain-`EXPORT_SYMBOL` originals (rule 0001).

use kernel::bindings;
use kernel::ffi::{c_int, c_void};
use kernel::prelude::*;

/// `IS_ALIGNED((long)addr, sizeof(long))` re-checked as `addr` advances
/// through each loop below.
#[inline(always)]
fn is_long_aligned(addr: *const c_void) -> bool {
    (addr as usize) % size_of::<usize>() == 0
}

/// memset_io() - Set a range of I/O memory to a constant value.
///
/// # Safety
/// `addr` is a valid `__iomem` pointer to at least `count` bytes.
#[export]
pub unsafe extern "C" fn memset_io(addr: *mut c_void, val: c_int, mut count: usize) {
    let val = val as u8;
    // C: `long qc = (u8)val; qc *= ~0UL / 0xff;` — replicate `val` into
    // every byte of a machine word (0x01010101... * val), the standard
    // "broadcast a byte across a word" trick.
    let qc: usize = (val as usize).wrapping_mul(usize::MAX / 0xff);
    let mut addr = addr;

    // SAFETY: per function contract; each sub-loop only advances `addr`
    // by as many bytes as it consumes from `count`, staying in bounds.
    unsafe {
        while count != 0 && !is_long_aligned(addr) {
            bindings::raw_writeb(val, addr.cast());
            addr = addr.cast::<u8>().add(1).cast();
            count -= 1;
        }

        while count >= size_of::<usize>() {
            #[cfg(CONFIG_64BIT)]
            bindings::raw_writeq(qc as u64, addr.cast());
            #[cfg(not(CONFIG_64BIT))]
            bindings::raw_writel(qc as u32, addr.cast());

            addr = addr.cast::<u8>().add(size_of::<usize>()).cast();
            count -= size_of::<usize>();
        }

        while count != 0 {
            bindings::raw_writeb(val, addr.cast());
            addr = addr.cast::<u8>().add(1).cast();
            count -= 1;
        }
    }
}

/// memcpy_fromio() - Copy a block of data from I/O memory.
///
/// # Safety
/// `dst` is valid for `count` writable bytes; `src` is a valid `__iomem`
/// pointer to at least `count` readable bytes.
#[export]
pub unsafe extern "C" fn memcpy_fromio(dst: *mut c_void, src: *const c_void, mut count: usize) {
    let mut src = src;
    let mut dst = dst;

    // SAFETY: per function contract; each sub-loop only advances `src`/`dst`
    // by as many bytes as it consumes from `count`, staying in bounds.
    unsafe {
        while count != 0 && !is_long_aligned(src) {
            *dst.cast::<u8>() = bindings::raw_readb(src.cast());
            src = src.cast::<u8>().add(1).cast();
            dst = dst.cast::<u8>().add(1).cast();
            count -= 1;
        }

        while count >= size_of::<usize>() {
            #[cfg(CONFIG_64BIT)]
            let val = bindings::raw_readq(src.cast()) as usize;
            #[cfg(not(CONFIG_64BIT))]
            let val = bindings::raw_readl(src.cast()) as usize;

            dst.cast::<usize>().write_unaligned(val);

            src = src.cast::<u8>().add(size_of::<usize>()).cast();
            dst = dst.cast::<u8>().add(size_of::<usize>()).cast();
            count -= size_of::<usize>();
        }

        while count != 0 {
            *dst.cast::<u8>() = bindings::raw_readb(src.cast());
            src = src.cast::<u8>().add(1).cast();
            dst = dst.cast::<u8>().add(1).cast();
            count -= 1;
        }
    }
}

/// memcpy_toio() - Copy a block of data into I/O memory.
///
/// # Safety
/// `dst` is a valid `__iomem` pointer to at least `count` writable bytes;
/// `src` is valid for `count` readable bytes.
#[export]
pub unsafe extern "C" fn memcpy_toio(dst: *mut c_void, src: *const c_void, mut count: usize) {
    let mut src = src;
    let mut dst = dst;

    // SAFETY: per function contract; each sub-loop only advances `src`/`dst`
    // by as many bytes as it consumes from `count`, staying in bounds.
    unsafe {
        while count != 0 && !is_long_aligned(dst) {
            bindings::raw_writeb(*src.cast::<u8>(), dst.cast());
            src = src.cast::<u8>().add(1).cast();
            dst = dst.cast::<u8>().add(1).cast();
            count -= 1;
        }

        while count >= size_of::<usize>() {
            let val = src.cast::<usize>().read_unaligned();

            #[cfg(CONFIG_64BIT)]
            bindings::raw_writeq(val as u64, dst.cast());
            #[cfg(not(CONFIG_64BIT))]
            bindings::raw_writel(val as u32, dst.cast());

            src = src.cast::<u8>().add(size_of::<usize>()).cast();
            dst = dst.cast::<u8>().add(size_of::<usize>()).cast();
            count -= size_of::<usize>();
        }

        while count != 0 {
            bindings::raw_writeb(*src.cast::<u8>(), dst.cast());
            src = src.cast::<u8>().add(1).cast();
            dst = dst.cast::<u8>().add(1).cast();
            count -= 1;
        }
    }
}
