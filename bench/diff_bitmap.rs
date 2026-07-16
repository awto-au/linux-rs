// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, bitmap.
//! Faithful copy of lib/bitmap_rs.rs's algorithms (kernel-crate
//! bindings/export stripped; find_bit/set_bit family reimplemented
//! host-side matching lib/find_bit_rs.rs's own algorithms exactly,
//! since that's what bindings::_find_next_bit et al. resolve to) —
//! same protocol/LCG as diff_bitmap.c.

const BITS_PER_LONG: u32 = u64::BITS;
const NWORDS: usize = 8;
const NBITS_MAX: usize = NWORDS * 64;

fn bits_to_longs(nr: usize) -> usize {
    nr.div_ceil(BITS_PER_LONG as usize)
}
fn bit_word(nr: usize) -> usize {
    nr / BITS_PER_LONG as usize
}
fn bitmap_first_word_mask(start: usize) -> u64 {
    u64::MAX << (start & (BITS_PER_LONG as usize - 1))
}
fn bitmap_last_word_mask(nbits: usize) -> u64 {
    u64::MAX >> (nbits.wrapping_neg() & (BITS_PER_LONG as usize - 1))
}
fn align_mask(x: usize, mask: usize) -> usize {
    x.wrapping_add(mask) & !mask
}
fn hweight_long(w: u64) -> u32 {
    w.count_ones()
}

fn test_bit(nr: usize, addr: &[u64]) -> bool {
    (addr[bit_word(nr)] >> (nr & (BITS_PER_LONG as usize - 1))) & 1 != 0
}
fn set_bit(nr: usize, addr: &mut [u64]) {
    addr[bit_word(nr)] |= 1u64 << (nr & (BITS_PER_LONG as usize - 1));
}
fn bitmap_zero(dst: &mut [u64], nbits: usize) {
    for w in dst.iter_mut().take(bits_to_longs(nbits)) {
        *w = 0;
    }
}

// find_bit_rs.rs's own generic algorithm (host-safe copy).
fn find_next_bit_generic(nbits: u64, start: u64, mut fetch: impl FnMut(usize) -> u64) -> u64 {
    let mut i = start;
    while i < nbits {
        if fetch((i / 64) as usize) & (1u64 << (i % 64)) != 0 {
            return i;
        }
        i += 1;
    }
    nbits
}
fn _find_next_bit(addr: &[u64], size: u64, start: u64) -> u64 {
    find_next_bit_generic(size, start, |idx| addr[idx])
}
fn _find_next_zero_bit(addr: &[u64], size: u64, start: u64) -> u64 {
    let mut i = start;
    while i < size {
        if addr[(i / 64) as usize] & (1u64 << (i % 64)) == 0 {
            return i;
        }
        i += 1;
    }
    size
}
fn _find_first_bit(addr: &[u64], size: u64) -> u64 {
    _find_next_bit(addr, size, 0)
}
fn find_nth_bit_generic(size: u64, num: u64, mut fetch: impl FnMut(usize) -> u64) -> u64 {
    let mut count = 0u64;
    let mut i = 0u64;
    while i < size {
        if fetch((i / 64) as usize) & (1u64 << (i % 64)) != 0 {
            if count == num {
                return i;
            }
            count += 1;
        }
        i += 1;
    }
    size
}
fn __find_nth_bit(addr: &[u64], size: u64, n: u64) -> u64 {
    find_nth_bit_generic(size, n, |idx| addr[idx])
}

fn bitmap_weight_generic(bits: usize, mut fetch: impl FnMut(usize) -> u64) -> u32 {
    let mut w = 0u32;
    for idx in 0..bits / BITS_PER_LONG as usize {
        w += hweight_long(fetch(idx));
    }
    if bits % BITS_PER_LONG as usize != 0 {
        let idx = bits / BITS_PER_LONG as usize;
        w += hweight_long(fetch(idx) & bitmap_last_word_mask(bits));
    }
    w
}

