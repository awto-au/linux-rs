// SPDX-License-Identifier: GPL-2.0
//! Base64 with multiple variants — Rust translation of `lib/base64.c`.
//!
//! Copyright (c) 2020 Hannes Reinecke, SUSE (original algorithm).
//!
//! The C builds its reverse-mapping tables with recursive initialiser
//! macros; here a `const fn` computes the identical tables at compile
//! time. Encode/decode walks kept faithful, including the C integer
//! promotions that make invalid characters surface as a negative `val`.

use kernel::bindings;
use kernel::ffi::c_int;
use kernel::prelude::*;

const BASE64_TABLES: [&[u8; 64]; 3] = [
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_",
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+,",
];

/// Same mapping the C INIT_* macros produce: 'A'-'Z' → 0..25,
/// 'a'-'z' → 26..51, '0'-'9' → 52..61, ch_62 → 62, ch_63 → 63, else -1.
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

/// Base64-encode binary data (not NUL-terminated); returns encoded length.
#[export]
pub unsafe extern "C" fn base64_encode(
    mut src: *const u8,
    mut srclen: c_int,
    dst: *mut kernel::ffi::c_char,
    padding: bool,
    variant: bindings::base64_variant,
) -> c_int {
    let table = BASE64_TABLES[variant as usize];
    let mut j: usize = 0;

    // SAFETY: src has srclen bytes; dst is large enough per the C API
    // contract (BASE64_CHARS(srclen) characters).
    unsafe {
        let put = |cp: &mut usize, b: u8| {
            *dst.add(*cp) = b as _;
            *cp += 1;
        };
        while srclen >= 3 {
            let ac: u32 =
                (*src as u32) << 16 | (*src.add(1) as u32) << 8 | *src.add(2) as u32;
            put(&mut j, table[(ac >> 18) as usize]);
            put(&mut j, table[(ac >> 12) as usize & 0x3f]);
            put(&mut j, table[(ac >> 6) as usize & 0x3f]);
            put(&mut j, table[ac as usize & 0x3f]);

            src = src.add(3);
            srclen -= 3;
        }

        match srclen {
            2 => {
                let ac: u32 = (*src as u32) << 16 | (*src.add(1) as u32) << 8;
                put(&mut j, table[(ac >> 18) as usize]);
                put(&mut j, table[(ac >> 12) as usize & 0x3f]);
                put(&mut j, table[(ac >> 6) as usize & 0x3f]);
                if padding {
                    put(&mut j, b'=');
                }
            }
            1 => {
                let ac: u32 = (*src as u32) << 16;
                put(&mut j, table[(ac >> 18) as usize]);
                put(&mut j, table[(ac >> 12) as usize & 0x3f]);
                if padding {
                    put(&mut j, b'=');
                    put(&mut j, b'=');
                }
            }
            _ => {}
        }
    }
    j as c_int
}

/// Base64-decode a string; returns decoded length or -1 if invalid.
#[export]
pub unsafe extern "C" fn base64_decode(
    src: *const kernel::ffi::c_char,
    mut srclen: c_int,
    dst: *mut u8,
    mut padding: bool,
    variant: bindings::base64_variant,
) -> c_int {
    let rev = &BASE64_REV_MAPS[variant as usize];
    let mut s = src.cast::<u8>();
    let mut j: usize = 0;

    // SAFETY: src has srclen bytes; dst is large enough per the C API
    // contract; table lookups are u8-indexed into 256-entry tables.
    unsafe {
        while srclen >= 4 {
            // C: s8 values are promoted (sign-extended) to int before the
            // shifts, so any -1 entry drives `val` negative.
            let input = [
                rev[*s as usize] as i32,
                rev[*s.add(1) as usize] as i32,
                rev[*s.add(2) as usize] as i32,
                rev[*s.add(3) as usize] as i32,
            ];
            let val: i32 = input[0] << 18 | input[1] << 12 | input[2] << 6 | input[3];

            if val < 0 {
                if !padding || srclen != 4 || *s.add(3) != b'=' {
                    return -1;
                }
                padding = false;
                srclen = if *s.add(2) == b'=' { 2 } else { 3 };
                break;
            }

            *dst.add(j) = (val >> 16) as u8;
            j += 1;
            *dst.add(j) = (val >> 8) as u8;
            j += 1;
            *dst.add(j) = val as u8;
            j += 1;

            s = s.add(4);
            srclen -= 4;
        }

        if srclen == 0 {
            return j as c_int;
        }
        if padding || srclen == 1 {
            return -1;
        }

        let mut val: i32 =
            ((rev[*s as usize] as i32) << 12) | ((rev[*s.add(1) as usize] as i32) << 6);
        *dst.add(j) = (val >> 10) as u8;
        j += 1;

        if srclen == 2 {
            // C compares against unsigned 0x800003ff: val converts to u32.
            if val as u32 & 0x800003ff != 0 {
                return -1;
            }
        } else {
            val |= rev[*s.add(2) as usize] as i32;
            if val as u32 & 0x80000003 != 0 {
                return -1;
            }
            *dst.add(j) = (val >> 2) as u8;
            j += 1;
        }
    }
    j as c_int
}
