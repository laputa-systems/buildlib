# Release module test qualification

Base: `545012257d40ee3cd082c6c8a88a3db61aabdcfa`. This test-only slice guards
canonical manifest bytes, archive modes and prefixes, and checksum filename
and encoding rules. Runtime source, public APIs, dependencies and consumers
are unchanged. It qualifies the complete `buildlib.release` Scenario, not the
package or a CI mutation lane.

## Boundary evidence

The manifest scenario compares complete literal bytes, including required
field order, nested extra-field order, two-space indentation, tuple-to-array
conversion, null, escaped Unicode and the trailing newline. It does not
compute expected JSON with the implementation's serializer.

An owned archive tree distinguishes each execute bit from write bits through
the resulting tar metadata. Three invalid prefix forms (empty, absolute and
parent traversal) must fail before replacing an existing archive. Newline
filenames and symlinks to regular assets must fail both checksum writing and
verification, with the previous checksum file preserved.

A Unicode checksum scenario verifies the actual file in a child interpreter
with UTF-8 mode explicitly disabled, then changes its text locale to `C`
after startup. Filesystem encoding stays UTF-8, while the text default is
ASCII. The child asserts its imported release module is the candidate before
calling the public verifier. Explicit UTF-8 succeeds; a default-encoding read
does not. This is an owned process with a ten-second timeout, not a change to
the parent locale or user environment. It corrects the earlier hypothesis
that Python 3.15's normal UTF-8 default makes an omitted encoding equivalent.

Five independent temporary source faults fail the corresponding assertions:

| Fault | Test (`test_` prefix omitted) |
| --- | --- |
| Change manifest indentation from two to three | `manifest_has_canonical_complete_bytes` |
| Ignore the other-execute bit | `archive_execute_mode_uses_each_execute_bit_only` |
| Stop recognizing an absolute archive prefix | `archive_refuses_invalid_prefix_before_replacing_output` |
| Permit newline asset names | `checksums_refuse_unsafe_assets_without_replacing_output` |
| Use the default text encoding for checksum reads | `checksum_utf8_reading_is_independent_of_text_locale` |

Run each named test against an isolated source copy with only that fault:
`uv run --locked pytest -q tests/test_release.py::test_NAME`. The accepted
harness checks the imported candidate path first. Every fault exits 1 on the
intended boundary assertion; restored source passes all 34 release cases.

## Mutation evidence

Native macOS arm64, mutmut 3.8.1 and CPython 3.15.1. Copy `src` and `tests`
into owned scratch with this `pyproject.toml`:

```toml
[tool.mutmut]
source_paths = ["src"]
pytest_add_cli_args_test_selection = ["tests/test_release.py"]
timeout_constant = 5.0
[tool.pytest.ini_options]
pythonpath = ["tests"]
```

With the pinned mutation environment first on `PATH`, scratch `TMPDIR`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and the campaign's atomic mutation lock:

```sh
/usr/bin/time -p timeout -k 5 1200 cpu-guard --cores 2 -- \
  mutmut run --max-children 1 'buildlib.release.*'
mutmut results --all true
```

The fixed module inventory is 331 identities. Before: 309 killed, 22 survived.
After: 317 killed, 14 survived; zero other selected classes, both exits 0.
Raw costs are 12.70 s and 13.73 s, not a runtime performance comparison.
Before/after metadata keys compare equal and exactly match final CLI result
names. The other 1,347 generated package identities are unchecked by this
Scenario. No exclusion or accepted risk is added.

Every remaining identity was inspected with `mutmut show`. Prefix suffixes
below with `buildlib.release.x_`:

| Identity suffix | Disposition and reason |
| --- | --- |
| `_stem__mutmut_3`, `_stem__mutmut_4` | Diagnostic only: change the version label in an error |
| `_ordered_object__mutmut_4` | Diagnostic only: decorate the rejected-key error text |
| `write_manifest__mutmut_58`, `write_sha256sums__mutmut_20`, `verify_sha256sums__mutmut_8` | Equivalent: UTF-8 codec-name case aliases |
| `create_archive__mutmut_38`, `create_archive__mutmut_42` | Equivalent: explicit `None` or omitted tar format selects the same PAX default on supported CPython |
| `create_archive__mutmut_39`, `create_archive__mutmut_43` | Equivalent: explicit `None` or omitted compression preset selects the same default level six |
| `_checksum_assets__mutmut_3`, `_checksum_assets__mutmut_5` | Diagnostic only for unsupported undecodable names; otherwise equivalent ordering, as explained below |
| `verify_sha256sums__mutmut_45`, `verify_sha256sums__mutmut_47` | Equivalent: names decoded from UTF-8 have the same Unicode and UTF-8 byte order |

On supported macOS/Linux, every enumerated Path shares its parent, so default
Path ordering compares names. UTF-8 preserves code-point order for serializable
names. Arbitrary undecodable POSIX names can instead contain surrogate escapes:
writing their checksum text fails UTF-8 encoding with either ordering; the
error position/text can differ. Verification first decodes UTF-8, validates
the listed order, and uses discovered assets as a mapping; unlisted names are
sorted separately. The private ordering mutations therefore change no valid
checksum output or accept/reject decision. Classify those two conservatively
as diagnostic-only rather than claiming unconditional equivalence.

The installed standard library confirms `tarfile.DEFAULT_FORMAT ==
tarfile.PAX_FORMAT == 2`, and `lzma.PRESET_DEFAULT == 6`; the compressor's
documented `None` default uses that preset. These are nine equivalent and
five diagnostic-only survivors. No Windows qualification is claimed.

## Native verification

CPython 3.15.2 and pytest 9.2.0: the focused 34 cases pass; Ruff and ty pass
through `cpu-guard --cores 2 -- make lint`. Runtime release source SHA-256
remains `56efa13412436adb19e2e49e2ccb46d752b041b9b5c991248f1ce0c9853d0379`.
Test SHA-256:
`580bde5cb7ae6e4fa0035534927885d84dc4e1a2e62024fe75178381627fecb6`.
The full project suite passes 201 tests, up from 193. After mutation and
fault probes stopped, warmed `/usr/bin/time -p cpu-guard --cores 2 --
make test` takes 6.45 s versus the matched base observation of 6.42 s.
Actual `perf-gate-ready`, suite and immediate `perf-gate-ready -1` exits are
0/0/0. These single observations meet the suite-cost allowance; no speedup
or regression measurement is claimed.
Consumer/runtime performance checks are unaffected by test-only changes.
Archives generated above are owned test fixtures; no release, publication or
production build runs.
