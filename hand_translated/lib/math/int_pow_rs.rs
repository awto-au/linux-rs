// SPDX-License-Identifier: GPL-2.0
//! Integer power — Rust translation of `lib/math/int_pow.c`.

use kernel::ffi::c_uint;
use kernel::prelude::*;

/// Computes: pow(base, exp), i.e. `base` raised to the `exp` power.
/// C multiplication wraps on overflow; `wrapping_mul` keeps that semantic.
#[export]
pub unsafe extern "C" fn int_pow(mut base: u64, mut exp: c_uint) -> u64 {
    let mut result: u64 = 1;

    while exp != 0 {
        if exp & 1 != 0 {
            result = result.wrapping_mul(base);
        }
        exp >>= 1;
        base = base.wrapping_mul(base);
    }

    result
}
