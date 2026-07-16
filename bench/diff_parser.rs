// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, parser.
//! Faithful copy of lib/parser_rs.rs's match_wildcard/match_one/
//! match_token algorithms (kernel-crate bindings/export stripped,
//! libc string calls replaced with equivalent Rust) — same
//! protocol/LCG as diff_parser.c.

use std::ffi::{CStr, CString};
use std::os::raw::{c_char, c_int};

const MAX_OPT_ARGS: usize = 3;

#[derive(Clone, Copy)]
struct Substring {
    from: *mut c_char,
    to: *mut c_char,
}

struct MatchToken {
    token: c_int,
    pattern: *const c_char,
}

fn isdigit(c: c_char) -> bool {
    (b'0' as c_char..=b'9' as c_char).contains(&c)
}

unsafe fn strchr(mut p: *const c_char, ch: u8) -> *const c_char {
    unsafe {
        loop {
            if *p as u8 == ch {
                return p;
            }
            if *p == 0 {
                return std::ptr::null();
            }
            p = p.add(1);
        }
    }
}

unsafe fn strcmp(a: *const c_char, b: *const c_char) -> c_int {
    unsafe { libc_strcmp(a, b) }
}
unsafe fn libc_strcmp(a: *const c_char, b: *const c_char) -> c_int {
    unsafe {
        let sa = CStr::from_ptr(a).to_bytes();
        let sb = CStr::from_ptr(b).to_bytes();
        sa.cmp(sb) as c_int
    }
}
unsafe fn strncmp(a: *const c_char, b: *const c_char, n: usize) -> c_int {
    unsafe {
        for i in 0..n {
            let ca = *a.add(i);
            let cb = *b.add(i);
            if ca != cb {
                return ca as c_int - cb as c_int;
            }
            if ca == 0 {
                break;
            }
        }
        0
    }
}
unsafe fn strlen(p: *const c_char) -> usize {
    unsafe { CStr::from_ptr(p).to_bytes().len() }
}
unsafe fn strtoul(p: *const c_char, endp: &mut *mut c_char, base: u32) -> u64 {
    unsafe {
        let s = CStr::from_ptr(p).to_str().unwrap();
        let mut end = 0usize;
        while end < s.len() && s.as_bytes()[end].is_ascii_hexdigit() {
            end += 1;
        }
        let val = u64::from_str_radix(&s[..end], base).unwrap_or(0);
        *endp = p.add(end) as *mut c_char;
        val
    }
}
unsafe fn strtol(p: *const c_char, endp: &mut *mut c_char, base: u32) -> i64 {
    unsafe { strtoul(p, endp, base) as i64 }
}

unsafe fn match_one(mut s: *mut c_char, mut p: *const c_char, args: *mut Substring) -> c_int {
    let mut argc: usize = 0;

    if p.is_null() {
        return 1;
    }

    unsafe {
        loop {
            let mut len: isize = -1;
            let meta = strchr(p, b'%');
            if meta.is_null() {
                return (strcmp(p, s) == 0) as c_int;
            }

            let meta_minus_p = meta.offset_from(p);
            if strncmp(p, s, meta_minus_p as usize) != 0 {
                return 0;
            }

            s = s.offset(meta_minus_p);
            p = meta.add(1);

            if isdigit(*p) {
                let mut endp: *mut c_char = std::ptr::null_mut();
                len = strtoul(p, &mut endp, 10) as isize;
                p = endp;
            } else if *p == b'%' as c_char {
                let c = *s;
                s = s.add(1);
                if c != b'%' as c_char {
                    return 0;
                }
                p = p.add(1);
                continue;
            }

            if argc >= MAX_OPT_ARGS {
                return 0;
            }

            let arg = args.add(argc);
            (*arg).from = s;

            let spec = *p;
            p = p.add(1);
            match spec as u8 as char {
                's' => {
                    let str_len = strlen(s) as isize;
                    if str_len == 0 {
                        return 0;
                    }
                    if len == -1 || len > str_len {
                        len = str_len;
                    }
                    (*arg).to = s.offset(len);
                }
                'd' => {
                    let mut endp: *mut c_char = std::ptr::null_mut();
                    strtol(s, &mut endp, 10);
                    (*arg).to = endp;
                    if (*arg).to == (*arg).from {
                        return 0;
                    }
                }
                'u' => {
                    let mut endp: *mut c_char = std::ptr::null_mut();
                    strtoul(s, &mut endp, 10);
                    (*arg).to = endp;
                    if (*arg).to == (*arg).from {
                        return 0;
                    }
                }
                'o' => {
                    let mut endp: *mut c_char = std::ptr::null_mut();
                    strtoul(s, &mut endp, 8);
                    (*arg).to = endp;
                    if (*arg).to == (*arg).from {
                        return 0;
                    }
                }
                'x' => {
                    let mut endp: *mut c_char = std::ptr::null_mut();
                    strtoul(s, &mut endp, 16);
                    (*arg).to = endp;
                    if (*arg).to == (*arg).from {
                        return 0;
                    }
                }
                _ => return 0,
            }
            s = (*arg).to;
            argc += 1;
        }
    }
}

