from __future__ import annotations

import errno
import os
import select
import signal
import struct
import time
from pathlib import Path

import pytest

from binaries import elf_image
from buildlib import elf, targets


def write(tmp_path: Path, name: str, **options) -> Path:
    path = tmp_path / name
    path.write_bytes(elf_image(**options))
    return path


def test_header_and_program_headers_preserve_unsigned_fields_and_order(tmp_path: Path) -> None:
    path = tmp_path / "independent"
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    header = struct.pack("<HHIQQQIHHHHHH", 0xFF01, 0xFF02, 1, 0, 80, 0, 0, 64, 56, 2, 0, 0, 0)
    first = struct.pack("<IIQQQQQQ", 0x80000001, 0x80000002, 0x8000000000000003,
                        0x8000000000000004, 5, 6, 0x8000000000000007, 8)
    second = struct.pack("<IIQQQQQQ", 4, 5, 6, 7, 8, 9, 10, 11)
    path.write_bytes(ident + header + bytes(16) + first + second)

    result = elf.read_header(path)
    assert result == elf.ELFHeader(path, 0xFF01, 0xFF02, 80, 2, (0x80000001, 4), ())
    assert (result.kind, result.arch, result.pie) == ("type-65281", "machine-65282", False)
    assert elf.program_headers(path) == [
        elf.ProgramHeader(0x80000001, 0x80000002, 0x8000000000000003, 0x8000000000000004,
                          6, 0x8000000000000007),
        elf.ProgramHeader(4, 5, 6, 7, 9, 10),
    ]


def test_empty_program_table_does_not_read_its_offset(tmp_path: Path) -> None:
    path = tmp_path / "no-program-headers"
    path.write_bytes(
        b"\x7fELF\x02\x01\x01" + bytes(9)
        + struct.pack("<HHIQQQIHHHHHH", 1, 62, 1, 0, 0xFFFFFFFFFFFFFFFF, 0, 0, 64, 0, 0, 0, 0, 0)
    )
    assert elf.read_header(path) == elf.ELFHeader(path, 1, 62, 0xFFFFFFFFFFFFFFFF, 0, (), ())
    assert elf.program_headers(path) == []


def test_header_from_fifo_finishes_before_writer_closes(tmp_path: Path) -> None:
    image = (b"\x7fELF\x02\x01\x01" + bytes(9)
             + struct.pack("<HHIQQQIHHHHHH", 1, 62, 1, 0, 0, 0, 0, 64, 0, 0, 0, 0, 0))
    regular = tmp_path / "regular"
    regular.write_bytes(image)
    assert elf.read_header(regular) == elf.ELFHeader(regular, 1, 62, 0, 0, (), ())

    fifo = tmp_path / "image.fifo"
    os.mkfifo(fifo)
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            os.write(write_fd, b"R")
            result = elf.read_header(fifo)
            message = b"1" if result == elf.ELFHeader(fifo, 1, 62, 0, 0, (), ()) else b"wrong header"
            os.write(write_fd, message)
        except BaseException as exc:
            os.write(write_fd, repr(exc).encode())
        finally:
            os.close(write_fd)
            os._exit(0)

    os.close(write_fd)
    producer = None
    deadline = time.monotonic() + 3
    try:
        ready, _, _ = select.select([read_fd], [], [], max(0, deadline - time.monotonic()))
        assert ready, "consumer did not start"
        assert os.read(read_fd, 1) == b"R", "consumer did not send its marker"
        while producer is None and time.monotonic() < deadline:
            try:
                producer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as exc:
                if exc.errno != errno.ENXIO:
                    raise
                select.select([], [], [], min(0.01, max(0, deadline - time.monotonic())))
        assert producer is not None, "consumer did not open FIFO"
        assert os.write(producer, image) == len(image)
        ready, _, _ = select.select([read_fd], [], [], max(0, deadline - time.monotonic()))
        assert ready, "consumer waited for writer EOF"
        assert os.read(read_fd, 256) == b"1", "consumer returned an incorrect header"
    finally:
        if producer is not None:
            os.close(producer)
        os.close(read_fd)
        pid, _ = os.waitpid(child, os.WNOHANG)
        if not pid:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)


