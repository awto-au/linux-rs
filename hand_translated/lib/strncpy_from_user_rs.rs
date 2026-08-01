// SPDX-License-Identifier: GPL-2.0
//! Word-at-a-time `strncpy_from_user()` — Rust translation of
//! `lib/strncpy_from_user.c`.
//!
//! Copies a NUL-terminated string from a userspace pointer into a
//! kernel buffer, using the same word-at-a-time scan as
//! `strnlen_user()` (`lib/strnlen_user_rs.rs`) and `sized_strscpy`
//! (`lib/string_rs.rs`), but through the fault-safe
//! `unsafe_get_user()`/goto machinery since the source is a raw
//! `__user` pointer that may fault. Reuses the exact
//! `access_ok`/`untagged_addr`/`TASK_SIZE_MAX`/`user_read_access_begin`/
//! `unsafe_get_user_ul` shims `strnlen_user_rs.rs` established in
//! `rust/helpers/uaccess.c` (rule 0014/0028) — same arch-specific,
//! security-relevant primitives, not re-derived here. One NEW shim is
//! added for this TU: `rust_helper_unsafe_get_user_u8`, a byte-sized
//! sibling of `unsafe_get_user_ul` needed for the C's `byte_at_a_time`
//! fallback loop (`unsafe_get_user(c, src+res, efault)` where `c` is
//! `char`) — the existing helper is word-sized only.
//!
//! Unlike `strnlen_user`, this C original has TWO distinct
//! `unsafe_get_user` fault targets, not one:
//!   - the word-at-a-time loop's `unsafe_get_user(c, ..., byte_at_a_time)`
//!     falls back to the byte-at-a-time loop on a fault (NOT an
//!     immediate -EFAULT) — a partial word may already be unreadable
//!     while trailing bytes of it are still reachable one at a time;
//!   - the byte-at-a-time loop's `unsafe_get_user(c, ..., efault)`
//!     returns -EFAULT directly.
//! Translated as two distinct `match ... { None => ... }` arms with
//! different consequences (`break` out of the word loop vs `return
//! -EFAULT`), never collapsed into one shared error path — same
//! shared-label/distinct-value discipline as rule 0020, applied here
//! to a fall-through-to-different-loop shape rather than a shared
//! return value.
//!
//! `has_zero`/`create_zero_mask`/`find_zero`/`fls64`/`zero_bytemask`
//! reuse the exact reimplementation already established in
//! `lib/string_rs.rs`/`lib/strnlen_user_rs.rs` (rule 0016 —
//! `arch/riscv/include/asm/word-at-a-time.h` header inlines,
//! `BITS_PER_LONG==64` arm). `zero_bytemask(mask)` is a plain `(mask)`
//! identity `#define` on riscv (`arch/riscv/include/asm/
//! word-at-a-time.h:48`), confirmed the same way `string_rs.rs`
//! confirmed it (`let bytemask = data; // zero_bytemask(mask) == mask
//! on riscv`) — reused verbatim, not reimplemented. `prep_zero_mask`
//! is likewise `return bits;` (no-op) on riscv and is folded away.
//!
//! `IS_UNALIGNED(src, dst)`: `CONFIG_HAVE_EFFICIENT_UNALIGNED_ACCESS`
//! is unset in this build's `.config` (confirmed absent, same finding
//! `string_rs.rs`'s module doc already documents for `sized_strscpy`),
//! so the live macro arm is the real alignment test
//! `((long)dst | (long)src) & (sizeof(long)-1)`, not the `0` stub —
//! translated as a real check below, not skipped.
//!
//! `can_do_masked_user_access()` is, as in `strnlen_user`, a
//! compile-time `0` on this arch (riscv never `#define`s
//! `masked_user_access_begin`) — the `if (can_do_masked_user_access())
//! { ... }` branch is genuinely unreachable for this build target and
//! is not translated, in the same spirit as rule 0026.
//!
//! Four pre-checks in the C original (`might_fault()`,
//! `should_fail_usercopy()`, `kasan_check_write()`,
//! `check_object_size()`) are all compile-time no-ops for this
//! build's `.config`, verified individually against their header
//! definitions:
//!   - `might_fault()`: `include/linux/kernel.h` — live only under
//!     `CONFIG_MMU && (CONFIG_PROVE_LOCKING || CONFIG_DEBUG_ATOMIC_SLEEP)`;
//!     both PROVE_LOCKING and DEBUG_ATOMIC_SLEEP are unset here, so it
//!     expands to `static inline void might_fault(void) { }`.
//!   - `should_fail_usercopy()`: `include/linux/fault-inject-usercopy.h`
//!     — live only under `CONFIG_FAULT_INJECTION_USERCOPY`, unset here,
//!     so it's `static inline bool should_fail_usercopy(void) { return
//!     false; }`.
//!   - `kasan_check_write()`: `include/linux/kasan-checks.h` — live
//!     only under `__SANITIZE_ADDRESS__` (KASAN instrumentation on this
//!     TU); this build has no KASAN config enabled, so it's `static
//!     inline bool kasan_check_write(...) { return true; }`, called as
//!     a statement with its result discarded either way.
//!   - `check_object_size()`: `include/linux/ucopysize.h` — live only
//!     under `CONFIG_HARDENED_USERCOPY`, unset here, so it's `static
//!     inline void check_object_size(...) { }`.
//! Each is a genuine no-op for this exact build, not a judgement call
//! generalised across configs — same "verify against the real header,
//! don't assume" bar the sibling TU set for `can_do_masked_user_access()`.
//! Not translated; their absence changes nothing observable for this
//! target.
//!
//! `strncpy_from_user` is a plain (non-`_GPL`) `EXPORT_SYMBOL` in the C
//! original; `#[export]` always emits `EXPORT_SYMBOL_GPL` — an
//! accepted, tracked deviation (rule 0001).

