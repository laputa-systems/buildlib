from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from buildlib import locks

PAYLOAD = b"upstream tarball bytes\n" * 100
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def entry(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "cpython",
        "version": "3.15.1",
        "url": "https://example.test/Python-3.15.1.tar.xz",
        "sha256": DIGEST,
    }
    base.update(changes)
    return {key: value for key, value in base.items() if value is not None}


def write(tmp_path: Path, document: Any, name: str = "sources.lock.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document) if not isinstance(document, str) else document)
    return path


def sources(*entries: Any) -> dict[str, Any]:
    return {"schema_version": 1, "inputs": list(entries)}


def test_load_sources_reads_the_contract_example(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        {
            "schema_version": 1,
            "comment": "kept by the repository",
            "inputs": [
                entry(size=len(PAYLOAD), role="source", license="Python-2.0", purpose="The distributed interpreter"),
                entry(name="mirrored", urls=["https://a.test/x", "https://b.test/x"], url=None, target="all", extra_field=[1]),
            ],
        },
    )
    lock = locks.load_sources(path)
    first, second = lock.inputs
    assert (first.name, first.version, first.sha256, first.size) == ("cpython", "3.15.1", DIGEST, len(PAYLOAD))
    assert first.urls == ("https://example.test/Python-3.15.1.tar.xz",)
    assert first.url == first.urls[0]
    assert (first.role, first.license, first.purpose, first.target) == ("source", "Python-2.0", "The distributed interpreter", None)
    assert second.urls == ("https://a.test/x", "https://b.test/x")
    assert (second.size, second.role, second.target) == (None, None, "all")
    assert dict(second.extra) == {"extra_field": [1]}
    assert lock.get("mirrored") is second
    assert lock.path == path
    field_name = "name"
    with pytest.raises(FrozenInstanceError):
        setattr(first, field_name, "other")
    assert hash(first) == hash(first)


def test_get_reports_unknown_names(tmp_path: Path) -> None:
    lock = locks.load_sources(write(tmp_path, sources(entry())))
    with pytest.raises(locks.LockError, match="no input named 'nope'"):
        lock.get("nope")


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ("{not json", "invalid JSON"),
        ([], "expected a JSON object"),
        ({"inputs": []}, "schema_version must be 1, found None"),
        ({"schema_version": 2, "inputs": []}, "schema_version must be 1, found 2"),
        ({"schema_version": True, "inputs": []}, "schema_version must be 1, found True"),
        ({"schema_version": "1", "inputs": []}, "schema_version must be 1"),
        ({"schema_version": 1}, "inputs must be a list"),
        ({"schema_version": 1, "inputs": {}}, "inputs must be a list"),
        (sources("cpython"), "inputs[0]: expected an object"),
        (sources(entry(name="")), "inputs[0]: name must be a nonempty string"),
        (sources(entry(version=None)), "input 'cpython': version must be a nonempty string"),
        (sources(entry(sha256="ABC")), "sha256 must be 64 lowercase hex digits"),
        (sources(entry(sha256=DIGEST.upper())), "sha256 must be 64 lowercase hex digits"),
        (sources(entry(url=None)), "exactly one of url and urls is required"),
        (sources(entry(urls=["https://a.test/x"])), "exactly one of url and urls is required"),
        (sources(entry(url=None, urls=[])), "urls must be a nonempty list"),
        (sources(entry(url=None, urls="https://a.test/x")), "urls must be a nonempty list"),
        (sources(entry(url=None, urls=["https://a.test/x", "https://a.test/x"])), "repeat a mirror"),
        (sources(entry(url="http://example.test/x")), "https:// string"),
        (sources(entry(url=7)), "https:// string"),
        (sources(entry(size=-1)), "size must be a nonnegative integer"),
        (sources(entry(size=True)), "size must be a nonnegative integer"),
        (sources(entry(size="12")), "size must be a nonnegative integer"),
        (sources(entry(role="")), "role must be a nonempty string"),
        (sources(entry(license=3)), "license must be a nonempty string"),
        (sources(entry(), entry()), "duplicate input 'cpython'"),
    ],
)
def test_load_sources_rejects_invalid_documents(tmp_path: Path, document: Any, reason: str) -> None:
    path = write(tmp_path, document)
    with pytest.raises(locks.LockError, match=r"sources\.lock\.json") as raised:
        locks.load_sources(path)
    assert raised.value.path == path
    assert reason in raised.value.reason
    assert str(raised.value) == f"{path}: {raised.value.reason}"