def test_public_header_readers_reject_independent_malformed_images(tmp_path: Path) -> None:
    valid = (b"\x7fELF\x02\x01\x01" + bytes(9)
             + struct.pack("<HHIQQQIHHHHHH", 1, 62, 1, 0, 64, 0, 0, 64, 56, 0, 0, 0, 0))
    cases = {
        "short": valid[:-1],
        "magic": b"BAD!" + valid[4:],
        "class": valid[:4] + b"\x01" + valid[5:],
        "byte-order": valid[:5] + b"\x02" + valid[6:],
        "version": valid[:6] + b"\x02" + valid[7:],
        "entry-size": valid[:54] + b"\x20\x00\x01\x00" + valid[58:],
        "truncated-table": valid[:56] + b"\x01\x00" + valid[58:],
    }
    for name, image in cases.items():
        path = tmp_path / name
        path.write_bytes(image)
        for reader in (elf.read_header, elf.program_headers):
            with pytest.raises(elf.ELFError):
                reader(path)


def test_inspect_reports_a_hardened_musl_executable(tmp_path: Path) -> None:
    path = write(tmp_path, "tool", needed=("libc.musl-x86_64.so.1",), runpath="$ORIGIN/../lib")
    assert elf.inspect(path) == {
        "arch": "x86_64",
        "kind": "pie-executable",
        "pie": True,
        "interpreter": "/lib/ld-musl-x86_64.so.1",
        "dependencies": ["libc.musl-x86_64.so.1"],
        "rpaths": ["$ORIGIN/../lib"],
        "relro": True,
        "bind_now": True,
        "stack_nx": True,
    }


def test_static_image_has_no_interpreter_or_dependencies(tmp_path: Path) -> None:
    path = write(tmp_path, "static", interp=None, flags_1=0x08000000)
    assert elf.interpreter(path) is None
    assert elf.dependencies(path) == []
    assert elf.rpaths(path) == []


@pytest.mark.parametrize(
    ("name", "interp", "flags_1", "kind", "pie"),
    [
        ("shared", None, 0, "shared-library", False),
        ("dynamic-pie", "/lib/ld.so", 0, "pie-executable", True),
        ("static-pie", None, 0x08000000, "pie-executable", True),
    ],
)
def test_dynamic_image_kind(tmp_path: Path, name: str, interp: str | None, flags_1: int, kind: str, pie: bool) -> None:
    path = write(tmp_path, name, interp=interp, flags_1=flags_1)
    header = elf.read_header(path)
    assert (header.kind, header.pie) == (kind, pie)
    report = elf.inspect(path)
    assert (report["kind"], report["pie"]) == (kind, pie)
    if pie:
        assert elf.hardening_violations(path) == []


def test_shared_library_hardening_policy(tmp_path: Path) -> None:
    path = write(tmp_path, "library", interp=None)
    assert elf.hardening_violations(path) == ["image is shared-library, not a PIE"]
    assert elf.hardening_violations(path, require_pie=False) == []
    weak = write(tmp_path, "weak-library", interp=None, relro=False)
    assert elf.hardening_violations(weak, require_pie=False) == ["missing PT_GNU_RELRO"]


def test_header_rejects_32_bit_images(tmp_path: Path) -> None:
    raw = bytearray(elf_image())
    raw[4] = 1
    path = tmp_path / "elf32"
    path.write_bytes(bytes(raw))
    with pytest.raises(elf.ELFError, match="only 64-bit ELF is supported"):
        elf.read_header(path)
    with pytest.raises(elf.ELFError, match="only 64-bit ELF is supported"):
        elf.inspect(path)


def test_header_decodes_machine_and_type(tmp_path: Path) -> None:
    header = elf.read_header(write(tmp_path, "arm", machine=elf.EM_AARCH64, etype=elf.ET_EXEC))
    assert (header.arch, header.kind, header.pie) == ("aarch64", "executable", False)


