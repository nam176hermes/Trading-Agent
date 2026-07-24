from __future__ import annotations

import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import tempfile
import traceback

import pytest

from packages.runtime_release.manifest import (
    ReleasePolicy,
    create_manifest,
    verify_release,
    write_manifest,
)


COMMIT = "1" * 40
PYTHON_IDENTITY = f"CPython {platform.python_version()}"
RELEASE_TYPE = "phase4-app"


@pytest.fixture
def tmp_path() -> Path:
    """Use the Linux filesystem so ownership and mode tests are meaningful."""
    path = Path(tempfile.mkdtemp(prefix="phase4-release-test-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _policy(**overrides: object) -> ReleasePolicy:
    values: dict[str, object] = {
        "expected_uid": os.getuid(),
        "expected_gid": os.getgid(),
        "release_type": RELEASE_TYPE,
        "expected_git_commit": COMMIT,
        "expected_python_identity": PYTHON_IDENTITY,
    }
    values.update(overrides)
    return ReleasePolicy(**values)


def _attest(root: Path, tmp_path: Path, policy: ReleasePolicy) -> tuple[Path, str]:
    entries = create_manifest(root, policy)
    manifest_path = tmp_path / "release.manifest.json"
    digest = write_manifest(
        entries,
        manifest_path,
        release_type=RELEASE_TYPE,
        git_commit=COMMIT,
        python_identity=PYTHON_IDENTITY,
    )
    return manifest_path, digest


def test_manifest_entries_are_byte_sorted_and_digest_canonical_json(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir(mode=0o755)
    (root / "z.txt").write_bytes(b"last")
    (root / "a.txt").write_bytes(b"first")
    os.chmod(root / "z.txt", 0o644)
    os.chmod(root / "a.txt", 0o640)

    entries = create_manifest(root, _policy())

    assert [entry["path"] for entry in entries] == ["a.txt", "z.txt"]
    assert entries[0] == {
        "path": "a.txt",
        "type": "file",
        "mode": "0640",
        "size": 5,
        "sha256": hashlib.sha256(b"first").hexdigest(),
    }
    aggregate = hashlib.sha256(
        json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    envelope = {
        "manifest_version": 1,
        "release_type": RELEASE_TYPE,
        "git_commit": COMMIT,
        "python_identity": PYTHON_IDENTITY,
        "entries": entries,
        "aggregate_sha256": aggregate,
    }
    canonical = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
    manifest_path = tmp_path / "manifest.json"
    assert write_manifest(
        entries,
        manifest_path,
        release_type=RELEASE_TYPE,
        git_commit=COMMIT,
        python_identity=PYTHON_IDENTITY,
    ) == hashlib.sha256(canonical).hexdigest()
    assert manifest_path.read_bytes() == canonical + b"\n"


@pytest.mark.parametrize(
    ("policy_change", "manifest_change"),
    [
        ({"expected_git_commit": "2" * 40}, {}),
        ({"release_type": "phase4-backend"}, {}),
        ({"expected_python_identity": "CPython 3.11.0"}, {}),
        ({}, {"git_commit": "2" * 40}),
        ({}, {"python_identity": "CPython 3.11.99"}),
    ],
)
def test_verify_rejects_wrong_manifest_authority(
    tmp_path: Path,
    policy_change: dict[str, str],
    manifest_change: dict[str, str],
) -> None:
    root = tmp_path / "release"
    root.mkdir(mode=0o755)
    artifact = root / "app.py"
    artifact.write_text("safe\n")
    artifact.chmod(0o644)
    policy = _policy()
    entries = create_manifest(root, policy)
    manifest_path = tmp_path / "manifest.json"
    metadata = {
        "release_type": RELEASE_TYPE,
        "git_commit": COMMIT,
        "python_identity": PYTHON_IDENTITY,
    }
    metadata.update(manifest_change)
    digest = write_manifest(entries, manifest_path, **metadata)

    with pytest.raises(ValueError, match="release verification failed"):
        verify_release(root, manifest_path, digest, _policy(**policy_change))


def test_verify_requires_external_commit_and_interpreter_authority(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir(mode=0o755)
    (root / "app.py").write_text("safe\n")
    os.chmod(root / "app.py", 0o644)
    manifest_path, digest = _attest(root, tmp_path, _policy())

    with pytest.raises(ValueError, match="release verification failed"):
        verify_release(
            root,
            manifest_path,
            digest,
            _policy(expected_git_commit=None, expected_python_identity=None),
        )


@pytest.mark.parametrize("mutation", ["missing", "modified", "extra"])
def test_verify_rejects_tree_tampering(tmp_path: Path, mutation: str) -> None:
    root = tmp_path / "release"
    root.mkdir(mode=0o755)
    artifact = root / "app.py"
    artifact.write_text("print('safe')\n")
    os.chmod(artifact, 0o644)
    policy = _policy()
    manifest_path, digest = _attest(root, tmp_path, policy)

    if mutation == "missing":
        artifact.unlink()
    elif mutation == "modified":
        artifact.write_text("print('changed')\n")
    else:
        (root / "extra.py").write_text("unexpected\n")

    with pytest.raises(ValueError, match="release verification failed"):
        verify_release(root, manifest_path, digest, policy)


def test_verify_rejects_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir(mode=0o755)
    target = root / "target"
    target.write_text("safe")
    os.chmod(target, 0o644)
    policy = _policy()
    manifest_path, digest = _attest(root, tmp_path, policy)
    target.unlink()
    target.symlink_to("/etc/passwd")

    with pytest.raises(ValueError, match="release verification failed"):
        verify_release(root, manifest_path, digest, policy)


@pytest.mark.parametrize("unsafe_mode", [0o666, 0o1644])
def test_verify_rejects_unsafe_mode(tmp_path: Path, unsafe_mode: int) -> None:
    root = tmp_path / "release"
    root.mkdir(mode=0o755)
    artifact = root / "app.py"
    artifact.write_text("safe")
    os.chmod(artifact, 0o644)
    policy = _policy()
    manifest_path, digest = _attest(root, tmp_path, policy)
    os.chmod(artifact, unsafe_mode)

    with pytest.raises(ValueError, match="release verification failed"):
        verify_release(root, manifest_path, digest, policy)


def test_manifest_refuses_special_mode_before_attestation(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir(mode=0o755)
    artifact = root / "app.py"
    artifact.write_text("safe")
    os.chmod(artifact, 0o1644)

    with pytest.raises(ValueError, match="unsafe mode"):
        create_manifest(root, _policy())


def test_verify_rejects_wrong_owner(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir(mode=0o755)
    artifact = root / "app.py"
    artifact.write_text("safe")
    os.chmod(artifact, 0o644)
    build_policy = _policy()
    manifest_path, digest = _attest(root, tmp_path, build_policy)
    verify_policy = _policy(expected_uid=os.getuid() + 1)

    with pytest.raises(ValueError, match="release verification failed"):
        verify_release(root, manifest_path, digest, verify_policy)


def test_verify_errors_do_not_disclose_paths_digests_or_contents(tmp_path: Path) -> None:
    secret_path = "customer-token-DO-NOT-LOG.txt"
    secret_content = "sk-live-DO-NOT-LOG"
    empty_entries: list[dict[str, object]] = []
    root = tmp_path / "release"
    root.mkdir(mode=0o755)
    (root / secret_path).write_text(secret_content)
    os.chmod(root / secret_path, 0o644)
    manifest_path = tmp_path / "manifest.json"
    expected_digest = write_manifest(
        empty_entries,
        manifest_path,
        release_type=RELEASE_TYPE,
        git_commit=COMMIT,
        python_identity=PYTHON_IDENTITY,
    )
    root.rename(tmp_path / "renamed-release")

    with pytest.raises(ValueError) as caught:
        verify_release(root, manifest_path, expected_digest, _policy())

    message = str(caught.value)
    assert message == "release verification failed"
    assert secret_path not in message
    assert secret_content not in message
    assert expected_digest not in message
    assert str(root) not in message
    formatted = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert secret_path not in formatted
    assert secret_content not in formatted
    assert expected_digest not in formatted
    assert str(root) not in formatted
