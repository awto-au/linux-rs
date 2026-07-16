// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, argv_split +
//! memcat_p. Faithful copies of the algorithms in lib/argv_split_rs.rs /
//! lib/memcat_p_rs.rs (kernel-crate bindings/export/kmalloc stripped —
//! host Vec<u8> stands in for the kernel allocation, split logic
//! unchanged). Same protocol/LCG as diff_argv_split.c.

fn is_space(c: u8) -> bool {
    c == b' ' || c == b'\t' || c == b'\n' || c == 0x0b || c == 0x0c || c == b'\r'
}

fn count_argc(s: &[u8]) -> i32 {
    let mut count = 0i32;
    let mut was_space = true;
    for &c in s {
        if is_space(c) {
            was_space = true;
        } else if was_space {
            was_space = false;
            count += 1;
        }
    }
    count
}

/// Host stand-in for argv_split: same split algorithm as the kernel
/// translation, over an owned buffer instead of a kmalloc'd one.
fn argv_split_ref(str_: &[u8]) -> (i32, Vec<Vec<u8>>) {
    let argc = count_argc(str_);
    let mut argv_str = str_.to_vec();
    let mut argv: Vec<usize> = Vec::new(); // start offsets into argv_str
    let mut was_space = true;
    for i in 0..argv_str.len() {
        if is_space(argv_str[i]) {
            was_space = true;
            argv_str[i] = 0;
        } else if was_space {
            was_space = false;
            argv.push(i);
        }
    }
    // Split argv_str at each recorded start into the returned words.
    let words: Vec<Vec<u8>> = argv
        .iter()
        .map(|&start| {
            let end = argv_str[start..]
                .iter()
                .position(|&c| c == 0)
                .map(|p| start + p)
                .unwrap_or(argv_str.len());
            argv_str[start..end].to_vec()
        })
        .collect();
    (argc, words)
}

/// Host stand-in for __memcat_p: identical merge order (a then b then
/// NULL), matching the reverse-fill loop's actual behaviour.
fn memcat_p_ref(a: &[u64], b: &[u64]) -> Vec<u64> {
    let mut out = Vec::with_capacity(a.len() + b.len());
    out.extend_from_slice(a);
    out.extend_from_slice(b);
    out
}

// Identical LCG to diff_argv_split.c / the other diff_* files.
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

const MAXLEN: usize = 60;

fn rand_argstr(rng: &mut Lcg) -> Vec<u8> {
    let len = (rng.next() as usize) % MAXLEN;
    let mut buf = Vec::with_capacity(len);
    while buf.len() < len {
        if rng.next() % 3 == 0 {
            let run = 1 + (rng.next() % 3) as usize;
            for _ in 0..run {
                if buf.len() >= len {
                    break;
                }
                buf.push(if rng.next() % 2 == 1 { b' ' } else { b'\t' });
            }
        } else {
            let run = 1 + (rng.next() % 5) as usize;
            for _ in 0..run {
                if buf.len() >= len {
                    break;
                }
                buf.push(b'!' + (rng.next() % 90) as u8);
            }
        }
    }
    buf
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(3000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(12345);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let buf = rand_argstr(&mut rng);
        let (argc, words) = argv_split_ref(&buf);
        print!("split,{},", argc);
        for w in &words {
            print!("{}|", String::from_utf8_lossy(w));
        }
        println!();
    }

    for _ in 0..(n / 3) {
        let alen = (rng.next() % 6) as usize;
        let blen = (rng.next() % 6) as usize;
        let a: Vec<u64> = (0..alen).map(|_| 1 + (rng.next() % 1000) as u64).collect();
        let b: Vec<u64> = (0..blen).map(|_| 1 + (rng.next() % 1000) as u64).collect();

        let merged = memcat_p_ref(&a, &b);
        print!("memcat,{},{},", alen, blen);
        for v in &merged {
            print!("{},", v);
        }
        println!();
    }
}
