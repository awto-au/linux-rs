// SPDX-License-Identifier: GPL-2.0-or-later
//! Tier-2.5 differential oracle: Rust translation side, find_bit.
//! Faithful copy of lib/find_bit_rs.rs's algorithm (kernel-crate
//! bindings/export stripped, raw pointers replaced by slice indexing —
//! same simplification `diff_ucs2_string.rs` uses) — same protocol/LCG
//! as diff_find_bit.c.

type Ul = u64;
const BITS_PER_LONG: usize = Ul::BITS as usize;

fn first_word_mask(start: usize) -> Ul {
    Ul::MAX << (start & (BITS_PER_LONG - 1))
}

fn last_word_mask(nbits: usize) -> Ul {
    Ul::MAX >> (nbits.wrapping_neg() & (BITS_PER_LONG - 1))
}

fn fns(mut word: Ul, mut n: Ul) -> Ul {
    while word != 0 && n != 0 {
        word &= word - 1;
        n -= 1;
    }
    if word != 0 {
        word.trailing_zeros() as Ul
    } else {
        BITS_PER_LONG as Ul
    }
}

fn find_first_bit_generic(size: Ul, mut fetch: impl FnMut(usize) -> Ul) -> Ul {
    let mut idx = 0usize;
    while (idx as Ul) * (BITS_PER_LONG as Ul) < size {
        let val = fetch(idx);
        if val != 0 {
            return core::cmp::min(
                idx as Ul * BITS_PER_LONG as Ul + val.trailing_zeros() as Ul,
                size,
            );
        }
        idx += 1;
    }
    size
}

fn find_next_bit_generic(size: Ul, start: Ul, mut fetch: impl FnMut(usize) -> Ul) -> Ul {
    if start >= size {
        return size;
    }
    let mask = first_word_mask(start as usize);
    let mut idx = start as usize / BITS_PER_LONG;

    let mut tmp = fetch(idx) & mask;
    while tmp == 0 {
        if (idx + 1) as Ul * BITS_PER_LONG as Ul >= size {
            return size;
        }
        idx += 1;
        tmp = fetch(idx);
    }
    core::cmp::min(
        idx as Ul * BITS_PER_LONG as Ul + tmp.trailing_zeros() as Ul,
        size,
    )
}

fn find_nth_bit_generic(size: Ul, num: Ul, mut fetch: impl FnMut(usize) -> Ul) -> Ul {
    let mut nr = num;
    let mut idx = 0usize;
    let mut tmp: Ul = 0;

    loop {
        if (idx + 1) as Ul * BITS_PER_LONG as Ul > size {
            break;
        }
        if idx as Ul * BITS_PER_LONG as Ul + nr >= size {
            return size;
        }
        tmp = fetch(idx);
        let w = tmp.count_ones() as Ul;
        if w > nr {
            return idx as Ul * BITS_PER_LONG as Ul + fns(tmp, nr);
        }
        nr -= w;
        idx += 1;
    }
    if size % BITS_PER_LONG as Ul != 0 {
        tmp = fetch(idx) & last_word_mask(size as usize);
    }
    idx as Ul * BITS_PER_LONG as Ul + fns(tmp, nr)
}

fn find_last_bit(addr: &[Ul], size: Ul) -> Ul {
    if size == 0 {
        return size;
    }
    let mut val = last_word_mask(size as usize);
    let mut idx = (size as usize - 1) / BITS_PER_LONG;
    loop {
        val &= addr[idx];
        if val != 0 {
            return idx as Ul * BITS_PER_LONG as Ul
                + (BITS_PER_LONG as u32 - 1 - val.leading_zeros()) as Ul;
        }
        val = Ul::MAX;
        if idx == 0 {
            break;
        }
        idx -= 1;
    }
    size
}

fn bitmap_read8(map: &[Ul], start: usize) -> Ul {
    let index = start / BITS_PER_LONG;
    let offset = start % BITS_PER_LONG;
    let space = BITS_PER_LONG - offset;
    if space >= 8 {
        return (map[index] >> offset) & last_word_mask(8);
    }
    let value_low = map[index] & first_word_mask(start);
    let value_high = map[index + 1] & last_word_mask(start + 8);
    (value_low >> offset) | (value_high << space)
}

fn find_next_clump8(clump: &mut Ul, addr: &[Ul], size: Ul, offset: Ul) -> Ul {
    let offset = find_next_bit_generic(size, offset, |idx| addr[idx]);
    if offset == size {
        return size;
    }
    let offset = (offset as usize & !7) as Ul;
    *clump = bitmap_read8(addr, offset as usize);
    offset
}

// Identical LCG to diff_find_bit.c.
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

const NWORDS: usize = 6;
const NBITS: usize = NWORDS * BITS_PER_LONG;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(424242);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let mut a = [0 as Ul; NWORDS];
        let mut b = [0 as Ul; NWORDS];
        let mut c = [0 as Ul; NWORDS];
        for k in 0..NWORDS {
            let r = rng.next();
            a[k] = if r % 4 == 0 {
                0
            } else {
                (r as Ul) << 32 | rng.next() as Ul
            };
            b[k] = if rng.next() % 4 == 0 {
                0
            } else {
                (rng.next() as Ul) << 32 | rng.next() as Ul
            };
            c[k] = if rng.next() % 4 == 0 {
                0
            } else {
                (rng.next() as Ul) << 32 | rng.next() as Ul
            };
        }
        let size = 1 + (rng.next() as Ul % (NBITS as Ul - 1));
        let start = rng.next() as Ul % (size + 2);
        let n_bit = rng.next() as Ul % (size + 2);

        println!("first,{}", find_first_bit_generic(size, |idx| a[idx]));
        println!(
            "first_and,{}",
            find_first_bit_generic(size, |idx| a[idx] & b[idx])
        );
        println!(
            "first_andnot,{}",
            find_first_bit_generic(size, |idx| a[idx] & !b[idx])
        );
        println!(
            "first_and_and,{}",
            find_first_bit_generic(size, |idx| a[idx] & b[idx] & c[idx])
        );
        println!("first_zero,{}", find_first_bit_generic(size, |idx| !a[idx]));
        println!("next,{}", find_next_bit_generic(size, start, |idx| a[idx]));
        println!(
            "next_and,{}",
            find_next_bit_generic(size, start, |idx| a[idx] & b[idx])
        );
        println!(
            "next_andnot,{}",
            find_next_bit_generic(size, start, |idx| a[idx] & !b[idx])
        );
        println!(
            "next_or,{}",
            find_next_bit_generic(size, start, |idx| a[idx] | b[idx])
        );
        println!(
            "next_zero,{}",
            find_next_bit_generic(size, start, |idx| !a[idx])
        );
        println!("nth,{}", find_nth_bit_generic(size, n_bit, |idx| a[idx]));
        println!(
            "nth_and,{}",
            find_nth_bit_generic(size, n_bit, |idx| a[idx] & b[idx])
        );
        println!(
            "nth_and_andnot,{}",
            find_nth_bit_generic(size, n_bit, |idx| a[idx] & b[idx] & !c[idx])
        );
        println!("last,{}", find_last_bit(&a, size));

        let mut clump: Ul = 0xdeadbeef;
        let clump_off = find_next_clump8(&mut clump, &a, size, start);
        println!(
            "clump8,{},{}",
            clump_off,
            if clump_off == size { 0 } else { clump }
        );
    }
}
