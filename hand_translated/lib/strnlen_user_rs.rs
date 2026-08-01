// SPDX-License-Identifier: GPL-2.0
//! Word-at-a-time `strnlen_user()` — Rust translation of
//! `lib/strnlen_user.c`.
//!
//! Measures the length (including the terminating NUL) of a
//! NUL-terminated string in userspace, without copying it, using the
//! same word-at-a-time scan as `sized_strscpy` (`lib/string_rs.rs`) but
//! reading through the fault-safe `unsafe_get_user()` machinery instead
//! of a plain kernel-memory load, since the source is a raw `__user`
//! pointer that may fault.
//!
//! `access_ok`/`untagged_addr`/`TASK_SIZE_MAX`/`user_read_access_begin`/
//! `unsafe_get_user` are all config- or CPU-feature-dependent macros —
//! riscv's `untagged_addr()` reads `current->mm` and probes
//! `RISCV_ISA_EXT_SUPM` at runtime (`CONFIG_RISCV_ISA_SUPM=y` in this
//! build's `.config`), and `TASK_SIZE` is `is_compat_task()`-conditional
//! under `CONFIG_COMPAT` (also `=y` here) — real per-task arch state,
//! not pure arithmetic. Shimmed in `rust/helpers/uaccess.c` (rule 0014)
//! rather than reimplemented, so this security-relevant logic stays
//! exactly as the real kernel compiles it.
//!
//! `unsafe_get_user(c, ptr, efault)`'s fault contract (verified against
//! `arch/riscv/include/asm/uaccess.h` + `asm-extable.h`): on a real page
//! fault during the `ld`/`lw`, the CPU traps into the kernel, which
//! consults the `EX_TYPE_UACCESS_ERR_ZERO` extable entry the macro's asm
//! emits, zeroes the destination and resumes at the compiled-in fixup —
//! i.e. `goto efault` is driven by real hardware fault delivery, not a
//! software pointer check. `rust_helper_unsafe_get_user_ul` wraps the
//! same macro unchanged and surfaces that outcome as `Some`/`None`.
//!
//! `has_zero`/`create_zero_mask`/`find_zero`/`fls64` reuse the exact
//! reimplementation already established in `lib/string_rs.rs` (rule
//! 0016 — plain header-inlines from
//! `arch/riscv/include/asm/word-at-a-time.h` / `fls64.h`,
//! `BITS_PER_LONG==64` arm). `prep_zero_mask` is literally `return
//! bits;` on riscv (no-op), folded away there and here.
//!
//! `can_do_masked_user_access()` (`include/linux/uaccess.h`) is a
//! preprocessor `#define ... 0` on this arch — riscv never `#define`s
//! `masked_user_access_begin` anywhere under `arch/riscv/` (verified by
//! grep across the whole arch tree), so `can_do_masked_user_access()`
//! expands to the literal constant `0`, not a runtime `static_branch`
//! key (contrast the GCD `efficient_ffs_key` precedent, which IS
//! runtime-toggled and was kept both-armed for that reason). The
//! `if (can_do_masked_user_access()) { ... masked_user_read_access_begin
//! ... }` branch in the C is therefore genuinely unreachable dead code
//! for this build target — its callees aren't even defined as symbols
//! or macros for riscv — and is skipped here rather than translated, in
//! the same spirit as rule 0026 (arch-override-dead-generic) applied to
//! an in-function branch rather than a whole guarded function.
//!
//! `strnlen_user` is a plain (non-`_GPL`) `EXPORT_SYMBOL` in the C
//! original; `#[export]` always emits `EXPORT_SYMBOL_GPL` — an accepted,
//! tracked deviation (rule 0001).

use kernel::bindings;
use kernel::ffi::{c_char, c_long, c_ulong};
use kernel::prelude::*;

/// `aligned_byte_mask(n)` (`include/linux/wordpart.h`) — little-endian
/// arm (riscv is LE): sets the low `n` bytes of a word to `1`s, so bytes
/// before the real string start (introduced by rounding the read
/// address down to word alignment) never look like a NUL. Rule 0016.
#[inline]
fn aligned_byte_mask(n: usize) -> usize {
    (1usize << (8 * n)).wrapping_sub(1)
}

/// `has_zero`/`create_zero_mask`/`find_zero`/`fls64` — identical
/// reimplementation to `lib/string_rs.rs`'s (rule 0016, same header
/// origin: `arch/riscv/include/asm/word-at-a-time.h` +
/// `include/asm-generic/bitops/fls64.h`).
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
/// Wraps `rust_helper_unsafe_get_user_ul`, itself a direct
/// `unsafe_get_user(x, ptr, efault)` shim (rule 0014) — see the module
/// doc for the fault contract. `None` is the "goto efault" outcome.
///
/// # Safety
/// Must be called with a real user-memory-access region active, i.e.
/// between a successful `user_read_access_begin()` and its matching
/// `user_read_access_end()`, and `ptr` must lie within the range that
/// call validated (mirrors the C macro's own precondition — it performs
/// no `access_ok()` of its own).
#[inline]
unsafe fn unsafe_get_user_ul(ptr: *const c_ulong) -> Option<usize> {
    let mut val: c_ulong = 0;
    // SAFETY: per function contract.
    let ok = unsafe { bindings::unsafe_get_user_ul(&mut val, ptr) };
    ok.then_some(val as usize)
}

