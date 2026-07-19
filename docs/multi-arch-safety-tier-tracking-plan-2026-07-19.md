# Plan: safety-tier and arch/endian tracking, per function/file

No code/schema/Kconfig applied yet — DDL below is proposed.

## Axes

**"3 versions" = arch/endian target matrix**: 32-bit × 64-bit × {little, big} = 4 real combos (no 128-bit, no third endianness in the kernel). Targets: riscv64-LE (shipping), riscv32-LE, riscv64-BE, riscv32-BE.

**Safety tier** (PLAN.md Phase 2 step 3 / Phase 2.5): `unsafe-baseline` → `safe-lifted` → `optimised` (pure-leaf only). Independent axis.

A function's state = matrix cell `(safety_tier, target_config)`, each `not_attempted | attempted | verified(tier N)`.

## Current state

Zero rv32/big-endian/x86_64 infra exists (`configs/` has one defconfig). `compile_commands.json` generation is vendored kernel tooling; every consuming script hardcodes one tree/`ARCH=riscv` (`dev.py:70,175`, 8 other scripts). This is greenfield bring-up, not extending dormant infra. Upstream precedent for endian-gated Rust: `arch/arm/Kconfig:139` — `select HAVE_RUST if CPU_LITTLE_ENDIAN && CPU_32v7 && !KASAN`. `arch/riscv/Kconfig:405` — `ARCH_RV32I depends on NONPORTABLE` (rv32 is non-default even upstream).

## Schema

```sql
CREATE TABLE translation_targets (
    id INTEGER PRIMARY KEY,
    arch TEXT NOT NULL,            -- Kconfig ARCH= value
    bits INTEGER NOT NULL,         -- 32 | 64
    endian TEXT NOT NULL,          -- 'little' | 'big'
    defconfig TEXT NOT NULL,
    is_shipping_target INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL
);
-- seed: (riscv, 64, little, riscv64-slim-serial, 1, <today>) — only real row.
-- 3 more rows added only once each has a real defconfig + compile_commands.json:
-- (riscv, 32, little, ?, 0, ?), (riscv, 64, big, ?, 0, ?), (riscv, 32, big, ?, 0, ?)

ALTER TABLE file_oracle_status ADD COLUMN target_id INTEGER NOT NULL
    REFERENCES translation_targets(id) DEFAULT 1;
-- old UNIQUE (c_file, population, tier) -> (c_file, population, tier, target_id)
-- existing rows backfill to target_id=1 (only config ever checked)

ALTER TABLE rules ADD COLUMN safety_tier TEXT
    CHECK (safety_tier IN ('unsafe-baseline','safe-lifted','optimised') OR safety_tier IS NULL);
-- NULL for tier-agnostic rules (fls-family, likely-unlikely, ...); non-NULL
-- only for 0023/0024/0025 (safe-lift-lock-guard/refcount/aref-ownership)

CREATE TABLE file_safety_tier_status (
    c_file TEXT NOT NULL,
    safety_tier TEXT NOT NULL,
    target_id INTEGER NOT NULL REFERENCES translation_targets(id),
    oracle_tier INTEGER NOT NULL,  -- PLAN.md's 5-tier oracle, same numbering as file_oracle_status
    status TEXT NOT NULL,
    detail TEXT,
    evidence_ref TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (c_file, safety_tier, target_id, oracle_tier)
);
-- separate table, not folded into file_oracle_status: population
-- (landed_tu/c2rust_corpus) and safety_tier answer different questions --
-- c2rust only emits unsafe-baseline today, so a shared key would be
-- mostly-empty cells for every c2rust_corpus row. Join on (c_file, target_id).
```

## Kconfig vs rulesdb

Kconfig already does build-time gating (proven 5x: `CONFIG_RUST_8250_STARTUP`/`CONFIG_RUST_8250_IRQ`, `drivers/tty/serial/8250/Kconfig:37-72`):

```kconfig
config RUST_8250_STARTUP
	bool "Rust translation of serial8250_do_startup/serial8250_do_shutdown"
	depends on SERIAL_8250 && RUST
	default n
```

Kconfig has no "verified" concept — only "buildable." rulesdb already has `file_oracle_status`'s highest-tier-passed view. Hybrid: Kconfig gates safety-tier build selection (new `CONFIG_RUST_<SLICE>_SAFE` options, same pattern); rulesdb tracks arch/config + verification evidence. Arch/endian needs no new per-function Kconfig — a kernel image is already arch-specific by construction (`ARCH=` + defconfig), so "does this apply on rv32" is answered by which kernel got built, not a per-function knob.

## Multiple candidate translations of one C file

Same C file, several competing Rust candidates (e.g. hand-translated vs c2rust-clean vs a second-pass rewrite) before one is picked canonical. Different axis from safety-tier/target — those assume one candidate per cell; this is N candidates *for* one cell, until pruned to 1.