fn bitmap_equal(bitmap1: &[u64], bitmap2: &[u64], bits: usize) -> bool {
    let lim = bits / BITS_PER_LONG as usize;
    for k in 0..lim {
        if bitmap1[k] != bitmap2[k] {
            return false;
        }
    }
    if bits % BITS_PER_LONG as usize != 0
        && (bitmap1[lim] ^ bitmap2[lim]) & bitmap_last_word_mask(bits) != 0
    {
        return false;
    }
    true
}

fn bitmap_or_equal(bitmap1: &[u64], bitmap2: &[u64], bitmap3: &[u64], bits: usize) -> bool {
    let lim = bits / BITS_PER_LONG as usize;
    for k in 0..lim {
        if (bitmap1[k] | bitmap2[k]) != bitmap3[k] {
            return false;
        }
    }
    if bits % BITS_PER_LONG as usize == 0 {
        return true;
    }
    let tmp = (bitmap1[lim] | bitmap2[lim]) ^ bitmap3[lim];
    (tmp & bitmap_last_word_mask(bits)) == 0
}

fn bitmap_complement(dst: &mut [u64], src: &[u64], bits: usize) {
    for k in 0..bits_to_longs(bits) {
        dst[k] = !src[k];
    }
}

fn bitmap_shift_right(dst: &mut [u64], src: &[u64], shift: usize, nbits: usize) {
    let lim = bits_to_longs(nbits);
    let off = shift / BITS_PER_LONG as usize;
    let rem = shift % BITS_PER_LONG as usize;
    let mask = bitmap_last_word_mask(nbits);
    let mut k = 0usize;
    while off + k < lim {
        let upper = if rem == 0 || off + k + 1 >= lim {
            0
        } else {
            let mut u = src[off + k + 1];
            if off + k + 1 == lim - 1 {
                u &= mask;
            }
            u << (BITS_PER_LONG as usize - rem)
        };
        let mut lower = src[off + k];
        if off + k == lim - 1 {
            lower &= mask;
        }
        lower >>= rem;
        dst[k] = lower | upper;
        k += 1;
    }
    if off != 0 {
        for w in dst.iter_mut().take(lim).skip(lim - off) {
            *w = 0;
        }
    }
}

fn bitmap_shift_left(dst: &mut [u64], src: &[u64], shift: usize, nbits: usize) {
    let lim = bits_to_longs(nbits) as isize;
    let off = (shift / BITS_PER_LONG as usize) as isize;
    let rem = shift % BITS_PER_LONG as usize;
    let mut k = lim - off - 1;
    while k >= 0 {
        let lower = if rem != 0 && k > 0 {
            src[(k - 1) as usize] >> (BITS_PER_LONG as usize - rem)
        } else {
            0
        };
        let upper = src[k as usize] << rem;
        dst[(k + off) as usize] = lower | upper;
        k -= 1;
    }
    if off != 0 {
        for w in dst.iter_mut().take(off as usize) {
            *w = 0;
        }
    }
}

fn bitmap_cut(dst: &mut [u64], src: &[u64], first: usize, mut cut: usize, nbits: usize) {
    let len = bits_to_longs(nbits);
    let mut keep: u64 = 0;
    if first % BITS_PER_LONG as usize != 0 {
        keep = src[first / BITS_PER_LONG as usize]
            & (u64::MAX >> (BITS_PER_LONG as usize - first % BITS_PER_LONG as usize));
    }
    dst[..len].copy_from_slice(&src[..len]);
    while cut > 0 {
        cut -= 1;
        let mut i = first / BITS_PER_LONG as usize;
        while i < len {
            let carry = if i < len - 1 { dst[i + 1] & 1 } else { 0 };
            dst[i] = (dst[i] >> 1) | (carry << (BITS_PER_LONG as usize - 1));
            i += 1;
        }
    }
    dst[first / BITS_PER_LONG as usize] &= u64::MAX << (first % BITS_PER_LONG as usize);
    dst[first / BITS_PER_LONG as usize] |= keep;
}

