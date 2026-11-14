"""Mach-O inspection for build outputs.

Everything a build asserts about a shipped binary has to come from its Mach-O
header and load commands, not from a compiler banner or a string search: the
architecture actually encoded (`cputype`), the deployment floor the loader will
enforce (`LC_BUILD_VERSION.minos`), which libraries will be loaded by name, and
where the loader will search for them (`LC_RPATH`).

Load commands are parsed here directly rather than scraped from `otool` output.
Fat/universal images are rejected: build repositories ship a single thin slice.
"""

from __future__ import annotations

import stat
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

_MAGIC_32 = {b"\xce\xfa\xed\xfe": "<", b"\xfe\xed\xfa\xce": ">"}
_MAGIC_64 = {b"\xcf\xfa\xed\xfe": "<", b"\xfe\xed\xfa\xcf": ">"}
_FAT_MAGIC = frozenset({b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                        b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"})

CPU_ARCH_ABI64 = 0x01000000
CPU_TYPE_X86_64 = 7 | CPU_ARCH_ABI64
CPU_TYPE_ARM64 = 12 | CPU_ARCH_ABI64
CPU_TYPE_ARM64_32 = 12 | 0x02000000
CPU_NAMES = {
    CPU_TYPE_X86_64: "x86_64",
    CPU_TYPE_ARM64: "arm64",
    CPU_TYPE_ARM64_32: "arm64_32",
}

MH_OBJECT, MH_EXECUTE, MH_DYLIB, MH_BUNDLE = 1, 2, 6, 8
MH_PIE = 0x00200000
FILETYPE_NAMES = {
    MH_OBJECT: "object", MH_EXECUTE: "execute", MH_DYLIB: "dylib",
    MH_BUNDLE: "bundle", 0x5: "core", 0xb: "dylib_stub",
}

LC_ID_DYLIB = 0x0D
LC_LOAD_DYLIB = 0x0C
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_RPATH = 0x8000001C
LC_UUID = 0x1B
LC_CODE_SIGNATURE = 0x1D
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
LC_LOAD_UPWARD_DYLIB = 0x80000023

_DEPENDENCY_COMMANDS = frozenset(
    {LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_LOAD_UPWARD_DYLIB}
)

PLATFORM_MACOS = 1
PLATFORM_NAMES = {
    1: "macos", 2: "ios", 3: "tvos", 4: "watchos", 5: "bridgeos",
    6: "maccatalyst", 7: "ios-simulator", 8: "tvos-simulator",
    9: "watchos-simulator", 10: "driverkit", 11: "visionos",
    12: "visionos-simulator",
}

_HEADER_FMT = "IiiIIII"
_HEADER_FMT_64 = _HEADER_FMT + "I"


class MachOError(Exception):
    """A file could not be read as a Mach-O image."""


@dataclass(frozen=True)
class MachOHeader:
    path: Path
    magic: int
    cputype: int
    cpusubtype: int
    filetype: int
    flags: int
    is_64: bool

    @property
    def arch(self) -> str:
        return CPU_NAMES.get(self.cputype, f"cputype-{self.cputype}")

    @property
    def kind(self) -> str:
        return FILETYPE_NAMES.get(self.filetype, f"filetype-{self.filetype}")

    @property
    def pie(self) -> bool:
        return bool(self.flags & MH_PIE)


@dataclass(frozen=True)
class BuildVersion:
    platform: int
    minos: str
    sdk: str

    @property
    def platform_name(self) -> str:
        return PLATFORM_NAMES.get(self.platform, f"platform-{self.platform}")


def format_version(raw: int) -> str:
    """Decode a packed Mach-O version into `major.minor[.patch]`."""
    major = (raw >> 16) & 0xFFFF
    minor = (raw >> 8) & 0xFF
    patch = raw & 0xFF
    return f"{major}.{minor}.{patch}" if patch else f"{major}.{minor}"


def is_macho(path: Path) -> bool:
    """True for a thin Mach-O of either bitness; False for fat images too."""
    try:
        with Path(path).open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic in _MAGIC_32 or magic in _MAGIC_64


def _magic_of(path: Path) -> bytes:
    try:
        with Path(path).open("rb") as handle:
            magic = handle.read(4)
    except OSError as error:
        raise MachOError(f"cannot read {path}: {error}") from error
    if magic in _FAT_MAGIC:
        raise MachOError(
            f"{path}: fat/universal Mach-O is not supported; build outputs are thin images"
        )
    return magic


def _header_fields(path: Path) -> tuple[str, bool, tuple]:
    """Byte order, bitness, and the raw header integers for one image."""
    path = Path(path)
    magic = _magic_of(path)
    endian = _MAGIC_64.get(magic) or _MAGIC_32.get(magic)
    if endian is None:
        raise MachOError(f"{path}: not a Mach-O file (magic {magic.hex()})")
    is_64 = magic in _MAGIC_64
    fmt = _HEADER_FMT_64 if is_64 else _HEADER_FMT
    size = struct.calcsize(fmt)
    with path.open("rb") as handle:
        raw = handle.read(size)
    if len(raw) < size:
        raise MachOError(f"{path}: truncated Mach-O header")
    return endian, is_64, struct.unpack(f"{endian}{fmt}", raw)


def read_header(path: Path) -> MachOHeader:
    """Parse the Mach-O header, rejecting malformed or fat images."""
    path = Path(path)
    _endian, is_64, fields = _header_fields(path)
    return MachOHeader(
        path=path, magic=fields[0], cputype=fields[1], cpusubtype=fields[2],
        filetype=fields[3], flags=fields[6], is_64=is_64,
    )


@dataclass(frozen=True)
class LoadCommand:
    cmd: int
    payload: bytes
    offset: int = 0


def load_commands(path: Path) -> list[LoadCommand]:
    """Return every load command, in file order."""
    path = Path(path)
    endian, is_64, fields = _header_fields(path)
    header_size = struct.calcsize(_HEADER_FMT_64 if is_64 else _HEADER_FMT)
    ncmds, sizeofcmds = fields[4], fields[5]
    with path.open("rb") as handle:
        handle.seek(header_size)
        blob = handle.read(sizeofcmds)
    if len(blob) < sizeofcmds:
        raise MachOError(f"{path}: truncated load commands")
    commands: list[LoadCommand] = []
    offset = 0
    for _ in range(ncmds):
        if offset + 8 > len(blob):
            raise MachOError(f"{path}: load command {len(commands)} runs past the header")
        cmd, cmdsize = struct.unpack_from(f"{endian}II", blob, offset)
        if cmdsize < 8 or offset + cmdsize > len(blob):
            raise MachOError(f"{path}: malformed load command size {cmdsize}")
        commands.append(
            LoadCommand(cmd=cmd, payload=blob[offset + 8:offset + cmdsize],
                        offset=header_size + offset)
        )
        offset += cmdsize
    return commands


_LC_HEADER_SIZE = 8


def _string_at(payload: bytes, offset: int) -> str:
    """Resolve a string whose offset is measured from the load command start.

    Mach-O's `lc_str` offsets are relative to the beginning of the command
    (including its 8-byte cmd/cmdsize header), while `LoadCommand.payload`
    begins after that header — so the offset has to be rebased, not used
    directly. Reading it unrebased silently returns a truncated path, which
    would make a wrong dependency name look like a valid one.
    """
    rebased = offset - _LC_HEADER_SIZE
    if rebased < 0 or rebased >= len(payload):
        raise MachOError(f"load command string offset {offset} is outside the command")
    end = payload.find(b"\x00", rebased)
    raw = payload[rebased:] if end == -1 else payload[rebased:end]
    return raw.decode("utf-8", "replace")


def build_version(path: Path) -> BuildVersion | None:
    """The `LC_BUILD_VERSION` (or legacy `LC_VERSION_MIN_MACOSX`) floor, if present."""
    for command in load_commands(path):
        if command.cmd == LC_BUILD_VERSION:
            platform, minos, sdk = struct.unpack_from("<III", command.payload, 0)
            return BuildVersion(platform, format_version(minos), format_version(sdk))
        if command.cmd == LC_VERSION_MIN_MACOSX:
            version, sdk = struct.unpack_from("<II", command.payload, 0)
            return BuildVersion(PLATFORM_MACOS, format_version(version), format_version(sdk))
    return None


def dependencies(path: Path) -> list[str]:
    """Every library this image loads by name (LC_LOAD_DYLIB and friends)."""
    names = []
    for command in load_commands(path):
        if command.cmd in _DEPENDENCY_COMMANDS:
            offset = struct.unpack_from("<I", command.payload, 0)[0]
            names.append(_string_at(command.payload, offset))
    return names


def dylib_id(path: Path) -> str | None:
    """This image's own install name, for a dylib (LC_ID_DYLIB)."""
    for command in load_commands(path):
        if command.cmd == LC_ID_DYLIB:
            offset = struct.unpack_from("<I", command.payload, 0)[0]
            return _string_at(command.payload, offset)
    return None


def rpaths(path: Path) -> list[str]:
    """Every LC_RPATH search path, in file order."""
    found = []
    for command in load_commands(path):
        if command.cmd == LC_RPATH:
            offset = struct.unpack_from("<I", command.payload, 0)[0]
            found.append(_string_at(command.payload, offset))
    return found


def inspect(path: Path) -> dict:
    """A report-ready summary of one Mach-O image."""
    header = read_header(path)
    version = build_version(path)
    return {
        "arch": header.arch,
        "kind": header.kind,
        "is_64": header.is_64,
        "pie": header.pie,
        "platform": version.platform_name if version else None,
        "minos": version.minos if version else None,
        "sdk": version.sdk if version else None,
        "install_name": dylib_id(path),
        "dependencies": dependencies(path),
        "rpaths": rpaths(path),
    }


def signature_status(path: Path) -> tuple[bool, str]:
    """(valid, detail) for one image's code signature."""
    result = subprocess.run(
        ["codesign", "--verify", "--strict", str(path)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    detail = (result.stderr or result.stdout).strip()
    return result.returncode == 0, detail


def signature_metadata_ranges(path: Path) -> list[tuple[int, int]]:
    """Byte ranges that are signing metadata rather than compiled content.

    An ad-hoc signature covers the whole image and therefore changes whenever
    anything does; the LC_UUID is regenerated per link. Two builds of the same
    source can differ in exactly these bytes and nothing else, which is a very
    different statement from "the builds differ" — this is what lets the
    reproducibility report tell them apart. Signatures must occupy the file tail
    after the header and load commands; malformed ranges raise MachOError.
    """
    endian, is_64, fields = _header_fields(path)
    commands_end = struct.calcsize(_HEADER_FMT_64 if is_64 else _HEADER_FMT) + fields[5]
    file_size = Path(path).stat().st_size
    ranges: list[tuple[int, int]] = []
    signature_seen = False
    for command in load_commands(path):
        if command.cmd == LC_UUID:
            if len(command.payload) != 16:
                raise MachOError(f"{path}: LC_UUID must contain exactly sixteen UUID bytes")
            ranges.append((command.offset + 8, 16))
        elif command.cmd == LC_CODE_SIGNATURE:
            if signature_seen or len(command.payload) != 8:
                raise MachOError(f"{path}: malformed or duplicate LC_CODE_SIGNATURE")
            signature_seen = True
            data_offset, data_size = struct.unpack_from(f"{endian}II", command.payload, 0)
            if data_offset < commands_end or not data_size or data_offset + data_size != file_size:
                raise MachOError(f"{path}: code signature must follow load commands and occupy the file tail")
            ranges.append((data_offset, data_size))
    return ranges


def normalized_bytes(path: Path) -> bytes:
    """The image with its UUID and signature ranges zeroed."""
    raw = bytearray(Path(path).read_bytes())
    for offset, size in signature_metadata_ranges(path):
        if offset < 0 or offset + size > len(raw):
            raise MachOError(f"{path}: normalization range outside file")
        raw[offset:offset + size] = b"\x00" * size
    return bytes(raw)


def find_machos(root: Path) -> list[Path]:
    """Real (non-symlink) Mach-O images under an installed tree."""
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
                if magic in _MAGIC_32 or magic in _MAGIC_64:
                    found.append(path)
    return sorted(found)
