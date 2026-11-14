"""Release assets: the per-target manifest, a reproducible `.tar.xz`, and `SHA256SUMS`."""

from __future__ import annotations

import json
import os
import re
import stat
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import _atomic, hashing, targets

MANIFEST_SCHEMA_VERSION = 1
CHECKSUMS_NAME = "SHA256SUMS"
REQUIRED_MANIFEST_FIELDS = (
    "schema_version",
    "product",
    "version",
    "target",
    "source_revision",
    "sources_lock_sha256",
)

_NAME_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SUMS_LINE = re.compile(r"([0-9a-f]{64})  (.+)\Z")
_XZ_PRESET = 6


class ReleaseError(Exception):
    """Release assets cannot be built or do not verify."""


def _stem(product: str, version: str, target: str) -> str:
    for label, value in (("product", product), ("version", version)):
        if not _NAME_PART.fullmatch(value):
            raise ReleaseError(f"invalid {label} for an asset name: {value!r}")
    targets.target_for_triple(target)
    return f"{product}-{version}-{target}"


def archive_name(product: str, version: str, target: str) -> str:
    """The standard archive name, `<product>-<version>-<target>.tar.xz`."""
    return f"{_stem(product, version, target)}.tar.xz"


def manifest_name(product: str, version: str, target: str) -> str:
    """The standard manifest name, `<product>-<version>-<target>.manifest.json`."""
    return f"{_stem(product, version, target)}.manifest.json"


