"""ELF inspection for build outputs.

Parses 64-bit little-endian ELF images directly (program headers and the dynamic
section), so hardening and linkage claims come from the bytes that ship rather
than from the text output of a tool. Other ELF classes and byte orders are
rejected.
"""

from __future__ import annotations

import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

ET_NONE, ET_REL, ET_EXEC, ET_DYN = 0, 1, 2, 3
EM_X86_64, EM_AARCH64 = 62, 183
MACHINE_NAMES = {EM_X86_64: "x86_64", EM_AARCH64: "aarch64"}

PT_NULL, PT_LOAD, PT_DYNAMIC, PT_INTERP, PT_NOTE = 0, 1, 2, 3, 4
PT_GNU_EH_FRAME, PT_GNU_STACK, PT_GNU_RELRO = 0x6474E550, 0x6474E551, 0x6474E552

DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ = 0, 1, 5, 10
DT_RPATH, DT_RUNPATH, DT_FLAGS = 15, 29, 30
DT_FLAGS_1 = 0x6FFFFFFB
DF_BIND_NOW, DF_1_NOW = 0x8, 0x1

PF_X, PF_W, PF_R = 0x1, 0x2, 0x4

NT_GNU_BUILD_ID = 3

_EHDR_SIZE = 64
_PHDR_SIZE = 56


class ELFError(Exception):
    """A file could not be read as a supported ELF image."""


@dataclass(frozen=True)
class ELFHeader:
    path: Path
    elf_class: int
    etype: int
    machine: int
    phoff: int
    phnum: int

    @property
    def arch(self) -> str:
        return MACHINE_NAMES.get(self.machine, f"machine-{self.machine}")

    @property
    def kind(self) -> str:
        if self.etype == ET_DYN:
            return "pie-executable"
        if self.etype == ET_EXEC:
            return "executable"
        if self.etype == ET_REL:
            return "relocatable"
        return f"type-{self.etype}"

    @property
    def pie(self) -> bool:
        return self.etype == ET_DYN

    @property
    def is_64(self) -> bool:
        return self.elf_class == 2


@dataclass(frozen=True)
class ProgramHeader:
    ptype: int
    flags: int
    offset: int
    vaddr: int
    filesz: int
    memsz: int


def is_elf(path: Path) -> bool:
    """True when the file starts with the ELF magic."""
    try:
        with Path(path).open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def _read_at(handle: BinaryIO, path: Path, offset: int, size: int, what: str) -> bytes:
    if offset < 0 or size < 0:
        raise ELFError(f"{path}: invalid {what} range")
    handle.seek(offset)
    data = handle.read(size)
    if len(data) < size:
        raise ELFError(f"{path}: truncated {what}")
    return data


