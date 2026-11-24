"""Install laputa-owned released tools from a pinned `tools.lock.json`.

A lock entry names the producer repository, release tag, product, version, and one
asset per target triple with its size and SHA-256. `install` downloads only on a
cache miss into `<cache>/<tool>/<sha256>/`, verifies size and digest before
extraction, extracts defensively, and publishes the directory atomically.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from . import _atomic, hashing, locks, release, targets

LOCK_SCHEMA = 1
USER_AGENT = locks.USER_AGENT
_USER_AGENT_HEADERS = {"User-Agent": USER_AGENT}
_DOWNLOAD_TIMEOUT = 120.0
_SUMS_LINE = re.compile(r"([0-9a-f]{64})  (.+)\Z")
_VERSION = r"[0-9][A-Za-z0-9._+-]*"
_LOCK_KEYS = frozenset({"repository", "tag", "product", "version", "assets"})
_ASSET_KEYS = frozenset({"name", "size", "sha256"})


class ToolError(Exception):
    """A tool is unknown, a release does not match the contract, or an archive is unsafe."""


@dataclass(frozen=True)
class ToolSpec:
    """How one owned tool's release assets are named and laid out.

    `layout` is "std" (`<product>-<version>-<triple>.tar.xz`), "bare"
    (`<product>-<triple>.tar.xz`, unversioned), or "mold"
    (`<product>-<version>-<arch>-linux-musl.tar.gz`). `prefix` is "flat" when the
    archive root is the payload or "top" when a single top-level directory wraps
    it. `bin_dir` is the payload-relative directory holding the programs; empty
    means the payload root itself.
    """

    tool: str
    repository: str
    product: str
    layout: str
    versioned: bool
    checksum_name: str = "SHA256SUMS"
    prefix: str = "flat"
    bin_dir: str = ""

    def asset_name(self, version: str, triple: str) -> str:
        """The release asset name for `version` on `triple`."""
        target = targets.target_for_triple(triple)
        if self.layout == "bare":
            return f"{self.product}-{target.triple}.tar.xz"
        if self.layout == "mold":
            return f"{self.product}-{version}-{target.machine}-linux-musl.tar.gz"
        return release.archive_name(self.product, version, target.triple)

    def match_asset(self, name: str, triple: str) -> str | None:
        """The version `name` carries for `triple`, or None when it is not this tool's asset."""
        target = targets.target_for_triple(triple)
        if self.layout == "bare":
            return "" if name == f"{self.product}-{target.triple}.tar.xz" else None
        product = re.escape(self.product)
        if self.layout == "mold":
            pattern = (
                rf"^{product}-(?P<version>{_VERSION})-{re.escape(target.machine)}-linux-musl\.tar\.gz$"
            )
        else:
            pattern = rf"^{product}-(?P<version>{_VERSION})-{re.escape(target.triple)}\.tar\.xz$"
        match = re.fullmatch(pattern, name)
        if match is None:
            return None
        version = match.group("version")
        assert isinstance(version, str)
        return version


