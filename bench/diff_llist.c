// SPDX-License-Identifier: GPL-2.0-only
// Tier-2.5 differential oracle: C original vs Rust translation, llist.
// Reference extracted from lib/llist.c (v7.1); kept byte-identical.
//
// Scope: llist_del_first, llist_del_first_this, llist_reverse_order —
// the full TU (see lib/llist_rs.rs's module doc: only the LKMM ordering
// primitives (smp_load_acquire/READ_ONCE/try_cmpxchg) stay C-side via
// shims; control flow is translated). This is a single-threaded host
// harness — no concurrent contention exists here, so cmpxchg always
// succeeds on its first attempt; that's fine, the algorithmic control
// flow (loop structure, comparisons, pointer updates) is what's under
// test, not the concurrency primitives themselves (those are re-used C
// macros / LKMM-equivalent Rust ops either side of the real translation,
// out of scope for a differential oracle).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct llist_node {
	struct llist_node *next;
};

struct llist_head {
	struct llist_node *first;
};

// Single-threaded stand-ins for the LKMM primitives (no concurrency in
// this harness, so plain loads/stores suffice; try_cmpxchg's "always
// succeeds when *expected == actual" semantics are preserved exactly).
static struct llist_node *smp_load_acquire_first(struct llist_head *head)
{
	return head->first;
}
static struct llist_node *read_once_next(struct llist_node *n)
{
	return n->next;
}
static int try_cmpxchg_first(struct llist_head *head, struct llist_node **expected,
			      struct llist_node *new)
{
	if (head->first == *expected) {
		head->first = new;
		return 1;
	}
	*expected = head->first;
	return 0;
}

static struct llist_node *llist_del_first(struct llist_head *head)
{
	struct llist_node *entry, *next;

	entry = smp_load_acquire_first(head);
	do {
		if (entry == NULL)
			return NULL;
		next = read_once_next(entry);
	} while (!try_cmpxchg_first(head, &entry, next));

	return entry;
}

static int llist_del_first_this(struct llist_head *head, struct llist_node *this)
{
	struct llist_node *entry, *next;

	entry = smp_load_acquire_first(head);
	do {
		if (entry != this)
			return 0;
		next = read_once_next(entry);
	} while (!try_cmpxchg_first(head, &entry, next));

	return 1;
}

static struct llist_node *llist_reverse_order(struct llist_node *head)
{
	struct llist_node *new_head = NULL;

	while (head) {
		struct llist_node *tmp = head;
		head = head->next;
		tmp->next = new_head;
		new_head = tmp;
	}

	return new_head;
}

// Explicit LCG (same constants used across all bench/diff_*.c files).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

#define MAXNODES 16

// Build a chain of `count` nodes (ids 0..count-1, node[i].next = &node[i+1],
// last->next = NULL), with `head.first` pointing at node[0] (or NULL if
// count==0). Returns the node array (caller-provided storage) so the
// harness can print node identity by index.
static void build_chain(struct llist_node nodes[MAXNODES], struct llist_head *head, int count)
{
	for (int i = 0; i < count; i++)
		nodes[i].next = (i + 1 < count) ? &nodes[i + 1] : NULL;
	head->first = count > 0 ? &nodes[0] : NULL;
}

static int node_index(struct llist_node nodes[MAXNODES], struct llist_node *p)
{
	if (!p)
		return -1;
	return (int)(p - nodes);
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;

	struct llist_node nodes[MAXNODES];

	// llist_del_first: repeated pops from a chain, until empty (+1 extra
	// pop on the empty list to exercise the NULL path).
	for (long i = 0; i < n; i++) {
		int count = lcg_next() % (MAXNODES + 1);
		struct llist_head head;
		build_chain(nodes, &head, count);

		for (int pop = 0; pop <= count; pop++) {
			struct llist_node *r = llist_del_first(&head);
			printf("del_first,%d,%d,%d\n", count, pop, node_index(nodes, r));
		}
	}

	// llist_del_first_this: build a chain, pick a "this" target (in-chain
	// or a foreign node), check deletion.
	struct llist_node foreign;
	for (long i = 0; i < n; i++) {
		int count = 1 + lcg_next() % MAXNODES;
		struct llist_head head;
		build_chain(nodes, &head, count);

		int use_foreign = lcg_next() % 4 == 0;
		struct llist_node *target = use_foreign ? &foreign : &nodes[lcg_next() % count];

		int r = llist_del_first_this(&head, target);
		int new_first = node_index(nodes, head.first);
		// node_index(nodes, target) is undefined behaviour when target
		// is the foreign node (pointer subtraction between unrelated
		// objects) — print a fixed sentinel (-2) for that case instead
		// of relying on whatever garbage the UB happens to produce.
		int target_idx_field = use_foreign ? -2 : node_index(nodes, target);
		printf("del_first_this,%d,%d,%d,%d,%d\n", count,
		       use_foreign ? -2 : node_index(nodes, target), r, new_first, target_idx_field);
	}

	// llist_reverse_order: build a chain, reverse it, print the resulting
	// order as a sequence of node indices.
	for (long i = 0; i < n; i++) {
		int count = lcg_next() % (MAXNODES + 1);
		struct llist_head head;
		build_chain(nodes, &head, count);

		struct llist_node *rev = llist_reverse_order(head.first);
		printf("reverse,%d,", count);
		struct llist_node *p = rev;
		int first = 1;
		while (p) {
			printf("%s%d", first ? "" : "-", node_index(nodes, p));
			first = 0;
			p = p->next;
		}
		printf("\n");
	}
	return 0;
}
