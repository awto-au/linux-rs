// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, string.
//! Faithful copy of lib/string_rs.rs's algorithms (kernel-crate
//! bindings/export stripped, libc calls replaced with equivalent Rust)
//! — same protocol/LCG as diff_string.c.

use std::ffi::CStr;
use std::os::raw::{c_char, c_int, c_void};

fn tolower(c: u8) -> u8 {
    if c.is_ascii_uppercase() {
        c - b'A' + b'a'
    } else {
        c
    }
}

fn strncasecmp(s1: &[u8], s2: &[u8], len: usize) -> i32 {
    if len == 0 {
        return 0;
    }
    let mut i1 = 0usize;
    let mut i2 = 0usize;
    let mut remaining = len;
    let mut c1: u8;
    let mut c2: u8;
    loop {
        c1 = s1[i1];
        i1 += 1;
        c2 = s2[i2];
        i2 += 1;
        if c1 == 0 || c2 == 0 {
            break;
        }
        if c1 == c2 {
            remaining -= 1;
            if remaining == 0 {
                break;
            }
            continue;
        }
        c1 = tolower(c1);
        c2 = tolower(c2);
        if c1 != c2 {
            break;
        }
        remaining -= 1;
        if remaining == 0 {
            break;
        }
    }
    c1 as i32 - c2 as i32
}

fn strcasecmp(s1: &[u8], s2: &[u8]) -> i32 {
    let mut i1 = 0usize;
    let mut i2 = 0usize;
    let mut c1: i32;
    let mut c2: i32;
    loop {
        c1 = tolower(s1[i1]) as i32;
        i1 += 1;
        c2 = tolower(s2[i2]) as i32;
        i2 += 1;
        if !(c1 == c2 && c1 != 0) {
            break;
        }
    }
    c1 - c2
}

fn strncpy_(dest: &mut [u8], src: &[u8], mut count: usize) {
    let mut ti = 0usize;
    let mut si = 0usize;
    while count != 0 {
        let c = src[si];
        dest[ti] = c;
        if c != 0 {
            si += 1;
        }
        ti += 1;
        count -= 1;
    }
}

const ALLBUTLAST_BYTE_MASK: usize = !0usize >> 8;

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

// See the matching comment in diff_string.c: the page-boundary-
// avoidance branch is intentionally skipped (no hazard on a host
// buffer, and it doesn't change dest/return-value outputs).
fn sized_strscpy(dest: &mut [u8], src: &[u8], count: usize) -> isize {
    const ONE_BITS: usize = usize::MAX / 0xff;
    const HIGH_BITS: usize = ONE_BITS * 0x80;

    let mut max = count;
    let mut res: isize = 0;

    if count == 0 || count > i32::MAX as usize {
        return -7; // -E2BIG
    }

    let mut count = count;

    while max >= core::mem::size_of::<usize>() {
        let c = usize::from_ne_bytes(
            src[res as usize..res as usize + core::mem::size_of::<usize>()]
                .try_into()
                .unwrap(),
        );
        let mask = has_zero(c, ONE_BITS, HIGH_BITS);
        if mask != 0 {
            let data = create_zero_mask(mask);
            let bytemask = data;
            let masked = c & bytemask;
            dest[res as usize..res as usize + core::mem::size_of::<usize>()]
                .copy_from_slice(&masked.to_ne_bytes());
            return res + find_zero(data) as isize;
        }
        count -= core::mem::size_of::<usize>();
        if count == 0 {
            let c = c & ALLBUTLAST_BYTE_MASK;
            dest[res as usize..res as usize + core::mem::size_of::<usize>()]
                .copy_from_slice(&c.to_ne_bytes());
            return -7;
        }
        dest[res as usize..res as usize + core::mem::size_of::<usize>()]
            .copy_from_slice(&c.to_ne_bytes());
        res += core::mem::size_of::<usize>() as isize;
        max -= core::mem::size_of::<usize>();
    }

    while count > 1 {
        let c = src[res as usize];
        dest[res as usize] = c;
        if c == 0 {
            return res;
        }
        res += 1;
        count -= 1;
    }

    dest[res as usize] = 0;
    if src[res as usize] != 0 {
        -7
    } else {
        res
    }
}