unsafe fn match_token(s: *mut c_char, table: *const MatchToken, args: *mut Substring) -> c_int {
    unsafe {
        let mut p = table;
        while match_one(s, (*p).pattern, args) == 0 {
            p = p.add(1);
        }
        (*p).token
    }
}

unsafe fn match_wildcard(pattern: *const c_char, str_: *const c_char) -> bool {
    let mut s = str_;
    let mut p = pattern;
    let mut star = false;
    let mut str_anchor = str_;
    let mut pattern_anchor = pattern;

    unsafe {
        while *s != 0 {
            match *p as u8 as char {
                '?' => {
                    s = s.add(1);
                    p = p.add(1);
                }
                '*' => {
                    star = true;
                    str_anchor = s;
                    p = p.add(1);
                    if *p == 0 {
                        return true;
                    }
                    pattern_anchor = p;
                }
                _ => {
                    if *s == *p {
                        s = s.add(1);
                        p = p.add(1);
                    } else {
                        if !star {
                            return false;
                        }
                        str_anchor = str_anchor.add(1);
                        s = str_anchor;
                        p = pattern_anchor;
                    }
                }
            }
        }

        while *p == b'*' as c_char {
            p = p.add(1);
        }
        *p == 0
    }
}

// Identical LCG to diff_parser.c.
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

const ALPHABET: &[u8] = b"ab?*";

fn gen_str(rng: &mut Lcg, maxlen: usize, with_wild: bool) -> CString {
    let len = (rng.next() as usize) % maxlen;
    let alpha: &[u8] = if with_wild { ALPHABET } else { b"ab" };
    let mut v = Vec::with_capacity(len);
    for _ in 0..len {
        v.push(alpha[(rng.next() as usize) % alpha.len()]);
    }
    CString::new(v).unwrap()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let pat = gen_str(&mut rng, 16, true);
        let s = gen_str(&mut rng, 16, false);
        let w = unsafe { match_wildcard(pat.as_ptr(), s.as_ptr()) };
        println!(
            "wildcard,{},{},{}",
            pat.to_str().unwrap(),
            s.to_str().unwrap(),
            w as i32
        );
    }

    let table_patterns = [
        CString::new("opt_a").unwrap(),
        CString::new("opt_b=%d").unwrap(),
        CString::new("opt_c=%s").unwrap(),
        CString::new("opt_d=%x").unwrap(),
        CString::new("opt_e=%u,%u").unwrap(),
    ];
    let table = [
        MatchToken { token: 1, pattern: table_patterns[0].as_ptr() },
        MatchToken { token: 2, pattern: table_patterns[1].as_ptr() },
        MatchToken { token: 3, pattern: table_patterns[2].as_ptr() },
        MatchToken { token: 4, pattern: table_patterns[3].as_ptr() },
        MatchToken { token: 5, pattern: table_patterns[4].as_ptr() },
        MatchToken { token: -1, pattern: std::ptr::null() },
    ];

    for _ in 0..n {
        let which = rng.next() % 6;
        let val = rng.next() % 1000;
        let uval = rng.next() % 1000;
        let uval2 = rng.next() % 1000;
        let buf = match which {
            0 => "opt_a".to_string(),
            1 => format!("opt_b={}", val as i32),
            2 => format!("opt_c=hello{}", val),
            3 => format!("opt_d={:x}", uval),
            4 => format!("opt_e={},{}", uval, uval2),
            _ => format!("opt_z_unknown{}", val),
        };
        let mut work = CString::new(buf.clone()).unwrap().into_bytes_with_nul();
        let mut sub_args = [Substring { from: std::ptr::null_mut(), to: std::ptr::null_mut() }; MAX_OPT_ARGS];
        let tok = unsafe {
            match_token(
                work.as_mut_ptr() as *mut c_char,
                table.as_ptr(),
                sub_args.as_mut_ptr(),
            )
        };
        print!("token,{},{}", buf, tok);
        for a in &sub_args {
            if !a.from.is_null() && !a.to.is_null() {
                let len = unsafe { a.to.offset_from(a.from) } as usize;
                let slice = unsafe { std::slice::from_raw_parts(a.from as *const u8, len) };
                print!(",[{}]", String::from_utf8_lossy(slice));
            } else {
                print!(",-");
            }
        }
        println!();
    }
}
