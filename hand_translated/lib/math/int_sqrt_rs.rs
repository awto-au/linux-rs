// SPDX-License-Identifier: GPL-2.0
//! Integer square root — Rust translation of `lib/math/int_sqrt.c`.
//!
//! Shift-and-subtract algorithm (Guy L. Steele). `int_sqrt64` is not
//! translated: it exists only when `BITS_PER_LONG < 64` and this corpus is
//! rv64.

use kernel::ffi::c_ulong;
use kernel::prelude::*;

/// Computes: floor(sqrt(x)).
#[export]
pub unsafe extern "C" fn int_sqrt(mut x: c_ulong) -> c_ulong {
    let mut y: c_ulong = 0;

    if x <= 1 {
        return x;
    }

    // C: m = 1UL << (__fls(x) & ~1UL) — __fls is the 0-based MSB index,
    // defined only for x != 0 (guaranteed by the guard above).
    let mut m: c_ulong = 1 << ((c_ulong::BITS - 1 - x.leading_zeros()) & !1);
    while m != 0 {
        let b = y + m;
        y >>= 1;

        if x >= b {
            x -= b;
            y += m;
        }
        m >>= 2;
    }

    y
}
