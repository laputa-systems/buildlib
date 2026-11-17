# ELF header test evidence

Scope: public `read_header` and `program_headers`, through `_read_header`. This is one Tests component, not completion of DSLP-4. The runtime is unchanged. `read_header` retains unknown type and machine values; `program_headers` returns all six exposed fields in file order. Both refuse malformed headers. `src/buildlib/elf.py:91-142` contains the parsing and public callers; other direct callers are at lines 184, 280 and 303.

## Inputs and costs

Base revision: `07e7bcb320e62642df733d5b162a7f2765602066`. SHA-256 of `src/buildlib/elf.py` before and after: `ef8cfe40095cb787e73649cf11074e1fc6cd292bca59db82c1b26fa95974386a`. SHA-256 of `tests/test_elf.py`: base `68b87a3ddcc72bbac168fcb8853a6d22060021c352f4521a97f467070a21e100`, final `05fef53050461b68bd919ad1d05864498c239b6842b3378c5c42602554dbb72d`.

macOS 27.2, CPython 3.15.2, pytest 9.2.0, mutmut 3.8.1. Native locked suite, `TMPDIR="$PWD/.work/tmp"`, `cpu-guard --cores 2`: warm `make test` before 135 passed, 6.43 s; after 139 passed, 6.45 s. The immediate `perf-gate-ready` and `perf-gate-ready -1` each exited 0 for both timings. A gate printed a hot-process diagnostic but returned 0. `make lint` exited 0 before and after. Focused ELF tests took 0.04 s before the final suite. The suite is the broad local integration layer; byte-built public API tests are the fast parser layer. No shipping runtime benchmark was requested.

## Scenario

Scratch copies of `src` and `tests` used this `pyproject.toml`:

```toml
[tool.mutmut]
source_paths = ["src"]
pytest_add_cli_args_test_selection = ["tests/test_elf.py"]
timeout_constant = 5.0
[tool.pytest.ini_options]
pythonpath = ["tests"]
```

From the worktree, create `.work/tmp/header-fresh`, copy `src` and `tests` there, and write that config. Set `TMPDIR` to the worktree's `.work/tmp` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Acquire `/Users/josh/d/stuff/.work/tmp/mutation.lock` using bounded `mkdir` retries and release only the acquired lock. From the scratch directory run:

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
cd .work/tmp/header-fresh
/usr/bin/time -p timeout -k 5 1200 cpu-guard --cores 2 /Users/josh/repos/pytest-parallel/.work/wt/mut/.work/tmp/mutenv/bin/mutmut run --max-children 1 'buildlib.elf.x__read_header__mutmut_*'
```

For the fresh baseline, replace the scratch `tests/test_elf.py` with `git show 07e7bcb320e62642df733d5b162a7f2765602066:tests/test_elf.py`. Copy the final test into scratch and repeat. The baseline and final runs exited 0 in 6.40 s and 7.48 s. The first attempt without disabled plugin autoload exited 1 because an installed pytest entry point could not import `pytest_parallel`; the serial retry passed. The generated `mutants/src/buildlib/elf.py` contains `x__read_header__mutmut_orig` and identities 1 through 98. The selection executes only these 98 identities; the 1,682 displayed progress denominator is the shared package inventory. Before: 80 killed, 18 survived, 0 timeout, 0 no-tests, 0 suspicious, 0 skipped, 0 type-check killed. After: 90 killed, 8 survived, all other raw classes 0. No timeout occurred, so no longer-timeout confirmation was needed. The before survivors were 7, 8, 14, 24, 27, 36, 40, 43, 45, 48, 55, 56, 57, 64, 76, 82, 85 and 86. The new tests kill 7, 8, 36, 43, 57, 64, 76, 82, 85 and 86.

Remaining survivors, identified by their numeric suffix:

- 14, 24, 27 and 40 replace the message of an `ELFError` with `None`. The same inputs still raise `ELFError`; the public parser has no promised diagnostic wording. The independent malformed cases check acceptance and exception type separately.
- 45, 48, 55 and 56 change the path or description passed to `_read_at`. Those arguments affect only the error text, because `_read_at` uses them only when formatting its errors. Truncated tables still raise `ELFError`.

## Negative controls

In the scratch directory, set `PYTHONPATH="$PWD/mutants/src:$PWD/tests"` and use the mutenv Python with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Importing `buildlib.elf` printed the generated `mutants/src/buildlib/elf.py` path. With `MUTANT_UNDER_TEST` set to each full identity, run `python -m pytest -q` on the named test:

- `buildlib.elf.x__read_header__mutmut_64`, `test_header_and_program_headers_preserve_unsigned_fields_and_order`: exit 1, assertion reports `memsz=None` instead of the literal unsigned value.
- `buildlib.elf.x__read_header__mutmut_43`, `test_empty_program_table_does_not_read_its_offset`: exit 1, unwanted seek raises `ValueError`.
- `buildlib.elf.x__read_header__mutmut_8`, `test_public_header_readers_reject_independent_malformed_images`: exit 1, a short header escapes the intended `ELFError` and raises `struct.error`.

With `MUTANT_UNDER_TEST=''`, those three tests passed together, exit 0. These are generated semantic mutants, not edits to shipping source.

The FIFO test uses a real named pipe and an owned forked consumer. A marker confirms the consumer started. The parent opens the writer, supplies an independent 64-byte header, and holds the writer open until the consumer reports the entire expected `ELFHeader`. One monotonic deadline bounds the marker, writer connection, and result. The parent closes descriptors and terminates and reaps the child on failure. The fork inherits the imported generated module and mutant selection; it does not exec a clean interpreter. A regular-file read in the same test also associates it with `_read_header` during mutmut test collection.

From scratch, the following negative and restored controls exited 1 and 0, respectively. Importing `buildlib.elf` first printed `header-fresh/mutants/src/buildlib/elf.py`. Mutant 7 failed the assertion `consumer waited for writer EOF` after 3.06 seconds; the clean control passed in 0.04 seconds.

```sh
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH="$PWD/mutants/src:$PWD/tests"
PY=/Users/josh/repos/pytest-parallel/.work/wt/mut/.work/tmp/mutenv/bin/python
MUTANT_UNDER_TEST=buildlib.elf.x__read_header__mutmut_7 cpu-guard --cores 2 "$PY" -m pytest -q tests/test_elf.py::test_header_from_fifo_finishes_before_writer_closes
MUTANT_UNDER_TEST='' cpu-guard --cores 2 "$PY" -m pytest -q tests/test_elf.py::test_header_from_fifo_finishes_before_writer_closes
```

## Remaining scope

This run covers `_read_header` only and does not classify the rest of ELF, Mach-O, locks, release, targets, or wider DSLP-4 work. It does not close a TODO.
