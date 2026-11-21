# Run buildlib without a Python installation

Copy `bootstrap/` into the consumer repository and commit both files. Run:

```sh
sh bootstrap/buildlib-run -- -c 'import buildlib.locks, sys; print(sys.version)'
```

Pass Python arguments after `--`. The script prepends the pinned wheel to `PYTHONPATH` and executes Python. It caches downloads under `${BUILDLIB_CACHE}` if set, or `${XDG_CACHE_HOME:-$HOME/.cache}/laputa-buildlib` otherwise. Keep the cache private to the user running the script.

Install `curl`, CA certificates, `tar`, `xz`, and either `sha256sum` or `shasum`. On Linux with musl 1.2.5 or newer, or on Alpine, the script uses python-build. On glibc Linux without a compatible system musl loader, it uses the pinned uv release to install a python-build-standalone interpreter. Ubuntu 24.04's musl 1.2.4 is too old for python-build. Debian 13 and Ubuntu 26.04 provide a suitable loader through `apt-get install musl`. Apple Silicon requires macOS 27 or newer. Linux x86-64 requires the Broadwell CPU baseline for python-build, and Linux ARM64 requires ARMv8-A with crypto extensions.

Set `BUILDLIB_PYTHON=python-build` or `BUILDLIB_PYTHON=uv` to select a path. The script prints its selection to standard error. The python-build path requires `/lib/ld-musl-<arch>.so.1` on Linux. If a python-build smoke test fails with a relocation error, automatic selection falls back to uv. The verified python-build cache remains available.

The lock pins each Python archive, uv archive, and the buildlib wheel with SHA-256. The script verifies archive size and SHA-256 before extraction. The pinned uv binary downloads its managed Python from upstream and verifies it using uv's own checksums. Thus the uv release and its Python download metadata are trusted to select and verify that interpreter.

To repin Python, choose a python-build release and update the `release` record and each target's HTTPS archive URL, byte size, and SHA-256 in `bootstrap/python.lock`. Verify the hashes against that release's `SHA256SUMS`. To repin uv, update `uv_version`, each uv archive URL, size, and SHA-256, and `uv_python` to an available upstream Python patch. To repin buildlib, update the wheel HTTPS URL and SHA-256 from its published index. Test each supported target after repinning. The lock records use the `url`, `size`, `sha256`, and `target` fields from the source lock contract, in a shell-readable format.
