// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, div64.
// Reference extracted from lib/math/div64.c (v7.1); kept byte-identical
// for mul_u64_add_u64_div_u64 (the BITS_PER_ITER==32, no-__int128-fast-
// path arm, matching what this build actually selects on riscv64).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef uint64_t u64;
typedef uint32_t u32;
typedef unsigned long ul;

static inline u64 mul_u32_u32(u32 a, u32 b) { return (u64)a * b; }
static inline u64 add_u64_u32(u64 a, u32 b) { return a + b; }
#define mul_add(a, b, c) add_u64_u32(mul_u32_u32(a, b), c)

static inline u64 mul_u64_u64_add_u64(u64 *p_lo, u64 a, u64 b, u64 c)
{
	u64 x, y, z;
	x = mul_add(a, b, c);
	y = mul_add(a, b >> 32, c >> 32);
	y = add_u64_u32(y, x >> 32);
	z = mul_add(a >> 32, b >> 32, y >> 32);
	y = mul_add(a >> 32, b, y);
	*p_lo = (y << 32) + (u32)x;
	return add_u64_u32(z, y >> 32);
}

#define BITS_PER_ITER 32
#define mul_u64_long_add_u64(p_lo, a, b, c) mul_u64_u64_add_u64(p_lo, a, b, c)
#define add_u64_long(a, b) ((a) + (b))

static inline u64 div64_u64(u64 dividend, u64 divisor) { return dividend / divisor; }

static u64 mul_u64_add_u64_div_u64(u64 a, u64 b, u64 c, u64 d)
{
	ul d_msig, q_digit;
	unsigned int reps, d_z_hi;
	u64 quotient, n_lo, n_hi;
	u32 overflow;

	n_hi = mul_u64_u64_add_u64(&n_lo, a, b, c);

	if (!n_hi)
		return div64_u64(n_lo, d);

	if (n_hi >= d) {
		if (d == 0) {
			// Match the Rust side's black_box: force a genuine
			// runtime UB divide-by-zero via volatile, not
			// compiler-provable-const. This test harness never
			// actually exercises d==0 (see main()'s divisor
			// generation, which always produces d >= 1); this
			// branch exists only for structural parity with the
			// kernel original.
			volatile u64 zero = 0;
			return ~0ULL / zero;
		}
		return ~0ULL;
	}

	d_z_hi = __builtin_clzll(d);
	if (d_z_hi) {
		d <<= d_z_hi;
		n_hi = n_hi << d_z_hi | n_lo >> (64 - d_z_hi);
		n_lo <<= d_z_hi;
	}

	reps = 64 / BITS_PER_ITER;
	if (!(u32)(n_hi >> 32)) {
		reps -= 32 / BITS_PER_ITER;
		n_hi = n_hi << 32 | n_lo >> 32;
		n_lo <<= 32;
	}

	n_lo = ~n_lo;
	n_hi = ~n_hi;

	d_msig = (d >> (64 - BITS_PER_ITER)) + 1;

	quotient = 0;
	while (reps--) {
		q_digit = (unsigned long)(~n_hi >> (64 - 2 * BITS_PER_ITER)) / d_msig;
		overflow = n_hi >> (64 - BITS_PER_ITER);
		n_hi = add_u64_u32(n_hi << BITS_PER_ITER, n_lo >> (64 - BITS_PER_ITER));
		n_lo <<= BITS_PER_ITER;
		overflow += mul_u64_long_add_u64(&n_hi, d, q_digit, n_hi);
		while (overflow < 0xffffffff >> (32 - BITS_PER_ITER)) {
			q_digit++;
			n_hi += d;
			overflow += n_hi < d;
		}
		quotient = add_u64_long(quotient << BITS_PER_ITER, q_digit);
	}

	if ((n_hi + d) > n_hi)
		quotient++;
	return quotient;
}

static u32 iter_div_u64_rem(u64 dividend, u32 divisor, u64 *remainder)
{
	u32 ret = 0;
	while (dividend >= divisor) {
		dividend -= divisor;
		ret++;
	}
	*remainder = dividend;
	return ret;
}

// Explicit LCG (same constants as bench/diff_base64.c).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}
static uint64_t lcg_next64(void)
{
	uint64_t hi = lcg_next();
	uint64_t lo = lcg_next();
	return (hi << 32) | lo;
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	for (long i = 0; i < n; i++) {
		u64 a = lcg_next64();
		u64 b = lcg_next64();
		u64 c = lcg_next64();
		// Keep d >= 1 (d==0 is a deliberate-trap path, not tested here)
		// and skew towards small values so the n_hi>=d overflow path,
		// as well as the ordinary long-division path, both get real
		// coverage (a uniform 64-bit d rarely triggers a*b+c overflow
		// past d).
		u64 d = (lcg_next() % 4 == 0) ? (lcg_next64() | 1) : ((u64)(lcg_next() % 1000000) + 1);

		u64 r1 = mul_u64_add_u64_div_u64(a, b, c, d);
		printf("muladddiv,%llu,%llu,%llu,%llu,%llu\n",
		       (unsigned long long)a, (unsigned long long)b,
		       (unsigned long long)c, (unsigned long long)d,
		       (unsigned long long)r1);

		// iter_div_u64_rem is documented for "dividend not expected to
		// be much bigger than divisor" (repeated-subtraction loop) —
		// a uniformly random 64-bit dividend against a small divisor
		// would need up to ~2^64 iterations. Keep dividend within a
		// bounded multiple of divisor, matching real call sites.
		u32 divisor = (lcg_next() % 1000000) + 1;
		u64 dividend = (u64)divisor * (lcg_next() % 100000) + (lcg_next() % divisor);
		u64 rem;
		u32 q = iter_div_u64_rem(dividend, divisor, &rem);
		printf("iterdiv,%llu,%u,%u,%llu\n",
		       (unsigned long long)dividend, divisor, q,
		       (unsigned long long)rem);
	}
	return 0;
}
