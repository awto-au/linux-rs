// SPDX-License-Identifier: GPL-2.0
//! Reciprocal division (Barrett-style) — Rust translation of
//! `lib/math/reciprocal_div.c`. See `<linux/reciprocal_div.h>` for the
//! algorithm description (magic-number division, avoiding a real divide
//! on the hot path).

use kernel::bindings;
use kernel::prelude::*;
use kernel::warn_on;

/// Precompute the reciprocal-multiplication constants for dividing by `d`.
#[export]
pub unsafe extern "C" fn reciprocal_value(d: u32) -> bindings::reciprocal_value {
    // C: l = fls(d - 1) — fls is 1-based, defined at 0 -> 0.
    let dm1 = d.wrapping_sub(1);
    let l: i32 = (32 - dm1.leading_zeros()) as i32;

    let mut m: u64 = (1u64 << 32).wrapping_mul((1u64 << l).wrapping_sub(d as u64));
    // C: do_div(m, d) — truncating unsigned division, remainder discarded.
    m /= d as u64;
    m += 1;

    bindings::reciprocal_value {
        m: m as u32,
        sh1: core::cmp::min(l, 1) as u8,
        sh2: core::cmp::max(l - 1, 0) as u8,
    }
}

/// Like `reciprocal_value`, with an extra `prec` bits of precision control.
///
/// Caller must ensure `ceil(log2(d)) != 32` (see the C header comment);
/// that case overflows u64 and is diagnosed here via `warn_on!`, exactly
/// as the C `WARN()` diagnosed it — MINUS the formatted message, since the
/// kernel crate has no `warn!(cond, fmt, args)` as of v7.1 (only
/// condition-only `warn_on!`). Deviation tracked; upgrade when available.
#[export]
pub unsafe extern "C" fn reciprocal_value_adv(
    d: u32,
    prec: u8,
) -> bindings::reciprocal_value_adv {
    // ceil(log2(d))
    let dm1 = d.wrapping_sub(1);
    let l: u32 = 32 - dm1.leading_zeros();

    warn_on!(l == 32);

    let mut post_shift = l;
    let mut mlow: u64 = (1u64 << (32 + l)) / d as u64;
    let mut mhigh: u64 =
        ((1u64 << (32 + l)) + (1u64 << (32 + l - prec as u32))) / d as u64;

    while post_shift > 0 {
        let lo = mlow >> 1;
        let hi = mhigh >> 1;

        if lo >= hi {
            break;
        }
        mlow = lo;
        mhigh = hi;
        post_shift -= 1;
    }

    bindings::reciprocal_value_adv {
        m: mhigh as u32,
        sh: post_shift as u8,
        exp: l as u8,
        is_wide_m: mhigh > u32::MAX as u64,
    }
}
