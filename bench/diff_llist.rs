// SPDX-License-Identifier: GPL-2.0-only
//! Tier-2.5 differential oracle: Rust translation side, llist. Faithful
//! copy of lib/llist_rs.rs's algorithm (kernel-crate bindings/export/LKMM
//! shim calls replaced with single-threaded plain loads/stores — see
//! diff_llist.c's header comment for why that's a legitimate stand-in
//! for a single-threaded harness) — same protocol/LCG as diff_llist.c.

const MAXNODES: usize = 16;

#[derive(Clone, Copy, PartialEq, Eq)]
struct NodeId(Option<usize>); // None == NULL, Some(i) == &nodes[i]

struct Head {
    first: NodeId,
}

// nodes[i].1 is the "next" field (by index); nodes[i].0 unused (id is
// the index itself). Modeling pointers as indices avoids unsafe raw
// pointers on the host while preserving identical control flow.
struct Nodes {
    next: [NodeId; MAXNODES],
}

fn smp_load_acquire_first(head: &Head) -> NodeId {
    head.first
}
fn read_once_next(nodes: &Nodes, n: NodeId) -> NodeId {
    nodes.next[n.0.unwrap()]
}
fn try_cmpxchg_first(head: &mut Head, expected: &mut NodeId, new: NodeId) -> bool {
    if head.first == *expected {
        head.first = new;
        true
    } else {
        *expected = head.first;
        false
    }
}

fn llist_del_first(head: &mut Head, nodes: &Nodes) -> NodeId {
    let mut entry = smp_load_acquire_first(head);
    loop {
        if entry.0.is_none() {
            return NodeId(None);
        }
        let next = read_once_next(nodes, entry);
        if try_cmpxchg_first(head, &mut entry, next) {
            break;
        }
    }
    entry
}

fn llist_del_first_this(head: &mut Head, nodes: &Nodes, this: NodeId) -> bool {
    let mut entry = smp_load_acquire_first(head);
    loop {
        if entry != this {
            return false;
        }
        let next = read_once_next(nodes, entry);
        if try_cmpxchg_first(head, &mut entry, next) {
            break;
        }
    }
    true
}

fn llist_reverse_order(nodes: &mut Nodes, mut head: NodeId) -> NodeId {
    let mut new_head = NodeId(None);
    while let Some(hi) = head.0 {
        let tmp = head;
        head = nodes.next[hi];
        nodes.next[hi] = new_head;
        new_head = tmp;
    }
    new_head
}

fn build_chain(nodes: &mut Nodes, count: usize) -> Head {
    for i in 0..count {
        nodes.next[i] = if i + 1 < count { NodeId(Some(i + 1)) } else { NodeId(None) };
    }
    Head { first: if count > 0 { NodeId(Some(0)) } else { NodeId(None) } }
}

fn node_index(id: NodeId) -> i32 {
    match id.0 {
        Some(i) => i as i32,
        None => -1,
    }
}

// Identical LCG to diff_llist.c.
struct Lcg(u64);
impl Lcg {
    fn next(&mut self) -> u32 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (self.0 >> 32) as u32
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let seed: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(271828);
    let mut rng = Lcg(seed);

    for _ in 0..n {
        let count = (rng.next() as usize) % (MAXNODES + 1);
        let mut nodes = Nodes { next: [NodeId(None); MAXNODES] };
        let mut head = build_chain(&mut nodes, count);

        for pop in 0..=count {
            let r = llist_del_first(&mut head, &nodes);
            println!("del_first,{},{},{}", count, pop, node_index(r));
        }
    }

    // foreign node id: MAXNODES (out of the nodes[] index space, matches
    // C's `&foreign` being outside the `nodes` array).
    let foreign = NodeId(Some(MAXNODES));
    for _ in 0..n {
        let count = 1 + (rng.next() as usize) % MAXNODES;
        let mut nodes = Nodes { next: [NodeId(None); MAXNODES] };
        let mut head = build_chain(&mut nodes, count);

        let use_foreign = rng.next() % 4 == 0;
        // Matches diff_llist.c's ternary exactly: the target-index LCG
        // draw only happens in the non-foreign branch (C:
        // `use_foreign ? &foreign : &nodes[lcg_next() % count]`) — draw
        // unconditionally here would desync the RNG stream from the
        // very next iteration onward.
        let (target, target_idx) = if use_foreign {
            (foreign, 0usize)
        } else {
            let idx = (rng.next() as usize) % count;
            (NodeId(Some(idx)), idx)
        };

        // try_cmpxchg_first / read_once_next only ever dereference `this`
        // via `nodes.next[..]` when entry==this and entry came from
        // head.first (a real in-range node); the foreign case short-
        // circuits at `entry != this` before any out-of-range index, so
        // this mirrors the C's pointer comparison safely.
        let r = llist_del_first_this(&mut head, &nodes, target);
        let new_first = node_index(head.first);
        // Matches diff_llist.c: the foreign-node "index" field is
        // undefined behaviour in C (pointer subtraction between
        // unrelated objects), so both sides print a fixed -2 sentinel
        // for that case rather than any specific value.
        let target_idx_field = if use_foreign { -2 } else { node_index(target) };
        println!(
            "del_first_this,{},{},{},{},{}",
            count,
            if use_foreign { -2 } else { target_idx as i32 },
            r as i32,
            new_first,
            target_idx_field
        );
    }

    for _ in 0..n {
        let count = (rng.next() as usize) % (MAXNODES + 1);
        let mut nodes = Nodes { next: [NodeId(None); MAXNODES] };
        let head = build_chain(&mut nodes, count);

        let rev = llist_reverse_order(&mut nodes, head.first);
        print!("reverse,{},", count);
        let mut p = rev;
        let mut first = true;
        while let Some(pi) = p.0 {
            if !first {
                print!("-");
            }
            print!("{}", pi);
            first = false;
            p = nodes.next[pi];
        }
        println!();
    }
}