def _ordered(value: Any) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ReleaseError("manifest keys must be strings")
        return {key: _ordered(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_ordered(item) for item in value]
    return value


def build_manifest(
    *,
    product: str,
    version: str,
    target: str,
    source_revision: str,
    sources_lock_sha256: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a manifest with the required fields first, then `extra` keys sorted at every depth.

    `source_revision` is the full commit SHA and `sources_lock_sha256` the digest of
    `sources.lock.json` at that commit. `extra` may not redefine a required field.
    """
    _stem(product, version, target)
    if not _REVISION.fullmatch(source_revision):
        raise ReleaseError(f"source_revision must be a full lowercase commit SHA: {source_revision!r}")
    if not hashing.is_sha256(sources_lock_sha256):
        raise ReleaseError(f"sources_lock_sha256 must be 64 lowercase hex digits: {sources_lock_sha256!r}")
    extra = {} if extra is None else extra
    clash = sorted(set(extra) & set(REQUIRED_MANIFEST_FIELDS))
    if clash:
        raise ReleaseError(f"extra manifest fields redefine required fields: {clash}")
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "product": product,
        "version": version,
        "target": target,
        "source_revision": source_revision,
        "sources_lock_sha256": sources_lock_sha256,
    }
    manifest.update(_ordered(extra))
    return manifest


def write_manifest(directory: Path | str, manifest: Mapping[str, Any]) -> Path:
    """Write `manifest` as `<product>-<version>-<target>.manifest.json` in `directory` and return its path.

    The manifest is re-checked for its required fields; the file is indented JSON with a
    trailing newline and is published atomically.
    """
    missing = [key for key in REQUIRED_MANIFEST_FIELDS if key not in manifest]
    if missing:
        raise ReleaseError(f"manifest lacks required fields: {missing}")
    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ReleaseError(f"manifest schema_version must be {MANIFEST_SCHEMA_VERSION}")
    rebuilt = build_manifest(
        product=manifest["product"],
        version=manifest["version"],
        target=manifest["target"],
        source_revision=manifest["source_revision"],
        sources_lock_sha256=manifest["sources_lock_sha256"],
        extra={key: value for key, value in manifest.items() if key not in REQUIRED_MANIFEST_FIELDS},
    )
    path = Path(directory) / manifest_name(rebuilt["product"], rebuilt["version"], rebuilt["target"])
    with _atomic.atomic_file(path) as handle:
        handle.write((json.dumps(rebuilt, indent=2) + "\n").encode("utf-8"))
    return path


def _entry(path: Path, name: str, mtime: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = mtime
    metadata = path.lstat()
    if stat.S_ISDIR(metadata.st_mode):
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
    elif stat.S_ISLNK(metadata.st_mode):
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(path)
        info.mode = 0o777
    elif stat.S_ISREG(metadata.st_mode):
        info.type = tarfile.REGTYPE
        info.size = metadata.st_size
        info.mode = 0o755 if metadata.st_mode & 0o111 else 0o644
    else:
        raise ReleaseError(f"unsupported file type in archive tree: {path}")
    return info


def create_archive(source: Path | str, archive: Path | str, *, mtime: int, prefix: str | None = None) -> Path:
    """Create a deterministic `.tar.xz` of the tree at `source` and return its path.

    Entries are sorted by relative path. Ownership is root with empty names, every
    mtime is `mtime` (typically SOURCE_DATE_EPOCH), directories are 0755, symlinks
    0777, and files 0755 when any execute bit is set and 0644 otherwise. With
    `prefix`, the tree sits under that top-level directory, which gets its own entry.
    The archive is written atomically; identical trees and arguments give identical bytes.
    """
    source, archive = Path(source), Path(archive)
    try:
        source_mode = source.lstat().st_mode
    except FileNotFoundError:
        raise ReleaseError(f"archive source is not a directory: {source}") from None
    if not stat.S_ISDIR(source_mode):
        raise ReleaseError(f"archive source is not a directory: {source}")
    if type(mtime) is not int or mtime < 0:
        raise ReleaseError(f"mtime must be a nonnegative integer: {mtime!r}")
    if prefix is not None and (not prefix or prefix.startswith("/") or ".." in prefix.split("/")):
        raise ReleaseError(f"invalid archive prefix: {prefix!r}")
    members = []
    directories = [source]
    while directories:
        for path in directories.pop().iterdir():
            members.append(path)
            if stat.S_ISDIR(path.lstat().st_mode):
                directories.append(path)
    members.sort(key=lambda path: path.relative_to(source).as_posix())
    with (
        _atomic.atomic_file(archive) as handle,
        tarfile.open(fileobj=handle, mode="w:xz", format=tarfile.PAX_FORMAT, preset=_XZ_PRESET) as tar,
    ):
        if prefix is not None:
            tar.addfile(_entry(source, prefix, mtime))
        for path in members:
            relative = path.relative_to(source).as_posix()
            info = _entry(path, relative if prefix is None else f"{prefix}/{relative}", mtime)
            if info.isreg():
                with path.open("rb") as stream:
                    tar.addfile(info, stream)
            else:
                tar.addfile(info)
    return archive


def _checksum_assets(directory: Path, name: str) -> list[Path]:
    assets = []
    for path in sorted(directory.iterdir(), key=lambda item: os.fsencode(item.name)):
        if path.name == name:
            continue
        if path.is_symlink() or not path.is_file():
            raise ReleaseError(f"release asset is not a regular file: {path}")
        if "\n" in path.name or "\\" in path.name:
            raise ReleaseError(f"release asset name cannot be listed in {name}: {path.name!r}")
        assets.append(path)
    return assets


def write_sha256sums(directory: Path | str, *, name: str = CHECKSUMS_NAME) -> Path:
    """Write `SHA256SUMS` covering every other file in `directory`, sorted by file name.

    Lines are `<hex>  <name>`, the format `sha256sum -c` reads. The directory must be
    flat and hold only regular files.
    """
    directory = Path(directory)
    assets = _checksum_assets(directory, name)
    if not assets:
        raise ReleaseError(f"no assets to list in {directory}")
    text = "".join(f"{hashing.sha256_file(path)}  {path.name}\n" for path in assets)
    path = directory / name
    with _atomic.atomic_file(path) as handle:
        handle.write(text.encode("utf-8"))
    return path


def verify_sha256sums(directory: Path | str, *, name: str = CHECKSUMS_NAME) -> None:
    """Check `SHA256SUMS` against `directory`; raise ReleaseError on the first problem.

    The file must be sorted by name, list each asset once, match every digest, and
    cover every other file in the directory.
    """
    directory = Path(directory)
    sums = directory / name
    try:
        text = sums.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ReleaseError(f"cannot read {sums}: {error}") from error
    if not text.endswith("\n"):
        raise ReleaseError(f"{sums}: missing trailing newline")
    listed: list[tuple[str, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        match = _SUMS_LINE.fullmatch(line)
        if match is None or "/" in match.group(2) or "\\" in match.group(2):
            raise ReleaseError(f"{sums}:{number}: malformed line {line!r}")
        listed.append((match.group(1), match.group(2)))
    names = [entry[1] for entry in listed]
    if len(set(names)) != len(names):
        raise ReleaseError(f"{sums}: an asset is listed twice")
    if names != sorted(names, key=os.fsencode):
        raise ReleaseError(f"{sums}: entries are not sorted by file name")
    assets = {path.name: path for path in _checksum_assets(directory, name)}
    for digest, asset in listed:
        path = assets.get(asset)
        if path is None:
            raise ReleaseError(f"{sums}: lists missing asset {asset}")
        if hashing.sha256_file(path) != digest:
            raise ReleaseError(f"{sums}: digest mismatch for {asset}")
    unlisted = sorted(set(assets) - set(names))
    if unlisted:
        raise ReleaseError(f"{sums}: does not cover {unlisted}")
