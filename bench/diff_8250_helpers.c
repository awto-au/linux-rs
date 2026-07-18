// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation,
// 8250/16550 pure register-bit-manipulation helpers.
//
// Scope: this is NOT the 8250 driver itself (drivers/tty/serial/8250/
// 8250_port.c, untouched) — it is a standalone extraction of the
// handful of genuinely pure, control-flow-free helpers a first
// hand-translation slice would target, exercised here exactly like
// every other bench/diff_*.c oracle. See docs/serial-8250-translation-
// scoping-2026-07-18.md for why these three functions were chosen and
// what is explicitly NOT in scope (register I/O, IRQ handling, tty
// core integration, the live console path).
//
// Reference extracted byte-identical from:
//   - drivers/tty/serial/8250/8250_port.c: serial8250_compute_lcr()
//     (LCR byte from termios cflag), fcr_get_rxtrig_bytes() /
//     bytes_to_fcr_rxtrig() (FCR RX-trigger lookup against the
//     per-UART-type rxtrig_bytes[] table from uart_config[]).
//   - drivers/tty/tty_ioctl.c: tty_get_char_size() (inlined here since
//     serial8250_compute_lcr calls it and it is out of 8250's TU but
//     is itself a two-line pure switch with no dependencies).
//   - include/linux/serial.h: UART_LCR_WLEN(x) macro.
//   - include/uapi/linux/serial_reg.h: UART_LCR_*, UART_FCR_R_TRIG_*
//     bit/shift constants.
//
// uart_config[].rxtrig_bytes is reproduced here only for the two
// entries a QEMU ns16550a device can report (PORT_16550 age-old FIFO-
// less variant and PORT_16550A, the type QEMU's virt board actually
// autoconfigures to) plus PORT_16750 as a third distinct-shape case,
// to exercise more than one trigger table without dragging in the
// full 11-entry array (an implementation, not behavioral, detail —
// the real translation must carry the whole table).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef uint8_t u8;
typedef uint32_t tcflag_t;

/* ---- include/uapi/linux/serial_reg.h (verbatim subset) ---- */
#define UART_LCR_DLAB		0x80
#define UART_LCR_SPAR		0x20
#define UART_LCR_EPAR		0x10
#define UART_LCR_PARITY		0x08
#define UART_LCR_STOP		0x04
#define UART_LCR_WLEN5		0x00
#define UART_LCR_WLEN6		0x01
#define UART_LCR_WLEN7		0x02
#define UART_LCR_WLEN8		0x03

#define UART_FCR_R_TRIG_00	0x00
#define UART_FCR_R_TRIG_01	0x40
#define UART_FCR_R_TRIG_10	0x80
#define UART_FCR_R_TRIG_11	0xc0
#define UART_FCR_R_TRIG_SHIFT		6
#define UART_FCR_R_TRIG_BITS(x)	(((x) & 0xc0) >> UART_FCR_R_TRIG_SHIFT)
#define UART_FCR_R_TRIG_MAX_STATE	4

/* ---- include/linux/serial.h (verbatim) ---- */
#define UART_LCR_WLEN(x)	((x) - 5)

/* ---- include/uapi/asm-generic/termbits.h (verbatim subset) ---- */
#define CSIZE		0x00000030
#define CS5		0x00000000
#define CS6		0x00000010
#define CS7		0x00000020
#define CS8		0x00000030
#define CSTOPB		0x00000040
#define PARENB		0x00000100
#define PARODD		0x00000200
#define CMSPAR		0x40000000

/* ---- drivers/tty/tty_ioctl.c:tty_get_char_size(), verbatim ---- */
static unsigned char tty_get_char_size(unsigned int cflag)
{
	switch (cflag & CSIZE) {
	case CS5:
		return 5;
	case CS6:
		return 6;
	case CS7:
		return 7;
	case CS8:
	default:
		return 8;
	}
}

/* ---- 8250_port.c:serial8250_compute_lcr(), verbatim (up->... unused) ---- */
static unsigned char serial8250_compute_lcr(tcflag_t c_cflag)
{
	u8 lcr = UART_LCR_WLEN(tty_get_char_size(c_cflag));

	if (c_cflag & CSTOPB)
		lcr |= UART_LCR_STOP;
	if (c_cflag & PARENB)
		lcr |= UART_LCR_PARITY;
	if (!(c_cflag & PARODD))
		lcr |= UART_LCR_EPAR;
	if (c_cflag & CMSPAR)
		lcr |= UART_LCR_SPAR;

	return lcr;
}

/* ---- 8250_port.c: subset of uart_config[].rxtrig_bytes, verbatim ---- */
enum { CFG_16550, CFG_16550A, CFG_16750, CFG_COUNT };
static const unsigned char rxtrig_bytes[CFG_COUNT][UART_FCR_R_TRIG_MAX_STATE] = {
	[CFG_16550]  = {0, 0, 0, 0},        /* no working FIFO */
	[CFG_16550A] = {1, 4, 8, 14},
	[CFG_16750]  = {1, 16, 32, 56},
};

/* ---- 8250_port.c:fcr_get_rxtrig_bytes(), verbatim modulo table lookup ---- */
static int fcr_get_rxtrig_bytes(int cfg, unsigned char fcr)
{
	unsigned char bytes = rxtrig_bytes[cfg][UART_FCR_R_TRIG_BITS(fcr)];

	return bytes ? bytes : -95; /* -EOPNOTSUPP */
}

/* ---- 8250_port.c:bytes_to_fcr_rxtrig(), verbatim modulo table lookup ---- */
static int bytes_to_fcr_rxtrig(int cfg, unsigned char bytes)
{
	int i;

	if (!rxtrig_bytes[cfg][UART_FCR_R_TRIG_BITS(UART_FCR_R_TRIG_00)])
		return -95; /* -EOPNOTSUPP */

	for (i = 1; i < UART_FCR_R_TRIG_MAX_STATE; i++) {
		if (bytes < rxtrig_bytes[cfg][i])
			return (--i) << UART_FCR_R_TRIG_SHIFT;
	}

	return UART_FCR_R_TRIG_11;
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
		/* Bias toward realistic cflag bit combinations plus raw
		 * fuzzing, matching what termios settings a real tty
		 * layer would actually pass through set_termios(). */
		tcflag_t cflag;
		unsigned r = lcg_next();
		if (i % 4 == 0) {
			tcflag_t csize_opts[4] = {CS5, CS6, CS7, CS8};
			cflag = csize_opts[r & 3];
			if (r & 0x10) cflag |= CSTOPB;
			if (r & 0x20) cflag |= PARENB;
			if (r & 0x40) cflag |= PARODD;
			if (r & 0x80) cflag |= CMSPAR;
		} else {
			cflag = r;
		}
		unsigned char lcr = serial8250_compute_lcr(cflag);
		printf("lcr,%u,%u\n", cflag, lcr);
	}

	for (long i = 0; i < n; i++) {
		int cfg = (int)(lcg_next() % CFG_COUNT);
		unsigned char fcr = (unsigned char)(lcg_next() & 0xff);
		int r = fcr_get_rxtrig_bytes(cfg, fcr);
		printf("rxtrig_get,%d,%u,%d\n", cfg, fcr, r);
	}

	for (long i = 0; i < n; i++) {
		int cfg = (int)(lcg_next() % CFG_COUNT);
		unsigned char bytes = (unsigned char)(lcg_next() & 0xff);
		int r = bytes_to_fcr_rxtrig(cfg, bytes);
		printf("rxtrig_set,%d,%u,%d\n", cfg, bytes, r);
	}
	return 0;
}
