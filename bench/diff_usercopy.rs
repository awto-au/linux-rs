// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, check_zeroed_user
//! (lib/usercopy_rs.rs). Faithful host-side copy of
//! lib/usercopy_rs.rs's check_zeroed_user algorithm (kernel-crate
//! bindings/export/unsafe_get_user shims stripped, replaced with plain
//! slice reads since there is no real userspace fault boundary on the
//! host) -- same protocol/LCG and same fault-free-arithmetic-only
//! scope as diff_usercopy.c (see that file's header comment for the
//! three-way exit-path coverage: size==0 trivial, mid-loop
//! goto-done-equivalent early exit, and natural loop-exit fallthrough
//! with the post-loop trim).

const ALIGN_MASK_BITS: u32 = (usize::BITS) as u32; // sizeof(usize) in bits, for clarity below

fn aligned_byte_mask(n: usize) -> usize {
    // little-endian arm, matches lib/usercopy_rs.rs
    (1usize << (8 * n)).wrapping_sub(1)
}

// Mirrors lib/usercopy_rs.rs's check_zeroed_user exactly (same
// alignment/masking arithmetic, same labeled-block early-exit-vs-
// natural-exit shape), minus the unsafe_get_user fault-shim calls
// (plain slice reads instead -- no fault possible on a host Vec, and
// this oracle never lets `size` exceed the backing buffer, see file
// header) and the SAFETY comments (no unsafe user-pointer contract on
// the host side).
fn check_zeroed_user(from: &[u8], off: usize, size: usize) -> i32 {
    let _ = ALIGN_MASK_BITS;
    if size == 0 {
        return 1;
    }

    let align = off % core::mem::size_of::<usize>();
    let start = off - align;
    let size = size + align;

    let read_word = |base: usize| -> usize {
        usize::from_ne_bytes(
            from[base..base + core::mem::size_of::<usize>()]
                .try_into()
                .unwrap(),
        )
    };

    let mut pos = start;
    let mut val = read_word(pos);
    if align != 0 {
        val &= !aligned_byte_mask(align);
    }

    let mut size = size;

    'scan: {
        while size > core::mem::size_of::<usize>() {
            if val != 0 {
                break 'scan;
            }

            pos += core::mem::size_of::<usize>();
            size -= core::mem::size_of::<usize>();

            val = read_word(pos);
        }

        if size < core::mem::size_of::<usize>() {
            val &= aligned_byte_mask(size);
        }
    }

    (val == 0) as i32
}

// Identical LCG to diff_usercopy.c.
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

const BACKING: usize = 128;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(424242);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let off = (rng.next() as usize) % 8;

        let mut backing = vec![0u8; BACKING + 16];

        let sizeof_ul = core::mem::size_of::<usize>();
        let mode = rng.next() % 7;
        let size: usize = match mode {
            0 => 0,
            1 => 1 + (rng.next() as usize) % (sizeof_ul - 1),
            2 => sizeof_ul,
            3 => sizeof_ul + 1 + (rng.next() as usize) % 7,
            4 => sizeof_ul * (2 + (rng.next() as usize) % 4),
            5 => sizeof_ul * (2 + (rng.next() as usize) % 4) + 1 + (rng.next() as usize) % 7,
            _ => 1 + (rng.next() as usize) % (BACKING - 16),
        };

        // already zeroed by vec! above
        let content_mode = rng.next() % 3;
        if content_mode == 1 && size > 0 {
            let pos = (rng.next() as usize) % size;
            backing[off + pos] = 1 + ((rng.next() % 255) as u8);
        } else if content_mode == 2 {
            for k in 0..size {
                backing[off + k] = rng.next() as u8;
            }
        }

        let r = check_zeroed_user(&backing, off, size);

        println!("check_zeroed_user,{},{},{}", off, size, r);
    }
}