use kernel::bindings;
use kernel::ffi::{c_char, c_long, c_ulong};
use kernel::prelude::*;

/// `has_zero`/`create_zero_mask`/`find_zero`/`fls64` — identical
/// reimplementation to `lib/string_rs.rs`/`lib/strnlen_user_rs.rs`'s
/// (rule 0016, same header origin: `arch/riscv/include/asm/
/// word-at-a-time.h` + `include/asm-generic/bitops/fls64.h`).
#[inline]
fn has_zero(val: usize, one_bits: usize, high_bits: usize) -> usize {
    ((val.wrapping_sub(one_bits)) & !val) & high_bits
}
#[inline]
fn create_zero_mask(bits: usize) -> usize {
    let bits = bits.wrapping_sub(1) & !bits;
    bits >> 7
}
#[inline]
fn fls64(x: u64) -> i32 {
    if x == 0 {
        0
    } else {
        (63 - x.leading_zeros() as i32) + 1
    }
}
#[inline]
fn find_zero(mask: usize) -> usize {
    (fls64(mask as u64) >> 3) as usize
}

const ONE_BITS: usize = usize::MAX / 0xff;
const HIGH_BITS: usize = ONE_BITS * 0x80;

/// Read one `unsigned long` word from userspace, fault-safe.
///
/// Wraps `rust_helper_unsafe_get_user_ul` (already established by
/// `strnlen_user_rs.rs`) — see that file's module doc for the fault
/// contract. `None` is the "goto <fault-label>" outcome; this TU
/// dispatches it to two DIFFERENT consequences depending on call site
/// (see `do_strncpy_from_user` below), unlike `strnlen_user` where
/// both `unsafe_get_user` sites shared the same `efault` target.
///
/// # Safety
/// Must be called with a real user-memory-access region active, i.e.
/// between a successful `user_read_access_begin()` and its matching
/// `user_read_access_end()`, and `ptr` must lie within the range that
/// call validated.
#[inline]
unsafe fn unsafe_get_user_ul(ptr: *const c_ulong) -> Option<usize> {
    let mut val: c_ulong = 0;
    // SAFETY: per function contract.
    let ok = unsafe { bindings::unsafe_get_user_ul(&mut val, ptr) };
    ok.then_some(val as usize)
}

