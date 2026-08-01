// SPDX-License-Identifier: GPL-2.0
//! Stable list mergesort — Rust translation of `lib/list_sort.c`.
//!
//! Bottom-up mergesort over intrusive `list_head` lists, eager 2:1-balanced
//! merging keyed on the bits of `count` (see the C original's excellent
//! commentary for the six-state analysis). Intermediate format: singly
//! linked, null-terminated, prev links unmaintained; `pending` is a
//! prev-linked list of sorted sublists. `likely()` hint dropped (rule 0007);
//! `__attribute__((nonnull))` becomes the stated SAFETY contract.

use core::ptr::{addr_of_mut, null_mut};
use kernel::bindings::list_head;
use kernel::ffi::{c_int, c_void};
use kernel::prelude::*;

type ListCmpFunc =
    unsafe extern "C" fn(*mut c_void, *const list_head, *const list_head) -> c_int;

/// Merge two null-terminated singly linked lists into intermediate format.
///
/// # Safety
/// `a` and `b` are non-null heads of null-terminated singly linked lists;
/// `cmp` is a valid comparison function (C nonnull(2,3,4) contract).
unsafe fn merge(
    priv_: *mut c_void,
    cmp: ListCmpFunc,
    mut a: *mut list_head,
    mut b: *mut list_head,
) -> *mut list_head {
    let mut head: *mut list_head = null_mut();
    let mut tail: *mut *mut list_head = &mut head;

    // SAFETY: per function contract; a/b checked non-null before deref.
    unsafe {
        loop {
            // If equal, take 'a' — important for sort stability.
            if cmp(priv_, a, b) <= 0 {
                *tail = a;
                tail = addr_of_mut!((*a).next);
                a = (*a).next;
                if a.is_null() {
                    *tail = b;
                    break;
                }
            } else {
                *tail = b;
                tail = addr_of_mut!((*b).next);
                b = (*b).next;
                if b.is_null() {
                    *tail = a;
                    break;
                }
            }
        }
    }
    head
}

/// Final merge, restoring the circular doubly linked structure onto `head`.
///
/// # Safety
/// As `merge`, plus `head` is a valid list head (C nonnull(2,3,4,5)).
unsafe fn merge_final(
    priv_: *mut c_void,
    cmp: ListCmpFunc,
    head: *mut list_head,
    mut a: *mut list_head,
    mut b: *mut list_head,
) {
    let mut tail: *mut list_head = head;

    // SAFETY: per function contract.
    unsafe {
        loop {
            // If equal, take 'a' — important for sort stability.
            if cmp(priv_, a, b) <= 0 {
                (*tail).next = a;
                (*a).prev = tail;
                tail = a;
                a = (*a).next;
                if a.is_null() {
                    break;
                }
            } else {
                (*tail).next = b;
                (*b).prev = tail;
                tail = b;
                b = (*b).next;
                if b.is_null() {
                    b = a;
                    break;
                }
            }
        }

        // Finish linking remainder of list b on to tail.
        (*tail).next = b;
        loop {
            (*b).prev = tail;
            tail = b;
            b = (*b).next;
            if b.is_null() {
                break;
            }
        }

        // And the final links to make a circular doubly linked list.
        (*tail).next = head;
        (*head).prev = tail;
    }
}

/// Sort a list (stable); see the C kernel-doc for the cmp contract.
#[export]
pub unsafe extern "C" fn list_sort(
    priv_: *mut c_void,
    head: *mut list_head,
    cmp: kernel::bindings::list_cmp_func_t,
) {
    // SAFETY: C ABI contract of list_sort — head is a valid (possibly
    // empty) circular doubly linked list, cmp is valid (the C prototype
    // carries __attribute__((nonnull)); Option is bindgen's nullable view).
    unsafe {
        let cmp: ListCmpFunc = cmp.unwrap_unchecked();
        let mut list = (*head).next;
        let mut pending: *mut list_head = null_mut();
        let mut count: usize = 0; // count of pending

        if list == (*head).prev {
            // Zero or one elements.
            return;
        }

        // Convert to a null-terminated singly linked list.
        (*(*head).prev).next = null_mut();

        // See the C original for the full invariant list: pending is a
        // prev-linked list of power-of-two sorted sublists, merged so
        // every final merge is at worst 2:1.
        loop {
            let mut tail: *mut *mut list_head = &mut pending;

            // Find the least-significant clear bit in count.
            let mut bits = count;
            while bits & 1 != 0 {
                tail = addr_of_mut!((**tail).prev);
                bits >>= 1;
            }
            // Do the indicated merge.
            if bits != 0 {
                let mut a = *tail;
                let b = (*a).prev;

                a = merge(priv_, cmp, b, a);
                // Install the merged result in place of the inputs.
                (*a).prev = (*b).prev;
                *tail = a;
            }

            // Move one element from input list to pending.
            (*list).prev = pending;
            pending = list;
            list = (*list).next;
            (*pending).next = null_mut();
            count += 1;

            if list.is_null() {
                break;
            }
        }

        // End of input; merge together all the pending lists.
        list = pending;
        pending = (*pending).prev;
        loop {
            let next = (*pending).prev;

            if next.is_null() {
                break;
            }
            list = merge(priv_, cmp, pending, list);
            pending = next;
        }
        // The final merge, rebuilding prev links.
        merge_final(priv_, cmp, head, pending, list);
    }
}