/// `do_strnlen_user()` (`__always_inline` in the C) — the word-at-a-time
/// core. `src` has already been validated for `max` bytes by the caller
/// (`user_read_access_begin`); `count` is the user-supplied limit.
///
/// Returns `0` on a fault (or on hitting the address-space maximum),
/// or `count + 1` if the address-space-limited scan (`max`) was
/// exhausted before finding a NUL but the caller-supplied `count` was
/// also already reached — the caller checks the result against `>
/// count` to detect this "too long" marker. Otherwise returns the
/// string length including the terminating NUL. See the C's own
/// comment: the result can overshoot `count` when it lands inside an
/// aligned word, by design — checked by the caller, not here.
///
/// # Safety
/// Same as `unsafe_get_user_ul`: must run inside an active
/// `user_read_access_begin()` region covering `src` for at least `max`
/// bytes (after the alignment adjustment below).
#[inline]
unsafe fn do_strnlen_user(src: *const c_char, count: usize, max: usize) -> c_long {
    let align = (size_of::<usize>() - 1) & (src as usize);
    // SAFETY: rounding down within the same allocation the caller
    // validated; `max += align` below widens the scan budget by exactly
    // the number of bytes this rewinds, matching the C 1:1.
    let src = unsafe { src.byte_sub(align) };
    let max = max + align;
    let mut res: usize = 0;

    // SAFETY: per function contract; this is the first `unsafe_get_user`
    // call, mirroring the C's unconditional pre-loop read.
    let mut c = match unsafe { unsafe_get_user_ul(src.cast()) } {
        Some(c) => c,
        None => return 0, // goto efault
    };
    c |= aligned_byte_mask(align);

    let mut max = max;
    loop {
        let data = has_zero(c, ONE_BITS, HIGH_BITS);
        if data != 0 {
            let data = create_zero_mask(data);
            return (res + find_zero(data) + 1 - align) as c_long;
        }
        res += size_of::<usize>();
        // We already handled 'unsigned long' bytes. Did we do it all?
        if max <= size_of::<usize>() {
            break;
        }
        max -= size_of::<usize>();
        // SAFETY: per function contract; `src` advances by `res` bytes,
        // still within the `max`-bytes region validated by the caller
        // (the `max <= size_of::<usize>()` check above guarantees
        // another full word is in bounds before this read).
        c = match unsafe { unsafe_get_user_ul(src.byte_add(res).cast()) } {
            Some(c) => c,
            None => return 0, // goto efault — reached directly, NOT
                               // through the res-=align/res>=count check
                               // below (rule 0020: shared-label,
                               // distinct-value goto).
        };
    }
    res -= align;

    // Uhhuh. We hit 'max'. But was that the user-specified maximum too?
    // If so, return the marker for "too long".
    if res >= count {
        return (count + 1) as c_long;
    }

    // Nope: we hit the address space limit, and we still had more
    // characters the caller would have wanted. That's 0.
    0
}

/// strnlen_user() - Get the size of a user string INCLUDING final NUL.
///
/// Context: User context only. This function may sleep if pagefaults
/// are enabled.
///
/// Returns the size of the string INCLUDING the terminating NUL. If the
/// string is too long, returns a number larger than `count`; the caller
/// must check the return value against `> count`. On exception (or
/// invalid count), returns 0.
///
/// NOTE! You should basically never use this function. There is almost
/// never any valid case for using the length of a user space string,
/// since the string can be changed at any time by other threads. Use
/// `strncpy_from_user()` instead to get a stable copy of the string.
#[export]
pub unsafe extern "C" fn strnlen_user(s: *const c_char, count: c_long) -> c_long {
    if count <= 0 {
        return 0;
    }
    let count = count as usize;

    // can_do_masked_user_access() is a compile-time `0` on riscv (see
    // module doc) — the masked-access fast path is unreachable for this
    // build target and is not translated; the reachable arm below is
    // the C's `else`-equivalent continuation.

    // SAFETY: TASK_SIZE_MAX is a plain read of arch/task state, no
    // preconditions.
    let max_addr = unsafe { bindings::strnlen_user_task_size_max() } as usize;
    // SAFETY: matches the C's `untagged_addr(str)` — a pure address
    // transform (pointer-tag stripping), no dereference.
    let src_addr = unsafe { bindings::untagged_addr_ul(s as usize) } as usize;

    if src_addr < max_addr {
        let mut max = max_addr - src_addr;
        // Truncate 'max' to the user-specified limit, so that we only
        // have one limit we need to check in the loop.
        if max > count {
            max = count;
        }

        // SAFETY: `s`/`max` are passed to access_ok() by
        // user_read_access_begin() itself; only proceeds into the
        // fault-safe read region if that check (and SUM-enable) both
        // succeed, matching the C's `if (user_read_access_begin(...))`.
        let begun = unsafe { bindings::user_read_access_begin(s.cast(), max as c_ulong) };
        if begun {
            // SAFETY: user_read_access_begin() above succeeded and
            // remains active until user_read_access_end() below,
            // satisfying do_strnlen_user's precondition.
            let retval = unsafe { do_strnlen_user(s, count, max) };
            // SAFETY: matches the just-begun access region.
            unsafe { bindings::user_read_access_end() };
            return retval;
        }
    }
    0
}
