# AGENTS.md

`laputa-buildlib` is the standard-library-only Python package that the laputa-systems build
repositories share. It implements the [build repository contract](https://github.com/laputa-systems/stuff/blob/main/docs/build-repos.md).

## Commands

- `make test` runs pytest; `make lint` runs `ruff check` and `ty check`; `make coverage` reports branch coverage; `make clean` removes the root package outputs and test and lint caches.
- `uv build` produces the wheel and sdist in `dist/`.
- Create `.work/tmp` and set `TMPDIR="$PWD/.work/tmp"` for commands that create scratch data.

## Invariants

- No runtime dependencies. Add only what at least two build repositories need today.
- Tests use real files, tar/xz archives, and byte-built Mach-O/ELF images from `tests/binaries.py`.
- Lock schema, manifest fields, and target triples follow that contract; change the contract first.

Deferred work is tracked in [TODO.md](TODO.md).

## Verification

| Command | Warm cost | Covers |
| --- | --- | --- |
| `make test` | 7.5s | `tests/` (207 pytest cases) |
| `make lint` | under 1s | `ruff check`, `ty check` over `src` and `tests` |
| `make coverage` | 7.5s | All first-party modules, branches and owned Python children; terminal/JSON report under `.work/coverage/`, no threshold |

Public API and the tests that guard it:

- `locks`: lock loading and validation in `test_load_*`; fetching (URL order, digest and size checks, atomic publish, symlink refusal, redirects) in `test_fetch_*` and `test_*redirect*`.
- `release`: asset names, manifest ordering and validation, deterministic archives, and `SHA256SUMS` in `tests/test_release.py`, which also runs `sha256sum -c`.
- `elf` and `macho`: header decoding, hardening, linkage, and normalization over the byte-built images in `tests/binaries.py`.
- `targets`: the supported triples and host mapping in `tests/test_targets.py`.

Consumers: cmake-build, node-build, ffmpeg-build, chromium-build, pi-build, and python-build pin a released wheel. A change to the public API reaches them only through a new release, so their tests are the consumer check before that release.

Performance workload: none. Build scripts call these helpers once per input or asset.

Coverage gaps, fault controls and suite-cost evidence: [coverage record](docs/deslop-coverage.md).
