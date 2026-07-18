// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation,
// check_zeroed_user (lib/usercopy.c). Isolates the fault-free
// word-at-a-time zero-scan arithmetic -- the real unsafe_get_user()
// page-fault path can't be triggered portably from a host program (no
// way to cause a real page fault at a chosen address and resume via
// extable outside the kernel), so this oracle only exercises
// check_zeroed_user()'s arithmetic with a real in-bounds host buffer
// (no fault ever possible); the fault path (-EFAULT via err_fault) is
// inspection-verified + boot-verified only, same constraint
// strnlen_user's and strncpy_from_user's landings documented.
//
// Faithfully reproduces the full three-way exit-path shape from
// lib/usercopy.c's check_zeroed_user() for THIS build
// (CONFIG_CPU_BIG_ENDIAN unset -- little-endian aligned_byte_mask, see
// lib/usercopy_rs.rs's module doc):
//   1. size == 0 -> 1 (trivial all-zero, no access-region entered)
//   2. done: reached via mid-loop `goto done` the instant a nonzero
//      word is found (val left untouched, NOT trimmed by the
//      post-loop `size < sizeof(long)` mask) -- exercised by placing
//      a nonzero byte in an early word of an otherwise-longer buffer
//   3. done: reached via natural loop-exit fallthrough, either because
//      size was <= sizeof(long) from the very start, or the loop ran
//      out of size -- exercised by all-zero buffers and short buffers
//      (size 1..sizeof(long)-1), where the post-loop trim IS applied
// Combined with an alignment sweep (0..7 byte offsets) and a
// size-boundary sweep (0, 1..7, sizeof(long), sizeof(long)+1..,
// several words), this exercises every arm of the three-way exit
// contract (0/1) that's reachable without a real fault.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned long ul;
typedef uintptr_t uptr;

#define ALIGNED_BYTE_MASK(n) ((1UL << (8 * (n))) - 1) // little-endian arm

// check_zeroed_user, byte-replicated from lib/usercopy.c, with
// unsafe_get_user()/user_read_access_begin()/_end() replaced by plain
// host-memory reads (no real userspace fault boundary on the host;
// `from` is always in-bounds for `size` bytes by construction of the
// driver loop below, so no fault is ever possible here -- matching
// the strncpy_from_user/strnlen_user oracles' identical constraint).
static int my_check_zeroed_user(const unsigned char *from, size_t size)
{
	unsigned long val;
	uintptr_t align = (uintptr_t)from % sizeof(unsigned long);

	if (size == 0)
		return 1;

	from -= align;
	size += align;

	// user_read_access_begin() always "succeeds" on the host (no
	// real access_ok()/SUM dance) -- the driver never lets `from`/
	// `size` leave the backing buffer, so this is faithful for the
	// fault-free arithmetic this oracle targets.

	memcpy(&val, from, sizeof(unsigned long));
	if (align)
		val &= ~ALIGNED_BYTE_MASK(align);

	while (size > sizeof(unsigned long)) {
		if (val)
			goto done; // unlikely(val) -- leaves val untouched

		from += sizeof(unsigned long);
		size -= sizeof(unsigned long);

		memcpy(&val, from, sizeof(unsigned long));
	}

	if (size < sizeof(unsigned long))
		val &= ALIGNED_BYTE_MASK(size);

done:
	return (val == 0);
}

// Explicit LCG (same constants as the strncpy_from_user/strnlen_user
// oracles).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

#define BACKING 128

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 424242;

	// Backing array oversized vs the largest size under test, offset
	// by a runtime-chosen byte so alignment varies across cases --
	// exercises every `align` (0..7) branch, not just the aligned
	// arm, same discipline as the sibling oracles.
	unsigned char backing[BACKING + 16];

	for (long i = 0; i < n; i++) {
		int off = lcg_next() % 8;
		unsigned char *from = backing + off;

		// size sweep: 0 (trivial), 1..7 (sub-word), sizeof(long)
		// exactly (single-word, natural exit), sizeof(long)+1..
		// (multi-word, exercises the loop body and the mid-loop
		// goto-done early exit), up to a handful of words.
		size_t size;
		int mode = lcg_next() % 7;
		switch (mode) {
		case 0: size = 0; break;
		case 1: size = 1 + lcg_next() % (sizeof(ul) - 1); break;      // sub-word
		case 2: size = sizeof(ul); break;                             // exact one word
		case 3: size = sizeof(ul) + 1 + lcg_next() % 7; break;        // just over one word
		case 4: size = sizeof(ul) * (2 + lcg_next() % 4); break;      // several whole words
		case 5: size = sizeof(ul) * (2 + lcg_next() % 4) + 1 + lcg_next() % 7; break; // several words + remainder
		default: size = 1 + lcg_next() % (BACKING - 16); break;       // wide random sweep
		}

		// Content sweep: all-zero (natural exit, val==0 -> returns
		// 1), a single nonzero byte placed at a random position
		// (exercises both the mid-loop goto-done early exit for
		// positions in an earlier word, AND the natural-exit path
		// with a nonzero trailing/final word for positions in the
		// last word), or fully random bytes (mostly nonzero,
		// exercises the "found nonzero immediately" case).
		memset(from, 0, size);
		int content_mode = lcg_next() % 3;
		if (content_mode == 1 && size > 0) {
			size_t pos = lcg_next() % size;
			from[pos] = 1 + (lcg_next() % 255);
		} else if (content_mode == 2) {
			for (size_t k = 0; k < size; k++)
				from[k] = (unsigned char)lcg_next();
		}

		int r = my_check_zeroed_user(from, size);

		printf("check_zeroed_user,%d,%zu,%d\n", off, size, r);
	}
	return 0;
}
