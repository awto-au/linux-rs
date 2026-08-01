// SPDX-License-Identifier: GPL-2.0
//! Merge two NULL-terminated pointer arrays — Rust translation of
//! `lib/memcat_p.c`.

use kernel::bindings;
use kernel::ffi::c_void;
use kernel::prelude::*;

/// Merge `a` and `b` (both NULL-terminated `void*` arrays) into a newly
/// allocated NULL-terminated array. NULL on allocation failure.
///
/// # Safety
/// `a` and `b` must each point to a NULL-terminated array of `*mut c_void`.
#[export]
pub unsafe extern "C" fn __memcat_p(
    a: *mut *mut c_void,
    b: *mut *mut c_void,
) -> *mut *mut c_void {
    // SAFETY: caller guarantees NULL-terminated arrays.
    let (nr_a, nr_b) = unsafe {
        let mut nr_a = 0isize;
        while !(*a.offset(nr_a)).is_null() {
            nr_a += 1;
        }
        let mut nr_b = 0isize;
        while !(*b.offset(nr_b)).is_null() {
            nr_b += 1;
        }
        (nr_a, nr_b)
    };
    let nr = nr_a + nr_b + 1; // +1 for the NULL terminator

    // SAFETY: kmalloc_array with a valid size/count; result checked below.
    let new = unsafe {
        bindings::kmalloc_array(
            nr as usize,
            core::mem::size_of::<*mut c_void>(),
            bindings::GFP_KERNEL,
        )
    } as *mut *mut c_void;
    if new.is_null() {
        return core::ptr::null_mut();
    }

    // SAFETY: `new` has room for `nr` entries; source indices stay within
    // the bounds established above (b[0..nr_b] then a[0..nr_a]).
    unsafe {
        for i in 0..nr_b {
            *new.offset(nr_a + i) = *b.offset(i);
        }
        for i in 0..nr_a {
            *new.offset(i) = *a.offset(i);
        }
        // NULL terminator (C relies on `nr`'s last slot; make it explicit).
        *new.offset(nr - 1) = core::ptr::null_mut();
    }

    new
}
