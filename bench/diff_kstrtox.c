// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, kstrtox.
// Reference extracted from lib/kstrtox.c (v7.1); kept byte-identical.
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint64_t u64;
#define KSTRTOX_OVERFLOW (1U << 31)
#define EINVAL 22
#define ERANGE 34
#define INT_MAX 2147483647

static inline char my__tolower(const char c) { return c | 0x20; }

static const char *my_parse_integer_fixup_radix(const char *s, unsigned int *base)
{
	if (*base == 0) {
		if (s[0] == '0') {
			if (my__tolower(s[1]) == 'x' && isxdigit((unsigned char)s[2]))
				*base = 16;
			else
				*base = 8;
		} else
			*base = 10;
	}
	if (*base == 16 && s[0] == '0' && my__tolower(s[1]) == 'x')
		s += 2;
	return s;
}

static unsigned int my_parse_integer_limit(const char *s, unsigned int base, u64 *p, size_t max_chars)
{
	u64 res = 0;
	unsigned int rv = 0;
	while (max_chars--) {
		unsigned int c = (unsigned char)*s;
		unsigned int lc = (unsigned char)my__tolower((char)c);
		unsigned int val;
		if ('0' <= c && c <= '9') val = c - '0';
		else if ('a' <= lc && lc <= 'f') val = lc - 'a' + 10;
		else break;
		if (val >= base) break;
		if (res & (~0ull << 60)) {
			if (res > (UINT64_MAX - val) / base)
				rv |= KSTRTOX_OVERFLOW;
		}
		res = res * base + val;
		rv++;
		s++;
	}
	*p = res;
	return rv;
}

static unsigned int my_parse_integer(const char *s, unsigned int base, u64 *p)
{
	return my_parse_integer_limit(s, base, p, INT_MAX);
}

static int my__kstrtoull(const char *s, unsigned int base, u64 *res)
{
	u64 _res;
	unsigned int rv;
	s = my_parse_integer_fixup_radix(s, &base);
	rv = my_parse_integer(s, base, &_res);
	if (rv & KSTRTOX_OVERFLOW) return -ERANGE;
	if (rv == 0) return -EINVAL;
	s += rv;
	if (*s == '\n') s++;
	if (*s) return -EINVAL;
	*res = _res;
	return 0;
}

static int my_kstrtoull(const char *s, unsigned int base, u64 *res)
{
	if (s[0] == '+') s++;
	return my__kstrtoull(s, base, res);
}

static int my_kstrtoll(const char *s, unsigned int base, int64_t *res)
{
	u64 tmp;
	int rv;
	if (s[0] == '-') {
		rv = my__kstrtoull(s + 1, base, &tmp);
		if (rv < 0) return rv;
		if ((int64_t)-tmp > 0) return -ERANGE;
		*res = -tmp;
	} else {
		rv = my_kstrtoull(s, base, &tmp);
		if (rv < 0) return rv;
		if ((int64_t)tmp < 0) return -ERANGE;
		*res = tmp;
	}
	return 0;
}

static int my_kstrtouint(const char *s, unsigned int base, uint32_t *res)
{
	u64 tmp; int rv;
	rv = my_kstrtoull(s, base, &tmp);
	if (rv < 0) return rv;
	if (tmp != (uint32_t)tmp) return -ERANGE;
	*res = tmp;
	return 0;
}

static int my_kstrtoint(const char *s, unsigned int base, int32_t *res)
{
	int64_t tmp; int rv;
	rv = my_kstrtoll(s, base, &tmp);
	if (rv < 0) return rv;
	if (tmp != (int32_t)tmp) return -ERANGE;
	*res = tmp;
	return 0;
}

static int my_kstrtou16(const char *s, unsigned int base, uint16_t *res)
{
	u64 tmp; int rv;
	rv = my_kstrtoull(s, base, &tmp);
	if (rv < 0) return rv;
	if (tmp != (uint16_t)tmp) return -ERANGE;
	*res = tmp;
	return 0;
}
static int my_kstrtos16(const char *s, unsigned int base, int16_t *res)
{
	int64_t tmp; int rv;
	rv = my_kstrtoll(s, base, &tmp);
	if (rv < 0) return rv;
	if (tmp != (int16_t)tmp) return -ERANGE;
	*res = tmp;
	return 0;
}
static int my_kstrtou8(const char *s, unsigned int base, uint8_t *res)
{
	u64 tmp; int rv;
	rv = my_kstrtoull(s, base, &tmp);
	if (rv < 0) return rv;
	if (tmp != (uint8_t)tmp) return -ERANGE;
	*res = tmp;
	return 0;
}
static int my_kstrtos8(const char *s, unsigned int base, int8_t *res)
{
	int64_t tmp; int rv;
	rv = my_kstrtoll(s, base, &tmp);
	if (rv < 0) return rv;
	if (tmp != (int8_t)tmp) return -ERANGE;
	*res = tmp;
	return 0;
}

