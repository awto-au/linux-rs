// SPDX-License-Identifier: GPL-2.0
//! Windowed min/max tracker — Rust translation of `lib/win_minmax.c`.
//!
//! Kathleen Nichols' algorithm: track best/2nd/3rd best over a moving time
//! window in constant space (see the C original's commentary).
//!
//! `minmax_reset` is a static inline in <linux/win_minmax.h> — invisible to
//! bindgen, so it is reimplemented here verbatim (its callers in OTHER C
//! files keep using the header copy; both compute the same struct writes).
//! Time deltas use wrapping arithmetic, as C unsigned subtraction does —
//! the algorithm depends on that for timestamp wraparound.

use kernel::bindings::{minmax, minmax_sample};
use kernel::prelude::*;

/// Header-inline `minmax_reset`, reimplemented (see module docs).
#[inline]
fn minmax_reset(m: &mut minmax, t: u32, meas: u32) -> u32 {
    let val = minmax_sample { t, v: meas };

    m.s[2] = val;
    m.s[1] = val;
    m.s[0] = val;
    m.s[0].v
}

/// As time advances, update the 1st, 2nd, and 3rd choices.
fn minmax_subwin_update(m: &mut minmax, win: u32, val: &minmax_sample) -> u32 {
    let dt = val.t.wrapping_sub(m.s[0].t);

    if dt > win {
        // Passed the entire window without a new val: shift choices down,
        // possibly twice (3rd choice was checked in-window on entry).
        m.s[0] = m.s[1];
        m.s[1] = m.s[2];
        m.s[2] = *val;
        if val.t.wrapping_sub(m.s[0].t) > win {
            m.s[0] = m.s[1];
            m.s[1] = m.s[2];
            m.s[2] = *val;
        }
    } else if m.s[1].t == m.s[0].t && dt > win / 4 {
        // A quarter of the window passed without a new val: take a 2nd
        // choice from the 2nd quarter of the window.
        m.s[1] = *val;
        m.s[2] = *val;
    } else if m.s[2].t == m.s[1].t && dt > win / 2 {
        // Half the window passed: take a 3rd choice from the last half.
        m.s[2] = *val;
    }
    m.s[0].v
}

/// Check if a new measurement updates the 1st, 2nd or 3rd choice max.
#[export]
pub unsafe extern "C" fn minmax_running_max(
    m: *mut minmax,
    win: u32,
    t: u32,
    meas: u32,
) -> u32 {
    let val = minmax_sample { t, v: meas };
    // SAFETY: caller passes a valid minmax state (C ABI contract).
    let m = unsafe { &mut *m };

    if val.v >= m.s[0].v /* found new max? */
        || val.t.wrapping_sub(m.s[2].t) > win
    /* nothing left in window? */
    {
        return minmax_reset(m, t, meas); // forget earlier samples
    }

    if val.v >= m.s[1].v {
        m.s[1] = val;
        m.s[2] = val;
    } else if val.v >= m.s[2].v {
        m.s[2] = val;
    }

    minmax_subwin_update(m, win, &val)
}

/// Check if a new measurement updates the 1st, 2nd or 3rd choice min.
#[export]
pub unsafe extern "C" fn minmax_running_min(
    m: *mut minmax,
    win: u32,
    t: u32,
    meas: u32,
) -> u32 {
    let val = minmax_sample { t, v: meas };
    // SAFETY: caller passes a valid minmax state (C ABI contract).
    let m = unsafe { &mut *m };

    if val.v <= m.s[0].v /* found new min? */
        || val.t.wrapping_sub(m.s[2].t) > win
    /* nothing left in window? */
    {
        return minmax_reset(m, t, meas); // forget earlier samples
    }

    if val.v <= m.s[1].v {
        m.s[1] = val;
        m.s[2] = val;
    } else if val.v <= m.s[2].v {
        m.s[2] = val;
    }

    minmax_subwin_update(m, win, &val)
}
