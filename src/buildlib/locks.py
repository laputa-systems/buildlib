"""Readers and validators for the pinned-input files build repositories share.

`sources.lock.json`, `release-budgets.json`, and `bootstrap.lock.json` require integer schema version 1.
Source input extras, per-target budget values, and the bootstrap object are preserved.
Other top-level source and budget keys are discarded.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import _atomic, hashing, targets

SCHEMA_VERSION = 1
USER_AGENT = "laputa-buildlib/0.1"

_INPUT_KEYS = frozenset({"name", "version", "sha256", "url", "urls", "size", "role", "target", "license", "purpose"})
_CHUNK = 1 << 20


class LockError(Exception):
    """A lock file is unreadable or violates its schema."""

    def __init__(self, path: Path | str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = Path(path)
        self.reason = reason


class FetchError(Exception):
    """No URL served the pinned bytes for an input."""


@dataclass(frozen=True)
class Input:
    """One pinned upstream input.

    `urls` lists mirrors that serve identical bytes, in the order to try them.
    `extra` holds fields the contract does not name.
    """

    name: str
    version: str
    sha256: str
    urls: tuple[str, ...]
    size: int | None = None
    role: str | None = None
    target: str | None = None
    license: str | None = None
    purpose: str | None = None
    extra: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}), hash=False)

    @property
    def url(self) -> str:
        """The first URL to try."""
        return self.urls[0]


@dataclass(frozen=True)
class SourcesLock:
    """A validated `sources.lock.json`."""

    path: Path
    inputs: tuple[Input, ...]

    def get(self, name: str) -> Input:
        """Return the input called `name`, or raise LockError."""
        for item in self.inputs:
            if item.name == name:
                return item
        raise LockError(self.path, f"no input named {name!r}")


@dataclass(frozen=True)
class ReleaseBudgets:
    """A validated `release-budgets.json`: per-target limits keyed by target triple."""

    path: Path
    targets: Mapping[str, Mapping[str, Any]]

    def for_target(self, triple: str) -> Mapping[str, Any]:
        """Return the limits recorded for `triple`, or raise LockError."""
        try:
            return self.targets[triple]
        except KeyError:
            raise LockError(self.path, f"no budget recorded for target {triple!r}") from None


def _read_object(path: Path | str) -> tuple[Path, dict[str, Any]]:
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise LockError(path, f"cannot read: {error}") from error
    except json.JSONDecodeError as error:
        raise LockError(path, f"invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise LockError(path, "expected a JSON object")
    version = document.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise LockError(path, f"schema_version must be {SCHEMA_VERSION}, found {version!r}")
    return path, document


def _text(path: Path, where: str, entry: Mapping[str, Any], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise LockError(path, f"{where}: {key} must be a nonempty string")
    return value


def _optional_text(path: Path, where: str, entry: Mapping[str, Any], key: str) -> str | None:
    if key not in entry:
        return None
    return _text(path, where, entry, key)


def _urls(path: Path, where: str, entry: Mapping[str, Any]) -> tuple[str, ...]:
    if ("url" in entry) == ("urls" in entry):
        raise LockError(path, f"{where}: exactly one of url and urls is required")
    raw = [entry["url"]] if "url" in entry else entry["urls"]
    if not isinstance(raw, list) or not raw:
        raise LockError(path, f"{where}: urls must be a nonempty list")
    for url in raw:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise LockError(path, f"{where}: every URL must be an https:// string, found {url!r}")
        try:
            parsed = urllib.parse.urlsplit(url)
            if not parsed.hostname:
                raise ValueError("missing host")
            _ = parsed.port
        except ValueError as error:
            raise LockError(path, f"{where}: invalid URL {url!r}: {error}") from error
    if len(set(raw)) != len(raw):
        raise LockError(path, f"{where}: urls repeat a mirror")
    return tuple(raw)


def _input(path: Path, index: int, entry: object) -> Input:
    if not isinstance(entry, dict):
        raise LockError(path, f"inputs[{index}]: expected an object")
    name = entry.get("name")
    where = f"input {name!r}" if isinstance(name, str) and name else f"inputs[{index}]"
    name = _text(path, where, entry, "name")
    sha256 = entry.get("sha256")
    if not hashing.is_sha256(sha256):
        raise LockError(path, f"{where}: sha256 must be 64 lowercase hex digits")
    assert isinstance(sha256, str)
    size = entry.get("size")
    if "size" in entry and (type(size) is not int or size < 0):
        raise LockError(path, f"{where}: size must be a nonnegative integer")
    target = _optional_text(path, where, entry, "target")
    return Input(
        name=name,
        version=_text(path, where, entry, "version"),
        sha256=sha256,
        urls=_urls(path, where, entry),
        size=size,
        role=_optional_text(path, where, entry, "role"),
        target=target,
        license=_optional_text(path, where, entry, "license"),
        purpose=_optional_text(path, where, entry, "purpose"),
        extra=MappingProxyType({key: value for key, value in entry.items() if key not in _INPUT_KEYS}),
    )


def load_sources(path: Path | str) -> SourcesLock:
    """Load and validate `sources.lock.json`; input names must be unique."""
    path, document = _read_object(path)
    entries = document.get("inputs")
    if not isinstance(entries, list):
        raise LockError(path, "inputs must be a list")
    inputs = tuple(_input(path, index, entry) for index, entry in enumerate(entries))
    seen: set[str] = set()
    for item in inputs:
        if item.name in seen:
            raise LockError(path, f"duplicate input {item.name!r}")
        seen.add(item.name)
    return SourcesLock(path, inputs)


def load_budgets(path: Path | str) -> ReleaseBudgets:
    """Load and validate `release-budgets.json`; every key of `targets` must be a supported triple."""
    path, document = _read_object(path)
    section = document.get("targets")
    if not isinstance(section, dict):
        raise LockError(path, "targets must be an object")
    for triple, limits in section.items():
        if triple not in targets.TARGETS:
            raise LockError(path, f"targets: unsupported target {triple!r}")
        if not isinstance(limits, dict):
            raise LockError(path, f"targets.{triple}: expected an object")
    return ReleaseBudgets(path, MappingProxyType({triple: MappingProxyType(limits) for triple, limits in section.items()}))


def load_bootstrap(path: Path | str) -> Mapping[str, Any]:
    """Load `bootstrap.lock.json`, checking only the schema version; the rest belongs to the repository."""
    _, document = _read_object(path)
    return MappingProxyType(document)


def verify_file(path: Path | str, item: Input) -> None:
    """Raise FetchError unless the file at `path` has the input's pinned size and SHA-256."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise FetchError(f"{item.name}: {path} is not a regular file")
    sha256, size = hashing.digest_file(path)
    if item.size is not None and size != item.size:
        raise FetchError(f"{item.name}: size {size} != pinned {item.size}")
    if sha256 != item.sha256:
        raise FetchError(f"{item.name}: sha256 {sha256} != pinned {item.sha256}")