TOOLS: Mapping[str, ToolSpec] = MappingProxyType(
    {
        spec.tool: spec
        for spec in (
            ToolSpec("gluon", "laputa-systems/gluon", "gluon", "bare", versioned=False),
            ToolSpec("rg", "laputa-systems/rg", "rg", "bare", versioned=False),
            ToolSpec("fd", "laputa-systems/fd", "fd", "bare", versioned=False),
            ToolSpec("jaq", "laputa-systems/jaq", "jaq", "bare", versioned=False),
            ToolSpec("git", "laputa-systems/git-build", "git", "std", versioned=True),
            ToolSpec("ssh", "laputa-systems/ssh-build", "ssh", "std", versioned=True),
            ToolSpec("oxish", "laputa-systems/ssh-build", "oxish", "std", versioned=True, bin_dir="bin"),
            ToolSpec("cmake", "laputa-systems/cmake-build", "cmake", "std", versioned=True, prefix="top", bin_dir="bin"),
            ToolSpec("node", "laputa-systems/node-build", "node", "std", versioned=True, prefix="top", bin_dir="bin"),
            ToolSpec("cpython", "laputa-systems/python-build", "cpython", "std", versioned=True, prefix="top", bin_dir="bin"),
            ToolSpec("llvm", "laputa-systems/llvm-prebuilt-musl", "llvm", "std", versioned=True, prefix="top", bin_dir="bin"),
            ToolSpec("llvm-dev", "laputa-systems/llvm-prebuilt-musl", "llvm-dev", "std", versioned=True, prefix="top", bin_dir="bin"),
            ToolSpec("ffmpeg-runtime", "laputa-systems/ffmpeg-build", "ffmpeg-runtime", "std", versioned=True, prefix="top", bin_dir="bin"),
            ToolSpec("ffmpeg-tools", "laputa-systems/ffmpeg-build", "ffmpeg-tools", "std", versioned=True, prefix="top", bin_dir="bin"),
            ToolSpec("ffmpeg-play-sdk", "laputa-systems/ffmpeg-build", "ffmpeg-play-sdk", "std", versioned=True, prefix="top"),
            ToolSpec("ffmpeg-fixtures", "laputa-systems/ffmpeg-build", "ffmpeg-fixtures", "std", versioned=True, prefix="top", bin_dir="bin"),
            ToolSpec(
                "mold",
                "joshuarli/mold",
                "mold",
                "mold",
                versioned=True,
                checksum_name="checksums",
                prefix="top",
                bin_dir="bin",
            ),
        )
    }
)


@dataclass(frozen=True)
class AssetPin:
    """One locked release asset: its name, byte size, and SHA-256."""

    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ToolEntry:
    """The locked release of one tool, with an asset pin per target triple."""

    tool: str
    repository: str
    tag: str
    product: str
    version: str
    assets: Mapping[str, AssetPin]

    def asset_for(self, triple: str) -> AssetPin:
        """The pin for `triple`, or raise ToolError."""
        try:
            return self.assets[triple]
        except KeyError:
            raise ToolError(f"{self.tool} {self.tag}: no asset for target {triple!r}") from None

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "tag": self.tag,
            "product": self.product,
            "version": self.version,
            "assets": {
                triple: {"name": pin.name, "size": pin.size, "sha256": pin.sha256}
                for triple, pin in self.assets.items()
            },
        }


@dataclass(frozen=True)
class ToolsLock:
    """A validated `tools.lock.json`."""

    path: Path
    entries: Mapping[str, ToolEntry]

    def entry(self, tool: str) -> ToolEntry:
        """The entry for `tool`, or raise ToolError."""
        try:
            return self.entries[tool]
        except KeyError:
            raise ToolError(f"{self.path}: no entry for tool {tool!r}") from None


def spec_for(tool: str) -> ToolSpec:
    """The tool-table spec for `tool`, or raise ToolError."""
    try:
        return TOOLS[tool]
    except KeyError:
        raise ToolError(f"unknown tool {tool!r} (known: {sorted(TOOLS)})") from None


