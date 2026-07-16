// SPDX-License-Identifier: GPL-2.0-only
//! Tier-2.5 differential oracle: Rust translation side, cmdline.
//! Faithful copy of lib/cmdline_rs.rs's algorithm (kernel bindings/export
//! stripped; kernel helper calls replaced by host equivalents matching
//! diff_cmdline.c's) — same protocol/LCG as diff_cmdline.c.

use std::os::raw::c_char;

// Faithful reimplementation of lib/kstrtox.c's _parse_integer_fixup_radix
// + _parse_integer_limit chain — identical to diff_cmdline.c's stand-in
// (NOT libc strtoull: no whitespace skip, auto-radix on base=0 exactly
// matching the kernel's 0/0x/decimal detection). The overflow status bit
// is discarded by the real simple_strntoull before use, so only the
// wrapping value matters here.
unsafe fn simple_strtoull(cp: *const c_char, endp: *mut *mut c_char, mut base: u32) -> u64 {
    unsafe {
        let mut p = cp as *const u8;
        if base == 0 {
            if *p == b'0' {
                let c1 = (*p.add(1) as char).to_ascii_lowercase();
                if c1 == 'x' && (*p.add(2) as char).is_ascii_hexdigit() {
                    base = 16;
                } else {
                    base = 8;
                }
            } else {
                base = 10;
            }
        }
        if base == 16 && *p == b'0' && (*p.add(1) as char).to_ascii_lowercase() == 'x' {
            p = p.add(2);
        }

        let mut res: u64 = 0;
        loop {
            let c = *p;
            let lc = (c as char).to_ascii_lowercase();
            let val: u32 = if c.is_ascii_digit() {
                (c - b'0') as u32
            } else if ('a'..='f').contains(&lc) {
                (lc as u8 - b'a' + 10) as u32
            } else {
                break;
            };
            if val >= base {
                break;
            }
            res = res.wrapping_mul(base as u64).wrapping_add(val as u64);
            p = p.add(1);
        }
        if !endp.is_null() {
            *endp = p as *mut c_char;
        }
        res
    }
}

unsafe fn simple_strtol(cp: *const c_char, endp: *mut *mut c_char, base: u32) -> i64 {
    unsafe {
        if *cp == b'-' as c_char {
            -(simple_strtoull(cp.add(1), endp, base) as i64)
        } else {
            simple_strtoull(cp, endp, base) as i64
        }
    }
}

unsafe fn is_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\n' | 0x0b | 0x0c | b'\r')
}

unsafe fn skip_spaces(mut s: *mut c_char) -> *mut c_char {
    unsafe {
        while is_space(*s as u8) {
            s = s.add(1);
        }
    }
    s
}

unsafe fn strlen(s: *const c_char) -> usize {
    let mut p = s;
    let mut n = 0usize;
    unsafe {
        while *p != 0 {
            n += 1;
            p = p.add(1);
        }
    }
    n
}

unsafe fn strncmp(a: *const c_char, b: *const c_char, n: usize) -> i32 {
    unsafe {
        for i in 0..n {
            let ca = *a.add(i) as u8;
            let cb = *b.add(i) as u8;
            if ca != cb {
                return ca as i32 - cb as i32;
            }
            if ca == 0 {
                break;
            }
        }
    }
    0
}

unsafe fn get_range(str_: *mut *mut c_char, mut pint: *mut i32, mut n: i32) -> i32 {
    unsafe {
        *str_ = (*str_).add(1);
        let upper_range = simple_strtol(*str_ as *const c_char, core::ptr::null_mut(), 0) as i32;
        let inc_counter = upper_range - *pint;

        let mut x = *pint;
        while n != 0 && x < upper_range {
            *pint = x;
            pint = pint.add(1);
            x += 1;
            n -= 1;
        }
        inc_counter
    }
}

unsafe fn get_option(str_: *mut *mut c_char, pint: *mut i32) -> i32 {
    unsafe {
        let mut cur = *str_;
        let value: i32;

        if cur.is_null() || *cur == 0 {
            return 0;
        }
        if *cur == b'-' as c_char {
            cur = cur.add(1);
            let u = simple_strtoull(cur as *const c_char, str_, 0);
            value = (u as i64).wrapping_neg() as i32;
        } else {
            let u = simple_strtoull(cur as *const c_char, str_, 0);
            value = u as i32;
        }
        if !pint.is_null() {
            *pint = value;
        }
        if cur == *str_ {
            return 0;
        }
        if **str_ == b',' as c_char {
            *str_ = (*str_).add(1);
            return 2;
        }
        if **str_ == b'-' as c_char {
            return 3;
        }
        1
    }
}

unsafe fn get_options(str_: *const c_char, nints: i32, ints: *mut i32) -> *mut c_char {
    let validate = nints == 0;
    let mut i: i32 = 1;
    let mut cur = str_ as *mut c_char;

    unsafe {
        while i < nints || validate {
            let pint = if validate { ints } else { ints.add(i as usize) };

            let res = get_option(&mut cur as *mut *mut c_char, pint);
            if res == 0 {
                break;
            }
            if res == 3 {
                let n = if validate { 0 } else { nints - i };
                let range_nums = get_range(&mut cur as *mut *mut c_char, pint, n);
                if range_nums < 0 {
                    break;
                }
                i += range_nums - 1;
            }
            i += 1;
            if res == 1 {
                break;
            }
        }
        *ints = i - 1;
    }
    cur
}

