from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Literal

import pytest

from buildlib import __main__ as cli
from buildlib import locks, targets, tools

FIXTURES = Path(__file__).parent / "fixtures"
GLUON_TAG = "release-fa8b6aace38683a17ee3f398d8b568341c7192fe"
MOLD_TAG = "mold-musl-3.0.0-cfb4aab"


class Response:
    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class FakeGitHub:
    """Serves release downloads, checksum files, and the release API from memory."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []
        self.user_agents: list[str | None] = []
        self.timeouts: list[float | None] = []

    def add_release(
        self,
        repository: str,
        tag: str,
        checksum_name: str,
        assets: dict[str, bytes],
        sums: str | None = None,
    ) -> None:
        base = f"/{repository}/releases/download/{tag}"
        api = f"/repos/{repository}/releases/tags/{tag}"
        for name, data in assets.items():
            self.files[f"{base}/{name}"] = data
        if sums is None:
            sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(assets.items()))
        self.files[f"{base}/{checksum_name}"] = sums.encode()
        self.files[api] = json.dumps(
            {"assets": [{"name": name, "size": len(data)} for name, data in assets.items()]}
        ).encode()

    def open(self, request: Any, timeout: float | None = None) -> Response:
        url = request.full_url if hasattr(request, "full_url") else str(request)
        path = urllib.parse.urlsplit(url).path
        self.requests.append(path)
        self.user_agents.append(request.get_header("User-agent"))
        self.timeouts.append(timeout)
        if path not in self.files:
            raise OSError(f"no such file: {path}")
        return Response(self.files[path])


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(urllib.request, "urlopen", fake.open)
    return fake


def tar_bytes(
    entries: list[tuple[str, bytes | str | None]], mode: Literal["w:xz", "w:gz"] = "w:xz"
) -> bytes:
    """Build an archive; entries are (name, content) files, (name, None) dirs, (name, "link:...") symlinks."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as tar:
        for name, content in entries:
            if content is None:
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            elif isinstance(content, str) and content.startswith("link:"):
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = content[5:]
                info.mode = 0o777
                tar.addfile(info)
            else:
                data = content if isinstance(content, bytes) else str(content).encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def flat_archive(tool: str = "rg", triple: str = "aarch64-apple-darwin") -> bytes:
    name = f"{tool}-{triple}"
    return tar_bytes([(name, b"#!/bin/sh\necho hi\n")])


def top_archive(top: str, triple: str, binary: str = "tool") -> bytes:
    return tar_bytes(
        [
            (f"{top}/", None),
            (f"{top}/bin/", None),
            (f"{top}/bin/{binary}", b"#!/bin/sh\necho hi\n"),
            (f"{top}/README", b"readme\n"),
        ]
    )


def lock_for(tool: str, tag: str, triple: str, archive: bytes, version: str = "1.0") -> tools.ToolEntry:
    spec = tools.spec_for(tool)
    name = spec.asset_name(version, triple)
    return tools.ToolEntry(
        tool=tool,
        repository=spec.repository,
        tag=tag,
        product=spec.product,
        version="" if not spec.versioned else version,
        assets={triple: tools.AssetPin(name=name, size=len(archive), sha256=hashlib.sha256(archive).hexdigest())},
    )


def write_lock(tmp_path: Path, *entries: tools.ToolEntry) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "tools.lock.json"
    document = {"schema": 1, "tools": {entry.tool: entry.as_dict() for entry in entries}}
    path.write_text(json.dumps(document))
    return path


def gluon_lock(tmp_path: Path) -> Path:
    return FIXTURES / "tools.lock.json"


# --- lock loading and validation -------------------------------------------------


def test_load_lock_reads_fixture() -> None:
    lock = tools.load_lock(FIXTURES / "tools.lock.json")
    entry = lock.entry("gluon")
    assert entry.tool == "gluon"
    assert entry.repository == "laputa-systems/gluon"
    assert entry.version == ""
    assert set(entry.assets) == set(targets.TRIPLES)
    darwin = entry.asset_for("aarch64-apple-darwin")
    assert darwin.name == "gluon-aarch64-apple-darwin.tar.xz"
    assert darwin.size == 860740


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.update(schema=2),
        lambda doc: doc.update(schema="1"),
        lambda doc: doc.pop("schema"),
        lambda doc: doc.update(extra=1),
        lambda doc: doc.update(tools=[]),
        lambda doc: doc["tools"].update(bogus={"repository": "x", "tag": "t", "product": "p", "version": "", "assets": {}}),
        lambda doc: doc["tools"]["gluon"].update(repository="evil/repo"),
        lambda doc: doc["tools"]["gluon"].update(product="other"),
        lambda doc: doc["tools"]["gluon"].update(tag=""),
        lambda doc: doc["tools"]["gluon"].update(version="1.0"),
        lambda doc: doc["tools"]["gluon"].update(unexpected=1),
        lambda doc: doc["tools"]["gluon"].pop("assets"),
        lambda doc: doc["tools"]["gluon"].update(assets={}),
        lambda doc: doc["tools"]["gluon"]["assets"].update(
            {"aarch64-apple-darwin": {"name": "wrong.tar.xz", "size": 1, "sha256": "0" * 64}}
        ),
        lambda doc: doc["tools"]["gluon"]["assets"]["aarch64-apple-darwin"].update(size=-1),
        lambda doc: doc["tools"]["gluon"]["assets"]["aarch64-apple-darwin"].update(size="1"),
        lambda doc: doc["tools"]["gluon"]["assets"]["aarch64-apple-darwin"].update(size=True),
        lambda doc: doc["tools"]["gluon"]["assets"]["aarch64-apple-darwin"].update(sha256="XYZ"),
        lambda doc: doc["tools"]["gluon"]["assets"].update(
            {"riscv64-linux": {"name": "x", "size": 1, "sha256": "0" * 64}}
        ),
        lambda doc: doc["tools"]["gluon"]["assets"]["aarch64-apple-darwin"].update(extra=1),
    ],
)
def test_load_lock_rejects_invalid_documents(tmp_path: Path, mutate: Any) -> None:
    document = json.loads((FIXTURES / "tools.lock.json").read_text())
    mutate(document)
    path = tmp_path / "tools.lock.json"
    path.write_text(json.dumps(document))
    with pytest.raises(locks.LockError) as raised:
        tools.load_lock(path)
    assert str(path) in str(raised.value)


def test_load_lock_versioned_rules(tmp_path: Path) -> None:
    entry = lock_for("cmake", "release-x", "aarch64-apple-darwin", b"x" * 10)
    assert entry.version == "1.0"
    path = write_lock(tmp_path, entry)
    loaded = tools.load_lock(path)
    assert loaded.entry("cmake").asset_for("aarch64-apple-darwin").name == (
        "cmake-1.0-aarch64-apple-darwin.tar.xz"
    )
    document = json.loads(path.read_text())
    document["tools"]["cmake"]["version"] = ""
    path.write_text(json.dumps(document))
    with pytest.raises(locks.LockError, match="version must be nonempty"):
        tools.load_lock(path)


def test_load_lock_missing_file(tmp_path: Path) -> None:
    with pytest.raises(locks.LockError, match="cannot read"):
        tools.load_lock(tmp_path / "absent.json")


# --- lock entry generation -------------------------------------------------------


def test_lock_entry_from_sha256sums_fixture(github: FakeGitHub) -> None:
    spec = tools.spec_for("gluon")
    github.files[
        f"/{spec.repository}/releases/download/{GLUON_TAG}/SHA256SUMS"
    ] = (FIXTURES / "SHA256SUMS").read_bytes()
    github.files[f"/repos/{spec.repository}/releases/tags/{GLUON_TAG}"] = json.dumps(
        {
            "assets": [
                {"name": "gluon-aarch64-apple-darwin.tar.xz", "size": 111},
                {"name": "gluon-aarch64-unknown-linux-musl.tar.xz", "size": 222},
                {"name": "gluon-x86_64-unknown-linux-musl.tar.xz", "size": 333},
                {"name": "SHA256SUMS", "size": 10},
            ]
        }
    ).encode()
    entry = tools.lock_entry("gluon", GLUON_TAG)
    assert entry.version == ""
    assert entry.tag == GLUON_TAG
    darwin = entry.asset_for("aarch64-apple-darwin")
    assert darwin.size == 111
    assert darwin.sha256 == "4c103442555d8e76a5d57bd4d9b4e90fa86e715ffe1a8e23bcbb3a6247618c6e"
    assert set(entry.assets) == set(targets.TRIPLES)


