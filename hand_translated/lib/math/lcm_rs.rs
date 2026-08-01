// SPDX-License-Identifier: GPL-2.0-only
//! Lowest common multiple — Rust translation of `lib/math/lcm.c`.

use kernel::bindings;
use kernel::ffi::c_ulong;
use kernel::prelude::*;

/// Lowest common multiple.
#[export]
pub unsafe extern "C" fn lcm(a: c_ulong, b: c_ulong) -> c_ulong {
    if a != 0 && b != 0 {
        // Cross-TU call through the C ABI, as in the original (gcd lives
        // in its own TU). SAFETY: gcd has no preconditions.
        let g = unsafe { bindings::gcd(a, b) };
        // C multiplication wraps on overflow; keep that semantic.
        (a / g).wrapping_mul(b)
    } else {
        0
    }
}

/// Like `lcm`, but `0` inputs fall back to the nonzero one.
#[export]
pub unsafe extern "C" fn lcm_not_zero(a: c_ulong, b: c_ulong) -> c_ulong {
    // SAFETY: `lcm` has no preconditions.
    let l = unsafe { lcm(a, b) };

    if l != 0 {
        return l;
    }

    // C: return (b ? : a);
    if b != 0 { b } else { a }
}