fn bitmap_and(dst: &mut [u64], bitmap1: &[u64], bitmap2: &[u64], bits: usize) -> bool {
    let lim = bits / BITS_PER_LONG as usize;
    let mut result: u64 = 0;
    for k in 0..lim {
        let v = bitmap1[k] & bitmap2[k];
        dst[k] = v;
        result |= v;
    }
    if bits % BITS_PER_LONG as usize != 0 {
        let v = bitmap1[lim] & bitmap2[lim] & bitmap_last_word_mask(bits);
        dst[lim] = v;
        result |= v;
    }
    result != 0
}
fn bitmap_or(dst: &mut [u64], bitmap1: &[u64], bitmap2: &[u64], bits: usize) {
    for k in 0..bits_to_longs(bits) {
        dst[k] = bitmap1[k] | bitmap2[k];
    }
}
fn bitmap_xor(dst: &mut [u64], bitmap1: &[u64], bitmap2: &[u64], bits: usize) {
    for k in 0..bits_to_longs(bits) {
        dst[k] = bitmap1[k] ^ bitmap2[k];
    }
}
fn bitmap_andnot(dst: &mut [u64], bitmap1: &[u64], bitmap2: &[u64], bits: usize) -> bool {
    let lim = bits / BITS_PER_LONG as usize;
    let mut result: u64 = 0;
    for k in 0..lim {
        let v = bitmap1[k] & !bitmap2[k];
        dst[k] = v;
        result |= v;
    }
    if bits % BITS_PER_LONG as usize != 0 {
        let v = bitmap1[lim] & !bitmap2[lim] & bitmap_last_word_mask(bits);
        dst[lim] = v;
        result |= v;
    }
    result != 0
}
fn bitmap_replace(dst: &mut [u64], old: &[u64], new: &[u64], mask: &[u64], nbits: usize) {
    for k in 0..bits_to_longs(nbits) {
        dst[k] = (old[k] & !mask[k]) | (new[k] & mask[k]);
    }
}
fn bitmap_intersects(bitmap1: &[u64], bitmap2: &[u64], bits: usize) -> bool {
    let lim = bits / BITS_PER_LONG as usize;
    for k in 0..lim {
        if bitmap1[k] & bitmap2[k] != 0 {
            return true;
        }
    }
    bits % BITS_PER_LONG as usize != 0 && (bitmap1[lim] & bitmap2[lim]) & bitmap_last_word_mask(bits) != 0
}
fn bitmap_subset(bitmap1: &[u64], bitmap2: &[u64], bits: usize) -> bool {
    let lim = bits / BITS_PER_LONG as usize;
    for k in 0..lim {
        if bitmap1[k] & !bitmap2[k] != 0 {
            return false;
        }
    }
    !(bits % BITS_PER_LONG as usize != 0 && (bitmap1[lim] & !bitmap2[lim]) & bitmap_last_word_mask(bits) != 0)
}