/// Read one byte from userspace, fault-safe — the byte-sized sibling
/// of `unsafe_get_user_ul`, needed for the C's `byte_at_a_time`
/// fallback loop. New shim: `rust_helper_unsafe_get_user_u8`
/// (`rust/helpers/uaccess.c`), same shape and contract as
/// `rust_helper_unsafe_get_user_ul` but wrapping `unsafe_get_user()`
/// for a `char` instead of an `unsigned long`.
///
/// # Safety
/// Same as `unsafe_get_user_ul`.
#[inline]
unsafe fn unsafe_get_user_u8(ptr: *const c_char) -> Option<u8> {
    let mut val: u8 = 0;
    // SAFETY: per function contract.
    let ok = unsafe { bindings::unsafe_get_user_u8(&mut val, ptr) };
    ok.then_some(val)
}

/// `IS_UNALIGNED(src, dst)` (`lib/strncpy_from_user.c`, this file's
/// own `#define`): `CONFIG_HAVE_EFFICIENT_UNALIGNED_ACCESS` is unset
/// in this build (see module doc), so the live definition is the real
/// alignment test, not the `0` stub.
#[inline]
fn is_unaligned(src: *const c_char, dst: *const c_char) -> bool {
    ((dst as isize) | (src as isize)) & (size_of::<usize>() as isize - 1) != 0
}

/// `do_strncpy_from_user()` (`__always_inline` in the C) — the
/// word-at-a-time core with byte-at-a-time fallback.
///
/// Returns the copied length (excluding the terminating NUL) on
/// success, `count` if `count` bytes were copied without finding a
/// NUL (the caller-supplied limit was reached — not an error), or
/// `-EFAULT` if the address-space limit (`max`) was hit before either
/// a NUL or `count` bytes — some data may already have been copied,
/// matching the C's own documented contract in `strncpy_from_user`'s
/// doc comment.
///
/// # Safety
/// `dst` has room for at least `max` bytes (mirrors the C: only bytes
/// actually written by the loops below, up to `max`, are touched).
/// Must run inside an active `user_read_access_begin()` region
/// covering `src` for at least `max` bytes.
#[inline]
unsafe fn do_strncpy_from_user(
    dst: *mut c_char,
    src: *const c_char,
    count: usize,
    max: usize,
) -> c_long {
    let mut res: usize = 0;
    let mut max = max;

    if !is_unaligned(src, dst) {
        'word_loop: while max >= size_of::<usize>() {
            // SAFETY: per function contract; `res` bytes into `src`,
            // still within the `max`-bytes region validated by the
            // caller (loop condition guarantees a full word is in
            // bounds).
            let c = match unsafe { unsafe_get_user_ul(src.byte_add(res).cast()) } {
                Some(c) => c,
                // Fall back to byte-at-a-time if we get a page fault
                // -- NOT an immediate -EFAULT: a partial word may
                // already be unreadable while trailing bytes of it
                // are still reachable one at a time. This is the
                // FIRST of the two distinct unsafe_get_user fault
                // targets in the C original (see module doc) --
                // breaks out of this loop into the byte_at_a_time
                // loop below, sharing `res`/`max` state, exactly as
                // the C's `goto byte_at_a_time` does.
                None => break 'word_loop,
            };

            // Note that we mask out the bytes following the NUL. This
            // is important to do because string oblivious code may
            // read past the NUL. For those routines, we don't want to
            // give them potentially random bytes after the NUL in
            // `src`. One example of such code is BPF map keys.
            let data = has_zero(c, ONE_BITS, HIGH_BITS);
            if data != 0 {
                let data = create_zero_mask(data);
                let mask = data; // zero_bytemask(mask) == mask on riscv
                // SAFETY: per function contract; `dst` has room for
                // `max` bytes and `res + size_of::<usize>() <= max`
                // here (loop condition).
                unsafe {
                    (dst.byte_add(res) as *mut usize).write_unaligned(c & mask);
                }
                return (res + find_zero(data)) as c_long;
            }

            // SAFETY: per function contract, same bounds as the
            // masked write above.
            unsafe {
                (dst.byte_add(res) as *mut usize).write_unaligned(c);
            }

            res += size_of::<usize>();
            max -= size_of::<usize>();
        }
    }

    // byte_at_a_time:
    while max != 0 {
        // SAFETY: per function contract; `res` bytes into `src`,
        // still within the `max`-bytes region validated by the caller
        // (loop condition: `max != 0` means at least one more byte is
        // in bounds).
        let c = match unsafe { unsafe_get_user_u8(src.byte_add(res)) } {
            Some(c) => c,
            // SECOND distinct unsafe_get_user fault target: returns
            // -EFAULT directly, reached only from this loop, never
            // from the word-loop's fault break above (module doc).
            None => return -(bindings::EFAULT as c_long),
        };
        // SAFETY: per function contract; `dst` has room for `max`
        // bytes and `res < max` here (loop condition).
        unsafe {
            *dst.byte_add(res) = c as c_char;
        }
        if c == 0 {
            return res as c_long;
        }
        res += 1;
        max -= 1;
    }

    // Uhhuh. We hit 'max'. But was that the user-specified maximum
    // too? If so, that's ok - we got as much as the user asked for.
    if res >= count {
        return res as c_long;
    }

    // Nope: we hit the address space limit, and we still had more
    // characters the caller would have wanted. That's an EFAULT.
    -(bindings::EFAULT as c_long)
}