def test_hardening_passes_for_the_baseline_on_every_linux_target(tmp_path: Path) -> None:
    for target in targets.TARGETS.values():
        if target.elf_machine is None:
            continue
        path = write(tmp_path, target.triple, machine=target.elf_machine)
        assert elf.hardening_violations(path, machine=target.elf_machine) == []


def test_hardening_reports_every_missing_property(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "weak",
        machine=elf.EM_AARCH64,
        etype=elf.ET_EXEC,
        bind_now=False,
        relro=False,
        stack_flags=elf.PF_R | elf.PF_W | elf.PF_X,
        load_flags=elf.PF_R | elf.PF_W | elf.PF_X,
    )
    problems = elf.hardening_violations(path, machine=elf.EM_X86_64)
    assert len(problems) == 6
    joined = "\n".join(problems)
    for expected in ("expected x86_64", "not a PIE", "executable stack", "PT_GNU_RELRO", "lazy binding", "writable and executable"):
        assert expected in joined


def test_only_a_writable_and_executable_load_segment_is_flagged(tmp_path: Path) -> None:
    data = write(tmp_path, "data", load_flags=elf.PF_R | elf.PF_W)
    assert elf.hardening_violations(data) == []
    stack = write(tmp_path, "stack", stack_flags=elf.PF_R | elf.PF_W | elf.PF_X)
    assert elf.hardening_violations(stack) == ["executable stack requested"]
    load = write(tmp_path, "load", load_flags=elf.PF_R | elf.PF_W | elf.PF_X)
    assert elf.hardening_violations(load) == ["writable and executable LOAD segment"]


def test_missing_gnu_stack_is_a_violation(tmp_path: Path) -> None:
    path = write(tmp_path, "nostack", stack_flags=None)
    assert elf.hardening_violations(path) == ["missing PT_GNU_STACK"]
    assert not elf.stack_nx(path)


def test_machine_is_only_checked_when_requested(tmp_path: Path) -> None:
    path = write(tmp_path, "arm", machine=elf.EM_AARCH64)
    assert elf.hardening_violations(path) == []


def test_build_id_is_zeroed_for_reproducibility_comparison(tmp_path: Path) -> None:
    first = write(tmp_path, "a", build_id=bytes(range(20)))
    second = write(tmp_path, "b", build_id=bytes(range(100, 120)))
    assert first.read_bytes() != second.read_bytes()
    assert elf.normalized_bytes(first) == elf.normalized_bytes(second)
    [(offset, size)] = elf.build_id_ranges(first)
    assert (size, first.read_bytes()[offset:offset + size]) == (20, bytes(range(20)))


