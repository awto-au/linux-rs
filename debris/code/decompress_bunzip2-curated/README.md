# decompress_bunzip2 curated archive

Preserved from `linux-riscv-worktrees/combined-c2rust-boot-22/` on 2026-07-20.

Contents:
- `decompress_bunzip2_rs.rs`
- `bunzip2_kunit_test.bz2`

Why this is in `debris/`:
- This material is curated and has recovery value.
- It is not the active path for current work.
- The active policy is that landed Rust ports should be reproducible from the original C file, current c2rust, and generic committed scripts/rules.

Status of active tree:
- The incorrect ad hoc in-tree porting path was removed from `linux-riscv/lib/`.
- Fresh c2rust evaluation for `lib/decompress_bunzip2.c` now lives under:
  - `tmp/c2rust-reference-check/lib_decompress_bunzip2.c/output/src/decompress_bunzip2.rs`

Primary evidence / history:
- `docs/combined-boot-attempt-2026-07-18.md`
- `docs/status/boot-logs/20260719T205026+1000-combined-c2rust-boot-22-bunzip2test.log`
