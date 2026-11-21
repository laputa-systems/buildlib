import hashlib
import os
import shutil
import subprocess
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "bootstrap" / "buildlib-run"


@pytest.fixture
def fixture(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(SCRIPT, scripts / "buildlib-run")
    data = tmp_path / "data"
    data.mkdir()
    payload = tmp_path / "payload" / "python" / "bin"
    payload.mkdir(parents=True)
    python = payload / "python3.15"
    python.write_text('#!/bin/sh\nif [ "$2" != "import sys" ]; then echo executed; fi\n')
    python.chmod(0o755)
    archive = data / "python.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        tar.add(payload.parent, arcname="python")
    uv_dir = tmp_path / "uv-fixture" / "uv-test"
    uv_dir.mkdir(parents=True)
    uv = uv_dir / "uv"
    uv.write_text(f'#!/bin/sh\nif [ "$2" = find ]; then echo "{python}"; fi\n')
    uv.chmod(0o755)
    uv_archive = data / "uv.tar.gz"
    with tarfile.open(uv_archive, "w:gz") as tar:
        tar.add(uv_dir, arcname="uv-test")
    wheel = data / "wheel.whl"
    wheel.write_bytes(b"wheel fixture")
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    handler = partial(SimpleHTTPRequestHandler, directory=str(data))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "uname").write_text('#!/bin/sh\ncase "$1" in -s) echo Darwin;; -m) echo arm64;; esac\n')
    (bindir / "uname").chmod(0o755)
    real_curl = shutil.which("curl")
    wrapper = bindir / "curl"
    wrapper.write_text(
        '#!/bin/sh\nout=\nwhile [ "$#" -gt 0 ]; do\n'
        'case "$1" in -o) out=$2; shift 2;; https://fixture/*) name=${1#https://fixture/}; shift;; '
        '--proto|--proto-redir|--retry) shift 2;; *) shift;; esac\ndone\n'
        f'exec "{real_curl}" -fsS -o "$out" "http://127.0.0.1:{server.server_port}/$name"\n'
    )
    wrapper.chmod(0o755)
    lock = scripts / "python.lock"
    lock.write_text(
        f"release fixture\nuv_python 3.15.2\nwheel https://fixture/wheel.whl {digest(wheel)}\n"
        f"python aarch64-apple-darwin https://fixture/python.tar.xz {archive.stat().st_size} {digest(archive)}\n"
        f"uv aarch64-apple-darwin https://fixture/uv.tar.gz {uv_archive.stat().st_size} {digest(uv_archive)}\n"
    )
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", BUILDLIB_CACHE=str(tmp_path / "cache"))
    try:
        yield scripts, lock, env, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run(fixture):
    scripts, _, env, _ = fixture
    return subprocess.run(["sh", str(scripts / "buildlib-run"), "--", "-c", "pass"], env=env, capture_output=True, text=True)


def test_rejects_archive_digest_and_size(fixture):
    _, lock, _, _ = fixture
    original = lock.read_text()
    fields = original.splitlines()[-2].split()
    fields[-1] = "0" * 64
    lock.write_text("\n".join([*original.splitlines()[:-2], " ".join(fields), original.splitlines()[-1]]) + "\n")
    result = run(fixture)
    assert result.returncode != 0
    assert "sha256 mismatch" in result.stderr
    assert "executed" not in result.stdout
    fields[-1] = original.splitlines()[-2].split()[-1]
    fields[-2] = "1"
    lock.write_text("\n".join([*original.splitlines()[:-2], " ".join(fields), original.splitlines()[-1]]) + "\n")
    result = run(fixture)
    assert result.returncode != 0
    assert "size mismatch" in result.stderr
    assert "executed" not in result.stdout


def test_unsupported_host(fixture):
    _, _, env, _ = fixture
    uname = Path(env["PATH"].split(":")[0]) / "uname"
    uname.write_text('#!/bin/sh\necho unsupported\n')
    result = run(fixture)
    assert result.returncode != 0
    assert "unsupported host" in result.stderr


def test_concurrent_publish_and_offline_cache(fixture):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run(fixture), range(4)))
    for result in results:
        assert result.returncode == 0 and "executed" in result.stdout, (result.returncode, result.stdout, result.stderr)
    _, _, env, _ = fixture
    curl = Path(env["PATH"].split(":")[0]) / "curl"
    curl.write_text("#!/bin/sh\nexit 1\n")
    result = run(fixture)
    assert result.returncode == 0
    assert "executed" in result.stdout
    cache = Path(env["BUILDLIB_CACHE"])
    assert not list(cache.glob(".buildlib-run.*"))
    assert not list(cache.glob("*.lock"))


def test_uv_selection_and_rejects_tampering(fixture):
    _, lock, env, _ = fixture
    env["BUILDLIB_PYTHON"] = "uv"
    original = lock.read_text()
    fields = original.splitlines()[-1].split()
    fields[-1] = "0" * 64
    lock.write_text("\n".join([*original.splitlines()[:-1], " ".join(fields)]) + "\n")
    result = run(fixture)
    assert result.returncode != 0
    assert "sha256 mismatch" in result.stderr
    assert "executed" not in result.stdout
    lock.write_text(original)
    result = run(fixture)
    assert result.returncode == 0, result.stderr
    assert "using uv" in result.stderr
    assert "executed" in result.stdout


def test_relocation_failure_falls_back_and_retains_archive(fixture):
    scripts, lock, env, _ = fixture
    failed_python = scripts.parent / "failed" / "python" / "bin"
    failed_python.mkdir(parents=True)
    executable = failed_python / "python3.15"
    executable.write_text('#!/bin/sh\necho "Error relocating: symbol not found" >&2\nexit 1\n')
    executable.chmod(0o755)
    archive = scripts.parent / "data" / "python.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        tar.add(failed_python.parent, arcname="python")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    records = lock.read_text().splitlines()
    fields = records[-2].split()
    fields[-2:] = [str(archive.stat().st_size), digest]
    records[-2] = " ".join(fields)
    lock.write_text("\n".join(records) + "\n")
    result = run(fixture)
    assert result.returncode == 0, result.stderr
    assert "using uv" in result.stderr
    assert "executed" in result.stdout
    assert (Path(env["BUILDLIB_CACHE"]) / digest / "python" / "bin" / "python3.15").is_file()
