// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, checksum.
//! Faithful copy of lib/checksum_rs.rs's algorithm (kernel-crate
//! bindings/export stripped) — same protocol/LCG as diff_checksum.c.
//!
//! Scope matches lib/checksum_rs.rs: csum_partial, ip_compute_csum,
//! csum_tcpudp_nofold. do_csum itself is a cross-TU call in the real
//! translation (bindings::do_csum, the arch/riscv version); here we
//! provide a faithful generic do_csum as the stand-in both languages
//! call, per diff_checksum.c's header comment.

fn csum_from32to16(mut sum: u32) -> u32 {
    sum = (sum & 0xffff) + (sum >> 16);
    sum = (sum & 0xffff) + (sum >> 16);
    sum
}

fn do_csum(buff: &[u8]) -> u32 {
    let len = buff.len() as isize;
    if len <= 0 {
        return 0;
    }
    let mut i: usize = 0;
    let mut len = len;
    let mut result: u32 = 0;

    let odd = (buff.as_ptr() as usize) & 1;
    if odd != 0 {
        result += (buff[i] as u32) << 8;
        len -= 1;
        i += 1;
    }
    if len >= 2 {
        if (buff.as_ptr() as usize + i) & 2 != 0 {
            let w = u16::from_ne_bytes([buff[i], buff[i + 1]]);
            result += w as u32;
            len -= 2;
            i += 2;
        }
        if len >= 4 {
            let end = i + ((len as usize) & !3);
            let mut carry: u32 = 0;
            loop {
                let w = u32::from_ne_bytes([buff[i], buff[i + 1], buff[i + 2], buff[i + 3]]);
                i += 4;
                result = result.wrapping_add(carry);
                let (r, ov) = result.overflowing_add(w);
                result = r;
                carry = if w > result { 1 } else { 0 };
                if i >= end {
                    break;
                }
                let _ = ov;
            }
            result = result.wrapping_add(carry);
            result = (result & 0xffff) + (result >> 16);
        }
        if len & 2 != 0 {
            let w = u16::from_ne_bytes([buff[i], buff[i + 1]]);
            result += w as u32;
            i += 2;
        }
    }
    if len & 1 != 0 {
        result += buff[i] as u32;
    }
    result = csum_from32to16(result);
    if odd != 0 {
        result = ((result >> 8) & 0xff) | ((result & 0xff) << 8);
    }
    result
}

fn csum_partial(buff: &[u8], wsum: u32) -> u32 {
    let sum = wsum;
    let mut result = do_csum(buff);
    result = result.wrapping_add(sum);
    if sum > result {
        result = result.wrapping_add(1);
    }
    result
}

fn ip_compute_csum(buff: &[u8]) -> u16 {
    let sum = do_csum(buff);
    !(sum as u16)
}

fn from64to32(mut x: u64) -> u32 {
    x = (x & 0xffffffff).wrapping_add(x >> 32);
    x = (x & 0xffffffff).wrapping_add(x >> 32);
    x as u32
}

fn csum_tcpudp_nofold(saddr: u32, daddr: u32, len: u32, proto: u8, sum: u32) -> u32 {
    let mut s: u64 = sum as u64;
    s += saddr as u64;
    s += daddr as u64;
    s += ((proto as u32).wrapping_add(len) as u64) << 8;
    from64to32(s)
}

// Identical LCG to diff_checksum.c.
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

const MAXLEN: usize = 260;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    let mut storage = [0u8; MAXLEN + 16];

    for _ in 0..n {
        let offset = (rng.next() % 8) as usize;
        let len = (rng.next() as usize) % (MAXLEN - offset);
        for k in 0..len {
            storage[offset + k] = (rng.next() & 0xff) as u8;
        }
        let wsum = rng.next();

        let r1 = csum_partial(&storage[offset..offset + len], wsum);
        println!("partial,{},{},{},{}", offset, len, wsum, r1);

        let r2 = ip_compute_csum(&storage[offset..offset + len]);
        println!("compute,{},{},{}", offset, len, r2);
    }

    for _ in 0..n {
        let saddr = rng.next();
        let daddr = rng.next();
        let len = rng.next() & 0xffff;
        let proto = (rng.next() & 0xff) as u8;
        let sum = rng.next();
        let r = csum_tcpudp_nofold(saddr, daddr, len, proto, sum);
        println!("tcpudp,{},{},{},{},{},{}", saddr, daddr, len, proto, sum, r);
    }
}
