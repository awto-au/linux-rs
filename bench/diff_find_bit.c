// SPDX-License-Identifier: GPL-2.0-or-later
// Tier-2.5 differential oracle: C original vs Rust translation, find_bit.
// Reference extracted from lib/find_bit.c (v7.1); kept byte-identical
// (the FIND_FIRST_BIT/FIND_NEXT_BIT/FIND_NTH_BIT macros and their C
// instantiations), host longs standing in for `unsigned long`.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned long ul;
#define BITS_PER_LONG (sizeof(ul) * 8)

#define min(a, b) ((a) < (b) ? (a) : (b))
#define BITMAP_FIRST_WORD_MASK(start) (~0UL << ((start) & (BITS_PER_LONG - 1)))
#define BITMAP_LAST_WORD_MASK(nbits) (~0UL >> (-(nbits) & (BITS_PER_LONG - 1)))

static unsigned int hweight_long(ul w)
{
	unsigned int c = 0;
	while (w) { c += w & 1; w >>= 1; }
	return c;
}

static unsigned int fns(ul word, unsigned int n)
{
	while (word && n--)
		word &= word - 1;
	return word ? __builtin_ctzl(word) : BITS_PER_LONG;
}

#define FIND_FIRST_BIT(FETCH, MUNGE, size)					\
({										\
	ul idx, val, sz = (size);						\
	for (idx = 0; idx * BITS_PER_LONG < sz; idx++) {			\
		val = (FETCH);							\
		if (val) {							\
			sz = min(idx * BITS_PER_LONG + __builtin_ctzl(MUNGE(val)), sz); \
			break;							\
		}								\
	}									\
	sz;									\
})

#define FIND_NEXT_BIT(FETCH, MUNGE, size, start)				\
({										\
	ul mask, idx, tmp, sz = (size), __start = (start);			\
	if (__start >= sz)							\
		goto out;							\
	mask = MUNGE(BITMAP_FIRST_WORD_MASK(__start));				\
	idx = __start / BITS_PER_LONG;						\
	for (tmp = (FETCH) & mask; !tmp; tmp = (FETCH)) {			\
		if ((idx + 1) * BITS_PER_LONG >= sz)				\
			goto out;						\
		idx++;								\
	}									\
	sz = min(idx * BITS_PER_LONG + __builtin_ctzl(MUNGE(tmp)), sz);	\
out:										\
	sz;									\
})

#define FIND_NTH_BIT(FETCH, size, num)						\
({										\
	ul sz = (size), nr = (num), idx, w, tmp = 0;				\
	for (idx = 0; (idx + 1) * BITS_PER_LONG <= sz; idx++) {		\
		if (idx * BITS_PER_LONG + nr >= sz)				\
			goto out;						\
		tmp = (FETCH);							\
		w = hweight_long(tmp);						\
		if (w > nr)							\
			goto found;						\
		nr -= w;							\
	}									\
	if (sz % BITS_PER_LONG)							\
		tmp = (FETCH) & BITMAP_LAST_WORD_MASK(sz);			\
found:										\
	sz = idx * BITS_PER_LONG + fns(tmp, nr);				\
out:										\
	sz;									\
})

#define NOP(x) (x)

static ul _find_first_bit(const ul *addr, ul size)
{ return FIND_FIRST_BIT(addr[idx], NOP, size); }

static ul _find_first_and_bit(const ul *addr1, const ul *addr2, ul size)
{ return FIND_FIRST_BIT(addr1[idx] & addr2[idx], NOP, size); }

static ul _find_first_andnot_bit(const ul *addr1, const ul *addr2, ul size)
{ return FIND_FIRST_BIT(addr1[idx] & ~addr2[idx], NOP, size); }

static ul _find_first_and_and_bit(const ul *addr1, const ul *addr2, const ul *addr3, ul size)
{ return FIND_FIRST_BIT(addr1[idx] & addr2[idx] & addr3[idx], NOP, size); }

static ul _find_first_zero_bit(const ul *addr, ul size)
{ return FIND_FIRST_BIT(~addr[idx], NOP, size); }

static ul _find_next_bit(const ul *addr, ul nbits, ul start)
{ return FIND_NEXT_BIT(addr[idx], NOP, nbits, start); }

static ul _find_next_and_bit(const ul *addr1, const ul *addr2, ul nbits, ul start)
{ return FIND_NEXT_BIT(addr1[idx] & addr2[idx], NOP, nbits, start); }

static ul _find_next_andnot_bit(const ul *addr1, const ul *addr2, ul nbits, ul start)
{ return FIND_NEXT_BIT(addr1[idx] & ~addr2[idx], NOP, nbits, start); }

static ul _find_next_or_bit(const ul *addr1, const ul *addr2, ul nbits, ul start)
{ return FIND_NEXT_BIT(addr1[idx] | addr2[idx], NOP, nbits, start); }

