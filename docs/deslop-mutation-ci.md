# Whole-change mutation gate

This paragraph exercises documentation-only change classification.

The PR job classifies every changed path, selects the union of changed Python
production files and the four critical modules for test, fixture, build, or
gate changes, and runs five independent shards on Linux x86-64, Linux arm64, and macOS arm64. A production file is selected
in full; this conservative superset avoids dropping changed lines. A relevant
file without identities fails. Unknown paths, failed baselines, missing or
inconsistent metadata, unsupported result codes, and exhausted budgets fail.
The only no-selection pass is an irrelevant change or an idle shard of a
nonempty union. Each shard has a 1,200-second deadline and one mutmut worker.

Run from the project root with a comparison commit reachable locally:

```sh
mkdir -p .work/tmp
TMPDIR="$PWD/.work/tmp" uv run --locked python -m unittest discover -s scripts -p 'test_mutants_gate.py'
TMPDIR="$PWD/.work/tmp" make mutants-diff BASE=<base-commit> SHARD=release
```

Run all five shards (`elf`, `macho`, `locks`, `release`, `other`) to cover the
union. The shared campaign mutation lock and a heavy slot are required for
local mutation runs. CI runs every shard on all three configured operating-system and architecture pairs; [GitHub documents
these runner labels and architectures](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). Those remote jobs
and negative controls remain unqualified until the parent runs them.

Mutmut 3.8.1 and its transitive packages are locked as development-only tools
on the configured Stuff mirror. The lock adds click 8.5.1, libcst 1.9.0,
linkify-it-py 2.2.0, markdown-it-py 4.3.0, mdit-py-plugins 0.7.0, mdurl 0.1.2,
platformdirs 4.14.5, pyyaml 6.0.3, rich 15.0.0, setproctitle 1.3.8,
textual 8.2.8 and typing-extensions 4.16.0. Installed metadata identifies BSD-3
(mutmut, click, setproctitle), MIT (the other packages except typing-extensions),
and PSF-2.0 (typing-extensions); libcst also includes PSF and Apache-2.0 derived
files. The package's runtime dependency list remains empty. `uv build --wheel`
produced 25,126 bytes, SHA-256
`a5dc5a93a79c33ed0f5ff676f989ae57f21365a7057e5382e1235597e00d126f`,
matching the previously recorded wheel. The complete local `.venv` occupied
110,496 KiB after synchronization; this is not an incremental-size measurement.
No publication occurred.

The exact 82 dispositioned identities in `.mutants-gate.toml` refer to the
individual reasons in the existing ELF, Mach-O, locks, and release records.
They are usable only with the matching source hash and generated mutant
function hash; a changed module or changed mutation must requalify its survivors. The result reader checks the mutmut 3.8.1 metadata
identity set against its generated spans and refuses pytest internal errors,
no-tests, untested, skipped, or unknown result codes. Timeouts are rerun with
longer limits and fail unless the rerun resolves or confirms the hang.

The current Darwin arm64 release Scenario on Python 3.15.2 selected 331
identities, 317 killed and 14 documented survivors, no other outcomes, in
14.82 seconds under the 20-minute limit. Inventory and dispositions match
[release evidence](deslop-release-contracts.md). Source SHA-256 is
`56efa13412436adb19e2e49e2ccb46d752b041b9b5c991248f1ce0c9853d0379`,
test SHA-256 `580bde5cb7ae6e4fa0035534927885d84dc4e1a2e62024fe75178381627fecb6`.
With generated `mutants/src` on `PYTHONPATH`, activating
`buildlib.release.x_write_manifest__mutmut_1` made
`test_write_manifest_rechecks_required_fields` fail on `KeyError`; the same
test passed without `MUTANT_UNDER_TEST`. The gate's release shard independently
reported the same 317/14 classes using the full current test suite in 20.05
seconds; after binding each disposition to its mutant hash, the final gate
run gave the same result in 20.12 seconds. The test and lint commands passed
207 cases and both static checks.

## Negative-control blueprint

In a disposable copy of `src` and `tests`, remove the whole
`test_write_manifest_rechecks_required_fields` function from
`tests/test_release.py`. In `src/buildlib/targets.py`, change the already tested
`return self.family == LINUX_MUSL` expression to
`family = self.family` followed by `return family == LINUX_MUSL`. The
current ordinary suite on that copy passes 206 cases, and Ruff and ty pass.
The selector prints all four critical modules and `src/buildlib/targets.py`.
The release scenario then fails
with 313 killed and 18 survivors: four newly surviving release identities are
`buildlib.release.x_build_manifest__mutmut_14`,
`buildlib.release.x_write_manifest__mutmut_1`, `__mutmut_3`, and `__mutmut_7`
(the last two use the same `buildlib.release.x_write_manifest` prefix).
The failing identities belong to the removed test's critical module; the
unrelated production file is not the reason for failure. Restore both files
before any normal work. The parent still needs to reproduce this as an actual
throwaway PR and verify all required CI shards.

Local fixture reproduction: create `.work/tmp/negative-control`, copy `src`
and `tests` there, make those two edits, and copy `.mutants-gate.toml` there.
With the campaign mutation lock and a heavy slot held, run:

```sh
PYTHONPATH="$PWD/.work/tmp/negative-control/src:$PWD/.work/tmp/negative-control/tests" \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --locked python -m pytest -q .work/tmp/negative-control/tests
uv run --locked python -c 'import pathlib,sys,time,tomllib; sys.path.insert(0,"scripts"); import mutants_gate as g; g.ROOT=pathlib.Path(".work/tmp/negative-control").resolve(); selected=g.select({"tests/test_release.py":"M","src/buildlib/targets.py":"M"}); print("selected union:",*sorted(selected),flush=True); g.run_module("src/buildlib/release.py",tomllib.loads((g.ROOT/".mutants-gate.toml").read_text()),time.monotonic()+1200)'
```

The pytest command exits 0; `ruff check src tests` and `ty check` from the
fixture directory also exit 0. The mutation command exits 1 on the four release
survivors above.
This local fixture exercises the selector and module runner; it is not a
claim about remote PR selection or a completed CI qualification.

Final gate configuration SHA-256: `a4c751a92b0ea2f041b4471155ca4bb3bba1987749c506927934028b87e4dfdf`.
The locked dependency graph SHA-256 is `a75fc6049711e32726d9cb1a1be7e11656f68bd7bbacb7c6c94b9a2c9e2a1b39`.
