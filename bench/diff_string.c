// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, string.
// Reference extracted from lib/string.c (v7.1); kept byte-identical for
// the non-arch-overridden subset (rule 0026 — riscv64 KASAN-off
// overrides memset/memcpy/memmove/strcmp/strlen/strncmp/strnlen/
// strchr/strrchr via arch/riscv/lib/*.S, not exercised here).
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>

typedef unsigned long ul;
#define E2BIG 7

static int my_strncasecmp(const char *s1, const char *s2, size_t len)
{
	unsigned char c1, c2;
	if (!len) return 0;
	do {
		c1 = *s1++;
		c2 = *s2++;
		if (!c1 || !c2) break;
		if (c1 == c2) continue;
		c1 = tolower(c1);
		c2 = tolower(c2);
		if (c1 != c2) break;
	} while (--len);
	return (int)c1 - (int)c2;
}

static int my_strcasecmp(const char *s1, const char *s2)
{
	int c1, c2;
	do {
		c1 = tolower((unsigned char)*s1++);
		c2 = tolower((unsigned char)*s2++);
	} while (c1 == c2 && c1 != 0);
	return c1 - c2;
}

static char *my_strncpy(char *dest, const char *src, size_t count)
{
	char *tmp = dest;
	while (count) {
		if ((*tmp = *src) != 0) src++;
		tmp++; count--;
	}
	return dest;
}

// sized_strscpy: byte-replicates the real riscv64 word-at-a-time
// algorithm (lib/string.c + arch/riscv/include/asm/word-at-a-time.h).
// The page-boundary-avoidance branch
// (CONFIG_HAVE_EFFICIENT_UNALIGNED_ACCESS, set on riscv64) is skipped
// here deliberately: on a host heap buffer there is no unmapped-next-
// page hazard to guard against, and skipping it does not change
// dest/return-value outputs (which is all this oracle checks) — it
// only changes how conservatively `max` is capped before the loop.
#define ONE_BITS (~0ul / 0xff)
#define HIGH_BITS (ONE_BITS * 0x80)
static inline ul has_zero(ul val, ul *bits)
{
	ul mask = ((val - ONE_BITS) & ~val) & HIGH_BITS;
	*bits = mask;
	return mask;
}
static inline ul create_zero_mask(ul bits)
{
	bits = (bits - 1) & ~bits;
	return bits >> 7;
}
static inline int my_fls64(uint64_t x)
{
	if (x == 0) return 0;
	return 64 - __builtin_clzll(x);
}
static inline ul find_zero(ul mask) { return my_fls64(mask) >> 3; }

static ssize_t my_sized_strscpy(char *dest, const char *src, size_t count)
{
	size_t max = count;
	long res = 0;

	if (count == 0 || count > INT32_MAX)
		return -E2BIG;

	while (max >= sizeof(ul)) {
		ul bits;
		ul c;
		memcpy(&c, src + res, sizeof(ul));
		if (has_zero(c, &bits)) {
			ul data = create_zero_mask(bits);
			ul bytemask = data;
			ul masked = c & bytemask;
			memcpy(dest + res, &masked, sizeof(ul));
			return res + find_zero(data);
		}
		count -= sizeof(ul);
		if (!count) {
			c &= (~0ul >> 8);
			memcpy(dest + res, &c, sizeof(ul));
			return -E2BIG;
		}
		memcpy(dest + res, &c, sizeof(ul));
		res += sizeof(ul);
		max -= sizeof(ul);
	}

	while (count > 1) {
		char c = src[res];
		dest[res] = c;
		if (!c) return res;
		res++;
		count--;
	}

	dest[res] = '\0';
	return src[res] ? -E2BIG : res;
}

static char *my_strncat(char *dest, const char *src, size_t count)
{
	char *tmp = dest;
	if (count) {
		while (*dest) dest++;
		while ((*dest++ = *src++) != 0) {
			if (--count == 0) { *dest = '\0'; break; }
		}
	}
	return tmp;
}

static size_t my_strlcat(char *dest, const char *src, size_t count)
{
	size_t dsize = strlen(dest);
	size_t len = strlen(src);
	size_t res = dsize + len;
	assert(dsize < count);
	dest += dsize;
	count -= dsize;
	if (len >= count) len = count - 1;
	memcpy(dest, src, len);
	dest[len] = 0;
	return res;
}

static char *my_strchrnul(const char *s, int c)
{
	while (*s && *s != (char)c) s++;
	return (char *)s;
}

static char *my_strnchrnul(const char *s, size_t count, int c)
{
	while (count-- && *s && *s != (char)c) s++;
	return (char *)s;
}

