from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

SHIM = Path(__file__).resolve().parent.parent / "bootstrap"

FAKE_PYTHON = '#!/bin/sh\nprintf "python %s|%s\\n" "$PYTHONPATH" "$*"\n'
FAKE_UV = """#!/bin/sh
case "$1 $2" in
    "python install") exit 0 ;;
    "python find")
        mkdir -p "$UV_PYTHON_INSTALL_DIR/bin"
        printf '%s' '#!/bin/sh
printf "uv-python %s|%s\\\\n" "$PYTHONPATH" "$*"
' > "$UV_PYTHON_INSTALL_DIR/bin/python3"
        chmod +x "$UV_PYTHON_INSTALL_DIR/bin/python3"
        printf '%s\\n' "$UV_PYTHON_INSTALL_DIR/bin/python3" ;;
    *) exit 2 ;;
esac
"""
TARGETS = ("x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl", "aarch64-apple-darwin")


def archive(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return path


def row(kind: str, target: str, version: str, path: Path, digest: str | None = None) -> str:
    data = path.read_bytes()
    return f"{kind} {target} {version} {len(data)} {digest or hashlib.sha256(data).hexdigest()} {path.as_uri()}\n"


def install(tmp_path: Path, *, wheel_digest: str | None = None) -> Path:
    """Copy the shim beside a pins file whose inputs are local fake archives."""
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    python = archive(inputs / "cpython.tar.gz", {"python/bin/python3": FAKE_PYTHON})
    uv = archive(inputs / "uv.tar.gz", {"uv-dir/uv": FAKE_UV})
    wheel = inputs / "buildlib.whl"
    wheel.write_bytes(b"wheel bytes")
    shim = tmp_path / "repo" / "bootstrap"
    shim.mkdir(parents=True)
    shutil.copy2(SHIM / "python", shim / "python")
    pins = "".join(row("cpython", target, "3.15.1", python) for target in TARGETS)
    pins += "".join(row("uv", target, "0.13.9", uv) for target in TARGETS[:2])
    pins += row("buildlib", "any", "0.1.0", wheel, wheel_digest)
    (shim / "pins").write_text(pins)
    return shim / "python"


def run(shim: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    return subprocess.run([str(shim), *args], env=env, capture_output=True, text=True, check=False)


def test_runs_a_pinned_python_with_the_buildlib_wheel_on_pythonpath(tmp_path: Path) -> None:
    shim = install(tmp_path)
    result = run(shim, tmp_path, "-c", "pass")
    assert result.returncode == 0, result.stderr
    which, rest = result.stdout.split(" ", 1)
    assert which in ("python", "uv-python")
    pythonpath, args = rest.rstrip("\n").split("|")
    assert Path(pythonpath).name.endswith("-buildlib.whl")
    assert Path(pythonpath).read_bytes() == b"wheel bytes"
    assert args == "-c pass"


def test_reuses_verified_downloads_when_the_source_is_gone(tmp_path: Path) -> None:
    shim = install(tmp_path)
    first = run(shim, tmp_path, "one")
    shutil.rmtree(tmp_path / "inputs")
    second = run(shim, tmp_path, "two")
    assert first.returncode == second.returncode == 0, second.stderr
    assert second.stdout.rstrip("\n").endswith("|two")


def test_rejects_a_download_that_does_not_match_its_pin(tmp_path: Path) -> None:
    shim = install(tmp_path, wheel_digest="0" * 64)
    result = run(shim, tmp_path)
    assert result.returncode == 1
    assert "did not serve the pinned size and sha256" in result.stderr
    assert result.stdout == ""
    downloads = tmp_path / "cache" / "buildlib-bootstrap" / "downloads"
    assert not [path.name for path in downloads.iterdir() if "buildlib" in path.name]


def test_names_a_missing_pin(tmp_path: Path) -> None:
    shim = install(tmp_path)
    pins = shim.parent / "pins"
    pins.write_text("".join(line for line in pins.read_text().splitlines(keepends=True) if not line.startswith("buildlib ")))
    result = run(shim, tmp_path)
    assert result.returncode == 1
    assert "pins no buildlib for any" in result.stderr
