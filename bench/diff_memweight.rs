// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, memweight.
//! Faithful copy of lib/memweight_rs.rs's algorithm (kernel-crate
//! bindings/export stripped) — same protocol/LCG as diff_memweight.c.

const BITS_PER_LONG: usize = usize::BITS as usize;
const SIZEOF_LONG: usize = core::mem::size_of::<usize>();
const INT_MAX: usize = i32::MAX as usize;

fn hweight8(mut w: u8) -> u32 {
    let mut c = 0u32;
    while w != 0 {
        c += (w & 1) as u32;
        w >>= 1;
    }
    c
}

fn bitmap_weight(bitmap: &[usize], nbits: u32) -> u32 {
    let n = nbits as usize / BITS_PER_LONG;
    let rem = nbits as usize % BITS_PER_LONG;
    let mut c = 0u32;
    for &w in &bitmap[..n] {
        c += w.count_ones();
    }
    if rem != 0 {
        let mask = if rem == BITS_PER_LONG {
            usize::MAX
        } else {
            (1usize << rem) - 1
        };
        c += (bitmap[n] & mask).count_ones();
    }
    c
}

fn memweight(ptr: *const u8, mut bytes: usize) -> usize {
    let mut ret: usize = 0;
    let mut bitmap = ptr;

    while bytes > 0 && (bitmap as usize) % SIZEOF_LONG != 0 {
        let b = unsafe { *bitmap };
        ret += hweight8(b) as usize;
        bytes -= 1;
        bitmap = unsafe { bitmap.add(1) };
    }

    let longs = bytes / SIZEOF_LONG;
    if longs != 0 {
        if longs >= INT_MAX / BITS_PER_LONG {
            panic!("BUG_ON hit (test bug, size too large)");
        }
        // SAFETY: `bitmap` is SIZEOF_LONG-aligned here (host allocation is
        // byte-granular so this cast needs read_unaligned in general, but
        // the loop above guarantees alignment before this point).
        let words = unsafe {
            core::slice::from_raw_parts(bitmap as *const usize, longs)
        };
        ret += bitmap_weight(words, (longs * BITS_PER_LONG) as u32) as usize;
        bytes -= longs * SIZEOF_LONG;
        bitmap = unsafe { bitmap.add(longs * SIZEOF_LONG) };
    }

    while bytes > 0 {
        let b = unsafe { *bitmap };
        ret += hweight8(b) as usize;
        bytes -= 1;
        bitmap = unsafe { bitmap.add(1) };
    }

    ret
}

// Identical LCG to diff_memweight.c.
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

const MAXLEN: usize = 200;

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

        let w = memweight(unsafe { storage.as_ptr().add(offset) }, len);
        println!("weight,{},{},{}", offset, len, w);
    }
}
