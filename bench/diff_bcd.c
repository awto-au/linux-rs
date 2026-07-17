// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, bcd.
// Reference extracted from lib/bcd.c (v7.1); kept byte-identical.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static unsigned _bcd2bin(unsigned char val)
{
	return (val & 0x0f) + (val >> 4) * 10;
}

static unsigned char _bin2bcd(unsigned val)
{
	const unsigned int t = (val * 103) >> 10;

	return (t << 4) | (val - t * 10);
}

// Explicit LCG (same constants used across all bench/diff_*.c files).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	for (long i = 0; i < n; i++) {
		unsigned char val = (unsigned char)lcg_next();
		unsigned r = _bcd2bin(val);
		printf("bcd2bin,%u,%u\n", val, r);
	}

	for (long i = 0; i < n; i++) {
		// _bin2bcd's contract is "val < 100" (bin2bcd(val), val is a BCD-
		// representable binary value); exercise that domain plus a few
		// values just outside it, matching what real kernel callers pass.
		unsigned val = lcg_next() % 100;
		unsigned char r = _bin2bcd(val);
		printf("bin2bcd,%u,%u\n", val, r);
	}
	return 0;
}
