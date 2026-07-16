// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, parser.
// Reference extracted from lib/parser.c (v7.1); kept byte-identical for
// match_wildcard and match_one/match_token (the two genuinely stateful
// pattern-scanning algorithms in this TU).
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_OPT_ARGS 3
typedef struct { char *from; char *to; } substring_t;
struct match_token { int token; const char *pattern; };
typedef struct match_token match_table_t[];

static int match_one(char *s, const char *p, substring_t args[])
{
	char *meta;
	int argc = 0;

	if (!p)
		return 1;

	while (1) {
		int len = -1;
		meta = strchr(p, '%');
		if (!meta)
			return strcmp(p, s) == 0;

		if (strncmp(p, s, meta - p))
			return 0;

		s += meta - p;
		p = meta + 1;

		if (isdigit((unsigned char)*p))
			len = strtoul(p, (char **)&p, 10);
		else if (*p == '%') {
			if (*s++ != '%')
				return 0;
			p++;
			continue;
		}

		if (argc >= MAX_OPT_ARGS)
			return 0;

		args[argc].from = s;
		switch (*p++) {
		case 's': {
			size_t str_len = strlen(s);
			if (str_len == 0)
				return 0;
			if (len == -1 || len > (int)str_len)
				len = str_len;
			args[argc].to = s + len;
			break;
		}
		case 'd':
			strtol(s, &args[argc].to, 0);
			goto num;
		case 'u':
			strtoul(s, &args[argc].to, 0);
			goto num;
		case 'o':
			strtoul(s, &args[argc].to, 8);
			goto num;
		case 'x':
			strtoul(s, &args[argc].to, 16);
		num:
			if (args[argc].to == args[argc].from)
				return 0;
			break;
		default:
			return 0;
		}
		s = args[argc].to;
		argc++;
	}
}

static int match_token(char *s, const match_table_t table, substring_t args[])
{
	const struct match_token *p;
	for (p = table; !match_one(s, p->pattern, args); p++)
		;
	return p->token;
}

static _Bool match_wildcard(const char *pattern, const char *str)
{
	const char *s = str;
	const char *p = pattern;
	_Bool star = 0;

	while (*s) {
		switch (*p) {
		case '?':
			s++; p++; break;
		case '*':
			star = 1;
			str = s;
			if (!*++p)
				return 1;
			pattern = p;
			break;
		default:
			if (*s == *p) { s++; p++; }
			else {
				if (!star) return 0;
				str++; s = str; p = pattern;
			}
			break;
		}
	}
	while (*p == '*') ++p;
	return !*p;
}

// Explicit LCG (same constants as bench/diff_base64.c).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

// Small alphabet so both literal matches and wildcard matches happen
// often (a purely uniform-random alphabet would almost never match).
static const char ALPHABET[] = "ab?*";
static void gen_str(char *buf, int maxlen, _Bool with_wild)
{
	int len = lcg_next() % maxlen;
	const char *alpha = with_wild ? ALPHABET : "ab";
	int alphalen = with_wild ? 4 : 2;
	for (int i = 0; i < len; i++)
		buf[i] = alpha[lcg_next() % alphalen];
	buf[len] = 0;
}

static const struct match_token TABLE[] = {
	{1, "opt_a"},
	{2, "opt_b=%d"},
	{3, "opt_c=%s"},
	{4, "opt_d=%x"},
	{5, "opt_e=%u,%u"},
	{-1, NULL},
};

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	char pat[32], str[32];
	for (long i = 0; i < n; i++) {
		gen_str(pat, 16, 1);
		gen_str(str, 16, 0);
		int w = match_wildcard(pat, str);
		printf("wildcard,%s,%s,%d\n", pat, str, w);
	}

	// match_token over a small fixed table with generated option
	// strings that sometimes match, sometimes don't.
	static const char *const OPT_PREFIXES[] = {
		"opt_a", "opt_b=", "opt_c=", "opt_d=", "opt_e=", "opt_z",
	};
	for (long i = 0; i < n; i++) {
		char buf[64];
		int which = lcg_next() % 6;
		int val = lcg_next() % 1000;
		unsigned uval = lcg_next() % 1000;
		unsigned uval2 = lcg_next() % 1000;
		switch (which) {
		case 0: snprintf(buf, sizeof(buf), "opt_a"); break;
		case 1: snprintf(buf, sizeof(buf), "opt_b=%d", val); break;
		case 2: snprintf(buf, sizeof(buf), "opt_c=hello%d", val); break;
		case 3: snprintf(buf, sizeof(buf), "opt_d=%x", uval); break;
		case 4: snprintf(buf, sizeof(buf), "opt_e=%u,%u", uval, uval2); break;
		default: snprintf(buf, sizeof(buf), "opt_z_unknown%d", val); break;
		}
		substring_t args[MAX_OPT_ARGS] = {{0}};
		char work[64];
		strcpy(work, buf);
		int tok = match_token(work, TABLE, args);
		printf("token,%s,%d", buf, tok);
		for (int a = 0; a < MAX_OPT_ARGS; a++) {
			if (args[a].from && args[a].to)
				printf(",[%.*s]", (int)(args[a].to - args[a].from), args[a].from);
			else
				printf(",-");
		}
		printf("\n");
	}
	return 0;
}
