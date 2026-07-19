# Safety Coverage Baseline Report — 2026-07-19

Source: `rulesdb/patterns.db`, `function_safety_status` + rollup views. No config/arch dimension exists in this schema (population is `landed_tu` | `c2rust_corpus` only, both riscv-only) — the requested multi-config scan attempted 4 non-riscv configs (csky, openrisc, xtensa, sh), **0 ran clean** (no LLVM/clang backend for any of the four on this host/toolchain; see commit history for detail). Numbers below are the two real populations that exist.

## landed_tu (riscv64-slim-serial, 38 files, 279 functions, 5476 LOC)

| Granularity | Strict-safe | Safe-with-exceptions |
|---|---|---|
| LOC | 6.6% | 6.6% |
| Functions | 18.3% (51/279) | 18.3% (51/279) |
| Files | 0.0% (0/38) | 0.0% (0/38) |
| Subsections | 0.0% (0/2) | 0.0% (0/2) |

## c2rust_corpus (533 files, 43069 functions, 1043313 LOC)

| Granularity | Strict-safe | Safe-with-exceptions |
|---|---|---|
| LOC | 0.0% | 0.0% |
| Functions | 0.0% (0/43069) | 0.0% (0/43069) |
| Files | 0.0% (0/533) | 0.0% (0/533) |
| Subsections | 0.0% (0/1) | 0.0% (0/1) |

Files/subsections counted safe only when 100% of their functions/LOC reach strict-safe or safe-with-exceptions.

## Non-riscv config scan

csky, openrisc, xtensa, sh: all blocked pre-build — clang has no codegen backend for any of the four (confirmed via `clang --print-targets`), and `scripts/Makefile.clang` has no `CLANG_TARGET_FLAGS_*` entry for any of them. No compile_commands.json produced; 0 files/functions scanned for all four.
