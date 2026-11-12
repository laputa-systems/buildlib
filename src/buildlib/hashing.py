"""SHA-256 helpers for files."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1 << 20


def digest_file(path: Path) -> tuple[str, int]:
    """Return the lowercase hex SHA-256 and byte size of a file."""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of a file."""
    return digest_file(path)[0]


def is_sha256(value: object) -> bool:
    """True for a 64-character lowercase hexadecimal string."""
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
