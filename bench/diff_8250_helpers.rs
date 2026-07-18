// SPDX-License-Identifier: GPL-2.0
//! Tier-2.5 differential oracle: Rust translation side, 8250/16550 pure
//! register-bit-manipulation helpers. Faithful port of the C reference
//! in diff_8250_helpers.c (kernel-crate bindings/export stripped) —
//! same protocol/LCG. See that file's header comment and
//! docs/serial-8250-translation-scoping-2026-07-18.md for provenance
//! and scope.

const UART_LCR_SPAR: u8 = 0x20;
const UART_LCR_EPAR: u8 = 0x10;
const UART_LCR_PARITY: u8 = 0x08;
const UART_LCR_STOP: u8 = 0x04;

const UART_FCR_R_TRIG_SHIFT: u32 = 6;
const UART_FCR_R_TRIG_00: u8 = 0x00;
const UART_FCR_R_TRIG_11: u8 = 0xc0;
const UART_FCR_R_TRIG_MAX_STATE: usize = 4;

fn uart_fcr_r_trig_bits(x: u8) -> usize {
    ((x & 0xc0) >> UART_FCR_R_TRIG_SHIFT) as usize
}

const CSIZE: u32 = 0x0000_0030;
const CS5: u32 = 0x0000_0000;
const CS6: u32 = 0x0000_0010;
const CS7: u32 = 0x0000_0020;
const CS8: u32 = 0x0000_0030;
const CSTOPB: u32 = 0x0000_0040;
const PARENB: u32 = 0x0000_0100;
const PARODD: u32 = 0x0000_0200;
const CMSPAR: u32 = 0x4000_0000;

/// drivers/tty/tty_ioctl.c:tty_get_char_size()
fn tty_get_char_size(cflag: u32) -> u8 {
    match cflag & CSIZE {
        CS5 => 5,
        CS6 => 6,
        CS7 => 7,
        CS8 => 8,
        _ => 8,
    }
}

/// include/linux/serial.h:UART_LCR_WLEN(x)
fn uart_lcr_wlen(x: u8) -> u8 {
    x - 5
}

/// 8250_port.c:serial8250_compute_lcr()
fn serial8250_compute_lcr(c_cflag: u32) -> u8 {
    let mut lcr = uart_lcr_wlen(tty_get_char_size(c_cflag));

    if c_cflag & CSTOPB != 0 {
        lcr |= UART_LCR_STOP;
    }
    if c_cflag & PARENB != 0 {
        lcr |= UART_LCR_PARITY;
    }
    if c_cflag & PARODD == 0 {
        lcr |= UART_LCR_EPAR;
    }
    if c_cflag & CMSPAR != 0 {
        lcr |= UART_LCR_SPAR;
    }

    lcr
}

// 8250_port.c: subset of uart_config[].rxtrig_bytes, verbatim values.
// (index constants document which row is which; only CFG_COUNT is read
// directly, the rest exist for readability parity with the C side.)
#[allow(dead_code)]
const CFG_16550: usize = 0;
#[allow(dead_code)]
const CFG_16550A: usize = 1;
#[allow(dead_code)]
const CFG_16750: usize = 2;
const CFG_COUNT: usize = 3;
const RXTRIG_BYTES: [[u8; UART_FCR_R_TRIG_MAX_STATE]; CFG_COUNT] = [
    [0, 0, 0, 0],      // CFG_16550: no working FIFO
    [1, 4, 8, 14],     // CFG_16550A
    [1, 16, 32, 56],   // CFG_16750
];

/// 8250_port.c:fcr_get_rxtrig_bytes()
fn fcr_get_rxtrig_bytes(cfg: usize, fcr: u8) -> i32 {
    let bytes = RXTRIG_BYTES[cfg][uart_fcr_r_trig_bits(fcr)];
    if bytes != 0 {
        bytes as i32
    } else {
        -95 // -EOPNOTSUPP
    }
}

/// 8250_port.c:bytes_to_fcr_rxtrig()
fn bytes_to_fcr_rxtrig(cfg: usize, bytes: u8) -> i32 {
    if RXTRIG_BYTES[cfg][uart_fcr_r_trig_bits(UART_FCR_R_TRIG_00)] == 0 {
        return -95; // -EOPNOTSUPP
    }

    for i in 1..UART_FCR_R_TRIG_MAX_STATE {
        if bytes < RXTRIG_BYTES[cfg][i] {
            return ((i - 1) as i32) << UART_FCR_R_TRIG_SHIFT;
        }
    }

    UART_FCR_R_TRIG_11 as i32
}

// Identical LCG to diff_8250_helpers.c.
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

    for i in 0..n {
        let r = rng.next();
        let cflag = if i % 4 == 0 {
            let csize_opts = [CS5, CS6, CS7, CS8];
            let mut c = csize_opts[(r & 3) as usize];
            if r & 0x10 != 0 {
                c |= CSTOPB;
            }
            if r & 0x20 != 0 {
                c |= PARENB;
            }
            if r & 0x40 != 0 {
                c |= PARODD;
            }
            if r & 0x80 != 0 {
                c |= CMSPAR;
            }
            c
        } else {
            r
        };
        let lcr = serial8250_compute_lcr(cflag);
        println!("lcr,{},{}", cflag, lcr);
    }

    for _ in 0..n {
        let cfg = (rng.next() % CFG_COUNT as u32) as usize;
        let fcr = (rng.next() & 0xff) as u8;
        let r = fcr_get_rxtrig_bytes(cfg, fcr);
        println!("rxtrig_get,{},{},{}", cfg, fcr, r);
    }

    for _ in 0..n {
        let cfg = (rng.next() % CFG_COUNT as u32) as usize;
        let bytes = (rng.next() & 0xff) as u8;
        let r = bytes_to_fcr_rxtrig(cfg, bytes);
        println!("rxtrig_set,{},{},{}", cfg, bytes, r);
    }
}