static char *my_strnchr(const char *s, size_t count, int c)
{
	while (count--) {
		if (*s == (char)c) return (char *)s;
		if (*s++ == '\0') break;
	}
	return NULL;
}

static size_t my_strspn(const char *s, const char *accept)
{
	const char *p;
	for (p = s; *p != '\0'; ++p)
		if (!strchr(accept, *p)) break;
	return p - s;
}

static size_t my_strcspn(const char *s, const char *reject)
{
	const char *p;
	for (p = s; *p != '\0'; ++p)
		if (strchr(reject, *p)) break;
	return p - s;
}

static char *my_strpbrk(const char *cs, const char *ct)
{
	const char *sc;
	for (sc = cs; *sc != '\0'; ++sc)
		if (strchr(ct, *sc)) return (char *)sc;
	return NULL;
}

static char *my_strsep(char **s, const char *ct)
{
	char *sbegin = *s;
	char *end;
	if (sbegin == NULL) return NULL;
	end = my_strpbrk(sbegin, ct);
	if (end) *end++ = '\0';
	*s = end;
	return sbegin;
}

static int my_memcmp(const void *cs, const void *ct, size_t count)
{
	if (count >= sizeof(ul)) {
		const ul *u1 = cs;
		const ul *u2 = ct;
		do {
			if (*u1 != *u2) break;
			u1++; u2++;
			count -= sizeof(ul);
		} while (count >= sizeof(ul));
		cs = u1; ct = u2;
	}
	const unsigned char *su1, *su2;
	int res = 0;
	for (su1 = cs, su2 = ct; 0 < count; ++su1, ++su2, count--)
		if ((res = *su1 - *su2) != 0) break;
	return res;
}

static void *my_memscan(void *addr, int c, size_t size)
{
	unsigned char *p = addr;
	while (size) {
		if (*p == (unsigned char)c) return (void *)p;
		p++; size--;
	}
	return (void *)p;
}

static char *my_strstr(const char *s1, const char *s2)
{
	size_t l1, l2;
	l2 = strlen(s2);
	if (!l2) return (char *)s1;
	l1 = strlen(s1);
	while (l1 >= l2) {
		l1--;
		if (!my_memcmp(s1, s2, l2)) return (char *)s1;
		s1++;
	}
	return NULL;
}

static char *my_strnstr(const char *s1, const char *s2, size_t len)
{
	size_t l2 = strlen(s2);
	if (!l2) return (char *)s1;
	while (len >= l2) {
		len--;
		if (!my_memcmp(s1, s2, l2)) return (char *)s1;
		s1++;
	}
	return NULL;
}

static void *my_memchr(const void *s, int c, size_t n)
{
	const unsigned char *p = s;
	while (n-- != 0)
		if ((unsigned char)c == *p++) return (void *)(p - 1);
	return NULL;
}

static void *check_bytes8(const uint8_t *start, uint8_t value, unsigned int bytes)
{
	while (bytes) {
		if (*start != value) return (void *)start;
		start++; bytes--;
	}
	return NULL;
}

static void *my_memchr_inv(const void *start, int c, size_t bytes)
{
	uint8_t value = c;
	uint64_t value64;
	unsigned int words, prefix;

	if (bytes <= 16) return check_bytes8(start, value, bytes);

	value64 = value;
	value64 *= 0x0101010101010101ULL;

	prefix = (unsigned long)start % 8;
	if (prefix) {
		uint8_t *r;
		prefix = 8 - prefix;
		r = check_bytes8(start, value, prefix);
		if (r) return r;
		start += prefix;
		bytes -= prefix;
	}

	words = bytes / 8;
	while (words) {
		if (*(uint64_t *)start != value64) return check_bytes8(start, value, 8);
		start += 8;
		words--;
	}
	return check_bytes8(start, value, bytes % 8);
}

// Explicit LCG (same constants as bench/diff_base64.c).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

static const char ALPHABET[] = "abcXYZ .";
static void gen_str(char *buf, int maxlen)
{
	int len = 1 + lcg_next() % (maxlen - 1);
	for (int i = 0; i < len; i++) buf[i] = ALPHABET[lcg_next() % (sizeof(ALPHABET) - 1)];
	buf[len] = 0;
}
static void gen_bytes(unsigned char *buf, int len, int biased)
{
	for (int i = 0; i < len; i++)
		buf[i] = biased ? (lcg_next() % 3) : (lcg_next() & 0xff);
}

