from __future__ import annotations

import hashlib
import json
import lzma
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest

from buildlib import release, targets

REVISION = "0123456789abcdef0123456789abcdef01234567"
LOCK_SHA = hashlib.sha256(b"lock").hexdigest()
EPOCH = 1_700_000_000


def manifest(**changes: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "product": "cpython",
        "version": "3.15.1",
        "target": "aarch64-apple-darwin",
        "source_revision": REVISION,
        "sources_lock_sha256": LOCK_SHA,
    }
    arguments.update(changes)
    return release.build_manifest(**arguments)


def test_asset_names_follow_the_contract() -> None:
    assert release.archive_name("node", "26.12.1", "x86_64-unknown-linux-musl") == "node-26.12.1-x86_64-unknown-linux-musl.tar.xz"
    assert (
        release.manifest_name("node", "26.12.1", "x86_64-unknown-linux-musl")
        == "node-26.12.1-x86_64-unknown-linux-musl.manifest.json"
    )


@pytest.mark.parametrize(
    ("product", "version", "target"),
    [
        ("../node", "1", "aarch64-apple-darwin"),
        ("node", "1/2", "aarch64-apple-darwin"),
        ("node", "", "aarch64-apple-darwin"),
        ("node", "1", "x86_64-unknown-linux-gnu"),
    ],
)
def test_asset_names_reject_unsafe_parts(product: str, version: str, target: str) -> None:
    with pytest.raises((release.ReleaseError, targets.UnsupportedTargetError)):
        release.archive_name(product, version, target)


def test_manifest_has_required_fields_first_then_sorted_extras(tmp_path: Path) -> None:
    built = manifest(extra={"toolchain": {"zeta": 1, "alpha": [{"b": 1, "a": 2}]}, "hardening": True, "archive": {"size": 5}})
    assert list(built) == [
        "schema_version",
        "product",
        "version",
        "target",
        "source_revision",
        "sources_lock_sha256",
        "archive",
        "hardening",
        "toolchain",
    ]
    assert list(built["toolchain"]) == ["alpha", "zeta"]
    assert list(built["toolchain"]["alpha"][0]) == ["a", "b"]
    path = release.write_manifest(tmp_path, built)
    assert path.name == "cpython-3.15.1-aarch64-apple-darwin.manifest.json"
    text = path.read_text()
    assert text.endswith("}\n")
    assert json.loads(text) == built
    assert list(json.loads(text)) == list(built)


def test_manifest_bytes_do_not_depend_on_extra_insertion_order(tmp_path: Path) -> None:
    first = release.write_manifest(tmp_path / "a", manifest(extra={"x": {"b": 1, "a": 2}, "y": 3}))
    second = release.write_manifest(tmp_path / "b", manifest(extra={"y": 3, "x": {"a": 2, "b": 1}}))
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"source_revision": "abc123"}, "source_revision"),
        ({"source_revision": REVISION.upper()}, "source_revision"),
        ({"sources_lock_sha256": "nope"}, "sources_lock_sha256"),
        ({"target": "riscv64-unknown-linux-musl"}, "not supported"),
        ({"product": "bad name"}, "invalid product"),
        ({"extra": {"product": "other"}}, "redefine required fields"),
        ({"extra": {"schema_version": 2}}, "redefine required fields"),
        ({"extra": {1: "x"}}, "keys must be strings"),
    ],
)
def test_manifest_validation(changes: dict[str, Any], message: str) -> None:
    with pytest.raises((release.ReleaseError, targets.UnsupportedTargetError), match=message):
        manifest(**changes)


def test_write_manifest_rechecks_required_fields(tmp_path: Path) -> None:
    incomplete = manifest()
    del incomplete["source_revision"]
    with pytest.raises(release.ReleaseError, match="lacks required fields"):
        release.write_manifest(tmp_path, incomplete)
    wrong = manifest()
    wrong["schema_version"] = 2
    with pytest.raises(release.ReleaseError, match="schema_version"):
        release.write_manifest(tmp_path, wrong)
    assert list(tmp_path.iterdir()) == []


