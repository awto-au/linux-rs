// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, bitmap.
// Reference extracted from lib/bitmap.c (v7.1); kept byte-identical for
// the 27-function translated subset (allocator-touching and
// CONFIG_NUMA-gated functions untranslated, not exercised here).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned long ul;
#define BITS_PER_LONG (sizeof(ul) * 8)
#define BIT_WORD(nr) ((nr) / BITS_PER_LONG)
#define BITS_TO_LONGS(nr) (((nr) + BITS_PER_LONG - 1) / BITS_PER_LONG)
#define BITMAP_FIRST_WORD_MASK(start) (~0UL << ((start) & (BITS_PER_LONG - 1)))
#define BITMAP_LAST_WORD_MASK(nbits) (~0UL >> (-(nbits) & (BITS_PER_LONG - 1)))
#define __ALIGN_MASK(x, mask) (((x) + (mask)) & ~(mask))

static inline unsigned int hweight_long(ul w) { return __builtin_popcountll((uint64_t)w); }

static _Bool my_bitmap_equal(const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{
	unsigned int k, lim = bits/BITS_PER_LONG;
	for (k = 0; k < lim; ++k)
		if (bitmap1[k] != bitmap2[k]) return 0;
	if (bits % BITS_PER_LONG)
		if ((bitmap1[k] ^ bitmap2[k]) & BITMAP_LAST_WORD_MASK(bits)) return 0;
	return 1;
}

static _Bool my_bitmap_or_equal(const ul *bitmap1, const ul *bitmap2, const ul *bitmap3, unsigned int bits)
{
	unsigned int k, lim = bits / BITS_PER_LONG;
	ul tmp;
	for (k = 0; k < lim; ++k)
		if ((bitmap1[k] | bitmap2[k]) != bitmap3[k]) return 0;
	if (!(bits % BITS_PER_LONG)) return 1;
	tmp = (bitmap1[k] | bitmap2[k]) ^ bitmap3[k];
	return (tmp & BITMAP_LAST_WORD_MASK(bits)) == 0;
}

static void my_bitmap_complement(ul *dst, const ul *src, unsigned int bits)
{
	unsigned int k, lim = BITS_TO_LONGS(bits);
	for (k = 0; k < lim; ++k) dst[k] = ~src[k];
}

static void my_bitmap_shift_right(ul *dst, const ul *src, unsigned shift, unsigned nbits)
{
	unsigned k, lim = BITS_TO_LONGS(nbits);
	unsigned off = shift/BITS_PER_LONG, rem = shift % BITS_PER_LONG;
	ul mask = BITMAP_LAST_WORD_MASK(nbits);
	for (k = 0; off + k < lim; ++k) {
		ul upper, lower;
		if (!rem || off + k + 1 >= lim) upper = 0;
		else {
			upper = src[off + k + 1];
			if (off + k + 1 == lim - 1) upper &= mask;
			upper <<= (BITS_PER_LONG - rem);
		}
		lower = src[off + k];
		if (off + k == lim - 1) lower &= mask;
		lower >>= rem;
		dst[k] = lower | upper;
	}
	if (off) memset(&dst[lim - off], 0, off*sizeof(ul));
}

static void my_bitmap_shift_left(ul *dst, const ul *src, unsigned int shift, unsigned int nbits)
{
	int k;
	unsigned int lim = BITS_TO_LONGS(nbits);
	unsigned int off = shift/BITS_PER_LONG, rem = shift % BITS_PER_LONG;
	for (k = lim - off - 1; k >= 0; --k) {
		ul upper, lower;
		if (rem && k > 0) lower = src[k - 1] >> (BITS_PER_LONG - rem);
		else lower = 0;
		upper = src[k] << rem;
		dst[k + off] = lower | upper;
	}
	if (off) memset(dst, 0, off*sizeof(ul));
}

static void my_bitmap_cut(ul *dst, const ul *src, unsigned int first, unsigned int cut, unsigned int nbits)
{
	unsigned int len = BITS_TO_LONGS(nbits);
	ul keep = 0, carry;
	int i;
	if (first % BITS_PER_LONG)
		keep = src[first / BITS_PER_LONG] & (~0UL >> (BITS_PER_LONG - first % BITS_PER_LONG));
	memmove(dst, src, len * sizeof(*dst));
	while (cut--) {
		for (i = first / BITS_PER_LONG; i < (int)len; i++) {
			if (i < (int)len - 1) carry = dst[i + 1] & 1UL;
			else carry = 0;
			dst[i] = (dst[i] >> 1) | (carry << (BITS_PER_LONG - 1));
		}
	}
	dst[first / BITS_PER_LONG] &= ~0UL << (first % BITS_PER_LONG);
	dst[first / BITS_PER_LONG] |= keep;
}

static _Bool my_bitmap_and(ul *dst, const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{
	unsigned int k, lim = bits/BITS_PER_LONG;
	ul result = 0;
	for (k = 0; k < lim; k++) result |= (dst[k] = bitmap1[k] & bitmap2[k]);
	if (bits % BITS_PER_LONG) result |= (dst[k] = bitmap1[k] & bitmap2[k] & BITMAP_LAST_WORD_MASK(bits));
	return result != 0;
}

static void my_bitmap_or(ul *dst, const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{
	unsigned int k, nr = BITS_TO_LONGS(bits);
	for (k = 0; k < nr; k++) dst[k] = bitmap1[k] | bitmap2[k];
}

static void my_bitmap_xor(ul *dst, const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{
	unsigned int k, nr = BITS_TO_LONGS(bits);
	for (k = 0; k < nr; k++) dst[k] = bitmap1[k] ^ bitmap2[k];
}

static _Bool my_bitmap_andnot(ul *dst, const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{
	unsigned int k, lim = bits/BITS_PER_LONG;
	ul result = 0;
	for (k = 0; k < lim; k++) result |= (dst[k] = bitmap1[k] & ~bitmap2[k]);
	if (bits % BITS_PER_LONG) result |= (dst[k] = bitmap1[k] & ~bitmap2[k] & BITMAP_LAST_WORD_MASK(bits));
	return result != 0;
}

static void my_bitmap_replace(ul *dst, const ul *old, const ul *new_, const ul *mask, unsigned int nbits)
{
	unsigned int k, nr = BITS_TO_LONGS(nbits);
	for (k = 0; k < nr; k++) dst[k] = (old[k] & ~mask[k]) | (new_[k] & mask[k]);
}

static _Bool my_bitmap_intersects(const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{
	unsigned int k, lim = bits/BITS_PER_LONG;
	for (k = 0; k < lim; ++k) if (bitmap1[k] & bitmap2[k]) return 1;
	if (bits % BITS_PER_LONG) if ((bitmap1[k] & bitmap2[k]) & BITMAP_LAST_WORD_MASK(bits)) return 1;
	return 0;
}

static _Bool my_bitmap_subset(const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{
	unsigned int k, lim = bits/BITS_PER_LONG;
	for (k = 0; k < lim; ++k) if (bitmap1[k] & ~bitmap2[k]) return 0;
	if (bits % BITS_PER_LONG) if ((bitmap1[k] & ~bitmap2[k]) & BITMAP_LAST_WORD_MASK(bits)) return 0;
	return 1;
}

#define BITMAP_WEIGHT(FETCH, bits) ({ \
	unsigned int __bits = (bits), idx, w = 0; \
	for (idx = 0; idx < __bits / BITS_PER_LONG; idx++) w += hweight_long(FETCH); \
	if (__bits % BITS_PER_LONG) w += hweight_long((FETCH) & BITMAP_LAST_WORD_MASK(__bits)); \
	w; })

static unsigned int my_bitmap_weight(const ul *bitmap, unsigned int bits)
{ return BITMAP_WEIGHT(bitmap[idx], bits); }
static unsigned int my_bitmap_weight_and(const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{ return BITMAP_WEIGHT(bitmap1[idx] & bitmap2[idx], bits); }
static unsigned int my_bitmap_weight_andnot(const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{ return BITMAP_WEIGHT(bitmap1[idx] & ~bitmap2[idx], bits); }
static unsigned int my_bitmap_weighted_or(ul *dst, const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{ return BITMAP_WEIGHT(({dst[idx] = bitmap1[idx] | bitmap2[idx]; dst[idx]; }), bits); }
static unsigned int my_bitmap_weighted_xor(ul *dst, const ul *bitmap1, const ul *bitmap2, unsigned int bits)
{ return BITMAP_WEIGHT(({dst[idx] = bitmap1[idx] ^ bitmap2[idx]; dst[idx]; }), bits); }

static void my_bitmap_set(ul *map, unsigned int start, int len)
{
	ul *p = map + BIT_WORD(start);
	const unsigned int size = start + len;
	int bits_to_set = BITS_PER_LONG - (start % BITS_PER_LONG);
	ul mask_to_set = BITMAP_FIRST_WORD_MASK(start);
	while (len - bits_to_set >= 0) {
		*p |= mask_to_set;
		len -= bits_to_set;
		bits_to_set = BITS_PER_LONG;
		mask_to_set = ~0UL;
		p++;
	}
	if (len) {
		mask_to_set &= BITMAP_LAST_WORD_MASK(size);
		*p |= mask_to_set;
	}
}

static void my_bitmap_clear(ul *map, unsigned int start, int len)
{
	ul *p = map + BIT_WORD(start);
	const unsigned int size = start + len;
	int bits_to_clear = BITS_PER_LONG - (start % BITS_PER_LONG);
	ul mask_to_clear = BITMAP_FIRST_WORD_MASK(start);
	while (len - bits_to_clear >= 0) {
		*p &= ~mask_to_clear;
		len -= bits_to_clear;
		bits_to_clear = BITS_PER_LONG;
		mask_to_clear = ~0UL;
		p++;
	}
	if (len) {
		mask_to_clear &= BITMAP_LAST_WORD_MASK(size);
		*p &= ~mask_to_clear;
	}
}

static _Bool my_test_bit(unsigned int nr, const ul *addr)
{ return 1UL & (addr[BIT_WORD(nr)] >> (nr & (BITS_PER_LONG-1))); }
static void my_set_bit(unsigned int nr, ul *addr)
{ addr[BIT_WORD(nr)] |= 1UL << (nr & (BITS_PER_LONG - 1)); }
static unsigned long my_find_next_zero_bit(const ul *addr, unsigned long size, unsigned long offset)
{
	for (unsigned long i = offset; i < size; i++)
		if (!my_test_bit(i, addr)) return i;
	return size;
}
static unsigned long my_find_next_bit(const ul *addr, unsigned long size, unsigned long offset)
{
	for (unsigned long i = offset; i < size; i++)
		if (my_test_bit(i, addr)) return i;
	return size;
}
static unsigned long my_find_first_bit(const ul *addr, unsigned long size)
{ return my_find_next_bit(addr, size, 0); }
static unsigned long my_find_nth_bit(const ul *addr, unsigned long size, unsigned long n)
{
	unsigned long count = 0;
	for (unsigned long i = 0; i < size; i++) {
		if (my_test_bit(i, addr)) {
			if (count == n) return i;
			count++;
		}
	}
	return size;
}

static unsigned long my_bitmap_find_next_zero_area_off(ul *map, unsigned long size, unsigned long start,
		unsigned int nr, unsigned long align_mask, unsigned long align_offset)
{
	unsigned long index, end, i;
again:
	index = my_find_next_zero_bit(map, size, start);
	index = __ALIGN_MASK(index + align_offset, align_mask) - align_offset;
	end = index + nr;
	if (end > size) return end;
	i = my_find_next_bit(map, end, index);
	if (i < end) { start = i + 1; goto again; }
	return index;
}

static int my_bitmap_pos_to_ord(const ul *buf, unsigned int pos, unsigned int nbits)
{
	if (pos >= nbits || !my_test_bit(pos, buf)) return -1;
	return my_bitmap_weight(buf, pos);
}

static void my_bitmap_zero(ul *dst, unsigned int nbits)
{ memset(dst, 0, BITS_TO_LONGS(nbits) * sizeof(ul)); }

static void my_bitmap_remap(ul *dst, const ul *src, const ul *old, const ul *new_, unsigned int nbits)
{
	unsigned int oldbit, w;
	if (dst == src) return;
	my_bitmap_zero(dst, nbits);
	w = my_bitmap_weight(new_, nbits);
	for (oldbit = my_find_first_bit(src, nbits); oldbit < nbits; oldbit = my_find_next_bit(src, nbits, oldbit + 1)) {
		int n = my_bitmap_pos_to_ord(old, oldbit, nbits);
		if (n < 0 || w == 0) my_set_bit(oldbit, dst);
		else my_set_bit(my_find_nth_bit(new_, nbits, n % w), dst);
	}
}

static int my_bitmap_bitremap(int oldbit, const ul *old, const ul *new_, int bits)
{
	int w = my_bitmap_weight(new_, bits);
	int n = my_bitmap_pos_to_ord(old, oldbit, bits);
	if (n < 0 || w == 0) return oldbit;
	else return my_find_nth_bit(new_, bits, n % w);
}

static void my_bitmap_from_arr32(ul *bitmap, const uint32_t *buf, unsigned int nbits)
{
	unsigned int i, halfwords;
	halfwords = (nbits + 31) / 32;
	for (i = 0; i < halfwords; i++) {
		bitmap[i/2] = (ul) buf[i];
		if (++i < halfwords) bitmap[i/2] |= ((ul) buf[i]) << 32;
	}
	if (nbits % BITS_PER_LONG) bitmap[(halfwords - 1) / 2] &= BITMAP_LAST_WORD_MASK(nbits);
}

static void my_bitmap_to_arr32(uint32_t *buf, const ul *bitmap, unsigned int nbits)
{
	unsigned int i, halfwords;
	halfwords = (nbits + 31) / 32;
	for (i = 0; i < halfwords; i++) {
		buf[i] = (uint32_t) (bitmap[i/2] & UINT32_MAX);
		if (++i < halfwords) buf[i] = (uint32_t) (bitmap[i/2] >> 32);
	}
	if (nbits % BITS_PER_LONG) buf[halfwords - 1] &= (uint32_t) (UINT32_MAX >> ((-nbits) & 31));
}

// Explicit LCG (same constants as bench/diff_base64.c).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

#define NWORDS 8
#define NBITS_MAX (NWORDS * 64)

static void gen_bitmap(ul *b, unsigned int nbits)
{
	unsigned int i;
	for (i = 0; i < NWORDS; i++) b[i] = ((uint64_t)lcg_next() << 32) | lcg_next();
	if (nbits % 64) b[nbits/64] &= BITMAP_LAST_WORD_MASK(nbits);
	for (i = nbits/64 + (nbits % 64 ? 1 : 0); i < NWORDS; i++) b[i] = 0;
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	for (long iter = 0; iter < n; iter++) {
		unsigned int nbits = 1 + lcg_next() % (NBITS_MAX - 1);
		ul a[NWORDS], b[NWORDS], c[NWORDS], dst[NWORDS];
		gen_bitmap(a, nbits);
		gen_bitmap(b, nbits);
		gen_bitmap(c, nbits);

		printf("equal,%u,%d\n", nbits, my_bitmap_equal(a, b, nbits));
		printf("or_equal,%u,%d\n", nbits, my_bitmap_or_equal(a, b, c, nbits));

		memcpy(dst, a, sizeof(dst));
		my_bitmap_complement(dst, a, nbits);
		printf("complement,%u,%016lx,%016lx\n", nbits, dst[0], dst[NWORDS-1]);

		unsigned shift = lcg_next() % nbits;
		memset(dst, 0, sizeof(dst));
		my_bitmap_shift_right(dst, a, shift, nbits);
		printf("shr,%u,%u,%016lx,%016lx\n", nbits, shift, dst[0], dst[NWORDS-1]);

		memset(dst, 0, sizeof(dst));
		my_bitmap_shift_left(dst, a, shift, nbits);
		printf("shl,%u,%u,%016lx,%016lx\n", nbits, shift, dst[0], dst[NWORDS-1]);

		unsigned int first = lcg_next() % nbits;
		unsigned int cut = lcg_next() % (nbits - first + 1);
		memcpy(dst, a, sizeof(dst));
		my_bitmap_cut(dst, a, first, cut, nbits);
		printf("cut,%u,%u,%u,%016lx,%016lx\n", nbits, first, cut, dst[0], dst[NWORDS-1]);

		memset(dst, 0, sizeof(dst));
		printf("and,%u,%d,%016lx\n", nbits, my_bitmap_and(dst, a, b, nbits), dst[0]);
		my_bitmap_or(dst, a, b, nbits);
		printf("or,%u,%016lx\n", nbits, dst[0]);
		my_bitmap_xor(dst, a, b, nbits);
		printf("xor,%u,%016lx\n", nbits, dst[0]);
		printf("andnot,%u,%d,%016lx\n", nbits, my_bitmap_andnot(dst, a, b, nbits), dst[0]);
		my_bitmap_replace(dst, a, b, c, nbits);
		printf("replace,%u,%016lx\n", nbits, dst[0]);
		printf("intersects,%u,%d\n", nbits, my_bitmap_intersects(a, b, nbits));
		printf("subset,%u,%d\n", nbits, my_bitmap_subset(a, b, nbits));

		printf("weight,%u,%u\n", nbits, my_bitmap_weight(a, nbits));
		printf("weight_and,%u,%u\n", nbits, my_bitmap_weight_and(a, b, nbits));
		printf("weight_andnot,%u,%u\n", nbits, my_bitmap_weight_andnot(a, b, nbits));
		memset(dst, 0, sizeof(dst));
		printf("weighted_or,%u,%u,%016lx\n", nbits, my_bitmap_weighted_or(dst, a, b, nbits), dst[0]);
		memset(dst, 0, sizeof(dst));
		printf("weighted_xor,%u,%u,%016lx\n", nbits, my_bitmap_weighted_xor(dst, a, b, nbits), dst[0]);

		unsigned int sstart = lcg_next() % nbits;
		int slen = lcg_next() % (nbits - sstart + 1);
		memcpy(dst, a, sizeof(dst));
		my_bitmap_set(dst, sstart, slen);
		printf("set,%u,%u,%d,%016lx,%016lx\n", nbits, sstart, slen, dst[0], dst[NWORDS-1]);
		memcpy(dst, a, sizeof(dst));
		my_bitmap_clear(dst, sstart, slen);
		printf("clear,%u,%u,%d,%016lx,%016lx\n", nbits, sstart, slen, dst[0], dst[NWORDS-1]);

		unsigned long zstart = lcg_next() % nbits;
		unsigned int znr = 1 + lcg_next() % 8;
		unsigned long zam = (1UL << (lcg_next() % 4)) - 1;
		unsigned long zao = lcg_next() % 4;
		unsigned long zres = my_bitmap_find_next_zero_area_off(a, nbits, zstart, znr, zam, zao);
		printf("findzeroarea,%u,%lu,%u,%lu,%lu,%lu\n", nbits, zstart, znr, zam, zao, zres);

		memset(dst, 0, sizeof(dst));
		my_bitmap_remap(dst, a, b, c, nbits);
		printf("remap,%u,%016lx,%016lx\n", nbits, dst[0], dst[NWORDS-1]);

		int oldbit = lcg_next() % nbits;
		int rres = my_bitmap_bitremap(oldbit, b, c, (int)nbits);
		printf("bitremap,%u,%d,%d\n", nbits, oldbit, rres);

		uint32_t arr32[NWORDS * 2];
		my_bitmap_to_arr32(arr32, a, nbits);
		uint32_t sum32 = 0;
		for (unsigned int i = 0; i < (nbits + 31) / 32; i++) sum32 ^= arr32[i];
		printf("to_arr32,%u,%08x\n", nbits, sum32);

		memset(dst, 0, sizeof(dst));
		my_bitmap_from_arr32(dst, arr32, nbits);
		printf("from_arr32,%u,%016lx,%016lx\n", nbits, dst[0], dst[NWORDS-1]);
	}
	return 0;
}
