// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, bcd. Faithful
//! copy of lib/bcd_rs.rs's algorithm (kernel-crate bindings/export
//! stripped) — same protocol/LCG as diff_bcd.c.

fn bcd2bin(val: u8) -> u32 {
    ((val & 0x0f) as u32) + ((val >> 4) as u32) * 10
}

fn bin2bcd(val: u32) -> u8 {
    let t = val.wrapping_mul(103) >> 10;
    ((t << 4) | (val - t * 10)) as u8
}

// Identical LCG to diff_bcd.c.
struct Lcg(u64);
impl Lcg {
    fn next(&mut self) -> u32 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (self.0 >> 32) as u32
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let val = (rng.next() & 0xff) as u8;
        let r = bcd2bin(val);
        println!("bcd2bin,{},{}", val, r);
    }

    for _ in 0..n {
        let val = rng.next() % 100;
        let r = bin2bcd(val);
        println!("bin2bcd,{},{}", val, r);
    }
}
