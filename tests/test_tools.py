from __future__ import annotations

import hashlib
import io
import json
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

    def add_release(
        self,
        repository: str,
        tag: str,
        checksum_name: str,
        assets: dict[str, bytes],
        sums: str | None = None,
    ) -> None:
        base = f"/dl/{repository}/releases/download/{tag}"
        api = f"/api/repos/{repository}/releases/tags/{tag}"
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
        if path not in self.files:
            raise OSError(f"no such file: {path}")
        return Response(self.files[path])


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(urllib.request, "urlopen", fake.open)
    monkeypatch.setattr(
        tools,
        "_release_url",
        lambda repository, tag, name: f"https://github.com/dl/{repository}/releases/download/{tag}/{name}",
    )
    monkeypatch.setattr(
        tools,
        "_api_url",
        lambda repository, tag: f"https://api.github.com/api/repos/{repository}/releases/tags/{tag}",
    )
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
        f"/dl/{spec.repository}/releases/download/{GLUON_TAG}/SHA256SUMS"
    ] = (FIXTURES / "SHA256SUMS").read_bytes()
    github.files[f"/api/repos/{spec.repository}/releases/tags/{GLUON_TAG}"] = json.dumps(
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
    github.files[f"/dl/{spec.repository}/releases/download/{MOLD_TAG}/checksums"] = (
        FIXTURES / "checksums"
    ).read_bytes()
    github.files[f"/api/repos/{spec.repository}/releases/tags/{MOLD_TAG}"] = json.dumps(
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
    with pytest.raises(tools.ToolError, match="no asset for target"):
        entry.asset_for("aarch64-apple-darwin")


def test_lock_entry_rejects_bad_releases(github: FakeGitHub) -> None:
    spec = tools.spec_for("rg")
    github.files[f"/dl/{spec.repository}/releases/download/t1/SHA256SUMS"] = b"not-a-sum\n"
    github.files[f"/api/repos/{spec.repository}/releases/tags/t1"] = b"{}"
    with pytest.raises(locks.LockError, match="malformed checksum"):
        tools.lock_entry("rg", "t1")
    github.files[f"/dl/{spec.repository}/releases/download/t2/SHA256SUMS"] = b""
    github.files[f"/api/repos/{spec.repository}/releases/tags/t2"] = b"{}"
    with pytest.raises(locks.LockError, match="lists no assets"):
        tools.lock_entry("rg", "t2")
    github.files[f"/dl/{spec.repository}/releases/download/t3/SHA256SUMS"] = (
        b"0" * 64 + b"  unrelated.bin\n"
    )
    github.files[f"/api/repos/{spec.repository}/releases/tags/t3"] = b"{}"
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
    assert (bindir / f"rg-{triple}").is_file()
    assert github.requests == [
        f"/dl/{spec.repository}/releases/download/t1/SHA256SUMS",
        f"/api/repos/{spec.repository}/releases/tags/t1",
        f"/dl/{spec.repository}/releases/download/t1/rg-{triple}.tar.xz",
    ]


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
    with pytest.raises(tools.ToolError, match="no asset for target"):
        tools.install(path, "rg", cache, target="aarch64-unknown-linux-musl")
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
    with pytest.raises(tools.ToolError, match="single top-level directory"):
        tools.install(path, "cmake", tmp_path / "cache", target=triple)


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


def test_cli_help_and_usage_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "buildlib" in capsys.readouterr().out
    assert cli.main(["tools"]) == 0
    assert "install" in capsys.readouterr().out
    with pytest.raises(SystemExit) as raised:
        cli.main(["tools", "install", "rg"])
    assert raised.value.code == 2
    with pytest.raises(SystemExit) as raised:
        cli.main(["bogus"])
    assert raised.value.code == 2


def test_cli_lock_install_cache_key(github: FakeGitHub, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    triple = "aarch64-apple-darwin"
    archive = flat_archive("rg", triple)
    spec = tools.spec_for("rg")
    github.add_release(spec.repository, "t9", "SHA256SUMS", {f"rg-{triple}.tar.xz": archive})
    lock_path = tmp_path / "tools.lock.json"
    assert cli.main(["tools", "lock", "rg", "--tag", "t9", "--lock", str(lock_path)]) == 0
    assert "locked rg t9" in capsys.readouterr().out
    cache = tmp_path / "cache"
    assert (
        cli.main(
            ["tools", "install", "rg", "--lock", str(lock_path), "--cache", str(cache), "--target", triple]
        )
        == 0
    )
    out = capsys.readouterr().out.strip()
    bindir = Path(out)
    assert bindir.is_dir()
    assert (bindir / f"rg-{triple}").is_file()
    assert (
        cli.main(["tools", "cache-key", "rg", "--lock", str(lock_path), "--target", triple]) == 0
    )
    key = capsys.readouterr().out.strip()
    assert key == tools.cache_key(lock_path, ["rg"], target=triple)


def test_cli_reports_errors_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "absent.json"
    assert cli.main(["tools", "install", "rg", "--lock", str(missing)]) == 1
    assert "buildlib:" in capsys.readouterr().err
    assert cli.main(["tools", "lock", "bogus", "--tag", "t", "--lock", str(tmp_path / "l.json")]) == 1
