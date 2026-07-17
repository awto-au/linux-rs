// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, decompress.
//! Faithful copy of lib/decompress_rs.rs's algorithm (kernel-crate
//! bindings/export/link_section stripped) — same protocol/LCG as
//! diff_decompress.c. Every decompressor slot is None, matching this
//! target config's CONFIG_DECOMPRESS_* == unset for all formats.

struct CompressFormat {
    magic: [u8; 2],
    name: Option<&'static str>,
    decompressor: Option<()>, // always None in this config; presence is what's tested
}

const COMPRESSED_FORMATS: [CompressFormat; 9] = [
    CompressFormat { magic: [0x1f, 0x8b], name: Some("gzip"), decompressor: None },
    CompressFormat { magic: [0x1f, 0x9e], name: Some("gzip"), decompressor: None },
    CompressFormat { magic: [0x42, 0x5a], name: Some("bzip2"), decompressor: None },
    CompressFormat { magic: [0x5d, 0x00], name: Some("lzma"), decompressor: None },
    CompressFormat { magic: [0xfd, 0x37], name: Some("xz"), decompressor: None },
    CompressFormat { magic: [0x89, 0x4c], name: Some("lzo"), decompressor: None },
    CompressFormat { magic: [0x02, 0x21], name: Some("lz4"), decompressor: None },
    CompressFormat { magic: [0x28, 0xb5], name: Some("zstd"), decompressor: None },
    // sentinel: name == None terminates the C original's `cf->name` loop
    CompressFormat { magic: [0, 0], name: None, decompressor: None },
];

/// Detect the decompressor for `inbuf` by magic number. Returns
/// (matched_name, decompressor_is_some).
fn decompress_method(inbuf: &[u8], name_out: &mut Option<Option<&'static str>>) -> bool {
    if inbuf.len() < 2 {
        if let Some(slot) = name_out {
            *slot = None;
        }
        return false;
    }

    let b0 = inbuf[0];
    let b1 = inbuf[1];

    let cf = COMPRESSED_FORMATS
        .iter()
        .find(|cf| cf.name.is_some() && cf.magic == [b0, b1])
        .unwrap_or_else(|| COMPRESSED_FORMATS.last().unwrap());

    if let Some(slot) = name_out {
        *slot = cf.name;
    }
    cf.decompressor.is_some()
}

// Identical LCG to diff_decompress.c.
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

const KNOWN_MAGICS: [[u8; 2]; 8] = [
    [0x1f, 0x8b], [0x1f, 0x9e], [0x42, 0x5a], [0x5d, 0x00],
    [0xfd, 0x37], [0x89, 0x4c], [0x02, 0x21], [0x28, 0xb5],
];

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let len = (rng.next() % 5) as usize; // 0..4
        let mut inbuf = [0u8; 4];

        if len >= 2 && rng.next() % 2 == 0 {
            let mi = (rng.next() % 8) as usize;
            inbuf[0] = KNOWN_MAGICS[mi][0];
            inbuf[1] = KNOWN_MAGICS[mi][1];
            for k in 2..len {
                inbuf[k] = (rng.next() & 0xff) as u8;
            }
        } else {
            for k in 0..len {
                inbuf[k] = (rng.next() & 0xff) as u8;
            }
        }

        let use_name = rng.next() % 2 == 1;
        let mut name_out: Option<Option<&'static str>> = if use_name { Some(None) } else { None };
        let has_fn = decompress_method(&inbuf[..len], &mut name_out);

        let printed_name = match (use_name, name_out) {
            (true, Some(Some(n))) => n,
            (true, Some(None)) => "(null)",
            (true, None) => unreachable!(),
            (false, _) => "-",
        };
        println!("method,{},{},{},{}", len, use_name as i32, printed_name, has_fn as i32);
    }
}
