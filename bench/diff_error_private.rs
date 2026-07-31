// SPDX-License-Identifier: GPL-2.0+ OR BSD-3-Clause
//! Tier-2.5 differential oracle: Rust translation side, error_private.
//! Faithful copy of lib/zstd/common/error_private_rs.rs's algorithm
//! (kernel-crate bindings/export/CStr stripped, returns &'static str
//! instead) — same protocol/LCG as diff_error_private.c.

fn err_get_error_string(code: i32) -> &'static str {
    match code {
        0 => "No error detected",
        1 => "Error (generic)",
        10 => "Unknown frame descriptor",
        12 => "Version not supported",
        14 => "Unsupported frame parameter",
        16 => "Frame requires too much memory for decoding",
        20 => "Data corruption detected",
        22 => "Restored data doesn't match checksum",
        24 => "Header of Literals' block doesn't respect format specification",
        40 => "Unsupported parameter",
        41 => "Unsupported combination of parameters",
        42 => "Parameter is out of bound",
        44 => "tableLog requires too much memory : unsupported",
        46 => "Unsupported max Symbol Value : too large",
        48 => "Specified maxSymbolValue is too small",
        49 => "This mode cannot generate an uncompressed block",
        50 => "pledged buffer stability condition is not respected",
        30 => "Dictionary is corrupted",
        32 => "Dictionary mismatch",
        34 => "Cannot create Dictionary from provided samples",
        70 => "Destination buffer is too small",
        72 => "Src size is incorrect",
        74 => "Operation on NULL destination buffer",
        80 => "Operation made no progress over multiple calls, due to output buffer being full",
        82 => "Operation made no progress over multiple calls, due to input being empty",
        100 => "Frame index is too large",
        102 => "An I/O error occurred when reading/seeking",
        104 => "Destination buffer is wrong",
        105 => "Source buffer is wrong",
        106 => "Block-level external sequence producer returned an error code",
        107 => "External sequences are not valid",
        60 => "Operation not authorized at current processing stage",
        62 => "Context should be init first",
        64 => "Allocation error : not enough memory",
        66 => "workSpace buffer is not large enough",
        _ => "Unspecified error code",
    }
}

const KNOWN_CODES: [i32; 36] = [
    0, 1, 10, 12, 14, 16, 20, 22, 24, 30, 32, 34, 40, 41, 42, 44, 46, 48,
    49, 50, 60, 62, 64, 66, 70, 72, 74, 80, 82, 100, 102, 104, 105, 106,
    107, 120,
];

// Identical LCG to diff_error_private.c.
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
        let code: i32 = if rng.next() % 2 == 0 {
            KNOWN_CODES[(rng.next() as usize) % KNOWN_CODES.len()]
        } else {
            (rng.next() % 200) as i32 - 50
        };
        let s = err_get_error_string(code);
        println!("code,{},{}", code, s);
    }
}
