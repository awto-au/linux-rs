// SPDX-License-Identifier: (GPL-2.0 OR MIT)
//! Tier-2.5 differential oracle: Rust translation side, glob. Faithful
//! copy of lib/glob_rs.rs's glob_match_str/glob_match_len algorithm
//! (kernel-crate bindings/no_mangle stripped) — same protocol/LCG as
//! diff_glob.c. See diff_glob.c's header comment for why this oracle
//! targets glob_match_len specifically (the real upstream KUnit suite,
//! lib/tests/glob_kunit.c, already covers glob_match's 64 cases on
//! real boot).

unsafe fn glob_match_str(pat: *const u8, str_: *const u8, str_end: Option<*const u8>) -> bool {
    let mut back_pat: Option<*const u8> = None;
    let mut back_str: Option<*const u8> = None;

    let mut pat = pat;
    let mut str_ = str_;

    loop {
        let at_end = str_end.is_some_and(|end| str_ >= end);
        let c: u8 = if at_end { 0 } else { unsafe { *str_ } };
        let d: u8 = unsafe { *pat };
        pat = unsafe { pat.add(1) };
        str_ = unsafe { str_.add(1) };

        match d {
            b'?' => {
                if c == 0 {
                    return false;
                }
            }
            b'*' => {
                if unsafe { *pat } == 0 {
                    return true;
                }
                back_pat = Some(pat);
                str_ = unsafe { str_.sub(1) };
                back_str = Some(str_);
            }
            b'[' => {
                if c == 0 {
                    return false;
                }
                let mut is_match = false;
                let inverted = unsafe { *pat } == b'!';
                let mut class = if inverted { unsafe { pat.add(1) } } else { pat };
                let mut a: u8 = unsafe { *class };
                class = unsafe { class.add(1) };

                let mut malformed = false;
                loop {
                    let mut b = a;

                    if a == 0 {
                        malformed = true;
                        break;
                    }

                    let class0 = unsafe { *class };
                    if class0 == b'-' {
                        let class1 = unsafe { *class.add(1) };
                        if class1 != b']' {
                            b = class1;
                            if b == 0 {
                                malformed = true;
                                break;
                            }
                            class = unsafe { class.add(2) };
                        }
                    }
                    if a <= c && c <= b {
                        is_match = true;
                    }

                    a = unsafe { *class };
                    class = unsafe { class.add(1) };
                    if a == b']' {
                        break;
                    }
                }

                if malformed {
                    if c == d {
                        if d == 0 {
                            return true;
                        }
                        continue;
                    }
                    if c == 0 || back_pat.is_none() {
                        return false;
                    }
                    pat = back_pat.unwrap();
                    str_ = unsafe { back_str.unwrap().add(1) };
                    back_str = Some(str_);
                    continue;
                }

                if is_match == inverted {
                    if c == 0 || back_pat.is_none() {
                        return false;
                    }
                    pat = back_pat.unwrap();
                    str_ = unsafe { back_str.unwrap().add(1) };
                    back_str = Some(str_);
                    continue;
                }
                pat = class;
            }
            b'\\' => {
                let d = unsafe { *pat };
                pat = unsafe { pat.add(1) };
                if c == d {
                    if d == 0 {
                        return true;
                    }
                    continue;
                }
                if c == 0 || back_pat.is_none() {
                    return false;
                }
                pat = back_pat.unwrap();
                str_ = unsafe { back_str.unwrap().add(1) };
                back_str = Some(str_);
            }
            _ => {
                if c == d {
                    if d == 0 {
                        return true;
                    }
                    continue;
                }
                if c == 0 || back_pat.is_none() {
                    return false;
                }
                pat = back_pat.unwrap();
                str_ = unsafe { back_str.unwrap().add(1) };
                back_str = Some(str_);
            }
        }
    }
}

fn glob_match_len(pat: &[u8], str_: &[u8], len: usize) -> bool {
    unsafe {
        let str_ptr = str_.as_ptr();
        glob_match_str(pat.as_ptr(), str_ptr, Some(str_ptr.add(len)))
    }
}

// Identical LCG to diff_glob.c.
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

const ALPHABET: &[u8] = b"ab[]!*?\\-c";

fn gen_buf(rng: &mut Lcg, max_len: usize, buf: &mut [u8; 17]) -> usize {
    let n = (rng.next() as usize) % (max_len + 1);
    for i in 0..n {
        buf[i] = ALPHABET[(rng.next() as usize) % ALPHABET.len()];
    }
    if n > 2 && rng.next() % 4 == 0 {
        buf[n / 2] = 0;
    }
    n
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let mut pat_buf = [0u8; 17];
        let pat_len = gen_buf(&mut rng, 16, &mut pat_buf);
        pat_buf[pat_len] = 0; // pat is always the real NUL-terminated contract

        let mut str_buf = [0u8; 17];
        let str_len = gen_buf(&mut rng, 16, &mut str_buf);

        let len = if rng.next() % 2 == 0 {
            str_len
        } else {
            (rng.next() as usize) % (str_len + 1)
        };

        let r = glob_match_len(&pat_buf[..=pat_len], &str_buf, len);
        println!("glob_match_len,{},{},{}", pat_len, len, r as i32);
    }
}
