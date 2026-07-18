// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation,
// strncpy_from_user. Isolates the fault-free word-at-a-time
// scanning/copying arithmetic from lib/strncpy_from_user.c -- the real
// unsafe_get_user() fault path can't be triggered portably from a host
// program (no way to cause a real page fault at a chosen address and
// resume via extable outside the kernel), so this oracle only exercises
// do_strncpy_from_user()'s arithmetic with `max` always large enough
// that no fault occurs; the fault path itself is inspection-verified +
// boot-verified only, same as strnlen_user's landing (see this file's
// sibling in the same commit, and lib/strnlen_user_rs.rs's module doc
// for the identical constraint).
//
// Faithfully reproduces both loops (word-at-a-time + byte-at-a-time
// fallback) and the IS_UNALIGNED gate exactly as
// lib/strncpy_from_user.c defines them for this build
// (CONFIG_HAVE_EFFICIENT_UNALIGNED_ACCESS unset -- see
// lib/strncpy_from_user_rs.rs's module doc), so alignment-driven
// fallback is genuinely exercised, not skipped.
//
// do_strncpy_from_user()'s -EFAULT return has TWO distinct triggers in
// the real kernel: (a) a genuine unsafe_get_user() page fault (the
// `goto efault`/`goto byte_at_a_time` targets) -- NOT reproducible on
// a host program, inspection- and boot-verified only; and (b) the
// pure-arithmetic "hit max, but max < count" post-loop check
// (`if (res >= count) return res; ... return -EFAULT;`), which needs
// no real fault at all -- just max < count, driven entirely by the
// address-space-limit truncation the caller (strncpy_from_user) does
// before calling in. This oracle deliberately drives max < count
// (mode 5 below) to exercise (b) for real, in addition to the
// count<=0 / truncation / exact-fit / full-copy classes.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned long ul;
#define EFAULT 14

#define IS_UNALIGNED(src, dst) (((long)(dst) | (long)(src)) & (sizeof(long) - 1))

#define ONE_BITS (~0ul / 0xff)
#define HIGH_BITS (ONE_BITS * 0x80)
static inline ul has_zero(ul val, ul *bits)
{
	ul mask = ((val - ONE_BITS) & ~val) & HIGH_BITS;
	*bits = mask;
	return mask;
}
static inline ul create_zero_mask(ul bits)
{
	bits = (bits - 1) & ~bits;
	return bits >> 7;
}
static inline int my_fls64(uint64_t x)
{
	if (x == 0) return 0;
	return 64 - __builtin_clzll(x);
}
static inline ul find_zero(ul mask) { return my_fls64(mask) >> 3; }
static inline ul zero_bytemask(ul mask) { return mask; } // riscv identity

// do_strncpy_from_user, byte-replicated from lib/strncpy_from_user.c.
// `src`/`dst` are plain host pointers here (no real userspace fault
// boundary); `max` is always chosen by the driver loop below to be
// >= the generated string's length + 1, so the byte_at_a_time loop's
// `max` exhaustion path and the final -EFAULT branch are exercised via
// the COUNT-vs-length boundary sweep (max==count in all cases below),
// never via a genuine out-of-bounds read.
static long my_do_strncpy_from_user(char *dst, const char *src, unsigned long count, unsigned long max)
{
	unsigned long res = 0;

	if (IS_UNALIGNED(src, dst))
		goto byte_at_a_time;

	while (max >= sizeof(ul)) {
		ul c, data, mask, bits;

		memcpy(&c, src + res, sizeof(ul));

		if (has_zero(c, &bits)) {
			data = create_zero_mask(bits);
			mask = zero_bytemask(data);
			ul masked = c & mask;
			memcpy(dst + res, &masked, sizeof(ul));
			return res + find_zero(data);
		}

		memcpy(dst + res, &c, sizeof(ul));

		res += sizeof(ul);
		max -= sizeof(ul);
	}

byte_at_a_time:
	while (max) {
		char c = src[res];
		dst[res] = c;
		if (!c)
			return res;
		res++;
		max--;
	}

	if (res >= count)
		return res;

	return -EFAULT;
}

// strncpy_from_user, minus might_fault/should_fail_usercopy/
// kasan_check_write/check_object_size (all no-ops for this build, see
// lib/strncpy_from_user_rs.rs's module doc) and minus the
// TASK_SIZE_MAX/untagged_addr address-space-limit dance (no real
// userspace address space on the host -- max is passed in directly by
// the test driver, exactly mirroring "src_addr < max_addr" always
// holding and max already truncated to count, which is the common
// case this oracle targets).
static long my_strncpy_from_user(char *dst, const char *src, long count, unsigned long max)
{
	if (count <= 0)
		return 0;
	return my_do_strncpy_from_user(dst, src, (unsigned long)count, max);
}

// Explicit LCG (same constants as bench/diff_base64.c / diff_string.c).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

static const char ALPHABET[] = "abcXYZ .";
static void gen_str(char *buf, int maxlen)
{
	int len = 1 + lcg_next() % (maxlen - 1);
	for (int i = 0; i < len; i++) buf[i] = ALPHABET[lcg_next() % (sizeof(ALPHABET) - 1)];
	buf[len] = 0;
}

#define BUFLEN 96

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 424242;

	// Backing arrays deliberately oversized vs BUFLEN and offset by a
	// runtime-chosen byte so src/dst alignment varies across cases --
	// exercises IS_UNALIGNED's real branch, not just the aligned arm.
	char src_backing[BUFLEN + 16];
	char dst_backing[BUFLEN + 16];

	for (long i = 0; i < n; i++) {
		int src_off = lcg_next() % 8;
		int dst_off = lcg_next() % 8;
		char *src = src_backing + src_off;
		char *dst = dst_backing + dst_off;

		memset(src_backing, 0, sizeof(src_backing));
		memset(dst_backing, 0x55, sizeof(dst_backing));

		gen_str(src, BUFLEN - 16);
		size_t slen = strlen(src);

		// count sweeps across, below, at, and above slen+1 (the NUL
		// position) -- the exact boundary sweep the task calls for:
		// alignment x NUL-position x count-boundary.
		long count;
		int mode = lcg_next() % 6;
		switch (mode) {
		case 0: count = 0; break;                              // count<=0 edge
		case 1: count = -(long)(lcg_next() % 4) - 1; break;     // negative count
		case 2: count = (long)(lcg_next() % (slen + 1)); break; // < strlen+1: truncation
		case 3: count = (long)(slen + 1); break;                // exact boundary
		case 4: count = (long)(slen + 1 + lcg_next() % 8); break; // > strlen+1
		default: count = (long)(slen + 1 + lcg_next() % 8); break; // mode 5: max<count below
		}

		// max == count for every mode except 5, which deliberately
		// sets max < count (a real "hit the address-space budget
		// before satisfying count" case -- pure arithmetic, see file
		// header point (b)) to exercise the non-fault -EFAULT return.
		unsigned long max = count > 0 ? (unsigned long)count : 0;
		if (mode == 5 && max > 0)
			max = lcg_next() % max; // strictly less than count

		long r = my_strncpy_from_user(dst, src, count, max);

		printf("strncpy_from_user,%s,%ld,%ld,[%.*s]\n",
		       src, count, r, r > 0 ? (int)r : 0, r > 0 ? dst : "");
	}
	return 0;
}
