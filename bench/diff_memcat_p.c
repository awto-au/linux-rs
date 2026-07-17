// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, memcat_p.
// Reference extracted from lib/memcat_p.c (v7.1); kept byte-identical
// (malloc instead of kmalloc_array — allocator choice is not under
// test, the merge/reverse-fill logic is).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void **__memcat_p(void **a, void **b)
{
	void **p = a, **new;
	int nr;

	for (nr = 0, p = a; *p; nr++, p++)
		;
	for (p = b; *p; nr++, p++)
		;
	nr++;

	new = malloc(nr * sizeof(void *));
	if (!new)
		return NULL;

	for (nr--; nr >= 0; nr--, p = p == b ? &a[nr] : p - 1)
		new[nr] = *p;

	return new;
}

// Explicit LCG (same constants used across all bench/diff_*.c files).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

#define MAXLEN 20

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	for (long i = 0; i < n; i++) {
		int alen = lcg_next() % MAXLEN;
		int blen = lcg_next() % MAXLEN;

		// Use distinct, identifiable "pointer" values (as small ints
		// cast to void*, never dereferenced) so the merged order is
		// externally observable without needing real allocations for
		// the elements themselves.
		void *a[MAXLEN + 1];
		void *b[MAXLEN + 1];
		for (int k = 0; k < alen; k++)
			a[k] = (void *)(uintptr_t)(0x1000 + lcg_next() % 0xff0 + 1); // never 0
		a[alen] = NULL;
		for (int k = 0; k < blen; k++)
			b[k] = (void *)(uintptr_t)(0x2000 + lcg_next() % 0xff0 + 1);
		b[blen] = NULL;

		void **merged = __memcat_p(a, b);

		printf("memcat,%d,%d,%d,", alen, blen, merged != NULL);
		if (merged) {
			int idx = 0;
			while (merged[idx] != NULL) {
				printf("%s%lx", idx ? "-" : "", (unsigned long)(uintptr_t)merged[idx]);
				idx++;
			}
			printf(",%d\n", idx);
			free(merged);
		} else {
			printf(",-1\n");
		}
	}
	return 0;
}
