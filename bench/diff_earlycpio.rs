// SPDX-License-Identifier: GPL-2.0-only
//! Tier-2.5 differential oracle: Rust translation side, earlycpio.
//! Faithful copy of lib/earlycpio_rs.rs's algorithm (kernel-crate
//! bindings/export stripped, strscpy/memcmp/strlen/pr_warn replaced with
//! plain slice ops — no kernel side effects) — same protocol/LCG as
//! diff_earlycpio.c.

const MAX_CPIO_FILE_NAME: usize = 18;
const C_NFIELDS: usize = 14;
const C_MAGIC: usize = 0;
const C_MODE: usize = 2;
const C_NAMESIZE: usize = 12;
const C_FILESIZE: usize = 7;

struct CpioData {
    data: Option<usize>, // offset into the input buffer, None == NULL
    size: usize,
    name: [u8; MAX_CPIO_FILE_NAME],
}

fn ptr_align(p: usize, a: usize) -> usize {
    (p + a - 1) & !(a - 1)
}

/// Faithful port of find_cpio_data. `path` is the search prefix (no NUL
/// needed, Rust slice has its own length). `data`/`len` describe the
/// input buffer; all "pointers" are represented as offsets into it to
/// avoid unsafe raw-pointer host code while preserving the exact same
/// arithmetic/bounds checks as the real translation.
fn find_cpio_data(path: &[u8], data: &[u8], mut len: usize, nextoff: &mut Option<i64>) -> CpioData {
    const CPIO_HEADER_LEN: usize = 8 * C_NFIELDS - 2;

    let mut cd = CpioData { data: None, size: 0, name: [0u8; MAX_CPIO_FILE_NAME] };
    let mypathsize = path.len();

    let mut p: usize = 0; // offset into `data`

    while len > CPIO_HEADER_LEN {
        if data[p] == 0 {
            p += 4;
            len -= 4;
            continue;
        }

        let mut ch = [0u32; C_NFIELDS];
        let mut j: u32 = 6;
        let mut chp = 0usize;
        let mut quit = false;

        for _i in 0..C_NFIELDS {
            let mut v: u32 = 0;
            while j > 0 {
                j -= 1;
                v <<= 4;
                let c = data[p];
                p += 1;

                let x = c.wrapping_sub(b'0');
                if x < 10 {
                    v += x as u32;
                    continue;
                }
                let x = (c | 0x20).wrapping_sub(b'a');
                if x < 6 {
                    v += x as u32 + 10;
                    continue;
                }
                quit = true;
                break;
            }
            if quit {
                break;
            }
            ch[chp] = v;
            chp += 1;
            j = 8;
        }
        if quit {
            return cd;
        }

        if ch[C_MAGIC].wrapping_sub(0x070701) > 1 {
            return cd;
        }

        len -= CPIO_HEADER_LEN;

        let dptr = ptr_align(p + ch[C_NAMESIZE] as usize, 4);
        let nptr = ptr_align(dptr + ch[C_FILESIZE] as usize, 4);

        if nptr > p + len || dptr < p || nptr < dptr {
            return cd;
        }

        if (ch[C_MODE] & 0o170000) == 0o100000
            && (ch[C_NAMESIZE] as usize) >= mypathsize
            && &data[p..p + mypathsize] == path
        {
            if let Some(slot) = nextoff.as_mut() {
                *slot = (nptr as i64) - 0; // data offset 0 == "data" base, matches (long)nptr - (long)data
            } else if nextoff.is_none() {
                // caller passed a Some(_) sentinel to request write; if the
                // harness didn't want it, nextoff itself is None and we
                // must not touch it (mirrors "if (nextoff) *nextoff = ..").
            }

            // strscpy: copy up to MAX_CPIO_FILE_NAME-1 bytes then NUL,
            // truncating on overflow — same observable behaviour as the
            // real bindings::sized_strscpy call.
            let src = &data[p + mypathsize..];
            let copy_len = core::cmp::min(MAX_CPIO_FILE_NAME - 1, src.len());
            // find a NUL within copy_len if present (strscpy stops at NUL)
            let nul_pos = src[..copy_len].iter().position(|&b| b == 0);
            let n = nul_pos.unwrap_or(copy_len);
            cd.name[..n].copy_from_slice(&src[..n]);
            cd.name[n] = 0;

            cd.data = Some(dptr);
            cd.size = ch[C_FILESIZE] as usize;
            return cd;
        }
        len -= nptr - p;
        p = nptr;
    }

    cd
}

