// SPDX-License-Identifier: GPL-2.0
//! Detect the decompression method based on magic number — Rust
//! translation of `lib/decompress.c`.
//!
//! Each table entry's decompressor slot is `#[cfg(CONFIG_DECOMPRESS_*)]`-
//! gated to the real `bindings::` function, or `None` when that config is
//! off — the direct equivalent of the C original's
//! `#ifndef CONFIG_DECOMPRESS_GZIP` / `#define gunzip NULL` pattern (see
//! `lib/decompress.c`). TU 22's first translation hardcoded every slot to
//! `None` unconditionally on the (then-true, since CONFIG_BLK_DEV_INITRD
//! was off) assumption that no CONFIG_DECOMPRESS_* would ever be enabled
//! for this target — losing the config-conditionality entirely meant
//! initramfs support (added later) silently could never decompress
//! anything, regardless of kernel config: `Initramfs unpacking failed:
//! decompressor failed` / `compression method gzip not configured` even
//! with CONFIG_DECOMPRESS_GZIP=y. Caught by the first real -initrd boot
//! (init/do_mounts.c coverage), never by KUnit (nothing kernel-space
//! exercised this dispatch table's actual decompressor pointers, only
//! its magic-number matching).
//!
//! `__init`/`__initconst` (freed after boot) expressed via
//! `#[link_section]` matching the sections `include/asm-generic/
//! vmlinux.lds.h` reserves for them — the closest faithful Rust
//! equivalent; the kernel crate has no higher-level wrapper for this yet
//! (checked 2026-07-16, no precedent found).

use kernel::bindings;
use kernel::ffi::{c_char, c_long, c_uchar, c_ulong};
use kernel::prelude::*;

type DecompressFn = Option<
    unsafe extern "C" fn(
        *mut c_uchar,
        c_long,
        Option<unsafe extern "C" fn(*mut kernel::ffi::c_void, c_ulong) -> c_long>,
        Option<unsafe extern "C" fn(*mut kernel::ffi::c_void, c_ulong) -> c_long>,
        *mut c_uchar,
        *mut c_long,
        Option<unsafe extern "C" fn(*mut c_char)>,
    ) -> kernel::ffi::c_int,
>;

#[repr(C)]
struct CompressFormat {
    magic: [u8; 2],
    name: *const c_char,
    decompressor: DecompressFn,
}
// SAFETY: a `*const c_char` to a 'static string literal is Sync (never
// mutated); this table is never written after initialisation, matching
// the C `static const` + __initconst placement.
unsafe impl Sync for CompressFormat {}

macro_rules! fmt {
    ($m0:expr, $m1:expr, $name:expr, $decompressor:expr) => {
        CompressFormat {
            magic: [$m0, $m1],
            name: concat!($name, "\0").as_ptr().cast(),
            decompressor: $decompressor,
        }
    };
}

// One accessor per codec, `#[cfg(CONFIG_DECOMPRESS_*)]`-gated to the real
// `bindings::` function or `None` — the direct equivalent of the C
// original's `#ifndef CONFIG_DECOMPRESS_GZIP` / `#define gunzip NULL`.
// A `const fn` per entry (rather than inlining `#[cfg]` on array elements)
// because `#[cfg]` cannot conditionally swap an *expression* inside a
// `static` array initializer, only whole items.
#[cfg(CONFIG_DECOMPRESS_GZIP)]
const fn gzip_decompressor() -> DecompressFn {
    Some(bindings::gunzip)
}
#[cfg(not(CONFIG_DECOMPRESS_GZIP))]
const fn gzip_decompressor() -> DecompressFn {
    None
}

#[cfg(CONFIG_DECOMPRESS_BZIP2)]
const fn bzip2_decompressor() -> DecompressFn {
    Some(bindings::bunzip2)
}
#[cfg(not(CONFIG_DECOMPRESS_BZIP2))]
const fn bzip2_decompressor() -> DecompressFn {
    None
}

