// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, bitmap-str.
//! Faithful copy of lib/bitmap-str_rs.rs's algorithms (kernel-crate
//! bindings/export stripped, cross-TU calls to kstrtox/bitmap/find_bit/
//! string/hexdump inlined as local fns) — same protocol/LCG as
//! diff_bitmap-str.c. Uses real raw pointers (not offsets) so the
//! ERR_PTR/NULL sentinel encoding matches the actual translation
//! exactly — a prior offset-based version spuriously flagged
//! legitimate "one before buffer start" positions as ERR_PTRs.

const BITS_PER_LONG: u32 = usize::BITS;
const MAX_ERRNO: isize = 4095;
const EINVAL: i32 = 22;
const ERANGE: i32 = 34;
const EOVERFLOW: i32 = 75;
const KSTRTOX_OVERFLOW: u32 = 1u32 << 31;
const NBITS: usize = 256;
const NWORDS: usize = NBITS / 64;

unsafe fn err_ptr(error: isize) -> *const u8 {
    error as *const u8
}
unsafe fn ptr_err(ptr: *const u8) -> isize {
    ptr as isize
}
unsafe fn is_err(ptr: *const u8) -> bool {
    (ptr as usize) >= (-MAX_ERRNO) as usize
}

// _parse_integer (lib/kstrtox_rs.rs), base fixed to 10 here (only
// caller in this file is bitmap_getnum).
unsafe fn parse_integer(mut s: *const u8) -> (usize, u64) {
    let mut res: u64 = 0;
    let mut rv: usize = 0;
    loop {
        let c = unsafe { *s };
        if !c.is_ascii_digit() {
            break;
        }
        let val = (c - b'0') as u64;
        if res & (!0u64 << 60) != 0 && res > (u64::MAX - val) / 10 {
            rv |= KSTRTOX_OVERFLOW as usize;
        }
        res = res.wrapping_mul(10).wrapping_add(val);
        rv += 1;
        s = unsafe { s.add(1) };
    }
    (rv, res)
}

fn hex_to_bin(ch: u8) -> i32 {
    let cu = ch & 0xdf;
    -1 + ((((ch.wrapping_sub(b'0').wrapping_add(1)) as u32)
        & (((ch.wrapping_sub(b'9').wrapping_sub(1)) as i8 as i32
            & (b'0' as i32 - 1 - ch as i32)) as u32
            >> 8)) as i32)
        + ((((cu.wrapping_sub(b'A').wrapping_add(11)) as u32)
            & (((cu.wrapping_sub(b'F').wrapping_sub(1)) as i8 as i32
                & (b'A' as i32 - 1 - cu as i32)) as u32
                >> 8)) as i32)
}

fn bitmap_zero(dst: &mut [u64]) {
    for w in dst.iter_mut() {
        *w = 0;
    }
}

fn bitmap_set(map: &mut [u64], start: u32, len: i32) {
    let mut p = (start / BITS_PER_LONG) as usize;
    let size = start.wrapping_add(len as u32);
    let mut bits_to_set = BITS_PER_LONG - (start % BITS_PER_LONG);
    let mut mask_to_set: u64 = !0u64 << (start & (BITS_PER_LONG - 1));
    let mut len = len;
    while len - bits_to_set as i32 >= 0 {
        map[p] |= mask_to_set;
        len -= bits_to_set as i32;
        bits_to_set = BITS_PER_LONG;
        mask_to_set = !0u64;
        p += 1;
    }
    if len != 0 {
        mask_to_set &= !0u64 >> ((0u32.wrapping_sub(size)) & (BITS_PER_LONG - 1));
        map[p] |= mask_to_set;
    }
}

fn bitmap_clear(map: &mut [u64], start: u32, len: i32) {
    let mut p = (start / BITS_PER_LONG) as usize;
    let size = start.wrapping_add(len as u32);
    let mut bits_to_clear = BITS_PER_LONG - (start % BITS_PER_LONG);
    let mut mask_to_clear: u64 = !0u64 << (start & (BITS_PER_LONG - 1));
    let mut len = len;
    while len - bits_to_clear as i32 >= 0 {
        map[p] &= !mask_to_clear;
        len -= bits_to_clear as i32;
        bits_to_clear = BITS_PER_LONG;
        mask_to_clear = !0u64;
        p += 1;
    }
    if len != 0 {
        mask_to_clear &= !0u64 >> ((0u32.wrapping_sub(size)) & (BITS_PER_LONG - 1));
        map[p] &= !mask_to_clear;
    }
}

fn find_next_bit(addr: &[u64], size: u32, offset: u32) -> u32 {
    let mut i = offset;
    while i < size {
        if addr[(i / BITS_PER_LONG) as usize] & (1u64 << (i % BITS_PER_LONG)) != 0 {
            return i;
        }
        i += 1;
    }
    size
}

fn end_of_str(c: u8) -> bool {
    c == 0 || c == b'\n'
}
fn end_of_region_(c: u8) -> bool {
    c.is_ascii_whitespace() || c == b','
}
fn end_of_region(c: u8) -> bool {
    end_of_region_(c) || end_of_str(c)
}