def _download(item: Input, url: str, destination: Path, timeout: float) -> None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        response = urllib.request.urlopen(request, timeout=timeout)
    except ValueError as error:
        raise FetchError(f"{item.name}: {url}: invalid URL or redirect: {error}") from error
    digest = hashlib.sha256()
    received = 0
    with response, _atomic.atomic_file(destination) as output:
        while chunk := response.read(_CHUNK):
            received += len(chunk)
            if item.size is not None and received > item.size:
                raise FetchError(f"{item.name}: {url} served more than the pinned {item.size} bytes")
            digest.update(chunk)
            output.write(chunk)
        if item.size is not None and received != item.size:
            raise FetchError(f"{item.name}: {url} served {received} bytes, pinned {item.size}")
        if digest.hexdigest() != item.sha256:
            raise FetchError(f"{item.name}: {url} served sha256 {digest.hexdigest()}, pinned {item.sha256}")


def fetch_input(item: Input, destination: Path | str, *, timeout: float = 120.0) -> Path:
    """Download an input to `destination`, trying `item.urls` in order.

    The file is published atomically and only when its size and SHA-256 match the
    pin; a destination that already verifies is returned untouched. Raises
    FetchError listing every URL's failure when none serves the pinned bytes.
    """
    destination = Path(destination)
    try:
        verify_file(destination, item)
    except FetchError:
        pass
    else:
        return destination
    if destination.is_symlink():
        raise FetchError(f"{item.name}: refusing to replace symlink {destination}")
    failures = []
    for url in item.urls:
        try:
            _download(item, url, destination, timeout)
        except FetchError as error:
            failures.append(str(error))
        except (OSError, http.client.HTTPException) as error:
            failures.append(f"{item.name}: {url}: {error}")
        else:
            return destination
    raise FetchError(f"{item.name}: no URL served the pinned bytes: " + "; ".join(failures))


__all__ = [
    "SCHEMA_VERSION",
    "FetchError",
    "Input",
    "LockError",
    "ReleaseBudgets",
    "SourcesLock",
    "fetch_input",
    "load_bootstrap",
    "load_budgets",
    "load_sources",
    "verify_file",
]
