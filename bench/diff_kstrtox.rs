// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, kstrtox.
//! Faithful copy of lib/kstrtox_rs.rs's algorithms (kernel-crate
//! bindings/export stripped) — same protocol/LCG as diff_kstrtox.c.

const KSTRTOX_OVERFLOW: u32 = 1u32 << 31;
const EINVAL: i32 = 22;
const ERANGE: i32 = 34;

fn kstrtox_tolower(c: u8) -> u8 {
    c | 0x20
}

fn isxdigit(c: u8) -> bool {
    c.is_ascii_hexdigit()
}

fn parse_integer_fixup_radix(s: &[u8], base: &mut u32) -> usize {
    let mut off = 0;
    if *base == 0 {
        if s[0] == b'0' {
            if kstrtox_tolower(s[1]) == b'x' && isxdigit(s[2]) {
                *base = 16;
            } else {
                *base = 8;
            }
        } else {
            *base = 10;
        }
    }
    if *base == 16 && s[0] == b'0' && kstrtox_tolower(s[1]) == b'x' {
        off = 2;
    }
    off
}

fn parse_integer_limit(s: &[u8], base: u32, max_chars: usize) -> (u32, u64) {
    let mut res: u64 = 0;
    let mut rv: u32 = 0;
    let mut i = 0;
    let mut max_chars = max_chars;
    while max_chars > 0 {
        max_chars -= 1;
        let c = s[i];
        let lc = kstrtox_tolower(c);
        let val: u32 = if c.is_ascii_digit() {
            (c - b'0') as u32
        } else if (b'a'..=b'f').contains(&lc) {
            (lc - b'a' + 10) as u32
        } else {
            break;
        };
        if val >= base {
            break;
        }
        if res & (!0u64 << 60) != 0 && res > (u64::MAX - val as u64) / base as u64 {
            rv |= KSTRTOX_OVERFLOW;
        }
        res = res.wrapping_mul(base as u64).wrapping_add(val as u64);
        rv += 1;
        i += 1;
    }
    (rv, res)
}

fn parse_integer(s: &[u8], base: u32) -> (u32, u64) {
    parse_integer_limit(s, base, i32::MAX as usize)
}

fn _kstrtoull(s: &[u8], base: u32) -> Result<u64, i32> {
    let mut base = base;
    let off = parse_integer_fixup_radix(s, &mut base);
    let s = &s[off..];
    let (rv, res) = parse_integer(s, base);
    if rv & KSTRTOX_OVERFLOW != 0 {
        return Err(-ERANGE);
    }
    if rv == 0 {
        return Err(-EINVAL);
    }
    let mut idx = rv as usize;
    if s[idx] == b'\n' {
        idx += 1;
    }
    if s[idx] != 0 {
        return Err(-EINVAL);
    }
    Ok(res)
}

fn kstrtoull(s: &[u8], base: u32) -> Result<u64, i32> {
    let s = if s[0] == b'+' { &s[1..] } else { s };
    _kstrtoull(s, base)
}

fn kstrtoll(s: &[u8], base: u32) -> Result<i64, i32> {
    if s[0] == b'-' {
        let tmp = _kstrtoull(&s[1..], base)?;
        let neg = (tmp as i64).wrapping_neg();
        if neg > 0 {
            return Err(-ERANGE);
        }
        Ok(neg)
    } else {
        let tmp = kstrtoull(s, base)?;
        if (tmp as i64) < 0 {
            return Err(-ERANGE);
        }
        Ok(tmp as i64)
    }
}

fn kstrtouint(s: &[u8], base: u32) -> Result<u32, i32> {
    let tmp = kstrtoull(s, base)?;
    if tmp != tmp as u32 as u64 {
        return Err(-ERANGE);
    }
    Ok(tmp as u32)
}

fn kstrtoint(s: &[u8], base: u32) -> Result<i32, i32> {
    let tmp = kstrtoll(s, base)?;
    if tmp != tmp as i32 as i64 {
        return Err(-ERANGE);
    }
    Ok(tmp as i32)
}

fn kstrtou16(s: &[u8], base: u32) -> Result<u16, i32> {
    let tmp = kstrtoull(s, base)?;
    if tmp != tmp as u16 as u64 {
        return Err(-ERANGE);
    }
    Ok(tmp as u16)
}

fn kstrtos16(s: &[u8], base: u32) -> Result<i16, i32> {
    let tmp = kstrtoll(s, base)?;
    if tmp != tmp as i16 as i64 {
        return Err(-ERANGE);
    }
    Ok(tmp as i16)
}

fn kstrtou8(s: &[u8], base: u32) -> Result<u8, i32> {
    let tmp = kstrtoull(s, base)?;
    if tmp != tmp as u8 as u64 {
        return Err(-ERANGE);
    }
    Ok(tmp as u8)
}

