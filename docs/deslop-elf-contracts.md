# ELF format and normalization test evidence

This component covers the entire `buildlib.elf` module, including the previously qualified header component in [deslop-elf-header.md](deslop-elf-header.md). It does not complete DSLP-4 or qualify a CI lane. Runtime, dependencies, and public contracts are unchanged. Public consumers include `inspect` and `hardening_violations` (`src/buildlib/elf.py:259` and `:270`), and the build repositories named in AGENTS.md; their released-wheel pins are unaffected. Local caller search found node-build validation, packaging and reproduction (`node-build/buildsys/validate_product.py:166`, `package.py:65`, `reproduce.py:14`) and cmake-build hardening (`cmake-build/buildsys/elf.py:12`); parent follow-up inspected pi-build's tooling and found locks, hashing, release and targets imports, with no ELF or Mach-O caller.

Base `dbc5e5a3da7d2351fa21b3df045511c2dae35cc0`, macOS arm64, native CPython 3.15.2, pytest 9.2.0, uv 0.13.7; scratch mutation CPython 3.15.1 and mutmut 3.8.1. SHA-256: `src/buildlib/elf.py` `ef8cfe40095cb787e73649cf11074e1fc6cd292bca59db82c1b26fa95974386a`, `tests/binaries.py` `556e46f12658790b6bb23cc6cb03a397265903104a31d16d987140e66ecf2be1`; before `tests/test_elf.py` `05fef53050461b68bd919ad1d05864498c239b6842b3378c5c42602554dbb72d`, after `ef71960aa783cd39edd55414f049b66d3b93991663b29e8e2fa2ea6b3fcf1f29`.

## Scenario

Fresh source and test copies used the following scratch `pyproject.toml`; no project configuration was changed.

```toml
[tool.mutmut]
source_paths = ["src"]
pytest_add_cli_args_test_selection = ["tests/test_elf.py"]
timeout_constant = 5.0
[tool.pytest.ini_options]
pythonpath = ["tests"]
```

Create each copy with `mkdir -p .work/tmp/elf-before .work/tmp/elf-candidate` and `cp -R src tests .work/tmp/elf-before/` (repeat for `elf-candidate` after editing); write the configuration above as each copy’s `pyproject.toml`. With `copy` set to its absolute path, the complete run was:

```sh
export TMPDIR="$PWD/.work/tmp" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
lock=/Users/josh/d/stuff/.work/tmp/mutation.lock
owned=0
for attempt in {1..60}; do
    if mkdir "$lock" 2>/dev/null; then owned=1; break; fi
    sleep 1
done
if [ "$owned" -ne 1 ]; then exit 1; fi
trap 'rmdir "$lock"' EXIT
cd "$copy"
/usr/bin/time -p timeout -k 5 1200 cpu-guard --cores 2 /Users/josh/repos/pytest-parallel/.work/wt/mut/.work/tmp/mutenv/bin/mutmut run --max-children 1 'buildlib.elf.*'
/Users/josh/repos/pytest-parallel/.work/wt/mut/.work/tmp/mutenv/bin/mutmut results --all true
```

The actual copies were `.work/tmp/elf-before` and `.work/tmp/elf-candidate`. Both runs exited 0; wall times including baseline and generation were 20.03 s and 21.10 s. An intermediate run took 20.01 s and a second intermediate run 20.03 s. Each generated 509 ELF identities; comparison of the generated function names against `results --all true` found 509 results, no missing or extra identities. All 1,173 other-package identities were `not checked`. Before: 409 killed, 90 survived, 10 no tests. After: 469 killed, 40 survived, zero no-tests, timeouts, suspicious, skipped, or other classes. No timeout needed confirmation. No exclusions were configured. The old 1,682 whole-package observation is historical, not this selection.

Before, the following numeric suffixes survived; `is_elf` 1–10 had no tests. Every other generated ELF identity was killed. After, only the identities listed in the disposition below survive; all other listed before identities and the ten previously untested identities are killed.

- `_read_at`: 1–6, 11; `_read_header`: 14, 24, 27, 40, 45, 48, 55, 56; `_segment_bytes`: 6, 9, 16, 17; `interpreter`: 6, 15–17.
- `_map_va`: 2, 4–6, 9; `_read_dynamic_entries`: 12, 15, 21, 22, 31, 32, 42, 43; `_dynamic_entries`: 15, 17, 19, 22, 24, 26, 27, 30, 41, 44, 51, 52; `_dyn_string`: 1–5, 14, 17, 18, 22, 24–26.
- `has_relro`: 2; `stack_nx`: 4; `has_bind_now`: 5, 9, 10; `hardening_violations`: 10–12, 40, 41; `build_id_ranges`: 13, 16, 22, 23, 27, 28, 35, 37, 51, 52, 54, 55, 57, 60, 64; `normalized_bytes`: 6–8, 17, 18.

## Remaining survivors

Each identity below is `buildlib.elf.x_<function>__mutmut_<suffix>`. Diagnostic mutations retain the same exception type or violation condition; no wording guarantee is asserted. There are no accepted-risk or unobservable exclusions.

