// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation,
// ucs2_string. Reference extracted from lib/ucs2_string.c (v7.1).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef uint16_t ucs2_char_t;
typedef uint8_t u8;
typedef uint16_t u16;

static unsigned long ucs2_strnlen(const ucs2_char_t *s, size_t maxlength)
{
	unsigned long length = 0;
	while (*s++ != 0 && length < maxlength)
		length++;
	return length;
}

static int ucs2_strscpy(ucs2_char_t *dst, const ucs2_char_t *src, size_t count)
{
	long res;

	if (count == 0)
		return -1; // -E2BIG, simplified for the harness (no INT_MAX check path)

	for (res = 0; res < (long)count; res++) {
		ucs2_char_t c = src[res];
		dst[res] = c;
		if (!c)
			return (int)res;
	}
	dst[count - 1] = 0;
	return -1;
}

static int ucs2_strncmp(const ucs2_char_t *a, const ucs2_char_t *b, size_t len)
{
	while (1) {
		if (len == 0) return 0;
		if (*a < *b) return -1;
		if (*a > *b) return 1;
		if (*a == 0) return 0;
		a++; b++; len--;
	}
}

static unsigned long ucs2_utf8size(const ucs2_char_t *src)
{
	unsigned long i, j = 0;
	for (i = 0; src[i]; i++) {
		u16 c = src[i];
		if (c >= 0x800) j += 3;
		else if (c >= 0x80) j += 2;
		else j += 1;
	}
	return j;
}

static unsigned long ucs2_as_utf8(u8 *dest, const ucs2_char_t *src, unsigned long maxlength)
{
	unsigned int i;
	unsigned long j = 0;
	unsigned long limit = ucs2_strnlen(src, maxlength);

	for (i = 0; maxlength && i < limit; i++) {
		u16 c = src[i];
		if (c >= 0x800) {
			if (maxlength < 3) break;
			maxlength -= 3;
			dest[j++] = 0xe0 | (c & 0xf000) >> 12;
			dest[j++] = 0x80 | (c & 0x0fc0) >> 6;
			dest[j++] = 0x80 | (c & 0x003f);
		} else if (c >= 0x80) {
			if (maxlength < 2) break;
			maxlength -= 2;
			dest[j++] = 0xc0 | (c & 0x7c0) >> 6;
			dest[j++] = 0x80 | (c & 0x03f);
		} else {
			maxlength -= 1;
			dest[j++] = c & 0x7f;
		}
	}
	if (maxlength) dest[j] = '\0';
	return j;
}

static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

// Bias the random u16 generation toward code points that exercise all
// three UTF-8 length branches (< 0x80, 0x80..0x800, >= 0x800), plus
// occasional embedded NULs to test early termination.
static ucs2_char_t rand_ucs2(void)
{
	uint32_t r = lcg_next();
	switch (r % 8) {
	case 0: return 0; // embedded NUL
	case 1: case 2: return (ucs2_char_t)(r % 0x80);
	case 3: case 4: return (ucs2_char_t)(0x80 + (r % (0x800 - 0x80)));
	default: return (ucs2_char_t)(0x800 + (r % (0x10000 - 0x800)));
	}
}

#define MAXN 40

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 3000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 12345;

	for (long i = 0; i < n; i++) {
		ucs2_char_t a[MAXN], b[MAXN];
		int alen = 1 + (lcg_next() % (MAXN - 1));
		for (int k = 0; k < alen - 1; k++) a[k] = rand_ucs2() | 1; // avoid accidental early NUL for strlen cases
		a[alen - 1] = 0;
		int blen = 1 + (lcg_next() % (MAXN - 1));
		for (int k = 0; k < blen - 1; k++) b[k] = rand_ucs2() | 1;
		b[blen - 1] = 0;

		printf("strnlen,%lu\n", ucs2_strnlen(a, MAXN));
		printf("utf8size,%lu\n", ucs2_utf8size(a));
		printf("strncmp,%d\n", ucs2_strncmp(a, b, MAXN));

		ucs2_char_t dst[MAXN];
		size_t cnt = 1 + (lcg_next() % MAXN);
		if (cnt > MAXN) cnt = MAXN;
		int cp = ucs2_strscpy(dst, a, cnt);
		printf("strscpy,%d,", cp);
		for (int k = 0; k < (cp >= 0 ? cp : 0); k++) printf("%04x", dst[k]);
		printf("\n");

		u8 utf8[MAXN * 3 + 1];
		unsigned long maxlen = lcg_next() % (MAXN * 3);
		unsigned long ulen = ucs2_as_utf8(utf8, a, maxlen);
		printf("as_utf8,%lu,", ulen);
		for (unsigned long k = 0; k < ulen; k++) printf("%02x", utf8[k]);
		printf("\n");
	}
	return 0;
}
