from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from binaries import (
    build_version_command,
    dylib_command,
    macho_command,
    macho_image,
    macho_string_command,
    signature_command,
    uuid_command,
    version,
)
from buildlib import macho


def sample(trailer: bytes = b"\x00" * 64, uuid: bytes = bytes(range(16)), signature_at: int | None = None) -> bytes:
    commands = [
        build_version_command(macho.PLATFORM_MACOS, version(26, 0), version(26, 4, 1)),
        dylib_command(macho.LC_LOAD_DYLIB, "/usr/lib/libSystem.B.dylib"),
        dylib_command(macho.LC_LOAD_WEAK_DYLIB, "@rpath/libweak.dylib"),
        macho_string_command(macho.LC_RPATH, "@loader_path/../lib"),
        macho_string_command(macho.LC_RPATH, "@executable_path/lib"),
        uuid_command(uuid),
    ]
    if signature_at is not None:
        commands.append(signature_command(signature_at, 16))
    return macho_image(commands, trailer=trailer)


def test_inspect_decodes_header_and_load_commands(tmp_path: Path) -> None:
    path = tmp_path / "tool"
    path.write_bytes(sample())
    assert macho.inspect(path) == {
        "arch": "arm64",
        "kind": "execute",
        "is_64": True,
        "pie": True,
        "platform": "macos",
        "minos": "26.0",
        "sdk": "26.4.1",
        "install_name": None,
        "dependencies": ["/usr/lib/libSystem.B.dylib", "@rpath/libweak.dylib"],
        "rpaths": ["@loader_path/../lib", "@executable_path/lib"],
    }


def test_dylib_id_and_legacy_version_min(tmp_path: Path) -> None:
    path = tmp_path / "libx.dylib"
    path.write_bytes(
        macho_image(
            [
                dylib_command(macho.LC_ID_DYLIB, "@rpath/libx.dylib"),
                macho_command(macho.LC_VERSION_MIN_MACOSX, b"".join(v.to_bytes(4, "little") for v in (version(13, 5), version(14, 0)))),
            ],
            filetype=macho.MH_DYLIB,
            flags=0,
        )
    )
    assert macho.dylib_id(path) == "@rpath/libx.dylib"
    found = macho.build_version(path)
    assert found is not None
    assert (found.platform, found.platform_name, found.minos, found.sdk) == (macho.PLATFORM_MACOS, "macos", "13.5", "14.0")
    assert macho.read_header(path).kind == "dylib"
    assert not macho.read_header(path).pie


def test_image_without_build_version_has_none(tmp_path: Path) -> None:
    path = tmp_path / "bare"
    path.write_bytes(macho_image([]))
    assert macho.build_version(path) is None
    assert macho.dependencies(path) == []
    assert macho.load_commands(path) == []


def test_is_macho_and_find_machos_ignore_other_files_and_symlinks(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    image = tmp_path / "bin" / "tool"
    image.write_bytes(sample())
    (tmp_path / "bin" / "alias").symlink_to("tool")
    (tmp_path / "readme.txt").write_text("text")
    assert macho.is_macho(image)
    assert not macho.is_macho(tmp_path / "readme.txt")
    assert not macho.is_macho(tmp_path / "missing")
    assert macho.find_machos(tmp_path) == [image]


def signed(uuid: bytes, signature: bytes) -> bytes:
    offset = len(sample(trailer=b"", signature_at=0)) + 4
    return sample(trailer=b"code" + signature, uuid=uuid, signature_at=offset)


def test_normalized_bytes_zeroes_uuid_and_signature_only(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(signed(bytes(range(16)), b"S" * 16))
    second.write_bytes(signed(bytes(reversed(range(16))), b"T" * 16))
    assert first.read_bytes() != second.read_bytes()
    assert macho.normalized_bytes(first) == macho.normalized_bytes(second)
    uuid_range, signature_range = macho.signature_metadata_ranges(first)
    assert uuid_range[1] == 16
    offset, size = signature_range
    assert first.read_bytes()[offset - 4:offset + size] == b"code" + b"S" * 16
    assert macho.normalized_bytes(first)[offset - 4:offset + size] == b"code" + bytes(16)


def test_normalized_bytes_leaves_compiled_content_alone(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(sample(trailer=b"code-1"))
    second.write_bytes(sample(trailer=b"code-2"))
    assert macho.normalized_bytes(first) != macho.normalized_bytes(second)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"\xca\xfe\xba\xbe" + b"\x00" * 60, "fat/universal"),
        (b"not a macho file at all", "not a Mach-O"),
        (b"\xcf\xfa\xed\xfe\x00", "truncated Mach-O header"),
    ],
)
def test_rejects_fat_foreign_and_truncated_files(tmp_path: Path, data: bytes, message: str) -> None:
    path = tmp_path / "bad"
    path.write_bytes(data)
    with pytest.raises(macho.MachOError, match=message):
        macho.read_header(path)


def test_rejects_load_commands_that_run_past_the_header(tmp_path: Path) -> None:
    good = sample()
    path = tmp_path / "short"
    path.write_bytes(good[:60])
    with pytest.raises(macho.MachOError, match="truncated load commands"):
        macho.load_commands(path)
    bad_size = bytearray(macho_image([macho_command(macho.LC_UUID, bytes(16))]))
    bad_size[32 + 4:32 + 8] = (4).to_bytes(4, "little")
    path.write_bytes(bytes(bad_size))
    with pytest.raises(macho.MachOError, match="malformed load command size"):
        macho.load_commands(path)


def fake_codesign(directory: Path, status: int, message: str) -> None:
    script = directory / "codesign"
    script.write_text(f"#!/bin/sh\necho '{message}' >&2\nexit {status}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_signature_status_reports_codesign_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    image = tmp_path / "tool"
    image.write_bytes(sample())
    fake_codesign(bin_dir, 0, "valid on disk")
    assert macho.signature_status(image) == (True, "valid on disk")
    fake_codesign(bin_dir, 1, "code object is not signed at all")
    assert macho.signature_status(image) == (False, "code object is not signed at all")


@pytest.mark.parametrize("unreadable", ["directory", "file"])
def test_find_machos_propagates_permission_errors(tmp_path: Path, unreadable: str) -> None:
    directory = tmp_path / "nested"
    directory.mkdir()
    image = directory / "image"
    image.write_bytes(sample())
    other = tmp_path / "a-image"
    other.write_bytes(image.read_bytes())
    (tmp_path / "linked-directory").symlink_to(directory, target_is_directory=True)
    assert macho.find_machos(tmp_path) == [other, image]
    target = directory if unreadable == "directory" else image
    mode = target.stat().st_mode
    target.chmod(0)
    try:
        with pytest.raises(PermissionError):
            macho.find_machos(tmp_path)
    finally:
        target.chmod(mode)
    assert macho.find_machos(tmp_path) == [other, image]