unsafe fn memparse(ptr: *const c_char, retptr: *mut *mut c_char) -> u64 {
    unsafe {
        let mut endptr: *mut c_char = core::ptr::null_mut();
        let mut ret = simple_strtoull(ptr, &mut endptr, 0);

        let suffix = *endptr as u8;
        let shifts: u32 = match suffix {
            b'E' | b'e' => 6,
            b'P' | b'p' => 5,
            b'T' | b't' => 4,
            b'G' | b'g' => 3,
            b'M' | b'm' => 2,
            b'K' | b'k' => 1,
            _ => {
                if !retptr.is_null() {
                    *retptr = endptr;
                }
                return ret;
            }
        };
        ret <<= 10 * shifts;
        endptr = endptr.add(1);

        if !retptr.is_null() {
            *retptr = endptr;
        }
        ret
    }
}

unsafe fn parse_option_str(mut str_: *const c_char, option: *const c_char) -> bool {
    unsafe {
        while *str_ != 0 {
            if strncmp(str_, option, strlen(option)) == 0 {
                str_ = str_.add(strlen(option));
                let c = *str_;
                if c == 0 || c == b',' as c_char {
                    return true;
                }
            }
            while *str_ != 0 && *str_ != b',' as c_char {
                str_ = str_.add(1);
            }
            if *str_ == b',' as c_char {
                str_ = str_.add(1);
            }
        }
    }
    false
}

unsafe fn next_arg(
    mut args: *mut c_char,
    param: *mut *mut c_char,
    val: *mut *mut c_char,
) -> *mut c_char {
    let mut equals: usize = 0;
    let mut in_quote = false;
    let mut quoted = false;
    let mut i: usize = 0;

    unsafe {
        if *args == b'"' as c_char {
            args = args.add(1);
            in_quote = true;
            quoted = true;
        }

        while *args.add(i) != 0 {
            let c = *args.add(i);
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

        *param = args;
        if equals == 0 {
            *val = core::ptr::null_mut();
        } else {
            *args.add(equals) = 0;
            let v = args.add(equals + 1);
            *val = v;
            if **val == b'"' as c_char {
                *val = v.add(1);
                if *args.add(i - 1) == b'"' as c_char {
                    *args.add(i - 1) = 0;
                }
            }
        }
        if quoted && i > 0 && *args.add(i - 1) == b'"' as c_char {
            *args.add(i - 1) = 0;
        }

        let tail = if *args.add(i) != 0 {
            *args.add(i) = 0;
            args.add(i + 1)
        } else {
            args.add(i)
        };

        skip_spaces(tail)
    }
}

// Identical LCG to diff_cmdline.c.
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

const POOL: &[u8] = b"0123456789-,= \"abcKMGTPEkmgtpe";
const MAXLEN: usize = 48;

fn print_hex(s: *const c_char) {
    if s.is_null() {
        print!("-1");
        return;
    }
    unsafe {
        let mut p = s as *const u8;
        while *p != 0 {
            print!("{:02x}", *p);
            p = p.add(1);
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(13371337);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let len = 1 + (rng.next() as usize % MAXLEN);
        let bytes: Vec<u8> = (0..len)
            .map(|_| POOL[rng.next() as usize % POOL.len()])
            .collect();
        // NUL-terminated buffer, CString can't hold interior content
        // safely here since we need raw byte control incl. later
        // in-place mutation; use a Vec<u8> with manual NUL terminator.
        let mut buf = bytes.clone();
        buf.push(0);

        unsafe {
            // get_option
            {
                let mut s = buf.as_mut_ptr() as *mut c_char;
                let mut dummy = 0i32;
                let rc = get_option(&mut s as *mut *mut c_char, &mut dummy);
                println!(
                    "option,{},{},{}",
                    rc,
                    dummy,
                    s as isize - buf.as_ptr() as isize
                );
            }
            // get_options (fill mode)
            {
                let mut b2 = buf.clone();
                let mut ints = [0i32; 18];
                let end = get_options(b2.as_mut_ptr() as *const c_char, 18, ints.as_mut_ptr());
                print!("options,{},", end as isize - b2.as_ptr() as isize);
                for v in ints {
                    print!("{},", v);
                }
                println!();
            }
            // get_options (validate mode)
            {
                let mut b2 = buf.clone();
                let mut ints = [0i32; 18];
                get_options(b2.as_mut_ptr() as *const c_char, 0, ints.as_mut_ptr());
                println!("options_validate,{}", ints[0]);
            }
            // memparse
            {
                let mut b2 = buf.clone();
                let mut end: *mut c_char = core::ptr::null_mut();
                let v = memparse(b2.as_mut_ptr() as *const c_char, &mut end);
                println!("memparse,{},{}", v, end as isize - b2.as_ptr() as isize);
            }
            // parse_option_str
            {
                let split = 1 + (rng.next() as usize % (if len > 1 { len - 1 } else { 1 }));
                let mut hay = bytes.clone();
                hay.push(0);
                let nlen = split.min(len);
                let mut needle = bytes[..nlen].to_vec();
                needle.push(0);
                let r = parse_option_str(
                    hay.as_ptr() as *const c_char,
                    needle.as_ptr() as *const c_char,
                );
                println!("optionstr,{}", r as i32);
            }
            // next_arg (mutates in place; private copy)
            {
                let mut copy = buf.clone();
                let mut param: *mut c_char = core::ptr::null_mut();
                let mut val: *mut c_char = core::ptr::null_mut();
                let rest = next_arg(copy.as_mut_ptr() as *mut c_char, &mut param, &mut val);
                print!("nextarg,");
                print_hex(param);
                print!(",{},", (!val.is_null()) as i32);
                print_hex(val);
                println!(",{}", rest as isize - copy.as_ptr() as isize);
            }
        }
    }
}
