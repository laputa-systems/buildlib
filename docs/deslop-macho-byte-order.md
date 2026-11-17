# Mach-O byte-order repair

Headers and command tables recognize both Mach-O byte orders. Numeric command
payloads previously forced little-endian, producing wrong versions and invalid
path offsets for supported big-endian images. A private parser now retains the
byte order alongside commands. Public `load_commands` still returns the same
raw `LoadCommand` list in order. Version, dependency, install-name and rpath
readers use the retained order. No public type, signature or file-read count changes.

Base: `ba509805443e84b81f89e24b6035f7f5ab4a319e`. Runtime repair: `6025512`.
SHA-256 of `src/buildlib/macho.py`:
`4126cc856553f668806752ffede3656a761f76c44ebfb3218368eb07d03cd31f`.
SHA-256 of `tests/test_macho.py`:
`1c00993a49a97144df14937baba89efdd570032d6ef9c3888cc12557d0a31726`.

## Regression and fault controls

Native macOS arm64, CPython 3.15.2, pytest 9.2.0. From the project root run:

```sh
TMPDIR="$PWD/.work/tmp" cpu-guard --cores 2 -- uv run --locked pytest -q \
  tests/test_macho.py -k payload_fields_follow_image_byte_order
```

On original source, the corrected fixture passes four little-endian cases
and fails four big-endian cases, exit 1. With the repair all eight pass. It
covers 32/64-bit headers, modern/legacy version commands, literal versions,
platform, two dependencies, install name, two rpaths and raw command bytes.
An initial fixture misplaced two assertions from an existing test; that run
is discarded. The accepted before/after evidence uses the corrected fixture.

Three malformed-table cases require `MachOError` when command headers or
sizes exceed the declared table, including trailing file bytes outside it.
A positive scenario preserves unknown commands and ignores non-table tails.
Two independent scratch faults fail their intended assertions, exit 1:
reading to EOF makes a short-table case succeed, and filtering unknown
commands loses the literal returned command. The runtime source is unchanged
by those probes. All twelve focused cases pass on the actual source.

## Mutation

Fresh scratch copies use mutmut 3.8.1 on CPython 3.15.1, with
`source_paths=["src"]`,
`pytest_add_cli_args_test_selection=["tests/test_macho.py"]`,
`timeout_constant=5.0`, pytest `pythonpath=["tests"]`, and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Acquire the campaign's atomic mutation
lock, then run from the copy:

```sh
/usr/bin/time -p timeout -k 5 1200 cpu-guard --cores 2 -- mutmut run \
  --max-children 1 'buildlib.macho.*'
mutmut results --all true
```

The original module selection has 405 identities: 353 killed, 52 survived.
Its runtime defect stopped test-only qualification. The repaired current
selection has 401: 359 killed, 42 survived and zero other classes, exit 0 in
15.83 s. Generated metadata keys exactly match the 401 result names. The
other 1,277 package identities remain unchecked. The helper refactor changes
the inventory; these counts are not a fixed-identity detection score.
An intermediate repaired run exposed three table-boundary misses caught by
the final scenarios.

The affected scope comprises `_load_commands`, `load_commands`, `_fields`,
`build_version`, `dependencies`, `dylib_id` and `rpaths`: 147 identities,
140 killed and seven dispositioned survivors. Prefix each name below with
`buildlib.macho.x_`:

- `_load_commands__mutmut_27`: diagnostic text becomes `None`; rejection remains.
- `build_version__mutmut_5` and `build_version__mutmut_21`,
  `dependencies__mutmut_6`, `dylib_id__mutmut_5`, `rpaths__mutmut_6`:
  only the error's path argument becomes `None`. `_fields` uses it only in diagnostics.
- `_fields__mutmut_11`: omitted `unpack_from` offset uses the identical zero default.

The first six are diagnostic-only; the last is equivalent. No exclusion is
configured. The other 35 current survivors remain outside this repair's
acceptance. The whole Mach-O module and DSLP-4 remain unqualified.

Generated `_load_commands__mutmut_17` loads the instrumented source and fails
the declared-table oracle, exit 1; its empty-mutant control passes all three
cases, exit 0. Set `PYTHONPATH` to the generated `mutants/src` and tests
folders, and set `MUTANT_UNDER_TEST` to its full identity or the empty string.
Run `python -m pytest -q tests/test_macho.py::test_load_commands_respects_declared_table_extent`.

## Consumers and cost

Static caller search finds affected Mach-O consumers in Node-build and
Python-build. Other declared build repositories use other modules. From an
isolated consumer checkout, put the candidate `src` then the consumer root
on `PYTHONPATH`. Use the candidate environment's Python. Import the consumer
adapter before asserting `buildlib.macho.__file__` resolves to the candidate.
Python-build's imported `build_version.__code__.co_filename` must also resolve there.
Then run these unittest names in that same process:

- Node-build `50ee5f5`: `tests.test_product`, 19 passed.
- Python-build `6664a847`: `tests.test_macho`,
  `tests.test_validate_product.MachOValidationTests`, `tests.test_reproduce`, 24 passed.

Both runs print the candidate path and use owned scratch with a two-core cap.
The first harness omitted the consumer root from `PYTHONPATH` and failed
import; the corrected runs above pass.

The native project suite passes 166 tests; Ruff and ty pass. Warmed
`make test` under `/usr/bin/time -p cpu-guard --cores 2 --` takes 6.42 s,
with actual pre/test/immediate-post gate exits 0/0/0. The matched base
observation is 6.42 s for 154 tests. These single observations meet the
suite-cost allowance and establish no speedup. The project declares no
runtime benchmark for its once-per-artifact helpers.

Both wheel builds pass. The pure-Python wheel grows from 25,086 to 25,126 B
(40 B); its embedded parser hash matches the source above. No dependencies
or native binaries are added. No release or production build ran.
