// SPDX-License-Identifier: (GPL-2.0 OR MIT)
// Tier-2.5 differential oracle: C original vs Rust translation, glob.
// Reference extracted from lib/glob.c (v7.1), kept byte-identical.
//
// The real upstream KUnit suite (lib/tests/glob_kunit.c, 64 cases) is
// the primary correctness gate for glob_match's tricky backtracking
// logic and already passes on real boot (dev.py check). This oracle's
// job is narrower: fuzz glob_match_len specifically -- the
// length-bounded variant KUnit doesn't cover at all -- exercising
// truncation mid-pattern and embedded NUL bytes glob_match's plain
// NUL-terminated contract can't reach.
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static bool glob_match_str(char const *pat, char const *str, char const *str_end)
{
	char const *back_pat = NULL, *back_str = NULL;

	for (;;) {
		unsigned char c = (str_end && str >= str_end) ? '\0' : *str;
		unsigned char d = *pat++;

		str++;

		switch (d) {
		case '?':
			if (c == '\0')
				return false;
			break;
		case '*':
			if (*pat == '\0')
				return true;
			back_pat = pat;
			back_str = --str;
			break;
		case '[': {
			if (c == '\0')
				return false;
			bool match = false, inverted = (*pat == '!');
			char const *class = inverted ? pat + 1 : pat;
			unsigned char a = *class++;

			do {
				unsigned char b = a;

				if (a == '\0')
					goto literal;

				if (class[0] == '-' && class[1] != ']') {
					b = class[1];

					if (b == '\0')
						goto literal;

					class += 2;
				}
				if (a <= c && c <= b)
					match = true;
			} while ((a = *class++) != ']');

			if (match == inverted)
				goto backtrack;
			pat = class;
			}
			break;
		case '\\':
			d = *pat++;
			// fallthrough
		default:
literal:
			if (c == d) {
				if (d == '\0')
					return true;
				break;
			}
backtrack:
			if (c == '\0' || !back_pat)
				return false;
			pat = back_pat;
			str = ++back_str;
			break;
		}
	}
}

static bool glob_match_len(char const *pat, char const *str, size_t len)
{
	return glob_match_str(pat, str, str + len);
}

// Explicit LCG (same constants used across all bench/diff_*.c files).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

// Pattern/string alphabets kept small and metacharacter-heavy so most
// random draws actually exercise ?, *, [ ] backtracking rather than
// degenerating into all-literal comparisons.
static const char alphabet[] = "ab[]!*?\\-c";

static void gen_buf(char *buf, size_t max_len, size_t *out_len)
{
	size_t n = lcg_next() % (max_len + 1);
	for (size_t i = 0; i < n; i++)
		buf[i] = alphabet[lcg_next() % (sizeof(alphabet) - 1)];
	// Occasionally embed a real NUL mid-buffer to exercise str_end's
	// "NUL within len still terminates" contract explicitly.
	if (n > 2 && lcg_next() % 4 == 0)
		buf[n / 2] = '\0';
	*out_len = n;
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;
	char pat[17];
	char str[17];

	for (long i = 0; i < n; i++) {
		size_t pat_len, str_len;
		gen_buf(pat, 16, &pat_len);
		pat[pat_len] = '\0'; // pat is always the real NUL-terminated contract
		gen_buf(str, 16, &str_len);

		// len is sometimes the real buffer length, sometimes
		// deliberately truncated shorter, to exercise mid-pattern
		// cutoff.
		size_t len = (lcg_next() % 2 == 0) ? str_len
						    : (lcg_next() % (str_len + 1));

		bool r = glob_match_len(pat, str, len);
		printf("glob_match_len,%zu,%zu,%d\n", pat_len, len, r);
	}
	return 0;
}
