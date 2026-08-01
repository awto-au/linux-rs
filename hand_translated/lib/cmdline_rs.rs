// SPDX-License-Identifier: GPL-2.0-only
//! Kernel command line / module option parsing — Rust translation of
//! `lib/cmdline.c`.
//!
//! Code and copyrights come from init/main.c and arch/i386/kernel/setup.c
//! (per the C original's header comment).

use kernel::bindings;
use kernel::ffi::{c_char, c_int};
use kernel::prelude::*;

/// C: `isspace(c)` (`<linux/ctype.h>`, function-like macro reading the
/// real `_ctype[]` table) — called as a real cross-TU data symbol
/// (`lib/ctype.c` is not yet translated) rather than reimplementing the
/// 256-entry table by hand (rule 0021, applied to data instead of a fn).
/// TODO_LINUX_RS: track unresolved cross-TU dependency on `_ctype` from
/// `lib/ctype.c` until that TU is translated or a permanent policy is
/// documented for this symbol.
#[inline]
fn is_space(c: u8) -> bool {
    const CT_SPACE: u8 = 0x20; // `_S` in <linux/ctype.h>
    // SAFETY: `_ctype` is a 256-entry array indexed by every possible
    // `unsigned char` value, `c` is a `u8` so always in range.
    (unsafe { *bindings::_ctype.as_ptr().add(c as usize) } & CT_SPACE) != 0
}

/// If a hyphen was found in get_option, this will handle the range of
/// numbers, M-N. This will expand the range and insert the values
/// [M, M+1, ..., N] into the ints array in get_options.
///
/// SAFETY: `str` points to a valid `*mut *mut c_char`; `pint` points to
/// at least `n` valid `c_int` slots when `n > 0` (get_options' contract).
unsafe fn get_range(str_: *mut *mut c_char, mut pint: *mut c_int, mut n: c_int) -> c_int {
    // C: (*str)++;
    // SAFETY: caller contract — `*str` points into a NUL-terminated string,
    // advancing past the hyphen just matched by the caller.
    unsafe { *str_ = (*str_).add(1) };

    // SAFETY: `*str_` is a valid C string (caller contract).
    let upper_range = unsafe { bindings::simple_strtol(*str_ as *const c_char, core::ptr::null_mut(), 0) } as c_int;
    // SAFETY: `pint` valid (caller contract).
    let inc_counter = upper_range - unsafe { *pint };

    // C: `for (x = *pint; n && x < upper_range; x++, n--) *pint++ = x;`
    // — writes consecutive ints starting at `pint`, advancing the local
    // pointer each write (not indexing); preserved verbatim, including
    // reading the initial `*pint` after `str` has already been advanced
    // but before any write.
    // SAFETY: `pint` valid (caller contract).
    let mut x = unsafe { *pint };
    while n != 0 && x < upper_range {
        // SAFETY: caller guarantees `n` valid slots from the original
        // `pint`; this is the (i)'th write for i < original n.
        unsafe {
            *pint = x;
            pint = pint.add(1);
        }
        x += 1;
        n -= 1;
    }

    inc_counter
}

/// Parse integer from an option string.
///
/// Read an int from an option string; if available accept a subsequent
/// comma as well. When `pint` is NULL the function can be used as a
/// validator of the current option in the string.
///
/// Return values:
/// - 0 - no int in string
/// - 1 - int found, no subsequent comma
/// - 2 - int found including a subsequent comma
/// - 3 - hyphen found to denote a range
///
/// Leading hyphen without integer is no integer case, but we consume it
/// for the sake of simplification.
///
/// SAFETY: `str` points to a valid `*mut c_char` (or the pointee may be
/// NULL); `pint`, if non-NULL, points to a valid `c_int`.
#[export]
pub unsafe extern "C" fn get_option(str_: *mut *mut c_char, pint: *mut c_int) -> c_int {
    // SAFETY: caller contract.
    let mut cur = unsafe { *str_ };
    let value: c_int;

    if cur.is_null() || unsafe { *cur } == 0 {
        return 0;
    }
    if unsafe { *cur } == b'-' as c_char {
        cur = unsafe { cur.add(1) };
        // C: value = -simple_strtoull(++cur, str, 0) — unsigned negation
        // (rule 0004) then truncating conversion to `int` (every real C
        // compiler's behaviour; both preserved with explicit casts).
        let u = unsafe { bindings::simple_strtoull(cur as *const c_char, str_, 0) };
        value = u.wrapping_neg() as c_int;
    } else {
        let u = unsafe { bindings::simple_strtoull(cur as *const c_char, str_, 0) };
        value = u as c_int;
    }
    if !pint.is_null() {
        unsafe { *pint = value };
    }
    // SAFETY: caller contract.
    if cur == unsafe { *str_ } {
        return 0;
    }
    // SAFETY: caller contract.
    if unsafe { **str_ } == b',' as c_char {
        // C: (*str)++;
        unsafe { *str_ = (*str_).add(1) };
        return 2;
    }
    if unsafe { **str_ } == b'-' as c_char {
        return 3;
    }

    1
}

