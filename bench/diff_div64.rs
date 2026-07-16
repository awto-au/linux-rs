// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, div64.
//! Faithful copy of lib/math/div64_rs.rs's algorithm (kernel-crate
//! bindings/export stripped) — same protocol/LCG as diff_div64.c.

fn mul_u32_u32(a: u32, b: u32) -> u64 {
    a as u64 * b as u64
}
fn add_u64_u32(a: u64, b: u32) -> u64 {
    a + b as u64
}
fn mul_add(a: u32, b: u32, c: u32) -> u64 {
    add_u64_u32(mul_u32_u32(a, b), c)
}

fn mul_u64_u64_add_u64(a: u64, b: u64, c: u64) -> (u64, u64) {
    let x = mul_add(a as u32, b as u32, c as u32);
    let mut y = mul_add(a as u32, (b >> 32) as u32, (c >> 32) as u32);
    y = add_u64_u32(y, (x >> 32) as u32);
    let z = mul_add((a >> 32) as u32, (b >> 32) as u32, (y >> 32) as u32);
    y = mul_add((a >> 32) as u32, b as u32, y as u32);
    let p_lo = (y << 32) + (x as u32) as u64;
    (add_u64_u32(z, (y >> 32) as u32), p_lo)
}

const BITS_PER_ITER: u32 = 32;

fn mul_u64_long_add_u64(a: u64, b: u64, c: u64) -> (u64, u64) {
    mul_u64_u64_add_u64(a, b, c)
}

fn iter_div_u64_rem(mut dividend: u64, divisor: u32) -> (u32, u64) {
    let mut ret: u32 = 0;
    while dividend >= divisor as u64 {
        dividend -= divisor as u64;
        ret += 1;
    }
    (ret, dividend)
}

fn mul_u64_add_u64_div_u64(a: u64, b: u64, c: u64, d: u64) -> u64 {
    let (mut n_hi, mut n_lo) = mul_u64_u64_add_u64(a, b, c);

    if n_hi == 0 {
        return n_lo / d;
    }

    if n_hi >= d {
        if d == 0 {
            let zero: u64 = core::hint::black_box(0);
            return u64::MAX / zero;
        }
        return u64::MAX;
    }

    let mut d = d;
    let d_z_hi = d.leading_zeros();
    if d_z_hi != 0 {
        d <<= d_z_hi;
        n_hi = (n_hi << d_z_hi) | (n_lo >> (64 - d_z_hi));
        n_lo <<= d_z_hi;
    }

    let mut reps = 64 / BITS_PER_ITER;
    if (n_hi >> 32) as u32 == 0 {
        reps -= 32 / BITS_PER_ITER;
        n_hi = (n_hi << 32) | (n_lo >> 32);
        n_lo <<= 32;
    }

    n_lo = !n_lo;
    n_hi = !n_hi;

    let d_msig: u64 = (d >> (64 - BITS_PER_ITER)) + 1;

    let mut quotient: u64 = 0;
    while reps > 0 {
        reps -= 1;
        let mut q_digit: u64 = (!n_hi >> (64 - 2 * BITS_PER_ITER)) / d_msig;
        let mut overflow: u32 = (n_hi >> (64 - BITS_PER_ITER)) as u32;
        n_hi = add_u64_u32(n_hi << BITS_PER_ITER, (n_lo >> (64 - BITS_PER_ITER)) as u32);
        n_lo <<= BITS_PER_ITER;
        let (carry, new_n_hi) = mul_u64_long_add_u64(d, q_digit, n_hi);
        n_hi = new_n_hi;
        overflow = overflow.wrapping_add(carry as u32);
        while overflow < (0xffffffffu32 >> (32 - BITS_PER_ITER)) {
            q_digit += 1;
            n_hi += d;
            overflow = overflow.wrapping_add((n_hi < d) as u32);
        }
        quotient = (quotient << BITS_PER_ITER) + q_digit;
    }

    if n_hi.wrapping_add(d) > n_hi {
        quotient += 1;
    }
    quotient
}

// Identical LCG to diff_div64.c.
struct Lcg(u64);
impl Lcg {
    fn next(&mut self) -> u32 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (self.0 >> 32) as u32
    }
    fn next64(&mut self) -> u64 {
        let hi = self.next() as u64;
        let lo = self.next() as u64;
        (hi << 32) | lo
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let a = rng.next64();
        let b = rng.next64();
        let c = rng.next64();
        let d = if rng.next() % 4 == 0 {
            rng.next64() | 1
        } else {
            (rng.next() % 1_000_000) as u64 + 1
        };

        let r1 = mul_u64_add_u64_div_u64(a, b, c, d);
        println!("muladddiv,{},{},{},{},{}", a, b, c, d, r1);

        // Keep dividend within a bounded multiple of divisor — see the
        // matching comment in diff_div64.c.
        let divisor = (rng.next() % 1_000_000) + 1;
        let dividend = divisor as u64 * (rng.next() % 100_000) as u64 + (rng.next() % divisor) as u64;
        let (q, rem) = iter_div_u64_rem(dividend, divisor);
        println!("iterdiv,{},{},{},{}", dividend, divisor, q, rem);
    }
}
