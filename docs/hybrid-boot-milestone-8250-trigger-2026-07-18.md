# 8250 RX-trigger helpers wired to Rust — 2026-07-18

Status: **achieved**, closes [awto-au/linux-rs#3](https://github.com/awto-au/linux-rs/issues/3).

Completes the scope from `docs/serial-8250-translation-scoping-2026-07-18.md`
and the follow-up left open by
`docs/hybrid-boot-milestone-2026-07-18.md` (`serial8250_compute_lcr`'s
landing): the remaining two of the three oracle-verified
`bench/diff_8250_helpers.{c,rs}` functions —
`fcr_get_rxtrig_bytes()` / `bytes_to_fcr_rxtrig()` (FCR RX-trigger-level
lookup) — are now wired into `drivers/tty/serial/8250/8250_port.c` the
same way `serial8250_compute_lcr()` was: a thin C wrapper under
`CONFIG_RUST` calling into `drivers/tty/serial/8250/8250_helpers_rs.rs`,
original C body kept verbatim in the `#else` arm. Kernel commit:
`7e022a940cf7` on `linux-rs/phase2-gcd`.

## The design fork, and which way it went

Issue #3 identified the fork explicitly: port `uart_config[]` (the
driver-wide `struct serial8250_config[]` table both functions index) to
Rust, or change the two functions' signatures to take an
already-resolved slice, pushing the `uart_config[]` lookup itself back
into the C caller.

**Went with slice-passing.** `uart_config[]` turned out to be bigger
than either prior doc estimated (~10 entries) — actually ~25 entries
keyed by the `PORT_*` enum, from `PORT_UNKNOWN` through
`PORT_LPC3220` and beyond, each carrying a `const char *name` plus
`.fifo_size`/`.tx_loadsz`/`.fcr`/`.flags`, all of which are read
elsewhere in `8250_port.c` well outside these two functions (device
naming, FIFO sizing, capability flags feeding autoconfig and
`set_termios`). Porting the whole table would have meant either
duplicating driver-wide data in two languages — a correctness hazard
of its own, since the two copies could silently drift — or a
materially larger follow-on TU migrating every reader, not just these
two functions. That's disproportionate for what the scoping doc always
called a narrow Tier-A slice.

The slice-passing shape turned out cheaper than either doc anticipated,
too. Because the *outer* C function signatures
(`fcr_get_rxtrig_bytes(struct uart_8250_port *up)` /
`bytes_to_fcr_rxtrig(struct uart_8250_port *up, unsigned char bytes)`)
don't change — only their bodies do, resolving
`&uart_config[up->port.type].rxtrig_bytes[0]` in C (the same expression
the original body used) and passing that 4-byte slice by pointer to
Rust — the two call sites (`do_get_rxtrig`, `do_set_rxtrig`, both
internal to `8250_port.c`) needed **zero** edits. Confirmed by grepping
the whole `drivers/tty/serial/8250/` directory: those are the only two
call sites that exist anywhere. This ends up exactly as narrow as the
`compute_lcr` "swap the function body only" pattern, not the more
invasive call-site-editing cost both docs assumed slice-passing would
carry.

## What changed

- `drivers/tty/serial/8250/8250_helpers_rs.rs`: added
  `fcr_get_rxtrig_bytes_rs(rxtrig_bytes: *const c_uchar, fcr: c_uchar) -> c_int`
  and `bytes_to_fcr_rxtrig_rs(rxtrig_bytes: *const c_uchar, bytes: c_uchar) -> c_int`,
  same `#[no_mangle] pub unsafe extern "C" fn` shape as
  `serial8250_compute_lcr_rs`. Both read the caller-supplied 4-byte
  (`UART_FCR_R_TRIG_MAX_STATE`) table via `core::slice::from_raw_parts`
  (precedented elsewhere in the tree, e.g. `lib/argv_split_rs.rs`,
  `drivers/gpu/nova-core/`) under a documented `# Safety` contract: the
  pointer must be non-null and point to a valid, fully-initialized
  4-byte array for the call's duration — satisfied because the C side
  always passes `&uart_config[...].rxtrig_bytes[0]`, a `static const`
  array field, never mutated, never null.
- `drivers/tty/serial/8250/8250_port.c`: `#ifdef CONFIG_RUST` / `#else`
  around both functions' bodies, `extern` declarations for the two Rust
  symbols in the `CONFIG_RUST` arm — identical mechanism to
  `serial8250_compute_lcr`.
- No `Makefile` change needed: `8250_base-$(CONFIG_RUST) +=
  8250_helpers_rs.o` (added for `compute_lcr`) already compiles the
  whole file unconditionally.

## Verification

