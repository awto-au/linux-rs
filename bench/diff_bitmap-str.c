// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, bitmap-str.
// Reference extracted from lib/bitmap-str.c (v7.1); kept byte-identical
// for the translated subset (bitmap_parselist, bitmap_parse, and their
// static string-parsing helpers — the *_user/print_* family stays C-
// only, not exercised here).
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned long ul;
#define BITS_PER_LONG (sizeof(ul) * 8)
#define BITS_TO_LONGS(nr) (((nr) + BITS_PER_LONG - 1) / BITS_PER_LONG)
#define BITS_TO_U32(nr) (((nr) + 31) / 32)
#define EINVAL 22
#define ERANGE 34
#define EOVERFLOW 75
#define MAX_ERRNO 4095

static void *err_ptr(long e) { return (void *)e; }
static long ptr_err(const void *p) { return (long)p; }
static int is_err(const void *p) { return (unsigned long)p >= (unsigned long)-MAX_ERRNO; }

static int my_hex_to_bin(unsigned char ch)
{
	unsigned char cu = ch & 0xdf;
	return -1 +
	       ((ch - '0' + 1) & (unsigned)((ch - '9' - 1) & ('0' - 1 - ch)) >> 8) +
	       ((cu - 'A' + 11) & (unsigned)((cu - 'F' - 1) & ('A' - 1 - cu)) >> 8);
}

// _parse_integer (lib/kstrtox.c, already validated in bench/diff_kstrtox.c)
static unsigned int my_parse_integer(const char *s, unsigned int base, uint64_t *p)
{
	uint64_t res = 0;
	unsigned int rv = 0;
	int max_chars = 2147483647;
	while (max_chars--) {
		unsigned int c = (unsigned char)*s;
		unsigned int lc = c | 0x20;
		unsigned int val;
		if ('0' <= c && c <= '9') val = c - '0';
		else if ('a' <= lc && lc <= 'f') val = lc - 'a' + 10;
		else break;
		if (val >= base) break;
		if (res & (~0ull << 60)) {
			if (res > (UINT64_MAX - val) / base) rv |= (1U << 31);
		}
		res = res * base + val;
		rv++;
		s++;
	}
	*p = res;
	return rv;
}

static void my_bitmap_zero(ul *dst, unsigned int nbits)
{ memset(dst, 0, BITS_TO_LONGS(nbits) * sizeof(ul)); }

static void my_bitmap_set(ul *map, unsigned int start, int len)
{
	ul *p = map + start / BITS_PER_LONG;
	const unsigned int size = start + len;
	int bits_to_set = BITS_PER_LONG - (start % BITS_PER_LONG);
	ul mask_to_set = ~0UL << (start & (BITS_PER_LONG - 1));
	while (len - bits_to_set >= 0) {
		*p |= mask_to_set;
		len -= bits_to_set;
		bits_to_set = BITS_PER_LONG;
		mask_to_set = ~0UL;
		p++;
	}
	if (len) {
		mask_to_set &= (~0UL >> (-size & (BITS_PER_LONG - 1)));
		*p |= mask_to_set;
	}
}

static void my_bitmap_clear(ul *map, unsigned int start, int len)
{
	ul *p = map + start / BITS_PER_LONG;
	const unsigned int size = start + len;
	int bits_to_clear = BITS_PER_LONG - (start % BITS_PER_LONG);
	ul mask_to_clear = ~0UL << (start & (BITS_PER_LONG - 1));
	while (len - bits_to_clear >= 0) {
		*p &= ~mask_to_clear;
		len -= bits_to_clear;
		bits_to_clear = BITS_PER_LONG;
		mask_to_clear = ~0UL;
		p++;
	}
	if (len) {
		mask_to_clear &= (~0UL >> (-size & (BITS_PER_LONG - 1)));
		*p &= ~mask_to_clear;
	}
}

