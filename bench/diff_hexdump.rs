// SPDX-License-Identifier: GPL-2.0-only
//! Tier-2.5 differential oracle: Rust translation side, hexdump. Faithful
//! copy of lib/hexdump_rs.rs's algorithm (kernel-crate bindings/export
//! stripped, `_ctype`/`snprintf` replaced with a local table / plain
//! Rust formatting) — same protocol/LCG as diff_hexdump.c.
//!
//! Scope: hex_to_bin, hex2bin, bin2hex, hex_dump_to_buffer (matches
//! lib/hexdump_rs.rs's module doc; print_hex_dump deliberately excluded
//! on both sides, see diff_hexdump.c).

const HEX_ASC: &[u8; 16] = b"0123456789abcdef";

fn hex_asc_lo(x: u8) -> u8 {
    HEX_ASC[(x & 0x0f) as usize]
}
fn hex_asc_hi(x: u8) -> u8 {
    HEX_ASC[((x & 0xf0) >> 4) as usize]
}
fn hex_byte_pack(buf: &mut [u8], pos: usize, byte: u8) -> usize {
    buf[pos] = hex_asc_hi(byte);
    buf[pos + 1] = hex_asc_lo(byte);
    pos + 2
}

const _U: u8 = 0x01;
const _L: u8 = 0x02;
const _D: u8 = 0x04;
const _C: u8 = 0x08;
const _P: u8 = 0x10;
const _S: u8 = 0x20;
const _X: u8 = 0x40;
const _SP: u8 = 0x80;

// Exact copy of lib/ctype.c's _ctype[] table.
#[rustfmt::skip]
const CTYPE: [u8; 256] = [
    _C,_C,_C,_C,_C,_C,_C,_C,
    _C,_C|_S,_C|_S,_C|_S,_C|_S,_C|_S,_C,_C,
    _C,_C,_C,_C,_C,_C,_C,_C,
    _C,_C,_C,_C,_C,_C,_C,_C,
    _S|_SP,_P,_P,_P,_P,_P,_P,_P,
    _P,_P,_P,_P,_P,_P,_P,_P,
    _D,_D,_D,_D,_D,_D,_D,_D,
    _D,_D,_P,_P,_P,_P,_P,_P,
    _P,_U|_X,_U|_X,_U|_X,_U|_X,_U|_X,_U|_X,_U,
    _U,_U,_U,_U,_U,_U,_U,_U,
    _U,_U,_U,_U,_U,_U,_U,_U,
    _U,_U,_U,_P,_P,_P,_P,_P,
    _P,_L|_X,_L|_X,_L|_X,_L|_X,_L|_X,_L|_X,_L,
    _L,_L,_L,_L,_L,_L,_L,_L,
    _L,_L,_L,_L,_L,_L,_L,_L,
    _L,_L,_L,_P,_P,_P,_P,_C,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    _S|_SP,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,
    _P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,_P,
    _U,_U,_U,_U,_U,_U,_U,_U,_U,_U,_U,_U,_U,_U,_U,_U,
    _U,_U,_U,_U,_U,_U,_U,_P,_U,_U,_U,_U,_U,_U,_U,_L,
    _L,_L,_L,_L,_L,_L,_L,_L,_L,_L,_L,_L,_L,_L,_L,_L,
    _L,_L,_L,_L,_L,_L,_L,_P,_L,_L,_L,_L,_L,_L,_L,_L,
];

fn isprint(c: u8) -> bool {
    (CTYPE[c as usize] & (_P | _U | _L | _D | _SP)) != 0
}
fn isascii(c: u8) -> bool {
    c <= 0x7f
}

fn hex_to_bin(ch: u8) -> i32 {
    let cu = ch & 0xdf;
    -1 + ((ch.wrapping_sub(b'0').wrapping_add(1)) as i32
        & (((ch.wrapping_sub(b'9').wrapping_sub(1))
            & (b'0'.wrapping_sub(1).wrapping_sub(ch))) as u32
            >> 8) as i32)
        + ((cu.wrapping_sub(b'A').wrapping_add(11)) as i32
            & (((cu.wrapping_sub(b'F').wrapping_sub(1))
                & (b'A'.wrapping_sub(1).wrapping_sub(cu))) as u32
                >> 8) as i32)
}

fn hex2bin(dst: &mut [u8], src: &[u8], mut count: usize) -> i32 {
    let mut si = 0usize;
    let mut di = 0usize;
    while count > 0 {
        count -= 1;
        let hi = hex_to_bin(src[si]);
        si += 1;
        if hi < 0 {
            return -22; // EINVAL
        }
        let lo = hex_to_bin(src[si]);
        si += 1;
        if lo < 0 {
            return -22;
        }
        dst[di] = ((hi << 4) | lo) as u8;
        di += 1;
    }
    0
}

