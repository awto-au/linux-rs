// SPDX-License-Identifier: GPL-2.0-only
// Tier-2.5 differential oracle: C original vs Rust translation, iomem_copy.
// Reference extracted from lib/iomem_copy.c (v7.1); kept byte-identical
// (raw MMIO accessors replaced with plain memory reads/writes -- the
// `__iomem` annotation carries no runtime effect for __raw_read*/__raw_write*
// on riscv64, they're plain volatile loads/stores; the alignment/word-size
// bookkeeping this oracle is checking is identical either way).
//
// Scope: memset_io, memcpy_fromio, memcpy_toio. Modelled on CONFIG_64BIT
// (this project's actual target, riscv64), matching lib/iomem_copy_rs.rs's
// #[cfg(CONFIG_64BIT)] branch.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef unsigned long ulong;

static inline int is_aligned_long(const void *addr)
{
	return ((ulong)addr % sizeof(long)) == 0;
}

static u8 raw_readb(const void *addr) { return *(const volatile u8 *)addr; }
static void raw_writeb(u8 val, void *addr) { *(volatile u8 *)addr = val; }
static uint64_t raw_readq(const void *addr) { return *(const volatile uint64_t *)addr; }
static void raw_writeq(uint64_t val, void *addr) { *(volatile uint64_t *)addr = val; }

static void memset_io_ref(void *addr, int val, size_t count)
{
	long qc = (u8)val;

	qc *= ~0UL / 0xff;

	while (count && !is_aligned_long(addr)) {
		raw_writeb(val, addr);
		addr = (u8 *)addr + 1;
		count--;
	}

	while (count >= sizeof(long)) {
		raw_writeq((uint64_t)qc, addr);

		addr = (u8 *)addr + sizeof(long);
		count -= sizeof(long);
	}

	while (count) {
		raw_writeb(val, addr);
		addr = (u8 *)addr + 1;
		count--;
	}
}

static void memcpy_fromio_ref(void *dst, const void *src, size_t count)
{
	while (count && !is_aligned_long(src)) {
		*(u8 *)dst = raw_readb(src);
		src = (const u8 *)src + 1;
		dst = (u8 *)dst + 1;
		count--;
	}

	while (count >= sizeof(long)) {
		long val = (long)raw_readq(src);
		memcpy(dst, &val, sizeof(long)); // put_unaligned

		src = (const u8 *)src + sizeof(long);
		dst = (u8 *)dst + sizeof(long);
		count -= sizeof(long);
	}

	while (count) {
		*(u8 *)dst = raw_readb(src);
		src = (const u8 *)src + 1;
		dst = (u8 *)dst + 1;
		count--;
	}
}

static void memcpy_toio_ref(void *dst, const void *src, size_t count)
{
	while (count && !is_aligned_long(dst)) {
		raw_writeb(*(const u8 *)src, dst);
		src = (const u8 *)src + 1;
		dst = (u8 *)dst + 1;
		count--;
	}

	while (count >= sizeof(long)) {
		long val;
		memcpy(&val, src, sizeof(long)); // get_unaligned
		raw_writeq((uint64_t)val, dst);

		src = (const u8 *)src + sizeof(long);
		dst = (u8 *)dst + sizeof(long);
		count -= sizeof(long);
	}

	while (count) {
		raw_writeb(*(const u8 *)src, dst);
		src = (const u8 *)src + 1;
		dst = (u8 *)dst + 1;
		count--;
	}
}

// Explicit LCG (same constants used across all bench/diff_*.c files).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

// Oversized buffer with deliberate byte-offset placement so alignment paths
// (unaligned lead-in of 0..7 bytes) get exercised, not just word-aligned starts.
#define BUFCAP 256

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	for (long i = 0; i < n; i++) {
		u8 backing[BUFCAP];
		int off = lcg_next() % 8; // vary alignment of the target range
		size_t count = lcg_next() % (BUFCAP - 16);
		int val = (int)(lcg_next() & 0xff);

		memset(backing, 0x33, sizeof(backing));
		memset_io_ref(backing + off, val, count);

		printf("memset_io,%d,%zu,%d,", off, count, val);
		for (size_t k = 0; k < sizeof(backing); k++)
			printf("%02x", backing[k]);
		printf("\n");
	}

	for (long i = 0; i < n; i++) {
		u8 src[BUFCAP], dst[BUFCAP];
		int soff = lcg_next() % 8;
		int doff = lcg_next() % 8;
		size_t count = lcg_next() % (BUFCAP - 16);

		for (size_t k = 0; k < sizeof(src); k++)
			src[k] = (u8)lcg_next();
		memset(dst, 0x55, sizeof(dst));

		memcpy_fromio_ref(dst + doff, src + soff, count);

		printf("memcpy_fromio,%d,%d,%zu,", soff, doff, count);
		for (size_t k = 0; k < sizeof(dst); k++)
			printf("%02x", dst[k]);
		printf("\n");
	}

	for (long i = 0; i < n; i++) {
		u8 src[BUFCAP], dst[BUFCAP];
		int soff = lcg_next() % 8;
		int doff = lcg_next() % 8;
		size_t count = lcg_next() % (BUFCAP - 16);

		for (size_t k = 0; k < sizeof(src); k++)
			src[k] = (u8)lcg_next();
		memset(dst, 0x77, sizeof(dst));

		memcpy_toio_ref(dst + doff, src + soff, count);

		printf("memcpy_toio,%d,%d,%zu,", soff, doff, count);
		for (size_t k = 0; k < sizeof(dst); k++)
			printf("%02x", dst[k]);
		printf("\n");
	}

	return 0;
}