def test_find_elfs_skips_symlinks_and_other_files(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    real = write(tmp_path / "bin", "tool")
    (tmp_path / "bin" / "alias").symlink_to(real.name)
    (tmp_path / "notes.txt").write_text("not an image")
    assert elf.find_elfs(tmp_path) == [real]


def test_rejects_unsupported_or_malformed_images(tmp_path: Path) -> None:
    good = elf_image()
    cases = {
        "text": b"hello world, not an elf image at all, padding padding padding padding padding!!",
        "elf32": good[:4] + b"\x01" + good[5:],
        "big-endian": good[:5] + b"\x02" + good[6:],
        "short-headers": good[:100],
        "bad-phentsize": good[:54] + (32).to_bytes(2, "little") + good[56:],
    }
    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        with pytest.raises(elf.ELFError):
            elf.program_headers(path)


def test_unmapped_string_table_is_an_error(tmp_path: Path) -> None:
    data = bytearray(elf_image(needed=("libx.so",)))
    path = tmp_path / "broken"
    marker = (elf.DT_STRTAB).to_bytes(8, "little", signed=True)
    at = bytes(data).index(marker, 64 + 56 * 5)
    data[at + 8:at + 16] = (0x10_0000).to_bytes(8, "little")
    path.write_bytes(bytes(data))
    with pytest.raises(elf.ELFError, match="not mapped"):
        elf.dependencies(path)


@pytest.mark.parametrize("unreadable", ["directory", "file"])
def test_find_elfs_propagates_permission_errors(tmp_path: Path, unreadable: str) -> None:
    directory = tmp_path / "nested"
    directory.mkdir()
    image = directory / "image"
    image.write_bytes(elf_image())
    other = tmp_path / "a-image"
    other.write_bytes(image.read_bytes())
    (tmp_path / "linked-directory").symlink_to(directory, target_is_directory=True)
    assert elf.find_elfs(tmp_path) == [other, image]
    target = directory if unreadable == "directory" else image
    mode = target.stat().st_mode
    target.chmod(0)
    try:
        with pytest.raises(PermissionError):
            elf.find_elfs(tmp_path)
    finally:
        target.chmod(mode)
    assert elf.find_elfs(tmp_path) == [other, image]


def independent_image(segments: list[tuple[int, int, int, int, bytes]]) -> bytes:
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    header = struct.pack("<HHIQQQIHHHHHH", 3, 62, 1, 0, 64, 0, 0, 64, 56, len(segments), 0, 0, 0)
    phdrs = b"".join(struct.pack("<IIQQQQQQ", kind, flags, offset, address, 0, len(data), len(data), 1)
                     for kind, flags, offset, address, data in segments)
    image = bytearray(ident + header + phdrs)
    for _, _, offset, _, data in segments:
        if len(image) < offset + len(data):
            image.extend(bytes(offset + len(data) - len(image)))
        image[offset:offset + len(data)] = data
    return bytes(image)


def test_magic_reader_handles_files_and_missing_paths(tmp_path: Path) -> None:
    image = tmp_path / "image"
    image.write_bytes(b"\x7fELFmore")
    assert elf.is_elf(image)
    image.write_bytes(b"\x7fEL")
    assert not elf.is_elf(image)
    assert not elf.is_elf(tmp_path / "missing")


def test_dynamic_strings_mapping_and_entry_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "dynamic"
    table = b"\x00lib\xff.so\x00first\x00last"
    entries = [(1, 1), (15, 9), (29, 15), (1, 0), (5, 0x4000), (10, len(table)), (0, 0), (1, 999)]
    dynamic = b"".join(struct.pack("<qQ", tag, value) for tag, value in entries)
    path.write_bytes(independent_image([(2, 0, 512, 0, dynamic), (1, 4, 768, 0x4000, table)]))
    assert elf.dependencies(path) == ["lib\ufffd.so", ""]
    assert elf.rpaths(path) == ["first", "last"]

    for addr, size in [(0x3fff, len(table)), (0x4000, len(table) + 1)]:
        broken = [(1, 1), (5, addr), (10, size), (0, 0)]
        data = b"".join(struct.pack("<qQ", tag, value) for tag, value in broken)
        path.write_bytes(independent_image([(2, 0, 512, 0, data), (1, 4, 768, 0x4000, table)]))
        with pytest.raises(elf.ELFError):
            elf.dependencies(path)

    for offset in (len(table), 0xffffffffffffffff):
        broken = [(1, offset), (5, 0x4000), (10, len(table)), (0, 0)]
        data = b"".join(struct.pack("<qQ", tag, value) for tag, value in broken)
        path.write_bytes(independent_image([(2, 0, 512, 0, data), (1, 4, 768, 0x4000, table)]))
        with pytest.raises(elf.ELFError):
            elf.dependencies(path)

    for entries in ([(1, 0), (10, 3)], [(1, 0), (5, 0x4000)]):
        data = b"".join(struct.pack("<qQ", tag, value) for tag, value in entries)
        path.write_bytes(independent_image([(2, 0, 512, 0, data), (1, 4, 768, 0x4000, table)]))
        with pytest.raises(elf.ELFError):
            elf.dependencies(path)

    path.write_bytes(independent_image([(2, 0, 512, 0, struct.pack("<qQ", 0x6ffffffb, 0x8000000008000001) + b"tail")]))
    assert elf.read_header(path).dynamic_flags_1 == (0x8000000008000001,)
    assert elf.has_bind_now(path)
    path.write_bytes(independent_image([(2, 0, 512, 0, struct.pack("<qQ", 0x6ffffffb, 1))]))
    assert elf.has_bind_now(path)

    data = b"".join(struct.pack("<qQ", tag, value) for tag, value in [(1, 0), (10, 1)])
    path.write_bytes(independent_image([(2, 0, 512, 0, data), (1, 4, 768, 0, b"X")]))
    with pytest.raises(elf.ELFError):
        elf.dependencies(path)

    data = b"".join(struct.pack("<qQ", tag, value) for tag, value in [(1, 0), (10, 1)])
    path.write_bytes(independent_image([(2, 0, 512, 0, data), (1, 4, 768, 1, b"X")]))
    with pytest.raises(elf.ELFError):
        elf.dependencies(path)

    data = b"".join(struct.pack("<qQ", tag, value) for tag, value in [(1, 0), (5, 0x4000), (10, 2)])
    path.write_bytes(independent_image([(2, 0, 512, 0, data), (1, 4, 768, 0x4000, b"X"),
                                        (0, 0, 769, 0, b"Y")]))
    with pytest.raises(elf.ELFError):
        elf.dependencies(path)


def test_interpreter_and_hardening_flags(tmp_path: Path) -> None:
    path = tmp_path / "flags"
    dynamic = struct.pack("<qQqQ", 30, 0, 0x6ffffffb, 0)
    image = independent_image([(3, 4, 512, 0, b"/ld\xff\x00ignored"), (2, 0, 544, 0, dynamic),
                               (0x6474e551, 1, 600, 0, b"")])
    path.write_bytes(image)
    assert elf.interpreter(path) == "/ld\ufffd"
    assert not elf.stack_nx(path)
    assert not elf.has_relro(path)
    assert not elf.has_bind_now(path)
    assert elf.hardening_violations(path, machine=999)[0] == "machine is x86_64, expected 999"


def test_note_ranges_preserve_padding_and_ignore_other_notes(tmp_path: Path) -> None:
    path = tmp_path / "notes"
    notes = (struct.pack("<III", 4, 3, 3) + b"GNU\x00" + b"abcP"
             + struct.pack("<III", 4, 2, 1) + b"GNU\x00" + b"deQQ"
             + struct.pack("<III", 4, 1, 3) + b"XYZ\x00" + b"fRRR"
             + struct.pack("<III", 4, 5, 3) + b"GNU\x00" + b"12345SSS"
             + struct.pack("<III", 4, 1, 3) + b"GNUX" + b"zTTT"
             + struct.pack("<III", 0xffffffff, 0, 3) + b"short")
    data = independent_image([(4, 4, 512, 0, notes)])
    path.write_bytes(data)
    assert elf.build_id_ranges(path) == [(528, 3), (588, 5)]
    expected = bytearray(data)
    expected[528:531] = bytes(3)
    expected[588:593] = bytes(5)
    assert elf.normalized_bytes(path) == bytes(expected)
    path.write_bytes(independent_image([(4, 4, 512, 0, bytes(12))]))
    assert elf.build_id_ranges(path) == []
    path.write_bytes(independent_image([(4, 4, 512, 0, struct.pack("<III", 4, 0xffffffff, 3) + b"GNU\x00")]))
    assert elf.build_id_ranges(path) == []


def test_empty_segments_and_zero_offset_are_readable(tmp_path: Path) -> None:
    path = tmp_path / "empty"
    path.write_bytes(independent_image([(3, 4, 0, 0, b""), (2, 0, 0, 0, b""), (4, 0, 0, 0, b"")]))
    assert elf.interpreter(path) == ""
    assert elf.build_id_ranges(path) == []
    assert elf.dependencies(path) == []
