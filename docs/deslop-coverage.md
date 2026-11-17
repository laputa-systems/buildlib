# Branch coverage and remaining reader boundaries

Base measurement: `2c535f389c65e0ce58b72491ad18b6491a3ccbb9`, 201 tests.
The final candidate integrates locks `87a248d920278f2795a63db8e5bd86d6a15f1a67`
and adds four ELF/Mach-O cases, for 207 tests. Runtime source, public APIs and
published package contents are unchanged. The report is informational: no
coverage threshold, exclusion, ratchet or CI workflow is added.

The verification path has independent static Ruff/ty checks, exact-test Edit
selectors (`pytest FILE::TEST`), component Change selectors (the two parser
files run 101 cases), and bounded complete-module mutation Scenarios. Fixtures
own their files, HTTP origins and child processes; no live service is tested.
The existing project acceptance command discovers all native tests; its cost
is recorded below. This slice does not redesign default/CI selection. Broader
cross-repository production builds and platform qualification were not run;
the whole-change selection contract remains unfinished.

## Report

Native macOS arm64, CPython 3.15.2, pytest 9.2.0, coverage.py 7.17.3:

| Revision | Lines executed / statements | Branches executed / total |
| --- | --- | --- |
| Base | 897 / 901 | 289 / 292 |
| Candidate | 900 / 901 | 291 / 292 |

All eight first-party Python files are included, including the empty package
initializer. There are zero excluded lines. The remaining line/branch is
`elf.py:92-93`, the negative-range rejection in private `_read_at`. Every
production caller supplies unsigned binary header fields, a nonnegative
count multiplied by 56, or a mapped offset guarded by `addr >= vaddr`.
The mapping adds that nonnegative difference to an unsigned offset. Thus a
negative size or offset cannot reach this guard through the public readers.
No private-only negative-input test or exclusion is added to manufacture a
fully executed report.

The other previously unrun decisions gain public boundary scenarios:

- Byte-built ELF headers preserve the `relocatable` kind and open-ended
  `type-4660` fallback. The latter remains a supported unknown value.
- A literal GNU note with an empty descriptor at EOF produces range `(528, 0)`
  and leaves the complete image unchanged during normalization.
- Reading a missing Mach-O file raises `MachOError` and retains the original
  `FileNotFoundError` cause.

Three separate scratch faults change the relocatable kind, discard an empty
build ID, and propagate the raw Mach-O read error. Each fails its named public
assertion, exit 1; restored source passes all 101 ELF/Mach-O cases. Run the
specific functions with `uv run --locked pytest -q FILE::TEST` after applying
only that fault to an owned source copy:

| File | Test |
| --- | --- |
| `tests/test_elf.py` | `test_header_kind_preserves_relocatable_and_unknown_types` |
| `tests/test_elf.py` | `test_zero_length_build_id_at_file_end_preserves_image` |
| `tests/test_macho.py` | `test_read_header_wraps_unreadable_image_with_original_cause` |

The harness checks the imported source path first.

## Reproduction and dependency

Run from the project root:

```sh
mkdir -p .work/tmp
TMPDIR="$PWD/.work/tmp" cpu-guard --cores 2 -- make coverage
```

The pinned development dependency comes from the Stuff PyPI mirror. Its
metadata declares Apache-2.0, the maintained coveragepy project, Python 3.15
support and no required transitive packages. Native installed code/metadata
occupy 1,840 KiB. The standard library has no branch-coverage collector.
No runtime dependency is added.

`tool.coverage.run` names `buildlib` as source, enables branches and subprocess
patching, and keeps parallel data under `.work/coverage/`. `make coverage`
erases prior owned data, runs pytest, combines parent and child data, then
prints a missing-lines report and writes `.work/coverage/coverage.json`.
The final run combines three process files; both locale scenarios check that
their child imported the candidate. Required conclusions and the unrun-region
disposition are recorded here; disposable report data can be regenerated.

## Mutation reconciliation

The added cases change ELF/Mach-O test inputs, so both complete module
Scenarios are repeated together, not inferred from coverage. With mutmut 3.8.1
on CPython 3.15.1, a scratch source/test copy uses:

```toml
[tool.mutmut]
source_paths = ["src"]
pytest_add_cli_args_test_selection = ["tests/test_elf.py", "tests/test_macho.py"]
timeout_constant = 5.0
[tool.pytest.ini_options]
pythonpath = ["tests"]
```

With the pinned mutation environment on `PATH`, scratch `TMPDIR`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and the campaign's atomic mutation lock:

```sh
/usr/bin/time -p timeout -k 5 1200 cpu-guard --cores 2 -- \
  mutmut run --max-children 1 'buildlib.elf.*' 'buildlib.macho.*'
mutmut results --all true
```

All 910 selected identities have results: 852 killed, 58 dispositioned
survivors, zero other classes, exit 0 in 36.74 s. Every identity and result
class exactly matches the accepted ELF and Mach-O qualifications, not just
their totals. Their individual dispositions remain in
[ELF](deslop-elf-contracts.md) and [Mach-O](deslop-macho-contracts.md).
The other 768 generated package identities are unchecked by this selection;
separate locks/release evidence is not counted as a new package-wide run.
An initial comparison used an intermediate ELF scratch result; that comparison
is discarded. The accepted comparison uses the owning ELF record's final
candidate and the accepted Mach-O result. No exclusion or accepted risk is added.

Runtime SHA-256 values remain:

- `elf.py`: `ef8cfe40095cb787e73649cf11074e1fc6cd292bca59db82c1b26fa95974386a`
- `macho.py`: `4126cc856553f668806752ffede3656a761f76c44ebfb3218368eb07d03cd31f`

Final test SHA-256 values:

- `test_elf.py`: `5ebd2639a958191ea9313c8f2990bbca626ceefc85014fea6dec6929e89bca0b`
- `test_macho.py`: `008405133cdea91e4c73ca6e818e2eefec7f9246b27f1b7db5d78652d2043189`

## Cost and remaining acceptance

All 207 native tests, Ruff and ty pass. After integration, warmed
`/usr/bin/time -p cpu-guard --cores 2 -- make test` takes 7.48 s versus the
201-test base's 6.45 s. The final coverage command takes 7.46 s. Both measured
commands have actual pre-gate/command/immediate-post-gate exits 0/0/0 using
`perf-gate-ready` and `perf-gate-ready -1`. An earlier 205-test observation
predated locks integration and is not the final suite receipt. These are
suite costs, not speedup or coverage-overhead claims; the cost allowance holds.

Base and candidate wheels are byte-identical, 25,126 B, SHA-256
`a5dc5a93a79c33ed0f5ff676f989ae57f21365a7057e5382e1235597e00d126f`.
Only tests, development tooling and documentation change, so consumer/runtime
workloads are unaffected. No production build, release or publication runs.

DSLP-4 remains open for `make mutants-diff` and a qualified whole-change
selection/result contract with negative controls. Workflow changes still
require separate owner authorization. This local report does not qualify CI.
