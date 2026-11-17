from __future__ import annotations

import os
import shutil
import stat
import struct
import subprocess
from collections.abc import Callable
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


def string_image_command(command: int, offset: int, tail: bytes) -> bytes:
    payload = struct.pack("<I", offset) + tail
    return struct.pack("<II", command, 8 + len(payload)) + payload


def test_load_command_strings_preserve_bytes_and_order(tmp_path: Path) -> None:
    path = tmp_path / "strings"
    path.write_bytes(macho_image([
        string_image_command(0x0C, 14, b"xxfirst\x00ignored"),
        string_image_command(0x8000001C, 12, b"one\x00ignored"),
        string_image_command(0x80000018, 12, b"bad\xff\x00ignored"),
        string_image_command(0x0D, 12, b"id\x00ignored"),
        string_image_command(0x8000001C, 12, b"tail"),
        string_image_command(0x8000001F, 12, b"\x00ignored"),
        string_image_command(0x80000023, 15, b"abcZ"),
        string_image_command(0x0C, 8, b"unused"),
    ]))
    assert macho.dependencies(path) == ["first", "bad\ufffd", "", "Z", "\x08"]
    assert macho.dylib_id(path) == "id"
    assert macho.rpaths(path) == ["one", "tail"]


@pytest.mark.parametrize(
    ("command", "reader"),
    [(0x0C, macho.dependencies), (0x0D, macho.dylib_id), (0x8000001C, macho.rpaths)],
)
@pytest.mark.parametrize("offset", [7, 16, 17])
def test_load_command_string_rejects_outside_offsets(
    tmp_path: Path, command: int, reader: Callable[[Path], object], offset: int
) -> None:
    path = tmp_path / "bad-string"
    path.write_bytes(macho_image([string_image_command(command, offset, b"tail")]))
    with pytest.raises(macho.MachOError, match=f"string offset {offset} is outside the command"):
        reader(path)


def test_image_without_build_version_has_none(tmp_path: Path) -> None:
    path = tmp_path / "bare"
    path.write_bytes(macho_image([]))
    assert macho.build_version(path) is None
    assert macho.dependencies(path) == []
    assert macho.load_commands(path) == []


@pytest.mark.parametrize("endian", ["<", ">"])
@pytest.mark.parametrize("is_64", [False, True])
@pytest.mark.parametrize("version_command", [0x32, 0x24])
def test_payload_fields_follow_image_byte_order(
    tmp_path: Path, endian: str, is_64: bool, version_command: int
) -> None:
    def string_command(kind: int, text: bytes, *, dylib: bool) -> bytes:
        prefix = struct.pack(f"{endian}IIII", 24, 0, 0, 0) if dylib else struct.pack(f"{endian}I", 12)
        payload = prefix + text + b"\0"
        size = (8 + len(payload) + 7) & ~7
        return struct.pack(f"{endian}II", kind, size) + payload + bytes(size - 8 - len(payload))

    version_bytes = (struct.pack(f"{endian}IIIIII", 0x32, 24, 1, 0x000d0500, 0x000e0000, 0)
                     if version_command == 0x32 else struct.pack(f"{endian}IIII", 0x24, 16, 0x000d0500, 0x000e0000))
    commands = [
        version_bytes,
        string_command(0x0d, b"@rpath/libExample.dylib", dylib=True),
        string_command(0x0c, b"/usr/lib/libOne.dylib", dylib=True),
        string_command(0x80000018, b"@rpath/libTwo.dylib", dylib=True),
        string_command(0x8000001c, b"@loader_path/lib", dylib=False),
        string_command(0x8000001c, b"@executable_path/plugins", dylib=False),
    ]
    cpu = (18 if endian == ">" else 7) | (0x01000000 if is_64 else 0)
    fields = [0xfeedfacf if is_64 else 0xfeedface, cpu, 0, 6, len(commands), sum(map(len, commands)), 0]
    if is_64:
        fields.append(0)
    image = struct.pack(f"{endian}IiiIIII" + ("I" if is_64 else ""), *fields) + b"".join(commands)
    path = tmp_path / "byte-order"
    path.write_bytes(image)
    assert macho.read_header(path).cputype == cpu
    assert macho.load_commands(path)[0].payload == version_bytes[8:]
    assert macho.build_version(path) == macho.BuildVersion(1, "13.5", "14.0")
    assert macho.dylib_id(path) == "@rpath/libExample.dylib"
    assert macho.dependencies(path) == ["/usr/lib/libOne.dylib", "@rpath/libTwo.dylib"]
    assert macho.rpaths(path) == ["@loader_path/lib", "@executable_path/plugins"]


