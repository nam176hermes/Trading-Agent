from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "ops/phase4b/verify-release.py"
APP_COMMIT = "fdc085a05019d700ccbce59370941e2c97ef899a"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(release: Path, manifest: Path) -> tuple[str, str]:
    entries: list[dict[str, object]] = []
    for path in sorted(release.rglob("*"), key=lambda item: os.fsencode(item.relative_to(release).as_posix())):
        relative = path.relative_to(release).as_posix()
        info = path.lstat()
        if path.is_dir():
            entries.append({
                "path": relative, "type": "directory",
                "mode": f"{stat.S_IMODE(info.st_mode):04o}", "size": 0,
                "sha256": EMPTY_SHA256,
            })
        else:
            entries.append({
                "path": relative, "type": "file",
                "mode": f"{stat.S_IMODE(info.st_mode):04o}", "size": info.st_size,
                "sha256": _sha(path),
            })
    canonical_entries = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode()
    document = {
        "manifest_version": 1,
        "release_type": "phase4-app",
        "git_commit": APP_COMMIT,
        "python_identity": "CPython 3.11.15",
        "entries": entries,
        "aggregate_sha256": hashlib.sha256(canonical_entries).hexdigest(),
    }
    canonical = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    raw = canonical + b"\n"
    manifest.write_bytes(raw)
    manifest.chmod(0o644)
    return hashlib.sha256(canonical).hexdigest(), hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, str]:
    release = tmp_path / "release"
    interpreter = release / ".venv/bin/python3.11"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)
    (release / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    release.chmod(0o755)
    for directory in (release / ".venv", release / ".venv/bin"):
        directory.chmod(0o755)
    manifest = tmp_path / "manifest.json"
    canonical, raw = _manifest(release, manifest)
    return release, manifest, canonical, raw


def _run(release: Path, manifest: Path, canonical: str, raw: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "/usr/bin/python3", "-I", str(VERIFIER), str(release), str(manifest),
            canonical, raw, "--commit", APP_COMMIT,
            "--python-identity", "CPython 3.11.15", "--release-type", "phase4-app",
            "--uid", str(os.geteuid()), "--gid", str(os.getegid()),
            "--manifest-mode", "0644",
        ],
        capture_output=True, text=True,
    )


def test_standalone_stdlib_verifier_accepts_exact_complete_release(native_tmp_path: Path) -> None:
    release, manifest, canonical, raw = _fixture(native_tmp_path)
    result = _run(release, manifest, canonical, raw)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "release verification passed"


@pytest.mark.parametrize(
    "mutation", ("bin_true", "missing", "extra", "symlink", "hardlink", "special", "mode"),
)
def test_standalone_verifier_rejects_every_tree_mutation_without_disclosure(
    native_tmp_path: Path, mutation: str,
) -> None:
    release, manifest, canonical, raw = _fixture(native_tmp_path)
    if mutation == "bin_true":
        shutil.copyfile("/bin/true", release / ".venv/bin/python3.11")
    elif mutation == "missing":
        (release / "app.py").unlink()
    elif mutation == "extra":
        (release / "extra.py").write_text("unexpected\n")
    elif mutation == "symlink":
        (release / "link").symlink_to("app.py")
    elif mutation == "hardlink":
        os.link(release / "app.py", release / "hardlink.py")
    elif mutation == "special":
        os.mkfifo(release / "fifo", 0o600)
    else:
        (release / "app.py").chmod(0o666)
    result = _run(release, manifest, canonical, raw)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "release verification rejected"
    combined = result.stdout + result.stderr
    assert str(release) not in combined
    assert canonical not in combined and raw not in combined


@pytest.mark.parametrize("field", ("canonical", "raw", "commit", "identity", "type"))
def test_standalone_verifier_rejects_wrong_external_authority(native_tmp_path: Path, field: str) -> None:
    release, manifest, canonical, raw = _fixture(native_tmp_path)
    command = [
        "/usr/bin/python3", "-I", str(VERIFIER), str(release), str(manifest),
        "f" * 64 if field == "canonical" else canonical,
        "e" * 64 if field == "raw" else raw,
        "--commit", "0" * 40 if field == "commit" else APP_COMMIT,
        "--python-identity", "CPython 3.11.14" if field == "identity" else "CPython 3.11.15",
        "--release-type", "phase4-backend" if field == "type" else "phase4-app",
        "--uid", str(os.geteuid()), "--gid", str(os.getegid()), "--manifest-mode", "0644",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    assert result.stderr.strip() == "release verification rejected"
@pytest.fixture
def native_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="phase4b-standalone-", dir="/home/thenam176/.cache",
    ) as raw:
        yield Path(raw)
