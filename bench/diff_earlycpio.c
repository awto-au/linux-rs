// SPDX-License-Identifier: GPL-2.0-only
// Tier-2.5 differential oracle: C original vs Rust translation, earlycpio.
// Reference extracted from lib/earlycpio.c (v7.1); kept byte-identical.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_CPIO_FILE_NAME 18

enum cpio_fields {
	C_MAGIC, C_INO, C_MODE, C_UID, C_GID, C_NLINK, C_MTIME, C_FILESIZE,
	C_MAJ, C_MIN, C_RMAJ, C_RMIN, C_NAMESIZE, C_CHKSUM, C_NFIELDS
};

struct cpio_data {
	void *data;
	size_t size;
	char name[MAX_CPIO_FILE_NAME];
};

#define PTR_ALIGN(p, a) ((typeof(p))(((uintptr_t)(p) + (a) - 1) & ~((uintptr_t)(a) - 1)))

static struct cpio_data find_cpio_data(const char *path, void *data,
					size_t len, long *nextoff)
{
	const size_t cpio_header_len = 8 * C_NFIELDS - 2;
	struct cpio_data cd = { NULL, 0, "" };
	const char *p, *dptr, *nptr;
	unsigned int ch[C_NFIELDS], *chp, v;
	unsigned char c, x;
	size_t mypathsize = strlen(path);
	int i, j;

	p = data;

	while (len > cpio_header_len) {
		if (!*p) {
			p += 4;
			len -= 4;
			continue;
		}

		j = 6;
		chp = ch;
		for (i = C_NFIELDS; i; i--) {
			v = 0;
			while (j--) {
				v <<= 4;
				c = *p++;

				x = c - '0';
				if (x < 10) {
					v += x;
					continue;
				}

				x = (c | 0x20) - 'a';
				if (x < 6) {
					v += x + 10;
					continue;
				}

				goto quit;
			}
			*chp++ = v;
			j = 8;
		}

		if ((ch[C_MAGIC] - 0x070701) > 1)
			goto quit;

		len -= cpio_header_len;

		dptr = PTR_ALIGN(p + ch[C_NAMESIZE], 4);
		nptr = PTR_ALIGN(dptr + ch[C_FILESIZE], 4);

		if (nptr > p + len || dptr < p || nptr < dptr)
			goto quit;

		if ((ch[C_MODE] & 0170000) == 0100000 &&
		    ch[C_NAMESIZE] >= mypathsize &&
		    !memcmp(p, path, mypathsize)) {

			if (nextoff)
				*nextoff = (long)nptr - (long)data;

			// (pr_warn on oversize name omitted here — logging only,
			// no effect on cd/return value; see lib/hexdump-style
			// bench convention of skipping printk side effects.)
			strncpy(cd.name, p + mypathsize, MAX_CPIO_FILE_NAME - 1);
			cd.name[MAX_CPIO_FILE_NAME - 1] = 0;

			cd.data = (void *)dptr;
			cd.size = ch[C_FILESIZE];
			return cd;
		}
		len -= (nptr - p);
		p = nptr;
	}

quit:
	return cd;
}

// Explicit LCG (same constants used across all bench/diff_*.c files).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

#define ALIGN4(x) (((x) + 3) & ~3u)

// Write one well-formed "070701" newc cpio header + name + data into buf,
// 4-byte aligned throughout (matching what a real early-boot cpio blob
// looks like), returns bytes written.
static size_t write_entry(unsigned char *buf, const char *name, unsigned int namesize,
			   unsigned int filesize, unsigned int mode, int corrupt_magic)
{
	unsigned int ch[C_NFIELDS] = {0};
	ch[C_MAGIC] = corrupt_magic ? 0x070699 : 0x070701;
	ch[C_INO] = 1;
	ch[C_MODE] = mode;
	ch[C_UID] = 0;
	ch[C_GID] = 0;
	ch[C_NLINK] = 1;
	ch[C_MTIME] = 0;
	ch[C_FILESIZE] = filesize;
	ch[C_MAJ] = 0;
	ch[C_MIN] = 0;
	ch[C_RMAJ] = 0;
	ch[C_RMIN] = 0;
	ch[C_NAMESIZE] = namesize;
	ch[C_CHKSUM] = 0;

	size_t off = 0;
	// magic field: 6 hex chars
	char tmp[16];
	snprintf(tmp, sizeof(tmp), "%06x", ch[C_MAGIC] & 0xffffff);
	memcpy(buf + off, tmp, 6);
	off += 6;
	for (int f = 1; f < C_NFIELDS; f++) {
		snprintf(tmp, sizeof(tmp), "%08x", ch[f]);
		memcpy(buf + off, tmp, 8);
		off += 8;
	}
	// name (namesize bytes incl NUL, caller ensures name buffer is that long)
	memcpy(buf + off, name, namesize);
	off += namesize;
	off = ALIGN4(off);
	// filesize bytes of body (arbitrary, zero-filled)
	memset(buf + off, 'D', filesize);
	off += filesize;
	off = ALIGN4(off);
	return off;
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	unsigned char buf[512];
	char search_paths[][8] = {"foo/", "bar/", "a/", ""};

	for (long i = 0; i < n; i++) {
		int kind = lcg_next() % 4;
		size_t len;

		if (kind == 0) {
			// pure random bytes — exercises the "invalid hex"/garbage path
			len = 20 + lcg_next() % 100;
			for (size_t k = 0; k < len; k++)
				buf[k] = (unsigned char)lcg_next();
		} else if (kind == 1) {
			// one well-formed entry with a random name/filesize, then
			// trailing garbage
			char name[12];
			int namelen = 1 + lcg_next() % 10;
			for (int k = 0; k < namelen; k++)
				name[k] = 'a' + (lcg_next() % 26);
			name[namelen] = 0;
			unsigned int namesize = namelen + 1;
			unsigned int filesize = lcg_next() % 40;
			unsigned int mode = (lcg_next() % 2) ? 0100644 : 0040755; // reg file or dir
			int corrupt = 0;
			size_t used = write_entry(buf, name, namesize, filesize, mode, corrupt);
			len = used;
			// pad a little trailing junk sometimes
			if (lcg_next() % 2) {
				int pad = lcg_next() % 30;
				for (int k = 0; k < pad && len + k < sizeof(buf) - 4; k++)
					buf[len + k] = (unsigned char)lcg_next();
				len += pad;
			}
		} else if (kind == 2) {
			// corrupted magic
			char name[8] = "x";
			size_t used = write_entry(buf, name, 2, 4, 0100644, 1);
			len = used;
		} else {
			// zero-padded alignment gap before a real entry
			int gap = 4 * (1 + lcg_next() % 3);
			memset(buf, 0, gap);
			char name[8] = "y";
			size_t used = write_entry(buf + gap, name, 2, 3, 0100644, 0);
			len = gap + used;
		}

		const char *path = search_paths[lcg_next() % 4];
		long nextoff = -999;
		struct cpio_data cd = find_cpio_data(path, buf, len, &nextoff);

		printf("cpio,%d,%s,%zu,%d,%zu,", kind, path, len, cd.data != NULL, cd.size);
		// print name as hex to avoid embedding raw/garbage bytes in CSV
		for (int k = 0; k < MAX_CPIO_FILE_NAME; k++)
			printf("%02x", (unsigned char)cd.name[k]);
		printf(",%ld\n", nextoff);
	}
	return 0;
}
