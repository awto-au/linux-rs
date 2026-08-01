// SPDX-License-Identifier: GPL-2.0
//! UCS2 string handling — Rust translation of `lib/ucs2_string.c`.
//!
//! Raw-pointer string walks, faithful; WARN_ON_ONCE via the C shim
//! (rule 0014 — exact here: this TU has a single call site).
//! MODULE_* macros dropped (builtin, CONFIG_MODULES=n).

use kernel::bindings;
use kernel::ffi::{c_int, c_ulong};
use kernel::prelude::*;

type Ucs2Char = bindings::ucs2_char_t; // u16

/// Return the number of unicode characters in data.
///
/// All pointer parameters follow the C contract: valid, NUL-terminated
/// (or `maxlength`-bounded) UCS2 buffers.
#[export]
pub unsafe extern "C" fn ucs2_strnlen(mut s: *const Ucs2Char, maxlength: usize) -> c_ulong {
    let mut length: c_ulong = 0;

    // SAFETY: per C contract; we read at most maxlength+1 chars up to NUL.
    unsafe {
        // C: while (*s++ != 0 && length < maxlength) length++;
        loop {
            let c = *s;
            s = s.add(1);
            if c != 0 && (length as usize) < maxlength {
                length += 1;
            } else {
                break;
            }
        }
    }
    length
}

/// Return the number of unicode characters in a NUL-terminated string.
#[export]
pub unsafe extern "C" fn ucs2_strlen(s: *const Ucs2Char) -> c_ulong {
    // SAFETY: as ucs2_strnlen.
    unsafe { ucs2_strnlen(s, !0usize) }
}

/// Return the length of this string in BYTES (not unicode characters).
#[export]
pub unsafe extern "C" fn ucs2_strsize(data: *const Ucs2Char, maxlength: c_ulong) -> c_ulong {
    let sz = core::mem::size_of::<Ucs2Char>() as c_ulong;
    // SAFETY: as ucs2_strnlen.
    unsafe { ucs2_strnlen(data, (maxlength / sz) as usize) * sz }
}

/// Copy a UCS2 string into a sized buffer, always NUL-terminating.
/// Returns characters copied (excl. NUL) or -E2BIG (count 0 or truncated).
#[export]
pub unsafe extern "C" fn ucs2_strscpy(
    dst: *mut Ucs2Char,
    src: *const Ucs2Char,
    count: usize,
) -> isize {
    let e2big = -(bindings::E2BIG as isize);

    // Need space for at least one NUL character.
    // SAFETY: WARN_ON_ONCE shim only evaluates the condition.
    if count == 0
        || unsafe {
            bindings::WARN_ON_ONCE(count > i32::MAX as usize / core::mem::size_of::<Ucs2Char>())
        }
    {
        return e2big;
    }

    // SAFETY: dst has room for `count` chars, src is NUL-terminated or
    // longer than count (C contract); buffers don't overlap.
    unsafe {
        // Copy at most 'count' characters, return early on NUL.
        let mut res: isize = 0;
        while (res as usize) < count {
            let c = *src.add(res as usize);
            *dst.add(res as usize) = c;

            if c == 0 {
                return res;
            }
            res += 1;
        }

        // No NUL found within count: enforce NUL-termination, report error.
        *dst.add(count - 1) = 0;
    }
    e2big
}

/// Compare two UCS2 strings, at most `len` characters.
#[export]
pub unsafe extern "C" fn ucs2_strncmp(
    mut a: *const Ucs2Char,
    mut b: *const Ucs2Char,
    mut len: usize,
) -> c_int {
    // SAFETY: per C contract (valid, bounded/NUL-terminated buffers).
    unsafe {
        loop {
            if len == 0 {
                return 0;
            }
            if *a < *b {
                return -1;
            }
            if *a > *b {
                return 1;
            }
            if *a == 0 {
                // implies *b == 0
                return 0;
            }
            a = a.add(1);
            b = b.add(1);
            len -= 1;
        }
    }
}

/// Number of bytes the string needs when encoded as UTF-8.
#[export]
pub unsafe extern "C" fn ucs2_utf8size(src: *const Ucs2Char) -> c_ulong {
    let mut j: c_ulong = 0;

    // SAFETY: src is NUL-terminated (C contract).
    unsafe {
        let mut i = 0usize;
        while *src.add(i) != 0 {
            let c: u16 = *src.add(i);

            if c >= 0x800 {
                j += 3;
            } else if c >= 0x80 {
                j += 2;
            } else {
                j += 1;
            }
            i += 1;
        }
    }
    j
}

/// Copy at most `maxlength` bytes of whole UTF-8 characters to `dest` from
/// the UCS2 string `src`. Returns characters copied, excluding final NUL.
#[export]
pub unsafe extern "C" fn ucs2_as_utf8(
    dest: *mut u8,
    src: *const Ucs2Char,
    mut maxlength: c_ulong,
) -> c_ulong {
    let mut j: usize = 0;
    // SAFETY: src NUL-terminated; dest has maxlength bytes (C contract).
    unsafe {
        let limit = ucs2_strnlen(src, maxlength as usize);

        let mut i: usize = 0;
        while maxlength != 0 && (i as c_ulong) < limit {
            let c: u16 = *src.add(i);

            if c >= 0x800 {
                if maxlength < 3 {
                    break;
                }
                maxlength -= 3;
                *dest.add(j) = 0xe0 | ((c & 0xf000) >> 12) as u8;
                j += 1;
                *dest.add(j) = 0x80 | ((c & 0x0fc0) >> 6) as u8;
                j += 1;
                *dest.add(j) = 0x80 | (c & 0x003f) as u8;
                j += 1;
            } else if c >= 0x80 {
                if maxlength < 2 {
                    break;
                }
                maxlength -= 2;
                *dest.add(j) = 0xc0 | ((c & 0x7c0) >> 6) as u8;
                j += 1;
                *dest.add(j) = 0x80 | (c & 0x03f) as u8;
                j += 1;
            } else {
                maxlength -= 1;
                *dest.add(j) = (c & 0x7f) as u8;
                j += 1;
            }
            i += 1;
        }
        if maxlength != 0 {
            *dest.add(j) = 0;
        }
    }
    j as c_ulong
}
