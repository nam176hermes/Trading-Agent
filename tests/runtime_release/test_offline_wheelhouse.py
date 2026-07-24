from __future__ import annotations

from collections.abc import Iterator
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile

import pytest

from packages.runtime_release.offline_wheelhouse import (
    MANIFEST_NAME,
    build_wheelhouse_manifest,
    verify_offline_wheelhouse,
    write_wheelhouse_manifest,
)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Use the Linux filesystem so ownership and mode checks are authoritative."""
    path = Path(tempfile.mkdtemp(prefix="offline-wheelhouse-test-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _wheel_bytes(*, name: str = "demo-package", version: str = "1.2.3") -> bytes:
    from io import BytesIO

    output = BytesIO()
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"{dist_info}/METADATA",
            "\n".join(
                (
                    "Metadata-Version: 2.4",
                    f"Name: {name}",
                    f"Version: {version}",
                    "License-Expression: MIT",
                    "",
                )
            ),
        )
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
    return output.getvalue()


def _lock_text(*, digest: str, size: int, version: str = "1.2.3") -> str:
    return f'''version = 1
revision = 3
requires-python = ">=3.11,<3.12"

[[package]]
name = "demo-package"
version = "{version}"
source = {{ registry = "https://pypi.org/simple" }}
wheels = [
    {{ url = "https://files.pythonhosted.org/packages/demo_package-{version}-py3-none-any.whl", hash = "sha256:{digest}", size = {size} }},
]
'''


def _wheelhouse(tmp_path: Path) -> tuple[Path, Path, Path]:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir(mode=0o700, parents=True)
    wheel = wheelhouse / "demo_package-1.2.3-py3-none-any.whl"
    wheel.write_bytes(_wheel_bytes())
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    lock = tmp_path / "uv.lock"
    lock.write_text(_lock_text(digest=digest, size=wheel.stat().st_size), encoding="utf-8")
    return wheelhouse, wheel, lock


def _open_for_fixture_mutation(wheelhouse: Path, *files: Path) -> None:
    wheelhouse.chmod(0o700)
    for path in files:
        path.chmod(0o600)


def _reseal_fixture(wheelhouse: Path, *files: Path) -> None:
    for path in files:
        path.chmod(0o444)
    wheelhouse.chmod(0o555)


def test_manifest_records_locked_artifact_provenance_and_tags(tmp_path: Path) -> None:
    wheelhouse, wheel, lock = _wheelhouse(tmp_path)

    document = build_wheelhouse_manifest(
        wheelhouse,
        lock,
        python_identity="CPython 3.11.15",
        downloader="pip 25.1.1",
    )

    assert document["manifest_version"] == 1
    assert document["lock_sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert document["python_identity"] == "CPython 3.11.15"
    assert document["downloader"] == "pip 25.1.1"
    assert len(document["artifacts"]) == 1
    artifact = document["artifacts"][0]
    assert artifact == {
        "artifact_type": "wheel",
        "filename": wheel.name,
        "license": "MIT",
        "package": "demo-package",
        "platform_tag": "any",
        "python_tag": "py3",
        "abi_tag": "none",
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "size": wheel.stat().st_size,
        "source_url": "https://files.pythonhosted.org/packages/demo_package-1.2.3-py3-none-any.whl",
        "version": "1.2.3",
    }
    assert len(document["aggregate_sha256"]) == 64


def test_written_manifest_verifies_sealed_wheelhouse(tmp_path: Path) -> None:
    wheelhouse, wheel, lock = _wheelhouse(tmp_path)
    wheel.chmod(0o664)
    manifest = write_wheelhouse_manifest(
        wheelhouse,
        lock,
        python_identity="CPython 3.11.15",
        downloader="pip 25.1.1",
    )

    assert manifest == wheelhouse / MANIFEST_NAME
    assert stat.S_IMODE(wheel.stat().st_mode) == 0o444
    digest = verify_offline_wheelhouse(wheelhouse, lock)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    assert digest == document["aggregate_sha256"]


def test_verifier_rejects_tamper_missing_artifact_and_lock_drift(tmp_path: Path) -> None:
    wheelhouse, wheel, lock = _wheelhouse(tmp_path)
    write_wheelhouse_manifest(
        wheelhouse,
        lock,
        python_identity="CPython 3.11.15",
        downloader="pip 25.1.1",
    )

    _open_for_fixture_mutation(wheelhouse, wheel, wheelhouse / MANIFEST_NAME)
    wheel.write_bytes(wheel.read_bytes() + b"tamper")
    _reseal_fixture(wheelhouse, wheel, wheelhouse / MANIFEST_NAME)
    with pytest.raises(ValueError, match="offline wheelhouse verification failed"):
        verify_offline_wheelhouse(wheelhouse, lock)

    wheelhouse, wheel, lock = _wheelhouse(tmp_path / "missing")
    write_wheelhouse_manifest(
        wheelhouse,
        lock,
        python_identity="CPython 3.11.15",
        downloader="pip 25.1.1",
    )
    _open_for_fixture_mutation(wheelhouse, wheel, wheelhouse / MANIFEST_NAME)
    wheel.unlink()
    _reseal_fixture(wheelhouse, wheelhouse / MANIFEST_NAME)
    with pytest.raises(ValueError, match="offline wheelhouse verification failed"):
        verify_offline_wheelhouse(wheelhouse, lock)

    wheelhouse, _wheel, lock = _wheelhouse(tmp_path / "drift")
    write_wheelhouse_manifest(
        wheelhouse,
        lock,
        python_identity="CPython 3.11.15",
        downloader="pip 25.1.1",
    )
    lock.write_text(lock.read_text().replace('version = "1.2.3"', 'version = "1.2.4"'))
    with pytest.raises(ValueError, match="offline wheelhouse verification failed"):
        verify_offline_wheelhouse(wheelhouse, lock)


def test_verifier_rejects_symlink_unexpected_file_and_writable_artifact(tmp_path: Path) -> None:
    wheelhouse, wheel, lock = _wheelhouse(tmp_path)
    write_wheelhouse_manifest(
        wheelhouse,
        lock,
        python_identity="CPython 3.11.15",
        downloader="pip 25.1.1",
    )

    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    _open_for_fixture_mutation(wheelhouse, wheel, wheelhouse / MANIFEST_NAME)
    (wheelhouse / "unsafe-link").symlink_to(outside)
    _reseal_fixture(wheelhouse, wheel, wheelhouse / MANIFEST_NAME)
    with pytest.raises(ValueError, match="offline wheelhouse verification failed"):
        verify_offline_wheelhouse(wheelhouse, lock)
    wheelhouse.chmod(0o700)
    (wheelhouse / "unsafe-link").unlink()

    unexpected = wheelhouse / "unexpected.txt"
    unexpected.write_text("unexpected", encoding="utf-8")
    _reseal_fixture(wheelhouse, wheel, wheelhouse / MANIFEST_NAME, unexpected)
    with pytest.raises(ValueError, match="offline wheelhouse verification failed"):
        verify_offline_wheelhouse(wheelhouse, lock)
    wheelhouse.chmod(0o700)
    unexpected.unlink()

    wheelhouse.chmod(0o555)
    wheel.chmod(stat.S_IRUSR | stat.S_IWUSR)
    with pytest.raises(ValueError, match="offline wheelhouse verification failed"):
        verify_offline_wheelhouse(wheelhouse, lock)


def test_verifier_rejects_non_absolute_and_missing_wheelhouse(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="offline wheelhouse verification failed"):
        verify_offline_wheelhouse(Path("relative-wheelhouse"), lock)
    with pytest.raises(ValueError, match="offline wheelhouse verification failed"):
        verify_offline_wheelhouse(tmp_path / "missing", lock)


def test_manifest_contains_no_absolute_local_path(tmp_path: Path) -> None:
    wheelhouse, _wheel, lock = _wheelhouse(tmp_path)
    manifest = write_wheelhouse_manifest(
        wheelhouse,
        lock,
        python_identity="CPython 3.11.15",
        downloader="pip 25.1.1",
    )

    content = manifest.read_text(encoding="utf-8")
    assert os.fspath(tmp_path) not in content
