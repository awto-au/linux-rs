// SPDX-License-Identifier: GPL-2.0-only
// Tier-2.5 differential oracle: C original vs Rust translation, cmdline.
// Reference extracted from lib/cmdline.c (v7.1), host stand-ins for the
// kernel helpers it calls (simple_strtol/simple_strtoull/skip_spaces/
// strncmp/strlen/isspace all behave identically to their libc analogues
// for the ASCII-only inputs this harness generates).
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Faithful reimplementation of lib/kstrtox.c's _parse_integer_fixup_radix
// + _parse_integer_limit chain that simple_strtoull(cp, endp, 0) drives —
// NOT libc strtoull, which (a) skips leading whitespace and (b) the
// kernel version never does. The overflow status bit
// (KSTRTOX_OVERFLOW) is masked off by simple_strntoull before use, so
// the returned value is simply the wrapping product/sum over the full
// consumed digit run — no need to replicate the overflow flag itself.
static unsigned long long simple_strtoull(const char *cp, char **endp, unsigned int base)
{
	if (base == 0) {
		if (cp[0] == '0') {
			int c1 = tolower((unsigned char)cp[1]);
			if (c1 == 'x' && isxdigit((unsigned char)cp[2]))
				base = 16;
			else
				base = 8;
		} else {
			base = 10;
		}
	}
	if (base == 16 && cp[0] == '0' && tolower((unsigned char)cp[1]) == 'x')
		cp += 2;

	unsigned long long res = 0;
	const char *s = cp;
	for (;;) {
		unsigned int c = (unsigned char)*s;
		unsigned int lc = tolower(c);
		unsigned int val;
		if (c >= '0' && c <= '9')
			val = c - '0';
		else if (lc >= 'a' && lc <= 'f')
			val = lc - 'a' + 10;
		else
			break;
		if (val >= base)
			break;
		res = res * base + val;
		s++;
	}
	if (endp)
		*endp = (char *)s;
	return res;
}
static long simple_strtol(const char *cp, char **endp, unsigned int base)
{
	if (*cp == '-')
		return -(long)simple_strtoull(cp + 1, endp, base);
	return (long)simple_strtoull(cp, endp, base);
}
static char *skip_spaces(const char *str)
{
	while (isspace((unsigned char)*str)) ++str;
	return (char *)str;
}

static int get_range(char **str, int *pint, int n)
{
	int x, inc_counter, upper_range;
	(*str)++;
	upper_range = simple_strtol((*str), NULL, 0);
	inc_counter = upper_range - *pint;
	for (x = *pint; n && x < upper_range; x++, n--)
		*pint++ = x;
	return inc_counter;
}

static int get_option(char **str, int *pint)
{
	char *cur = *str;
	int value;
	if (!cur || !(*cur))
		return 0;
	if (*cur == '-')
		value = -simple_strtoull(++cur, str, 0);
	else
		value = simple_strtoull(cur, str, 0);
	if (pint)
		*pint = value;
	if (cur == *str)
		return 0;
	if (**str == ',') {
		(*str)++;
		return 2;
	}
	if (**str == '-')
		return 3;
	return 1;
}

static char *get_options(const char *str, int nints, int *ints)
{
	int validate = (nints == 0);
	int res, i = 1;
	while (i < nints || validate) {
		int *pint = validate ? ints : ints + i;
		res = get_option((char **)&str, pint);
		if (res == 0)
			break;
		if (res == 3) {
			int n = validate ? 0 : nints - i;
			int range_nums;
			range_nums = get_range((char **)&str, pint, n);
			if (range_nums < 0)
				break;
			i += (range_nums - 1);
		}
		i++;
		if (res == 1)
			break;
	}
	ints[0] = i - 1;
	return (char *)str;
}

static unsigned long long memparse(const char *ptr, char **retptr)
{
	char *endptr;
	unsigned long long ret = simple_strtoull(ptr, &endptr, 0);
	switch (*endptr) {
	case 'E': case 'e': ret <<= 10;
	case 'P': case 'p': ret <<= 10;
	case 'T': case 't': ret <<= 10;
	case 'G': case 'g': ret <<= 10;
	case 'M': case 'm': ret <<= 10;
	case 'K': case 'k': ret <<= 10; endptr++;
	default: break;
	}
	if (retptr)
		*retptr = endptr;
	return ret;
}

