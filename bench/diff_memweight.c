// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, memweight.
// Reference extracted from lib/memweight.c (v7.1); kept byte-identical.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned long ul;
#define BITS_PER_LONG (sizeof(ul) * 8)
#define INT_MAX 2147483647

static unsigned int hweight8(unsigned char w)
{
	unsigned int c = 0;
	while (w) { c += w & 1; w >>= 1; }
	return c;
}

static unsigned int bitmap_weight(const ul *bitmap, unsigned int nbits)
{
	unsigned int n = nbits / BITS_PER_LONG;
	unsigned int rem = nbits % BITS_PER_LONG;
	unsigned int c = 0;
	for (unsigned int i = 0; i < n; i++) {
		ul w = bitmap[i];
		while (w) { c += w & 1; w >>= 1; }
	}
	if (rem) {
		ul w = bitmap[n] & ((rem == BITS_PER_LONG) ? ~0UL : ((1UL << rem) - 1));
		while (w) { c += w & 1; w >>= 1; }
	}
	return c;
}

static size_t memweight(const void *ptr, size_t bytes)
{
	size_t ret = 0;
	size_t longs;
	const unsigned char *bitmap = ptr;

	for (; bytes > 0 && ((unsigned long)bitmap) % sizeof(long);
			bytes--, bitmap++)
		ret += hweight8(*bitmap);

	longs = bytes / sizeof(long);
	if (longs) {
		if (longs >= (size_t)(INT_MAX / BITS_PER_LONG)) {
			fprintf(stderr, "BUG_ON hit (test bug, size too large)\n");
			abort();
		}
		ret += bitmap_weight((unsigned long *)bitmap,
				longs * BITS_PER_LONG);
		bytes -= longs * sizeof(long);
		bitmap += longs * sizeof(long);
	}
	for (; bytes > 0; bytes--, bitmap++)
		ret += hweight8(*bitmap);

	return ret;
}

// Explicit LCG (same constants as bench/diff_base64.c).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

#define MAXLEN 200

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	// Over-allocate and offset the start within a larger buffer so
	// `bitmap`'s initial alignment (relative to sizeof(long)) varies
	// across iterations, same as real callers with arbitrary pointers.
	unsigned char storage[MAXLEN + 16];

	for (long i = 0; i < n; i++) {
		int offset = lcg_next() % 8; // vary alignment 0..7
		int len = lcg_next() % (MAXLEN - offset);
		for (int k = 0; k < len; k++) storage[offset + k] = (unsigned char)lcg_next();

		size_t w = memweight(storage + offset, (size_t)len);
		printf("weight,%d,%d,%zu\n", offset, len, w);
	}
	return 0;
}
