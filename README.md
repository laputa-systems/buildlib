# laputa-buildlib

Standard-library-only helpers for the laputa-systems build repositories. Requires Python 3.15 or newer.
Implements the [build repository contract](https://github.com/laputa-systems/stuff/blob/main/docs/build-repos.md).
Import the submodules directly:

```python
from buildlib.locks import fetch_input, load_sources

source = load_sources("sources.lock.json").get("cpython")
fetch_input(source, ".work/downloads/Python.tar.xz")
```

| Module | Provides |
| --- | --- |
| [buildlib.locks](src/buildlib/locks.py) | Source-input validation and verified downloads; budget and bootstrap readers. `LockError` includes the path and reason. |
| [buildlib.release](src/buildlib/release.py) | Per-target manifests, reproducible `.tar.xz` archives, and a sorted `SHA256SUMS` writer and verifier. |
| [buildlib.macho](src/buildlib/macho.py) | Thin Mach-O metadata and UUID/signature normalization. `signature_status` requires macOS `codesign`. |
| [buildlib.elf](src/buildlib/elf.py) | 64-bit little-endian ELF metadata, hardening checks, and build-id normalization. |
| [buildlib.targets](src/buildlib/targets.py) | Supported release triples and their platform, architecture, and loader mappings. |
| [buildlib.hashing](src/buildlib/hashing.py) | SHA-256 and byte size of files. |

Budget validation checks the schema version, supported target names, and object-shaped limits.
Bootstrap validation checks the object and schema version. Repository-specific fields need caller validation.

## Bootstrap shim

A repository that needs Python 3.15 and buildlib on a fresh host, before any Python exists there, vendors the
[`bootstrap/`](bootstrap) directory and runs its scripts through it:

```sh
bootstrap/python scripts/setup.py
```

`bootstrap/python` needs only `sh`, `curl`, `tar`, `awk` and `sha256sum` or `shasum`. It reads `bootstrap/pins`
(one input per line: kind, target, version, size, SHA-256, URL), checks each download's size and SHA-256 before it
unpacks it, and runs CPython with the pinned buildlib wheel first on `PYTHONPATH`. On macOS, and on Linux with musl
1.2.5 or newer, it runs [python-build](https://github.com/laputa-systems/python-build)'s CPython. On glibc that
needs the distribution's `musl` package (Debian 13, Ubuntu 26.04). With older or missing musl, as on Ubuntu 24.04
([python-build#19](https://github.com/laputa-systems/python-build/issues/19)), it installs the same CPython version
through the pinned uv instead. Downloads are cached under `${XDG_CACHE_HOME:-~/.cache}/buildlib-bootstrap`. Update a
vendored copy by copying the directory again.

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

Licensed under [Apache-2.0](LICENSE). Report problems through [GitHub issues](https://github.com/laputa-systems/buildlib/issues).