/// Parse a string into a list of integers.
///
/// This function parses a string containing a comma-separated list of
/// integers, a hyphen-separated range of positive integers, or a
/// combination of both. The parse halts when the array is full, or when
/// no more numbers can be retrieved from the string.
///
/// When `nints` is 0, the function just validates the given `str` and
/// returns the amount of parseable integers as described below.
///
/// The first element is filled by the number of collected integers in
/// the range. The rest is what was parsed from `str`.
///
/// Returns the character in the string which caused the parse to end
/// (typically a null terminator, if `str` is completely parseable).
///
/// SAFETY: `str` is a valid NUL-terminated C string; `ints` points to at
/// least `max(1, nints)` valid `c_int` slots.
#[export]
pub unsafe extern "C" fn get_options(str_: *const c_char, nints: c_int, ints: *mut c_int) -> *mut c_char {
    let validate = nints == 0;
    let mut i: c_int = 1;
    let mut cur = str_ as *mut c_char;

    while i < nints || validate {
        // SAFETY: `ints` has room for at least `nints` slots (or slot 0
        // when validating); `i` stays < nints on the non-validate path.
        let pint = if validate {
            ints
        } else {
            unsafe { ints.add(i as usize) }
        };

        let res = unsafe { get_option(&mut cur as *mut *mut c_char, pint) };
        if res == 0 {
            break;
        }
        if res == 3 {
            let n = if validate { 0 } else { nints - i };
            // SAFETY: `cur`/`pint` valid per the same contract as above;
            // `n` matches the remaining slots from `pint` when writing.
            let range_nums = unsafe { get_range(&mut cur as *mut *mut c_char, pint, n) };
            if range_nums < 0 {
                break;
            }
            // Decrement the result by one to leave out the last number in
            // the range. The next iteration will handle the upper number
            // in the range.
            i += range_nums - 1;
        }
        i += 1;
        if res == 1 {
            break;
        }
    }
    // SAFETY: `ints` has at least 1 slot.
    unsafe { *ints = i - 1 };
    cur
}

/// Parse a string with mem suffixes into a number.
///
/// Parses a string into a number. The number stored at `ptr` is
/// potentially suffixed with K, M, G, T, P, E.
///
/// SAFETY: `ptr` is a valid NUL-terminated C string; `retptr`, if
/// non-NULL, points to a valid `*mut c_char`.
#[export]
pub unsafe extern "C" fn memparse(ptr: *const c_char, retptr: *mut *mut c_char) -> u64 {
    let mut endptr: *mut c_char = core::ptr::null_mut();

    // SAFETY: caller contract.
    let ret = unsafe { bindings::simple_strtoull(ptr, &mut endptr, 0) };

    // SAFETY: `endptr` was just set by simple_strtoull to point within
    // the string (or at its NUL terminator).
    let suffix = unsafe { *endptr } as u8;
    // C: a fallthrough switch — EVERY matched tier (E down to K) falls
    // through the REST of the chain, so `shl` accumulates 10 once per
    // tier from the matched one down to K (E: 60, ..., K: 10). Unlike
    // the pre-fix C, `endptr++`/suffix-consumption is now GATED on
    // `shl != 0 && ptr != endptr` (i.e. only when a suffix matched AND
    // there was a preceding numeric prefix) — a bare suffix like "k" or
    // "E" with no leading digits must NOT be consumed, and must report
    // the suffix char itself via `retptr` (upstream commit
    // 9a4580db6e9f, "lib: fix memparse() to handle overflow").
    let shl: u32 = match suffix {
        b'E' | b'e' => 60,
        b'P' | b'p' => 50,
        b'T' | b't' => 40,
        b'G' | b'g' => 30,
        b'M' | b'm' => 20,
        b'K' | b'k' => 10,
        _ => 0,
    };

    let ret = if shl != 0 && ptr != endptr.cast_const() {
        // C: `check_shl_overflow(ret, shl, &ret)` — bits lost off the
        // top of a u64 left shift means overflow; saturate to
        // ULLONG_MAX (u64::MAX) rather than wrapping, matching the
        // fixed C semantics (previously we always wrapped).
        let shifted = ret.wrapping_shl(shl);
        let overflowed = (shifted >> shl) != ret;
        // SAFETY: `endptr` points at the suffix char itself (not NUL,
        // since a suffix was matched), so `+1` stays in-bounds of the
        // NUL-terminated string.
        endptr = unsafe { endptr.add(1) };
        if overflowed {
            u64::MAX
        } else {
            shifted
        }
    } else {
        ret
    };

    if !retptr.is_null() {
        unsafe { *retptr = endptr };
    }

    ret
}