static ul my_find_next_bit(const ul *addr, ul size, ul offset)
{
	for (ul i = offset; i < size; i++)
		if (addr[i / BITS_PER_LONG] & (1UL << (i % BITS_PER_LONG))) return i;
	return size;
}

static int my_end_of_str(char c) { return c == '\0' || c == '\n'; }
static int my_end_of_region_(char c) { return isspace((unsigned char)c) || c == ','; }
static int my_end_of_region(char c) { return my_end_of_region_(c) || my_end_of_str(c); }

static const char *my_bitmap_getnum(const char *str, unsigned int *num, unsigned int lastbit)
{
	uint64_t n;
	unsigned int len;
	if (str[0] == 'N') { *num = lastbit; return str + 1; }
	len = my_parse_integer(str, 10, &n);
	if (!len) return err_ptr(-EINVAL);
	if (len & (1U << 31) || n != (unsigned int)n) return err_ptr(-EOVERFLOW);
	*num = n;
	return str + len;
}

static const char *my_bitmap_find_region(const char *str)
{
	while (my_end_of_region_(*str)) str++;
	return my_end_of_str(*str) ? NULL : str;
}

static const char *my_bitmap_find_region_reverse(const char *start, const char *end)
{
	while (start <= end && my_end_of_region_(*end)) end--;
	return end;
}

struct region { unsigned int start, off, group_len, end, nbits; };

static const char *my_bitmap_parse_region(const char *str, struct region *r)
{
	unsigned int lastbit = r->nbits - 1;
	if (!strncasecmp(str, "all", 3)) {
		r->start = 0; r->end = lastbit; str += 3;
		goto check_pattern;
	}
	str = my_bitmap_getnum(str, &r->start, lastbit);
	if (is_err(str)) return str;
	if (my_end_of_region(*str)) goto no_end;
	if (*str != '-') return err_ptr(-EINVAL);
	str = my_bitmap_getnum(str + 1, &r->end, lastbit);
	if (is_err(str)) return str;
check_pattern:
	if (my_end_of_region(*str)) goto no_pattern;
	if (*str != ':') return err_ptr(-EINVAL);
	str = my_bitmap_getnum(str + 1, &r->off, lastbit);
	if (is_err(str)) return str;
	if (*str != '/') return err_ptr(-EINVAL);
	return my_bitmap_getnum(str + 1, &r->group_len, lastbit);
no_end:
	r->end = r->start;
no_pattern:
	r->off = r->end + 1;
	r->group_len = r->end + 1;
	return my_end_of_str(*str) ? NULL : str;
}

static void my_bitmap_set_region(const struct region *r, ul *bitmap)
{
	unsigned int start;
	for (start = r->start; start <= r->end; start += r->group_len) {
		unsigned int l = r->end - start + 1;
		if (r->off < l) l = r->off;
		my_bitmap_set(bitmap, start, l);
	}
}

static int my_bitmap_check_region(const struct region *r)
{
	if (r->start > r->end || r->group_len == 0 || r->off > r->group_len) return -EINVAL;
	if (r->end >= r->nbits) return -ERANGE;
	return 0;
}

static int my_bitmap_parselist(const char *buf, ul *maskp, int nmaskbits)
{
	struct region r;
	long ret;
	r.nbits = nmaskbits;
	my_bitmap_zero(maskp, r.nbits);
	while (buf) {
		buf = my_bitmap_find_region(buf);
		if (buf == NULL) return 0;
		buf = my_bitmap_parse_region(buf, &r);
		if (is_err(buf)) return ptr_err(buf);
		ret = my_bitmap_check_region(&r);
		if (ret) return ret;
		my_bitmap_set_region(&r, maskp);
	}
	return 0;
}

static const char *my_bitmap_get_x32_reverse(const char *start, const char *end, uint32_t *num)
{
	uint32_t ret = 0;
	int c, i;
	for (i = 0; i < 32; i += 4) {
		c = my_hex_to_bin((unsigned char)*end--);
		if (c < 0) return err_ptr(-EINVAL);
		ret |= (uint32_t)c << i;
		if (start > end || my_end_of_region_(*end)) goto out;
	}
	if (my_hex_to_bin((unsigned char)*end--) >= 0) return err_ptr(-EOVERFLOW);
out:
	*num = ret;
	return end;
}

