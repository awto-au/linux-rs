// SPDX-License-Identifier: GPL-2.0
//! `_copy_from_user()`/`_copy_to_user()`/`check_zeroed_user()` — Rust
//! translation of `lib/usercopy.c`.
//!
//! FOURTH file in the `__user`-pointer/`unsafe_get_user`/fault-handling
//! family, sibling of `lib/strnlen_user_rs.rs` (TU 34) and
//! `lib/strncpy_from_user_rs.rs` (TU 35). Reuses every uaccess shim
//! those two TUs established in `rust/helpers/uaccess.c` (rule 0014):
//! `rust_helper_user_read_access_begin`/`_end`,
//! `rust_helper_unsafe_get_user_ul`. No new shim is needed for
//! `check_zeroed_user()` — it reads only word-sized (`unsigned long`)
//! values, same as `strnlen_user`, not the byte-sized reads
//! `strncpy_from_user` added `rust_helper_unsafe_get_user_u8` for.
//!
//! ## `#if !defined(INLINE_COPY_USER)` scoping: in-scope, NOT dead code
//!
//! Checked before assuming anything: `INLINE_COPY_USER` is `#define`d
//! in exactly one place, `include/asm-generic/uaccess.h:94`, itself
//! gated `#ifdef CONFIG_UACCESS_MEMCPY`. riscv's Kconfig only
//! `select`s `UACCESS_MEMCPY` `if !MMU`
//! (`arch/riscv/Kconfig:229`) — this build has `CONFIG_MMU=y`, so
//! `CONFIG_UACCESS_MEMCPY` is unset and `INLINE_COPY_USER` is never
//! defined for this target (confirmed: `grep -rn "define
//! INLINE_COPY_USER" arch/riscv/` finds nothing; riscv provides its
//! own `raw_copy_from_user`/`raw_copy_to_user` directly in
//! `arch/riscv/include/asm/uaccess.h`, independent of the
//! `CONFIG_UACCESS_MEMCPY` generic-memcpy fallback). So
//! `#if !defined(INLINE_COPY_USER)` in `lib/usercopy.c` **is true**,
//! and `_copy_from_user`/`_copy_to_user` **are compiled** here — proven
//! directly against the shared tree's own build artifact, not just
//! preprocessor reasoning: `nm lib/usercopy.o` (from a prior build of
//! the unmodified C file) shows `T _copy_from_user` and `T
//! _copy_to_user` as real global text symbols calling
//! `__asm_copy_from_user`/`__asm_copy_to_user`, alongside `T
//! check_zeroed_user` — all three genuinely linked, none dead.
//!
//! ## `_copy_from_user`/`_copy_to_user`: thin FFI wrappers, narrow scope
//!
//! `_inline_copy_from_user()`/`_inline_copy_to_user()` are `static
//! inline` functions defined in `include/linux/uaccess.h` (not
//! `lib/usercopy.c`) — they are not separate linkable C symbols, so
//! there is nothing to bind to directly. Rather than re-deriving their
//! logic in Rust (`might_fault`/`should_fail_usercopy`/
//! `can_do_masked_user_access`/`access_ok`/`barrier_nospec`/
//! `raw_copy_from_user`/`instrument_copy_from_user_*` — a materially
//! different, larger translation target than this TU's scope), two
//! tiny `__rust_helper` C shims
//! (`rust_helper_inline_copy_from_user`/`_to_user` in
//! `rust/helpers/uaccess.c`) call the real header-inline functions
//! unchanged, and these two Rust functions are thin wrappers around
//! them — the same "leave it a real C symbol, declared not
//! reimplemented" scope discipline the 8250 TU applied to
//! `tty_get_char_size()`. The existing `rust_helper__copy_from_user`/
//! `_to_user` shims already in `rust/helpers/uaccess.c` are `#ifdef
//! INLINE_COPY_USER`-gated (the opposite, unused arm for this build)
//! and are left untouched.
//!
//! ## `check_zeroed_user()`: the real translation target
//!
//! Word-at-a-time zero-check over a userspace buffer, same fault-safe
//! `user_read_access_begin`/`unsafe_get_user`/`user_read_access_end`
//! shape as the two prior TUs, but with a `while` loop (not a `for`)
//! and a genuinely new control-flow shape neither prior TU has: an
//! `unlikely(val) goto done` EARLY-EXIT-NONZERO path that is NOT an
//! error — it shares the `done:` label with the loop's normal
//! fall-through-to-completion exit, and that shared label is reached
//! with `user_read_access_end()` still needing to run before the
//! ternary-like `(val == 0)` -> `0`/`1` result is computed. Three
//! distinct exit paths, matching the function's own doc comment
//! (`0`/`1`/`-EFAULT`) exactly:
//!   1. `size == 0` -> `1` (trivially all-zero), before any user access
//!      begins at all — no `user_read_access_end()` to pair, matching
//!      the C's unconditional early `return 1;`.
//!   2. `err_fault:` — a real `unsafe_get_user` page fault. Reached
//!      from either of the two `unsafe_get_user` call sites (pre-loop
//!      and in-loop) with `user_read_access_end()` called first, then
//!      `-EFAULT`. Both sites share this ONE fault target (unlike
//!      `strncpy_from_user`'s two DISTINCT targets, rule 0020) — safe
//!      to collapse into one Rust arm since no result-carrying state
//!      differs between the two `None` cases: both fall straight
//!      through to end-access + `-EFAULT` with no intervening use of
//!      `val`.
//!   3. `done:` — reached TWO ways: (a) `goto done` the instant a
//!      nonzero word is found mid-loop (`unlikely(val)`), leaving `val`
//!      at that nonzero value and skipping the rest of the scan
//!      entirely — a genuine early-exit-but-not-error, not folded into
//!      the fault path despite both being "leave the loop early"; and
//!      (b) natural loop exit (either `size <= sizeof(long)` from the
//!      start, or the `while` condition `size > sizeof(long)` finally
//!      failing), followed by the post-loop `if (size <
//!      sizeof(unsigned long)) val &= aligned_byte_mask(size);` trim —
//!      which must NOT run for path (a), since the C's `goto done`
//!      jumps straight past that `if`. Translated as a labeled Rust
//!      block (`'scan: { ... }`) so the early-exit `break 'scan` and
//!      the natural-fallthrough path both land after the block but
//!      the post-loop trim only executes on the fallthrough, matching
//!      the C's control flow exactly rather than approximating it with
//!      a flag variable.
//!
//! `aligned_byte_mask(n)` (`include/linux/wordpart.h`) reused verbatim
//! from `lib/strnlen_user_rs.rs` (rule 0016) — riscv is little-endian
//! (`CONFIG_CPU_BIG_ENDIAN` unset), so the live `#ifdef __LITTLE_ENDIAN`
//! arm is `((1UL << 8*(n))-1)`, the same formula already established.
//!
//! `check_zeroed_user`/`_copy_from_user`/`_copy_to_user` are all plain
//! (non-`_GPL`) `EXPORT_SYMBOL` in the C original; `#[export]` always
//! emits `EXPORT_SYMBOL_GPL` — an accepted, tracked deviation (rule
//! 0001).