def make_tree(root: Path, *, reverse: bool = False) -> Path:
    files: list[tuple[str, bytes, int]] = [
        ("bin/tool", b"#!/bin/sh\necho tool\n", 0o700),
        ("lib/libx.so", b"\x7fELF-ish" * 50, 0o600),
        ("lib/a-b", b"dash", 0o664),
        ("lib/a/inner", b"inner", 0o640),
        ("share/doc/LICENSE", "licence é\n".encode(), 0o444),
        ("share/" + "long-name-" * 20 + "/file", b"long", 0o644),
    ]
    for relative, data, mode in reversed(files) if reverse else files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
    (root / "lib" / "libx.so.1").symlink_to("libx.so")
    (root / "empty").mkdir()
    (root / "linked-lib").symlink_to("lib", target_is_directory=True)
    return root


def test_archive_round_trip_normalizes_metadata(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "tree")
    archive = release.create_archive(tree, tmp_path / "out" / "p.tar.xz", mtime=EPOCH, prefix="python")
    assert archive == tmp_path / "out" / "p.tar.xz"
    assert archive.read_bytes().startswith(b"\xfd7zXZ\x00")
    with lzma.open(archive) as stream:
        assert stream.read(6) == b"python"
    with tarfile.open(archive, "r:xz") as tar:
        members = tar.getmembers()
        names = [member.name for member in members]
        assert names[0] == "python"
        assert names[1:] == sorted(names[1:])
        assert len(set(names)) == len(names)
        for member in members:
            assert (member.uid, member.gid, member.uname, member.gname, member.mtime) == (0, 0, "", "", EPOCH)
        by_name = {member.name: member for member in members}
        assert by_name["python"].isdir()
        assert by_name["python"].mode == 0o755
        assert by_name["python/empty"].isdir()
        assert by_name["python/empty"].mode == 0o755
        assert by_name["python/bin/tool"].mode == 0o755
        assert by_name["python/lib/libx.so"].mode == 0o644
        assert by_name["python/lib/a-b"].mode == 0o644
        assert by_name["python/share/doc/LICENSE"].mode == 0o644
        assert by_name["python/linked-lib"].issym()
        assert "python/linked-lib/libx.so" not in by_name
        link = by_name["python/lib/libx.so.1"]
        assert link.issym()
        assert link.linkname == "libx.so"
        assert link.mode == 0o777
        extracted = tar.extractfile("python/lib/libx.so")
        assert extracted is not None
        assert extracted.read() == (tree / "lib" / "libx.so").read_bytes()
        licence = tar.extractfile("python/share/doc/LICENSE")
        assert licence is not None
        assert licence.read().decode() == "licence é\n"
        assert by_name["python/share/" + "long-name-" * 20 + "/file"].isfile()


def test_archive_without_prefix_has_no_root_entry(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "tree")
    archive = release.create_archive(tree, tmp_path / "p.tar.xz", mtime=0)
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert names[0] == "bin"
    assert "." not in names


def test_archive_bytes_depend_only_on_content_and_arguments(tmp_path: Path) -> None:
    first = make_tree(tmp_path / "one")
    second = make_tree(tmp_path / "two", reverse=True)
    for path in second.rglob("*"):
        if not path.is_symlink():
            os.utime(path, (1_000, 1_000))
    one = release.create_archive(first, tmp_path / "one.tar.xz", mtime=EPOCH, prefix="p")
    two = release.create_archive(second, tmp_path / "two.tar.xz", mtime=EPOCH, prefix="p")
    assert one.read_bytes() == two.read_bytes()
    again = release.create_archive(first, tmp_path / "again.tar.xz", mtime=EPOCH + 1, prefix="p")
    assert again.read_bytes() != one.read_bytes()


def test_archive_publication_is_atomic(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "tree")
    destination = tmp_path / "p.tar.xz"
    destination.write_bytes(b"previous release")
    os.mkfifo(tree / "pipe")
    with pytest.raises(release.ReleaseError, match="unsupported file type"):
        release.create_archive(tree, destination, mtime=EPOCH)
    assert destination.read_bytes() == b"previous release"
    assert [path.name for path in tmp_path.iterdir() if path.name != "tree"] == ["p.tar.xz"]


def test_archive_rejects_bad_arguments(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "tree")
    link = tmp_path / "link"
    link.symlink_to(tree)
    with pytest.raises(release.ReleaseError, match="not a directory"):
        release.create_archive(link, tmp_path / "a.tar.xz", mtime=0)
    with pytest.raises(release.ReleaseError, match="not a directory"):
        release.create_archive(tmp_path / "missing", tmp_path / "a.tar.xz", mtime=0)
    with pytest.raises(release.ReleaseError, match="mtime"):
        release.create_archive(tree, tmp_path / "a.tar.xz", mtime=-1)
    with pytest.raises(release.ReleaseError, match="prefix"):
        release.create_archive(tree, tmp_path / "a.tar.xz", mtime=0, prefix="../x")
    assert not (tmp_path / "a.tar.xz").exists()