// Identical LCG to diff_earlycpio.c.
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

fn align4(x: usize) -> usize {
    (x + 3) & !3
}

fn write_entry(
    buf: &mut [u8],
    name: &[u8],
    namesize: u32,
    filesize: u32,
    mode: u32,
    corrupt_magic: bool,
) -> usize {
    let mut ch = [0u32; C_NFIELDS];
    ch[C_MAGIC] = if corrupt_magic { 0x070699 } else { 0x070701 };
    ch[1] = 1; // C_INO
    ch[C_MODE] = mode;
    ch[3] = 0; // C_UID
    ch[4] = 0; // C_GID
    ch[5] = 1; // C_NLINK
    ch[6] = 0; // C_MTIME
    ch[C_FILESIZE] = filesize;
    ch[8] = 0; // C_MAJ
    ch[9] = 0; // C_MIN
    ch[10] = 0; // C_RMAJ
    ch[11] = 0; // C_RMIN
    ch[C_NAMESIZE] = namesize;
    ch[13] = 0; // C_CHKSUM

    let mut off = 0usize;
    let magic_hex = format!("{:06x}", ch[C_MAGIC] & 0xffffff);
    buf[off..off + 6].copy_from_slice(magic_hex.as_bytes());
    off += 6;
    for f in 1..C_NFIELDS {
        let hex = format!("{:08x}", ch[f]);
        buf[off..off + 8].copy_from_slice(hex.as_bytes());
        off += 8;
    }
    buf[off..off + namesize as usize].copy_from_slice(&name[..namesize as usize]);
    off += namesize as usize;
    off = align4(off);
    for k in 0..filesize as usize {
        buf[off + k] = b'D';
    }
    off += filesize as usize;
    off = align4(off);
    off
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    let mut buf = [0u8; 512];
    let search_paths: [&[u8]; 4] = [b"foo/", b"bar/", b"a/", b""];

    for _ in 0..n {
        let kind = rng.next() % 4;
        let len: usize;

        if kind == 0 {
            len = (20 + rng.next() % 100) as usize;
            for k in 0..len {
                buf[k] = (rng.next() & 0xff) as u8;
            }
        } else if kind == 1 {
            let namelen = (1 + rng.next() % 10) as usize;
            let mut name = [0u8; 12];
            for k in 0..namelen {
                name[k] = b'a' + (rng.next() % 26) as u8;
            }
            name[namelen] = 0;
            let namesize = (namelen + 1) as u32;
            let filesize = rng.next() % 40;
            let mode = if rng.next() % 2 == 1 { 0o100644 } else { 0o040755 };
            let used = write_entry(&mut buf, &name, namesize, filesize, mode, false);
            let mut l = used;
            if rng.next() % 2 == 1 {
                let pad = (rng.next() % 30) as usize;
                for k in 0..pad {
                    if l + k >= buf.len() - 4 {
                        break;
                    }
                    buf[l + k] = (rng.next() & 0xff) as u8;
                }
                l += pad;
            }
            len = l;
        } else if kind == 2 {
            let name = [b'x', 0];
            let used = write_entry(&mut buf, &name, 2, 4, 0o100644, true);
            len = used;
        } else {
            let gap = 4 * (1 + (rng.next() % 3) as usize);
            for k in 0..gap {
                buf[k] = 0;
            }
            let name = [b'y', 0];
            let used = write_entry(&mut buf[gap..], &name, 2, 3, 0o100644, false);
            len = gap + used;
        }

        let path = search_paths[(rng.next() % 4) as usize];
        let mut nextoff: Option<i64> = Some(-999);
        let data_snapshot = buf; // find_cpio_data borrows a fresh slice of this
        let cd = find_cpio_data(path, &data_snapshot[..], len, &mut nextoff);

        print!(
            "cpio,{},{},{},{},{},",
            kind,
            core::str::from_utf8(path).unwrap(),
            len,
            cd.data.is_some() as i32,
            cd.size
        );
        for b in cd.name.iter() {
            print!("{:02x}", b);
        }
        println!(",{}", nextoff.unwrap());
    }
}
