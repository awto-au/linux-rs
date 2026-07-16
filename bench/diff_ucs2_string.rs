// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, ucs2_string.
//! Faithful copy of lib/ucs2_string_rs.rs's algorithm (kernel-crate
//! bindings/export stripped) — same protocol/LCG as diff_ucs2_string.c.
//! Safe-slice reimplementation of the same pointer-walk logic (raw
//! pointers unnecessary for a host-side equivalence check).

type Ucs2Char = u16;

fn ucs2_strnlen(s: &[Ucs2Char], maxlength: usize) -> u64 {
    let mut length: u64 = 0;
    for &c in s {
        if c == 0 || (length as usize) >= maxlength {
            break;
        }
        length += 1;
    }
    length
}

fn ucs2_strscpy(dst: &mut [Ucs2Char], src: &[Ucs2Char], count: usize) -> i64 {
    if count == 0 {
        return -1;
    }
    for res in 0..count {
        let c = src[res];
        dst[res] = c;
        if c == 0 {
            return res as i64;
        }
    }
    dst[count - 1] = 0;
    -1
}

fn ucs2_strncmp(a: &[Ucs2Char], b: &[Ucs2Char], mut len: usize) -> i32 {
    let mut i = 0;
    loop {
        if len == 0 {
            return 0;
        }
        if a[i] < b[i] {
            return -1;
        }
        if a[i] > b[i] {
            return 1;
        }
        if a[i] == 0 {
            return 0;
        }
        i += 1;
        len -= 1;
    }
}

fn ucs2_utf8size(src: &[Ucs2Char]) -> u64 {
    let mut j: u64 = 0;
    for &c in src {
        if c == 0 {
            break;
        }
        if c >= 0x800 {
            j += 3;
        } else if c >= 0x80 {
            j += 2;
        } else {
            j += 1;
        }
    }
    j
}

fn ucs2_as_utf8(dest: &mut [u8], src: &[Ucs2Char], mut maxlength: u64) -> u64 {
    let mut j: usize = 0;
    let limit = ucs2_strnlen(src, maxlength as usize);

    let mut i: usize = 0;
    while maxlength != 0 && (i as u64) < limit {
        let c = src[i];

        if c >= 0x800 {
            if maxlength < 3 {
                break;
            }
            maxlength -= 3;
            dest[j] = 0xe0 | ((c & 0xf000) >> 12) as u8;
            j += 1;
            dest[j] = 0x80 | ((c & 0x0fc0) >> 6) as u8;
            j += 1;
            dest[j] = 0x80 | (c & 0x003f) as u8;
            j += 1;
        } else if c >= 0x80 {
            if maxlength < 2 {
                break;
            }
            maxlength -= 2;
            dest[j] = 0xc0 | ((c & 0x7c0) >> 6) as u8;
            j += 1;
            dest[j] = 0x80 | (c & 0x03f) as u8;
            j += 1;
        } else {
            maxlength -= 1;
            dest[j] = (c & 0x7f) as u8;
            j += 1;
        }
        i += 1;
    }
    if maxlength != 0 {
        dest[j] = 0;
    }
    j as u64
}

// Identical LCG to diff_ucs2_string.c / the other diff_* files.
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

fn rand_ucs2(rng: &mut Lcg) -> Ucs2Char {
    let r = rng.next();
    match r % 8 {
        0 => 0,
        1 | 2 => (r % 0x80) as Ucs2Char,
        3 | 4 => (0x80 + (r % (0x800 - 0x80))) as Ucs2Char,
        _ => (0x800 + (r % (0x10000 - 0x800))) as Ucs2Char,
    }
}

const MAXN: usize = 40;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(3000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(12345);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let mut a = [0 as Ucs2Char; MAXN];
        let alen = 1 + (rng.next() as usize % (MAXN - 1));
        for k in 0..alen - 1 {
            a[k] = rand_ucs2(&mut rng) | 1;
        }
        a[alen - 1] = 0;
        let mut b = [0 as Ucs2Char; MAXN];
        let blen = 1 + (rng.next() as usize % (MAXN - 1));
        for k in 0..blen - 1 {
            b[k] = rand_ucs2(&mut rng) | 1;
        }
        b[blen - 1] = 0;

        println!("strnlen,{}", ucs2_strnlen(&a, MAXN));
        println!("utf8size,{}", ucs2_utf8size(&a));
        println!("strncmp,{}", ucs2_strncmp(&a, &b, MAXN));

        let mut dst = [0 as Ucs2Char; MAXN];
        let mut cnt = 1 + (rng.next() as usize % MAXN);
        if cnt > MAXN {
            cnt = MAXN;
        }
        let cp = ucs2_strscpy(&mut dst, &a, cnt);
        print!("strscpy,{},", cp);
        for k in 0..(if cp >= 0 { cp as usize } else { 0 }) {
            print!("{:04x}", dst[k]);
        }
        println!();

        let mut utf8 = [0u8; MAXN * 3 + 1];
        let maxlen = (rng.next() as u64) % (MAXN as u64 * 3);
        let ulen = ucs2_as_utf8(&mut utf8, &a, maxlen);
        print!("as_utf8,{},", ulen);
        for k in 0..ulen as usize {
            print!("{:02x}", utf8[k]);
        }
        println!();
    }
}