use kernel::bindings;
use kernel::ffi::{c_int, c_ulong, c_void};
use kernel::prelude::*;

/// `aligned_byte_mask(n)` (`include/linux/wordpart.h`) — little-endian
/// arm (riscv is LE): sets the low `n` bytes of a word to `1`s.
/// Identical to `lib/strnlen_user_rs.rs`'s (rule 0016).
#[inline]
fn aligned_byte_mask(n: usize) -> usize {
    (1usize << (8 * n)).wrapping_sub(1)
}

/// Read one `unsigned long` word from userspace, fault-safe.
///
/// Wraps `rust_helper_unsafe_get_user_ul`, already established by
/// `strnlen_user_rs.rs`/`strncpy_from_user_rs.rs` — see either file's
/// module doc for the fault contract. `None` is the "goto err_fault"
/// outcome.
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

/// check_zeroed_user: check if a userspace buffer only contains zero
/// bytes.
///
/// `from`: Source address, in userspace.
/// `size`: Size of buffer.
///
/// This is effectively shorthand for `memchr_inv(from, 0, size) ==
/// NULL` for userspace addresses (and is more efficient because we
/// don't care where the first non-zero byte is).
///
/// Returns:
///  * 0: There were non-zero bytes present in the buffer.
///  * 1: The buffer was full of zero bytes.
///  * -EFAULT: access to userspace failed.
#[export]
pub unsafe extern "C" fn check_zeroed_user(from: *const c_void, size: usize) -> c_int {
    if size == 0 {
        // Trivially all-zero; no user access region entered at all,
        // matching the C's unconditional early `return 1;` before
        // `user_read_access_begin()`.
        return 1;
    }

    let align = (from as usize) % size_of::<usize>();
    // SAFETY: rounding down within the same allocation the caller
    // will validate below via user_read_access_begin(); `size += align`
    // widens the access-region request by exactly the number of bytes
    // this rewinds, matching the C 1:1.
    let from = unsafe { (from as *const u8).sub(align) };
    let size = size + align;

    // SAFETY: `from`/`size` are passed to access_ok() by
    // user_read_access_begin() itself; only proceeds into the
    // fault-safe read region if that check (and SUM-enable) both
    // succeed, matching the C's `if (!user_read_access_begin(from,
    // size)) return -EFAULT;`.
    let begun = unsafe { bindings::user_read_access_begin(from.cast(), size as c_ulong) };
    if !begun {
        return -(bindings::EFAULT as c_int);
    }

    // SAFETY: per function contract; this is the first
    // unsafe_get_user call, mirroring the C's unconditional pre-loop
    // read, inside the just-begun access region covering `size`
    // bytes from `from`.
    let mut val = match unsafe { unsafe_get_user_ul(from.cast()) } {
        Some(v) => v,
        None => {
            // err_fault: SAFETY: matches the just-begun access region.
            unsafe { bindings::user_read_access_end() };
            return -(bindings::EFAULT as c_int);
        }
    };
    if align != 0 {
        val &= !aligned_byte_mask(align);
    }

    let mut from = from;
    let mut size = size;

    // Labeled block so the mid-loop `goto done` (unlikely(val), a
    // genuine early-exit-not-error) and the natural loop-exit
    // fallthrough both reach the same post-block code, but ONLY the
    // fallthrough runs the post-loop `size < sizeof(long)` trim below
    // -- exactly mirroring the C's `goto done` jumping straight past
    // that `if`, not folding the two `done:`-reaching paths into one
    // Rust arm that always trims.
    'scan: {
        while size > size_of::<usize>() {
            if val != 0 {
                // goto done -- leaves `val` untouched (nonzero),
                // skips the rest of the scan AND the post-loop trim.
                break 'scan;
            }

            // SAFETY: still within the size_of::<usize>() region
            // rewound below by exactly the amount `from`/`size`
            // advance, remaining inside the caller-validated range
            // (loop condition guarantees a full word is in bounds).
            from = unsafe { from.add(size_of::<usize>()) };
            size -= size_of::<usize>();

            // SAFETY: per function contract; second and subsequent
            // unsafe_get_user call sites share the SAME err_fault
            // target as the pre-loop read above -- unlike
            // strncpy_from_user's two DISTINCT fault targets (rule
            // 0020), both `None` cases here fall straight through to
            // end-access + -EFAULT with no other state to carry, so
            // collapsing them into one match arm is faithful, not a
            // shortcut.
            val = match unsafe { unsafe_get_user_ul(from.cast()) } {
                Some(v) => v,
                None => {
                    // SAFETY: matches the just-begun access region.
                    unsafe { bindings::user_read_access_end() };
                    return -(bindings::EFAULT as c_int);
                }
            };
        }

        // Natural loop exit only (never reached via the `goto done`
        // break above): trim trailing bytes beyond `size` out of
        // `val` before the done: comparison, matching the C's
        // `if (size < sizeof(unsigned long)) val &=
        // aligned_byte_mask(size);` exactly.
        if size < size_of::<usize>() {
            val &= aligned_byte_mask(size);
        }
    }

    // done: SAFETY: matches the just-begun access region.
    unsafe { bindings::user_read_access_end() };
    (val == 0) as c_int
}