def test_lock_entry_std_versioned_and_dev_product_isolation(github: FakeGitHub) -> None:
    spec = tools.spec_for("llvm")
    tag = "release-llvm"
    assets = {
        f"llvm-23.1.5-{triple}.tar.xz": b"llvm" for triple in ("aarch64-unknown-linux-musl", "x86_64-unknown-linux-musl")
    }
    assets.update(
        {
            f"llvm-dev-23.1.5-{triple}.tar.xz": b"dev" for triple in ("aarch64-unknown-linux-musl", "x86_64-unknown-linux-musl")
        }
    )
    github.add_release(spec.repository, tag, "SHA256SUMS", assets)
    entry = tools.lock_entry("llvm", tag)
    assert entry.version == "23.1.5"
    assert all(pin.name.startswith("llvm-23.1.5-") for pin in entry.assets.values())
    dev = tools.lock_entry("llvm-dev", tag)
    assert dev.version == "23.1.5"
    assert all(pin.name.startswith("llvm-dev-23.1.5-") for pin in dev.assets.values())


def test_lock_entry_mold_special_case(github: FakeGitHub) -> None:
    spec = tools.spec_for("mold")
    github.files[f"/{spec.repository}/releases/download/{MOLD_TAG}/checksums"] = (
        FIXTURES / "checksums"
    ).read_bytes()
    github.files[f"/repos/{spec.repository}/releases/tags/{MOLD_TAG}"] = json.dumps(
        {
            "assets": [
                {"name": "mold-3.0.0-aarch64-linux-musl.tar.gz", "size": 10},
                {"name": "mold-3.0.0-x86_64-linux-musl.tar.gz", "size": 20},
            ]
        }
    ).encode()
    entry = tools.lock_entry("mold", MOLD_TAG)
    assert entry.version == "3.0.0"
    assert set(entry.assets) == {"aarch64-unknown-linux-musl", "x86_64-unknown-linux-musl"}
    pin = entry.asset_for("aarch64-unknown-linux-musl")
    assert pin.name == "mold-3.0.0-aarch64-linux-musl.tar.gz"
    with pytest.raises(tools.ToolError) as raised:
        entry.asset_for("aarch64-apple-darwin")
    assert str(raised.value) == (
        f"mold {MOLD_TAG}: no asset for target 'aarch64-apple-darwin'"
    )


def test_lock_entry_rejects_bad_releases(github: FakeGitHub) -> None:
    spec = tools.spec_for("rg")
    github.files[f"/{spec.repository}/releases/download/t1/SHA256SUMS"] = b"not-a-sum\n"
    github.files[f"/repos/{spec.repository}/releases/tags/t1"] = b"{}"
    with pytest.raises(locks.LockError, match="malformed checksum"):
        tools.lock_entry("rg", "t1")
    github.files[f"/{spec.repository}/releases/download/t2/SHA256SUMS"] = b""
    github.files[f"/repos/{spec.repository}/releases/tags/t2"] = b"{}"
    with pytest.raises(locks.LockError, match="lists no assets"):
        tools.lock_entry("rg", "t2")
    github.files[f"/{spec.repository}/releases/download/t3/SHA256SUMS"] = (
        b"0" * 64 + b"  unrelated.bin\n"
    )
    github.files[f"/repos/{spec.repository}/releases/tags/t3"] = b"{}"
    with pytest.raises(tools.ToolError, match="no assets match"):
        tools.lock_entry("rg", "t3")
    with pytest.raises(tools.ToolError, match="unknown tool"):
        tools.lock_entry("nope", "t1")


def test_write_lock_adds_and_replaces(tmp_path: Path) -> None:
    first = lock_for("rg", "t1", "aarch64-apple-darwin", flat_archive())
    path = tmp_path / "tools.lock.json"
    tools.write_lock(path, first)
    loaded = tools.load_lock(path)
    assert loaded.entry("rg").tag == "t1"
    second = lock_for("rg", "t2", "aarch64-apple-darwin", flat_archive())
    tools.write_lock(path, second)
    third = lock_for("fd", "t3", "aarch64-apple-darwin", flat_archive("fd"))
    tools.write_lock(path, third)
    loaded = tools.load_lock(path)
    assert loaded.entry("rg").tag == "t2"
    assert loaded.entry("fd").tag == "t3"


# --- install ----------------------------------------------------------------------


