# Locks module test qualification

Base `545012257d40ee3cd082c6c8a88a3db61aabdcfa`. This slice changes tests only: the runtime API, lock formats, and consumers are unchanged. The contract specifies unique names, ordered URL mirrors, SHA-256, optional size and repository-owned fields in `stuff/docs/build-repos.md:20-49`. The README calls `load_sources`, `get`, and `fetch_input`; node-build also verifies cached files and reads budgets and bootstrap data, and cmake-build uses all three loaders and verification. Fetching protects pinned bytes from an incorrect origin; the owned HTTP test server supplies the network edge. Existing tests retain mirror order, failures, redirects, symlink refusal and atomic replacement checks.

Two new public scenarios check UTF-8 lock reading independent of locale and a 1,500,000-byte download across multiple reads, including the user agent, explicit and default timeout, published bytes and streaming read bound. The first wraps the filesystem read to record its encoding; the second records the request and delegates to a real loopback server. Both use owned temporary files.

## Fault controls

Copy `src` and `tests` to an isolated scratch tree. Run each named test using `PYTHONPATH="$scratch/src:$scratch/tests" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 cpu-guard --cores 2 -- .venv/bin/pytest -q "$scratch/tests/test_locks.py::test_NAME"`. The following one-line faults were applied to the scratch source only and reverted; both faults exited 1 at the stated assertion, and both restored tests passed (2 passed, exit 0):

| Fault | Test and assertion |
| --- | --- |
| `_read_object`: `encoding="utf-8"` to `encoding=None` | `load_sources_reads_utf8_independent_of_locale`: `[None] != ["utf-8"]` |
| `fetch_input`: pass `None` instead of `timeout` to `_download` | `fetch_streams_large_input_with_request_settings`: `[None, None] != [7.5, 120.0]` |

## Mutation recipe

Native Darwin arm64; mutmut 3.8.1, CPython 3.15.1. Copy `src` and `tests` to a fresh scratch directory, with this minimal `pyproject.toml`:

```toml
[tool.mutmut]
source_paths = ["src"]
pytest_add_cli_args_test_selection = ["tests/test_locks.py"]
timeout_constant = 5.0
[tool.pytest.ini_options]
pythonpath = ["tests"]
```

Set `TMPDIR` to owned `.work/tmp`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and prepend `/Users/josh/repos/pytest-parallel/.work/wt/mut/.work/tmp/mutenv/bin` to `PATH`. Acquire `/Users/josh/d/stuff/.work/tmp/mutation.lock` by atomic `mkdir`, waiting at most 120 seconds; release only the acquired lock with an exit trap. From the scratch directory run:

```sh
/usr/bin/time -p timeout -k 5 1200 cpu-guard --cores 2 -- \
  mutmut run --max-children 1 'buildlib.locks.*'
mutmut results --all true
```

The base run exited 0 in 125.01 seconds: 388 selected identities, 369 killed, 19 survived. The generated package inventory includes other modules and their not-checked results are outside this Scenario, not passes. The historic whole-package inventory and earlier ELF/Mach-O qualification counts are separate evidence, not current locks results. No mutation exclusions were used.

Every final survivor was inspected with `mutmut show`; prefix each suffix with `buildlib.locks.x_`. These dispositions concern diagnostic wording or protocol-equivalent header names, not untested data validation. No survivor was excluded.

| Identity suffix | Disposition |
| --- | --- |
| `_read_object__mutmut_21` | Diagnostic only: decorates the non-object rejection text; the rejection and path remain. |
| `_urls__mutmut_44`, `_urls__mutmut_45`, `_urls__mutmut_46` | Diagnostic only: changes the missing-host detail; invalid URL rejection and cause remain. |
| `load_sources__mutmut_12` | Diagnostic only: decorates the non-list rejection text. |
| `load_budgets__mutmut_12` | Diagnostic only: decorates the non-object rejection text. |
| `_download__mutmut_7`, `_download__mutmut_8` | Equivalent: HTTP header names are case-insensitive, and `urllib.request.Request` normalizes their case. |
| `fetch_input__mutmut_24` | Diagnostic only: changes the separator between retained URL failure details. |

The final run exited 0 in 131.41 seconds; `mutmut results --all true` exited 0. It reports 379 killed and 9 survived, with zero untested, timeout, unviable or other selected results. The baseline and final `locks.py.meta` each contain exactly the same 388 identities; each metadata key set exactly equals its raw CLI result names. Thus none is implicitly passed. The nine survivors above comprise two equivalent and seven diagnostic-only changes. The intermediate candidate run before the default-timeout assertion had 378 killed and 10 survived; it is not the final qualification.

## Verification and costs

- Source SHA-256: `e4e2968d72a6b6645458c127f775d16a93cd6400515d915826ef1009213c5ccf`; baseline tests SHA-256: `04cb50bfe59e3198bcbd14231627b0ebe21a40acee95a35fd904d0f6e44ef70b`; final tests SHA-256: `8e8be5759707a61554eb623222ff4ee888acb2f606a55cc1d42c264ba9abcc95`.
- After mutation and fault work stopped, `perf-gate-ready`, `/usr/bin/time -p cpu-guard --cores 2 -- make test`, and immediately `perf-gate-ready -1` exited 0/0/0. CPython 3.15.2 and pytest 9.2.0 ran 195 tests in 6.01 seconds; measured wall time was 6.43 seconds against the historical 193-test, 6.42-second baseline. This meets the 20% or 30-second allowance and is not a speedup claim.
- `cpu-guard --cores 2 -- make lint` exited 0: Ruff and ty passed. An earlier lint attempt exited 2 because the recorder's return annotation was narrower than ty could prove; the annotation was corrected and the final Scenario and checks were rerun.
- No runtime defect was established. Release, package-wide coverage, whole-change selection and CI qualification remain outside this module Scenario.