static int my_kstrtobool(const char *s, _Bool *res)
{
	if (!s) return -EINVAL;
	switch (s[0]) {
	case 'e': case 'E': case 'y': case 'Y': case 't': case 'T': case '1':
		*res = 1; return 0;
	case 'd': case 'D': case 'n': case 'N': case 'f': case 'F': case '0':
		*res = 0; return 0;
	case 'o': case 'O':
		switch (s[1]) {
		case 'n': case 'N': *res = 1; return 0;
		case 'f': case 'F': *res = 0; return 0;
		default: break;
		}
		break;
	default: break;
	}
	return -EINVAL;
}

// Explicit LCG (same constants as bench/diff_base64.c).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

// Alphabet biased toward valid-ish numeric strings so parsing actually
// exercises the digit/overflow/sign/radix-prefix paths, not just
// immediate -EINVAL.
static void gen_numstr(char *buf, int maxlen)
{
	int len = lcg_next() % maxlen;
	int i = 0;
	if (lcg_next() % 3 == 0) buf[i++] = (lcg_next() % 2) ? '+' : '-';
	if (lcg_next() % 4 == 0 && i < len) { buf[i++] = '0'; if (i < len && lcg_next() % 2) buf[i++] = 'x'; }
	static const char digits[] = "0123456789abcdefABCDEF";
	for (; i < len; i++) buf[i] = digits[lcg_next() % (sizeof(digits) - 1)];
	if (lcg_next() % 5 == 0 && i < maxlen) buf[i++] = '\n';
	buf[i] = 0;
}

#define BUFLEN 32

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	char s[BUFLEN + 1];
	char sesc[BUFLEN * 2 + 1];
	for (long i = 0; i < n; i++) {
		gen_numstr(s, BUFLEN);
		// Escape embedded newlines for the one-record-per-line log
		// format (the newline is meaningful INPUT to kstrtoull, not
		// a record separator — printing it raw would corrupt CSV).
		{
			int j = 0;
			for (int k = 0; s[k]; k++) {
				if (s[k] == '\n') { sesc[j++] = '\\'; sesc[j++] = 'n'; }
				else sesc[j++] = s[k];
			}
			sesc[j] = 0;
		}
		unsigned int base = (lcg_next() % 5 == 0) ? 0 : (2 + lcg_next() % 15);

		u64 r_ull = 0xdeadbeef;
		int rv1 = my_kstrtoull(s, base, &r_ull);
		printf("ull,%s,%u,%d,%llu\n", sesc, base, rv1, (unsigned long long)r_ull);

		int64_t r_ll = 0xdeadbeef;
		int rv2 = my_kstrtoll(s, base, &r_ll);
		printf("ll,%s,%u,%d,%lld\n", sesc, base, rv2, (long long)r_ll);

		uint32_t r_ui = 0xdeadbeef;
		int rv3 = my_kstrtouint(s, base, &r_ui);
		printf("uint,%s,%u,%d,%u\n", sesc, base, rv3, r_ui);

		int32_t r_i = 0xdeadbeef;
		int rv4 = my_kstrtoint(s, base, &r_i);
		printf("int,%s,%u,%d,%d\n", sesc, base, rv4, r_i);

		uint16_t r_u16 = 0xdead;
		int rv5 = my_kstrtou16(s, base, &r_u16);
		printf("u16,%s,%u,%d,%u\n", sesc, base, rv5, r_u16);

		int16_t r_s16 = 0xdead;
		int rv6 = my_kstrtos16(s, base, &r_s16);
		printf("s16,%s,%u,%d,%d\n", sesc, base, rv6, r_s16);

		uint8_t r_u8 = 0xde;
		int rv7 = my_kstrtou8(s, base, &r_u8);
		printf("u8,%s,%u,%d,%u\n", sesc, base, rv7, r_u8);

		int8_t r_s8 = 0xde;
		int rv8 = my_kstrtos8(s, base, &r_s8);
		printf("s8,%s,%u,%d,%d\n", sesc, base, rv8, r_s8);

		char bs[3];
		bs[0] = "eEyYtT1dDnNfF0oO?"[lcg_next() % 17];
		bs[1] = "nNfF?"[lcg_next() % 5];
		bs[2] = 0;
		_Bool r_b = 0;
		int rv9 = my_kstrtobool(bs, &r_b);
		printf("bool,%s,%d,%d\n", bs, rv9, r_b);
	}
	return 0;
}
