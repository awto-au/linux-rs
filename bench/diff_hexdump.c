// SPDX-License-Identifier: GPL-2.0-only
// Tier-2.5 differential oracle: C original vs Rust translation, hexdump.
// Reference extracted from lib/hexdump.c (v7.1); kept byte-identical.
//
// Scope: hex_to_bin, hex2bin, bin2hex, hex_dump_to_buffer — matches
// lib/hexdump_rs.rs's module doc (print_hex_dump deliberately deferred,
// it's a thin printk wrapper around hex_dump_to_buffer with no
// standalone algorithmic content of its own).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
#define EINVAL 22

static const char hex_asc[] = "0123456789abcdef";

static inline char hex_asc_lo(int x) { return hex_asc[x & 0x0f]; }
static inline char hex_asc_hi(int x) { return hex_asc[(x & 0xf0) >> 4]; }
static inline char *hex_byte_pack(char *buf, u8 byte)
{
	*buf++ = hex_asc_hi(byte);
	*buf++ = hex_asc_lo(byte);
	return buf;
}

// Exact copy of lib/ctype.c's _ctype[] table (locale-independent, unlike
// glibc's isprint/isascii) so hex_dump_to_buffer's ASCII column matches
// the real kernel's classification bit-for-bit.
#define _U 0x01
#define _L 0x02
#define _D 0x04
#define _C 0x08
#define _P 0x10
#define _S 0x20
#define _X 0x40
#define _SP 0x80

static const unsigned char _ctype[] = {
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
_L,_L,_L,_L,_L,_L,_L,_P,_L,_L,_L,_L,_L,_L,_L,_L};

static inline int isprint_k(int c) { return (_ctype[(unsigned char)c] & (_P|_U|_L|_D|_SP)) != 0; }
static inline int isascii_k(int c) { return ((unsigned char)c) <= 0x7f; }

static int hex_to_bin(unsigned char ch)
{
	unsigned char cu = ch & 0xdf;
	return -1 +
		((ch - '0' +  1) & (unsigned)((ch - '9' - 1) & ('0' - 1 - ch)) >> 8) +
		((cu - 'A' + 11) & (unsigned)((cu - 'F' - 1) & ('A' - 1 - cu)) >> 8);
}

static int hex2bin(u8 *dst, const char *src, size_t count)
{
	while (count--) {
		int hi, lo;

		hi = hex_to_bin(*src++);
		if (hi < 0)
			return -EINVAL;
		lo = hex_to_bin(*src++);
		if (lo < 0)
			return -EINVAL;

		*dst++ = (hi << 4) | lo;
	}
	return 0;
}

static char *bin2hex(char *dst, const void *src, size_t count)
{
	const unsigned char *_src = src;

	while (count--)
		dst = hex_byte_pack(dst, *_src++);
	return dst;
}

static inline int is_power_of_2(unsigned int n) { return n && !(n & (n - 1)); }

static int hex_dump_to_buffer(const void *buf, size_t len, int rowsize, int groupsize,
			       char *linebuf, size_t linebuflen, int ascii)
{
	const u8 *ptr = buf;
	int ngroups;
	u8 ch;
	int j, lx = 0;
	int ascii_column;
	int ret;

	if (rowsize != 16 && rowsize != 32)
		rowsize = 16;

	if (len > (size_t)rowsize)
		len = rowsize;
	if (!is_power_of_2(groupsize) || groupsize > 8)
		groupsize = 1;
	if ((len % groupsize) != 0)
		groupsize = 1;

	ngroups = len / groupsize;
	ascii_column = rowsize * 2 + rowsize / groupsize + 1;

	if (!linebuflen)
		goto overflow1;

	if (!len)
		goto nil;

	if (groupsize == 8) {
		const uint64_t *ptr8 = buf;

		for (j = 0; j < ngroups; j++) {
			uint64_t v;
			memcpy(&v, ptr8 + j, 8);
			ret = snprintf(linebuf + lx, linebuflen - lx,
				       "%s%16.16llx", j ? " " : "",
				       (unsigned long long)v);
			if (ret >= (int)(linebuflen - lx))
				goto overflow1;
			lx += ret;
		}
	} else if (groupsize == 4) {
		const uint32_t *ptr4 = buf;

		for (j = 0; j < ngroups; j++) {
			uint32_t v;
			memcpy(&v, ptr4 + j, 4);
			ret = snprintf(linebuf + lx, linebuflen - lx,
				       "%s%8.8x", j ? " " : "", v);
			if (ret >= (int)(linebuflen - lx))
				goto overflow1;
			lx += ret;
		}
	} else if (groupsize == 2) {
		const uint16_t *ptr2 = buf;

		for (j = 0; j < ngroups; j++) {
			uint16_t v;
			memcpy(&v, ptr2 + j, 2);
			ret = snprintf(linebuf + lx, linebuflen - lx,
				       "%s%4.4x", j ? " " : "", v);
			if (ret >= (int)(linebuflen - lx))
				goto overflow1;
			lx += ret;
		}
	} else {
		for (j = 0; j < (int)len; j++) {
			if (linebuflen < (size_t)(lx + 2))
				goto overflow2;
			ch = ptr[j];
			linebuf[lx++] = hex_asc_hi(ch);
			if (linebuflen < (size_t)(lx + 2))
				goto overflow2;
			linebuf[lx++] = hex_asc_lo(ch);
			if (linebuflen < (size_t)(lx + 2))
				goto overflow2;
			linebuf[lx++] = ' ';
		}
		if (j)
			lx--;
	}
	if (!ascii)
		goto nil;

	while (lx < ascii_column) {
		if (linebuflen < (size_t)(lx + 2))
			goto overflow2;
		linebuf[lx++] = ' ';
	}
	for (j = 0; j < (int)len; j++) {
		if (linebuflen < (size_t)(lx + 2))
			goto overflow2;
		ch = ptr[j];
		linebuf[lx++] = (isascii_k(ch) && isprint_k(ch)) ? ch : '.';
	}
nil:
	linebuf[lx] = '\0';
	return lx;
overflow2:
	linebuf[lx++] = '\0';
overflow1:
	return ascii ? ascii_column + (int)len : (groupsize * 2 + 1) * ngroups - 1;
}