def _read_json(path: Path | str) -> tuple[Path, dict[str, Any]]:
    path = Path(path)
    try:
        document = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError) as error:
        raise locks.LockError(path, f"cannot read: {error}") from error
    except json.JSONDecodeError as error:
        raise locks.LockError(path, f"invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise locks.LockError(path, "expected a JSON object")
    return path, document


def _require_keys(path: Path, where: str, entry: Mapping[str, Any], allowed: frozenset[str]) -> None:
    extra = sorted(set(entry) - allowed)
    missing = sorted(allowed - set(entry))
    if extra or missing:
        parts = []
        if missing:
            parts.append(f"missing {missing}")
        if extra:
            parts.append(f"unexpected {extra}")
        raise locks.LockError(path, f"{where}: {'; '.join(parts)}")


def _text(path: Path, where: str, entry: Mapping[str, Any], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise locks.LockError(path, f"{where}: {key} must be a nonempty string")
    return value


def _entry(path: Path, tool: str, raw: object) -> ToolEntry:
    spec = TOOLS.get(tool)
    if spec is None:
        raise locks.LockError(path, f"tools.{tool}: unknown tool (known: {sorted(TOOLS)})")
    where = f"tools.{tool}"
    if not isinstance(raw, dict):
        raise locks.LockError(path, f"{where}: expected an object")
    _require_keys(path, where, raw, _LOCK_KEYS)
    repository = _text(path, where, raw, "repository")
    if repository != spec.repository:
        raise locks.LockError(path, f"{where}: repository {repository!r} != {spec.repository!r}")
    tag = _text(path, where, raw, "tag")
    product = _text(path, where, raw, "product")
    if product != spec.product:
        raise locks.LockError(path, f"{where}: product {product!r} != {spec.product!r}")
    version = raw.get("version")
    if not isinstance(version, str):
        raise locks.LockError(path, f"{where}: version must be a string")
    if spec.versioned and not version:
        raise locks.LockError(path, f"{where}: version must be nonempty for {tool}")
    if not spec.versioned and version:
        raise locks.LockError(path, f"{where}: {tool} is unversioned; version must be empty")
    raw_assets = raw.get("assets")
    if not isinstance(raw_assets, dict) or not raw_assets:
        raise locks.LockError(path, f"{where}: assets must be a nonempty object")
    assets: dict[str, AssetPin] = {}
    for triple, pin_raw in raw_assets.items():
        asset_where = f"{where}.assets.{triple}"
        if triple not in targets.TARGETS:
            raise locks.LockError(path, f"{asset_where}: unsupported target {triple!r}")
        if not isinstance(pin_raw, dict):
            raise locks.LockError(path, f"{asset_where}: expected an object")
        _require_keys(path, asset_where, pin_raw, _ASSET_KEYS)
        name = _text(path, asset_where, pin_raw, "name")
        expected = spec.asset_name(version, triple)
        if name != expected:
            raise locks.LockError(path, f"{asset_where}: name {name!r} != {expected!r}")
        size = pin_raw.get("size")
        if type(size) is not int or size < 0:
            raise locks.LockError(path, f"{asset_where}: size must be a nonnegative integer")
        sha256 = pin_raw.get("sha256")
        if not hashing.is_sha256(sha256):
            raise locks.LockError(path, f"{asset_where}: sha256 must be 64 lowercase hex digits")
        assets[triple] = AssetPin(name=name, size=size, sha256=sha256)
    return ToolEntry(
        tool=tool,
        repository=repository,
        tag=tag,
        product=product,
        version=version,
        assets=MappingProxyType(assets),
    )


def load_lock(path: Path | str) -> ToolsLock:
    """Load and validate `tools.lock.json` (schema 1)."""
    path, document = _read_json(path)
    _require_keys(path, "lock", document, frozenset({"schema", "tools"}))
    schema = document.get("schema")
    if type(schema) is not int or schema != LOCK_SCHEMA:
        raise locks.LockError(path, f"schema must be {LOCK_SCHEMA}, found {schema!r}")
    raw_tools = document.get("tools")
    if not isinstance(raw_tools, dict):
        raise locks.LockError(path, "tools must be an object")
    entries = {tool: _entry(path, tool, raw) for tool, raw in raw_tools.items()}
    return ToolsLock(path, MappingProxyType(entries))


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers=_USER_AGENT_HEADERS)
    with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as response:
        return bytes(response.read())


def _release_url(repository: str, tag: str, name: str) -> str:
    base = urllib.parse.quote(f"{repository}/releases/download/{tag}")
    return f"https://github.com/{base}/{urllib.parse.quote(name)}"


def _api_url(repository: str, tag: str) -> str:
    return f"https://api.github.com/repos/{repository}/releases/tags/{urllib.parse.quote(tag)}"


def _parse_sums(text: str, path: Path) -> dict[str, str]:
    sums: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        match = _SUMS_LINE.fullmatch(line)
        if match is None:
            raise locks.LockError(path, f"line {number}: malformed checksum line {line!r}")
        digest, name = match.groups()
        if name in sums:
            raise locks.LockError(path, f"line {number}: duplicate asset {name!r}")
        sums[name] = digest
    if not sums:
        raise locks.LockError(path, "checksum file lists no assets")
    return sums


def lock_entry(tool: str, tag: str) -> ToolEntry:
    """Build a lock entry for `tool` at release `tag` from the release's checksum file.

    Fetches the checksum file and the GitHub release API over HTTPS; every asset
    the tool table matches for a supported triple is pinned with its size and
    digest. Raises LockError or ToolError when the release does not fit.
    """
    spec = spec_for(tool)
    if not tag:
        raise ToolError(f"{tool}: tag must be nonempty")
    sums_url = _release_url(spec.repository, tag, spec.checksum_name)
    try:
        sums_text = _fetch(sums_url).decode()
    except (OSError, UnicodeDecodeError) as error:
        raise ToolError(f"{tool}: cannot fetch {sums_url}: {error}") from error
    sums = _parse_sums(sums_text, Path(sums_url))
    try:
        api = json.loads(_fetch(_api_url(spec.repository, tag)))
    except (OSError, json.JSONDecodeError) as error:
        raise ToolError(f"{tool}: cannot fetch release API for {spec.repository}@{tag}: {error}") from error
    sizes: dict[str, int] = {}
    assets = api.get("assets", []) if isinstance(api, dict) else []
    for asset in assets:
        name, size = asset.get("name"), asset.get("size")
        if isinstance(name, str) and type(size) is int:
            sizes[name] = size
    pins: dict[str, AssetPin] = {}
    versions: set[str] = set()
    for triple in targets.TRIPLES:
        for name, digest in sums.items():
            found = spec.match_asset(name, triple)
            if found is None:
                continue
            if name not in sizes:
                raise ToolError(f"{tool}: release API has no size for asset {name!r}")
            pins[triple] = AssetPin(name=name, size=sizes[name], sha256=digest)
            versions.add(found)
            break
    if not pins:
        raise ToolError(f"{tool} {tag}: no assets match the tool table in {spec.checksum_name}")
    if len(versions) > 1:
        raise ToolError(f"{tool} {tag}: assets disagree on version: {sorted(versions)}")
    version = versions.pop()
    return ToolEntry(
        tool=tool,
        repository=spec.repository,
        tag=tag,
        product=spec.product,
        version=version,
        assets=MappingProxyType(pins),
    )


def write_lock(path: Path | str, entry: ToolEntry) -> ToolsLock:
    """Add or replace `entry` in the lock file at `path` and return the reloaded lock."""
    path = Path(path)
    tools_map: dict[str, Any] = {}
    if path.exists():
        existing = load_lock(path)
        tools_map = {name: item.as_dict() for name, item in existing.entries.items()}
    tools_map[entry.tool] = entry.as_dict()
    document = {"schema": LOCK_SCHEMA, "tools": tools_map}
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    with _atomic.atomic_file(path) as handle:
        handle.write(text.encode())
    return load_lock(path)


def _resolve_lock(lock: ToolsLock | Path | str) -> ToolsLock:
    if isinstance(lock, ToolsLock):
        return lock
    return load_lock(lock)


def _resolve_target(target: str | targets.Target | None) -> str:
    if target is None:
        return targets.native_target().triple
    if isinstance(target, targets.Target):
        return target.triple
    return targets.target_for_triple(target).triple


def cache_key(lock: ToolsLock | Path | str, tools: list[str], target: str | targets.Target | None = None) -> str:
    """A stable digest of the selected entries' pins for one target, for CI caching."""
    resolved = _resolve_lock(lock)
    triple = _resolve_target(target)
    selected = []
    for tool in sorted(tools):
        entry = resolved.entry(tool)
        pin = entry.asset_for(triple)
        selected.append({"tool": tool, "name": pin.name, "size": pin.size, "sha256": pin.sha256})
    canonical = json.dumps(selected, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _member_parts(name: str) -> list[str]:
    if PurePosixPath(name).is_absolute():
        raise ToolError(f"unsafe archive member path: {name!r}")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ToolError(f"unsafe archive member path: {name!r}")
    return parts


def _check_link(member_dir: str, linkname: str) -> None:
    if not linkname or PurePosixPath(linkname).is_absolute():
        raise ToolError(f"unsafe archive link target: {linkname!r}")
    resolved = posixpath.normpath(posixpath.join(member_dir, linkname))
    if resolved == ".." or resolved.startswith("../"):
        raise ToolError(f"archive link escapes the root: {linkname!r}")


def _extract(archive: Path, destination: Path) -> None:
    """Extract `archive` into `destination`, rejecting escapes and non-file members."""
    try:
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            checked: list[tuple[tarfile.TarInfo, list[str]]] = []
            for member in members:
                parts = _member_parts(member.name)
                member_dir = posixpath.dirname(posixpath.join(*parts))
                if member.issym():
                    _check_link(member_dir, member.linkname)
                elif not (member.isreg() or member.isdir()):
                    raise ToolError(
                        f"{archive.name}: unsupported member type {member.type!r} in {member.name!r}"
                    )
                checked.append((member, parts))
            for member, parts in checked:
                target = destination.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.issym():
                    if target.is_symlink() or target.exists():
                        target.unlink()
                    os.symlink(member.linkname, target)
                    continue
                source = tar.extractfile(member)
                assert source is not None
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777 or 0o644)
    except tarfile.TarError as error:
        raise ToolError(f"{archive.name}: not a readable tar archive: {error}") from error


def _payload(root: Path, spec: ToolSpec) -> Path:
    """The directory `bin_dir` is relative to: the single top-level directory for "top" archives."""
    if spec.prefix == "flat":
        return root
    entries = list(root.iterdir())
    if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
        raise ToolError(f"{spec.tool}: archive lacks a single top-level directory")
    return entries[0]


def _bin_dir(payload: Path, spec: ToolSpec) -> Path:
    """The payload-relative directory holding the programs."""
    candidate = payload / spec.bin_dir
    if not candidate.is_dir():
        raise ToolError(f"{spec.tool}: archive has no {spec.bin_dir} directory")
    return candidate


def _publish_binaries(directory: Path, spec: ToolSpec, triple: str) -> None:
    """Rename bare-archive payloads to their command names.

    A bare release ships the program as `<product>-<triple>`; consumers put the
    bin directory on PATH, so it must appear as `<product>` and stay executable.
    """
    if spec.layout != "bare":
        return
    source = directory / f"{spec.product}-{triple}"
    if not source.is_file() or source.is_symlink():
        return
    target = directory / spec.product
    source.replace(target)
    target.chmod(0o755)


def install(
    lock: ToolsLock | Path | str,
    tool: str,
    cache_dir: Path | str,
    target: str | targets.Target | None = None,
) -> Path:
    """Install `tool` from the lock into `cache_dir`; return its bin directory.

    The cache lives at `<cache_dir>/<tool>/<sha256>/`. A hit returns without any
    network access; a miss downloads the pinned asset, verifies size and SHA-256,
    extracts it defensively, and publishes the directory with one rename.
    """
    resolved = _resolve_lock(lock)
    entry = resolved.entry(tool)
    spec = spec_for(tool)
    triple = _resolve_target(target)
    pin = entry.asset_for(triple)
    cache = Path(cache_dir) / tool
    final = cache / pin.sha256
    cached = final / spec.bin_dir
    if final.is_symlink() or (final.exists() and not final.is_dir()):
        raise ToolError(f"{tool}: cache path {final} is not a directory")
    if final.is_dir():
        if cached.is_dir():
            return cached
        shutil.rmtree(final)
    cache.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=cache))
    try:
        archive = staging / pin.name
        item = locks.Input(
            name=pin.name,
            version=entry.version or entry.tag,
            sha256=pin.sha256,
            urls=(_release_url(entry.repository, entry.tag, pin.name),),
            size=pin.size,
        )
        locks.fetch_input(item, archive)
        tree = staging / pin.sha256
        tree.mkdir()
        _extract(archive, tree)
        payload = _payload(tree, spec)
        binaries = _bin_dir(payload, spec)
        _publish_binaries(binaries, spec, triple)
        if final.exists() or final.is_symlink():
            if final.is_dir() and cached.is_dir():
                shutil.rmtree(staging)
                return cached
            raise ToolError(f"{tool}: cache path {final} appeared during install")
        os.rename(payload, final)
    except BaseException:
        shutil.rmtree(staging)
        raise
    shutil.rmtree(staging)
    return cached


__all__ = [
    "LOCK_SCHEMA",
    "TOOLS",
    "AssetPin",
    "ToolEntry",
    "ToolError",
    "ToolSpec",
    "ToolsLock",
    "cache_key",
    "install",
    "load_lock",
    "lock_entry",
    "spec_for",
    "write_lock",
]
