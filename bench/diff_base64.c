// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, base64.
// Host reference extracted from lib/base64.c (v7.1); kept byte-identical.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int8_t s8;
typedef uint8_t u8;
typedef uint32_t u32;

enum base64_variant { BASE64_STD, BASE64_URLSAFE, BASE64_IMAP };

static const char base64_tables[][65] = {
	[BASE64_STD] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
	[BASE64_URLSAFE] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_",
	[BASE64_IMAP] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+,",
};

#define INIT_1(v, ch_62, ch_63) \
	[v] = (v) >= 'A' && (v) <= 'Z' ? (v) - 'A' \
		: (v) >= 'a' && (v) <= 'z' ? (v) - 'a' + 26 \
		: (v) >= '0' && (v) <= '9' ? (v) - '0' + 52 \
		: (v) == (ch_62) ? 62 : (v) == (ch_63) ? 63 : -1
#define INIT_2(v, ...) INIT_1(v, __VA_ARGS__), INIT_1((v) + 1, __VA_ARGS__)
#define INIT_4(v, ...) INIT_2(v, __VA_ARGS__), INIT_2((v) + 2, __VA_ARGS__)
#define INIT_8(v, ...) INIT_4(v, __VA_ARGS__), INIT_4((v) + 4, __VA_ARGS__)
#define INIT_16(v, ...) INIT_8(v, __VA_ARGS__), INIT_8((v) + 8, __VA_ARGS__)
#define INIT_32(v, ...) INIT_16(v, __VA_ARGS__), INIT_16((v) + 16, __VA_ARGS__)
#define BASE64_REV_INIT(ch_62, ch_63) { \
	[0 ... 0x1f] = -1, \
	INIT_32(0x20, ch_62, ch_63), \
	INIT_32(0x40, ch_62, ch_63), \
	INIT_32(0x60, ch_62, ch_63), \
	[0x80 ... 0xff] = -1 }

static const s8 base64_rev_maps[][256] = {
	[BASE64_STD] = BASE64_REV_INIT('+', '/'),
	[BASE64_URLSAFE] = BASE64_REV_INIT('-', '_'),
	[BASE64_IMAP] = BASE64_REV_INIT('+', ','),
};

static int base64_encode(const u8 *src, int srclen, char *dst, int padding, int variant)
{
	u32 ac = 0;
	char *cp = dst;
	const char *t = base64_tables[variant];

	while (srclen >= 3) {
		ac = src[0] << 16 | src[1] << 8 | src[2];
		*cp++ = t[ac >> 18];
		*cp++ = t[(ac >> 12) & 0x3f];
		*cp++ = t[(ac >> 6) & 0x3f];
		*cp++ = t[ac & 0x3f];
		src += 3;
		srclen -= 3;
	}
	switch (srclen) {
	case 2:
		ac = src[0] << 16 | src[1] << 8;
		*cp++ = t[ac >> 18];
		*cp++ = t[(ac >> 12) & 0x3f];
		*cp++ = t[(ac >> 6) & 0x3f];
		if (padding) *cp++ = '=';
		break;
	case 1:
		ac = src[0] << 16;
		*cp++ = t[ac >> 18];
		*cp++ = t[(ac >> 12) & 0x3f];
		if (padding) { *cp++ = '='; *cp++ = '='; }
		break;
	}
	return cp - dst;
}

static int base64_decode(const char *src, int srclen, u8 *dst, int padding, int variant)
{
	u8 *bp = dst;
	s8 input[4];
	int32_t val;
	const u8 *s = (const u8 *)src;
	const s8 *rv = base64_rev_maps[variant];

	while (srclen >= 4) {
		input[0] = rv[s[0]]; input[1] = rv[s[1]];
		input[2] = rv[s[2]]; input[3] = rv[s[3]];
		val = input[0] << 18 | input[1] << 12 | input[2] << 6 | input[3];
		if (val < 0) {
			if (!padding || srclen != 4 || s[3] != '=') return -1;
			padding = 0;
			srclen = s[2] == '=' ? 2 : 3;
			break;
		}
		*bp++ = val >> 16; *bp++ = val >> 8; *bp++ = val;
		s += 4; srclen -= 4;
	}
	if (!srclen) return bp - dst;
	if (padding || srclen == 1) return -1;
	val = (rv[s[0]] << 12) | (rv[s[1]] << 6);
	*bp++ = val >> 10;
	if (srclen == 2) {
		if (val & 0x800003ff) return -1;
	} else {
		val |= rv[s[2]];
		if (val & 0x80000003) return -1;
		*bp++ = val >> 2;
	}
	return bp - dst;
}

// Explicit LCG, defined identically here and in diff_base64.rs — NOT
// libc rand()/language-stdlib RNGs, which do not agree across languages
// even with the same seed. Both sides must see the byte-identical input
// stream for a diff to be meaningful.
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

// Protocol: for each of N random-length inputs, print
// "enc,<len>,<hex(dst)>\ndec,<len>,<hex(dst)>\n" — the harness runner
// diffs this transcript byte-for-byte against the Rust build's.
int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 2000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 12345;

	for (long i = 0; i < n; i++) {
		int variant = lcg_next() % 3;
		int padding = lcg_next() % 2;
		int srclen = lcg_next() % 130; // 0..129, crosses every mod-3/4 case
		uint8_t src[130];
		for (int k = 0; k < srclen; k++) src[k] = (uint8_t)lcg_next();

		char enc[256];
		int elen = base64_encode(src, srclen, enc, padding, variant);
		printf("enc,%d,%d,%d,", variant, padding, elen);
		for (int k = 0; k < elen; k++) printf("%02x", (unsigned char)enc[k]);
		printf("\n");

		uint8_t dec[256];
		int dlen = base64_decode(enc, elen, dec, padding, variant);
		printf("dec,%d,%d,%d,", variant, padding, dlen);
		for (int k = 0; k < dlen && dlen > 0; k++) printf("%02x", dec[k]);
		printf("\n");
	}
	return 0;
}
