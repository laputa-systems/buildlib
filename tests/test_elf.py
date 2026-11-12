from __future__ import annotations

from pathlib import Path

import pytest

from binaries import elf_image
from buildlib import elf, targets


def write(tmp_path: Path, name: str, **options) -> Path:
    path = tmp_path / name
    path.write_bytes(elf_image(**options))
    return path


def test_inspect_reports_a_hardened_musl_executable(tmp_path: Path) -> None:
    path = write(tmp_path, "tool", needed=("libc.musl-x86_64.so.1",), runpath="$ORIGIN/../lib")
    assert elf.inspect(path) == {
        "arch": "x86_64",
        "kind": "pie-executable",
        "is_64": True,
        "pie": True,
        "interpreter": "/lib/ld-musl-x86_64.so.1",
        "dependencies": ["libc.musl-x86_64.so.1"],
        "rpaths": ["$ORIGIN/../lib"],
        "relro": True,
        "bind_now": True,
        "stack_nx": True,
    }


def test_static_image_has_no_interpreter_or_dependencies(tmp_path: Path) -> None:
    path = write(tmp_path, "static", interp=None)
    assert elf.interpreter(path) is None
    assert elf.dependencies(path) == []
    assert elf.rpaths(path) == []


def test_header_decodes_machine_and_type(tmp_path: Path) -> None:
    header = elf.read_header(write(tmp_path, "arm", machine=elf.EM_AARCH64, etype=elf.ET_EXEC))
    assert (header.arch, header.kind, header.pie, header.is_64) == ("aarch64", "executable", False, True)


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
