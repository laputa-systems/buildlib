# Owned tools from pinned releases

`buildlib.tools` installs the fleet's own released tools — gluon (meson, ninja,
gluon-build), the ffmpeg-build products, cmake, llvm, node, cpython, git, ssh and
oxish, ripgrep, fd, jaq, and mold — from the producer's GitHub release, verified
against a consumer-pinned lock.

## The lock

`tools.lock.json` (schema 1) pins each tool to a producer repository, a release
tag, a product, a version, and one asset per target triple:

```json
{
  "schema": 1,
  "tools": {
    "gluon": {
      "repository": "laputa-systems/gluon",
      "tag": "release-fa8b6aace38683a17ee3f398d8b568341c7192fe",
      "product": "gluon",
      "version": "",
      "assets": {
        "aarch64-apple-darwin": {"name": "gluon-aarch64-apple-darwin.tar.xz", "size": 860740, "sha256": "…"}
      }
    }
  }
}
```

`version` is empty for unversioned products (gluon, rg, fd, jaq). Some producers
ship several products per release; the tool name selects the product (for
example `ffmpeg-runtime` versus `ffmpeg-play-sdk`, `llvm` versus `llvm-dev`,
`ssh` versus `oxish`). mold is the odd layout — `checksums` instead of
`SHA256SUMS`, `.tar.gz`, and `<arch>-linux-musl` asset names — and the tool table
absorbs that so consumers never see it.

## CLI

```sh
python -m buildlib tools lock gluon --tag release-<sha> [--lock tools.lock.json]
python -m buildlib tools install gluon ffmpeg-runtime --lock tools.lock.json [--cache DIR] [--target TRIPLE]
python -m buildlib tools cache-key gluon ffmpeg-runtime --lock tools.lock.json [--target TRIPLE]
```

`lock` fetches the release's checksum file and the GitHub release API (for asset
sizes) and adds or replaces the entry. `install` prints one bin directory per
tool. `cache-key` prints a stable SHA-256 over the selected entries' pins for the
target, for CI cache keys. Bare invocation prints help; usage errors exit 2.

`install` caches under `<cache>/<tool>/<sha256>/`. A cache hit makes no network
request. A miss downloads the pinned asset, verifies size and SHA-256 before
extraction, extracts defensively (no absolute paths, `..`, escaping links, or
device files), and publishes the directory with one rename. To run buildlib
itself without an installed Python 3.15, vendor `bootstrap/` as described in
[bootstrap.md](bootstrap.md); inside this repository `setup/buildlib` wraps
`bootstrap/buildlib-run` and runs the CLI from `src/`.

## GitHub action

`laputa-systems/buildlib/setup@<sha>` is a composite action. It needs no
registry: `setup/buildlib` bootstraps a pinned Python 3.15 through
`bootstrap/buildlib-run` (which needs `curl`, CA certificates, `tar`, `xz`, and
`sha256sum` or `shasum`) and then runs buildlib from the action checkout's
`src/`, dropping the pinned wheel from `sys.path`.

```yaml
- uses: laputa-systems/buildlib/setup@<sha> # vX.Y.Z
  with:
    tools: gluon ffmpeg-runtime
    lock: tools.lock.json        # default; relative to the workspace
    cache-dir: ${{ runner.temp }}/laputa-tools  # default
```

The action computes `cache-key` for the locked pins, restores `cache-dir` through
`actions/cache`, runs `install`, and appends each printed bin directory to
`GITHUB_PATH`. A second run on the same lock restores from cache without
downloading.

The Linux builds of cpython, node and the ffmpeg products are musl-dynamic. On a
glibc runner without `/lib/ld-musl-<arch>.so.1`, the action installs the
distribution `musl` package with `apt-get` before installing tools. That loader
is enough for cpython. node also needs musl `libstdc++` and `libgcc_s`, and
ffmpeg-runtime needs musl `libva`, which Ubuntu does not ship; use those on glibc
runners only inside a musl container until their producers link them statically.

## Docker and scripts

The same module drives non-GitHub paths. `setup/buildlib` wraps
`bootstrap/buildlib-run`, so a checkout of this repository is enough — no
registry, no installed Python 3.15:

```dockerfile
COPY tools.lock.json /src/tools.lock.json
RUN git clone --depth 1 https://github.com/laputa-systems/buildlib /opt/buildlib \
 && sh /opt/buildlib/setup/buildlib tools install cmake \
      --lock /src/tools.lock.json --cache /opt/laputa-tools > /opt/laputa-bindir \
 && ln -sfn "$(cat /opt/laputa-bindir)" /opt/laputa-bin
ENV PATH="/opt/laputa-bin:${PATH}"
```

`install` prints one bin directory per tool; the symlink gives `PATH` a stable
entry across lock updates (rebuild the image to pick them up). The runner needs `curl`, CA certificates, `tar`, `xz`, and `sha256sum`
or `shasum` (see [bootstrap.md](bootstrap.md)). Scripts and Makefiles call
`python -m buildlib tools install …` with any Python 3.15.