/// strncpy_from_user: - Copy a NUL terminated string from userspace.
///
/// `dst`:   Destination address, in kernel space. This buffer must be
///          at least `count` bytes long.
/// `src`:   Source address, in user space.
/// `count`: Maximum number of bytes to copy, including the trailing
///          NUL.
///
/// Copies a NUL-terminated string from userspace to kernel space.
///
/// On success, returns the length of the string (not including the
/// trailing NUL).
///
/// If access to userspace fails, returns `-EFAULT` (some data may
/// have been copied).
///
/// If `count` is smaller than the length of the string, copies
/// `count` bytes and returns `count`.
#[export]
pub unsafe extern "C" fn strncpy_from_user(
    dst: *mut c_char,
    src: *const c_char,
    count: c_long,
) -> c_long {
    // might_fault()/should_fail_usercopy()/kasan_check_write()/
    // check_object_size() are all compile-time no-ops for this
    // build's .config (verified individually against their header
    // definitions -- see module doc) and are not translated.

    if count <= 0 {
        return 0;
    }
    let count = count as usize;

    // can_do_masked_user_access() is a compile-time `0` on riscv (see
    // module doc) -- the masked-access fast path is unreachable for
    // this build target and is not translated; the reachable arm
    // below is the C's `else`-equivalent continuation.

    // SAFETY: TASK_SIZE_MAX is a plain read of arch/task state, no
    // preconditions.
    let max_addr = unsafe { bindings::strnlen_user_task_size_max() } as usize;
    // SAFETY: matches the C's `untagged_addr(src)` -- a pure address
    // transform (pointer-tag stripping), no dereference.
    let src_addr = unsafe { bindings::untagged_addr_ul(src as usize) } as usize;

    if src_addr < max_addr {
        let mut max = max_addr - src_addr;
        // Truncate 'max' to the user-specified limit, so that we only
        // have one limit we need to check in the loop.
        if max > count {
            max = count;
        }

        // SAFETY: `src`/`max` are passed to access_ok() by
        // user_read_access_begin() itself; only proceeds into the
        // fault-safe read region if that check (and SUM-enable) both
        // succeed, matching the C's `if (user_read_access_begin(...))`.
        let begun = unsafe { bindings::user_read_access_begin(src.cast(), max as c_ulong) };
        if begun {
            // SAFETY: user_read_access_begin() above succeeded and
            // remains active until user_read_access_end() below,
            // satisfying do_strncpy_from_user's precondition; `dst`
            // has room for `count >= max` bytes per this function's
            // own contract (mirrors the C doc: "This buffer must be
            // at least @count bytes long").
            let retval = unsafe { do_strncpy_from_user(dst, src, count, max) };
            // SAFETY: matches the just-begun access region.
            unsafe { bindings::user_read_access_end() };
            return retval;
        }
    }
    -(bindings::EFAULT as c_long)
}