// Explicit LCG (same constants used across all bench/diff_*.c files).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	// hex_to_bin: every byte value
	for (long i = 0; i < n; i++) {
		unsigned char ch = (unsigned char)lcg_next();
		int r = hex_to_bin(ch);
		printf("hex2bin_digit,%u,%d\n", ch, r);
	}

	// hex2bin / bin2hex round trip style, count 0..15
	for (long i = 0; i < n; i++) {
		int count = lcg_next() % 16;
		char hexsrc[40];
		int valid = lcg_next() % 2;
		for (int k = 0; k < count * 2; k++) {
			if (valid) {
				const char *digits = "0123456789abcdefABCDEF";
				hexsrc[k] = digits[lcg_next() % 22];
			} else {
				hexsrc[k] = (char)(32 + lcg_next() % 95); // may include invalid hex chars
			}
		}
		u8 dst[20];
		int rc = hex2bin(dst, hexsrc, count);
		printf("hex2bin,%d,%d,%d,", count, valid, rc);
		if (rc == 0) {
			for (int k = 0; k < count; k++)
				printf("%02x", dst[k]);
		}
		printf("\n");

		// bin2hex on random bytes
		int bcount = lcg_next() % 20;
		u8 src[20];
		for (int k = 0; k < bcount; k++)
			src[k] = (u8)lcg_next();
		char out[48];
		char *end = bin2hex(out, src, bcount);
		printf("bin2hex,%d,%.*s\n", bcount, (int)(end - out), out);
	}

	// hex_dump_to_buffer: random buf/len/rowsize/groupsize/linebuflen/ascii
	for (long i = 0; i < n; i++) {
		int rowsize = (lcg_next() % 2) ? 16 : 32;
		if (lcg_next() % 8 == 0) rowsize = 1 + lcg_next() % 40; // occasionally invalid, forces default
		int gspick = lcg_next() % 5;
		int groupsize = gspick == 0 ? 1 : gspick == 1 ? 2 : gspick == 2 ? 4 : gspick == 3 ? 8 : 3; // 3 is invalid -> forces groupsize=1
		int len = lcg_next() % (rowsize > 0 && rowsize <= 64 ? rowsize + 8 : 40);
		if (len < 0) len = 0;
		unsigned char buf[64];
		for (int k = 0; k < len && k < 64; k++)
			buf[k] = (unsigned char)lcg_next();
		int ascii = lcg_next() % 2;
		size_t linebuflen = lcg_next() % 100; // exercise truncation paths too
		char linebuf[256];
		memset(linebuf, 0x7e, sizeof(linebuf));

		int ret = hex_dump_to_buffer(buf, (size_t)len, rowsize, groupsize,
					      linebuf, linebuflen, ascii);
		printf("dump,%d,%d,%d,%zu,%d,%d,", rowsize, groupsize, len, linebuflen, ascii, ret);
		// print linebuf up to whatever was actually written+NUL (or all
		// 0x7e sentinel bytes if linebuflen==0, nothing written)
		size_t printed = linebuflen == 0 ? 0 : strnlen(linebuf, linebuflen);
		for (size_t k = 0; k < printed; k++)
			printf("%02x", (unsigned char)linebuf[k]);
		printf("\n");
	}
	return 0;
}
