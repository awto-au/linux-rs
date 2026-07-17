// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, memcat_p.
//! Faithful copy of lib/memcat_p_rs.rs's algorithm (kernel-crate
//! bindings/export/kmalloc_array stripped — host Vec stands in for the
//! kernel allocation, merge/reverse-fill logic unchanged) — same
//! protocol/LCG as diff_memcat_p.c.

/// Faithful port of __memcat_p's counting + reverse-fill loop, over
/// index-based "pointers" (u64 values, 0 reserved as the NULL
/// terminator, matching the C's void* NULL-termination convention)
/// instead of raw pointers.
fn memcat_p(a: &[u64], b: &[u64]) -> Option<Vec<u64>> {
    // a and b are NUL-terminated (already, by construction of the caller
    // — trailing 0 present); find their lengths same as the C's counting
    // loops (nr_a elements before the 0, plus nr_b).
    let nr_a = a.iter().position(|&x| x == 0).unwrap();
    let nr_b = b.iter().position(|&x| x == 0).unwrap();
    let nr = nr_a + nr_b + 1; // +1 for the NULL terminator

    // kmalloc_array failure isn't modeled on the host (always succeeds);
    // matches every other bench/diff_*.rs's treatment of allocation.
    let mut new = vec![0u64; nr];

    // Reverse-fill: last slot is the NULL terminator (0), then walk
    // backwards through b (b[nr_b-1..0]), then through a (a[nr_a-1..0]),
    // exactly matching the C's `p = p == b ? &a[nr] : p - 1` pointer walk.
    new[nr - 1] = 0;
    for i in (0..nr_b).rev() {
        new[nr_a + i] = b[i];
    }
    for i in (0..nr_a).rev() {
        new[i] = a[i];
    }

    Some(new)
}

// Identical LCG to diff_memcat_p.c.
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

const MAXLEN: usize = 20;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let alen = (rng.next() as usize) % MAXLEN;
        let blen = (rng.next() as usize) % MAXLEN;

        let mut a = vec![0u64; alen + 1];
        let mut b = vec![0u64; blen + 1];
        for k in 0..alen {
            a[k] = 0x1000 + (rng.next() % 0xff0) as u64 + 1;
        }
        a[alen] = 0;
        for k in 0..blen {
            b[k] = 0x2000 + (rng.next() % 0xff0) as u64 + 1;
        }
        b[blen] = 0;

        let merged = memcat_p(&a, &b);

        print!("memcat,{},{},{},", alen, blen, merged.is_some() as i32);
        match merged {
            Some(m) => {
                let mut idx = 0usize;
                while m[idx] != 0 {
                    if idx != 0 {
                        print!("-");
                    }
                    print!("{:x}", m[idx]);
                    idx += 1;
                }
                println!(",{}", idx);
            }
            None => println!(",-1"),
        }
    }
}
