// SPDX-License-Identifier: GPL-2.0
//! Software Hamming weight — Rust translation of `lib/hweight.c`.
//!
//! Faithful bit-parallel popcount; `u32::count_ones()` is the obvious
//! optimised-lane candidate but per the discipline this stays literal
//! (these ARE the arch fallbacks when hardware popcount is absent).

use kernel::ffi::c_uint;
use kernel::prelude::*;

/// Hamming weight of a 32-bit word.
#[export]
pub unsafe extern "C" fn __sw_hweight32(w: c_uint) -> c_uint {
    #[cfg(CONFIG_ARCH_HAS_FAST_MULTIPLIER)]
    {
        let mut w = w;
        w -= (w >> 1) & 0x55555555;
        w = (w & 0x33333333) + ((w >> 2) & 0x33333333);
        w = (w + (w >> 4)) & 0x0f0f0f0f;
        w.wrapping_mul(0x01010101) >> 24
    }
    #[cfg(not(CONFIG_ARCH_HAS_FAST_MULTIPLIER))]
    {
        let mut res = w - ((w >> 1) & 0x55555555);
        res = (res & 0x33333333) + ((res >> 2) & 0x33333333);
        res = (res + (res >> 4)) & 0x0F0F0F0F;
        res += res >> 8;
        (res + (res >> 16)) & 0x000000FF
    }
}

/// Hamming weight of a 16-bit word.
#[export]
pub unsafe extern "C" fn __sw_hweight16(w: c_uint) -> c_uint {
    let mut res = w - ((w >> 1) & 0x5555);
    res = (res & 0x3333) + ((res >> 2) & 0x3333);
    res = (res + (res >> 4)) & 0x0F0F;
    (res + (res >> 8)) & 0x00FF
}

/// Hamming weight of an 8-bit word.
#[export]
pub unsafe extern "C" fn __sw_hweight8(w: c_uint) -> c_uint {
    let mut res = w - ((w >> 1) & 0x55);
    res = (res & 0x33) + ((res >> 2) & 0x33);
    (res + (res >> 4)) & 0x0F
}

/// Hamming weight of a 64-bit word.
#[export]
pub unsafe extern "C" fn __sw_hweight64(w: u64) -> kernel::ffi::c_ulong {
    #[cfg(not(CONFIG_64BIT))]
    {
        // SAFETY: plain arithmetic helpers.
        unsafe {
            (__sw_hweight32((w >> 32) as c_uint) + __sw_hweight32(w as c_uint)) as _
        }
    }
    #[cfg(all(CONFIG_64BIT, CONFIG_ARCH_HAS_FAST_MULTIPLIER))]
    {
        let mut w = w;
        w -= (w >> 1) & 0x5555555555555555;
        w = (w & 0x3333333333333333) + ((w >> 2) & 0x3333333333333333);
        w = (w + (w >> 4)) & 0x0f0f0f0f0f0f0f0f;
        (w.wrapping_mul(0x0101010101010101) >> 56) as kernel::ffi::c_ulong
    }
    #[cfg(all(CONFIG_64BIT, not(CONFIG_ARCH_HAS_FAST_MULTIPLIER)))]
    {
        let mut res = w - ((w >> 1) & 0x5555555555555555);
        res = (res & 0x3333333333333333) + ((res >> 2) & 0x3333333333333333);
        res = (res + (res >> 4)) & 0x0F0F0F0F0F0F0F0F;
        res += res >> 8;
        res += res >> 16;
        ((res + (res >> 32)) & 0x00000000000000FF) as kernel::ffi::c_ulong
    }
}