/// `_copy_from_user()` — out-of-line body used when `INLINE_COPY_USER`
/// is not defined (see module doc: true for this riscv64/MMU build).
/// Thin wrapper around the real `_inline_copy_from_user()` header
/// inline, called unchanged via a `rust_helper_inline_copy_from_user`
/// C shim rather than re-derived in Rust — see module doc for why
/// this is the faithful, narrow scope for this pair of functions.
#[export]
pub unsafe extern "C" fn _copy_from_user(
    to: *mut c_void,
    from: *const c_void,
    n: c_ulong,
) -> c_ulong {
    // SAFETY: forwards verbatim to the C `_inline_copy_from_user()`,
    // whose own preconditions (`to` valid for `n` bytes, `from` a
    // `__user` pointer) are identical to this function's — no
    // additional obligations introduced by the FFI hop.
    unsafe { bindings::inline_copy_from_user(to, from, n) }
}

/// `_copy_to_user()` — out-of-line body used when `INLINE_COPY_USER`
/// is not defined (see module doc). Thin wrapper around the real
/// `_inline_copy_to_user()` header inline, same discipline as
/// `_copy_from_user` above.
#[export]
pub unsafe extern "C" fn _copy_to_user(to: *mut c_void, from: *const c_void, n: c_ulong) -> c_ulong {
    // SAFETY: forwards verbatim to the C `_inline_copy_to_user()`; see
    // `_copy_from_user`'s SAFETY comment.
    unsafe { bindings::inline_copy_to_user(to, from, n) }
}
