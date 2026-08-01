// SPDX-License-Identifier: GPL-2.0-or-later
//! IP/TCP/UDP checksumming routines — Rust translation of `lib/checksum.c`.
//!
//! Authors (original C): Jorge Cwik, Arnt Gulbrandsen, Tom May, Andreas
//! Schwab; revised by Kenneth Albanowski.
//!
//! **riscv provides its own `do_csum` and `ip_fast_csum`**
//! (`arch/riscv/include/asm/checksum.h`: `#define do_csum do_csum` /
//! `#define ip_fast_csum ip_fast_csum`, real implementation in
//! `arch/riscv/lib/csum.c`), which `#ifndef`-guards `lib/checksum.c`'s
//! own generic `do_csum`/`ip_fast_csum` out of the build entirely on
//! this arch — they are dead code here, not translated (same "riscv
//! overrides the generic path" shape as `int_sqrt64`'s `BITS_PER_LONG`
//! guard). Only `csum_partial`, `ip_compute_csum`, and
//! `csum_tcpudp_nofold` actually compile for our target; they call the
//! real (arch) `do_csum` via `bindings::do_csum`, per rule 0021
//! (cross-TU C call — `do_csum` lives in a different, untranslated TU).
//!
//! `__sum16`/`__wsum`/`__be32` are sparse-only `__bitwise` annotations,
//! invisible to bindgen; they collapse to plain `u16`/`u32` here exactly
//! as they do for the C build.

use kernel::bindings;
use kernel::ffi::{c_int, c_void};
use kernel::prelude::*;

/// Header-inline `from64to32` (only used by `csum_tcpudp_nofold` in this
/// TU), reimplemented per rule 0016.
#[inline]
fn from64to32(mut x: u64) -> u32 {
    // add up 32-bit and 32-bit for 32+c bit
    x = (x & 0xffffffff).wrapping_add(x >> 32);
    // add up carry..
    x = (x & 0xffffffff).wrapping_add(x >> 32);
    x as u32
}

/// Computes the checksum of a memory block at `buff`, adding in `wsum`.
///
/// # Safety
/// `buff` points to `len` valid bytes when `len > 0` (C ABI contract);
/// must be called with even `len` except for the last fragment.
#[export]
pub unsafe extern "C" fn csum_partial(buff: *const c_void, len: c_int, wsum: u32) -> u32 {
    // SAFETY: per function contract; do_csum is the real (arch) symbol,
    // same contract.
    let result_csum = unsafe { bindings::do_csum(buff.cast(), len) };
    let mut result = result_csum;

    // add in old sum, and carry..
    result = result.wrapping_add(wsum);
    if wsum > result {
        result = result.wrapping_add(1);
    }
    result
}

/// Miscellaneous IP-like checksum (e.g. ICMP).
///
/// # Safety
/// `buff` points to `len` valid bytes when `len > 0` (C ABI contract).
#[export]
pub unsafe extern "C" fn ip_compute_csum(buff: *const c_void, len: c_int) -> u16 {
    // SAFETY: per function contract.
    let sum = unsafe { bindings::do_csum(buff.cast(), len) };
    !(sum as u16)
}

/// TCP/UDP pseudo-header checksum, unfolded (32-bit result).
#[export]
pub unsafe extern "C" fn csum_tcpudp_nofold(
    saddr: u32,
    daddr: u32,
    len: u32,
    proto: u8,
    sum: u32,
) -> u32 {
    let mut s: u64 = sum as u64;

    s += saddr as u64;
    s += daddr as u64;
    // __LITTLE_ENDIAN branch — riscv is always LE, matches the C's
    // #ifdef __LITTLE_ENDIAN arm exactly.
    s += ((proto as u32).wrapping_add(len) as u64) << 8;

    from64to32(s)
}
