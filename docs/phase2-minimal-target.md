# Phase 2 — the minimal riscv64 corpus (measured, building, booting)

2026-07-16. Per re-scoped PLAN Phase 2: trim the shipping-target config
first, so the first translation pass is as small as possible.

## The comparison that motivated this

| Corpus | target TUs | target lines | vs lab |
|---|---:|---:|---:|
| x86_64 defconfig+RUST (the lab) | 2,996 | 2,785,187 | 100% |
| riscv64 tinyconfig (aliveness only, no console) | ~451 | ~400k | **~14%** |
| riscv64 tinyconfig + slim serial (**chosen**) | 511 | 454,666 | **~16%** |

The first-pass translation target is a **sixth** of the lab corpus. And its
statement families are a subset of the lab's (same kernel, fewer subsystems),
so every rule learned on the lab corpus transfers.

## Chosen variant: `configs/riscv64-slim-serial.defconfig`

Serial is essential (Dan) — but not the 8250/16550 stack:

- `CONFIG_PRINTK` + **SBI earlycon** (`earlycon=sbi`): boot console via
  OpenSBI firmware calls — zero UART driver code on QEMU.
- **`liteuart`** (mainline LiteX UART, ~300 lines) + serial core for the
  Cynthion SoC path; `CONFIG_LITEX_SOC_CONTROLLER` included.
- VT/input/HID/PS2 (dragged in by TTY defaults) explicitly stripped again.

**Boot verified 2026-07-16** on `qemu-system-riscv64 -M virt`: full dmesg
over SBI earlycon → mounts nullfs rootfs → panics at "No working init
found" (correct: no userspace yet) → clean exit. `#3 PREEMPT` rv64 image,
1043K kernel code.

## Removed now / added later

| Removed from first pass | Added when |
|---|---|
| 8250/16550, VT, input, HID | never (liteuart/earlycon suffice) |
| block, filesystems (beyond VFS core + ramfs/nullfs), initramfs | Phase 2.5 — when userspace init is wanted (tiny static /init for "alive" heartbeat from PID 1) |
| net, USB, crypto, modules (`CONFIG_MODULES=n`) | as translated drivers need them |
| SMP | when the soft-core grows harts |
| sysfs/procfs/sysctl (tinyconfig defaults off) | debugging convenience, lab only |
| kexec/purgatory (in current build — trim candidate) | likely never for FPGA |
| EFI stub (pulled by riscv defaults — trim candidate) | never for FPGA/LiteX boot |

Unavoidable core that IS the first-pass translation set: `arch/riscv`
(entry/traps/mm/sbi/timers), kernel core (sched, irq, time, locking, rcu-tiny,
printk), mm, VFS core, `drivers/of` (device tree — LiteX boots with DT),
`drivers/base`, irqchip (PLIC/INTC), clocksource, lib/. Directory breakdown in
`tmp/` census logs.

## Open items

- rv64 vs rv32: Cynthion's ECP5 realistically runs a VexRiscv-class **rv32**
  core; rv64 chosen per Dan for now (QEMU virt + possible 64-bit soft core).
  Config is regenerable either way; statement families barely change.
- EFI stub + kexec/purgatory + PCI remnants in the build are trim candidates
  worth ~30 TUs.
- Next: unsafe-first translation of first files on this corpus, rule DB
  schema, safe-version attempt via `kernel` crate (PLAN Phase 2 steps 2–4).