fn bitmap_weight(bitmap: &[u64], bits: usize) -> u32 {
    bitmap_weight_generic(bits, |idx| bitmap[idx])
}
fn bitmap_weight_and(bitmap1: &[u64], bitmap2: &[u64], bits: usize) -> u32 {
    bitmap_weight_generic(bits, |idx| bitmap1[idx] & bitmap2[idx])
}
fn bitmap_weight_andnot(bitmap1: &[u64], bitmap2: &[u64], bits: usize) -> u32 {
    bitmap_weight_generic(bits, |idx| bitmap1[idx] & !bitmap2[idx])
}
fn bitmap_weighted_or(dst: &mut [u64], bitmap1: &[u64], bitmap2: &[u64], bits: usize) -> u32 {
    let mut w = 0u32;
    for idx in 0..bits / BITS_PER_LONG as usize {
        let v = bitmap1[idx] | bitmap2[idx];
        dst[idx] = v;
        w += hweight_long(v);
    }
    if bits % BITS_PER_LONG as usize != 0 {
        let idx = bits / BITS_PER_LONG as usize;
        let v = bitmap1[idx] | bitmap2[idx];
        dst[idx] = v;
        w += hweight_long(v & bitmap_last_word_mask(bits));
    }
    w
}
fn bitmap_weighted_xor(dst: &mut [u64], bitmap1: &[u64], bitmap2: &[u64], bits: usize) -> u32 {
    let mut w = 0u32;
    for idx in 0..bits / BITS_PER_LONG as usize {
        let v = bitmap1[idx] ^ bitmap2[idx];
        dst[idx] = v;
        w += hweight_long(v);
    }
    if bits % BITS_PER_LONG as usize != 0 {
        let idx = bits / BITS_PER_LONG as usize;
        let v = bitmap1[idx] ^ bitmap2[idx];
        dst[idx] = v;
        w += hweight_long(v & bitmap_last_word_mask(bits));
    }
    w
}

fn bitmap_set(map: &mut [u64], start: usize, len: i32) {
    let size = start as isize + len as isize;
    let mut bits_to_set = BITS_PER_LONG as isize - (start % BITS_PER_LONG as usize) as isize;
    let mut mask_to_set = bitmap_first_word_mask(start);
    let mut len = len as isize;
    let mut p = bit_word(start);
    while len - bits_to_set >= 0 {
        map[p] |= mask_to_set;
        len -= bits_to_set;
        bits_to_set = BITS_PER_LONG as isize;
        mask_to_set = u64::MAX;
        p += 1;
    }
    if len != 0 {
        mask_to_set &= bitmap_last_word_mask(size as usize);
        map[p] |= mask_to_set;
    }
}
fn bitmap_clear(map: &mut [u64], start: usize, len: i32) {
    let size = start as isize + len as isize;
    let mut bits_to_clear = BITS_PER_LONG as isize - (start % BITS_PER_LONG as usize) as isize;
    let mut mask_to_clear = bitmap_first_word_mask(start);
    let mut len = len as isize;
    let mut p = bit_word(start);
    while len - bits_to_clear >= 0 {
        map[p] &= !mask_to_clear;
        len -= bits_to_clear;
        bits_to_clear = BITS_PER_LONG as isize;
        mask_to_clear = u64::MAX;
        p += 1;
    }
    if len != 0 {
        mask_to_clear &= bitmap_last_word_mask(size as usize);
        map[p] &= !mask_to_clear;
    }
}

fn bitmap_find_next_zero_area_off(
    map: &[u64],
    size: u64,
    mut start: u64,
    nr: u32,
    align_mask_: u64,
    align_offset: u64,
) -> u64 {
    loop {
        let mut index = _find_next_zero_bit(map, size, start);
        index = align_mask((index + align_offset) as usize, align_mask_ as usize) as u64 - align_offset;
        let end = index + nr as u64;
        if end > size {
            return end;
        }
        let i = _find_next_bit(map, end, index);
        if i < end {
            start = i + 1;
            continue;
        }
        return index;
    }
}

fn bitmap_pos_to_ord(buf: &[u64], pos: u32, nbits: u32) -> i32 {
    if pos >= nbits || !test_bit(pos as usize, buf) {
        return -1;
    }
    bitmap_weight_generic(pos as usize, |idx| buf[idx]) as i32
}

fn bitmap_remap(dst: &mut [u64], src: &[u64], old: &[u64], new: &[u64], nbits: u32) {
    bitmap_zero(dst, nbits as usize);
    let w = bitmap_weight_generic(nbits as usize, |idx| new[idx]);
    let mut oldbit = _find_first_bit(src, nbits as u64);
    while oldbit < nbits as u64 {
        let n = bitmap_pos_to_ord(old, oldbit as u32, nbits);
        if n < 0 || w == 0 {
            set_bit(oldbit as usize, dst);
        } else {
            let target = __find_nth_bit(new, nbits as u64, (n as u32 % w) as u64);
            set_bit(target as usize, dst);
        }
        oldbit = _find_next_bit(src, nbits as u64, oldbit + 1);
    }
}

