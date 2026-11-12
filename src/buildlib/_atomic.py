"""Atomic file publication shared by the fetch, manifest, and archive writers."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import BinaryIO


@contextlib.contextmanager
def atomic_file(destination: Path) -> Generator[BinaryIO]:
    """Yield a binary handle whose content replaces `destination` only on success."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
    scratch = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        scratch.chmod(0o644)
        os.replace(scratch, destination)
    finally:
        scratch.unlink(missing_ok=True)
