from __future__ import annotations

import pytest

from buildlib import targets


def test_supported_triples_are_exactly_the_released_targets() -> None:
    assert targets.TRIPLES == (
        "aarch64-apple-darwin",
        "aarch64-unknown-linux-musl",
        "x86_64-unknown-linux-musl",
    )
    assert set(targets.TARGETS) == set(targets.TRIPLES)


def test_platform_strings_per_target() -> None:
    mac = targets.target_for_triple("aarch64-apple-darwin")
    assert (mac.os, mac.machine, mac.family, mac.executable_format) == ("darwin", "arm64", "macos", "mach-o")
    assert (mac.docker_platform, mac.alpine_arch, mac.musl_loader, mac.elf_machine) == (None, None, None, None)
    assert mac.is_macos
    arm = targets.target_for_triple("aarch64-unknown-linux-musl")
    assert (arm.os, arm.machine, arm.family, arm.executable_format) == ("linux", "aarch64", "linux-musl", "elf")
    assert (arm.docker_platform, arm.alpine_arch, arm.musl_loader, arm.elf_machine) == (
        "linux/arm64",
        "aarch64",
        "/lib/ld-musl-aarch64.so.1",
        183,
    )
    x86 = targets.target_for_triple("x86_64-unknown-linux-musl")
    assert (x86.docker_platform, x86.alpine_arch, x86.musl_loader, x86.elf_machine) == (
        "linux/amd64",
        "x86_64",
        "/lib/ld-musl-x86_64.so.1",
        62,
    )
    assert x86.is_linux


@pytest.mark.parametrize("triple", ["x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu", "x86_64-apple-darwin", ""])
def test_unsupported_triples_are_rejected(triple: str) -> None:
    with pytest.raises(targets.UnsupportedTargetError, match="not supported"):
        targets.target_for_triple(triple)


@pytest.mark.parametrize(
    ("system", "machine", "triple"),
    [
        ("Darwin", "arm64", "aarch64-apple-darwin"),
        ("Linux", "aarch64", "aarch64-unknown-linux-musl"),
        ("Linux", "arm64", "aarch64-unknown-linux-musl"),
        ("Linux", "x86_64", "x86_64-unknown-linux-musl"),
        ("Linux", "AMD64", "x86_64-unknown-linux-musl"),
    ],
)
def test_native_target_maps_host_spellings(system: str, machine: str, triple: str) -> None:
    assert targets.native_target(system, machine).triple == triple


@pytest.mark.parametrize(("system", "machine"), [("Darwin", "x86_64"), ("Windows", "AMD64"), ("Linux", "riscv64")])
def test_native_target_rejects_unsupported_hosts(system: str, machine: str) -> None:
    with pytest.raises(targets.UnsupportedTargetError):
        targets.native_target(system, machine)


def test_native_target_defaults_to_this_host_or_fails_cleanly() -> None:
    try:
        found = targets.native_target()
    except targets.UnsupportedTargetError:
        return
    assert found.triple in targets.TRIPLES