fn bitmap_bitremap(oldbit: i32, old: &[u64], new: &[u64], bits: i32) -> i32 {
    let w = bitmap_weight_generic(bits as usize, |idx| new[idx]) as i32;
    let n = bitmap_pos_to_ord(old, oldbit as u32, bits as u32);
    if n < 0 || w == 0 {
        oldbit
    } else {
        __find_nth_bit(new, bits as u64, (n % w) as u64) as i32
    }
}

fn bitmap_from_arr32(bitmap: &mut [u64], buf: &[u32], nbits: u32) {
    let halfwords = (nbits as usize).div_ceil(32);
    let mut i = 0usize;
    while i < halfwords {
        bitmap[i / 2] = buf[i] as u64;
        i += 1;
        if i < halfwords {
            bitmap[i / 2] |= (buf[i] as u64) << 32;
        }
        i += 1;
    }
    if (nbits as usize) % BITS_PER_LONG as usize != 0 {
        bitmap[(halfwords - 1) / 2] &= bitmap_last_word_mask(nbits as usize);
    }
}
fn bitmap_to_arr32(buf: &mut [u32], bitmap: &[u64], nbits: u32) {
    let halfwords = (nbits as usize).div_ceil(32);
    let mut i = 0usize;
    while i < halfwords {
        buf[i] = (bitmap[i / 2] & u32::MAX as u64) as u32;
        i += 1;
        if i < halfwords {
            buf[i] = (bitmap[i / 2] >> 32) as u32;
        }
        i += 1;
    }
    if (nbits as usize) % BITS_PER_LONG as usize != 0 {
        buf[halfwords - 1] &= (u32::MAX as u64 >> (nbits.wrapping_neg() & 31)) as u32;
    }
}

struct Lcg(u64);
impl Lcg {
    fn next(&mut self) -> u32 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (self.0 >> 32) as u32
    }
}