def test_unreadable_file_names_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(locks.LockError, match="cannot read") as raised:
        locks.load_sources(missing)
    assert raised.value.path == missing


def test_load_budgets_keeps_per_target_limits(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        {
            "schema_version": 1,
            "targets": {
                "aarch64-apple-darwin": {"archive_bytes": 100, "nested": {"a": 1}},
                "x86_64-unknown-linux-musl": {},
            },
        },
        "release-budgets.json",
    )
    budgets = locks.load_budgets(path)
    assert budgets.for_target("aarch64-apple-darwin")["archive_bytes"] == 100
    assert dict(budgets.for_target("x86_64-unknown-linux-musl")) == {}
    with pytest.raises(locks.LockError, match="no budget recorded for target 'aarch64-unknown-linux-musl'"):
        budgets.for_target("aarch64-unknown-linux-musl")


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ({"schema_version": 1}, "targets must be an object"),
        ({"schema_version": 1, "targets": []}, "targets must be an object"),
        ({"schema_version": 1, "targets": {"x86_64-unknown-linux-gnu": {}}}, "unsupported target"),
        ({"schema_version": 1, "targets": {"aarch64-apple-darwin": 5}}, "targets.aarch64-apple-darwin: expected an object"),
        ({"schema_version": 2, "targets": {}}, "schema_version must be 1"),
    ],
)
def test_load_budgets_rejects_invalid_documents(tmp_path: Path, document: Any, reason: str) -> None:
    path = write(tmp_path, document, "release-budgets.json")
    with pytest.raises(locks.LockError, match=reason) as raised:
        locks.load_budgets(path)
    assert raised.value.path == path


def test_load_bootstrap_checks_only_the_schema(tmp_path: Path) -> None:
    path = write(tmp_path, {"schema_version": 1, "toolchain": {"llvm": "23"}}, "bootstrap.lock.json")
    document = locks.load_bootstrap(path)
    assert document["toolchain"] == {"llvm": "23"}
    mutable: Any = document
    with pytest.raises(TypeError):
        mutable["other"] = 1
    with pytest.raises(locks.LockError, match="schema_version must be 1"):
        locks.load_bootstrap(write(tmp_path, {"schema_version": 3}, "other.json"))
    with pytest.raises(locks.LockError, match="expected a JSON object"):
        locks.load_bootstrap(write(tmp_path, "[1]", "list.json"))


class Origin:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []
        self.base = ""


