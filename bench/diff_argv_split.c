// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation,
// argv_split + memcat_p (both pure-logic TUs with no KUnit suite).
// Reference extracted from lib/argv_split.c + lib/memcat_p.c (v7.1).
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int count_argc(const char *str)
{
	int count = 0;
	int was_space;
	for (was_space = 1; *str; str++) {
		if (isspace((unsigned char)*str)) {
			was_space = 1;
		} else if (was_space) {
			was_space = 0;
			count++;
		}
	}
	return count;
}

// Host argv_split: same algorithm, malloc/free instead of kmalloc/kfree
// (allocator choice is not part of what's under test — the SPLITTING
// logic is).
static char **argv_split_ref(const char *str, int *argcp)
{
	char *argv_str = strdup(str);
	if (!argv_str) return NULL;

	int argc = count_argc(argv_str);
	char **argv = malloc((argc + 2) * sizeof(*argv));
	if (!argv) { free(argv_str); return NULL; }

	*argv = argv_str;
	char **argv_ret = ++argv;
	int was_space;
	for (was_space = 1; *argv_str; argv_str++) {
		if (isspace((unsigned char)*argv_str)) {
			was_space = 1;
			*argv_str = 0;
		} else if (was_space) {
			was_space = 0;
			*argv++ = argv_str;
		}
	}
	*argv = NULL;
	if (argcp) *argcp = argc;
	return argv_ret;
}

static void argv_free_ref(char **argv)
{
	argv--;
	free(argv[0]);
	free(argv);
}

static void **memcat_p_ref(void **a, void **b)
{
	void **p, **new;
	int nr;
	for (nr = 0, p = a; *p; nr++, p++) ;
	for (p = b; *p; nr++, p++) ;
	nr++;
	new = malloc(nr * sizeof(void *));
	if (!new) return NULL;
	for (nr--; nr >= 0; nr--, p = p == b ? &a[nr] : p - 1)
		new[nr] = *p;
	return new;
}

static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

// Bias toward whitespace-heavy strings: runs of spaces/tabs, words of
// random printable chars, leading/trailing whitespace, empty string.
static void rand_argstr(char *buf, int maxlen)
{
	int len = lcg_next() % maxlen;
	int i = 0;
	while (i < len) {
		if (lcg_next() % 3 == 0) {
			int run = 1 + (lcg_next() % 3);
			for (int k = 0; k < run && i < len; k++, i++)
				buf[i] = (lcg_next() % 2) ? ' ' : '\t';
		} else {
			int run = 1 + (lcg_next() % 5);
			for (int k = 0; k < run && i < len; k++, i++)
				buf[i] = '!' + (lcg_next() % 90); // printable, non-space
		}
	}
	buf[len] = 0;
}

#define MAXLEN 60

int main(int argc, char **argv_unused)
{
	long n = argc > 1 ? atol(argv_unused[1]) : 3000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv_unused[2]) : 12345;

	// argv_split cases
	for (long i = 0; i < n; i++) {
		char buf[MAXLEN + 1];
		rand_argstr(buf, MAXLEN);

		int rargc;
		char **rargv = argv_split_ref(buf, &rargc);
		printf("split,%d,", rargc);
		if (rargv) {
			for (int k = 0; rargv[k]; k++) printf("%s|", rargv[k]);
			argv_free_ref(rargv);
		} else {
			printf("NULL");
		}
		printf("\n");
	}

	// memcat_p cases: random-length arrays of small distinct "pointer"
	// values (encode as (void*)(uintptr_t)n so both sides can print them).
	for (long i = 0; i < n / 3; i++) {
		int alen = lcg_next() % 6;
		int blen = lcg_next() % 6;
		void *a[7], *b[7];
		for (int k = 0; k < alen; k++) a[k] = (void *)(uintptr_t)(1 + lcg_next() % 1000);
		a[alen] = NULL;
		for (int k = 0; k < blen; k++) b[k] = (void *)(uintptr_t)(1 + lcg_next() % 1000);
		b[blen] = NULL;

		void **merged = memcat_p_ref(a, b);
		printf("memcat,%d,%d,", alen, blen);
		if (merged) {
			for (int k = 0; merged[k]; k++) printf("%lu,", (unsigned long)(uintptr_t)merged[k]);
			free(merged);
		} else {
			printf("NULL");
		}
		printf("\n");
	}
	return 0;
}
