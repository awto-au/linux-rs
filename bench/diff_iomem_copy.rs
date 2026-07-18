// SPDX-License-Identifier: GPL-2.0-only
//! Tier-2.5 differential oracle: Rust translation side, iomem_copy. Faithful
//! copy of lib/iomem_copy_rs.rs's algorithm (kernel-crate bindings/export
//! stripped, raw MMIO accessor calls replaced with plain volatile-free
//! reads/writes on host memory — see diff_iomem_copy.c for why that's a
//! faithful stand-in) — same protocol/LCG as diff_iomem_copy.c. Modelled on
//! CONFIG_64BIT (this project's actual target), matching the C side.

use std::os::raw::c_void;

#[inline(always)]
fn is_long_aligned(addr: *const c_void) -> bool {
    (addr as usize) % size_of::<usize>() == 0
}

unsafe fn raw_readb(addr: *const c_void) -> u8 {
    unsafe { addr.cast::<u8>().read() }
}
unsafe fn raw_writeb(val: u8, addr: *mut c_void) {
    unsafe { addr.cast::<u8>().write(val) }
}
unsafe fn raw_readq(addr: *const c_void) -> u64 {
    unsafe { addr.cast::<u64>().read() }
}
unsafe fn raw_writeq(val: u64, addr: *mut c_void) {
    unsafe { addr.cast::<u64>().write(val) }
}

unsafe fn memset_io(addr: *mut c_void, val: i32, mut count: usize) {
    let val = val as u8;
    let qc: usize = (val as usize).wrapping_mul(usize::MAX / 0xff);
    let mut addr = addr;

    unsafe {
        while count != 0 && !is_long_aligned(addr) {
            raw_writeb(val, addr.cast());
            addr = addr.cast::<u8>().add(1).cast();
            count -= 1;
        }

        while count >= size_of::<usize>() {
            raw_writeq(qc as u64, addr.cast());

            addr = addr.cast::<u8>().add(size_of::<usize>()).cast();
            count -= size_of::<usize>();
        }

        while count != 0 {
            raw_writeb(val, addr.cast());
            addr = addr.cast::<u8>().add(1).cast();
            count -= 1;
        }
    }
}

unsafe fn memcpy_fromio(dst: *mut c_void, src: *const c_void, mut count: usize) {
    let mut src = src;
    let mut dst = dst;

    unsafe {
        while count != 0 && !is_long_aligned(src) {
            *dst.cast::<u8>() = raw_readb(src.cast());
            src = src.cast::<u8>().add(1).cast();
            dst = dst.cast::<u8>().add(1).cast();
            count -= 1;
        }

        while count >= size_of::<usize>() {
            let val = raw_readq(src.cast()) as usize;

            dst.cast::<usize>().write_unaligned(val);

            src = src.cast::<u8>().add(size_of::<usize>()).cast();
            dst = dst.cast::<u8>().add(size_of::<usize>()).cast();
            count -= size_of::<usize>();
        }

        while count != 0 {
            *dst.cast::<u8>() = raw_readb(src.cast());
            src = src.cast::<u8>().add(1).cast();
            dst = dst.cast::<u8>().add(1).cast();
            count -= 1;
        }
    }
}

unsafe fn memcpy_toio(dst: *mut c_void, src: *const c_void, mut count: usize) {
    let mut src = src;
    let mut dst = dst;

    unsafe {
        while count != 0 && !is_long_aligned(dst) {
            raw_writeb(*src.cast::<u8>(), dst.cast());
            src = src.cast::<u8>().add(1).cast();
            dst = dst.cast::<u8>().add(1).cast();
            count -= 1;
        }

        while count >= size_of::<usize>() {
            let val = src.cast::<usize>().read_unaligned();

            raw_writeq(val as u64, dst.cast());

            src = src.cast::<u8>().add(size_of::<usize>()).cast();
            dst = dst.cast::<u8>().add(size_of::<usize>()).cast();
            count -= size_of::<usize>();
        }

        while count != 0 {
            raw_writeb(*src.cast::<u8>(), dst.cast());
            src = src.cast::<u8>().add(1).cast();
            dst = dst.cast::<u8>().add(1).cast();
            count -= 1;
        }
    }
}

fn size_of<T>() -> usize {
    core::mem::size_of::<T>()
}

// Identical LCG to diff_iomem_copy.c.
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

const BUFCAP: usize = 256;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let mut backing = [0x33u8; BUFCAP];
        let off = (rng.next() % 8) as usize;
        let count = (rng.next() as usize) % (BUFCAP - 16);
        let val = (rng.next() & 0xff) as i32;

        // SAFETY: `off + count <= BUFCAP - 16 + 8 < BUFCAP`, well within
        // `backing`'s bounds.
        unsafe {
            memset_io(
                backing.as_mut_ptr().add(off).cast(),
                val,
                count,
            );
        }

        print!("memset_io,{},{},{},", off, count, val);
        for b in backing.iter() {
            print!("{:02x}", b);
        }
        println!();
    }

    for _ in 0..n {
        let mut src = [0u8; BUFCAP];
        let mut dst = [0x55u8; BUFCAP];
        let soff = (rng.next() % 8) as usize;
        let doff = (rng.next() % 8) as usize;
        let count = (rng.next() as usize) % (BUFCAP - 16);

        for b in src.iter_mut() {
            *b = (rng.next() & 0xff) as u8;
        }

        // SAFETY: offsets + count stay within both buffers' bounds (see
        // memset_io loop above for the same bound reasoning).
        unsafe {
            memcpy_fromio(
                dst.as_mut_ptr().add(doff).cast(),
                src.as_ptr().add(soff).cast(),
                count,
            );
        }

        print!("memcpy_fromio,{},{},{},", soff, doff, count);
        for b in dst.iter() {
            print!("{:02x}", b);
        }
        println!();
    }

    for _ in 0..n {
        let mut src = [0u8; BUFCAP];
        let mut dst = [0x77u8; BUFCAP];
        let soff = (rng.next() % 8) as usize;
        let doff = (rng.next() % 8) as usize;
        let count = (rng.next() as usize) % (BUFCAP - 16);

        for b in src.iter_mut() {
            *b = (rng.next() & 0xff) as u8;
        }

        // SAFETY: offsets + count stay within both buffers' bounds.
        unsafe {
            memcpy_toio(
                dst.as_mut_ptr().add(doff).cast(),
                src.as_ptr().add(soff).cast(),
                count,
            );
        }

        print!("memcpy_toio,{},{},{},", soff, doff, count);
        for b in dst.iter() {
            print!("{:02x}", b);
        }
        println!();
    }
}
