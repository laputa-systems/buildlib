"""Builders for minimal Mach-O and ELF images used as test fixtures."""

from __future__ import annotations

import struct

from buildlib import elf, macho


def _pad(data: bytes, multiple: int) -> bytes:
    return data + b"\x00" * (-len(data) % multiple)


def macho_command(cmd: int, payload: bytes) -> bytes:
    body = _pad(payload, 8)
    return struct.pack("<II", cmd, 8 + len(body)) + body


def macho_string_command(cmd: int, text: str, *, prefix: bytes = b"") -> bytes:
    offset = 8 + 4 + len(prefix)
    return macho_command(cmd, struct.pack("<I", offset) + prefix + text.encode() + b"\x00")


def dylib_command(cmd: int, name: str) -> bytes:
    return macho_string_command(cmd, name, prefix=struct.pack("<III", 2, 0x10000, 0x10000))


def build_version_command(platform: int, minos: int, sdk: int) -> bytes:
    return macho_command(macho.LC_BUILD_VERSION, struct.pack("<IIII", platform, minos, sdk, 0))


def uuid_command(value: bytes) -> bytes:
    return macho_command(macho.LC_UUID, value)


def signature_command(offset: int, size: int) -> bytes:
    return macho_command(macho.LC_CODE_SIGNATURE, struct.pack("<II", offset, size))


def macho_image(
    commands: list[bytes],
    *,
    cputype: int = macho.CPU_TYPE_ARM64,
    filetype: int = macho.MH_EXECUTE,
    flags: int = macho.MH_PIE,
    trailer: bytes = b"",
) -> bytes:
    body = b"".join(commands)
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, cputype, 0, filetype, len(commands), len(body), flags, 0)
    return header + body + trailer


def version(major: int, minor: int, patch: int = 0) -> int:
    return (major << 16) | (minor << 8) | patch


def elf_image(
    *,
    machine: int = elf.EM_X86_64,
    etype: int = elf.ET_DYN,
    interp: str | None = "/lib/ld-musl-x86_64.so.1",
    needed: tuple[str, ...] = (),
    runpath: str | None = None,
    bind_now: bool = True,
    relro: bool = True,
    stack_flags: int | None = elf.PF_R | elf.PF_W,
    build_id: bytes | None = None,
    load_flags: int = elf.PF_R | elf.PF_X,
) -> bytes:
    strtab = b"\x00"
    offsets: dict[str, int] = {}
    for name in (*needed, *([runpath] if runpath else [])):
        offsets[name] = len(strtab)
        strtab += name.encode() + b"\x00"

    dynamic_entries = [(elf.DT_NEEDED, offsets[name]) for name in needed]
    if runpath:
        dynamic_entries.append((elf.DT_RUNPATH, offsets[runpath]))
    if bind_now:
        dynamic_entries.append((elf.DT_FLAGS, elf.DF_BIND_NOW))

    note = b""
    if build_id is not None:
        note = struct.pack("<III", 4, len(build_id), elf.NT_GNU_BUILD_ID) + b"GNU\x00" + _pad(build_id, 4)

    kinds = [elf.PT_LOAD, elf.PT_DYNAMIC]
    if interp is not None:
        kinds.append(elf.PT_INTERP)
    if stack_flags is not None:
        kinds.append(elf.PT_GNU_STACK)
    if relro:
        kinds.append(elf.PT_GNU_RELRO)
    if note:
        kinds.append(elf.PT_NOTE)

    cursor = 64 + 56 * len(kinds)
    interp_bytes = interp.encode() + b"\x00" if interp is not None else b""
    interp_at = cursor
    cursor += len(interp_bytes)
    strtab_at = cursor
    cursor += len(strtab)
    cursor += -cursor % 8
    dynamic_at = cursor
    dynamic_entries += [(elf.DT_STRTAB, strtab_at), (elf.DT_STRSZ, len(strtab)), (elf.DT_NULL, 0)]
    dynamic = b"".join(struct.pack("<qQ", tag, value) for tag, value in dynamic_entries)
    cursor += len(dynamic)
    cursor += -cursor % 4
    note_at = cursor
    total = note_at + len(note)

    segments = {
        elf.PT_LOAD: (load_flags, 0, total),
        elf.PT_DYNAMIC: (elf.PF_R | elf.PF_W, dynamic_at, len(dynamic)),
        elf.PT_INTERP: (elf.PF_R, interp_at, len(interp_bytes)),
        elf.PT_GNU_STACK: (stack_flags or 0, 0, 0),
        elf.PT_GNU_RELRO: (elf.PF_R, dynamic_at, len(dynamic)),
        elf.PT_NOTE: (elf.PF_R, note_at, len(note)),
    }
    program_headers = b"".join(
        struct.pack("<IIQQQQQQ", kind, segments[kind][0], segments[kind][1], segments[kind][1], 0,
                    segments[kind][2], segments[kind][2], 8)
        for kind in kinds
    )
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    header = ident + struct.pack("<HHIQQQIHHHHHH", etype, machine, 1, 0, 64, 0, 0, 64, 56, len(kinds), 0, 0, 0)
    image = bytearray(header + program_headers)
    image += interp_bytes + strtab
    image += b"\x00" * (dynamic_at - len(image))
    image += dynamic
    image += b"\x00" * (note_at - len(image))
    image += note
    assert len(image) == total
    return bytes(image)