@pytest.fixture
def origin() -> Iterator[Origin]:
    state = Origin()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.requests.append(self.path)
            body = state.files.get(self.path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any, **kwargs: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def item(origin: Origin, *paths: str, size: int | None = len(PAYLOAD), sha256: str = DIGEST) -> locks.Input:
    return locks.Input(
        name="tarball", version="1", sha256=sha256, urls=tuple(origin.base + path for path in paths), size=size
    )


def test_fetch_downloads_verifies_and_publishes(origin: Origin, tmp_path: Path) -> None:
    origin.files["/a"] = PAYLOAD
    destination = tmp_path / "nested" / "inputs" / "tarball.tar.xz"
    assert locks.fetch_input(item(origin, "/a"), destination) == destination
    assert destination.read_bytes() == PAYLOAD
    assert sorted(path.name for path in destination.parent.iterdir()) == ["tarball.tar.xz"]


def test_fetch_tries_urls_in_order_and_stops_at_the_first_good_one(origin: Origin, tmp_path: Path) -> None:
    origin.files["/bad"] = b"x" * len(PAYLOAD)
    origin.files["/good"] = PAYLOAD
    origin.files["/unused"] = PAYLOAD
    destination = tmp_path / "out"
    locks.fetch_input(item(origin, "/missing", "/bad", "/good", "/unused"), destination)
    assert origin.requests == ["/missing", "/bad", "/good"]
    assert destination.read_bytes() == PAYLOAD


def test_fetch_failure_lists_every_url_and_leaves_nothing_behind(origin: Origin, tmp_path: Path) -> None:
    origin.files["/short"] = PAYLOAD[:-1]
    origin.files["/long"] = PAYLOAD + b"!"
    origin.files["/wrong"] = b"y" * len(PAYLOAD)
    destination = tmp_path / "out" / "tarball"
    with pytest.raises(locks.FetchError) as raised:
        locks.fetch_input(item(origin, "/missing", "/short", "/long", "/wrong"), destination)
    message = str(raised.value)
    assert "/missing" in message
    assert f"served {len(PAYLOAD) - 1} bytes" in message
    assert "more than the pinned" in message
    assert "served sha256" in message
    assert list(destination.parent.iterdir()) == []


def test_fetch_without_a_pinned_size_still_checks_the_digest(origin: Origin, tmp_path: Path) -> None:
    origin.files["/a"] = PAYLOAD
    destination = tmp_path / "out"
    locks.fetch_input(item(origin, "/a", size=None), destination)
    assert destination.read_bytes() == PAYLOAD
    origin.files["/b"] = PAYLOAD + b"tampered"
    with pytest.raises(locks.FetchError, match="served sha256"):
        locks.fetch_input(item(origin, "/b", size=None), tmp_path / "other")


def test_fetch_skips_the_network_when_the_destination_already_verifies(origin: Origin, tmp_path: Path) -> None:
    destination = tmp_path / "out"
    destination.write_bytes(PAYLOAD)
    locks.fetch_input(item(origin, "/a"), destination)
    assert origin.requests == []


def test_fetch_replaces_a_corrupt_destination(origin: Origin, tmp_path: Path) -> None:
    origin.files["/a"] = PAYLOAD
    destination = tmp_path / "out"
    destination.write_bytes(b"corrupt")
    locks.fetch_input(item(origin, "/a"), destination)
    assert destination.read_bytes() == PAYLOAD


def test_failed_fetch_leaves_the_previous_file_and_no_scratch_files(origin: Origin, tmp_path: Path) -> None:
    destination = tmp_path / "out"
    destination.write_bytes(b"old")
    with pytest.raises(locks.FetchError):
        locks.fetch_input(item(origin, "/missing"), destination)
    assert destination.read_bytes() == b"old"
    assert [path.name for path in tmp_path.iterdir()] == ["out"]


def test_fetch_refuses_to_replace_a_symlink(origin: Origin, tmp_path: Path) -> None:
    origin.files["/a"] = PAYLOAD
    target = tmp_path / "target"
    target.write_bytes(b"precious")
    destination = tmp_path / "out"
    destination.symlink_to(target)
    with pytest.raises(locks.FetchError, match="symlink"):
        locks.fetch_input(item(origin, "/a"), destination)
    assert target.read_bytes() == b"precious"
    assert origin.requests == []


def test_unreachable_origin_is_a_fetch_error(tmp_path: Path) -> None:
    unreachable = locks.Input(name="x", version="1", sha256=DIGEST, urls=("http://127.0.0.1:9/x",), size=1)
    with pytest.raises(locks.FetchError, match="no URL served the pinned bytes"):
        locks.fetch_input(unreachable, tmp_path / "out", timeout=2.0)


def test_verify_file_checks_size_and_digest(tmp_path: Path) -> None:
    good = locks.Input(name="x", version="1", sha256=DIGEST, urls=("https://a.test/x",), size=len(PAYLOAD))
    path = tmp_path / "f"
    path.write_bytes(PAYLOAD)
    locks.verify_file(path, good)
    path.write_bytes(PAYLOAD[:-1])
    with pytest.raises(locks.FetchError, match="size"):
        locks.verify_file(path, good)
    path.write_bytes(b"z" * len(PAYLOAD))
    with pytest.raises(locks.FetchError, match="sha256"):
        locks.verify_file(path, good)
    with pytest.raises(locks.FetchError, match="not a regular file"):
        locks.verify_file(tmp_path / "missing", good)