fn gen_bitmap(rng: &mut Lcg, nbits: usize) -> [u64; NWORDS] {
    let mut b = [0u64; NWORDS];
    for w in b.iter_mut() {
        *w = ((rng.next() as u64) << 32) | rng.next() as u64;
    }
    if nbits % 64 != 0 {
        b[nbits / 64] &= bitmap_last_word_mask(nbits);
    }
    for w in b.iter_mut().skip(nbits / 64 + if nbits % 64 != 0 { 1 } else { 0 }) {
        *w = 0;
    }
    b
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let nbits = 1 + (rng.next() as usize) % (NBITS_MAX - 1);
        let a = gen_bitmap(&mut rng, nbits);
        let b = gen_bitmap(&mut rng, nbits);
        let c = gen_bitmap(&mut rng, nbits);
        let mut dst = [0u64; NWORDS];

        println!("equal,{},{}", nbits, bitmap_equal(&a, &b, nbits) as i32);
        println!("or_equal,{},{}", nbits, bitmap_or_equal(&a, &b, &c, nbits) as i32);

        dst = a;
        bitmap_complement(&mut dst, &a, nbits);
        println!("complement,{},{:016x},{:016x}", nbits, dst[0], dst[NWORDS - 1]);

        let shift = (rng.next() as usize) % nbits;
        dst = [0; NWORDS];
        bitmap_shift_right(&mut dst, &a, shift, nbits);
        println!("shr,{},{},{:016x},{:016x}", nbits, shift, dst[0], dst[NWORDS - 1]);

        dst = [0; NWORDS];
        bitmap_shift_left(&mut dst, &a, shift, nbits);
        println!("shl,{},{},{:016x},{:016x}", nbits, shift, dst[0], dst[NWORDS - 1]);

        let first = (rng.next() as usize) % nbits;
        let cut = (rng.next() as usize) % (nbits - first + 1);
        dst = a;
        bitmap_cut(&mut dst, &a, first, cut, nbits);
        println!("cut,{},{},{},{:016x},{:016x}", nbits, first, cut, dst[0], dst[NWORDS - 1]);

        dst = [0; NWORDS];
        let r = bitmap_and(&mut dst, &a, &b, nbits);
        println!("and,{},{},{:016x}", nbits, r as i32, dst[0]);
        bitmap_or(&mut dst, &a, &b, nbits);
        println!("or,{},{:016x}", nbits, dst[0]);
        bitmap_xor(&mut dst, &a, &b, nbits);
        println!("xor,{},{:016x}", nbits, dst[0]);
        let r = bitmap_andnot(&mut dst, &a, &b, nbits);
        println!("andnot,{},{},{:016x}", nbits, r as i32, dst[0]);
        bitmap_replace(&mut dst, &a, &b, &c, nbits);
        println!("replace,{},{:016x}", nbits, dst[0]);
        println!("intersects,{},{}", nbits, bitmap_intersects(&a, &b, nbits) as i32);
        println!("subset,{},{}", nbits, bitmap_subset(&a, &b, nbits) as i32);

        println!("weight,{},{}", nbits, bitmap_weight(&a, nbits));
        println!("weight_and,{},{}", nbits, bitmap_weight_and(&a, &b, nbits));
        println!("weight_andnot,{},{}", nbits, bitmap_weight_andnot(&a, &b, nbits));
        dst = [0; NWORDS];
        let w = bitmap_weighted_or(&mut dst, &a, &b, nbits);
        println!("weighted_or,{},{},{:016x}", nbits, w, dst[0]);
        dst = [0; NWORDS];
        let w = bitmap_weighted_xor(&mut dst, &a, &b, nbits);
        println!("weighted_xor,{},{},{:016x}", nbits, w, dst[0]);

        let sstart = (rng.next() as usize) % nbits;
        let slen = (rng.next() as usize % (nbits - sstart + 1)) as i32;
        dst = a;
        bitmap_set(&mut dst, sstart, slen);
        println!("set,{},{},{},{:016x},{:016x}", nbits, sstart, slen, dst[0], dst[NWORDS - 1]);
        dst = a;
        bitmap_clear(&mut dst, sstart, slen);
        println!("clear,{},{},{},{:016x},{:016x}", nbits, sstart, slen, dst[0], dst[NWORDS - 1]);

        let zstart = (rng.next() as u64) % nbits as u64;
        let znr = 1 + rng.next() % 8;
        let zam = (1u64 << (rng.next() % 4)) - 1;
        let zao = (rng.next() % 4) as u64;
        let zres = bitmap_find_next_zero_area_off(&a, nbits as u64, zstart, znr, zam, zao);
        println!("findzeroarea,{},{},{},{},{},{}", nbits, zstart, znr, zam, zao, zres);

        dst = [0; NWORDS];
        bitmap_remap(&mut dst, &a, &b, &c, nbits as u32);
        println!("remap,{},{:016x},{:016x}", nbits, dst[0], dst[NWORDS - 1]);

        let oldbit = (rng.next() as usize % nbits) as i32;
        let rres = bitmap_bitremap(oldbit, &b, &c, nbits as i32);
        println!("bitremap,{},{},{}", nbits, oldbit, rres);

        let mut arr32 = [0u32; NWORDS * 2];
        bitmap_to_arr32(&mut arr32, &a, nbits as u32);
        let mut sum32 = 0u32;
        for v in arr32.iter().take(nbits.div_ceil(32)) {
            sum32 ^= v;
        }
        println!("to_arr32,{},{:08x}", nbits, sum32);

        dst = [0; NWORDS];
        bitmap_from_arr32(&mut dst, &arr32, nbits as u32);
        println!("from_arr32,{},{:016x},{:016x}", nbits, dst[0], dst[NWORDS - 1]);
    }
}
