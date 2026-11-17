# Mach-O module test qualification

This test-only slice completes the Mach-O Scenario started by the
[byte-order repair](deslop-macho-byte-order.md). Base:
`5a1852ccf6bc1420b3577aaed19d3aa5a48f2f7b`. Public runtime, API, consumers,
dependencies and wheel contents are unchanged. Locks, release, package-wide
coverage and a qualified mutation CI lane remain separate DSLP-4 work.

## Boundary scenarios

Independent byte-built images exercise both byte orders and both header sizes.
The added scenarios preserve complete raw headers, reject a missing 64-bit
reserved word, return exact UUID/signature ranges and normalized bytes, and
reject overlapping, empty, non-tail, malformed and duplicate signature data.
An owned-file rewrite between the initial read and metadata resolution proves
that normalization refuses ranges outside its earlier snapshot. It uses no
threads, timing assumptions or external files.

An owned shell executable exercises `signature_status` with literal success
and failure statuses, stderr precedence, stdout fallback and whitespace.
A forwarding process adapter requires strict verification, the complete path
including spaces, captured text and a positive timeout of at most 120 seconds.
It then executes the real subprocess. No signing identity, real codesign
service, network or user binary is used.

Nine temporary faults each fail their named boundary assertion, exit 1:

| Fault | Boundary test (`test_` prefix omitted) |
| --- | --- |
| Replace CPU field with zero | `empty_header_preserves_every_raw_field` |
| Force 64-bit header format | `empty_header_preserves_every_raw_field` |
| Force 32-bit header format | `64_bit_header_requires_reserved_word` |
| Replace strict verification with deep verification | `signature_process_contract_and_output_precedence` |
| Move UUID start back one byte | `signature_ranges_normalize_exact_bytes` |
| Permit signatures overlapping commands | `signature_range_refuses_overlap_empty_and_non_tail_data` |
| Drop UUID payload-size rejection | `signature_commands_require_exact_payload_sizes` |
| Permit a second valid signature | `duplicate_signature_is_rejected_even_when_both_ranges_fit` |
| Conjoin normalization's independent bounds checks | `normalization_refuses_metadata_that_outgrows_read_snapshot` |

Run each test with `uv run --locked pytest -q tests/test_macho.py::test_NAME`
against an isolated source copy containing that one fault. The accepted harness
asserts the imported module's resolved path before pytest. Its first setup
omitted the fixture helper's ELF import and failed collection; that result is
discarded. After correcting the copy, all nine faults fail assertions and the
restored source passes all 69 Mach-O cases.

## Mutation evidence

Native macOS arm64; mutmut 3.8.1, CPython 3.15.1. Copy `src` and `tests` to
owned scratch, then use this scratch `pyproject.toml`:

```toml
[tool.mutmut]
source_paths = ["src"]
pytest_add_cli_args_test_selection = ["tests/test_macho.py"]
timeout_constant = 5.0
[tool.pytest.ini_options]
pythonpath = ["tests"]
```

With `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, scratch `TMPDIR`, the pinned mutation
environment first on `PATH`, and the campaign's atomic mutation lock held:

```sh
/usr/bin/time -p timeout -k 5 1200 cpu-guard --cores 2 -- \
  mutmut run --max-children 1 'buildlib.macho.*'
mutmut results --all true
```

The fixed inventory has 401 identities. Before: 359 killed, 42 survived.
After: 383 killed, 18 survived; zero untested, timeout, unviable or other
selected results. The final run exits 0 in 19.04 s. This is diagnostic tool
cost, not a runtime performance comparison. Generated metadata keys exactly
match the 401 CLI result names. The other 1,277 package identities are outside
this Scenario, not passes.

Every final survivor was inspected with `mutmut show`. Prefix names below
with `buildlib.macho.x_`. No exclusion or accepted risk was added.

| Identity suffix | Disposition and reason |
| --- | --- |
| `_magic_of__mutmut_8` | Diagnostic only: read error message becomes `None`; error and cause remain |
| `_load_commands__mutmut_27` | Diagnostic only: short-table error message becomes `None` |
| `_string_at__mutmut_26` | Equivalent: `UTF-8` and `utf-8` resolve to the same codec |
| `_fields__mutmut_11` | Equivalent: omitted unpack offset defaults to zero |
| `build_version__mutmut_5`, `build_version__mutmut_21` | Diagnostic only: path is used only in `_fields` error text |
| `dependencies__mutmut_6`, `dylib_id__mutmut_5`, `rpaths__mutmut_6` | Diagnostic only: same error-path substitution |
| `signature_status__mutmut_6` | Equivalent: subprocess treats `check=None` and `check=False` as false |
| `signature_metadata_ranges__mutmut_12` | Equivalent: the initially false flag is only tested for truth before being set to true |
| `signature_metadata_ranges__mutmut_18`, `signature_metadata_ranges__mutmut_27`, `signature_metadata_ranges__mutmut_44` | Diagnostic only: rejection message becomes `None` |
| `signature_metadata_ranges__mutmut_36` | Equivalent: omitted unpack offset defaults to zero |
| `normalized_bytes__mutmut_6`, `normalized_bytes__mutmut_7` | Equivalent: every range from the real resolver starts strictly after a positive-size header, so zero cannot occur, including during file changes |
| `normalized_bytes__mutmut_10` | Diagnostic only: out-of-snapshot rejection message becomes `None` |

These are seven equivalent and eleven diagnostic-only survivors. In particular,
the other normalization bounds mutants were observable during a file change;
the controlled rewrite scenario catches them rather than calling them equivalent.

## Native verification and cost

CPython 3.15.2, pytest 9.2.0: all 193 project tests pass, up from 166.
Ruff and ty pass via `cpu-guard --cores 2 -- make lint`. Runtime parser SHA-256
remains `4126cc856553f668806752ffede3656a761f76c44ebfb3218368eb07d03cd31f`.
Test SHA-256 is
`5285fb191b9f16836efbedc7931e0429befa14983b173a7ff2b981f284d81d64`.
After mutation and fault probes stopped, warmed `/usr/bin/time -p
cpu-guard --cores 2 -- make test` took 6.42 s versus the repair baseline's
6.42 s for 166 tests. Actual `perf-gate-ready`, test and immediate
`perf-gate-ready -1` exits were 0/0/0. These single observations meet the
suite-cost allowance and establish no speedup. An earlier observation near
fault-harness activity is not used for this cost decision.
Consumer and runtime performance checks are unaffected because only tests and
this proof record change. The prior repair's consumer proof remains in its
own record. No production build, release or publication ran.
