// SPDX-License-Identifier: GPL-2.0
//! Rational fractions — Rust translation of `lib/math/rational.c`.
//!
//! Copyright (C) 2009 emlix GmbH, Oskar Schirmer <oskar@scara.com>
//! Copyright (C) 2019 Trent Piepho <tpiepho@gmail.com>
//!
//! Best rational approximation via continued fractions (see the C original
//! for the full commentary). MODULE_DESCRIPTION/MODULE_LICENSE dropped:
//! no-ops for builtin code (CONFIG_MODULES=n corpus).

use kernel::ffi::c_ulong;
use kernel::prelude::*;

/// Calculate best rational approximation for a given fraction, respecting
/// register-size limits on numerator and denominator.
#[export]
pub unsafe extern "C" fn rational_best_approximation(
    given_numerator: c_ulong,
    given_denominator: c_ulong,
    max_numerator: c_ulong,
    max_denominator: c_ulong,
    best_numerator: *mut c_ulong,
    best_denominator: *mut c_ulong,
) {
    let mut n = given_numerator;
    let mut d = given_denominator;
    let mut n0: c_ulong = 0;
    let mut d1: c_ulong = 0;
    let mut n1: c_ulong = 1;
    let mut d0: c_ulong = 1;

    loop {
        if d == 0 {
            break;
        }
        // Next continued-fraction term 'a' via the Euclidean algorithm.
        let dp = d;
        let a = n / d;
        d = n % d;
        n = dp;

        // Current convergent n2/d2. C unsigned arithmetic wraps; keep it.
        let n2 = n0.wrapping_add(a.wrapping_mul(n1));
        let d2 = d0.wrapping_add(a.wrapping_mul(d1));

        // If the convergent exceeds the maxes, return the previous
        // convergent or the largest semi-convergent (term 't').
        if n2 > max_numerator || d2 > max_denominator {
            let mut t = c_ulong::MAX;

            if d1 != 0 {
                t = (max_denominator - d0) / d1;
            }
            if n1 != 0 {
                t = core::cmp::min(t, (max_numerator - n0) / n1);
            }

            // Choose the semi-convergent if it's closer than the previous
            // convergent (or if this is the first iteration: d1 == 0).
            if d1 == 0
                || 2 * t > a
                || (2 * t == a && d0.wrapping_mul(dp) > d1.wrapping_mul(d))
            {
                n1 = n0.wrapping_add(t.wrapping_mul(n1));
                d1 = d0.wrapping_add(t.wrapping_mul(d1));
            }
            break;
        }
        n0 = n1;
        n1 = n2;
        d0 = d1;
        d1 = d2;
    }
    // SAFETY: caller passes valid output pointers (C ABI contract).
    unsafe {
        *best_numerator = n1;
        *best_denominator = d1;
    }
}