def _read_header(path: Path) -> tuple[ELFHeader, list[ProgramHeader]]:
    path = Path(path)
    with path.open("rb") as handle:
        raw = handle.read(_EHDR_SIZE)
        if len(raw) < _EHDR_SIZE or raw[:4] != b"\x7fELF":
            raise ELFError(f"{path}: not an ELF file")
        elf_class, data, version = raw[4], raw[5], raw[6]
        if elf_class != 2:
            raise ELFError(f"{path}: only 64-bit ELF is supported (class {elf_class})")
        if data != 1:
            raise ELFError(f"{path}: only little-endian ELF is supported")
        if version != 1:
            raise ELFError(f"{path}: bad ELF version {version}")
        (etype, machine, _ever, _entry, phoff, _shoff, _flags, _ehsize,
         phentsize, phnum, _shentsize, _shnum, _shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", raw, 16)
        if phnum and phentsize != _PHDR_SIZE:
            raise ELFError(f"{path}: unexpected program header size {phentsize}")
        table = _read_at(handle, path, phoff, phnum * _PHDR_SIZE, "program headers") if phnum else b""
    headers = [
        ProgramHeader(ptype, flags, offset, vaddr, filesz, memsz)
        for ptype, flags, offset, vaddr, _paddr, filesz, memsz, _align in struct.iter_unpack("<IIQQQQQQ", table)
    ]
    return ELFHeader(path, elf_class, etype, machine, phoff, phnum), headers


def read_header(path: Path) -> ELFHeader:
    """Parse the ELF header, rejecting anything but 64-bit little-endian images."""
    return _read_header(path)[0]


def program_headers(path: Path) -> list[ProgramHeader]:
    """Return every program header in file order."""
    return _read_header(path)[1]


def _segment_bytes(path: Path, header: ProgramHeader) -> bytes:
    with Path(path).open("rb") as handle:
        return _read_at(handle, Path(path), header.offset, header.filesz, "segment")


def interpreter(path: Path) -> str | None:
    """The PT_INTERP path, or None when the image has no interpreter segment."""
    for header in program_headers(path):
        if header.ptype == PT_INTERP:
            return _segment_bytes(path, header).split(b"\x00")[0].decode("utf-8", "replace")
    return None


def _map_va(headers: list[ProgramHeader], addr: int, size: int) -> tuple[int, int] | None:
    for header in headers:
        if header.ptype != PT_LOAD:
            continue
        if header.vaddr <= addr and addr + size <= header.vaddr + header.filesz:
            return header.offset + (addr - header.vaddr), size
    return None


def _dynamic_entries(path: Path) -> tuple[list[tuple[int, int]], bytes]:
    path = Path(path)
    _, headers = _read_header(path)
    dynamic = next((h for h in headers if h.ptype == PT_DYNAMIC), None)
    if dynamic is None:
        return [], b""
    with path.open("rb") as handle:
        blob = _read_at(handle, path, dynamic.offset, dynamic.filesz, "dynamic section")
        entries: list[tuple[int, int]] = []
        for offset in range(0, len(blob) - 15, 16):
            tag, val = struct.unpack_from("<qQ", blob, offset)
            entries.append((tag, val))
            if tag == DT_NULL:
                break
        strtab = next((v for t, v in entries if t == DT_STRTAB), 0)
        strsz = next((v for t, v in entries if t == DT_STRSZ), 0)
        if not strtab or not strsz:
            return entries, b""
        mapped = _map_va(headers, strtab, strsz)
        if mapped is None:
            raise ELFError(f"{path}: STRTAB {strtab:#x} is not mapped by any LOAD segment")
        return entries, _read_at(handle, path, mapped[0], strsz, "string table")


def _dyn_string(table: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(table):
        raise ELFError(f"dynamic string offset {offset} outside strtab")
    end = table.find(b"\x00", offset)
    raw = table[offset:] if end == -1 else table[offset:end]
    return raw.decode("utf-8", "replace")


def dependencies(path: Path) -> list[str]:
    """Every DT_NEEDED library name, in file order."""
    entries, table = _dynamic_entries(path)
    return [_dyn_string(table, val) for tag, val in entries if tag == DT_NEEDED]


def rpaths(path: Path) -> list[str]:
    """Every DT_RPATH and DT_RUNPATH value, in file order."""
    entries, table = _dynamic_entries(path)
    return [_dyn_string(table, val) for tag, val in entries if tag in (DT_RPATH, DT_RUNPATH)]


def has_relro(path: Path) -> bool:
    """True when the image has a PT_GNU_RELRO segment."""
    return any(h.ptype == PT_GNU_RELRO for h in program_headers(path))


def stack_nx(path: Path) -> bool:
    """True when PT_GNU_STACK exists and does not request an executable stack."""
    for header in program_headers(path):
        if header.ptype == PT_GNU_STACK:
            return not bool(header.flags & PF_X)
    return False


def has_bind_now(path: Path) -> bool:
    """True when DT_FLAGS or DT_FLAGS_1 requests eager binding."""
    entries, _ = _dynamic_entries(path)
    for tag, val in entries:
        if tag == DT_FLAGS and val & DF_BIND_NOW:
            return True
        if tag == DT_FLAGS_1 and val & DF_1_NOW:
            return True
    return False


def inspect(path: Path) -> dict:
    """A report-ready summary of one ELF image."""
    header = read_header(path)
    return {
        "arch": header.arch,
        "kind": header.kind,
        "is_64": header.is_64,
        "pie": header.pie,
        "interpreter": interpreter(path),
        "dependencies": dependencies(path),
        "rpaths": rpaths(path),
        "relro": has_relro(path),
        "bind_now": has_bind_now(path),
        "stack_nx": stack_nx(path),
    }


def hardening_violations(path: Path, *, machine: int | None = None) -> list[str]:
    """List the ways an image falls short of the release hardening baseline.

    The baseline is a PIE (ET_DYN) image with a non-executable PT_GNU_STACK, a
    PT_GNU_RELRO segment, eager binding, and no writable-and-executable LOAD
    segment. When `machine` (an `EM_*` value, see `targets.Target.elf_machine`)
    is given, the image must also be built for it. An empty list means the
    image passes. Interpreter, needed-library, and RPATH policy differ per
    repository and are left to the caller.
    """
    header, headers = _read_header(path)
    problems = []
    if machine is not None and header.machine != machine:
        problems.append(f"machine is {header.arch}, expected {MACHINE_NAMES.get(machine, machine)}")
    if not header.pie:
        problems.append(f"image is {header.kind}, not a PIE")
    stack = next((h for h in headers if h.ptype == PT_GNU_STACK), None)
    if stack is None:
        problems.append("missing PT_GNU_STACK")
    elif stack.flags & PF_X:
        problems.append("executable stack requested")
    if not any(h.ptype == PT_GNU_RELRO for h in headers):
        problems.append("missing PT_GNU_RELRO")
    if not has_bind_now(path):
        problems.append("lazy binding: neither DF_BIND_NOW nor DF_1_NOW is set")
    if any(h.ptype == PT_LOAD and h.flags & PF_W and h.flags & PF_X for h in headers):
        problems.append("writable and executable LOAD segment")
    return problems


def build_id_ranges(path: Path) -> list[tuple[int, int]]:
    """File ranges `(offset, size)` holding GNU build-id bytes."""
    path = Path(path)
    _, headers = _read_header(path)
    ranges: list[tuple[int, int]] = []
    with path.open("rb") as handle:
        for header in headers:
            if header.ptype != PT_NOTE:
                continue
            blob = _read_at(handle, path, header.offset, header.filesz, "note segment")
            cursor = 0
            while cursor + 12 <= len(blob):
                namesz, descsz, note_type = struct.unpack_from("<III", blob, cursor)
                name_start = cursor + 12
                name_end = name_start + ((namesz + 3) & ~3)
                desc_start = name_end
                desc_end = desc_start + ((descsz + 3) & ~3)
                if desc_end > len(blob):
                    break
                name = blob[name_start:name_start + namesz]
                if note_type == NT_GNU_BUILD_ID and name.rstrip(b"\x00") == b"GNU":
                    ranges.append((header.offset + desc_start, descsz))
                cursor = desc_end
    return ranges


def normalized_bytes(path: Path) -> bytes:
    """The image with its GNU build-id bytes zeroed."""
    raw = bytearray(Path(path).read_bytes())
    for offset, size in build_id_ranges(Path(path)):
        if 0 <= offset < len(raw):
            end = min(offset + size, len(raw))
            raw[offset:end] = b"\x00" * (end - offset)
    return bytes(raw)


def find_elfs(root: Path) -> list[Path]:
    """Real (non-symlink) ELF images under a tree, sorted."""
    found = []
    directories = [Path(root)]
    while directories:
        for path in directories.pop().iterdir():
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                directories.append(path)
            elif stat.S_ISREG(mode):
                with path.open("rb") as handle:
                    magic = handle.read(4)
                if magic == b"\x7fELF":
                    found.append(path)
    return sorted(found)