static ul _find_next_zero_bit(const ul *addr, ul nbits, ul start)
{ return FIND_NEXT_BIT(~addr[idx], NOP, nbits, start); }

static ul __find_nth_bit(const ul *addr, ul size, ul n)
{ return FIND_NTH_BIT(addr[idx], size, n); }

static ul __find_nth_and_bit(const ul *addr1, const ul *addr2, ul size, ul n)
{ return FIND_NTH_BIT(addr1[idx] & addr2[idx], size, n); }

static ul __find_nth_and_andnot_bit(const ul *addr1, const ul *addr2, const ul *addr3, ul size, ul n)
{ return FIND_NTH_BIT(addr1[idx] & addr2[idx] & ~addr3[idx], size, n); }

static ul _find_last_bit(const ul *addr, ul size)
{
	if (size) {
		ul val = BITMAP_LAST_WORD_MASK(size);
		ul idx = (size - 1) / BITS_PER_LONG;
		do {
			val &= addr[idx];
			if (val)
				return idx * BITS_PER_LONG + (BITS_PER_LONG - 1 - __builtin_clzl(val));
			val = ~0ul;
		} while (idx--);
	}
	return size;
}

// bitmap_get_value8(map, start) == bitmap_read(map, start, 8), from
// <linux/bitmap.h> (static __always_inline, specialised to nbits=8 here).
static ul bitmap_get_value8(const ul *map, ul start)
{
	size_t index = start / BITS_PER_LONG;
	ul offset = start % BITS_PER_LONG;
	ul space = BITS_PER_LONG - offset;

	if (space >= 8)
		return (map[index] >> offset) & BITMAP_LAST_WORD_MASK(8);

	ul value_low = map[index] & BITMAP_FIRST_WORD_MASK(start);
	ul value_high = map[index + 1] & BITMAP_LAST_WORD_MASK(start + 8);
	return (value_low >> offset) | (value_high << space);
}

static ul find_next_clump8(ul *clump, const ul *addr, ul size, ul offset)
{
	offset = _find_next_bit(addr, size, offset);
	if (offset == size)
		return size;
	offset = offset & ~(ul)7; // round_down(offset, 8)
	*clump = bitmap_get_value8(addr, offset);
	return offset;
}

// Explicit LCG (same constants as bench/diff_base64.c) — the shared
// project convention for reproducible cross-language input streams.
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

#define NWORDS 6
#define NBITS (NWORDS * BITS_PER_LONG)

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 424242;

	for (long i = 0; i < n; i++) {
		ul a[NWORDS], b[NWORDS], c[NWORDS];
		for (int k = 0; k < NWORDS; k++) {
			// Bias toward sparse words sometimes, dense other times,
			// to exercise both the "found in this word" and
			// "skip empty words" branches.
			uint32_t r = lcg_next();
			a[k] = (r % 4 == 0) ? 0 : ((ul)r << 32 | lcg_next());
			b[k] = (lcg_next() % 4 == 0) ? 0 : ((ul)lcg_next() << 32 | lcg_next());
			c[k] = (lcg_next() % 4 == 0) ? 0 : ((ul)lcg_next() << 32 | lcg_next());
		}
		ul size = 1 + (lcg_next() % (NBITS - 1)); // 1..NBITS-1, exercises partial last word
		ul start = lcg_next() % (size + 2); // sometimes >= size
		ul n_bit = lcg_next() % (size + 2);

		printf("first,%lu\n", _find_first_bit(a, size));
		printf("first_and,%lu\n", _find_first_and_bit(a, b, size));
		printf("first_andnot,%lu\n", _find_first_andnot_bit(a, b, size));
		printf("first_and_and,%lu\n", _find_first_and_and_bit(a, b, c, size));
		printf("first_zero,%lu\n", _find_first_zero_bit(a, size));
		printf("next,%lu\n", _find_next_bit(a, size, start));
		printf("next_and,%lu\n", _find_next_and_bit(a, b, size, start));
		printf("next_andnot,%lu\n", _find_next_andnot_bit(a, b, size, start));
		printf("next_or,%lu\n", _find_next_or_bit(a, b, size, start));
		printf("next_zero,%lu\n", _find_next_zero_bit(a, size, start));
		printf("nth,%lu\n", __find_nth_bit(a, size, n_bit));
		printf("nth_and,%lu\n", __find_nth_and_bit(a, b, size, n_bit));
		printf("nth_and_andnot,%lu\n", __find_nth_and_andnot_bit(a, b, c, size, n_bit));
		printf("last,%lu\n", _find_last_bit(a, size));

		ul clump = 0xdeadbeef;
		ul clump_off = find_next_clump8(&clump, a, size, start);
		printf("clump8,%lu,%lu\n", clump_off, clump_off == size ? 0 : clump);
	}
	return 0;
}