```sql
CREATE TABLE translation_candidates (
    id INTEGER PRIMARY KEY,
    c_file TEXT NOT NULL,
    candidate_name TEXT NOT NULL,      -- 'hand', 'c2rust-raw', 'c2rust-v2', ...
    rs_path TEXT NOT NULL,
    kconfig_symbol TEXT NOT NULL,      -- 'RUST_KLIST_HAND', 'RUST_KLIST_C2RUST'
    is_canonical INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    UNIQUE (c_file, candidate_name)
);
-- file_safety_tier_status / file_oracle_status rows key on (c_file, ...) today;
-- add candidate_id so a candidate keeps its own verification history --
-- switching the picked candidate must not discard the losing one's evidence.
ALTER TABLE file_oracle_status ADD COLUMN candidate_id INTEGER
    REFERENCES translation_candidates(id);
```

Kconfig side — a `choice` block, same mechanism as `RUST_8250_STARTUP`, one symbol per candidate, mutually exclusive:

```kconfig
choice
	prompt "klist.c Rust translation source"
	depends on RUST
	default RUST_KLIST_C2RUST

config RUST_KLIST_HAND
	bool "hand-translated"

config RUST_KLIST_C2RUST
	bool "c2rust-generated"

endchoice
```

`Makefile` picks the object per selected symbol, same pattern as any other `CONFIG_*`-gated source file — no new build mechanism, just one `choice` per multi-candidate file instead of a plain `bool`. Promoting a candidate to canonical: flip `is_canonical`, change `default` in the `choice` block, leave the losing candidate's row and Kconfig symbol in place (still selectable, still has its evidence) rather than deleting it — matches this project's "never discard, flag `needs_reverification`" stance on drift elsewhere in this doc.

## Kernel-version drift

No content-hash exists anywhere (`c2rust_rev`/`corpus_rev` are git-revision strings, not per-file hashes; `sync_file_oracle_status.py`'s own docstring admits tiers 2/3 have no persisted record at all).

```sql
ALTER TABLE file_oracle_status ADD COLUMN c_source_sha256 TEXT NOT NULL;
ALTER TABLE file_safety_tier_status ADD COLUMN c_source_sha256 TEXT NOT NULL;
-- new status value: 'needs_reverification'
```

On kernel-tag bump: recompute each tracked file's hash. Match → evidence still valid. Mismatch → flag `needs_reverification`, keep old evidence queryable, never silently trust or drop it. Surface as a dashboard queue (extend `docs/status/dashboard.html`'s existing generation, same pattern as `work_items_active`).

## Pipeline: cheap vs expensive per axis

| | cheap (same logic, new flags) | expensive (new translation work) |
|---|---|---|
| safety tier | — | unsafe→safe-lifted (hand-translation only; c2rust emits unsafe-baseline exclusively, confirmed via `docs/python-transpiler-rewrite-scoping-2026-07-18.md`'s full crate audit) |
| target config | re-run oracle tiers 2-4 against new `compile_commands.json`, IF a working defconfig+boot exists | stand up a new defconfig + first boot (rv32/BE start here — nothing to reuse) |

Arch variants are NOT always just a recompile: `lib/math/gcd.c`'s Zbb static-key (`docs/phase2-first-translation.md`) shows identical C can exercise a different live path per config. Rule 0026 (`arch-override-dead-generic`) shows a function's body may not even compile for a different arch. Both require re-running oracle tiers 2-4 per target, not assuming carry-forward — this is why `target_id` is part of the key, not a global flag.

Work-item generation (extend `sync_work_items.py`): for each `unsafe-baseline` file at `target_id=1`, no `safe-lifted` row → queue Phase 2 step 3 work. No second `target_id` row at all → queue arch bring-up (defconfig + boot), independent of any per-file question.

## Not done here

Schema/Kconfig not applied. No rv32/BE defconfig stood up. Safe-lift translation pass not implemented (Phase 2 step 3 owns that). Drift-detection script not written (column/status design only).

## Why safe-lift still needs full oracle re-verification

`docs/python-transpiler-rewrite-scoping-2026-07-18.md`: this project's real historical bugs are unchecked map/graph lookups and wildcard-arm matches — Rust's exhaustiveness checking already doesn't catch this project's actual bug class, even inside c2rust-transpile's own all-Rust code. Safe-lifting's real value (PLAN's unsafe-class policy: raw pointers/locking → `Guard`/`Arc`/refcount types) is eliminating use-after-free/data-race shapes structurally, not adding compile-time exhaustiveness. `file_safety_tier_status` records `(safety_tier, oracle_tier)` together, not a boolean "upgraded" flag — a safe-lifted function still needs its own tier 2-4 pass, per README.md's "oracle certifies equivalence, never assumed correctness from representation alone."
