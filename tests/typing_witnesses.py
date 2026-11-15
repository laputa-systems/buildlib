"""Programs the type checker must reject; `unused-ignore-comment` fails if one is accepted."""

from buildlib.hashing import is_sha256


def increment_if_not_digest(value: str | int) -> int:
    if not is_sha256(value):
        return value + 1  # ty: ignore[unsupported-operator, unsound-return-statement]
    return 0