unsafe fn bitmap_getnum(str_: *const u8, num: *mut u32, lastbit: u32) -> *const u8 {
    unsafe {
        if *str_ == b'N' {
            *num = lastbit;
            return str_.add(1);
        }
        let (len, n) = parse_integer(str_);
        if len == 0 {
            return err_ptr(-(EINVAL as isize));
        }
        if (len as u32) & KSTRTOX_OVERFLOW != 0 || n != n as u32 as u64 {
            return err_ptr(-(EOVERFLOW as isize));
        }
        *num = n as u32;
        str_.add(len)
    }
}

unsafe fn bitmap_find_region(str_: *const u8) -> *const u8 {
    let mut str_ = str_;
    unsafe {
        while end_of_region_(*str_) {
            str_ = str_.add(1);
        }
        if end_of_str(*str_) {
            core::ptr::null()
        } else {
            str_
        }
    }
}

unsafe fn bitmap_find_region_reverse(start: *const u8, end: *const u8) -> *const u8 {
    let mut end = end;
    unsafe {
        while start <= end && end_of_region_(*end) {
            end = end.sub(1);
        }
        end
    }
}

struct Region {
    start: u32,
    off: u32,
    group_len: u32,
    end: u32,
    nbits: u32,
}

unsafe fn strncasecmp_all(s: *const u8) -> bool {
    unsafe {
        (*s | 0x20) == b'a'
            && (*s.add(1) | 0x20) == b'l'
            && (*s.add(2) | 0x20) == b'l'
    }
}

unsafe fn bitmap_parse_region(str_: *const u8, r: *mut Region) -> *const u8 {
    unsafe {
        let lastbit = (*r).nbits - 1;
        let mut str_ = str_;

        if strncasecmp_all(str_) {
            (*r).start = 0;
            (*r).end = lastbit;
            str_ = str_.add(3);
        } else {
            str_ = bitmap_getnum(str_, &mut (*r).start, lastbit);
            if is_err(str_) {
                return str_;
            }

            if end_of_region(*str_) {
                (*r).end = (*r).start;
                (*r).off = (*r).end + 1;
                (*r).group_len = (*r).end + 1;
                return if end_of_str(*str_) { core::ptr::null() } else { str_ };
            }

            if *str_ != b'-' {
                return err_ptr(-(EINVAL as isize));
            }
            str_ = bitmap_getnum(str_.add(1), &mut (*r).end, lastbit);
            if is_err(str_) {
                return str_;
            }
        }

        // check_pattern:
        if end_of_region(*str_) {
            (*r).off = (*r).end + 1;
            (*r).group_len = (*r).end + 1;
            return if end_of_str(*str_) { core::ptr::null() } else { str_ };
        }
        if *str_ != b':' {
            return err_ptr(-(EINVAL as isize));
        }
        str_ = bitmap_getnum(str_.add(1), &mut (*r).off, lastbit);
        if is_err(str_) {
            return str_;
        }
        if *str_ != b'/' {
            return err_ptr(-(EINVAL as isize));
        }
        bitmap_getnum(str_.add(1), &mut (*r).group_len, lastbit)
    }
}

fn bitmap_set_region(r: &Region, bitmap: &mut [u64]) {
    let mut start = r.start;
    while start <= r.end {
        let len = core::cmp::min(r.end - start + 1, r.off);
        bitmap_set(bitmap, start, len as i32);
        start += r.group_len;
    }
}

fn bitmap_check_region(r: &Region) -> i32 {
    if r.start > r.end || r.group_len == 0 || r.off > r.group_len {
        return -EINVAL;
    }
    if r.end >= r.nbits {
        return -ERANGE;
    }
    0
}

unsafe fn bitmap_parselist(buf: *const u8, maskp: &mut [u64], nmaskbits: i32) -> i32 {
    let mut r = Region { start: 0, off: 0, group_len: 0, end: 0, nbits: nmaskbits as u32 };
    bitmap_zero(maskp);

    unsafe {
        let mut buf = buf;
        loop {
            if buf.is_null() {
                return 0;
            }
            buf = bitmap_find_region(buf);
            if buf.is_null() {
                return 0;
            }

            buf = bitmap_parse_region(buf, &mut r);
            if is_err(buf) {
                return ptr_err(buf) as i32;
            }

            let ret = bitmap_check_region(&r);
            if ret != 0 {
                return ret;
            }

            bitmap_set_region(&r, maskp);
        }
    }
}

fn bits_to_u32(nr: u32) -> u32 {
    nr.div_ceil(32)
}

unsafe fn bitmap_get_x32_reverse(start: *const u8, end: *const u8, num: *mut u32) -> *const u8 {
    let mut ret: u32 = 0;
    let mut end = end;
    unsafe {
        let mut i = 0;
        while i < 32 {
            let c = hex_to_bin(*end);
            end = end.sub(1);
            if c < 0 {
                return err_ptr(-(EINVAL as isize));
            }
            ret |= (c as u32) << i;

            if start > end || end_of_region_(*end) {
                *num = ret;
                return end;
            }
            i += 4;
        }
        if hex_to_bin(*end) >= 0 {
            return err_ptr(-(EOVERFLOW as isize));
        }
        *num = ret;
        end
    }
}