@pytest.mark.parametrize(
    ("count", "table_size", "body"),
    [(1, 4, struct.pack("<II", 0x7777, 8)),
     (1, 8, struct.pack("<II", 0x7777, 16)),
     (2, 8, struct.pack("<II", 0x7777, 8))],
)
def test_load_commands_respects_declared_table_extent(
    tmp_path: Path, count: int, table_size: int, body: bytes
) -> None:
    path = tmp_path / "command-table"
    path.write_bytes(struct.pack("<IiiIIII", 0xfeedface, 7, 0, 2, count, table_size, 0) + body)
    with pytest.raises(macho.MachOError):
        macho.load_commands(path)


def test_load_commands_keeps_unknown_commands_and_ignores_non_table_tail(tmp_path: Path) -> None:
    path = tmp_path / "command-table"
    path.write_bytes(struct.pack("<IiiIIII", 0xfeedface, 7, 0, 2, 1, 8, 0)
                     + struct.pack("<II", 0x7777, 8) + b"outside table")
    assert macho.load_commands(path) == [macho.LoadCommand(0x7777, b"", 28)]
    path.write_bytes(struct.pack("<IiiIIII", 0xfeedface, 7, 0, 2, 0, 0, 0) + b"outside table")
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

    for command, readers in (
        (macho.LC_RPATH, (macho.rpaths, macho.inspect)),
        (macho.LC_ID_DYLIB, (macho.dylib_id,)),
        (macho.LC_BUILD_VERSION, (macho.build_version,)),
        (macho.LC_VERSION_MIN_MACOSX, (macho.build_version,)),
    ):
        path.write_bytes(macho_image([macho_command(command, b"")]))
        for reader in readers:
            with pytest.raises(macho.MachOError, match="too short"):
                reader(path)
    path.write_bytes(macho_image([macho_command(next(iter(macho._DEPENDENCY_COMMANDS)), b"")]))
    with pytest.raises(macho.MachOError, match="too short"):
        macho.dependencies(path)


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


@pytest.mark.parametrize(
    ("commands", "trailer"),
    [
        ([macho_command(macho.LC_UUID, b"")], b"a" * 16),
        ([signature_command(1, 0xffffffff)], b"code"),
        ([macho_command(macho.LC_CODE_SIGNATURE, b"")], b"code"),
        ([signature_command(32, 16)], b"code" * 4),
        ([signature_command(48, 8)], b"s" * 16),
        ([signature_command(64, 8), signature_command(64, 8)], b"s" * 8),
    ],
)
def test_normalization_rejects_malformed_metadata(
    tmp_path: Path, commands: list[bytes], trailer: bytes
) -> None:
    path = tmp_path / "malformed"
    path.write_bytes(macho_image(commands, trailer=trailer))
    with pytest.raises(macho.MachOError):
        macho.normalized_bytes(path)