def test_install_downloads_extracts_and_caches(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive("rg", triple)
    spec = tools.spec_for("rg")
    github.add_release(spec.repository, "t1", "SHA256SUMS", {f"rg-{triple}.tar.xz": archive})
    entry = tools.lock_entry("rg", "t1")
    path = write_lock(tmp_path, entry)
    cache = tmp_path / "cache"
    bindir = tools.install(path, "rg", cache, target=triple)
    pin = entry.asset_for(triple)
    assert bindir == cache / "rg" / pin.sha256
    executable = bindir / "rg"
    assert executable.is_file()
    assert executable.stat().st_mode & 0o777 == 0o755
    assert not (bindir / f"rg-{triple}").exists()
    assert github.requests == [
        f"/{spec.repository}/releases/download/t1/SHA256SUMS",
        f"/repos/{spec.repository}/releases/tags/t1",
        f"/{spec.repository}/releases/download/t1/rg-{triple}.tar.xz",
    ]
    assert github.user_agents == [locks.USER_AGENT] * 3
    assert github.timeouts == [120.0] * 3


@pytest.mark.parametrize("tool", ["rg", "fd", "jaq", "ast-grep"])
def test_install_bare_archive_publishes_command_name(
    github: FakeGitHub, tmp_path: Path, tool: str
) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive(tool, triple)
    serve(github, tool, "t1", triple, archive)
    entry = lock_for(tool, "t1", triple, archive)
    path = write_lock(tmp_path / tool, entry)
    bindir = tools.install(path, tool, tmp_path / "cache", target=triple)
    executable = bindir / tool
    assert executable.is_file()
    assert executable.stat().st_mode & 0o777 == 0o755
    assert not (bindir / f"{tool}-{triple}").exists()


def test_install_flat_archive_keeps_payload_names(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = tar_bytes(
        [
            ("gluon-build", b"#!/bin/sh\n"),
            ("meson", b"#!/bin/sh\n"),
            ("ninja", b"#!/bin/sh\n"),
            ("LICENSE-MIT", b"mit\n"),
        ]
    )
    serve(github, "gluon", "t1", triple, archive)
    entry = lock_for("gluon", "t1", triple, archive)
    path = write_lock(tmp_path, entry)
    bindir = tools.install(path, "gluon", tmp_path / "cache", target=triple)
    for name in ("gluon-build", "meson", "ninja", "LICENSE-MIT"):
        assert (bindir / name).is_file()
    assert not (bindir / f"gluon-{triple}").exists()
    assert not (bindir / "gluon").exists()


def test_install_cache_hit_makes_no_network_request(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive("rg", triple)
    spec = tools.spec_for("rg")
    github.add_release(spec.repository, "t1", "SHA256SUMS", {f"rg-{triple}.tar.xz": archive})
    entry = tools.lock_entry("rg", "t1")
    path = write_lock(tmp_path, entry)
    cache = tmp_path / "cache"
    first = tools.install(path, "rg", cache, target=triple)
    github.requests.clear()
    github.files.clear()
    second = tools.install(path, "rg", cache, target=triple)
    assert second == first
    assert github.requests == []


def test_install_rejects_digest_and_size_mismatch(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive("rg", triple)
    spec = tools.spec_for("rg")
    name = f"rg-{triple}.tar.xz"
    github.add_release(spec.repository, "t1", "SHA256SUMS", {name: archive})
    entry = tools.lock_entry("rg", "t1")
    write_lock(tmp_path, entry)
    cache = tmp_path / "cache"

    bad = tools.ToolEntry(
        tool="rg",
        repository=spec.repository,
        tag="t1",
        product="rg",
        version="",
        assets={
            triple: tools.AssetPin(
                name=name, size=len(archive), sha256="0" * 64
            )
        },
    )
    bad_path = write_lock(tmp_path / "bad", bad)
    with pytest.raises(locks.FetchError, match="sha256"):
        tools.install(bad_path, "rg", cache, target=triple)
    assert list((cache / "rg").iterdir()) == []

    short = tools.ToolEntry(
        tool="rg",
        repository=spec.repository,
        tag="t1",
        product="rg",
        version="",
        assets={triple: tools.AssetPin(name=name, size=len(archive) + 1, sha256=hashlib.sha256(archive).hexdigest())},
    )
    short_path = write_lock(tmp_path / "short", short)
    with pytest.raises(locks.FetchError, match="served"):
        tools.install(short_path, "rg", cache, target=triple)
    assert list((cache / "rg").iterdir()) == []


def test_install_partial_download_leaves_no_staging(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive("rg", triple)
    spec = tools.spec_for("rg")
    name = f"rg-{triple}.tar.xz"
    github.add_release(spec.repository, "t1", "SHA256SUMS", {name: archive})
    # Lock pins a digest that matches nothing served: the download itself fails.
    bad_entry = tools.ToolEntry(
        tool="rg",
        repository=spec.repository,
        tag="t1",
        product="rg",
        version="",
        assets={triple: tools.AssetPin(name=name, size=len(archive), sha256="f" * 64)},
    )
    path = write_lock(tmp_path, bad_entry)
    cache = tmp_path / "cache"
    with pytest.raises(locks.FetchError):
        tools.install(path, "rg", cache, target=triple)
    leftovers = [p for p in cache.rglob("*") if p.name.startswith(".install-") or p.name.startswith(".")]
    assert leftovers == []
    assert not (cache / "rg" / hashlib.sha256(archive).hexdigest()).exists()


def test_install_selects_target_asset(github: FakeGitHub, tmp_path: Path) -> None:
    darwin_archive = flat_archive("rg", "aarch64-apple-darwin")
    musl_archive = flat_archive("rg", "x86_64-unknown-linux-musl")
    spec = tools.spec_for("rg")
    github.add_release(
        spec.repository,
        "t1",
        "SHA256SUMS",
        {
            "rg-aarch64-apple-darwin.tar.xz": darwin_archive,
            "rg-x86_64-unknown-linux-musl.tar.xz": musl_archive,
        },
    )
    entry = tools.lock_entry("rg", "t1")
    path = write_lock(tmp_path, entry)
    cache = tmp_path / "cache"
    darwin = tools.install(path, "rg", cache, target="aarch64-apple-darwin")
    assert darwin.name == entry.asset_for("aarch64-apple-darwin").sha256
    musl = tools.install(path, "rg", cache, target="x86_64-unknown-linux-musl")
    assert musl.name == entry.asset_for("x86_64-unknown-linux-musl").sha256
    assert darwin != musl
    with pytest.raises(tools.ToolError) as raised:
        tools.install(path, "rg", cache, target="aarch64-unknown-linux-musl")
    assert str(raised.value) == (
        "rg t1: no asset for target 'aarch64-unknown-linux-musl'"
    )
    with pytest.raises(targets.UnsupportedTargetError):
        tools.install(path, "rg", cache, target="riscv64-linux")


def test_install_wrapped_and_mold_layouts(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-unknown-linux-musl"
    cmake_archive = top_archive("cmake-4.4.5-x", triple, "cmake")
    spec = tools.spec_for("cmake")
    name = spec.asset_name("4.4.5", triple)
    github.add_release(spec.repository, "t1", "SHA256SUMS", {name: cmake_archive})
    entry = lock_for("cmake", "t1", triple, cmake_archive, version="4.4.5")
    path = write_lock(tmp_path, entry)
    bindir = tools.install(path, "cmake", tmp_path / "cache", target=triple)
    assert (bindir / "cmake").is_file()
    assert bindir.parent.name == entry.asset_for(triple).sha256
    assert not (bindir.parent.parent / "cmake-4.4.5-x").exists()

    mold_archive = tar_bytes(
        [
            ("mold-3.0.0-aarch64-linux-musl/", None),
            ("mold-3.0.0-aarch64-linux-musl/bin/", None),
            ("mold-3.0.0-aarch64-linux-musl/bin/mold", b"#!/bin/sh\n"),
        ],
        mode="w:gz",
    )
    mspec = tools.spec_for("mold")
    mname = mspec.asset_name("3.0.0", triple)
    github.add_release(mspec.repository, MOLD_TAG, "checksums", {mname: mold_archive})
    mentry = tools.lock_entry("mold", MOLD_TAG)
    mpath = write_lock(tmp_path / "mold", mentry)
    mbindir = tools.install(mpath, "mold", tmp_path / "mcache", target=triple)
    assert (mbindir / "mold").is_file()
    assert mbindir.parent.name == mentry.asset_for(triple).sha256


def serve(github: FakeGitHub, tool: str, tag: str, triple: str, archive: bytes, version: str = "") -> None:
    spec = tools.spec_for(tool)
    name = spec.asset_name(version or "1.0", triple)
    github.add_release(spec.repository, tag, spec.checksum_name, {name: archive})


def test_install_rejects_unsafe_archives(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"

    def install_bad(
        entries: list[tuple[str, bytes | str | None]], mode: Literal["w:xz", "w:gz"] = "w:xz"
    ) -> None:
        archive = tar_bytes(entries, mode)
        serve(github, "rg", "t1", triple, archive)
        entry = lock_for("rg", "t1", triple, archive)
        path = write_lock(tmp_path / hashlib.sha256(archive).hexdigest()[:8], entry)
        with pytest.raises(tools.ToolError):
            tools.install(path, "rg", tmp_path / "cache", target=triple)

    install_bad([("../evil", b"x")])
    install_bad([("/abs/evil", b"x")])
    install_bad([("a/../../evil", b"x")])
    install_bad([("sub/link", "link:../../etc/passwd"), ("sub/", None)])
    install_bad([("sub/link", "link:/etc/passwd"), ("sub/", None)])


def test_install_rejects_archive_member_paths_exact(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"

    def install_bad(entries: list[tuple[str, bytes | str | None]]) -> str:
        archive = tar_bytes(entries)
        serve(github, "rg", "t1", triple, archive)
        entry = lock_for("rg", "t1", triple, archive)
        path = write_lock(tmp_path / hashlib.sha256(archive).hexdigest()[:8], entry)
        with pytest.raises(tools.ToolError) as raised:
            tools.install(path, "rg", tmp_path / "cache", target=triple)
        return str(raised.value)

    for name in ("../evil", "/abs/evil", "a/../../evil", "..", "a/../b", ".", "./"):
        message = install_bad([(name, b"x")])
        assert message == f"unsafe archive member path: {name!r}", name


def test_install_rejects_archive_link_targets_exact(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"

    def install_bad(entries: list[tuple[str, bytes | str | None]]) -> str:
        archive = tar_bytes(entries)
        serve(github, "rg", "t1", triple, archive)
        entry = lock_for("rg", "t1", triple, archive)
        path = write_lock(tmp_path / hashlib.sha256(archive).hexdigest()[:8], entry)
        with pytest.raises(tools.ToolError) as raised:
            tools.install(path, "rg", tmp_path / "cache", target=triple)
        return str(raised.value)

    assert install_bad([("l", "link:")]) == "unsafe archive link target: ''"
    assert install_bad([("l", "link:..")]) == "archive link escapes the root: '..'"
    assert install_bad([("l", "link:../x")]) == "archive link escapes the root: '../x'"
    assert install_bad([("sub/l", "link:../../x"), ("sub/", None)]) == (
        "archive link escapes the root: '../../x'"
    )


def test_install_rejects_members_written_through_escaping_links(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    root = tmp_path / "inner"
    root.mkdir()
    archive = tar_bytes([("s", "link:."), ("a", "link:s/.."), ("a/evil", b"x")])
    serve(github, "rg", "t1", triple, archive)
    path = write_lock(root, lock_for("rg", "t1", triple, archive))
    with pytest.raises(tools.ToolError) as raised:
        tools.install(path, "rg", root / "cache", target=triple)
    assert str(raised.value).endswith("member path escapes the root: 'a/evil'")
    assert not list(tmp_path.rglob("evil"))


def test_install_accepts_inside_symlinks(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = tar_bytes(
        [
            (f"rg-{triple}", b"#!/bin/sh\n"),
            ("lib/", None),
            ("lib/libfoo.so.1", b"elf\n"),
            ("lib/libfoo.so", "link:libfoo.so.1"),
            ("dup", "link:first"),
            ("dup", "link:second"),
            ("sub/", None),
            ("sub/up", "link:../ok"),
            ("ok", b"target\n"),
        ]
    )
    serve(github, "rg", "t1", triple, archive)
    entry = lock_for("rg", "t1", triple, archive)
    path = write_lock(tmp_path, entry)
    bindir = tools.install(path, "rg", tmp_path / "cache", target=triple)
    link = bindir / "lib" / "libfoo.so"
    assert link.is_symlink()
    assert os.readlink(link) == "libfoo.so.1"
    assert (bindir / "lib" / "libfoo.so.1").is_file()
    assert os.readlink(bindir / "dup") == "second"
    assert os.readlink(bindir / "sub" / "up") == "../ok"
    assert (bindir / "ok").is_file()


def test_install_nested_and_out_of_order_members(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = tar_bytes(
        [
            ("deep/nested/dir/tool", b"data\n"),
            ("deep/nested/dir/tool2", b"more\n"),
            ("deep/nested/dir/", None),
            ("deep/nested/", None),
            ("deep/", None),
            ("deep/", None),
            ("lonely/a/b/c/", None),
        ]
    )
    serve(github, "rg", "t1", triple, archive)
    entry = lock_for("rg", "t1", triple, archive)
    path = write_lock(tmp_path, entry)
    bindir = tools.install(path, "rg", tmp_path / "cache", target=triple)
    assert (bindir / "deep" / "nested" / "dir" / "tool").is_file()
    assert (bindir / "lonely" / "a" / "b" / "c").is_dir()


def test_install_member_mode_defaults_and_preserves(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as tar:
        for name, mode in (("plain", 0), ("exec", 0o755), ("odd", 0o640)):
            data = b"x\n"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            tar.addfile(info, io.BytesIO(data))
    archive = buffer.getvalue()
    serve(github, "rg", "t1", triple, archive)
    entry = lock_for("rg", "t1", triple, archive)
    path = write_lock(tmp_path, entry)
    bindir = tools.install(path, "rg", tmp_path / "cache", target=triple)
    assert (bindir / "plain").stat().st_mode & 0o777 == 0o644
    assert (bindir / "exec").stat().st_mode & 0o777 == 0o755
    assert (bindir / "odd").stat().st_mode & 0o777 == 0o640


def test_install_rejects_unreadable_archive(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = b"this is not a tar archive"
    serve(github, "rg", "t1", triple, archive)
    entry = lock_for("rg", "t1", triple, archive)
    path = write_lock(tmp_path, entry)
    with pytest.raises(tools.ToolError) as raised:
        tools.install(path, "rg", tmp_path / "cache", target=triple)
    assert str(raised.value).startswith("rg-aarch64-apple-darwin.tar.xz: not a readable tar archive:")


def test_install_rejects_hardlink_and_device_members(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as tar:
        info = tarfile.TarInfo("hard")
        info.type = tarfile.LNKTYPE
        info.linkname = "target"
        tar.addfile(info)
        info = tarfile.TarInfo("null")
        info.type = tarfile.CHRTYPE
        tar.addfile(info)
    archive = buffer.getvalue()
    serve(github, "rg", "t1", triple, archive)
    entry = lock_for("rg", "t1", triple, archive)
    path = write_lock(tmp_path, entry)
    with pytest.raises(tools.ToolError, match="unsupported member type"):
        tools.install(path, "rg", tmp_path / "cache", target=triple)


def test_install_rejects_missing_bin(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = tar_bytes([("README", b"x")])
    serve(github, "cmake", "t1", triple, archive, version="4.4.5")
    spec = tools.spec_for("cmake")
    entry = tools.ToolEntry(
        tool="cmake",
        repository=spec.repository,
        tag="t1",
        product="cmake",
        version="4.4.5",
        assets={
            triple: tools.AssetPin(
                name=spec.asset_name("4.4.5", triple),
                size=len(archive),
                sha256=hashlib.sha256(archive).hexdigest(),
            )
        },
    )
    path = write_lock(tmp_path, entry)
    with pytest.raises(tools.ToolError) as raised:
        tools.install(path, "cmake", tmp_path / "cache", target=triple)
    assert str(raised.value) == "cmake: archive lacks a single top-level directory"


def test_install_rejects_missing_bin_dir(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = tar_bytes(
        [
            ("cmake-4.4.5-x/", None),
            ("cmake-4.4.5-x/README", b"x\n"),
        ]
    )
    spec = tools.spec_for("cmake")
    name = spec.asset_name("4.4.5", triple)
    github.add_release(spec.repository, "t1", "SHA256SUMS", {name: archive})
    entry = lock_for("cmake", "t1", triple, archive, version="4.4.5")
    path = write_lock(tmp_path, entry)
    with pytest.raises(tools.ToolError) as raised:
        tools.install(path, "cmake", tmp_path / "cache", target=triple)
    assert str(raised.value) == "cmake: archive has no bin directory"


def test_install_rejects_single_symlink_top(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = tar_bytes([("only", "link:target")])
    spec = tools.spec_for("cmake")
    name = spec.asset_name("4.4.5", triple)
    github.add_release(spec.repository, "t1", "SHA256SUMS", {name: archive})
    entry = lock_for("cmake", "t1", triple, archive, version="4.4.5")
    path = write_lock(tmp_path, entry)
    with pytest.raises(tools.ToolError) as raised:
        tools.install(path, "cmake", tmp_path / "cache", target=triple)
    assert str(raised.value) == "cmake: archive lacks a single top-level directory"


def test_cache_key_is_stable_and_target_scoped(tmp_path: Path) -> None:
    darwin = flat_archive("rg", "aarch64-apple-darwin")
    musl = flat_archive("rg", "x86_64-unknown-linux-musl")
    spec = tools.spec_for("rg")
    pins = {
        "aarch64-apple-darwin": tools.AssetPin(
            name="rg-aarch64-apple-darwin.tar.xz", size=len(darwin), sha256=hashlib.sha256(darwin).hexdigest()
        ),
        "x86_64-unknown-linux-musl": tools.AssetPin(
            name="rg-x86_64-unknown-linux-musl.tar.xz", size=len(musl), sha256=hashlib.sha256(musl).hexdigest()
        ),
    }
    e = tools.ToolEntry(tool="rg", repository=spec.repository, tag="t", product="rg", version="", assets=pins)
    path = write_lock(tmp_path, e)
    key1 = tools.cache_key(path, ["rg"], target="aarch64-apple-darwin")
    key2 = tools.cache_key(path, ["rg"], target="aarch64-apple-darwin")
    key3 = tools.cache_key(path, ["rg"], target="x86_64-unknown-linux-musl")
    assert key1 == key2
    assert key1 != key3
    assert len(key1) == 64
    int(key1, 16)


# --- CLI -------------------------------------------------------------------------

TOP_HELP = """\
usage: buildlib [-h] COMMAND ...

Shared helpers for the laputa-systems build repositories.

positional arguments:
  COMMAND
    tools     install owned tools from pinned releases

options:
  -h, --help  show this help message and exit
"""

TOOLS_HELP = """\
usage: buildlib tools [-h] ACTION ...

positional arguments:
  ACTION
    lock       add or update a tools.lock.json entry from a release
    install    install tools and print one bin directory per tool
    cache-key  print a stable digest of the selected lock entries

options:
  -h, --help   show this help message and exit
"""

LOCK_HELP = """\
usage: buildlib tools lock [-h] --tag TAG [--lock LOCK] tool

positional arguments:
  tool         tool name from the buildlib tool table

options:
  -h, --help   show this help message and exit
  --tag TAG    release tag to lock, e.g. release-<sha>
  --lock LOCK  lock file path (default: tools.lock.json)
"""

INSTALL_HELP = """\
usage: buildlib tools install [-h] --lock LOCK [--cache CACHE]
                              [--target TARGET]
                              TOOL [TOOL ...]

positional arguments:
  TOOL             tool names from the lock

options:
  -h, --help       show this help message and exit
  --lock LOCK      tools.lock.json path
  --cache CACHE    cache directory (default: ~/.cache/laputa-tools)
  --target TARGET  target triple (default: this host)
"""

CACHE_KEY_HELP = """\
usage: buildlib tools cache-key [-h] --lock LOCK [--target TARGET]
                                TOOL [TOOL ...]

positional arguments:
  TOOL             tool names from the lock

options:
  -h, --help       show this help message and exit
  --lock LOCK      tools.lock.json path
  --target TARGET  target triple (default: this host)
"""


def run_cli(argv: list[str]) -> int:
    try:
        return cli.main(argv)
    except SystemExit as error:
        code = error.code
        assert isinstance(code, int)
        return code


def assert_help(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    expected: str,
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    assert run_cli(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == expected
    assert captured.err == ""


def assert_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    expected_err: str,
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    assert run_cli(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == expected_err


def test_cli_help_exact(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert_help(monkeypatch, capsys, [], TOP_HELP)
    assert_help(monkeypatch, capsys, ["--help"], TOP_HELP)
    assert_help(monkeypatch, capsys, ["tools"], TOOLS_HELP)
    assert_help(monkeypatch, capsys, ["tools", "--help"], TOOLS_HELP)
    assert_help(monkeypatch, capsys, ["tools", "lock", "--help"], LOCK_HELP)
    assert_help(monkeypatch, capsys, ["tools", "install", "--help"], INSTALL_HELP)
    assert_help(monkeypatch, capsys, ["tools", "cache-key", "--help"], CACHE_KEY_HELP)


def test_cli_usage_errors_exact(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert_usage_error(
        monkeypatch,
        capsys,
        ["bogus"],
        "usage: buildlib [-h] COMMAND ...\n"
        "buildlib: error: argument COMMAND: invalid choice: 'bogus' (choose from 'tools')\n",
    )
    assert_usage_error(
        monkeypatch,
        capsys,
        ["tools", "bogus"],
        "usage: buildlib tools [-h] ACTION ...\n"
        "buildlib tools: error: argument ACTION: invalid choice: 'bogus' "
        "(choose from 'lock', 'install', 'cache-key')\n",
    )
    assert_usage_error(
        monkeypatch,
        capsys,
        ["tools", "install"],
        "usage: buildlib tools install [-h] --lock LOCK [--cache CACHE]\n"
        "                              [--target TARGET]\n"
        "                              TOOL [TOOL ...]\n"
        "buildlib tools install: error: the following arguments are required: TOOL, --lock\n",
    )
    assert_usage_error(
        monkeypatch,
        capsys,
        ["tools", "install", "rg"],
        "usage: buildlib tools install [-h] --lock LOCK [--cache CACHE]\n"
        "                              [--target TARGET]\n"
        "                              TOOL [TOOL ...]\n"
        "buildlib tools install: error: the following arguments are required: --lock\n",
    )
    assert_usage_error(
        monkeypatch,
        capsys,
        ["tools", "lock", "rg"],
        "usage: buildlib tools lock [-h] --tag TAG [--lock LOCK] tool\n"
        "buildlib tools lock: error: the following arguments are required: --tag\n",
    )
    assert_usage_error(
        monkeypatch,
        capsys,
        ["tools", "cache-key", "rg"],
        "usage: buildlib tools cache-key [-h] --lock LOCK [--target TARGET]\n"
        "                                TOOL [TOOL ...]\n"
        "buildlib tools cache-key: error: the following arguments are required: --lock\n",
    )


def serve_rg(
    github: FakeGitHub, tag: str = "t9", triples: tuple[str, ...] = ("aarch64-apple-darwin",)
) -> dict[str, bytes]:
    archives = {triple: flat_archive("rg", triple) for triple in triples}
    assets = {f"rg-{triple}.tar.xz": archive for triple, archive in archives.items()}
    github.add_release(tools.spec_for("rg").repository, tag, "SHA256SUMS", assets)
    return archives


def test_cli_lock_install_cache_key(
    github: FakeGitHub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    darwin, musl = "aarch64-apple-darwin", "x86_64-unknown-linux-musl"
    serve_rg(github, triples=(darwin, musl))
    foreign = darwin if targets.native_target().triple == musl else musl
    lock_path = tmp_path / "tools.lock.json"
    assert cli.main(["tools", "lock", "rg", "--tag", "t9", "--lock", str(lock_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"locked rg t9 -> {lock_path}\n"
    assert captured.err == ""

    cache = tmp_path / "cache"
    assert (
        cli.main(
            [
                "tools",
                "install",
                "rg",
                "--lock",
                str(lock_path),
                "--cache",
                str(cache),
                "--target",
                foreign,
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    bindir = Path(out.strip())
    pin = tools.load_lock(lock_path).entry("rg").asset_for(foreign)
    assert out == f"{cache / 'rg' / pin.sha256}\n"
    assert (bindir / "rg").is_file()
    assert bindir.name == pin.sha256

    assert (
        cli.main(["tools", "cache-key", "rg", "--lock", str(lock_path), "--target", foreign]) == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == tools.cache_key(lock_path, ["rg"], target=foreign) + "\n"


def test_cli_lock_uses_default_lock_path(
    github: FakeGitHub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    serve_rg(github)
    assert cli.main(["tools", "lock", "rg", "--tag", "t9"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "locked rg t9 -> tools.lock.json\n"
    assert captured.err == ""
    assert tools.load_lock(tmp_path / "tools.lock.json").entry("rg").tag == "t9"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tools.lock.json"]


def test_cli_install_default_cache_xdg(
    github: FakeGitHub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    native = targets.native_target().triple
    serve_rg(github, triples=(native,))
    entry = tools.lock_entry("rg", "t9")
    lock_path = write_lock(tmp_path, entry)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    assert cli.main(["tools", "install", "rg", "--lock", str(lock_path)]) == 0
    out = capsys.readouterr().out
    pin = entry.asset_for(native)
    assert out == f"{xdg / 'laputa-tools' / 'rg' / pin.sha256}\n"
    assert (xdg / "laputa-tools" / "rg" / pin.sha256 / "rg").is_file()


def test_cli_install_default_cache_home(
    github: FakeGitHub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    native = targets.native_target().triple
    serve_rg(github, triples=(native,))
    entry = tools.lock_entry("rg", "t9")
    lock_path = write_lock(tmp_path, entry)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert cli.main(["tools", "install", "rg", "--lock", str(lock_path)]) == 0
    out = capsys.readouterr().out
    pin = entry.asset_for(native)
    expected = tmp_path / "home" / ".cache" / "laputa-tools" / "rg" / pin.sha256
    assert out == f"{expected}\n"
    assert (expected / "rg").is_file()


def test_cli_install_native_target_default(
    github: FakeGitHub,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    native = targets.native_target().triple
    other = "x86_64-unknown-linux-musl" if native == "aarch64-apple-darwin" else "aarch64-apple-darwin"
    serve_rg(github, triples=(native, other))
    entry = tools.lock_entry("rg", "t9")
    lock_path = write_lock(tmp_path, entry)
    cache = tmp_path / "cache"
    assert cli.main(["tools", "install", "rg", "--lock", str(lock_path), "--cache", str(cache)]) == 0
    out = capsys.readouterr().out.strip()
    pin = entry.asset_for(native)
    assert out == str(cache / "rg" / pin.sha256)


def test_cli_reports_errors_on_stderr_exact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "absent.json"
    assert cli.main(["tools", "install", "rg", "--lock", str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"buildlib: {missing}: cannot read: [Errno 2] No such file or directory: '{missing}'\n"
    )

    empty = tmp_path / "empty.lock.json"
    empty.write_text('{"schema": 1, "tools": {}}')
    assert cli.main(["tools", "install", "rg", "--lock", str(empty)]) == 1
    captured = capsys.readouterr()
    assert captured.err == f"buildlib: {empty}: no entry for tool 'rg'\n"

    assert cli.main(["tools", "cache-key", "rg", "--lock", str(empty)]) == 1
    captured = capsys.readouterr()
    assert captured.err == f"buildlib: {empty}: no entry for tool 'rg'\n"

    assert cli.main(["tools", "lock", "bogus", "--tag", "t", "--lock", str(tmp_path / "l.json")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "buildlib: unknown tool 'bogus' (known: "
        "['ast-grep', 'cmake', 'cpython', 'fd', 'ffmpeg-fixtures', 'ffmpeg-play-sdk', 'ffmpeg-runtime', "
        "'ffmpeg-tools', 'git', 'gluon', 'jaq', 'llvm', 'llvm-dev', 'mold', 'node', "
        "'oxish', 'rg', 'ssh'])\n"
    )


def test_cli_lock_failure_on_stderr(
    github: FakeGitHub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = tools.spec_for("rg")
    assert cli.main(["tools", "lock", "rg", "--tag", "t", "--lock", str(tmp_path / "l.json")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    url = f"https://github.com/{spec.repository}/releases/download/t/SHA256SUMS"
    assert captured.err == f"buildlib: rg: cannot fetch {url}: no such file: /{spec.repository}/releases/download/t/SHA256SUMS\n"


# --- lock document errors ---------------------------------------------------------


def rg_document() -> dict[str, Any]:
    spec = tools.spec_for("rg")
    return {
        "schema": 1,
        "tools": {
            "rg": {
                "repository": spec.repository,
                "tag": "t1",
                "product": "rg",
                "version": "",
                "assets": {
                    "aarch64-apple-darwin": {
                        "name": "rg-aarch64-apple-darwin.tar.xz",
                        "size": 1,
                        "sha256": "0" * 64,
                    }
                },
            }
        },
    }


def asset_pin() -> dict[str, Any]:
    return {"name": "rg-aarch64-apple-darwin.tar.xz", "size": 1, "sha256": "0" * 64}


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d["tools"].update(rg=5), "tools.rg: expected an object"),
        (lambda d: d["tools"]["rg"].pop("tag"), "tools.rg: missing ['tag']"),
        (lambda d: d["tools"]["rg"].pop("tag").pop("version") if False else d["tools"]["rg"].pop("version"), "tools.rg: missing ['version']"),
        (lambda d: d["tools"]["rg"].update(extra=1), "tools.rg: unexpected ['extra']"),
        (
            lambda d: d["tools"]["rg"].update(repository="evil/repo"),
            "tools.rg: repository 'evil/repo' != 'laputa-systems/rg'",
        ),
        (
            lambda d: d["tools"]["rg"].update(product="other"),
            "tools.rg: product 'other' != 'rg'",
        ),
        (
            lambda d: d["tools"]["rg"].update(tag=""),
            "tools.rg: tag must be a nonempty string",
        ),
        (
            lambda d: d["tools"]["rg"].update(tag=5),
            "tools.rg: tag must be a nonempty string",
        ),
        (
            lambda d: d["tools"]["rg"].update(version=5),
            "tools.rg: version must be a string",
        ),
        (
            lambda d: d["tools"]["rg"].update(version="1.0"),
            "tools.rg: rg is unversioned; version must be empty",
        ),
        (
            lambda d: d["tools"]["rg"].update(assets=[]),
            "tools.rg: assets must be a nonempty object",
        ),
        (
            lambda d: d["tools"]["rg"].update(assets={"aarch64-apple-darwin": 5}),
            "tools.rg.assets.aarch64-apple-darwin: expected an object",
        ),
        (
            lambda d: d["tools"]["rg"]["assets"].update(
                {"riscv64-linux": asset_pin()}
            ),
            "tools.rg.assets.riscv64-linux: unsupported target 'riscv64-linux'",
        ),
        (
            lambda d: d["tools"]["rg"]["assets"]["aarch64-apple-darwin"].pop("size"),
            "tools.rg.assets.aarch64-apple-darwin: missing ['size']",
        ),
        (
            lambda d: d["tools"]["rg"]["assets"]["aarch64-apple-darwin"].update(extra=1),
            "tools.rg.assets.aarch64-apple-darwin: unexpected ['extra']",
        ),
        (
            lambda d: d["tools"]["rg"]["assets"]["aarch64-apple-darwin"].update(name="x"),
            "tools.rg.assets.aarch64-apple-darwin: name 'x' != 'rg-aarch64-apple-darwin.tar.xz'",
        ),
        (
            lambda d: d["tools"]["rg"]["assets"]["aarch64-apple-darwin"].update(size=-1),
            "tools.rg.assets.aarch64-apple-darwin: size must be a nonnegative integer",
        ),
        (
            lambda d: d["tools"]["rg"]["assets"]["aarch64-apple-darwin"].update(sha256="Z" * 64),
            "tools.rg.assets.aarch64-apple-darwin: sha256 must be 64 lowercase hex digits",
        ),
        (
            lambda d: d["tools"]["rg"].update(version=None),
            "tools.rg: version must be a string",
        ),
        (
            lambda d: d["tools"]["rg"].update(repository=5),
            "tools.rg: repository must be a nonempty string",
        ),
        (
            lambda d: d["tools"]["rg"].update(product=5),
            "tools.rg: product must be a nonempty string",
        ),
        (
            lambda d: (
                d["tools"]["rg"].pop("tag"),
                d["tools"]["rg"].update(extra=1),
            ),
            "tools.rg: missing ['tag']; unexpected ['extra']",
        ),
        (
            lambda d: d["tools"]["rg"]["assets"]["aarch64-apple-darwin"].update(name=5),
            "tools.rg.assets.aarch64-apple-darwin: name must be a nonempty string",
        ),
        (
            lambda d: d["tools"]["rg"]["assets"]["aarch64-apple-darwin"].update(name=""),
            "tools.rg.assets.aarch64-apple-darwin: name must be a nonempty string",
        ),
    ],
)
def test_load_lock_exact_entry_errors(tmp_path: Path, mutate: Any, expected: str) -> None:
    document = rg_document()
    mutate(document)
    path = tmp_path / "tools.lock.json"
    path.write_text(json.dumps(document))
    with pytest.raises(locks.LockError) as raised:
        tools.load_lock(path)
    assert str(raised.value) == f"{path}: {expected}"


def test_load_lock_exact_top_level_errors(tmp_path: Path) -> None:
    cases = [
        ({"schema": 1, "tools": {}, "extra": 1}, "lock: unexpected ['extra']"),
        ({"schema": 1}, "lock: missing ['tools']"),
        ({"tools": {}}, "lock: missing ['schema']"),
        ({"schema": 2, "tools": {}}, "schema must be 1, found 2"),
        ({"schema": "1", "tools": {}}, "schema must be 1, found '1'"),
        ({"schema": 1, "tools": []}, "tools must be an object"),
    ]
    for document, expected in cases:
        path = tmp_path / "tools.lock.json"
        path.write_text(json.dumps(document))
        with pytest.raises(locks.LockError) as raised:
            tools.load_lock(path)
        assert str(raised.value) == f"{path}: {expected}", document

    path = tmp_path / "list.json"
    path.write_text("[]")
    with pytest.raises(locks.LockError) as raised:
        tools.load_lock(path)
    assert str(raised.value) == f"{path}: expected a JSON object"

    path = tmp_path / "bad.json"
    path.write_text("{")
    with pytest.raises(locks.LockError) as raised:
        tools.load_lock(path)
    assert str(raised.value).startswith(f"{path}: invalid JSON:")

    missing = tmp_path / "absent.json"
    with pytest.raises(locks.LockError) as raised:
        tools.load_lock(missing)
    assert str(raised.value).startswith(f"{missing}: cannot read:")


def test_load_lock_unknown_tool_exact(tmp_path: Path) -> None:
    document = rg_document()
    document["tools"]["bogus"] = document["tools"].pop("rg")
    path = tmp_path / "tools.lock.json"
    path.write_text(json.dumps(document))
    with pytest.raises(locks.LockError) as raised:
        tools.load_lock(path)
    assert str(raised.value) == (
        f"{path}: tools.bogus: unknown tool (known: {sorted(tools.TOOLS)})"
    )


def test_load_lock_versioned_and_zero_size(tmp_path: Path) -> None:
    document = rg_document()
    pin = document["tools"]["rg"]["assets"]["aarch64-apple-darwin"]
    pin["size"] = 0
    path = tmp_path / "tools.lock.json"
    path.write_text(json.dumps(document))
    loaded = tools.load_lock(path)
    assert loaded.entry("rg").asset_for("aarch64-apple-darwin").size == 0

    spec = tools.spec_for("cmake")
    document = {
        "schema": 1,
        "tools": {
            "cmake": {
                "repository": spec.repository,
                "tag": "t",
                "product": "cmake",
                "version": "",
                "assets": {
                    "aarch64-apple-darwin": {
                        "name": "cmake--aarch64-apple-darwin.tar.xz",
                        "size": 0,
                        "sha256": "0" * 64,
                    }
                },
            }
        },
    }
    path.write_text(json.dumps(document))
    with pytest.raises(locks.LockError) as raised:
        tools.load_lock(path)
    assert str(raised.value) == f"{path}: tools.cmake: version must be nonempty for cmake"


# --- lock entry generation ---------------------------------------------------------


def test_lock_entry_exact_sums_errors(github: FakeGitHub) -> None:
    spec = tools.spec_for("rg")
    repo = spec.repository
    base = f"/{repo}/releases/download"
    api = f"/repos/{repo}/releases/tags"

    def serve(tag: str, body: bytes, api_body: bytes = b"{}") -> None:
        github.files[f"{base}/{tag}/SHA256SUMS"] = body
        github.files[f"{api}/{tag}"] = api_body

    serve("t1", b"0" * 64 + b"  ok.tar.xz\nbad\n")
    with pytest.raises(locks.LockError) as raised:
        tools.lock_entry("rg", "t1")
    url = str(Path(f"https://github.com{base}/t1/SHA256SUMS"))
    assert str(raised.value) == f"{url}: line 2: malformed checksum line 'bad'"

    serve("t2", b"0" * 64 + b"  a.tar.xz\n" + b"1" * 64 + b"  a.tar.xz\n")
    with pytest.raises(locks.LockError) as raised:
        tools.lock_entry("rg", "t2")
    assert str(raised.value) == f"{url.replace('t1', 't2')}: line 2: duplicate asset 'a.tar.xz'"

    serve("t3", b"")
    with pytest.raises(locks.LockError) as raised:
        tools.lock_entry("rg", "t3")
    assert str(raised.value) == f"{url.replace('t1', 't3')}: checksum file lists no assets"


def test_lock_entry_exact_api_errors(github: FakeGitHub) -> None:
    spec = tools.spec_for("rg")
    repo = spec.repository
    name = "rg-aarch64-apple-darwin.tar.xz"
    sums = f"{'0' * 64}  {name}\n".encode()
    base = f"/{repo}/releases/download/t1/SHA256SUMS"
    github.files[base] = sums

    with pytest.raises(tools.ToolError) as raised:
        tools.lock_entry("rg", "t1")
    assert str(raised.value) == (
        f"rg: cannot fetch release API for {repo}@t1: "
        f"no such file: /repos/{repo}/releases/tags/t1"
    )

    github.files[f"/repos/{repo}/releases/tags/t1"] = b"[]"
    with pytest.raises(tools.ToolError) as raised:
        tools.lock_entry("rg", "t1")
    assert str(raised.value) == f"rg: release API has no size for asset {name!r}"

    github.files[f"/repos/{repo}/releases/tags/t1"] = json.dumps(
        {"assets": [{"name": name, "size": "big"}]}
    ).encode()
    with pytest.raises(tools.ToolError) as raised:
        tools.lock_entry("rg", "t1")
    assert str(raised.value) == f"rg: release API has no size for asset {name!r}"

    github.files[f"/repos/{repo}/releases/tags/t1"] = json.dumps(
        {"assets": [{"name": name, "size": True}]}
    ).encode()
    with pytest.raises(tools.ToolError) as raised:
        tools.lock_entry("rg", "t1")
    assert str(raised.value) == f"rg: release API has no size for asset {name!r}"


def test_lock_entry_disagreeing_versions(github: FakeGitHub) -> None:
    spec = tools.spec_for("cmake")
    repo = spec.repository
    darwin = "cmake-1.0-aarch64-apple-darwin.tar.xz"
    musl = "cmake-2.0-x86_64-unknown-linux-musl.tar.xz"
    sums = "".join(f"{'0' * 64}  {n}\n" for n in (darwin, musl)).encode()
    github.files[f"/{repo}/releases/download/t1/SHA256SUMS"] = sums
    github.files[f"/repos/{repo}/releases/tags/t1"] = json.dumps(
        {"assets": [{"name": darwin, "size": 1}, {"name": musl, "size": 2}]}
    ).encode()
    with pytest.raises(tools.ToolError) as raised:
        tools.lock_entry("cmake", "t1")
    assert str(raised.value) == "cmake t1: assets disagree on version: ['1.0', '2.0']"


def test_lock_entry_tag_and_tool_errors() -> None:
    with pytest.raises(tools.ToolError) as raised:
        tools.lock_entry("rg", "")
    assert str(raised.value) == "rg: tag must be nonempty"
    with pytest.raises(tools.ToolError) as raised:
        tools.lock_entry("nope", "t")
    assert str(raised.value) == f"unknown tool 'nope' (known: {sorted(tools.TOOLS)})"


def test_release_and_api_urls() -> None:
    assert tools._release_url("laputa-systems/rg", "release-1", "rg-x.tar.xz") == (
        "https://github.com/laputa-systems/rg/releases/download/release-1/rg-x.tar.xz"
    )
    assert tools._release_url("r", "t", "a b?c#d") == (
        "https://github.com/r/releases/download/t/a%20b%3Fc%23d"
    )
    assert tools._api_url("laputa-systems/rg", "release-1") == (
        "https://api.github.com/repos/laputa-systems/rg/releases/tags/release-1"
    )
    assert tools._api_url("r", "a b") == "https://api.github.com/repos/r/releases/tags/a%20b"
    assert tools._release_url("r", "t", "a/b") == "https://github.com/r/releases/download/t/a/b"
    assert tools._api_url("r", "a/b") == "https://api.github.com/repos/r/releases/tags/a/b"


# --- cache key and lock writing -----------------------------------------------------


def test_cache_key_exact_digest(tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive("rg", triple)
    spec = tools.spec_for("rg")
    pin = tools.AssetPin(
        name=f"rg-{triple}.tar.xz",
        size=len(archive),
        sha256=hashlib.sha256(archive).hexdigest(),
    )
    entry = tools.ToolEntry(
        tool="rg", repository=spec.repository, tag="t", product="rg", version="", assets={triple: pin}
    )
    path = write_lock(tmp_path, entry)
    canonical = json.dumps(
        [{"tool": "rg", "name": pin.name, "size": pin.size, "sha256": pin.sha256}],
        sort_keys=True,
        separators=(",", ":"),
    )
    assert tools.cache_key(path, ["rg"], target=triple) == hashlib.sha256(
        canonical.encode()
    ).hexdigest()


def test_write_lock_exact_format(tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive("rg", triple)
    spec = tools.spec_for("rg")
    entry = tools.ToolEntry(
        tool="rg",
        repository=spec.repository,
        tag="t1",
        product="rg",
        version="",
        assets={
            triple: tools.AssetPin(
                name=f"rg-{triple}.tar.xz",
                size=len(archive),
                sha256=hashlib.sha256(archive).hexdigest(),
            )
        },
    )
    path = tmp_path / "tools.lock.json"
    tools.write_lock(path, entry)
    document = {
        "schema": 1,
        "tools": {
            "rg": {
                "repository": spec.repository,
                "tag": "t1",
                "product": "rg",
                "version": "",
                "assets": {
                    triple: {
                        "name": f"rg-{triple}.tar.xz",
                        "size": len(archive),
                        "sha256": hashlib.sha256(archive).hexdigest(),
                    }
                },
            }
        },
    }
    expected = json.dumps(document, indent=2, sort_keys=True) + "\n"
    assert path.read_text() == expected


# --- install edge cases -------------------------------------------------------------


def test_install_rejects_non_directory_cache_path(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive("rg", triple)
    serve(github, "rg", "t1", triple, archive)
    entry = lock_for("rg", "t1", triple, archive)
    path = write_lock(tmp_path, entry)
    pin = entry.asset_for(triple)
    cache = tmp_path / "cache"
    final = cache / "rg" / pin.sha256

    final.parent.mkdir(parents=True)
    final.write_text("not a dir")
    with pytest.raises(tools.ToolError) as raised:
        tools.install(path, "rg", cache, target=triple)
    assert str(raised.value) == f"rg: cache path {final} is not a directory"

    final.unlink()
    final.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(tools.ToolError) as raised:
        tools.install(path, "rg", cache, target=triple)
    assert str(raised.value) == f"rg: cache path {final} is not a directory"


def test_install_replaces_partial_cache_dir(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = top_archive("cmake-4.4.5-x", triple, "cmake")
    spec = tools.spec_for("cmake")
    name = spec.asset_name("4.4.5", triple)
    github.add_release(spec.repository, "t1", "SHA256SUMS", {name: archive})
    entry = lock_for("cmake", "t1", triple, archive, version="4.4.5")
    path = write_lock(tmp_path, entry)
    pin = entry.asset_for(triple)
    cache = tmp_path / "cache"
    stale = cache / "cmake" / pin.sha256
    stale.mkdir(parents=True)
    (stale / "junk").write_text("x")
    bindir = tools.install(path, "cmake", cache, target=triple)
    assert (bindir / "cmake").is_file()
    assert not (bindir.parent / "junk").exists()


def test_install_staging_uses_cache_dir(
    github: FakeGitHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive("rg", triple)
    serve(github, "rg", "t1", triple, archive)
    entry = lock_for("rg", "t1", triple, archive)
    path = write_lock(tmp_path, entry)
    calls: list[dict[str, Any]] = []
    real_mkdtemp = __import__("tempfile").mkdtemp

    def spy(**kwargs: Any) -> str:
        calls.append(kwargs)
        result = real_mkdtemp(**kwargs)
        assert isinstance(result, str)
        return result

    monkeypatch.setattr("tempfile.mkdtemp", spy)
    tools.install(path, "rg", tmp_path / "cache", target=triple)
    assert calls == [{"dir": tmp_path / "cache" / "rg"}]


def test_install_fetch_input_fields(
    github: FakeGitHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive("rg", triple)
    serve(github, "rg", "t1", triple, archive)
    entry = lock_for("rg", "t1", triple, archive)
    path = write_lock(tmp_path, entry)
    pin = entry.asset_for(triple)
    seen: list[locks.Input] = []

    def fake_fetch(item: locks.Input, destination: Path, **kwargs: Any) -> Path:
        seen.append(item)
        Path(destination).write_bytes(archive)
        return Path(destination)

    monkeypatch.setattr(tools.locks, "fetch_input", fake_fetch)
    tools.install(path, "rg", tmp_path / "cache", target=triple)
    spec = tools.spec_for("rg")
    assert len(seen) == 1
    item = seen[0]
    assert item.name == pin.name
    assert item.sha256 == pin.sha256
    assert item.size == pin.size
    assert item.version == entry.tag
    assert item.urls == (
        f"https://github.com/{spec.repository}/releases/download/t1/{pin.name}",
    )


def test_install_versioned_entry_uses_version(
    github: FakeGitHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triple = "aarch64-apple-darwin"
    archive = top_archive("cmake-4.4.5-x", triple, "cmake")
    spec = tools.spec_for("cmake")
    name = spec.asset_name("4.4.5", triple)
    github.add_release(spec.repository, "t1", "SHA256SUMS", {name: archive})
    entry = lock_for("cmake", "t1", triple, archive, version="4.4.5")
    path = write_lock(tmp_path, entry)
    seen: list[locks.Input] = []

    def fake_fetch(item: locks.Input, destination: Path, **kwargs: Any) -> Path:
        seen.append(item)
        Path(destination).write_bytes(archive)
        return Path(destination)

    monkeypatch.setattr(tools.locks, "fetch_input", fake_fetch)
    tools.install(path, "cmake", tmp_path / "cache", target=triple)
    assert seen[0].version == "4.4.5"


def test_install_cache_appears_during_install(
    github: FakeGitHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triple = "aarch64-apple-darwin"
    archive = top_archive("cmake-4.4.5-x", triple, "cmake")
    spec = tools.spec_for("cmake")
    name = spec.asset_name("4.4.5", triple)
    github.add_release(spec.repository, "t1", "SHA256SUMS", {name: archive})
    entry = lock_for("cmake", "t1", triple, archive, version="4.4.5")
    path = write_lock(tmp_path, entry)
    pin = entry.asset_for(triple)
    cache = tmp_path / "cache"
    final = cache / "cmake" / pin.sha256

    def appear(binaries: Path, tool_spec: tools.ToolSpec, target_triple: str) -> None:
        (final / "bin").mkdir(parents=True)
        (final / "bin" / "cmake").write_text("stale")

    monkeypatch.setattr(tools, "_publish_binaries", appear)
    bindir = tools.install(path, "cmake", cache, target=triple)
    assert bindir == final / "bin"
    assert (bindir / "cmake").read_text() == "stale"


def test_install_cache_appears_invalid_during_install(
    github: FakeGitHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triple = "aarch64-apple-darwin"
    archive = top_archive("cmake-4.4.5-x", triple, "cmake")
    spec = tools.spec_for("cmake")
    name = spec.asset_name("4.4.5", triple)
    github.add_release(spec.repository, "t1", "SHA256SUMS", {name: archive})
    entry = lock_for("cmake", "t1", triple, archive, version="4.4.5")
    path = write_lock(tmp_path, entry)
    pin = entry.asset_for(triple)
    cache = tmp_path / "cache"
    final = cache / "cmake" / pin.sha256

    def appear(binaries: Path, tool_spec: tools.ToolSpec, target_triple: str) -> None:
        final.mkdir(parents=True)
        (final / "partial").write_text("x")

    monkeypatch.setattr(tools, "_publish_binaries", appear)
    with pytest.raises(tools.ToolError) as raised:
        tools.install(path, "cmake", cache, target=triple)
    assert str(raised.value) == f"cmake: cache path {final} appeared during install"


def test_install_bare_symlink_payload_not_renamed(github: FakeGitHub, tmp_path: Path) -> None:
    triple = "aarch64-apple-darwin"
    archive = tar_bytes([("rg-aarch64-apple-darwin", "link:payload")])
    serve(github, "rg", "t1", triple, archive)
    entry = lock_for("rg", "t1", triple, archive)
    path = write_lock(tmp_path, entry)
    bindir = tools.install(path, "rg", tmp_path / "cache", target=triple)
    assert not (bindir / "rg").exists()
    assert not (bindir / "rg").is_symlink()
    assert (bindir / f"rg-{triple}").is_symlink()