static int parse_option_str(const char *str, const char *option)
{
	while (*str) {
		if (!strncmp(str, option, strlen(option))) {
			str += strlen(option);
			if (!*str || *str == ',')
				return 1;
		}
		while (*str && *str != ',')
			str++;
		if (*str == ',')
			str++;
	}
	return 0;
}

static char *next_arg(char *args, char **param, char **val)
{
	unsigned int i, equals = 0;
	int in_quote = 0, quoted = 0;
	if (*args == '"') {
		args++;
		in_quote = 1;
		quoted = 1;
	}
	for (i = 0; args[i]; i++) {
		if (isspace((unsigned char)args[i]) && !in_quote)
			break;
		if (equals == 0) {
			if (args[i] == '=')
				equals = i;
		}
		if (args[i] == '"')
			in_quote = !in_quote;
	}
	*param = args;
	if (!equals)
		*val = NULL;
	else {
		args[equals] = '\0';
		*val = args + equals + 1;
		if (**val == '"') {
			(*val)++;
			if (args[i-1] == '"')
				args[i-1] = '\0';
		}
	}
	if (quoted && i > 0 && args[i-1] == '"')
		args[i-1] = '\0';
	if (args[i]) {
		args[i] = '\0';
		args += i + 1;
	} else
		args += i;
	return skip_spaces(args);
}

// Hex-encode so embedded ',' '"' etc. from the random pool can't be
// misread as CSV structure by the harness runner's line diff.
static void print_hex(const char *s)
{
	if (!s) { printf("-1"); return; }
	for (const unsigned char *p = (const unsigned char *)s; *p; p++)
		printf("%02x", *p);
}

// Explicit LCG (same constants as bench/diff_base64.c).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

// Printable ASCII biased toward cmdline-ish tokens: digits, '-', ',',
// '=', '"', space, letters — the character classes every function here
// branches on.
static char rand_char(void)
{
	static const char pool[] = "0123456789-,= \"abcKMGTPEkmgtpe";
	return pool[lcg_next() % (sizeof(pool) - 1)];
}

#define MAXLEN 48

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 13371337;

	for (long i = 0; i < n; i++) {
		char buf[MAXLEN + 1];
		int len = 1 + (lcg_next() % MAXLEN);
		for (int k = 0; k < len; k++) buf[k] = rand_char();
		buf[len] = 0;

		// get_option / get_options on the same string.
		{
			char *s = buf;
			int dummy;
			int rc = get_option(&s, &dummy);
			printf("option,%d,%d,%ld\n", rc, dummy, (long)(s - buf));
		}
		{
			int ints[18];
			memset(ints, 0, sizeof(ints));
			char *end = get_options(buf, 18, ints);
			printf("options,%ld,", (long)(end - buf));
			for (int k = 0; k < 18; k++) printf("%d,", ints[k]);
			printf("\n");
		}
		{
			int ints[18];
			memset(ints, 0, sizeof(ints));
			get_options(buf, 0, ints); // validate mode
			printf("options_validate,%d\n", ints[0]);
		}

		// memparse: needs a leading digit run to be meaningful, but
		// feed the raw random buffer too (exercises the "no digits"
		// edge via simple_strtoull returning 0, endptr==ptr).
		{
			char *end;
			unsigned long long v = memparse(buf, &end);
			printf("memparse,%llu,%ld\n", v, (long)(end - buf));
		}

		// parse_option_str: split buf into "haystack,needle" halves.
		{
			int split = 1 + (lcg_next() % (len > 1 ? len - 1 : 1));
			char hay[MAXLEN + 1], needle[MAXLEN + 1];
			memcpy(hay, buf, len); hay[len] = 0;
			int nlen = split < len ? split : len;
			memcpy(needle, buf, nlen); needle[nlen] = 0;
			int r = parse_option_str(hay, needle);
			printf("optionstr,%d\n", r);
		}

		// next_arg mutates in place — always give it a private copy.
		{
			char copy[MAXLEN + 1];
			memcpy(copy, buf, len + 1);
			char *param = NULL, *val = NULL;
			char *rest = next_arg(copy, &param, &val);
			printf("nextarg,");
			print_hex(param);
			printf(",%d,", val != NULL);
			print_hex(val);
			printf(",%ld\n", (long)(rest - copy));
		}
	}
	return 0;
}
