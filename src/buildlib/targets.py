"""The target triples the build repositories release, and the strings each one maps to."""

from __future__ import annotations

import platform
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

MACOS = "macos"
LINUX_MUSL = "linux-musl"

_MACHINE_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}


class UnsupportedTargetError(Exception):
    """No target description exists for this triple or machine."""


@dataclass(frozen=True)
class Target:
    """One released target.

    `machine` is the `platform.machine()` spelling on that target ("arm64" on
    macOS, "aarch64" on Linux). `family` is "macos" or "linux-musl". The Linux
    fields are None on macOS.
    """

    triple: str
    machine: str
    os: str
    family: str
    executable_format: str
    docker_platform: str | None = None
    alpine_arch: str | None = None
    musl_loader: str | None = None
    elf_machine: int | None = None

    @property
    def is_macos(self) -> bool:
        return self.family == MACOS

    @property
    def is_linux(self) -> bool:
        return self.family == LINUX_MUSL


_DARWIN_ARM64 = Target(
    triple="aarch64-apple-darwin",
    machine="arm64",
    os="darwin",
    family=MACOS,
    executable_format="mach-o",
)

_LINUX_X86_64 = Target(
    triple="x86_64-unknown-linux-musl",
    machine="x86_64",
    os="linux",
    family=LINUX_MUSL,
    executable_format="elf",
    docker_platform="linux/amd64",
    alpine_arch="x86_64",
    musl_loader="/lib/ld-musl-x86_64.so.1",
    elf_machine=62,
)

_LINUX_AARCH64 = Target(
    triple="aarch64-unknown-linux-musl",
    machine="aarch64",
    os="linux",
    family=LINUX_MUSL,
    executable_format="elf",
    docker_platform="linux/arm64",
    alpine_arch="aarch64",
    musl_loader="/lib/ld-musl-aarch64.so.1",
    elf_machine=183,
)

TARGETS: Mapping[str, Target] = MappingProxyType(
    {target.triple: target for target in (_DARWIN_ARM64, _LINUX_X86_64, _LINUX_AARCH64)}
)
TRIPLES: tuple[str, ...] = tuple(sorted(TARGETS))


def target_for_triple(triple: str) -> Target:
    """Return the description of a supported triple, or raise UnsupportedTargetError."""
    try:
        return TARGETS[triple]
    except KeyError:
        raise UnsupportedTargetError(f"target not supported: {triple!r} (supported: {list(TRIPLES)})") from None


def native_target(system: str | None = None, machine: str | None = None) -> Target:
    """Return the target matching this host, or the given `platform.system()` and `platform.machine()`."""
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    architecture = _MACHINE_ALIASES.get(machine.lower(), machine.lower())
    host_os = system.lower()
    for target in TARGETS.values():
        if target.os == host_os and _MACHINE_ALIASES.get(target.machine, target.machine) == architecture:
            return target
    raise UnsupportedTargetError(f"no target for host {system} {machine}; supported: {list(TRIPLES)}")