unsafe fn bitmap_parse(start: *const u8, buflen: u32, maskp: &mut [u64], nmaskbits: i32) -> i32 {
    unsafe {
        // strnchrnul(start, buflen, '\n') - 1
        let mut p = start;
        let mut n = 0u32;
        while n < buflen && *p != 0 && *p != b'\n' {
            p = p.add(1);
            n += 1;
        }
        let mut end = p.sub(1);

        let mut chunks = bits_to_u32(nmaskbits as u32) as i32;
        let bitmap32: &mut [u32] = bytemuck_u64_to_u32_mut(maskp);
        let mut chunk: isize = 0;

        loop {
            end = bitmap_find_region_reverse(start, end);
            if start > end {
                break;
            }

            if chunks == 0 {
                return -EOVERFLOW;
            }
            chunks -= 1;

            let r = bitmap_get_x32_reverse(start, end, &mut bitmap32[chunk as usize]);
            if is_err(r) {
                return ptr_err(r) as i32;
            }
            end = r;
            chunk += 1;
        }

        let unset_bit = (bits_to_u32(nmaskbits as u32) as i32 - chunks) * 32;
        if unset_bit < nmaskbits {
            bitmap_clear(maskp, unset_bit as u32, nmaskbits - unset_bit);
            return 0;
        }
        if find_next_bit(maskp, nmaskbits as u32, unset_bit as u32) != unset_bit as u32 {
            return -EOVERFLOW;
        }
        0
    }
}

fn bytemuck_u64_to_u32_mut(s: &mut [u64]) -> &mut [u32] {
    // SAFETY: u64 slice is always validly reinterpretable as twice as
    // many u32s on this (little-endian) host, matching the C side's
    // `(uint32_t *)maskp` cast under LE.
    unsafe { core::slice::from_raw_parts_mut(s.as_mut_ptr().cast::<u32>(), s.len() * 2) }
}

struct Lcg(u64);
impl Lcg {
    fn next(&mut self) -> u32 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (self.0 >> 32) as u32
    }
}

fn gen_listbuf(rng: &mut Lcg, maxlen: usize) -> Vec<u8> {
    let mut buf = Vec::with_capacity(maxlen + 1);
    let nterms = 1 + rng.next() % 5;
    for t in 0..nterms {
        if buf.len() >= maxlen.saturating_sub(20) {
            break;
        }
        if t > 0 {
            buf.push(b',');
        }
        let kind = rng.next() % 6;
        if kind == 0 {
            buf.extend_from_slice(b"all");
        } else {
            let a = rng.next() % NBITS as u32;
            if rng.next() % 3 == 0 && kind != 5 {
                buf.push(b'N');
            } else {
                buf.extend_from_slice(a.to_string().as_bytes());
            }
            if kind >= 2 {
                let b = rng.next() % NBITS as u32;
                buf.push(b'-');
                buf.extend_from_slice(b.to_string().as_bytes());
                if kind >= 4 {
                    let off = 1 + rng.next() % 4;
                    let grp = off + rng.next() % 4;
                    buf.push(b':');
                    buf.extend_from_slice(off.to_string().as_bytes());
                    buf.push(b'/');
                    buf.extend_from_slice(grp.to_string().as_bytes());
                }
            }
        }
    }
    buf.push(0);
    buf
}

fn gen_hexbuf(rng: &mut Lcg, maxlen: usize) -> Vec<u8> {
    const HEXDIGITS: &[u8] = b"0123456789abcdefABCDEF";
    let len = 1 + (rng.next() as usize) % (maxlen - 1);
    let mut buf = Vec::with_capacity(maxlen + 1);
    for i in 0..len {
        if buf.len() >= maxlen - 1 {
            break;
        }
        if i > 0 && rng.next() % 8 == 0 {
            buf.push(b',');
        } else {
            buf.push(HEXDIGITS[(rng.next() as usize) % HEXDIGITS.len()]);
        }
    }
    buf.push(0);
    buf
}

fn cstr(v: &[u8]) -> &str {
    let end = v.iter().position(|&b| b == 0).unwrap_or(v.len());
    std::str::from_utf8(&v[..end]).unwrap()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let listbuf = gen_listbuf(&mut rng, 127);
        let mut mask = [0u64; NWORDS];
        let r1 = unsafe { bitmap_parselist(listbuf.as_ptr(), &mut mask, NBITS as i32) };
        println!("parselist,{},{},{:016x},{:016x}", cstr(&listbuf), r1, mask[0], mask[NWORDS - 1]);

        let hexbuf = gen_hexbuf(&mut rng, 79);
        let mut mask2 = [0u64; NWORDS];
        let hexlen = hexbuf.iter().position(|&b| b == 0).unwrap_or(hexbuf.len());
        let r2 = unsafe { bitmap_parse(hexbuf.as_ptr(), hexlen as u32, &mut mask2, NBITS as i32) };
        println!("parse,{},{},{:016x},{:016x}", cstr(&hexbuf), r2, mask2[0], mask2[NWORDS - 1]);
    }
}