fn strncat_(dest: &mut Vec<u8>, src: &[u8], mut count: usize) {
    if count != 0 {
        let mut si = 0usize;
        loop {
            let c = src[si];
            si += 1;
            dest.pop(); // remove trailing NUL before appending
            dest.push(c);
            dest.push(0);
            if c == 0 {
                break;
            }
            count -= 1;
            if count == 0 {
                break;
            }
        }
    }
}

fn strlcat_(dest: &mut Vec<u8>, src: &[u8], count: usize) -> usize {
    let dsize = dest.len() - 1; // dest is NUL-terminated Vec (excl. trailing 0 in "len")
    let len = src.len();
    let res = dsize + len;
    assert!(dsize < count, "BUG_ON hit: dsize >= count");
    let mut remaining = count - dsize;
    let mut len = len;
    if len >= remaining {
        len = remaining - 1;
    }
    dest.truncate(dsize);
    dest.extend_from_slice(&src[..len]);
    dest.push(0);
    let _ = &mut remaining;
    res
}

fn strchrnul(s: &[u8], c: u8) -> usize {
    let mut i = 0;
    while s[i] != 0 && s[i] != c {
        i += 1;
    }
    i
}

fn strnchrnul(s: &[u8], mut count: usize, c: u8) -> usize {
    let mut i = 0;
    while count != 0 {
        count -= 1;
        if s[i] == 0 || s[i] == c {
            break;
        }
        i += 1;
    }
    i
}

fn strnchr(s: &[u8], mut count: usize, c: u8) -> isize {
    let mut i = 0isize;
    while count != 0 {
        count -= 1;
        if s[i as usize] == c {
            return i;
        }
        if s[i as usize] == 0 {
            break;
        }
        i += 1;
    }
    -1
}

fn strspn(s: &[u8], accept: &[u8]) -> usize {
    let mut i = 0;
    while s[i] != 0 {
        if !accept.contains(&s[i]) {
            break;
        }
        i += 1;
    }
    i
}

fn strcspn(s: &[u8], reject: &[u8]) -> usize {
    let mut i = 0;
    while s[i] != 0 {
        if reject.contains(&s[i]) {
            break;
        }
        i += 1;
    }
    i
}

fn strpbrk(cs: &[u8], ct: &[u8]) -> isize {
    let mut i = 0isize;
    while cs[i as usize] != 0 {
        if ct.contains(&cs[i as usize]) {
            return i;
        }
        i += 1;
    }
    -1
}

fn strsep<'a>(s: &mut Option<&'a [u8]>, ct: &[u8]) -> Option<&'a [u8]> {
    let sbegin = (*s)?;
    let end = strpbrk(sbegin, ct);
    if end >= 0 {
        let tok = &sbegin[..end as usize];
        *s = Some(&sbegin[end as usize + 1..]);
        Some(tok)
    } else {
        *s = None;
        Some(sbegin)
    }
}

fn memcmp_(cs: &[u8], ct: &[u8], count: usize) -> i32 {
    let mut count = count;
    let mut i = 0usize;

    if count >= core::mem::size_of::<usize>() {
        loop {
            let u1 = usize::from_ne_bytes(cs[i..i + 8].try_into().unwrap());
            let u2 = usize::from_ne_bytes(ct[i..i + 8].try_into().unwrap());
            if u1 != u2 {
                break;
            }
            i += 8;
            count -= 8;
            if count < 8 {
                break;
            }
        }
    }

    let mut res: i32 = 0;
    let mut j = i;
    let mut count = count;
    while count > 0 {
        res = cs[j] as i32 - ct[j] as i32;
        if res != 0 {
            break;
        }
        j += 1;
        count -= 1;
    }
    res
}

