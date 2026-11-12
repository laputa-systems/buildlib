# laputa-buildlib

Shared helpers for the laputa-systems build repositories, implementing the build repository contract
kept in the [stuff](https://github.com/laputa-systems/stuff) repository.
Standard library only; Python 3.15 or newer. Import the submodules directly.

| Module | Provides |
| --- | --- |
| `buildlib.locks` | Load and validate `sources.lock.json`, `release-budgets.json`, and `bootstrap.lock.json` (`LockError` carries the file path and reason); `fetch_input` downloads a pinned input, trying its URLs in order and publishing atomically after the size and SHA-256 match. |
| `buildlib.release` | Per-target `<product>-<version>-<target>.manifest.json` with deterministic key order, reproducible `.tar.xz` archives, and a sorted `SHA256SUMS` writer and verifier. |
| `buildlib.macho` | Mach-O header and load-command inspection, signature status, and UUID/signature-normalized bytes for reproducibility comparisons. |
| `buildlib.elf` | ELF header, program-header, and dynamic-section inspection, a hardening check, and build-id-normalized bytes. |
| `buildlib.targets` | The released triples (`aarch64-apple-darwin`, `x86_64-unknown-linux-musl`, `aarch64-unknown-linux-musl`) and the platform, architecture, and loader strings for each. |
| `buildlib.hashing` | SHA-256 of files. |

## Pinning

The package is published to the stuff PyPI registry. Pin an exact version and point uv at that index
in the consuming repository's `pyproject.toml`:

```toml
[project]
dependencies = ["laputa-buildlib==0.1.0"]

[[tool.uv.index]]
url = "https://pypi.17166969.xyz/current/simple/"
default = true
```

Then run `uv lock` and commit `uv.lock`. Upgrade by changing the pin.

## Development

`make test` runs the tests, `make lint` runs Ruff and ty, and `make clean` removes build output.