1. **Oracle re-run, not assumed valid from a prior session:**
   `scripts/diff_oracle.py 8250_helpers` →
   `ORACLE 2.5 PASS: 8250_helpers — 7500 cases, 15000 output lines,
   byte-identical`. Covers all three functions, including the two
   wired in here.
2. **`dev.py check` before vs. after**, both runs confirmed to reflect
   an uncontended tree (this driver directory saw genuine concurrent
   multi-agent activity mid-TU — a `strnlen_user` translation and an
   `iomem_copy` SPDX fix landing in parallel; build/boot were
   deliberately deferred until the tree was confirmed clear, and only
   this TU's two files were staged for commit):
   - Before: `tmp/boot-history/20260718T121029+1000-post-spdx-check.log`
     — 16/16 KUnit suites pass, `ORACLE PASS`, `INIT REACHED`.
   - After: `tmp/boot-history/20260718T121301+1000-default.log` (plus a
     repeat boot `20260718T121431+1000-default.log`) — identical 16/16
     KUnit suites pass, `ORACLE PASS`, `INIT REACHED`.
3. **Byte-for-byte serial console transcript diff, before vs. after**
   (the check this TU's own scoping doc says is not optional given
   what's at stake): the only differences are the expected
   build-number/timestamp banner line, plus a single benign reordering
   of two unrelated early-boot printk lines (`Serial: 8250/16550
   driver...` vs. an unrelated `Freeing initrd memory...` line from a
   different subsystem). Confirmed this is not console
   corruption/drop/garble:
   - Both messages' *content* is byte-identical on both sides — only
     their relative order shifted by one line.
   - Reproducible and stable: two consecutive boots of the same
     post-change binary produced byte-identical transcripts (banner
     line aside), and two consecutive boots of the same pre-change
     binary were also byte-identical with zero jitter — ruling out
     run-to-run timing nondeterminism as the explanation. The reorder
     is a one-time, deterministic consequence of the binary's code
     layout shifting size (the new `#ifdef CONFIG_RUST` block adds
     code to `8250_port.c`), not a runtime race.
   - Confirmed neither newly-wired function is even reachable during
     boot: both are `static`, called only from `do_get_rxtrig`/
     `do_set_rxtrig`, which back `DEVICE_ATTR_RW(rx_trig_bytes)` — a
     sysfs attribute, not anything the boot/KUnit/console path invokes.
     So this specific boot comparison doesn't exercise the new Rust
     code at all; it's the console-non-corruption check the scoping
     doc calls for regardless, and it passed clean.
4. **Rust symbols confirmed present and genuinely linked**, same check
   used for the `compute_lcr` milestone:
   `nm vmlinux.unstripped | grep -E 'fcr_get_rxtrig_bytes_rs|bytes_to_fcr_rxtrig_rs'`
   → both present as global text (`T`), not dead-stripped. Cross-checked
   `nm drivers/tty/serial/8250/8250_port.o` shows both as undefined
   (`U`) references — the compiled C wrapper genuinely calls out to
   them, not an unused declaration sitting dead in the object file.

## What this does and doesn't establish

**Does:** completes the full oracle-verified Tier-A slice from the
scoping doc — all three `bench/diff_8250_helpers.{c,rs}` functions are
now live in the boot-tested kernel, `uart_config[]` stays single-sourced
in C, and the mechanism (hand-adapted `CONFIG_RUST` C-wrapper pattern,
not `integrate_tu.py`'s whole-file swap) is now proven across three
functions, not just one.

**Doesn't:** these two functions are, like `compute_lcr`, pure
zero-I/O, zero-side-effect register-bit arithmetic reachable only from
a sysfs attribute — this boot's clean transcript does not itself
exercise them (see point 3 above), only rules out that the code
*layout* change didn't corrupt the console. Nothing about Tier B
(register I/O, `mem_serial_in`/`mem_serial_out`) or Tier C
(interrupt-context RX/TX, `set_termios` control flow, the console write
path itself) is touched or de-risked by this TU — those remain gated as
documented in the scoping doc, in particular the requirement for an
external, non-self-referential verification tier before the console
write path is ever swapped.

## Note on concurrent work in this tree

While this TU was in progress, the shared `linux-riscv/` working tree
had genuine concurrent multi-agent activity: a `run_c2rust_baseline.py`
full-corpus triage run, and a `strnlen_user` translation in progress
(touching `lib/Makefile`, `rust/bindings/bindings_helper.h`,
`rust/helpers/uaccess.c`, and a new untracked `lib/strnlen_user_rs.rs`)
from another session. Per this project's existing precedent for this
exact scenario (see the "Note on concurrent work" section of
`docs/hybrid-boot-milestone-2026-07-18.md`), work was paused until the
tree was confirmed free of running build/boot processes, and only this
TU's two files (`8250_helpers_rs.rs`, `8250_port.c`) were staged for
commit — verified via `git status --short` immediately before staging
that no foreign file was included.