fn bin2hex(dst: &mut [u8], src: &[u8], mut count: usize) -> usize {
    let mut si = 0usize;
    let mut di = 0usize;
    while count > 0 {
        count -= 1;
        di = hex_byte_pack(dst, di, src[si]);
        si += 1;
    }
    di
}

fn is_power_of_2(n: i32) -> bool {
    n > 0 && (n & (n - 1)) == 0
}

fn overflow1(ascii: bool, ascii_column: i32, len: i32, groupsize: i32, ngroups: i32) -> i32 {
    if ascii {
        ascii_column + len
    } else {
        (groupsize * 2 + 1) * ngroups - 1
    }
}

fn hex_dump_to_buffer(
    buf: &[u8],
    mut len: usize,
    mut rowsize: i32,
    mut groupsize: i32,
    linebuf: &mut [u8],
    linebuflen: usize,
    ascii: bool,
) -> i32 {
    if rowsize != 16 && rowsize != 32 {
        rowsize = 16;
    }
    if len > rowsize as usize {
        len = rowsize as usize;
    }
    if !is_power_of_2(groupsize) || groupsize > 8 {
        groupsize = 1;
    }
    if len % groupsize as usize != 0 {
        groupsize = 1;
    }

    let ngroups = (len / groupsize as usize) as i32;
    let ascii_column = rowsize * 2 + rowsize / groupsize + 1;

    if linebuflen == 0 {
        return overflow1(ascii, ascii_column, len as i32, groupsize, ngroups);
    }
    if len == 0 {
        linebuf[0] = 0;
        return 0;
    }

    let mut lx: usize = 0;

    // snprintf(dst, cap, ...) semantics: writes up to cap-1 formatted
    // bytes plus a NUL (truncating if the formatted text is longer),
    // and returns the length the UNTRUNCATED text would have had. The
    // overflow check below compares against that untruncated length,
    // exactly like the C `ret >= linebuflen - lx` check — but even on
    // the overflow branch, snprintf has already written a truncated
    // prefix + NUL into the buffer, which the C's `overflow1:` return
    // path leaves in place (no further cleanup). Replicate that here so
    // the emitted linebuf bytes match byte-for-byte.
    fn snprintf_like(linebuf: &mut [u8], lx: usize, cap: usize, s: &[u8]) -> usize {
        // cap is `linebuflen - lx` (room INCLUDING the NUL, C snprintf
        // convention). Write min(s.len(), cap-1) bytes then a NUL.
        let n = if cap == 0 { 0 } else { s.len().min(cap - 1) };
        linebuf[lx..lx + n].copy_from_slice(&s[..n]);
        if cap > 0 {
            linebuf[lx + n] = 0;
        }
        s.len() // snprintf's return value: untruncated length
    }

    if groupsize == 8 {
        for j in 0..ngroups {
            let off = (j * 8) as usize;
            let v = u64::from_ne_bytes(buf[off..off + 8].try_into().unwrap());
            let s = if j != 0 {
                format!(" {:016x}", v)
            } else {
                format!("{:016x}", v)
            };
            let sb = s.as_bytes();
            let ret = snprintf_like(linebuf, lx, linebuflen - lx, sb);
            if ret >= linebuflen - lx {
                return overflow1(ascii, ascii_column, len as i32, groupsize, ngroups);
            }
            lx += ret;
        }
    } else if groupsize == 4 {
        for j in 0..ngroups {
            let off = (j * 4) as usize;
            let v = u32::from_ne_bytes(buf[off..off + 4].try_into().unwrap());
            let s = if j != 0 {
                format!(" {:08x}", v)
            } else {
                format!("{:08x}", v)
            };
            let sb = s.as_bytes();
            let ret = snprintf_like(linebuf, lx, linebuflen - lx, sb);
            if ret >= linebuflen - lx {
                return overflow1(ascii, ascii_column, len as i32, groupsize, ngroups);
            }
            lx += ret;
        }
    } else if groupsize == 2 {
        for j in 0..ngroups {
            let off = (j * 2) as usize;
            let v = u16::from_ne_bytes(buf[off..off + 2].try_into().unwrap());
            let s = if j != 0 {
                format!(" {:04x}", v)
            } else {
                format!("{:04x}", v)
            };
            let sb = s.as_bytes();
            let ret = snprintf_like(linebuf, lx, linebuflen - lx, sb);
            if ret >= linebuflen - lx {
                return overflow1(ascii, ascii_column, len as i32, groupsize, ngroups);
            }
            lx += ret;
        }
    } else {
        let mut j = 0usize;
        while j < len {
            if linebuflen < lx + 2 {
                linebuf[lx] = 0;
                lx += 1;
                return overflow1(ascii, ascii_column, len as i32, groupsize, ngroups);
            }
            let ch = buf[j];
            linebuf[lx] = hex_asc_hi(ch);
            lx += 1;
            if linebuflen < lx + 2 {
                linebuf[lx] = 0;
                lx += 1;
                return overflow1(ascii, ascii_column, len as i32, groupsize, ngroups);
            }
            linebuf[lx] = hex_asc_lo(ch);
            lx += 1;
            if linebuflen < lx + 2 {
                linebuf[lx] = 0;
                lx += 1;
                return overflow1(ascii, ascii_column, len as i32, groupsize, ngroups);
            }
            linebuf[lx] = b' ';
            lx += 1;
            j += 1;
        }
        if j != 0 {
            lx -= 1;
        }
    }
    if !ascii {
        linebuf[lx] = 0;
        return lx as i32;
    }

    while lx < ascii_column as usize {
        if linebuflen < lx + 2 {
            linebuf[lx] = 0;
            lx += 1;
            return overflow1(ascii, ascii_column, len as i32, groupsize, ngroups);
        }
        linebuf[lx] = b' ';
        lx += 1;
    }
    for j in 0..len {
        if linebuflen < lx + 2 {
            linebuf[lx] = 0;
            lx += 1;
            return overflow1(ascii, ascii_column, len as i32, groupsize, ngroups);
        }
        let ch = buf[j];
        linebuf[lx] = if isascii(ch) && isprint(ch) { ch } else { b'.' };
        lx += 1;
    }
    linebuf[lx] = 0;
    lx as i32
}

