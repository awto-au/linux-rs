// SPDX-License-Identifier: GPL-2.0
//! Split a string into an argv-like array — Rust translation of
//! `lib/argv_split.c`.
//!
//! The C stashes the backing string allocation at `argv[-1]` so
//! `argv_free` can recover it from just the returned pointer; the
//! pointer arithmetic (`argv--` / `argv[0]`) is preserved verbatim.

use kernel::bindings;
use kernel::ffi::{c_char, c_int};
use kernel::prelude::*;

fn is_space(c: u8) -> bool {
    // SAFETY: isspace has no preconditions; c promoted to c_int as C does.
    unsafe { bindings::isspace(c as c_int) != 0 }
}

fn count_argc(s: &[u8]) -> i32 {
    let mut count = 0i32;
    let mut was_space = true;
    for &c in s {
        if c == 0 {
            break;
        }
        if is_space(c) {
            was_space = true;
        } else if was_space {
            was_space = false;
            count += 1;
        }
    }
    count
}

/// Free an argv previously returned by `argv_split`.
///
/// # Safety
/// `argv` must be a pointer previously returned by `argv_split`.
#[export]
pub unsafe extern "C" fn argv_free(argv: *mut *mut c_char) {
    // SAFETY: argv_split always returns `base.add(1)`, so `argv.sub(1)`
    // recovers the base slot holding the string allocation (C: argv--).
    unsafe {
        let base = argv.sub(1);
        bindings::kfree((*base).cast());
        bindings::kfree(base.cast());
    }
}

/// Split `str` at whitespace into a NULL-terminated argv; NULL on
/// allocation failure. `*argcp` receives the argument count if non-NULL.
///
/// # Safety
/// `str` must be a valid NUL-terminated C string.
#[export]
pub unsafe extern "C" fn argv_split(
    gfp: bindings::gfp_t,
    str_: *const c_char,
    argcp: *mut c_int,
) -> *mut *mut c_char {
    // SAFETY: kstrndup with a valid NUL-terminated source (C ABI contract).
    let argv_str = unsafe {
        bindings::kstrndup(str_, bindings::KMALLOC_MAX_SIZE as usize - 1, gfp)
    };
    if argv_str.is_null() {
        return core::ptr::null_mut();
    }

    // SAFETY: argv_str is NUL-terminated; bounded scan for counting only.
    let len = unsafe { bindings::strlen(argv_str) } as usize;
    let s = unsafe { core::slice::from_raw_parts(argv_str.cast::<u8>(), len) };
    let argc = count_argc(s);

    // SAFETY: kmalloc_array with a valid size/count; checked below.
    let base = unsafe {
        bindings::kmalloc_array(
            (argc + 2) as usize,
            core::mem::size_of::<*mut c_char>(),
            gfp,
        )
    } as *mut *mut c_char;
    if base.is_null() {
        // SAFETY: argv_str came from kstrndup above.
        unsafe {
            bindings::kfree(argv_str.cast());
        }
        return core::ptr::null_mut();
    }

    // SAFETY: base has room for argc+2 slots; argv_str has `len+1` bytes
    // (incl. NUL) that we walk and NUL-split in place, exactly as the C
    // does — argv_str's allocation now belongs to `base[0]`.
    unsafe {
        *base = argv_str;
        let argv_ret = base.add(1);
        let mut argv = argv_ret;
        let mut was_space = true;
        let mut p = argv_str as *mut u8;
        for _ in 0..len {
            if is_space(*p) {
                was_space = true;
                *p = 0;
            } else if was_space {
                was_space = false;
                *argv = p.cast();
                argv = argv.add(1);
            }
            p = p.add(1);
        }
        *argv = core::ptr::null_mut();

        if !argcp.is_null() {
            *argcp = argc;
        }
        argv_ret
    }
}
