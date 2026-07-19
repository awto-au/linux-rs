# Plan: An As-C-Free-As-Possible Rust Linux Userspace with Ion and uutils

**Target:** RISC-V 64-bit Linux (`riscv64gc`) in QEMU first, then physical hardware  
**Primary shell:** Ion  
**Primary command suite:** uutils coreutils multicall binary  
**Ultimate objective:** a useful Linux userspace whose maintained application code is Rust, progressing toward zero C object code and zero libc dependency in the runtime image  
**Document status:** implementation plan and engineering decision record  
**Date:** 19 July 2026

## 1. Executive summary

The recommended route is incremental. Do not make the first successful boot depend on solving the entire libc-free Rust runtime problem.

1. Boot a conventional Linux kernel with a tiny static Rust PID 1, statically linked Ion, and the uutils multicall binary in an initramfs.
2. Use the official `riscv64gc-unknown-linux-musl` Rust target initially. This produces a self-contained system but includes musl C object code.
3. Remove every nonessential conventional utility and replace missing functions with small Rust programs.
4. Inventory every executable, library, object, build script, native dependency, interpreter, and firmware file.
5. Rebuild selected programs around direct Linux syscalls using `rustix`'s `linux_raw` backend or a deliberately small internal syscall layer.
6. Treat Ion and uutils as upstream application code that may require controlled forks to remove their libc-facing assumptions.
7. Only claim “C-free userspace” when binary provenance and disassembly audits demonstrate that no C-compiled runtime objects remain in the shipped image.

The first useful result is realistic. The final strict result is a research and porting project because Rust's normal Linux `std` target expects a platform C library for parts of process startup, threading, signals, allocation, DNS, locale, and other operating-system services.

## 2. Define exactly what “C-free” means

The phrase must be divided into measurable levels.

| Level | Definition | Expected result |
|---|---|---|
| L0 - Rust-facing system | The visible shell and normal commands are written in Rust; development/build tools may use C | Easy |
| L1 - Rust runtime image | Every executable installed in initramfs is predominantly Rust; no BusyBox, Bash, GNU coreutils, glibc programs, or C daemons | Practical |
| L2 - No dynamic libc | No runtime ELF has a dynamic dependency on glibc or musl; static musl objects may still be linked into executables | Practical with the musl target |
| L3 - No C application objects | Application and utility code is Rust, but Rust `std` startup/runtime may still contain libc-derived objects | Harder to prove |
| L4 - No C object code in userspace | No shipped executable or library contains object code compiled from C, including libc startup objects and allocator/runtime helpers | Research-grade but possible for a constrained system |
| L5 - Pure-Rust provenance | All userspace source and build-time code contributing machine code is audited Rust/assembly; any assembly is explicitly documented | Strictest useful claim |

The initial deliverable should target **L2**, then advance individual programs toward **L4**. L5 is optional because a tiny amount of architecture-specific assembly may be the cleanest way to enter from `_start` and issue system calls. Assembly is not C and should not be hidden under a misleading “100% safe Rust” claim.

### 2.1 Things that do not automatically violate the runtime goal

- The Linux kernel itself, if the goal is specifically a Rust userspace. If the kernel is also being translated to Rust, track that separately.
- QEMU and host-side cross-compilers because they do not ship in the guest image.
- Device firmware blobs, provided they are classified separately and their provenance is recorded.
- A small reviewed RISC-V assembly entry stub.
- Build tools written in C that do not contribute code to the final binaries, if the claim is only about shipped runtime bytes.

### 2.2 Things that do violate strict L4/L5

- Statically linked musl startup or libc routines.
- `libgcc`, compiler runtime objects compiled from C, or an unnoticed C allocator.
- `compiler_builtins` compiled with C sources rather than the pure-Rust intrinsics (see Section 21, decision 11) — this applies to every `no_std`/`core`-linked binary, independent of whether the application itself calls libc.
- Native libraries pulled in by Cargo crates through `build.rs`, `cc`, `pkg-config`, CMake, Meson, or bindgen-driven `-sys` crates.
- BusyBox used as `/init`, rescue shell, `mount`, or `mdev`, even if normally hidden.
- Dynamically loaded NSS, resolver, locale, PAM, terminfo, TLS, compression, or database libraries written in C.
- Shell scripts whose interpreter is a C implementation, if that interpreter ships in the image.

### 2.3 PID 1 provenance: two permanent binaries, not one evolving claim

The musl-`std` PID 1 (Phase 3, M2) and the raw-syscall PID 1 (Slice 2, M5) are two permanently separate binaries with two separate provenance claims, not one binary that migrates from L2 to L4 over time:

- `/init` (musl-std): the shipping init for the L2 product and the permanent rescue/reference image (Section 14, Strategy 1). Its provenance claim stays L2 — statically linked musl objects are present and documented, never hidden.
- `/init-raw` (raw-syscall): the M5 deliverable. Its provenance claim is L4 for its own object code — no libc-derived objects, verified per Section 12.3.

M5's "PID 1 contains no libc-derived objects" refers only to `/init-raw`. The musl PID 1 is not retired when `/init-raw` lands; a build that wants to boot the L4 PID 1 selects it explicitly (initramfs variant, not a silent swap), and the L2 musl image remains available as the fallback/recovery path per Section 14's recommended choice. Any milestone or audit report must name which PID 1 binary it is describing.

## 3. Architectural choice

### 3.1 Recommended initial architecture

```text
Linux kernel
  -> built-in devtmpfs and initramfs support
  -> /init: small Rust PID 1
       -> mounts /proc, /sys, /dev, /run and /tmp
       -> establishes console and environment
       -> launches /bin/ion
  -> /bin/ion: interactive and scripting shell
  -> /bin/coreutils: uutils multicall executable
       -> /bin/ls, /bin/cat, ... symlinked to coreutils
  -> small purpose-built Rust utilities for gaps
```

### 3.2 Why Ion

