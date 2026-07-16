// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side. Faithful copy of
//! lib/base64_rs.rs's algorithm (kernel-crate `#[export]`/bindings types
//! stripped for host build) — same protocol as diff_base64.c, same LCG
//! so both binaries see the byte-identical input stream.

const BASE64_TABLES: [&[u8; 64]; 3] = [
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_",
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+,",
];

const fn rev_map(ch_62: u8, ch_63: u8) -> [i8; 256] {
    let mut t = [-1i8; 256];
    let mut v = 0usize;
    while v < 256 {
        let c = v as u8;
        t[v] = if c >= b'A' && c <= b'Z' {
            (c - b'A') as i8
        } else if c >= b'a' && c <= b'z' {
            (c - b'a') as i8 + 26
        } else if c >= b'0' && c <= b'9' {
            (c - b'0') as i8 + 52
        } else if c == ch_62 {
            62
        } else if c == ch_63 {
            63
        } else {
            -1
        };
        v += 1;
    }
    t
}

const BASE64_REV_MAPS: [[i8; 256]; 3] =
    [rev_map(b'+', b'/'), rev_map(b'-', b'_'), rev_map(b'+', b',')];

fn base64_encode(src: &[u8], dst: &mut [u8], padding: bool, variant: usize) -> usize {
    let table = BASE64_TABLES[variant];
    let mut j = 0;
    let mut i = 0;
    let mut srclen = src.len();

    let mut put = |dst: &mut [u8], j: &mut usize, b: u8| {
        dst[*j] = b;
        *j += 1;
    };

    while srclen >= 3 {
        let ac: u32 = (src[i] as u32) << 16 | (src[i + 1] as u32) << 8 | src[i + 2] as u32;
        put(dst, &mut j, table[(ac >> 18) as usize]);
        put(dst, &mut j, table[(ac >> 12) as usize & 0x3f]);
        put(dst, &mut j, table[(ac >> 6) as usize & 0x3f]);
        put(dst, &mut j, table[ac as usize & 0x3f]);
        i += 3;
        srclen -= 3;
    }
    match srclen {
        2 => {
            let ac: u32 = (src[i] as u32) << 16 | (src[i + 1] as u32) << 8;
            put(dst, &mut j, table[(ac >> 18) as usize]);
            put(dst, &mut j, table[(ac >> 12) as usize & 0x3f]);
            put(dst, &mut j, table[(ac >> 6) as usize & 0x3f]);
            if padding {
                put(dst, &mut j, b'=');
            }
        }
        1 => {
            let ac: u32 = (src[i] as u32) << 16;
            put(dst, &mut j, table[(ac >> 18) as usize]);
            put(dst, &mut j, table[(ac >> 12) as usize & 0x3f]);
            if padding {
                put(dst, &mut j, b'=');
                put(dst, &mut j, b'=');
            }
        }
        _ => {}
    }
    j
}

fn base64_decode(
    src: &[u8],
    mut srclen: i64,
    dst: &mut [u8],
    mut padding: bool,
    variant: usize,
) -> i64 {
    let rev = &BASE64_REV_MAPS[variant];
    let mut s = 0usize;
    let mut j = 0usize;

    while srclen >= 4 {
        let input = [
            rev[src[s] as usize] as i32,
            rev[src[s + 1] as usize] as i32,
            rev[src[s + 2] as usize] as i32,
            rev[src[s + 3] as usize] as i32,
        ];
        let val: i32 = input[0] << 18 | input[1] << 12 | input[2] << 6 | input[3];
        if val < 0 {
            if !padding || srclen != 4 || src[s + 3] != b'=' {
                return -1;
            }
            padding = false;
            srclen = if src[s + 2] == b'=' { 2 } else { 3 };
            break;
        }
        dst[j] = (val >> 16) as u8;
        j += 1;
        dst[j] = (val >> 8) as u8;
        j += 1;
        dst[j] = val as u8;
        j += 1;
        s += 4;
        srclen -= 4;
    }
    if srclen == 0 {
        return j as i64;
    }
    if padding || srclen == 1 {
        return -1;
    }
    let mut val: i32 = ((rev[src[s] as usize] as i32) << 12) | ((rev[src[s + 1] as usize] as i32) << 6);
    dst[j] = (val >> 10) as u8;
    j += 1;
    if srclen == 2 {
        if val as u32 & 0x800003ff != 0 {
            return -1;
        }
    } else {
        val |= rev[src[s + 2] as usize] as i32;
        if val as u32 & 0x80000003 != 0 {
            return -1;
        }
        dst[j] = (val >> 2) as u8;
        j += 1;
    }
    j as i64
}

// Identical LCG to diff_base64.c (same constants, same u32-from-high-bits
// extraction) — required for both sides to see the same input stream.
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
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(2000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(12345);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let variant = (rng.next() % 3) as usize;
        let padding = rng.next() % 2 == 1;
        let srclen = (rng.next() % 130) as usize;
        let src: Vec<u8> = (0..srclen).map(|_| (rng.next() & 0xff) as u8).collect();

        let mut enc = [0u8; 256];
        let elen = base64_encode(&src, &mut enc, padding, variant);
        print!("enc,{},{},{},", variant, padding as i32, elen);
        for b in &enc[..elen] {
            print!("{:02x}", b);
        }
        println!();

        let mut dec = [0u8; 256];
        let dlen = base64_decode(&enc[..elen], elen as i64, &mut dec, padding, variant);
        print!("dec,{},{},{},", variant, padding as i32, dlen);
        if dlen > 0 {
            for b in &dec[..dlen as usize] {
                print!("{:02x}", b);
            }
        }
        println!();
    }
}
