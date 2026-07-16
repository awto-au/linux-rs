// SPDX-License-Identifier: GPL-2.0
//! Host benchmark: faithful Rust translations (as shipped in patches/)
//! vs optimised idiomatic Rust, over the same fixed-seed LCG stream as
//! cref.c. Build: rustc -O (see scripts/bench_math.py). Host-indicative
//! only — target numbers come from the FPGA later.

use std::hint::black_box;
use std::time::Instant;

// ---- faithful translations (kernel-crate bits removed) ----

fn gcd_faithful(mut a: u64, mut b: u64) -> u64 {
    let r = a | b;
    if a == 0 || b == 0 {
        return r;
    }
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

fn int_sqrt_faithful(mut x: u64) -> u64 {
    let mut y: u64 = 0;
    if x <= 1 {
        return x;
    }
    let mut m: u64 = 1 << ((u64::BITS - 1 - x.leading_zeros()) & !1);
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

fn int_pow_faithful(mut base: u64, mut exp: u32) -> u64 {
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

const LOGTABLE: [u16; 256] = include!("logtable.inc");

fn intlog2_faithful(value: u32) -> u32 {
    if value == 0 {
        return 0;
    }
    let msb = u32::BITS - value.leading_zeros() - 1;
    let significand = value << (31 - msb);
    let logentry = ((significand >> 23) as usize) % LOGTABLE.len();
    let diff = ((LOGTABLE[(logentry + 1) % LOGTABLE.len()] as i32
        - LOGTABLE[logentry] as i32)
        & 0xffff) as u32;
    let interpolation = ((significand & 0x7fffff).wrapping_mul(diff)) >> 15;
    (msb << 24) + ((LOGTABLE[logentry] as u32) << 8) + interpolation
}

// ---- optimised variants ----

/// std's isqrt (Karatsuba-style / hardware-assisted where possible).
fn int_sqrt_opt(x: u64) -> u64 {
    x.isqrt()
}

/// Branch-reduced binary GCD (idiomatic; same algorithm family).
fn gcd_opt(a: u64, b: u64) -> u64 {
    if a == 0 || b == 0 {
        return a | b;
    }
    let shift = (a | b).trailing_zeros();
    let (mut a, mut b) = (a >> a.trailing_zeros(), b >> b.trailing_zeros());
    while a != b {
        if a > b {
            a -= b;
            a >>= a.trailing_zeros();
        } else {
            b -= a;
            b >>= b.trailing_zeros();
        }
    }
    a << shift
}

#[inline]
fn lcg(s: &mut u64) -> u64 {
    *s = s
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *s
}

fn bench(name: &str, n: u64, mut f: impl FnMut(u64, u64) -> u64) {
    let mut s: u64 = 0x123456789abcdef0;
    let mut acc: u64 = 0;
    let t0 = Instant::now();
    for _ in 0..n {
        let va = lcg(&mut s);
        let vb = lcg(&mut s);
        acc = acc.wrapping_add(f(va, vb));
    }
    let dt = t0.elapsed().as_nanos() as f64;
    println!("{},{:.6},{}", name, dt / n as f64, black_box(acc));
}

fn main() {
    let n: u64 = std::env::args()
        .nth(1)
        .and_then(|a| a.parse().ok())
        .unwrap_or(10_000_000);
    bench("rust-faithful,gcd", n, |a, b| gcd_faithful(a, b | 1));
    bench("rust-opt,gcd", n, |a, b| gcd_opt(a, b | 1));
    bench("rust-faithful,int_sqrt", n, |a, _| int_sqrt_faithful(a));
    bench("rust-opt,int_sqrt", n, |a, _| int_sqrt_opt(a));
    bench("rust-faithful,int_pow", n, |a, b| {
        int_pow_faithful(a | 1, (b & 63) as u32)
    });
    bench("rust-faithful,intlog2", n, |a, _| {
        intlog2_faithful((a | 1) as u32) as u64
    });

    // Equivalence spot-check (1M random inputs) — optimised variants must
    // agree with faithful before their numbers mean anything.
    let mut s: u64 = 42;
    for _ in 0..1_000_000u64 {
        let a = lcg(&mut s);
        let b = lcg(&mut s) | 1;
        assert_eq!(gcd_faithful(a, b), gcd_opt(a, b));
        assert_eq!(int_sqrt_faithful(a), int_sqrt_opt(a));
    }
    println!("equivalence,ok,1000000");
}