Ion is a system shell written in Rust and developed alongside Redox OS, while still supporting Unix-like hosts. It has the shell functions required for a real console: parsing, built-ins, variables, redirection, pipelines, command execution, and job-related functionality. The [Ion manual](https://doc.redox-os.org/ion-manual/) is a baseline reference; for source, use the GitLab-canonical repository (Section 23) — the GitHub copy is a read-only mirror.

Important cautions, current as of this plan:

- Ion is marked experimental and its syntax is not POSIX `sh` or Bash syntax.
- Its `Cargo.toml` currently identifies it as `1.0.0-alpha`, declares `edition = "2018"`, and uses several Git dependencies including a stuck, unreleased fork of `nix` with a years-old unresolved FIXME. Pinning is essential; the `nix` fork needs real engineering work on Ion itself, not just a version pin, before job-control/signal code depending on it can be trusted.
- MSRV is 1.65; CI is pinned to Rust 1.76. Use the CI-validated version for this project's toolchain, not the MSRV floor.
- Terminal handling, job control, signals, `fork`/`exec`, pipes, and process groups are exactly the areas likely to expose incomplete syscall or libc replacement work, and are also where the stuck `nix` fork is most likely to bite.

### 3.3 Why uutils coreutils

[uutils coreutils](https://uutils.org/coreutils/) is the most mature Rust replacement for GNU coreutils. All major utilities exist, although some GNU options or behaviours can still differ. The project officially supports a [multicall binary](https://github.com/uutils/coreutils/blob/main/docs/src/multicall.md), which reduces duplication and is well suited to initramfs.

`riscv64gc-unknown-linux-musl` is compile-tested in uutils CI only — CI sets `skip-tests: true` for that target, so no correctness test has ever run there in CI. The one historical manual QEMU run against this target (2022, uutils/coreutils#3184) found 9 failures clustered in permission handling, signal timing, and musl struct-shape edge cases. Open issue uutils/coreutils#10218 documents a live `time_t`/struct-field musl ABI mismatch. Treat "compiles for riscv64gc-musl" and "is correct on riscv64gc-musl" as two separate, independently unproven claims; Phase 1 (Section 7) must include first-ever correctness validation for this target as new work, not assume it as an existing baseline.

Use the multicall layout first:

```text
/bin/coreutils
/bin/ls       -> coreutils
/bin/cat      -> coreutils
/bin/cp       -> coreutils
/bin/mv       -> coreutils
/bin/rm       -> coreutils
...
```

The direct form remains available for debugging:

```sh
/bin/coreutils ls -la /
```

### 3.4 Why not RustyBox as the base

RustyBox covers BusyBox-like functions but remains a work in progress and began as a C2Rust translation. It may be useful as a temporary source of missing applets, but it weakens provenance, maintenance, and idiomatic/safe-Rust goals. Any borrowed applet should be separately reviewed and ideally rewritten or upstreamed.

## 4. Repository and reproducibility layout

This work lands inside `linux-rs`, not a separate repository, and reuses the existing boot/QEMU/initramfs pipeline rather than building a parallel one. `linux-rs` already has a working cross-compiled statically-linked riscv64 initramfs (`scripts/build_initramfs.py`), a QEMU boot harness (`scripts/boot_qemu.py`) with `--run-id`/`--qemu-extra` flags and an `INIT_REACHED` marker, and a tracked archival pattern (`docs/status/boot-history.csv` + `docs/status/boot-logs/`, auto-committed and pushed per run). Ion/uutils integration is a new rootfs *variant* consumed by that same pipeline, not a fork of it.

Only the crates/vendor/patches side is new layout, added under the existing repo root:

```text
crates/
  init/                    PID 1 (musl-std, Phase 3)
  init-raw/                PID 1 (raw-syscall, Slice 2) — separate binary, see 2.3
  mount-early/             optional helper
  rescue/                  minimal built-in emergency console
  syscall-smoke/           raw syscall validation (Slice 1)
  image-audit/             image provenance checker
vendor/
  ion/                     Git submodule or vendored pinned tree, GitLab-canonical origin
  uutils-coreutils/
patches/
  ion/
  uutils/
config/
  ion-initrc
  passwd
  group
docs/
  compatibility.md
  c-free-audit.md
  syscall-coverage.md
  threat-model.md
```

`rust-toolchain.toml`, `sources.lock.toml`, and the workspace `Cargo.toml`/`Cargo.lock` live at the existing repo root alongside the kernel build config, not duplicated per-variant.

### 4.1 Reusing the boot/QEMU/initramfs pipeline

- **Rootfs assembly**: extend `scripts/build_initramfs.py` with a `--rootfs-variant {busybox,ion-uutils}` flag (default `busybox`, today's behavior unchanged). The `ion-uutils` variant builds `crates/init` (or `crates/init-raw`, selected by a second flag once M5 lands — see 2.3) plus the vendored Ion and uutils multicall binary, and packs them into `tmp/initramfs/initramfs-ion-uutils.cpio.gz` using the same cpio/gzip assembly code path already in the script, instead of a new `assemble-initramfs.sh`.
- **Boot**: reuse `scripts/boot_qemu.py` unchanged for the QEMU invocation itself. Extend `ensure_initramfs()` to accept the same `--rootfs-variant` flag and select the matching cpio path, so `boot_qemu.py --rootfs-variant ion-uutils --run-id ion-smoke` is the whole invocation. No new `boot-qemu.sh`.
- **Archival**: every Ion/uutils boot goes through the existing `archive_boot()`/`commit_and_push_history()` path in `boot_qemu.py` — same `docs/status/boot-history.csv` schema, same `docs/status/boot-logs/` tree, same auto-commit-and-push. Use `--run-id` values that identify the variant (e.g. `ion-uutils-m2`) so boot-history rows and archived logs are distinguishable from kernel-only busybox runs without a schema change.
- **Test harness**: `scripts/dev.py boot`/`scripts/kunit_oracle.py` gate on `INIT_REACHED`; the Ion/uutils PID 1 must emit the same marker string so this project's existing pass/fail gating covers it without a parallel oracle.
- No `build-riscv64-musl.sh`, `assemble-initramfs.sh`, `boot-qemu.sh`, or `test-qemu.sh` scripts are created. Cargo builds for the new crates are invoked directly (`cargo build --target riscv64gc-unknown-linux-musl ...`) from `build_initramfs.py`'s variant path, matching how that script already shells out to the musl cross toolchain for BusyBox.

Rules:

- Commit `Cargo.lock`.
- Pin Ion and uutils by full commit SHA, never just `master` or a moving tag.
- Record the Rust compiler version, linker version, kernel commit/config, QEMU version, and host container digest.
- Vendor Cargo dependencies for reproducible/offline builds after the first known-good build.
- Record every local patch as a small, reviewable commit and submit generally useful fixes upstream.
- Produce a software bill of materials for both source packages and final ELF contents.

## 5. Target and toolchain options

### Option A - `riscv64gc-unknown-linux-musl` - recommended first

Rust lists this as a Tier 2 target for RISC-V Linux using musl. See the official [target documentation](https://doc.rust-lang.org/rustc/platform-support/riscv64gc-unknown-linux-musl.html).

Advantages:

- Standard Rust `std` works.
- Static executables are straightforward.
- Ion and uutils have the greatest chance of compiling with minimal changes.
- No runtime dynamic loader or shared libc is needed.
- It provides a reliable behavioural reference for later libc-free work.

Disadvantage:

- It is not strictly C-free because musl and startup objects are C/assembly-derived.

### Option B - GNU target with glibc

Use only as a host-side compatibility reference. It is larger, dynamically linked by default, and conflicts with the C-free direction. It can still help determine whether a failure is a RISC-V problem, a musl problem, or an application problem.

### Option C - custom Linux target plus `no_std`

Create a custom RISC-V target specification and supply:

- `_start` and process-entry decoding (`argc`, `argv`, `envp`, auxiliary vector)
- panic strategy
- allocator
- thread-local storage if required
- raw syscall veneer
- signal restorer
- stack protection decisions
- unwind or abort policy

This is the likely strict L4 route, but normal `std`-dependent Ion and uutils cannot simply be relinked against it. Either a libc-free `std` port is required or the applications must be adapted to a substitute platform layer.

### Option D - fork/rebuild Rust `std` for a libc-free Linux environment

This preserves the largest amount of upstream application code but is a major toolchain project. It requires identifying and replacing every libc-facing section of `std::sys` for Linux. Consider it only after the raw-syscall smoke programs and minimal PID 1 are solid.

### Option E - relibc

[relibc](https://github.com/redox-os/relibc) is a C/POSIX compatibility library written in Rust, actively developed but Redox-primary; its Linux target is secondary and has no known production users. uutils itself evaluated relibc and declined it (uutils/coreutils#1906, closed "not planned," 2026-01-19) — the single most natural real-world adopter for this plan's own dependency passed on it. Its Linux/POSIX coverage must be tested against Ion and uutils rather than assumed, and it is not a drop-in guaranteed solution for a Linux userspace. Treat this as an experimental workstream, not the critical path.

## 6. Phase 0 - Freeze requirements and acceptance tests

### 6.1 Initial functional scope

The first image must:

- Boot under QEMU `virt` on RISC-V 64.
- Reach an interactive Ion prompt on `ttyS0` without BusyBox.
- Mount `/proc`, `/sys`, `/dev`, `/run`, and `/tmp`.
- Run uutils through both multicall and symlink invocation.
- Execute external programs using `$PATH`.
- Support pipelines, input/output redirection, environment variables, and exit codes.
- Shut down or reboot cleanly using a Rust command or PID 1 control path.
- Survive Ctrl-C at the prompt and while a foreground child runs.
- Reap orphaned and terminated child processes.
- Provide a recovery path if Ion exits repeatedly.

### 6.2 Initial required commands

Classify commands rather than importing every uutils applet immediately.

**Boot and recovery:**

- `true`, `false`, `echo`, `printf`, `test`
- `cat`, `head`, `tail`, `wc`
- `ls`, `stat`, `readlink`, `realpath`
- `mkdir`, `rmdir`, `touch`
- `cp`, `mv`, `rm`, `ln`
- `chmod`, `chown`, `chgrp`
- `sync`, `sleep`, `uname`, `id`
- a Rust `mount`, `umount`, `reboot`, and `poweroff` path if uutils coverage is insufficient

**Useful diagnostics:**

- `env`, `printenv`, `pwd`, `whoami`
- `date`, `uptime` equivalent
- `df`, `du`
- `ps` equivalent, perhaps from uutils procps later
- `dmesg` equivalent or direct `/dev/kmsg` reader
- `hexdump`/`od`, `sha256sum`

**Deferred:**

- full user management
- PAM/login stack
- networking configuration and DNS
- package management
- locales beyond UTF-8 basics
- dynamic module loading
- graphical environment
- full POSIX shell compatibility

### 6.3 Acceptance evidence

For every milestone save:

- kernel and userspace git SHAs
- kernel config
- rootfs file manifest with SHA-256 values
- QEMU command line
- complete serial boot log
- test results
- `readelf`/dependency audit output
- Cargo dependency/license/native-code report
- measured image size and peak memory

## 7. Phase 1 - Host-native proof of Ion plus uutils

Before cross-compiling, validate behaviour on the development host.

1. Clone pinned Ion and uutils revisions.
2. Build each with the pinned toolchain and committed lockfile.
3. Run Ion non-interactively against a shell test corpus.
4. Build uutils using the normal release profile and `release-small` profile.
5. Generate symlinks to the multicall executable in a temporary root.
6. Put only that root's `bin` first in `$PATH` and verify Ion invokes uutils rather than GNU utilities.
7. Run each selected command's upstream tests.
8. Record commands Ion expects but uutils does not provide.

Test Ion features explicitly:

- quoting and escaping
- variable and environment expansion
- command substitution if used
- glob expansion
- pipelines and pipe error propagation
- `>`, `>>`, `<`, and descriptor redirection
- built-in versus external command precedence
- foreground and background jobs
- Ctrl-C, Ctrl-Z, `fg`, `bg`, and `jobs`, if supported
- scripts, functions, aliases, and startup files
- behaviour when `/etc/passwd`, locale data, terminfo, or a home directory is absent

Deliverable: `compatibility.md` listing every required feature as pass, fail, workaround, patch, or deferred.

## 8. Phase 2 - Build the static musl RISC-V reference image

### 8.1 Toolchain

Install/pin:

- Rust toolchain containing `rustc`, `cargo`, `rust-src`, and the RISC-V musl target
- LLVM `lld` or a pinned RISC-V linker
- QEMU system emulator for RISC-V
- `readelf`, `objdump`, `nm`, and `strip`
- CPIO and a compression tool for initramfs creation - host-side only

Use explicit Cargo configuration, for example conceptually:

```toml
[build]
target = "riscv64gc-unknown-linux-musl"

[target.riscv64gc-unknown-linux-musl]
linker = "rust-lld"
rustflags = [
  "-C", "target-feature=+crt-static",
  "-C", "panic=abort",
]
```

Do not copy this blindly if Ion or uutils require unwind semantics during initial bring-up. First obtain a correct build, then optimize panic/unwind behaviour deliberately.

### 8.2 Build profiles

Maintain two profiles:

- `release-debuggable`: optimized but with symbols retained in separate debug files.
- `release-small`: size-optimized, LTO enabled after correctness is established, abort-on-panic if compatible, and stripped for the image.

Record effects of:

- `opt-level = "s"` versus `"z"`
- thin versus fat LTO
- one versus multiple codegen units
- symbol stripping
- debug info split
- panic unwind versus abort

### 8.3 Ion build

- Begin with upstream feature defaults.
- Disable nonessential features such as optional graphical/Piston functionality.
- Test whether Unicode support is required for intended console use.
- Inspect all Git dependencies and pin their exact revisions.
- Run `cargo tree -e features` and `cargo tree --target ...`.
- Search the dependency graph for `libc`, `nix`, `cc`, `cmake`, `pkg-config`, bindgen, and `*-sys` crates.
- Check whether terminal handling goes directly through Rust abstractions or a libc wrapper.
- Preserve a known-good upstream build as the behavioural oracle before applying libc-removal patches.

### 8.4 uutils build

- Build the multicall binary as documented by uutils.
- Start with the portable common set.
- Add platform-specific utilities only when required.
- Prefer `release-small` for initramfs after testing it against the normal release build.
- Generate symlinks from a declared allow-list, not by blindly exposing every compiled applet.
- Test invocation through the executable name and `coreutils APPLET` form.
- Keep a machine-readable list of enabled applets and why each is present.

### 8.5 Rootfs assembly

Assembled by `scripts/build_initramfs.py --rootfs-variant ion-uutils` (Section 4.1), not a standalone script. The root filesystem should initially contain only:

```text
/init
/bin/ion
/bin/coreutils
/bin/<approved uutils symlinks>
/etc/ion/initrc
/etc/passwd
/etc/group
/dev/console
/proc
/sys
/run
/tmp
```

Prefer kernel-managed devtmpfs. Avoid importing udev initially. Set secure permissions:

- `/tmp`: mode `01777`
- `/root`: mode `0700`, if present
- configuration files: read-only in initramfs
- console: controlled ownership and mode

Generate the initramfs deterministically:

- stable ordering
- fixed owner/group
- normalized timestamps if reproducible builds are required
- no host absolute paths
- no accidental host binaries, libraries, caches, SSH keys, Cargo credentials, or shell history

## 9. Phase 3 - Implement a correct Rust PID 1

Do not simply make Ion PID 1. A shell is not necessarily a correct init process.

PID 1 responsibilities:

1. Open or duplicate `/dev/console` onto file descriptors 0, 1, and 2.
2. Set a predictable environment: `PATH`, `HOME`, `TERM`, `USER`, and locale policy.
3. Mount devtmpfs, procfs, sysfs, tmpfs for `/run`, and optionally tmpfs for `/tmp`.
4. Set the hostname if required.
5. Launch Ion in a new session with a controlling terminal.
6. Reap all children, including orphaned descendants.
7. Forward or appropriately handle termination, interrupt, hangup, and child signals.
8. Detect repeated Ion crashes and enter a minimal recovery loop.
9. Support explicit reboot, poweroff, and halt requests.
10. Sync writable filesystems before shutdown where appropriate.

Keep PID 1 small and dependency-light. `crates/init` (this phase) uses `std` against the musl target and is the permanent L2 binary described in Section 2.3. `crates/init-raw` (Slice 2, M5) is a second, separate raw-syscall PID 1 built in parallel as the first strict libc-free program — it does not replace `crates/init`.

### 9.1 Recovery strategy

Avoid a hidden BusyBox rescue fallback. Instead include one of:

- a tiny built-in Rust command loop in PID 1 supporting `help`, `mounts`, `ps-lite`, `exec`, `reboot`, and `poweroff`; or
- a separate audited `rescue` binary written in Rust.

If Ion exits:

- record its exit status
- delay with an event/timer mechanism to prevent a tight crash loop
- offer recovery after a small retry count
- never panic PID 1 because a startup file is malformed

## 10. Phase 4 - Kernel facilities needed by Ion and uutils

This matters particularly if the Linux kernel is being translated or reduced.

### 10.1 Process and execution

- `clone`/`clone3` or `fork`/`vfork` semantics used by the runtime
- `execve` and possibly `execveat`
- `wait4`/`waitid`
- process groups and sessions
- `setsid`, `setpgid`, `getpgid`, `tcsetpgrp`
- credentials and identity calls
- `prctl` where dependencies use it

### 10.2 File descriptors and pipelines

- `openat`, `close`, `read`, `write`
- `pipe2`
- `dup`, `dup2`, `dup3`
- `fcntl`
- `poll`, `ppoll`, or equivalent
- `ioctl`
- directory enumeration via `getdents64`
- `statx` and/or legacy stat calls as required
- symlink, rename, unlink, chmod, chown, and timestamp operations

### 10.3 Terminal and job control

- PTY/TTY support
- termios ioctls
- foreground process groups
- canonical and raw modes
- window size queries
- job-control signals
- controlling-terminal semantics

This is likely the highest-risk kernel-facing area for Ion.

### 10.4 Memory and runtime

- `mmap`, `munmap`, `mprotect`, and possibly `mremap`
- `brk` if anything still expects it
- `futex` for locks and threads
- thread-local storage mechanism expected by the target
- `getrandom`
- clocks and nanosleep
- signal actions, masks, alternate stack if used, and RISC-V signal return

### 10.5 Filesystems and pseudo-filesystems

- initramfs/rootfs
- devtmpfs
- procfs
- sysfs
- tmpfs for writable ephemeral paths
- a persistent filesystem later

Create a syscall coverage table with columns for syscall, caller, test, kernel implementation status, QEMU result, and hardware result. Use tracing on the conventional kernel reference build to discover real calls rather than guessing.

## 11. Phase 5 - Expand beyond coreutils

uutils maintains or participates in Rust replacements for more of the conventional base system. Consider these only as requirements emerge:

- findutils: `find`, `xargs`, `locate`, `updatedb`
- diffutils: `diff`, `cmp`, `diff3`, `sdiff`
- procps replacements: process display and system information
- util-linux replacements: selected low-level system utilities
- hostname, ACL, and related utilities

The [uutils organisation](https://github.com/uutils) tracks these projects. Maturity varies, so each must pass the same cross-build, behavioural, native-dependency, and runtime audit gates as coreutils.

For gaps, choose in this order:

1. Existing maintained Rust implementation with compatible licence.
2. Extend an existing uutils project and upstream the change.
3. Write a small purpose-specific Rust utility.
4. Temporarily use a clearly labelled Rust project such as RustyBox.
5. Use a C implementation only in a separate development image, never silently in the claimed Rust image.

## 12. Phase 6 - Dependency and native-code audit

This phase begins on day one and becomes a release gate.

### 12.1 Source dependency audit

For each workspace and vendored project:

```sh
cargo tree --all-features
cargo tree -e features
cargo tree -i libc
cargo metadata --locked --format-version 1
```

Search manifests and build scripts for:

```text
build.rs
cc
cmake
pkg-config
bindgen
autotools
meson
*-sys
#[link(...)]
extern "C"
```

`extern "C"` is an ABI declaration, not proof of C source, but every occurrence must be explained.

### 12.2 ELF audit

For every regular executable/library in the rootfs inspect:

```sh
file PROGRAM
readelf -h -l -d -s -n PROGRAM
objdump -p PROGRAM
nm -A PROGRAM
strings -a PROGRAM
```

Check for:

- `PT_INTERP`
- `DT_NEEDED`
- dynamic loader paths
- glibc symbol versions
- musl identification strings
- undefined libc symbols
- native archive member names
- unexpected `.so` files
- debug paths that disclose host data

`ldd` is not sufficient and should not be run on an untrusted foreign binary. It also cannot prove that a static binary contains no libc-derived code.

### 12.3 Link map and archive provenance

Generate linker map files. Preserve unstripped binaries and intermediate `.rlib`/`.a` files outside the runtime image. Expand archives and map every object member back to:

- crate
- source language
- source repository and revision
- compiler
- build script

Static musl can otherwise disappear into a single ELF while still violating L4.

### 12.4 Build-process containment

Run builds in a minimal container or sandbox where no undeclared host libraries are available. Deny network access during the reproducible build. Fail if:

- Cargo tries to update the lockfile
- a build script invokes an undeclared compiler
- `pkg-config` resolves a host package
- output changes between identical builds beyond understood metadata

### 12.5 Release manifests

Produce:

- source SBOM
- binary/rootfs manifest with hashes
- licence report
- native-code provenance report
- C-free level achieved and known exceptions
- reproducibility report

## 13. Phase 7 - Remove libc in controlled slices

Do not attempt to convert Ion and all uutils simultaneously. Establish a libc-free vertical slice.

### Slice 1 - raw syscall smoke executable

Write a `no_std`, `no_main` RISC-V executable that:

- starts at `_start`
- decodes arguments and environment
- writes to stdout
- opens and reads a file
- lists a directory
- obtains monotonic time and randomness
- allocates memory using `mmap`
- exits with a selected status

Use either a tiny internal syscall module or [rustix](https://github.com/bytecodealliance/rustix) where its `linux_raw` backend is usable. Rustix documents that `linux_raw` is selected by default on supported platforms unless the libc backend is requested. Verify the generated binary rather than relying only on feature selection.

### Slice 2 - libc-free PID 1

Port PID 1 to the raw runtime:

- console I/O
- mounts
- signal handling and child reaping
- process launch
- reboot/poweroff

This creates a useful strict-Rust component without requiring the shell to be ported yet.

### Slice 3 - small utilities

Port or implement:

- `true`, `false`, `echo`
- `cat`
- `ls-lite`
- `mkdir`, `rm-lite`
- `mount`, `umount`
- `sleep`

Use them to validate the runtime API surface.

### Slice 4 - reusable platform crate

Factor only proven needs into a platform layer:

- owned file descriptors
- paths/C-string conversion without implicit allocation surprises
- file operations and directory iteration
- spawn/exec/wait
- pipes and redirection
- signals
- TTY/termios
- clocks, randomness, identity, mounts
- allocator and synchronization primitives

Keep unsafe code concentrated, documented, and fuzz/testable. Do not create a broad pseudo-libc before callers demonstrate the requirement.

### Slice 5 - Ion port

Ion is the priority because it exercises the complete interactive process model.

Likely work:

- replace or adapt Unix system abstraction modules
- replace terminal/liner dependencies if they assume libc
- provide raw process spawning and waiting
- implement pipes and descriptor redirection
- implement signals and job control
- remove libc-backed randomness, time, environment, identity, and filesystem calls
- decide whether to retain full Rust `std`, use a custom `std`, or port Ion toward a constrained platform facade

Do not rewrite the parser or shell language. Preserve upstream logic and isolate platform changes behind traits/modules.

### Slice 6 - uutils port

uutils is much broader than Ion. Divide applets by syscall/runtime complexity:

1. Pure computation or simple stream I/O.
2. Basic filesystem operations.
3. Metadata, users/groups, permissions, sparse files, extended attributes.
4. Process, terminal, locale, date/time, and platform-specific commands.

Enable only applets that meet the current C-free level. A smaller honestly audited multicall binary is better than a complete binary with hidden libc dependencies.

## 14. Rust `std` strategy decision

After the raw PID 1 and utilities work, choose one of three strategic directions.

### Strategy 1 - accept static musl for Ion/uutils

Best if the objective is a useful Rust-authored system rather than absolute provenance purity.

- Keep strict libc-free components where easy.
- Document static musl as the only C exception.
- Spend effort on kernel and application features rather than toolchain internals.

### Strategy 2 - build a libc-free Linux `std`

Best if existing Rust applications must compile mostly unchanged.

- Fork the relevant `std::sys` implementation.
- Replace libc calls with direct syscall/platform code.
- Supply startup, TLS, signals, threading, unwinding/abort, allocator, DNS, and environment facilities.
- Maintain a custom Rust toolchain or target.

This has the highest compatibility payoff but a large permanent maintenance cost.

### Strategy 3 - port Ion/uutils away from full `std`

Best for a deliberately constrained appliance userspace.

- Use `alloc` plus internal platform crates.
- Remove unsupported features.
- Replace `std` types gradually or provide narrow compatibility facades.

This may produce the smallest system but requires extensive application changes and can diverge from upstream.

### Recommended choice

Maintain the musl build as the permanent reference and rescue development image. Develop Strategy 2 experimentally because it offers the best chance of keeping Ion and uutils close to upstream. If its maintenance cost becomes excessive, retain L2 as the practical product and use L4 only for PID 1 and security-critical services.

## 15. Testing programme

### 15.1 Test matrix

| Build | Architecture | Kernel | Purpose |
|---|---|---|---|
| Host GNU | x86-64 | Distribution Linux | Fast behavioural reference |
| Host musl | x86-64 | Distribution Linux | Static/musl issue separation |
| Guest musl | RISC-V 64 | Upstream Linux | Cross-target reference |
| Guest musl | RISC-V 64 | Reduced/translated kernel | Kernel compatibility |
| Guest raw | RISC-V 64 | Upstream Linux | libc-free runtime proof |
| Guest raw | RISC-V 64 | Reduced/translated kernel | Final integration |
| Hardware | RISC-V 64 | selected kernel | Real TTY/device validation |

### 15.2 Automated boot tests

The harness should:

1. Start QEMU with serial output captured.
2. Detect a unique boot-ready marker from PID 1.
3. Wait for an Ion prompt marker.
4. Submit commands over the serial console.
5. Verify stdout, stderr, and exit status markers.
6. Exercise Ctrl-C and child reaping.
7. Request shutdown.
8. Fail on timeout, kernel panic, PID 1 panic, unexpected reboot, or rescue-loop entry.

### 15.3 Shell integration cases

At minimum:

```text
echo hello
printf ... | wc -c
cat < input > output
false followed by exit-status inspection
PATH lookup through multicall symlinks
quoted spaces and Unicode
glob with zero, one, and many matches
large pipeline exceeding pipe buffer capacity
foreground long-running command interrupted by Ctrl-C
background child followed by job inspection/wait
Ion script launched from initrc
```

### 15.4 uutils tests

- Run upstream tests on the host.
- Cross-run a selected conformance subset under QEMU user mode where appropriate.
- Run integration tests under system-mode QEMU against the actual rootfs.
- Compare output and exit codes with GNU coreutils where compatibility is intended.
- Add tests for initramfs limitations, `/proc`, devtmpfs, permissions, read-only root, and tmpfs.

QEMU user-mode emulation is a weaker signal specifically for the failure class the 2022 manual riscv64gc-musl run actually found (Section 3.3): permission handling, signal timing, and musl struct-shape edge cases. User-mode QEMU translates syscalls through the host kernel rather than exercising a real guest kernel, so uid/gid and signal-timing behavior do not emulate faithfully. Treat user-mode results for those specific categories as informative only, not as a substitute for the system-mode runs against the actual rootfs.

### 15.5 Robustness and security

- malformed Ion syntax and startup files
- extremely long arguments/environment
- failed allocation and full tmpfs
- interrupted system calls
- invalid UTF-8 filenames
- broken pipe and closed console
- symlink races in file utilities
- descriptor leaks across `exec`
- orphan and zombie storms
- signal floods
- fuzz Ion parser and any new syscall/path parsing layers

## 16. Size and performance programme

Record, do not guess:

- compressed and uncompressed initramfs size
- Ion and coreutils ELF size before/after stripping
- unique code size versus duplicated individual applets
- boot time to PID 1 and prompt
- resident memory at idle prompt
- process-spawn latency
- pipeline throughput
- startup time for common applets

Compare:

- uutils multicall versus selected individual binaries
- panic abort versus unwind
- LTO choices
- musl versus raw runtime
- full Unicode versus constrained features

Do not sacrifice correctness, testability, or upstream compatibility for small size until measurements show that size is a real constraint.

## 17. Security and unsafe-code policy

- Deny unsafe code by default in new high-level crates.
- Allow unsafe only in the syscall/runtime/platform boundary and document each invariant.
- Prefer owned file-descriptor types and capability-like APIs.
- Mark descriptors close-on-exec unless deliberately inherited.
- Avoid global mutable state in PID 1 and signal paths.
- Use async-signal-safe raw operations inside signal handlers, or avoid complex handlers through signal-fd/event-loop designs where supported.
- Compile with stack and relocation hardening appropriate to static RISC-V executables, measuring compatibility.
- Use read-only rootfs plus tmpfs writable paths initially.
- Run interactive Ion as a non-root user once device/mount setup is complete, if the appliance model permits it.
- Separate privileged service actions from ordinary shell use.

## 18. Known risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Ion experimental status | Build breakage or behaviour changes | Pin commit and lockfile; maintain tests and small patch set |
| Ion non-POSIX syntax | Existing shell scripts do not work | Write Ion-native startup scripts; do not promise `/bin/sh` compatibility |
| Hidden libc in static ELF | False C-free claim | Link maps, archive provenance, symbol/disassembly audit |
| Rust `std` depends on libc facilities | Blocks L4 for upstream apps | Raw platform prototype, custom `std` research, keep musl reference |
| uutils applet differences | Scripts or recovery procedures fail | Per-applet conformance tests and allow-list |
| Job-control/TTY gaps | Poor or broken interactive shell | Dedicated PTY/termios/process-group test suite |
| Reduced kernel lacks obscure syscall | Runtime failures | Trace upstream kernel reference and maintain syscall coverage matrix |
| Dependency update introduces native code | Silent regression | Locked builds, Cargo policy checks, automated native-code gate |
| PID 1 mishandles children/signals | Zombies, hang, lost console | Small dedicated init with stress tests |
| Custom toolchain maintenance burden | Project stalls | Keep L2 musl path healthy and define stop/go criteria for L4 |
| Rootfs accidentally includes host file | Security/provenance failure | Manifest-based assembly in isolated build environment |

## 19. Milestones and exit criteria

### M0 - Reproducible host build

- Ion and uutils pinned and built.
- Host integration tests pass.
- No accidental GNU utility invocation in the test environment.

### M1 - RISC-V static executables

- Ion and uutils cross-compile for RISC-V musl.
- Each runs under RISC-V Linux.
- ELF audit confirms no dynamic interpreter and no `DT_NEEDED` entries.

### M2 - First Rust userspace prompt

- Rust PID 1 reaches Ion on QEMU serial.
- Approved uutils applets work.
- No BusyBox or C executable exists in initramfs.
- This is L2, not yet L4.

### M3 - Reliable interactive system

- Pipelines, redirection, signals, foreground jobs, child reaping, recovery, and shutdown pass automated tests.
- Kernel syscall coverage is documented.

### M4 - Expanded useful base

- Required procps/findutils/util-linux functions are supplied by audited Rust programs.
- Rootfs is reproducible and has SBOM/licence/provenance reports.

### M5 - Libc-free vertical slice

- `crates/syscall-smoke` and `crates/init-raw` (the raw-syscall PID 1, distinct from `crates/init` — see 2.3) contain no libc-derived objects, including `compiler_builtins` (Section 21.1).
- Link maps and object provenance support the L4 claim for those components.

### M6 - Libc-free Ion prototype

- Ion reaches an interactive prompt through the new platform/runtime path.
- External execution, pipes, redirection, TTY, signals, and job control pass.
- Remaining unsupported features are documented.

### M7 - Audited libc-free utility subset

- A declared uutils subset or compatible fork builds without C objects.
- Multicall invocation passes conformance tests.
- Runtime image contains no hidden C fallback.

### M8 - Strict release candidate

- Every shipped userspace byte has recorded provenance.
- No C-derived object code is found.
- Exceptions such as assembly and firmware are explicitly listed.
- QEMU and hardware acceptance tests pass.
- The exact achieved claim is stated as L4 or L5.

## 20. Suggested first implementation sprint

Work packages A-E are co-equal and fit one sprint. Work package F does not — see 20.1.

### Work package A - crates/vendor/patches layout

- Establish `crates/`, `vendor/`, `patches/` layout under the existing repo root and toolchain pin (Section 4).
- Pin Ion and uutils commits.
- Add host builds and dependency inventory.
- Create the applet allow-list.

### Work package B - minimal PID 1

- Implement `crates/init` (musl-std): console setup, mounts, child launch/reaping, and shutdown.
- Add recovery loop.
- Unit-test state transitions on the host where possible.

### Work package C - initramfs variant

- Add `--rootfs-variant ion-uutils` to `scripts/build_initramfs.py` (Section 4.1).
- Install Ion/coreutils and approved symlinks into the variant tree.
- Extend the existing manifest/compressed-archive output to cover the variant.
- Reject unexpected ELF files.

### Work package D - boot integration

- Extend `scripts/boot_qemu.py`'s `ensure_initramfs()` to select the `ion-uutils` variant image.
- Confirm the Ion/uutils PID 1 emits the same `INIT_REACHED` marker `scripts/kunit_oracle.py` already gates on.
- Confirm `docs/status/boot-history.csv`/`docs/status/boot-logs/` rows are produced via the existing `archive_boot()` path, using a `--run-id` that identifies the variant.
- Add shutdown and timeout handling to the smoke-command sequence.

### Work package E - C/native audit

- Cargo graph scan.
- ELF dynamic dependency scan.
- Link-map preservation.
- Rootfs hash/SBOM output.
- Initial report accurately labelled L2.

### 20.1 Work package F - strict runtime experiment (own phase, not sprint 1)

- Implement raw `_start` and RISC-V syscall veneer.
- Write `crates/syscall-smoke`.
- Build `crates/init-raw` prototype.
- Compare the API requirements with Ion and uutils dependency calls.
- Verify `compiler_builtins` provenance per Section 21.1 for every binary produced.

This work package is disproportionately larger and riskier than A-E and should not be scheduled as a co-equal sixth item in the same first sprint. Reasons, both already in this plan and from direct project experience:

- Section 10.3 flags terminal/job-control/TTY handling as the highest-risk kernel-facing area for Ion, and Work Package F's stated target — hand-written raw `_start` plus RISC-V syscall veneer, no translator or `std` assistance at all — sits directly on that same flagged area once it extends past the smoke program toward PID 1 process/signal handling.
- Section 14 treats the libc-free `std` question as a multi-strategy research decision to be made *after* the raw PID 1 and utilities work exists, not before — meaning Work Package F's output is an input to a decision, not a fixed-scope deliverable with a known shape going in.
- This project's own measured throughput is the sizing reference: getting one already-mechanically-translated, rule-conformant kernel `.c` file to link and boot inside an already-working kernel took hours per file this session, with a long tail of non-obvious gap classes, using tooling assistance. Work Package F has no translator assist at all and targets a harder problem (raw entry, syscall ABI, no reference implementation to diff against) than a single translated file.

Treat Work Package F as its own phase, sized and scheduled after A-E land and after the Section 21 decisions (especially 11, `compiler_builtins`) are recorded, not as a parallel sixth track inside the first sprint.

## 21. Decisions to record before coding deeply

1. Is the final claim about the shipped userspace only, or must host build tools also be Rust?
2. Is reviewed RISC-V assembly acceptable? It should be.
3. Is static musl an acceptable permanent fallback/recovery image?
4. Must Ion retain all interactive job-control features?
5. Must existing POSIX shell scripts work? Ion alone will not provide that promise.
6. Which uutils applets are genuinely required?
7. Will the system remain an initramfs appliance, or pivot to a persistent root filesystem?
8. Is networking in scope, and if so are DNS, TLS, DHCP, and time synchronization required?
9. Must users/groups, login, permissions, and multi-user isolation work?
10. Is the target only RISC-V 64 little-endian, or must x86-64/AArch64 remain supported as reference targets?
11. How is `compiler_builtins` built, and how is that verified?

Recommended defaults: shipped-runtime scope; assembly permitted and audited; musl fallback retained; full Ion interactive behaviour; no POSIX compatibility promise; minimal applet allow-list; initramfs first; networking deferred; single-user console first; RISC-V final with x86-64 as a host reference.

### 21.1 Decision 11 in detail: `compiler_builtins` and `optimized-compiler-builtins`

`compiler_builtins` is an implicit dependency of every `no_std` Rust program via `core` — this includes `crates/syscall-smoke` (Section 13, Slice 1) and `crates/init-raw`, the plan's own strictest-claim deliverables. Its `Cargo.toml` declares `links = "compiler-rt"` and, by default, compiles real C source for the routines it provides. `rust-lang/rust`'s `bootstrap.example.toml` sets `optimized-compiler-builtins = true` for any non-`dev` release channel, so the prebuilt `compiler_builtins.rlib` shipped by rustup for stable/beta/nightly is plausibly C-compiled today, independent of whether application code is `no_std`/syscall-only. An L4 claim is false by default unless this is addressed.

Fix (nightly-gated, no stable-channel equivalent exists):

```toml
# rust-toolchain.toml
[toolchain]
channel = "nightly-2026-XX-XX"   # pin to the exact validated nightly
components = ["rust-src"]
```

```sh
cargo +nightly build \
  -Zbuild-std=core,alloc \
  --config profile.dev.build-override.opt-level=2 \
  --set build.optimized-compiler-builtins=false \
  --target riscv64gc-unknown-linux-musl
```

`--set build.optimized-compiler-builtins=false` forces the pure-Rust intrinsics path instead of the C implementation, and `-Zbuild-std` is required so `core`/`compiler_builtins` are rebuilt at all rather than consuming rustup's prebuilt rlib.

This is a build-time flag, not proof. Verify against the actual produced artifact, the same instinct Section 13 Slice 1 already states ("verify the generated binary rather than relying only on feature selection"):

```sh
# no object member sourced from compiler-rt's C files should be present
ar t libcompiler_builtins-*.rlib | grep -i '\.o$'
nm -A libcompiler_builtins-*.rlib | grep -E ' T | t ' | head
objdump -d target/riscv64gc-unknown-linux-musl/release/syscall-smoke | grep -A5 '<__mulsi3>\|<__udivdi3>'
```

Record the exact nightly date, the flags used, and the `nm`/`objdump` verification output in `docs/c-free-audit.md` for every L4-claiming binary. Re-verify after every toolchain bump — `optimized-compiler-builtins` defaults and `-Zbuild-std` behavior are nightly-only and unstable.

## 22. Definition of done

The project is complete at the practical milestone when:

- the kernel boots a dedicated Rust PID 1;
- Ion provides the only normal interactive shell;
- uutils provides the approved normal commands through a multicall executable;
- no BusyBox, Bash, GNU coreutils, glibc executable, conventional init, or C daemon ships in the image;
- all binaries are static and the image boots reproducibly on RISC-V QEMU and target hardware;
- behaviour, syscall coverage, provenance, licences, and exceptions are documented.

The strict C-free research goal is complete only when:

- no shipped ELF contains C-derived objects, including statically linked libc/startup code;
- all native-code-producing build dependencies are identified;
- `_start`, allocation, panic behaviour, TLS, threading, signals, process management, and syscall entry have audited Rust/assembly implementations;
- Ion and the approved uutils subset pass their integration/conformance tests through that runtime;
- an independent audit can reproduce the build and verify the claim from source to final initramfs.

## 23. Primary references

- [Ion source repository (GitLab, canonical)](https://gitlab.redox-os.org/redox-os/ion) — the [GitHub copy](https://github.com/redox-os/ion) is a read-only mirror (`has_issues: false`); use the GitLab repository, not the GitHub star/commit/issue counts, to judge project activity.
- [Ion manual](https://doc.redox-os.org/ion-manual/)
- [uutils project](https://uutils.org/)
- [uutils coreutils](https://uutils.org/coreutils/)
- [uutils multicall documentation](https://github.com/uutils/coreutils/blob/main/docs/src/multicall.md)
- [uutils GitHub organisation](https://github.com/uutils)
- [Official Rust RISC-V musl target documentation](https://doc.rust-lang.org/rustc/platform-support/riscv64gc-unknown-linux-musl.html)
- [rustix source and backend documentation](https://github.com/bytecodealliance/rustix)
- [relibc source repository (GitLab, canonical)](https://gitlab.redox-os.org/redox-os/relibc) — the [GitHub copy](https://github.com/redox-os/relibc) is a read-only mirror; same activity-tracking caveat as Ion above.
- [Redox OS userspace architecture](https://doc.redox-os.org/book/user-space.html)

