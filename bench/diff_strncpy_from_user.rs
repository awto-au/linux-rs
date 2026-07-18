// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side,
//! strncpy_from_user. Faithful host-side copy of
//! lib/strncpy_from_user_rs.rs's do_strncpy_from_user algorithm
//! (kernel-crate bindings/export/unsafe_get_user shims stripped,
//! replaced with plain slice reads since there is no real userspace
//! fault boundary on the host) -- same protocol/LCG and same
//! fault-free-arithmetic-only scope as diff_strncpy_from_user.c (see
//! that file's header comment for the two distinct -EFAULT triggers
//! and why mode 5's max<count case genuinely exercises one of them).

fn has_zero(val: usize, one_bits: usize, high_bits: usize) -> usize {
    ((val.wrapping_sub(one_bits)) & !val) & high_bits
}
fn create_zero_mask(bits: usize) -> usize {
    let bits = bits.wrapping_sub(1) & !bits;
    bits >> 7
}
fn fls64(x: u64) -> i32 {
    if x == 0 {
        0
    } else {
        (63 - x.leading_zeros() as i32) + 1
    }
}
fn find_zero(mask: usize) -> usize {
    (fls64(mask as u64) >> 3) as usize
}

fn is_unaligned(src: *const u8, dst: *const u8) -> bool {
    ((dst as isize) | (src as isize)) & (core::mem::size_of::<usize>() as isize - 1) != 0
}

// Mirrors lib/strncpy_from_user_rs.rs's do_strncpy_from_user exactly
// (same two-loop shape, same word/byte boundary arithmetic), minus the
// unsafe_get_user fault-shim calls (plain slice reads instead -- no
// fault possible on a host Vec, and this oracle never lets `max`
// exceed the backing buffer, see file header) and the SAFETY comments
// (no unsafe user-pointer contract on the host side).
fn do_strncpy_from_user(dst: &mut [u8], src: &[u8], count: usize, max: usize) -> isize {
    let mut res: usize = 0;
    let mut max = max;

    let src_ptr = src.as_ptr();
    let dst_ptr = dst.as_ptr();

    if !is_unaligned(src_ptr, dst_ptr) {
        while max >= core::mem::size_of::<usize>() {
            let c = usize::from_ne_bytes(
                src[res..res + core::mem::size_of::<usize>()]
                    .try_into()
                    .unwrap(),
            );

            const ONE_BITS: usize = usize::MAX / 0xff;
            const HIGH_BITS: usize = ONE_BITS * 0x80;
            let data = has_zero(c, ONE_BITS, HIGH_BITS);
            if data != 0 {
                let data = create_zero_mask(data);
                let mask = data; // zero_bytemask(mask) == mask on riscv
                let masked = c & mask;
                dst[res..res + core::mem::size_of::<usize>()]
                    .copy_from_slice(&masked.to_ne_bytes());
                return (res + find_zero(data)) as isize;
            }

            dst[res..res + core::mem::size_of::<usize>()].copy_from_slice(&c.to_ne_bytes());

            res += core::mem::size_of::<usize>();
            max -= core::mem::size_of::<usize>();
        }
    }

    // byte_at_a_time:
    while max != 0 {
        let c = src[res];
        dst[res] = c;
        if c == 0 {
            return res as isize;
        }
        res += 1;
        max -= 1;
    }

    if res >= count {
        return res as isize;
    }

    -14 // -EFAULT
}

fn strncpy_from_user(dst: &mut [u8], src: &[u8], count: isize, max: usize) -> isize {
    if count <= 0 {
        return 0;
    }
    do_strncpy_from_user(dst, src, count as usize, max)
}

// Identical LCG to diff_strncpy_from_user.c.
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

const ALPHABET: &[u8] = b"abcXYZ .";

fn gen_str(rng: &mut Lcg, maxlen: usize) -> Vec<u8> {
    let len = 1 + (rng.next() as usize) % (maxlen - 1);
    let mut v = Vec::with_capacity(len + 1);
    for _ in 0..len {
        v.push(ALPHABET[(rng.next() as usize) % ALPHABET.len()]);
    }
    v.push(0);
    v
}

const BUFLEN: usize = 96;

fn cstr(v: &[u8]) -> &str {
    let end = v.iter().position(|&b| b == 0).unwrap_or(v.len());
    std::str::from_utf8(&v[..end]).unwrap()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(424242);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        // Backing arrays oversized vs BUFLEN and offset by a
        // runtime-chosen byte so src/dst alignment varies across
        // cases -- exercises IS_UNALIGNED's real branch, matching
        // diff_strncpy_from_user.c's approach exactly.
        let src_off = (rng.next() as usize) % 8;
        let dst_off = (rng.next() as usize) % 8;

        let mut src_backing = vec![0u8; BUFLEN + 16];
        let mut dst_backing = vec![0x55u8; BUFLEN + 16];

        let s = gen_str(&mut rng, BUFLEN - 16);
        src_backing[src_off..src_off + s.len()].copy_from_slice(&s);
        let slen = s.len() - 1; // gen_str includes trailing NUL in its Vec

        let mode = rng.next() % 6;
        let count: isize = match mode {
            0 => 0,
            1 => -((rng.next() % 4) as isize) - 1,
            2 => (rng.next() as usize % (slen + 1)) as isize,
            3 => (slen + 1) as isize,
            4 => (slen + 1 + (rng.next() as usize % 8)) as isize,
            _ => (slen + 1 + (rng.next() as usize % 8)) as isize, // mode 5: max<count below
        };

        // max == count for every mode except 5, which deliberately
        // sets max < count (a real "hit the address-space budget
        // before satisfying count" case -- pure arithmetic, see
        // diff_strncpy_from_user.c's file header point (b)) to
        // exercise the non-fault -EFAULT return.
        let mut max: usize = if count > 0 { count as usize } else { 0 };
        if mode == 5 && max > 0 {
            max = (rng.next() as usize) % max; // strictly less than count
        }

        let src = &src_backing[src_off..];
        let dst = &mut dst_backing[dst_off..];

        let r = strncpy_from_user(dst, src, count, max);

        let out = if r > 0 { cstr(&dst[..r as usize]) } else { "" };
        println!("strncpy_from_user,{},{},{},[{}]", cstr(&s), count, r, out);
    }
}
