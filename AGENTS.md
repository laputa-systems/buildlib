# AGENTS.md

`laputa-buildlib` is the standard-library-only Python package that the laputa-systems build
repositories share. It implements the [build repository contract](https://github.com/laputa-systems/stuff/blob/main/docs/build-repos.md).

## Commands

- `make test` runs pytest; `make lint` runs `ruff check` and `ty check`; `make clean` removes scratch output.
- `uv build` produces the wheel and sdist in `dist/`.
- Create `.work/tmp` and set `TMPDIR="$PWD/.work/tmp"` for commands that create scratch data.

## Invariants

- No runtime dependencies. Add only what at least two build repositories need today.
- Tests use real files, tar/xz archives, and byte-built Mach-O/ELF images from `tests/binaries.py`.
- Lock schema, manifest fields, and target triples follow that contract; change the contract first.

Deferred work is tracked in [TODO.md](TODO.md).
