// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, decompress.
// Reference extracted from lib/decompress.c (v7.1); kept byte-identical.
//
// Scope: this target config has every CONFIG_DECOMPRESS_* unset, so every
// table entry's decompressor slot is NULL in both C and Rust (see
// lib/decompress_rs.rs's module doc) — decompress_method's magic-number
// matching + name lookup is the live logic under test; the individual
// decompressor function pointers are always NULL/None on both sides, so
// we compare "which format name (or none) matched" and the numeric magic
// index, not decompressor identity (a NULL fn ptr has no printable form
// worth diffing).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define gunzip NULL
#define bunzip2 NULL
#define unlzma NULL
#define unxz NULL
#define unlzo NULL
#define unlz4 NULL
#define unzstd NULL

typedef int (*decompress_fn)(void);

struct compress_format {
	unsigned char magic[2];
	const char *name;
	decompress_fn decompressor;
};

static const struct compress_format compressed_formats[] = {
	{ .magic = {0x1f, 0x8b}, .name = "gzip", .decompressor = gunzip },
	{ .magic = {0x1f, 0x9e}, .name = "gzip", .decompressor = gunzip },
	{ .magic = {0x42, 0x5a}, .name = "bzip2", .decompressor = bunzip2 },
	{ .magic = {0x5d, 0x00}, .name = "lzma", .decompressor = unlzma },
	{ .magic = {0xfd, 0x37}, .name = "xz", .decompressor = unxz },
	{ .magic = {0x89, 0x4c}, .name = "lzo", .decompressor = unlzo },
	{ .magic = {0x02, 0x21}, .name = "lz4", .decompressor = unlz4 },
	{ .magic = {0x28, 0xb5}, .name = "zstd", .decompressor = unzstd },
	{ /* sentinel */ }
};

static decompress_fn decompress_method(const unsigned char *inbuf, long len,
					const char **name)
{
	const struct compress_format *cf;

	if (len < 2) {
		if (name)
			*name = NULL;
		return NULL;
	}

	for (cf = compressed_formats; cf->name; cf++)
		if (!memcmp(inbuf, cf->magic, 2))
			break;

	if (name)
		*name = cf->name;
	return cf->decompressor;
}

// Explicit LCG (same constants used across all bench/diff_*.c files).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

// Real magic bytes, so a meaningful fraction of cases hit an actual match
// rather than only the fully-random "no match" path.
static const unsigned char known_magics[][2] = {
	{0x1f, 0x8b}, {0x1f, 0x9e}, {0x42, 0x5a}, {0x5d, 0x00},
	{0xfd, 0x37}, {0x89, 0x4c}, {0x02, 0x21}, {0x28, 0xb5},
};

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	for (long i = 0; i < n; i++) {
		unsigned char inbuf[4];
		int len = lcg_next() % 5; // 0..4, crosses the len<2 boundary

		if (len >= 2 && lcg_next() % 2 == 0) {
			// Force a known magic about half the time.
			int mi = lcg_next() % 8;
			inbuf[0] = known_magics[mi][0];
			inbuf[1] = known_magics[mi][1];
			for (int k = 2; k < len; k++)
				inbuf[k] = (unsigned char)lcg_next();
		} else {
			for (int k = 0; k < len; k++)
				inbuf[k] = (unsigned char)lcg_next();
		}

		const char *name = (const char *)0xdeadbeef; // sentinel, overwritten if name!=NULL
		int use_name = lcg_next() % 2;
		decompress_fn fn = decompress_method(inbuf, len, use_name ? &name : NULL);

		printf("method,%d,%d,%s,%d\n", len, use_name,
		       (use_name && name) ? name : (use_name ? "(null)" : "-"),
		       fn != NULL);
	}
	return 0;
}
