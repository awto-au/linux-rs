// SPDX-License-Identifier: GPL-2.0
//! BCD conversion — Rust translation of `lib/bcd.c`.

use kernel::ffi::c_uint;
use kernel::prelude::*;

/// Convert a binary-coded-decimal byte to binary.
#[export]
pub unsafe extern "C" fn _bcd2bin(val: u8) -> c_uint {
    ((val & 0x0f) as c_uint) + ((val >> 4) as c_uint) * 10
}

/// Convert a binary value (< 100) to binary-coded decimal.
#[export]
pub unsafe extern "C" fn _bin2bcd(val: c_uint) -> u8 {
    let t = val.wrapping_mul(103) >> 10;

    ((t << 4) | (val - t * 10)) as u8
}