fn memscan(addr: &[u8], c: u8) -> usize {
    let mut i = 0;
    while i < addr.len() {
        if addr[i] == c {
            return i;
        }
        i += 1;
    }
    i
}

fn strstr_(s1: &[u8], s2: &[u8]) -> isize {
    let l2 = s2.len();
    if l2 == 0 {
        return 0;
    }
    let mut l1 = s1.len();
    let mut i = 0isize;
    while l1 >= l2 {
        l1 -= 1;
        if memcmp_(&s1[i as usize..i as usize + l2], s2, l2) == 0 {
            return i;
        }
        i += 1;
    }
    -1
}

fn strnstr_(s1: &[u8], s2: &[u8], mut len: usize) -> isize {
    let l2 = s2.len();
    if l2 == 0 {
        return 0;
    }
    let mut i = 0isize;
    while len >= l2 {
        len -= 1;
        if memcmp_(&s1[i as usize..i as usize + l2], s2, l2) == 0 {
            return i;
        }
        i += 1;
    }
    -1
}

fn memchr_(s: &[u8], c: u8, mut n: usize) -> isize {
    let mut i = 0isize;
    while n != 0 {
        n -= 1;
        if c == s[i as usize] {
            return i;
        }
        i += 1;
    }
    -1
}

fn check_bytes8(start: &[u8], value: u8, bytes: u32) -> isize {
    for i in 0..bytes as usize {
        if start[i] != value {
            return i as isize;
        }
    }
    -1
}

fn memchr_inv(start: &[u8], c: u8, bytes: usize) -> isize {
    let value = c;

    if bytes <= 16 {
        return check_bytes8(start, value, bytes as u32);
    }

    let mut value64: u64 = value as u64;
    value64 = value64.wrapping_mul(0x0101010101010101);

    let mut off = 0usize;
    let mut bytes = bytes;

    let mut prefix = (start.as_ptr() as usize) % 8;
    if prefix != 0 {
        prefix = 8 - prefix;
        let r = check_bytes8(&start[off..], value, prefix as u32);
        if r >= 0 {
            return off as isize + r;
        }
        off += prefix;
        bytes -= prefix;
    }

    let mut words = bytes / 8;
    while words != 0 {
        let w = u64::from_ne_bytes(start[off..off + 8].try_into().unwrap());
        if w != value64 {
            let r = check_bytes8(&start[off..], value, 8);
            return off as isize + r;
        }
        off += 8;
        words -= 1;
    }

    let r = check_bytes8(&start[off..], value, (bytes % 8) as u32);
    if r >= 0 {
        off as isize + r
    } else {
        -1
    }
}

// Identical LCG to diff_string.c.
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

fn gen_bytes(rng: &mut Lcg, len: usize, biased: bool) -> Vec<u8> {
    (0..len)
        .map(|_| if biased { (rng.next() % 3) as u8 } else { (rng.next() & 0xff) as u8 })
        .collect()
}