// Identical LCG to diff_hexdump.c.
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

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let ch = (rng.next() & 0xff) as u8;
        let r = hex_to_bin(ch);
        println!("hex2bin_digit,{},{}", ch, r);
    }

    for _ in 0..n {
        let count = (rng.next() % 16) as usize;
        let mut hexsrc = [0u8; 40];
        let valid = rng.next() % 2 == 1;
        let digits: &[u8] = b"0123456789abcdefABCDEF";
        for k in 0..count * 2 {
            if valid {
                hexsrc[k] = digits[(rng.next() % 22) as usize];
            } else {
                hexsrc[k] = (32 + rng.next() % 95) as u8;
            }
        }
        let mut dst = [0u8; 20];
        let rc = hex2bin(&mut dst, &hexsrc, count);
        print!("hex2bin,{},{},{},", count, valid as i32, rc);
        if rc == 0 {
            for k in 0..count {
                print!("{:02x}", dst[k]);
            }
        }
        println!();

        let bcount = (rng.next() % 20) as usize;
        let mut src = [0u8; 20];
        for k in 0..bcount {
            src[k] = (rng.next() & 0xff) as u8;
        }
        let mut out = [0u8; 48];
        let end = bin2hex(&mut out, &src, bcount);
        println!(
            "bin2hex,{},{}",
            bcount,
            core::str::from_utf8(&out[..end]).unwrap()
        );
    }

    for _ in 0..n {
        let mut rowsize: i32 = if rng.next() % 2 == 1 { 16 } else { 32 };
        if rng.next() % 8 == 0 {
            rowsize = 1 + (rng.next() % 40) as i32;
        }
        let gspick = rng.next() % 5;
        let groupsize: i32 = match gspick {
            0 => 1,
            1 => 2,
            2 => 4,
            3 => 8,
            _ => 3,
        };
        let mut len: i32 = (rng.next()
            % (if rowsize > 0 && rowsize <= 64 { rowsize as u32 + 8 } else { 40 }))
            as i32;
        if len < 0 {
            len = 0;
        }
        let mut buf = [0u8; 64];
        for k in 0..(len as usize).min(64) {
            buf[k] = (rng.next() & 0xff) as u8;
        }
        let ascii = rng.next() % 2 == 1;
        let linebuflen = (rng.next() % 100) as usize;
        let mut linebuf = [0x7eu8; 256];

        let ret = hex_dump_to_buffer(
            &buf,
            len as usize,
            rowsize,
            groupsize,
            &mut linebuf,
            linebuflen,
            ascii,
        );
        print!(
            "dump,{},{},{},{},{},{},",
            rowsize, groupsize, len, linebuflen, ascii as i32, ret
        );
        let printed = if linebuflen == 0 {
            0
        } else {
            linebuf[..linebuflen].iter().position(|&b| b == 0).unwrap_or(linebuflen)
        };
        for k in 0..printed {
            print!("{:02x}", linebuf[k]);
        }
        println!();
    }
}
