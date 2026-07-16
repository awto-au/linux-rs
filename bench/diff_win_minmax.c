// SPDX-License-Identifier: GPL-2.0
// Tier-2.5 differential oracle: C original vs Rust translation, win_minmax.
// Reference extracted from lib/win_minmax.c + include/linux/win_minmax.h
// (v7.1), byte-identical to the shipped C.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef uint32_t u32;

struct minmax_sample { u32 t; u32 v; };
struct minmax { struct minmax_sample s[3]; };

static u32 minmax_reset(struct minmax *m, u32 t, u32 meas)
{
	struct minmax_sample val = { .t = t, .v = meas };
	m->s[2] = m->s[1] = m->s[0] = val;
	return m->s[0].v;
}

static u32 minmax_subwin_update(struct minmax *m, u32 win, const struct minmax_sample *val)
{
	u32 dt = val->t - m->s[0].t;

	if (dt > win) {
		m->s[0] = m->s[1];
		m->s[1] = m->s[2];
		m->s[2] = *val;
		if (val->t - m->s[0].t > win) {
			m->s[0] = m->s[1];
			m->s[1] = m->s[2];
			m->s[2] = *val;
		}
	} else if ((m->s[1].t == m->s[0].t) && dt > win/4) {
		m->s[2] = m->s[1] = *val;
	} else if ((m->s[2].t == m->s[1].t) && dt > win/2) {
		m->s[2] = *val;
	}
	return m->s[0].v;
}

static u32 minmax_running_max(struct minmax *m, u32 win, u32 t, u32 meas)
{
	struct minmax_sample val = { .t = t, .v = meas };

	if (val.v >= m->s[0].v || val.t - m->s[2].t > win)
		return minmax_reset(m, t, meas);

	if (val.v >= m->s[1].v)
		m->s[2] = m->s[1] = val;
	else if (val.v >= m->s[2].v)
		m->s[2] = val;

	return minmax_subwin_update(m, win, &val);
}

static u32 minmax_running_min(struct minmax *m, u32 win, u32 t, u32 meas)
{
	struct minmax_sample val = { .t = t, .v = meas };

	if (val.v <= m->s[0].v || val.t - m->s[2].t > win)
		return minmax_reset(m, t, meas);

	if (val.v <= m->s[1].v)
		m->s[2] = m->s[1] = val;
	else if (val.v <= m->s[2].v)
		m->s[2] = val;

	return minmax_subwin_update(m, win, &val);
}

static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

// Protocol: NUM_SEQ independent (max, min) tracker pairs, each fed
// STEPS_PER_SEQ (t, meas) samples (t strictly increasing, meas random,
// win fixed per sequence but varied across sequences to exercise the
// subwin_update quarter/half-window branches). Print the return value of
// every call.
#define NUM_SEQ 200
#define STEPS_PER_SEQ 60

int main(int argc, char **argv)
{
	(void)argc; (void)argv;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 12345;

	for (int seq = 0; seq < NUM_SEQ; seq++) {
		struct minmax mx = {0}, mn = {0};
		u32 win = 10 + (lcg_next() % 200); // varies window/quarter/half thresholds
		u32 t = 0;

		for (int i = 0; i < STEPS_PER_SEQ; i++) {
			t += 1 + (lcg_next() % 20); // strictly increasing, irregular gaps
			u32 meas = lcg_next() % 1000;

			u32 rmax = minmax_running_max(&mx, win, t, meas);
			u32 rmin = minmax_running_min(&mn, win, t, meas);
			printf("max,%u\nmin,%u\n", rmax, rmin);
		}
	}
	return 0;
}
