// SPDX-License-Identifier: GPL-2.0-only
//! Find a specific cpio member in an uncompressed early-boot cpio archive
//! — Rust translation of `lib/earlycpio.c`.
//!
//! Copyright 2012 Intel Corporation; author H. Peter Anvin (original C).
//!
//! Hex-field header parser + bounds-checked buffer walk, kept faithful:
//! same `goto quit` early-return points (as plain `return`s here — no
//! cleanup happens at `quit`, it's a bare early-exit), same overflow
//! checks on the computed `dptr`/`nptr` pointers.

use kernel::bindings;
use kernel::ffi::{c_char, c_int, c_long, c_void};
use kernel::prelude::*;

const MAX_CPIO_FILE_NAME: usize = 18;
const C_NFIELDS: usize = 14; // C_MAGIC..C_CHKSUM
const C_MAGIC: usize = 0;
const C_MODE: usize = 2;
const C_NAMESIZE: usize = 12;
const C_FILESIZE: usize = 7;

// SAFETY note: bindings::cpio_data is bindgen-generated from
// <linux/earlycpio.h>'s `struct cpio_data` — #[export] requires the
// EXACT bindgen type here, not a locally-defined lookalike struct
// (even an identical #[repr(C)] one is a different Rust type).
use kernel::bindings::cpio_data as CpioData;

/// `PTR_ALIGN(p, a)`: round `p` up to the next multiple of `a` (`a` a
/// power of 2) — header-inline macro, rule 0016.
#[inline]
fn ptr_align(p: *const u8, a: usize) -> *const u8 {
    let addr = p as usize;
    ((addr + a - 1) & !(a - 1)) as *const u8
}

/// Search for files in an uncompressed cpio.
///
/// # Safety
/// `path` is a valid NUL-terminated C string; `data` points to `len`
/// valid bytes; `nextoff`, if non-NULL, is a valid `*mut c_long`.
#[export]
pub unsafe extern "C" fn find_cpio_data(
    path: *const c_char,
    data: *mut c_void,
    len: usize,
    nextoff: *mut c_long,
) -> CpioData {
    const CPIO_HEADER_LEN: usize = 8 * C_NFIELDS - 2;

    let empty_name = {
        let mut n = [0 as c_char; MAX_CPIO_FILE_NAME];
        n[0] = 0;
        n
    };
    let mut cd = CpioData { data: core::ptr::null_mut(), size: 0, name: empty_name };

    // SAFETY: caller contract (path is a valid C string).
    let mypathsize = unsafe { bindings::strlen(path) } as usize;

    let mut p = data as *const u8;
    let mut len = len;

    // SAFETY: the whole loop body operates within the `len` bytes at
    // `data` the caller guarantees, matching the C's own bounds exactly
    // (each field read is checked against `len`/computed offsets before
    // being dereferenced, same as the original).
    unsafe {
        while len > CPIO_HEADER_LEN {
            if *p == 0 {
                // All cpio headers need to be 4-byte aligned.
                p = p.add(4);
                len -= 4;
                continue;
            }

            let mut ch = [0u32; C_NFIELDS];
            let mut j: u32 = 6; // the magic field is only 6 characters
            let mut chp = 0usize;
            let mut quit = false;

            for _i in 0..C_NFIELDS {
                let mut v: u32 = 0;
                while j > 0 {
                    j -= 1;
                    v <<= 4;
                    let c = *p;
                    p = p.add(1);

                    let x = c.wrapping_sub(b'0');
                    if x < 10 {
                        v += x as u32;
                        continue;
                    }
                    let x = (c | 0x20).wrapping_sub(b'a');
                    if x < 6 {
                        v += x as u32 + 10;
                        continue;
                    }
                    quit = true; // Invalid hexadecimal
                    break;
                }
                if quit {
                    break;
                }
                ch[chp] = v;
                chp += 1;
                j = 8; // all other fields are 8 characters
            }
            if quit {
                return cd;
            }

            if ch[C_MAGIC].wrapping_sub(0x070701) > 1 {
                return cd; // Invalid magic
            }

            len -= CPIO_HEADER_LEN;

            let dptr = ptr_align(p.add(ch[C_NAMESIZE] as usize), 4);
            let nptr = ptr_align(dptr.add(ch[C_FILESIZE] as usize), 4);

            if nptr > p.add(len) || dptr < p || nptr < dptr {
                return cd; // Buffer overrun
            }

            if (ch[C_MODE] & 0o170000) == 0o100000
                && (ch[C_NAMESIZE] as usize) >= mypathsize
                && bindings::memcmp(p.cast(), path.cast(), mypathsize) == 0
            {
                if !nextoff.is_null() {
                    *nextoff = (nptr as isize - data as isize) as c_long;
                }

                if (ch[C_NAMESIZE] as usize).wrapping_sub(mypathsize) >= MAX_CPIO_FILE_NAME {
                    bindings::pr_warn_cpio_name_too_long(
                        p.cast(),
                        MAX_CPIO_FILE_NAME as c_int,
                    );
                }
                bindings::sized_strscpy(
                    cd.name.as_mut_ptr(),
                    p.add(mypathsize).cast(),
                    MAX_CPIO_FILE_NAME,
                );

                cd.data = dptr as *mut c_void;
                cd.size = ch[C_FILESIZE] as usize;
                return cd; // Found it!
            }
            len -= nptr as usize - p as usize;
            p = nptr;
        }
    }

    cd
}
