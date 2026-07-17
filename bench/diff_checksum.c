// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, checksum.
// Reference extracted from lib/checksum.c (v7.1); kept byte-identical.
//
// Scope: riscv provides its own do_csum/ip_fast_csum (arch override,
// #ifndef-guards the generic ones out of the build) — see
// lib/checksum_rs.rs's module doc. Only csum_partial, ip_compute_csum,
// and csum_tcpudp_nofold are actually compiled/translated for this
// target, so only those three are exercised here. do_csum itself (the
// arch/riscv/lib/csum.c version) is a separate, untranslated TU that
// both sides call through — we need a do_csum implementation for this
// host harness, so we use the SAME generic lib/checksum.c do_csum both
// C and Rust effectively depend on via bindings; the point under test is
// csum_partial/ip_compute_csum/csum_tcpudp_nofold's own arithmetic, not
// do_csum, so a faithful generic do_csum here is a legitimate stand-in
// for whichever concrete do_csum the real kernel links in (both sides of
// the real diff go through the identical extern symbol).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

static unsigned int csum_from32to16(unsigned int sum)
{
	sum = (sum & 0xffff) + (sum >> 16);
	sum = (sum & 0xffff) + (sum >> 16);
	return sum;
}

static unsigned int do_csum(const unsigned char *buff, int len)
{
	int odd;
	unsigned int result = 0;

	if (len <= 0)
		goto out;
	odd = 1 & (unsigned long) buff;
	if (odd) {
		result += (*buff << 8); // little-endian arm
		len--;
		buff++;
	}
	if (len >= 2) {
		if (2 & (unsigned long) buff) {
			result += *(unsigned short *) buff;
			len -= 2;
			buff += 2;
		}
		if (len >= 4) {
			const unsigned char *end = buff + ((unsigned)len & ~3);
			unsigned int carry = 0;
			do {
				unsigned int w = *(unsigned int *) buff;
				buff += 4;
				result += carry;
				result += w;
				carry = (w > result);
			} while (buff < end);
			result += carry;
			result = (result & 0xffff) + (result >> 16);
		}
		if (len & 2) {
			result += *(unsigned short *) buff;
			buff += 2;
		}
	}
	if (len & 1)
		result += *buff; // little-endian arm
	result = csum_from32to16(result);
	if (odd)
		result = ((result >> 8) & 0xff) | ((result & 0xff) << 8);
out:
	return result;
}

static u32 csum_partial(const void *buff, int len, u32 wsum)
{
	unsigned int sum = wsum;
	unsigned int result = do_csum(buff, len);

	result += sum;
	if (sum > result)
		result += 1;
	return result;
}

static u16 ip_compute_csum(const void *buff, int len)
{
	return (u16)~do_csum(buff, len);
}

static u32 from64to32(u64 x)
{
	x = (x & 0xffffffff) + (x >> 32);
	x = (x & 0xffffffff) + (x >> 32);
	return (u32)x;
}

static u32 csum_tcpudp_nofold(u32 saddr, u32 daddr, u32 len, uint8_t proto, u32 sum)
{
	unsigned long long s = sum;

	s += saddr;
	s += daddr;
	s += (proto + len) << 8; // little-endian arm
	return from64to32(s);
}

// Explicit LCG (same constants used across all bench/diff_*.c files).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

#define MAXLEN 260

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	unsigned char storage[MAXLEN + 16];

	for (long i = 0; i < n; i++) {
		int offset = lcg_next() % 8; // vary alignment 0..7
		int len = lcg_next() % (MAXLEN - offset);
		for (int k = 0; k < len; k++)
			storage[offset + k] = (unsigned char)lcg_next();
		u32 wsum = lcg_next();

		u32 r1 = csum_partial(storage + offset, len, wsum);
		printf("partial,%d,%d,%u,%u\n", offset, len, wsum, r1);

		u16 r2 = ip_compute_csum(storage + offset, len);
		printf("compute,%d,%d,%u\n", offset, len, r2);
	}

	for (long i = 0; i < n; i++) {
		u32 saddr = lcg_next();
		u32 daddr = lcg_next();
		u32 len = lcg_next() & 0xffff;
		uint8_t proto = (uint8_t)lcg_next();
		u32 sum = lcg_next();
		u32 r = csum_tcpudp_nofold(saddr, daddr, len, proto, sum);
		printf("tcpudp,%u,%u,%u,%u,%u,%u\n", saddr, daddr, len, proto, sum, r);
	}
	return 0;
}