const BUFLEN: usize = 64;

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
        let a = gen_str(&mut rng, BUFLEN - 1);
        let b = gen_str(&mut rng, BUFLEN - 1);
        let len = (rng.next() as usize) % (BUFLEN - 1) + 1;

        println!(
            "strncasecmp,{},{},{},{}",
            cstr(&a),
            cstr(&b),
            len,
            strncasecmp(&a, &b, len)
        );
        println!("strcasecmp,{},{},{}", cstr(&a), cstr(&b), strcasecmp(&a, &b));

        let mut dst1 = vec![0u8; BUFLEN];
        let mut a_padded = a.clone();
        a_padded.resize(BUFLEN, 0);
        strncpy_(&mut dst1, &a_padded, BUFLEN - 1);
        println!("strncpy,{},[{}]", cstr(&a), cstr(&dst1[..BUFLEN - 1]));

        let mut dst2 = vec![0x55u8; BUFLEN];
        let cnt = (rng.next() as usize) % (BUFLEN - 1) + 1;
        let mut a_scpy = a.clone();
        a_scpy.resize(BUFLEN, 0);
        let r = sized_strscpy(&mut dst2, &a_scpy, cnt);
        println!("strscpy,{},{},{},[{}]", cstr(&a), cnt, r, cstr(&dst2));

        let mut dst3: Vec<u8> = a[..a.len() - 1].to_vec();
        dst3.push(0);
        strncat_(&mut dst3, &b, (rng.next() % 16) as usize);
        println!("strncat,{},{},[{}]", cstr(&a), cstr(&b), cstr(&dst3));

        let mut dst4: Vec<u8> = a[..a.len() - 1].to_vec();
        dst4.push(0);
        let src_nonul = &b[..b.len() - 1];
        let lr = strlcat_(&mut dst4, src_nonul, BUFLEN);
        println!("strlcat,{},{},{},[{}]", cstr(&a), cstr(&b), lr, cstr(&dst4));

        let ch = ALPHABET[(rng.next() as usize) % ALPHABET.len()];
        let r1 = strchrnul(&a, ch);
        println!("strchrnul,{},{},{}", cstr(&a), ch as char, r1);
        let r2 = strnchrnul(&a, len, ch);
        println!("strnchrnul,{},{},{},{}", cstr(&a), len, ch as char, r2);
        let r3 = strnchr(&a, len, ch);
        println!("strnchr,{},{},{},{}", cstr(&a), len, ch as char, r3);

        println!("strspn,{},{},{}", cstr(&a), cstr(&b), strspn(&a, &b));
        println!("strcspn,{},{},{}", cstr(&a), cstr(&b), strcspn(&a, &b));
        let r4 = strpbrk(&a, &b);
        println!("strpbrk,{},{},{}", cstr(&a), cstr(&b), r4);

        let mut sp: Option<&[u8]> = Some(&a[..]);
        print!("strsep,{},{}", cstr(&a), cstr(&b));
        for _ in 0..5 {
            if sp.is_none() {
                break;
            }
            let tok = strsep(&mut sp, &b[..]);
            print!(",[{}]", cstr(tok.unwrap()));
        }
        println!();

        let m1 = gen_bytes(&mut rng, BUFLEN, true);
        let mut m2 = m1.clone();
        let mlen = (rng.next() as usize) % BUFLEN;
        if rng.next() % 4 == 0 && mlen > 0 {
            let idx = (rng.next() as usize) % mlen;
            m2[idx] ^= 0xff;
        }
        println!("memcmp,{},{}", mlen, memcmp_(&m1[..mlen], &m2[..mlen], mlen));

        let sbuf = gen_bytes(&mut rng, BUFLEN, true);
        let target = (rng.next() % 3) as u8;
        let sr = memscan(&sbuf, target);
        println!("memscan,{},{}", target, sr);

        let sr2 = strstr_(&a[..a.len() - 1], &b[..b.len() - 1]);
        println!("strstr,{},{},{}", cstr(&a), cstr(&b), sr2);
        let slen = (rng.next() as usize) % (BUFLEN - 1);
        let mut a_ns = a.clone();
        a_ns.resize(BUFLEN, 0);
        let sr3 = strnstr_(&a_ns, &b[..b.len() - 1], slen);
        println!("strnstr,{},{},{},{}", cstr(&a), cstr(&b), slen, sr3);

        let cr = memchr_(&m1, target, mlen);
        println!("memchr,{},{},{}", target, mlen, cr);

        let fillval = (rng.next() % 3) as u8;
        let mut invbuf = vec![fillval; BUFLEN];
        if rng.next() % 3 != 0 {
            let pos = (rng.next() as usize) % BUFLEN;
            invbuf[pos] = (fillval + 1) % 3;
        }
        let ilen = (rng.next() as usize) % BUFLEN;
        let ir = memchr_inv(&invbuf[..ilen], fillval, ilen);
        println!("memchr_inv,{},{},{}", fillval, ilen, ir);
    }
}