#[cfg(CONFIG_DECOMPRESS_LZMA)]
const fn lzma_decompressor() -> DecompressFn {
    Some(bindings::unlzma)
}
#[cfg(not(CONFIG_DECOMPRESS_LZMA))]
const fn lzma_decompressor() -> DecompressFn {
    None
}

#[cfg(CONFIG_DECOMPRESS_XZ)]
const fn xz_decompressor() -> DecompressFn {
    Some(bindings::unxz)
}
#[cfg(not(CONFIG_DECOMPRESS_XZ))]
const fn xz_decompressor() -> DecompressFn {
    None
}

#[cfg(CONFIG_DECOMPRESS_LZO)]
const fn lzo_decompressor() -> DecompressFn {
    Some(bindings::unlzo)
}
#[cfg(not(CONFIG_DECOMPRESS_LZO))]
const fn lzo_decompressor() -> DecompressFn {
    None
}

#[cfg(CONFIG_DECOMPRESS_LZ4)]
const fn lz4_decompressor() -> DecompressFn {
    Some(bindings::unlz4)
}
#[cfg(not(CONFIG_DECOMPRESS_LZ4))]
const fn lz4_decompressor() -> DecompressFn {
    None
}

#[cfg(CONFIG_DECOMPRESS_ZSTD)]
const fn zstd_decompressor() -> DecompressFn {
    Some(bindings::unzstd)
}
#[cfg(not(CONFIG_DECOMPRESS_ZSTD))]
const fn zstd_decompressor() -> DecompressFn {
    None
}

#[link_section = ".init.rodata"]
static COMPRESSED_FORMATS: [CompressFormat; 9] = [
    fmt!(0x1f, 0x8b, "gzip", gzip_decompressor()),
    fmt!(0x1f, 0x9e, "gzip", gzip_decompressor()),
    fmt!(0x42, 0x5a, "bzip2", bzip2_decompressor()),
    fmt!(0x5d, 0x00, "lzma", lzma_decompressor()),
    fmt!(0xfd, 0x37, "xz", xz_decompressor()),
    fmt!(0x89, 0x4c, "lzo", lzo_decompressor()),
    fmt!(0x02, 0x21, "lz4", lz4_decompressor()),
    fmt!(0x28, 0xb5, "zstd", zstd_decompressor()),
    // sentinel: name == NULL terminates the C original's `cf->name` loop
    CompressFormat { magic: [0, 0], name: core::ptr::null(), decompressor: None },
];

/// Detect the decompressor for `inbuf`/`len` by magic number.
///
/// # Safety
/// `inbuf` points to `len` valid bytes when `len >= 2`; `name`, if
/// non-NULL, is a valid `*mut *const c_char` to write the format name into.
#[link_section = ".init.text"]
#[export]
pub unsafe extern "C" fn decompress_method(
    inbuf: *const c_uchar,
    len: c_long,
    name: *mut *const c_char,
) -> DecompressFn {
    if len < 2 {
        // SAFETY: caller contract (name is a valid out-pointer if non-NULL).
        if !name.is_null() {
            unsafe { *name = core::ptr::null() };
        }
        return None; // Need at least this much...
    }

    // SAFETY: len >= 2 checked above; caller guarantees `len` valid bytes.
    let (b0, b1) = unsafe { (*inbuf, *inbuf.add(1)) };
    // SAFETY: pr_debug has no preconditions; the shim just formats+logs.
    unsafe { bindings::pr_debug_decompress_magic(b0, b1) };

    let cf = COMPRESSED_FORMATS
        .iter()
        .find(|cf| !cf.name.is_null() && cf.magic == [b0, b1])
        .unwrap_or_else(|| COMPRESSED_FORMATS.last().unwrap()); // sentinel: name == NULL

    // SAFETY: caller contract.
    if !name.is_null() {
        unsafe { *name = cf.name };
    }
    cf.decompressor
}