static int my_bitmap_parse(const char *start, unsigned int buflen, ul *maskp, int nmaskbits)
{
	const char *nl = memchr(start, '\n', buflen);
	const char *end = (nl ? nl : start + strnlen(start, buflen)) - 1;
	int chunks = BITS_TO_U32(nmaskbits);
	uint32_t *bitmap = (uint32_t *)maskp;
	int chunk;
	for (chunk = 0; ; chunk++) {
		end = my_bitmap_find_region_reverse(start, end);
		if (start > end) break;
		if (!chunks--) return -EOVERFLOW;
		end = my_bitmap_get_x32_reverse(start, end, &bitmap[chunk]);
		if (is_err(end)) return ptr_err(end);
	}
	int unset_bit = (BITS_TO_U32(nmaskbits) - chunks) * 32;
	if (unset_bit < nmaskbits) {
		my_bitmap_clear(maskp, unset_bit, nmaskbits - unset_bit);
		return 0;
	}
	if (my_find_next_bit(maskp, unset_bit, nmaskbits) != (ul)unset_bit) return -EOVERFLOW;
	return 0;
}

// Explicit LCG (same constants as bench/diff_base64.c).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

#define NBITS 256
#define NWORDS (NBITS / 64)

// Generate a plausible bitmap-list string: comma-separated
// numbers/ranges/patterns, occasionally "all" or "N".
static void gen_listbuf(char *buf, int maxlen)
{
	int pos = 0;
	int nterms = 1 + lcg_next() % 5;
	for (int t = 0; t < nterms && pos < maxlen - 20; t++) {
		if (t > 0) buf[pos++] = ',';
		int kind = lcg_next() % 6;
		if (kind == 0) {
			pos += sprintf(buf + pos, "all");
		} else {
			int a = lcg_next() % NBITS;
			if (lcg_next() % 3 == 0 && kind != 5) {
				pos += sprintf(buf + pos, "N");
			} else {
				pos += sprintf(buf + pos, "%d", a);
			}
			if (kind >= 2) {
				int b = lcg_next() % NBITS;
				pos += sprintf(buf + pos, "-%d", b);
				if (kind >= 4) {
					int off = 1 + lcg_next() % 4;
					int grp = off + lcg_next() % 4;
					pos += sprintf(buf + pos, ":%d/%d", off, grp);
				}
			}
		}
	}
	buf[pos] = 0;
}

static void gen_hexbuf(char *buf, int maxlen)
{
	int len = 1 + lcg_next() % (maxlen - 1);
	static const char hexdigits[] = "0123456789abcdefABCDEF";
	int pos = 0;
	for (int i = 0; i < len && pos < maxlen - 1; i++) {
		if (i > 0 && lcg_next() % 8 == 0) buf[pos++] = ',';
		else buf[pos++] = hexdigits[lcg_next() % (sizeof(hexdigits) - 1)];
	}
	buf[pos] = 0;
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	for (long i = 0; i < n; i++) {
		char listbuf[128];
		gen_listbuf(listbuf, sizeof(listbuf) - 1);
		ul mask[NWORDS];
		int r1 = my_bitmap_parselist(listbuf, mask, NBITS);
		printf("parselist,%s,%d,%016lx,%016lx\n", listbuf, r1, mask[0], mask[NWORDS - 1]);

		char hexbuf[80];
		gen_hexbuf(hexbuf, sizeof(hexbuf) - 1);
		ul mask2[NWORDS] = {0};
		int r2 = my_bitmap_parse(hexbuf, (unsigned int)strlen(hexbuf), mask2, NBITS);
		printf("parse,%s,%d,%016lx,%016lx\n", hexbuf, r2, mask2[0], mask2[NWORDS - 1]);
	}
	return 0;
}
