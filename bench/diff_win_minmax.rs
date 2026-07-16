// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, win_minmax.
//! Faithful copy of lib/win_minmax_rs.rs's algorithm (kernel-crate
//! bindings/export stripped for host build) — same protocol as
//! diff_win_minmax.c, same shared LCG.

#[derive(Clone, Copy, PartialEq, Eq)]
struct MinmaxSample {
    t: u32,
    v: u32,
}

struct Minmax {
    s: [MinmaxSample; 3],
}

fn minmax_reset(m: &mut Minmax, t: u32, meas: u32) -> u32 {
    let val = MinmaxSample { t, v: meas };
    m.s[2] = val;
    m.s[1] = val;
    m.s[0] = val;
    m.s[0].v
}

fn minmax_subwin_update(m: &mut Minmax, win: u32, val: &MinmaxSample) -> u32 {
    let dt = val.t.wrapping_sub(m.s[0].t);

    if dt > win {
        m.s[0] = m.s[1];
        m.s[1] = m.s[2];
        m.s[2] = *val;
        if val.t.wrapping_sub(m.s[0].t) > win {
            m.s[0] = m.s[1];
            m.s[1] = m.s[2];
            m.s[2] = *val;
        }
    } else if m.s[1].t == m.s[0].t && dt > win / 4 {
        m.s[1] = *val;
        m.s[2] = *val;
    } else if m.s[2].t == m.s[1].t && dt > win / 2 {
        m.s[2] = *val;
    }
    m.s[0].v
}

fn minmax_running_max(m: &mut Minmax, win: u32, t: u32, meas: u32) -> u32 {
    let val = MinmaxSample { t, v: meas };

    if val.v >= m.s[0].v || val.t.wrapping_sub(m.s[2].t) > win {
        return minmax_reset(m, t, meas);
    }
    if val.v >= m.s[1].v {
        m.s[1] = val;
        m.s[2] = val;
    } else if val.v >= m.s[2].v {
        m.s[2] = val;
    }
    minmax_subwin_update(m, win, &val)
}

fn minmax_running_min(m: &mut Minmax, win: u32, t: u32, meas: u32) -> u32 {
    let val = MinmaxSample { t, v: meas };

    if val.v <= m.s[0].v || val.t.wrapping_sub(m.s[2].t) > win {
        return minmax_reset(m, t, meas);
    }
    if val.v <= m.s[1].v {
        m.s[1] = val;
        m.s[2] = val;
    } else if val.v <= m.s[2].v {
        m.s[2] = val;
    }
    minmax_subwin_update(m, win, &val)
}

// Identical LCG to diff_win_minmax.c / diff_base64.{c,rs}.
struct Lcg(u64);
impl Lcg {
    fn next(&mut self) -> u32 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (self.0 >> 32) as u32
    }
}

const NUM_SEQ: usize = 200;
const STEPS_PER_SEQ: usize = 60;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(12345);
    let mut rng = Lcg(seed);
    let zero = MinmaxSample { t: 0, v: 0 };

    for _ in 0..NUM_SEQ {
        let mut mx = Minmax { s: [zero; 3] };
        let mut mn = Minmax { s: [zero; 3] };
        let win = 10 + (rng.next() % 200);
        let mut t: u32 = 0;

        for _ in 0..STEPS_PER_SEQ {
            t = t.wrapping_add(1 + (rng.next() % 20));
            let meas = rng.next() % 1000;

            let rmax = minmax_running_max(&mut mx, win, t, meas);
            let rmin = minmax_running_min(&mut mn, win, t, meas);
            println!("max,{}\nmin,{}", rmax, rmin);
        }
    }
}
