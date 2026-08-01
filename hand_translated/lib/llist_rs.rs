// SPDX-License-Identifier: GPL-2.0-only
//! Lock-less NULL-terminated singly linked list — Rust translation of
//! `lib/llist.c`.
//!
//! Copyright 2010,2011 Intel Corp. (original algorithm, Huang Ying).
//!
//! TIER 3 (concurrency): the LKMM ordering primitives — `smp_load_acquire`,
//! `READ_ONCE`, `try_cmpxchg` — stay compiled in C via per-primitive shims
//! (rust/helpers/llist.c, rule 0014), so memory-ordering semantics are the
//! C macros' by construction. Only control flow is translated. Lifting to
//! `kernel::sync::atomic` (same LKMM model) is a later, separately-reviewed
//! step. Concurrency contracts (single consumer for `llist_del_first`, …)
//! are the C kernel-doc's, unchanged.

use kernel::bindings::{self, llist_head, llist_node};
use kernel::prelude::*;

/// Delete the first entry of a lock-less list; NULL if empty.
///
/// Only ONE `llist_del_first` user may run concurrently with multiple
/// `llist_add` users (see C kernel-doc for the ABA reasoning).
#[export]
pub unsafe extern "C" fn llist_del_first(head: *mut llist_head) -> *mut llist_node {
    // SAFETY: head is a valid llist_head (C ABI contract); all shared-
    // memory accesses go through the LKMM shims.
    unsafe {
        let mut entry = bindings::llist_load_acquire_first(head);
        loop {
            if entry.is_null() {
                return core::ptr::null_mut();
            }
            let next = bindings::llist_read_once_next(entry);
            // try_cmpxchg updates `entry` on failure, as in C.
            if bindings::llist_try_cmpxchg_first(head, &mut entry, next) {
                break;
            }
        }
        entry
    }
}

/// Delete `this` if it is the first entry; returns whether it was.
///
/// Multiple callers may run concurrently with `llist_add` callers,
/// provided every caller offers a different `this`.
#[export]
pub unsafe extern "C" fn llist_del_first_this(
    head: *mut llist_head,
    this: *mut llist_node,
) -> bool {
    // SAFETY: as llist_del_first.
    unsafe {
        // C comment: acquire ensures ordering wrt try_cmpxchg in
        // llist_del_first().
        let mut entry = bindings::llist_load_acquire_first(head);
        loop {
            if entry != this {
                return false;
            }
            let next = bindings::llist_read_once_next(entry);
            if bindings::llist_try_cmpxchg_first(head, &mut entry, next) {
                break;
            }
        }
        true
    }
}

/// Reverse the order of a (detached) llist chain; returns the new first
/// entry. Pure pointer reversal — the chain is private to the caller, so
/// no ordering primitives are involved (as in C).
#[export]
pub unsafe extern "C" fn llist_reverse_order(mut head: *mut llist_node) -> *mut llist_node {
    let mut new_head: *mut llist_node = core::ptr::null_mut();

    // SAFETY: caller owns the detached chain (C ABI contract).
    unsafe {
        while !head.is_null() {
            let tmp = head;
            head = (*head).next;
            (*tmp).next = new_head;
            new_head = tmp;
        }
    }
    new_head
}
