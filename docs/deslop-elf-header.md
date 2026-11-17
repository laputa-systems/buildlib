# ELF header test evidence

Scope: public `read_header` and `program_headers`, through `_read_header`. This is one Tests component, not completion of DSLP-4. The runtime is unchanged. `read_header` retains unknown type and machine values; `program_headers` returns all six exposed fields in file order. Both refuse malformed headers. `src/buildlib/elf.py:91-142` contains the parsing and public callers; other direct callers are at lines 184, 280 and 303.

## Inputs and costs

Base revision: `07e7bcb320e62642df733d5b162a7f2765602066`. SHA-256 of `src/buildlib/elf.py` before and after: `ef8cfe40095cb787e73649cf11074e1fc6cd292bca59db82c1b26fa95974386a`. SHA-256 of `tests/test_elf.py`: base `68b87a3ddcc72bbac168fcb8853a6d22060021c352f4521a97f467070a21e100`, final `b4a9aaaed2618602afa73eeb7fcf7c40b21f34c4f3c7a8c95c51350c06be130d`.

macOS 27.2, CPython 3.15.2, pytest 9.2.0, mutmut 3.8.1. Native locked suite, `TMPDIR="$PWD/.work/tmp"`, `cpu-guard --cores 2`: warm `make test` before 135 passed, 6.43 s; after 138 passed, 6.42 s. The immediate `perf-gate-ready` and `perf-gate-ready -1` each exited 0 for both timings. One pre-gate printed a hot-process diagnostic but returned 0. `make lint` exited 0 before and after. Focused ELF tests took 0.04 s before the final suite. The suite is the broad local integration layer; byte-built public API tests are the fast parser layer. No shipping runtime benchmark was requested.

## Scenario

Scratch copies of `src` and `tests` used this `pyproject.toml`:

```toml
[tool.mutmut]
source_paths = ["src"]
pytest_add_cli_args_test_selection = ["tests/test_elf.py"]
[tool.pytest.ini_options]
pythonpath = ["tests"]
```

From the worktree, create `.work/tmp/header-mutation`, copy `src` and `tests` there, and write that config. Set `TMPDIR` to the worktree's `.work/tmp` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Acquire `/Users/josh/d/stuff/.work/tmp/mutation.lock` using bounded `mkdir` retries and release only the acquired lock. From the scratch directory run:

```sh
timeout -k 5 1200 cpu-guard --cores 2 /Users/josh/repos/pytest-parallel/.work/wt/mut/.work/tmp/mutenv/bin/mutmut run --max-children 1 'buildlib.elf.x__read_header__mutmut_*'
```

Copy the changed test into the scratch `tests` directory and repeat. Both runs exited 0 in 4.31 s. The first attempt without disabled plugin autoload exited 1 because an installed pytest entry point could not import `pytest_parallel`; the serial retry passed. The generated `mutants/src/buildlib/elf.py` contains `x__read_header__mutmut_orig` and identities 1 through 98. The selection executes only these 98 identities; the 1,682 displayed progress denominator is the shared package inventory. Before: 80 killed, 18 survived, 0 timeout, 0 no-tests, 0 suspicious, 0 skipped, 0 type-check killed. After: 89 killed, 9 survived, all other raw classes 0. No timeout occurred, so no longer-timeout confirmation was needed. The before survivors were 7, 8, 14, 24, 27, 36, 40, 43, 45, 48, 55, 56, 57, 64, 76, 82, 85 and 86. The new tests kill 8, 36, 43, 57, 64, 76, 82, 85 and 86.

Remaining survivors, identified by their numeric suffix:

- 14, 24, 27 and 40 replace the message of an `ELFError` with `None`. The same inputs still raise `ELFError`; the public parser has no promised diagnostic wording. The independent malformed cases check acceptance and exception type separately.
- 45, 48, 55 and 56 change the path or description passed to `_read_at`. Those arguments affect only the error text, because `_read_at` uses them only when formatting its errors. Truncated tables still raise `ELFError`.
- 7 reads the whole file instead of 64 bytes. For finite regular files successfully read into memory, later parsing uses the same first 64 bytes and `_read_at` seeks explicitly. This is not equivalent under resource pressure: a large file can consume more memory or fail. The functional scenario does not prove a resource bound; this survivor remains an unprotected resource risk for the parent to disposition.

## Negative controls

In the scratch directory, set `PYTHONPATH="$PWD/mutants/src:$PWD/tests"` and use the mutenv Python with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Importing `buildlib.elf` printed the generated `mutants/src/buildlib/elf.py` path. With `MUTANT_UNDER_TEST` set to each full identity, run `python -m pytest -q` on the named test:

- `buildlib.elf.x__read_header__mutmut_64`, `test_header_and_program_headers_preserve_unsigned_fields_and_order`: exit 1, assertion reports `memsz=None` instead of the literal unsigned value.
- `buildlib.elf.x__read_header__mutmut_43`, `test_empty_program_table_does_not_read_its_offset`: exit 1, unwanted seek raises `ValueError`.
- `buildlib.elf.x__read_header__mutmut_8`, `test_public_header_readers_reject_independent_malformed_images`: exit 1, a short header escapes the intended `ELFError` and raises `struct.error`.

With `MUTANT_UNDER_TEST=''`, those three tests passed together, exit 0. These are generated semantic mutants, not edits to shipping source.

## Remaining scope

This run covers `_read_header` only and does not classify the rest of ELF, Mach-O, locks, release, targets, or wider DSLP-4 work. It does not close a TODO. The whole-file-read resource risk remains open.