/// Parse a string and check whether an option is set.
///
/// This function parses a string containing a comma-separated list of
/// strings like a=b,c. Returns true if there's such option in the
/// string, false otherwise.
///
/// SAFETY: `str`, `option` are valid NUL-terminated C strings.
#[export]
pub unsafe extern "C" fn parse_option_str(mut str_: *const c_char, option: *const c_char) -> bool {
    while unsafe { *str_ } != 0 {
        // C calls `strlen(option)` twice per iteration here (once inside
        // strncmp's argument, once for the `str +=`) rather than hoisting
        // it — kept as two calls: "optimisation is the compiler's job"
        // (README discipline), not the translator's.
        // SAFETY: caller contract; strncmp reads at most strlen(option)
        // bytes from each, `str_` is NUL-terminated so this cannot overrun.
        if unsafe { bindings::strncmp(str_, option, bindings::strlen(option)) } == 0 {
            str_ = unsafe { str_.add(bindings::strlen(option) as usize) };
            let c = unsafe { *str_ };
            if c == 0 || c == b',' as c_char {
                return true;
            }
        }

        while unsafe { *str_ } != 0 && unsafe { *str_ } != b',' as c_char {
            str_ = unsafe { str_.add(1) };
        }

        if unsafe { *str_ } == b',' as c_char {
            str_ = unsafe { str_.add(1) };
        }
    }

    false
}

/// Parse a string to get a param value pair.
///
/// You can use `"` around spaces, but can't escape `"`. Hyphens and
/// underscores are equivalent in parameter names.
///
/// SAFETY: `args` is a valid, WRITABLE, NUL-terminated C string (mutated
/// in place, same as the C original); `param`, `val` are valid output
/// pointers.
#[export]
pub unsafe extern "C" fn next_arg(
    mut args: *mut c_char,
    param: *mut *mut c_char,
    val: *mut *mut c_char,
) -> *mut c_char {
    // C: `unsigned int i, equals = 0;` — `equals == 0` means "not found
    // yet" (so a literal '=' at position 0 is indistinguishable from "no
    // equals" — a real quirk of the C, preserved verbatim, not fixed).
    let mut equals: usize = 0;
    let mut in_quote = false;
    let mut quoted = false;
    let mut i: usize = 0;

    // SAFETY: caller contract.
    if unsafe { *args } == b'"' as c_char {
        args = unsafe { args.add(1) };
        in_quote = true;
        quoted = true;
    }

    // SAFETY: `args` is NUL-terminated; the loop stops at the NUL, same
    // bound as the C `for (i = 0; args[i]; i++)`.
    while unsafe { *args.add(i) } != 0 {
        let c = unsafe { *args.add(i) };
        if is_space(c as u8) && !in_quote {
            break;
        }
        if equals == 0 && c == b'=' as c_char {
            equals = i;
        }
        if c == b'"' as c_char {
            in_quote = !in_quote;
        }
        i += 1;
    }

    // SAFETY: `param` valid (caller contract).
    unsafe { *param = args };
    if equals == 0 {
        // SAFETY: `val` valid (caller contract).
        unsafe { *val = core::ptr::null_mut() };
    } else {
        // SAFETY: `equals < i`, in-bounds write of the NUL we just found
        // the string ends at-or-after `i`.
        unsafe { *args.add(equals) = 0 };
        // SAFETY: in-bounds per the same reasoning.
        let v = unsafe { args.add(equals + 1) };
        unsafe { *val = v };

        // Don't include quotes in value.
        // SAFETY: `*val` points within the string (just computed).
        if unsafe { **val } == b'"' as c_char {
            unsafe { *val = v.add(1) };
            // SAFETY: `i > 0` here since `equals < i` implies `i >= 1`.
            if unsafe { *args.add(i - 1) } == b'"' as c_char {
                unsafe { *args.add(i - 1) = 0 };
            }
        }
    }
    // SAFETY: caller contract on `args`/`i` bound as above.
    if quoted && i > 0 && unsafe { *args.add(i - 1) } == b'"' as c_char {
        unsafe { *args.add(i - 1) = 0 };
    }

    // SAFETY: `i` is within the string's bound (loop invariant above).
    let tail = if unsafe { *args.add(i) } != 0 {
        unsafe { *args.add(i) = 0 };
        unsafe { args.add(i + 1) }
    } else {
        unsafe { args.add(i) }
    };

    // Chew up trailing spaces.
    // SAFETY: `tail` points into the (now possibly NUL-split) string.
    unsafe { bindings::skip_spaces(tail) }
}