- `_read_at` 6, 11; `_read_header` 14, 24, 27, 40, 45, 48, 55, 56; `_segment_bytes` 6, 9, 16, 17; `_read_dynamic_entries` 12, 15, 21, 22; `_dynamic_entries` 41, 44, 51, 52; `_dyn_string` 5; `build_id_ranges` 13, 16, 22, 23: error message or its path/description argument only (`elf.py:91`, `:108`, `:149`, `:168`, `:183`, `:195`, `:301`). `_read_at` uses path and description only for errors; the public malformed cases still check exceptions.
- `hardening_violations` 40, 41: change the wording of the lazy-binding diagnostic only (`elf.py:292`); the lazy condition and list entry remain.
- `_read_at` 1: changing `or` to `and` affects only negative offset or size; all public callers obtain them from unsigned ELF fields, nonnegative products, or the mapped difference guarded by `vaddr <= addr` (`elf.py:120`, `:151`, `:170`, `:189`, `:310`). Zero offset and size are exercised at `tests/test_elf.py:408`.
- `interpreter` 15 and `_dyn_string` 24: `UTF-8` is the same Python codec as `utf-8`; public malformed byte witnesses are at `tests/test_elf.py:328` and `:380`.
- `_read_dynamic_entries` 43: signed versus unsigned tag decoding differs only for high-bit tags. All public consumers compare tags to nonnegative constants; negative or high-bit unknown tags are ignored (`elf.py:127`, `:186`, `:206`, `:212`, `:235`). Values remain unsigned.
- `_dynamic_entries` 15, 22: `None` instead of zero as a missing table address or size is equally false in the immediately following check (`elf.py:186–189`); public missing-field cases are at `tests/test_elf.py:345`.
- `build_id_ranges` 27, 28: with exactly 12 bytes left, processing can only append a range if a GNU name is present, but no name bytes remain; otherwise both variants return the same ranges. The zero-size header witness is at `tests/test_elf.py:402`.
- `normalized_bytes` 6, 7: a build-ID descriptor begins at least 12 bytes after its unsigned note offset, so offset zero cannot arise from `build_id_ranges`; 8: an offset equal to file length can only have size zero after the note segment length check, and assigning an empty slice changes no bytes (`elf.py:306–329`).

## Controls and checks

Independent literal ELF images exercise dynamic entry ordering, exact segment boundaries, unsigned flags, malformed and unterminated strings, interpreter replacement decoding, multiple notes, padding, truncated notes, and normalization (`tests/test_elf.py:300–413`). Existing tests cover file traversal, permissions, headers, hardening and public reports. Five scratch hand faults were applied one at a time to a copy of `src/buildlib/elf.py`; each targeted `cpu-guard --cores 2 <mutenv-python> -m pytest -q <scratch-tests>/test_elf.py::<test>` exited 1, then 0 after restoration:

| Test suffix | Fault and failure |
| --- | --- |
| `magic_reader_handles_files_and_missing_paths` | read five magic bytes; positive assertion failed |
| `dynamic_strings_mapping_and_entry_boundaries` | change `vaddr <= addr` to `<`; valid boundary raised `ELFError` |
| `interpreter_and_hardening_flags` | strict UTF-8; invalid byte raised `UnicodeDecodeError` |
| `note_ranges_preserve_padding_and_ignore_other_notes` | change descriptor padding `+ 3` to `- 3`; second range missing |
| `empty_segments_and_zero_offset_are_readable` | reject offset zero; empty dynamic segment raised `ELFError` |

With `PYTHONPATH="$PWD/mutants/src:$PWD/tests"`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, the mutenv Python printed `.work/tmp/elf-candidate/mutants/src/buildlib/elf.py` as the loaded module. `MUTANT_UNDER_TEST=buildlib.elf.x_build_id_ranges__mutmut_52 cpu-guard --cores 2 <mutenv-python> -m pytest -q tests/test_elf.py::test_note_ranges_preserve_padding_and_ignore_other_notes` exited 1 at the literal range assertion; `MUTANT_UNDER_TEST=''` with the same command exited 0. The first focused test attempt lacked `PYTHONPATH` and failed import; retry with `PYTHONPATH="$PWD/src:$PWD/tests"` passed.

With `TMPDIR="$PWD/.work/tmp"`, `timeout -k 5 1200 cpu-guard --cores 2 -- make lint` exited 0 (ruff and ty clean). The supplied matched native warm baseline was 149 tests, 6.40 s, gates 0/0. After warming, `perf-gate-ready`, `/usr/bin/time -p timeout -k 5 1200 cpu-guard --cores 2 -- make test`, and immediate `perf-gate-ready -1` each exited 0: 154 passed, 6.42 s real (pytest 5.33 s). An earlier full suite also passed 154 in 6.42 s, with both gates 0; its postcheck printed a hot-process diagnostic but exited 0. These single observations are within the 20%/30 s suite-cost limit, not a speedup claim. Shipping runtime is unchanged; no runtime benchmark or consumer override was needed. Broader coverage, other modules, and the DSLP-4 CI lane remain open.