def release_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "release"
    directory.mkdir()
    (directory / "b.tar.xz").write_bytes(b"bravo")
    (directory / "a.tar.xz").write_bytes(b"alpha")
    (directory / "B.manifest.json").write_bytes(b"{}")
    return directory


def test_sha256sums_are_sorted_and_cover_every_other_asset(tmp_path: Path) -> None:
    directory = release_directory(tmp_path)
    sums = release.write_sha256sums(directory)
    assert sums == directory / "SHA256SUMS"
    lines = sums.read_text().splitlines()
    assert [line.split("  ")[1] for line in lines] == ["B.manifest.json", "a.tar.xz", "b.tar.xz"]
    assert lines[1] == f"{hashlib.sha256(b'alpha').hexdigest()}  a.tar.xz"
    release.verify_sha256sums(directory)
    release.write_sha256sums(directory)
    release.verify_sha256sums(directory)
    assert sums.read_text().count("\n") == 3


@pytest.mark.skipif(shutil.which("sha256sum") is None and shutil.which("shasum") is None, reason="needs a checksum tool")
def test_sha256sums_satisfy_the_system_checker(tmp_path: Path) -> None:
    directory = release_directory(tmp_path)
    release.write_sha256sums(directory)
    command = ["sha256sum", "--check", "SHA256SUMS"] if shutil.which("sha256sum") else ["shasum", "-a", "256", "--check", "SHA256SUMS"]
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_verify_detects_each_kind_of_drift(tmp_path: Path) -> None:
    directory = release_directory(tmp_path)
    sums = release.write_sha256sums(directory)
    original = sums.read_text()

    (directory / "a.tar.xz").write_bytes(b"tampered")
    with pytest.raises(release.ReleaseError, match=r"digest mismatch for a\.tar\.xz"):
        release.verify_sha256sums(directory)
    (directory / "a.tar.xz").write_bytes(b"alpha")

    (directory / "extra.txt").write_bytes(b"new")
    with pytest.raises(release.ReleaseError, match="does not cover"):
        release.verify_sha256sums(directory)
    (directory / "extra.txt").unlink()

    (directory / "b.tar.xz").unlink()
    with pytest.raises(release.ReleaseError, match=r"missing asset b\.tar\.xz"):
        release.verify_sha256sums(directory)
    (directory / "b.tar.xz").write_bytes(b"bravo")

    lines = original.splitlines(keepends=True)
    cases = {
        "not sorted": "".join(reversed(lines)),
        "listed twice": original + lines[0],
        "malformed": original.replace("  ", " ", 1),
        "trailing newline": original.rstrip("\n"),
        "malformed line": original + "../escape\n",
    }
    for message, text in cases.items():
        sums.write_text(text)
        with pytest.raises(release.ReleaseError, match=message):
            release.verify_sha256sums(directory)
    sums.write_text(original)
    release.verify_sha256sums(directory)


def test_sums_reject_nested_or_unlistable_assets(tmp_path: Path) -> None:
    directory = release_directory(tmp_path)
    (directory / "sub").mkdir()
    with pytest.raises(release.ReleaseError, match="not a regular file"):
        release.write_sha256sums(directory)
    (directory / "sub").rmdir()
    (directory / "back\\slash").write_bytes(b"x")
    with pytest.raises(release.ReleaseError, match="cannot be listed"):
        release.write_sha256sums(directory)
    (directory / "back\\slash").unlink()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(release.ReleaseError, match="no assets"):
        release.write_sha256sums(empty)
    with pytest.raises(release.ReleaseError, match="cannot read"):
        release.verify_sha256sums(empty)


def test_archive_rejects_unreadable_directory_without_replacing_archive(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "tree")
    destination = tmp_path / "p.tar.xz"
    release.create_archive(tree, destination, mtime=EPOCH)
    original = destination.read_bytes()
    directory = tree / "lib"
    directory.chmod(0)
    try:
        with pytest.raises(PermissionError):
            release.create_archive(tree, destination, mtime=EPOCH)
        assert destination.read_bytes() == original
        assert sorted(path.name for path in tmp_path.iterdir()) == ["p.tar.xz", "tree"]
    finally:
        directory.chmod(0o755)
    release.create_archive(tree, destination, mtime=EPOCH)
    assert destination.read_bytes() == original