#define BUFLEN 64

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	char a[BUFLEN], b[BUFLEN];

	for (long i = 0; i < n; i++) {
		memset(a, 0, sizeof(a));
		memset(b, 0, sizeof(b));
		gen_str(a, BUFLEN - 1);
		gen_str(b, BUFLEN - 1);
		size_t len = lcg_next() % (BUFLEN - 1) + 1;

		printf("strncasecmp,%s,%s,%zu,%d\n", a, b, len, my_strncasecmp(a, b, len));
		printf("strcasecmp,%s,%s,%d\n", a, b, my_strcasecmp(a, b));

		char dst1[BUFLEN] = {0};
		my_strncpy(dst1, a, BUFLEN - 1);
		printf("strncpy,%s,[%.*s]\n", a, BUFLEN - 1, dst1);

		char dst2[BUFLEN];
		memset(dst2, 0x55, sizeof(dst2));
		size_t cnt = lcg_next() % (BUFLEN - 1) + 1;
		ssize_t r = my_sized_strscpy(dst2, a, cnt);
		printf("strscpy,%s,%zu,%zd,[%.*s]\n", a, cnt, r, BUFLEN, dst2);

		char dst3[BUFLEN];
		strcpy(dst3, a);
		my_strncat(dst3, b, lcg_next() % 16);
		printf("strncat,%s,%s,[%s]\n", a, b, dst3);

		char dst4[BUFLEN];
		strcpy(dst4, a);
		size_t lr = my_strlcat(dst4, b, BUFLEN);
		printf("strlcat,%s,%s,%zu,[%s]\n", a, b, lr, dst4);

		int ch = ALPHABET[lcg_next() % (sizeof(ALPHABET) - 1)];
		char *r1 = my_strchrnul(a, ch);
		printf("strchrnul,%s,%c,%ld\n", a, ch, r1 - a);
		char *r2 = my_strnchrnul(a, len, ch);
		printf("strnchrnul,%s,%zu,%c,%ld\n", a, len, ch, r2 - a);
		char *r3 = my_strnchr(a, len, ch);
		printf("strnchr,%s,%zu,%c,%ld\n", a, len, ch, r3 ? r3 - a : -1);

		printf("strspn,%s,%s,%zu\n", a, b, my_strspn(a, b));
		printf("strcspn,%s,%s,%zu\n", a, b, my_strcspn(a, b));
		char *r4 = my_strpbrk(a, b);
		printf("strpbrk,%s,%s,%ld\n", a, b, r4 ? r4 - a : -1);

		char work[BUFLEN];
		strcpy(work, a);
		char *sp = work;
		printf("strsep,%s,%s", a, b);
		for (int k = 0; k < 5 && sp; k++) {
			char *tok = my_strsep(&sp, b);
			printf(",[%s]", tok);
		}
		printf("\n");

		unsigned char m1[BUFLEN], m2[BUFLEN];
		gen_bytes(m1, BUFLEN, 1);
		memcpy(m2, m1, BUFLEN);
		size_t mlen = lcg_next() % BUFLEN;
		if (lcg_next() % 4 == 0 && mlen > 0)
			m2[lcg_next() % mlen] ^= 0xff;
		printf("memcmp,%zu,%d\n", mlen, my_memcmp(m1, m2, mlen));

		unsigned char sbuf[BUFLEN];
		gen_bytes(sbuf, BUFLEN, 1);
		int target = lcg_next() % 3;
		void *sr = my_memscan(sbuf, target, BUFLEN);
		printf("memscan,%d,%ld\n", target, (unsigned char *)sr - sbuf);

		char *sr2 = my_strstr(a, b);
		printf("strstr,%s,%s,%ld\n", a, b, sr2 ? sr2 - a : -1);
		size_t slen = lcg_next() % (BUFLEN - 1);
		char *sr3 = my_strnstr(a, b, slen);
		printf("strnstr,%s,%s,%zu,%ld\n", a, b, slen, sr3 ? sr3 - a : -1);

		void *cr = my_memchr(m1, target, mlen);
		printf("memchr,%d,%zu,%ld\n", target, mlen, cr ? (unsigned char *)cr - m1 : -1);

		unsigned char invbuf[BUFLEN];
		int fillval = lcg_next() % 3;
		for (int k = 0; k < BUFLEN; k++) invbuf[k] = fillval;
		if (lcg_next() % 3 != 0) {
			int pos = lcg_next() % BUFLEN;
			invbuf[pos] = (fillval + 1) % 3;
		}
		size_t ilen = lcg_next() % BUFLEN;
		void *ir = my_memchr_inv(invbuf, fillval, ilen);
		printf("memchr_inv,%d,%zu,%ld\n", fillval, ilen, ir ? (unsigned char *)ir - invbuf : -1);
	}
	return 0;
}