def test_signature_normalization_preserves_preceding_content(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(signed(bytes(16), b"S" * 16))
    second.write_bytes(signed(bytes(16), b"S" * 16).replace(b"code", b"diff"))
    assert macho.normalized_bytes(first) != macho.normalized_bytes(second)


def literal_macho(
    commands: list[bytes], *, endian: str = "<", is_64: bool = True,
    flags: int = 0, tail: bytes = b"",
) -> bytes:
    cpu = (18 if endian == ">" else 7) | (0x01000000 if is_64 else 0)
    fields = [0xfeedfacf if is_64 else 0xfeedface, cpu, -1, 6,
              len(commands), sum(map(len, commands)), flags]
    if is_64:
        fields.append(0)
    return struct.pack(f"{endian}IiiIIII" + ("I" if is_64 else ""), *fields) + b"".join(commands) + tail


@pytest.mark.parametrize("endian", ["<", ">"])
@pytest.mark.parametrize("is_64", [False, True])
def test_empty_header_preserves_every_raw_field(tmp_path: Path, endian: str, is_64: bool) -> None:
    path = tmp_path / "header"
    path.write_bytes(literal_macho([], endian=endian, is_64=is_64, flags=0x80200000))
    cpu = (18 if endian == ">" else 7) | (0x01000000 if is_64 else 0)
    assert macho.read_header(path) == macho.MachOHeader(
        path=path, magic=0xfeedfacf if is_64 else 0xfeedface, cputype=cpu,
        cpusubtype=-1, filetype=6, flags=0x80200000, is_64=is_64,
    )
    assert macho.load_commands(path) == []


@pytest.mark.parametrize("endian", ["<", ">"])
def test_64_bit_header_requires_reserved_word(tmp_path: Path, endian: str) -> None:
    path = tmp_path / "header"
    path.write_bytes(literal_macho([], endian=endian)[:-4])
    with pytest.raises(macho.MachOError):
        macho.read_header(path)


def test_signature_process_contract_and_output_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "codesign"
    script.write_text(
        '#!/bin/sh\nprintf "%s" "$X_TEST_STDOUT"\nprintf "%s" "$X_TEST_STDERR" >&2\nexit "$X_TEST_STATUS"\n'
    )
    script.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    path = tmp_path / "image with spaces"
    path.write_bytes(b"owned fixture")
    real_run = subprocess.run

    def bounded_run(
        args: list[str], *, capture_output: bool, text: bool, timeout: float, check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert args[0] == "codesign"
        assert args[-1] == str(path)
        assert len(args) == 4
        assert set(args[1:-1]) == {"--verify", "--strict"}
        assert capture_output
        assert text
        assert not check
        assert timeout is not None
        assert 0 < timeout <= 120
        return real_run(args, capture_output=True, text=True, timeout=timeout, check=check)

    monkeypatch.setattr(macho.subprocess, "run", bounded_run)
    for status, stderr, stdout, expected in [
        (0, " valid \n", "ignored", (True, "valid")),
        (7, " failed \n", "ignored", (False, "failed")),
        (0, "", " fallback \n", (True, "fallback")),
        (9, "", "", (False, "")),
    ]:
        monkeypatch.setenv("X_TEST_STATUS", str(status))
        monkeypatch.setenv("X_TEST_STDERR", stderr)
        monkeypatch.setenv("X_TEST_STDOUT", stdout)
        assert macho.signature_status(path) == expected


@pytest.mark.parametrize("endian", ["<", ">"])
@pytest.mark.parametrize("is_64", [False, True])
@pytest.mark.parametrize("gap", [b"", b"CODE"])
def test_signature_ranges_normalize_exact_bytes(
    tmp_path: Path, endian: str, is_64: bool, gap: bytes,
) -> None:
    path = tmp_path / "metadata"
    header_size = 32 if is_64 else 28
    offset = header_size + 40 + len(gap)
    commands = [struct.pack(f"{endian}II", 0x1b, 24) + bytes(range(16)),
                struct.pack(f"{endian}IIII", 0x1d, 16, offset, 4)]
    image = literal_macho(commands, endian=endian, is_64=is_64, tail=gap + b"SIGN")
    path.write_bytes(image)
    assert macho.signature_metadata_ranges(path) == [(header_size + 8, 16), (offset, 4)]
    expected = bytearray(image)
    expected[header_size + 8:header_size + 24] = bytes(16)
    expected[offset:offset + 4] = bytes(4)
    assert macho.normalized_bytes(path) == bytes(expected)


@pytest.mark.parametrize(("offset", "size", "tail"), [(71, 5, b"SIGN"), (72, 0, b""), (72, 3, b"SIGN"), (72, 5, b"SIGN")])
def test_signature_range_refuses_overlap_empty_and_non_tail_data(
    tmp_path: Path, offset: int, size: int, tail: bytes,
) -> None:
    path = tmp_path / "metadata"
    path.write_bytes(literal_macho([
        struct.pack("<II", 0x1b, 24) + bytes(16),
        struct.pack("<IIII", 0x1d, 16, offset, size),
    ], tail=tail))
    with pytest.raises(macho.MachOError):
        macho.signature_metadata_ranges(path)


@pytest.mark.parametrize(("kind", "size"), [(0x1b, 0), (0x1b, 8), (0x1b, 24), (0x1d, 0), (0x1d, 4), (0x1d, 16)])
def test_signature_commands_require_exact_payload_sizes(tmp_path: Path, kind: int, size: int) -> None:
    path = tmp_path / "metadata"
    payload = (struct.pack("<II", 40 + size, 4) + bytes(size - 8)) if size >= 8 else bytes(size)
    path.write_bytes(literal_macho([struct.pack("<II", kind, 8 + size) + payload], tail=b"SIGN"))
    with pytest.raises(macho.MachOError):
        macho.signature_metadata_ranges(path)


def test_duplicate_signature_is_rejected_even_when_both_ranges_fit(tmp_path: Path) -> None:
    path = tmp_path / "metadata"
    command = struct.pack("<IIII", 0x1d, 16, 64, 4)
    path.write_bytes(literal_macho([command, command], tail=b"SIGN"))
    with pytest.raises(macho.MachOError):
        macho.signature_metadata_ranges(path)


def test_normalization_refuses_metadata_that_outgrows_read_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "changing-image"
    path.write_bytes(literal_macho([], tail=bytes(20)))
    read_ranges = macho.signature_metadata_ranges

    def changed_ranges(image: Path) -> list[tuple[int, int]]:
        image.write_bytes(literal_macho([struct.pack("<IIII", 0x1d, 16, 53, 8)], tail=b"CODE!SIGNHERE"))
        return read_ranges(image)

    monkeypatch.setattr(macho, "signature_metadata_ranges", changed_ranges)
    with pytest.raises(macho.MachOError):
        macho.normalized_bytes(path)
