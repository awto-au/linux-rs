// SPDX-License-Identifier: GPL-2.0-only
//! Greatest common divisor — Rust translation of `lib/math/gcd.c`.
//!
//! linux-rs Phase 2: first translated TU. Binary (Stein) GCD when `__ffs`
//! is efficient, even/odd halving algorithm otherwise — selected, as in C,
//! by the `efficient_ffs_key` static key, which riscv *disables* at boot
//! when the Zbb extension is absent (arch/riscv/kernel/setup.c). The key is
//! read by value (`static_key_count`) rather than as a patched branch: the
//! kernel crate has `static_branch_unlikely!` only for false-keys as of
//! v7.1, and this config builds without CONFIG_JUMP_LABEL anyway.

use kernel::bindings;
use kernel::ffi::c_ulong;
use kernel::prelude::*;

/// `static_branch_likely(&efficient_ffs_key)`, value-equivalent form.
#[inline]
fn efficient_ffs() -> bool {
    // SAFETY: `efficient_ffs_key` is a static key defined by C in gcd_rs
    // (declared in <linux/gcd.h>); `static_key_count` only reads it.
    unsafe {
        bindings::static_key_count(
            core::ptr::addr_of_mut!(bindings::efficient_ffs_key)
                .cast::<bindings::static_key>(),
        ) > 0
    }
}

/// Binary GCD: fast when trailing_zeros (`__ffs`) is cheap.
/// Callers guarantee `a != 0 && b != 0`.
fn binary_gcd(mut a: c_ulong, mut b: c_ulong) -> c_ulong {
    let r = a | b;
    b >>= b.trailing_zeros();
    if b == 1 {
        return r & r.wrapping_neg();
    }
    loop {
        a >>= a.trailing_zeros();
        if a == 1 {
            return r & r.wrapping_neg();
        }
        if a == b {
            return a << r.trailing_zeros();
        }
        if a < b {
            core::mem::swap(&mut a, &mut b);
        }
        a -= b;
    }
}

/// Even/odd halving GCD: wins when bit-scan is a loop.
/// Callers guarantee `a != 0 && b != 0`.
fn odd_even_gcd(mut a: c_ulong, mut b: c_ulong) -> c_ulong {
    let mut r = a | b;
    // Isolate lsbit of r.
    r &= r.wrapping_neg();

    while b & r == 0 {
        b >>= 1;
    }
    if b == r {
        return r;
    }
    loop {
        while a & r == 0 {
            a >>= 1;
        }
        if a == r {
            return r;
        }
        if a == b {
            return a;
        }
        if a < b {
            core::mem::swap(&mut a, &mut b);
        }
        a -= b;
        a >>= 1;
        // `a` was halved above, so `a + b` cannot exceed c_ulong::MAX —
        // same invariant the C version relies on.
        if a & r != 0 {
            a += b;
        }
        a >>= 1;
    }
}

/// Calculate and return the greatest common divisor of 2 unsigned longs.
///
/// Signature checked against the declaration in `<linux/gcd.h>` by
/// `#[export]`; exported as `EXPORT_SYMBOL_GPL`, matching the C original.
#[export]
pub unsafe extern "C" fn gcd(a: c_ulong, b: c_ulong) -> c_ulong {
    if a == 0 || b == 0 {
        return a | b;
    }
    if efficient_ffs() {
        binary_gcd(a, b)
    } else {
        odd_even_gcd(a, b)
    }
}