fn kstrtos8(s: &[u8], base: u32) -> Result<i8, i32> {
    let tmp = kstrtoll(s, base)?;
    if tmp != tmp as i8 as i64 {
        return Err(-ERANGE);
    }
    Ok(tmp as i8)
}

fn kstrtobool(s: &[u8]) -> Result<bool, i32> {
    match s[0] as char {
        'e' | 'E' | 'y' | 'Y' | 't' | 'T' | '1' => Ok(true),
        'd' | 'D' | 'n' | 'N' | 'f' | 'F' | '0' => Ok(false),
        'o' | 'O' => match s[1] as char {
            'n' | 'N' => Ok(true),
            'f' | 'F' => Ok(false),
            _ => Err(-EINVAL),
        },
        _ => Err(-EINVAL),
    }
}

struct Lcg(u64);
impl Lcg {
    fn next(&mut self) -> u32 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (self.0 >> 32) as u32
    }
}

const BUFLEN: usize = 32;
const DIGITS: &[u8] = b"0123456789abcdefABCDEF";

fn gen_numstr(rng: &mut Lcg, maxlen: usize) -> [u8; BUFLEN + 1] {
    let mut buf = [0u8; BUFLEN + 1];
    let len = (rng.next() as usize) % maxlen;
    let mut i = 0;
    if rng.next() % 3 == 0 {
        buf[i] = if rng.next() % 2 != 0 { b'+' } else { b'-' };
        i += 1;
    }
    if rng.next() % 4 == 0 && i < len {
        buf[i] = b'0';
        i += 1;
        if i < len && rng.next() % 2 != 0 {
            buf[i] = b'x';
            i += 1;
        }
    }
    while i < len {
        buf[i] = DIGITS[(rng.next() as usize) % DIGITS.len()];
        i += 1;
    }
    if rng.next() % 5 == 0 && i < maxlen {
        buf[i] = b'\n';
        i += 1;
    }
    buf[i] = 0;
    buf
}

fn cstr(v: &[u8]) -> String {
    let end = v.iter().position(|&b| b == 0).unwrap_or(v.len());
    // Escape embedded newlines for the one-record-per-line log format —
    // matches diff_kstrtox.c's sesc escaping exactly.
    std::str::from_utf8(&v[..end]).unwrap().replace('\n', "\\n")
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let s = gen_numstr(&mut rng, BUFLEN);
        let base: u32 = if rng.next() % 5 == 0 { 0 } else { 2 + rng.next() % 15 };

        let r1 = kstrtoull(&s, base);
        println!(
            "ull,{},{},{},{}",
            cstr(&s), base,
            r1.err().unwrap_or(0),
            r1.unwrap_or(0xdeadbeefu64)
        );

        let r2 = kstrtoll(&s, base);
        println!(
            "ll,{},{},{},{}",
            cstr(&s), base,
            r2.err().unwrap_or(0),
            r2.unwrap_or(0xdeadbeefi64)
        );

        let r3 = kstrtouint(&s, base);
        println!("uint,{},{},{},{}", cstr(&s), base, r3.err().unwrap_or(0), r3.unwrap_or(0xdeadbeefu32));

        let r4 = kstrtoint(&s, base);
        println!("int,{},{},{},{}", cstr(&s), base, r4.err().unwrap_or(0), r4.unwrap_or(0xdeadbeefu32 as i32));

        let r5 = kstrtou16(&s, base);
        println!("u16,{},{},{},{}", cstr(&s), base, r5.err().unwrap_or(0), r5.unwrap_or(0xdeadu16));

        let r6 = kstrtos16(&s, base);
        println!("s16,{},{},{},{}", cstr(&s), base, r6.err().unwrap_or(0), r6.unwrap_or(0xdeadu16 as i16));

        let r7 = kstrtou8(&s, base);
        println!("u8,{},{},{},{}", cstr(&s), base, r7.err().unwrap_or(0), r7.unwrap_or(0xdeu8));

        let r8 = kstrtos8(&s, base);
        println!("s8,{},{},{},{}", cstr(&s), base, r8.err().unwrap_or(0), r8.unwrap_or(0xdeu8 as i8));

        let alphabet1: &[u8] = b"eEyYtT1dDnNfF0oO?";
        let alphabet2: &[u8] = b"nNfF?";
        let mut bs = [0u8; 3];
        bs[0] = alphabet1[(rng.next() as usize) % alphabet1.len()];
        bs[1] = alphabet2[(rng.next() as usize) % alphabet2.len()];
        bs[2] = 0;
        let r9 = kstrtobool(&bs);
        println!("bool,{},{},{}", cstr(&bs), r9.err().unwrap_or(0), r9.unwrap_or(false) as i32);
    }
}
