# Reference projects — the target-design corpus

Added 2026-07-16. **Credit:** this corpus and the corpora/rule-provenance
scheme below come from a ChatGPT deep-research survey brought into the
project; repos, licences and activity were independently verified
(`gh`) before inclusion. Pinned list: [references/manifest.toml](../references/manifest.toml).

## Why these matter

The pattern DB needs to know not just *what a Linux C idiom is* but *what
good kernel Rust for it looks like*. Several clean-slate Rust kernels now
boot and run real Linux userspace — they are the missing target-language
examples:

| Project | Boots | Runs Linux binaries | Use here |
|---|---|---|---|
| **Asterinas** (MPL-2.0, active) | yes | yes — 230+ syscalls, NixOS image | safe-architecture reference: typed addresses, page ownership, unsafe confined to OSTD (~14% TCB); RISC-V support |
| **Moss** (MIT, active) | yes (AArch64) | yes — dynamic Arch userspace, 109 syscalls | the cleanest typed-user-pointer design (`TUA<T>`, `UserCopyable`, MaybeUninit boundary); host-testable libkernel |
| **Kerla** (unmaintained) | yes | yes — limited x86-64 | compact, readable Linux-ABI monolith: syscall dispatch, errno, ELF load |
| **linux-0.11-rs** | yes (i386) | its own userland | **paired corpus**: historical Linux C ↔ Rust reimplementation, function by function |
| **rCore/ArceOS** | yes | partial | minimal RISC-V machinery: SBI, traps, satp, PLIC — the Cynthion boot path vocabulary |
| **Rust-for-Linux** | (is Linux) | native | the ONLY source for emitted-code interfaces: in-tree abstractions, pinned-init, lock guards, KUnit |

Redox/Theseus: general Rust-OS patterns only; architecturally non-Linux —
low priority.

## Usage policy (evidence vs emitted code)

- External code is **evidence**: type shapes, abstraction boundaries,
  safety invariants, test techniques. Rules record provenance
  (`independently derived | structurally inferred | API-inspired | adapted`).
- Emitted kernel code is derived from the Linux source + Rust-for-Linux
  interfaces. **Never mechanically copy** from MPL/MIT clean-room kernels
  into GPL-2.0 kernel output — licence hygiene and semantic hygiene
  coincide here: clean-room kernels implement Linux-*visible* behaviour,
  not Linux-internal semantics (e.g. Moss's `copy_from_user` returns
  `Result`, Linux's returns uncopied-byte count — a rule trained naively
  on Moss would be *wrong*).

## Planned corpora (from the survey, adopted)

- **A — paired C↔Rust**: linux-0.11-rs vs Linux 0.11; Rust-for-Linux
  drivers vs their C predecessors. Trains rules (direct_port pairs only).
- **B — target idioms**: RfL > Asterinas > Moss > Kerla ranked index of
  Rust patterns per Linux concept (uaccess, guards, intrusive lists…).
- **C — boot path**: upstream riscv + rCore/ArceOS + RustSBI, indexed by
  boot stage (reset → DT → early alloc → traps → timer → first thread →
  heartbeat).
- **D — validation techniques**: Moss host tests, Asterinas CI, RfL KUnit;
  plus the survey's "tier 2.5" oracle addition — a generated semantic
  micro-harness comparing C vs Rust per-operation (return value, output
  bytes, state mutations, lock/refcount trace) — adopted into PLAN's
  oracle between ABI-diff and KUnit.

First multi-stage rule to implement with this corpus:
**USERSPACE_TYPED_COPY** (`copy_from_user` family) — literal unsafe →
checked in-tree → safe wrapper, with negative examples (partial-copy-count
observed ⇒ must NOT match).
