from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import shutil
import tempfile

import pytest


@pytest.fixture
def tmp_path():
    path = Path(tempfile.mkdtemp(prefix="task2-authority-", dir="/home/thenam176/.cache"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


APP_COMMIT = "a" * 40
BACKEND_COMMIT = "b" * 40


def _document() -> dict[str, object]:
    from packages.safety_evidence import CANONICAL_SAFETY_SOURCE_ROOT, safety_source_fingerprint

    base = "/opt/trading-agent-phase4"
    app_root = f"{base}/releases/app-{APP_COMMIT}"
    backend_root = f"{base}/releases/backend-{BACKEND_COMMIT}"
    manifests = f"{base}/manifests"
    return {
        "manifest_version": 1,
        "application": {
            "git_commit": APP_COMMIT,
            "release_root": app_root,
            "manifest_path": f"{manifests}/app-{APP_COMMIT}.manifest.json",
            "manifest_sha256": "1" * 64,
            "python_path": f"{app_root}/.venv/bin/python3.11",
            "python_identity": "CPython 3.11.13",
        },
        "backend": {
            "git_commit": BACKEND_COMMIT,
            "release_root": backend_root,
            "manifest_path": f"{manifests}/backend-{BACKEND_COMMIT}.manifest.json",
            "manifest_sha256": "2" * 64,
            "python_path": f"{backend_root}/.venv/bin/python3.11",
            "python_identity": "CPython 3.11.13",
        },
        "command_manifest": {
            "path": f"{manifests}/commands-{BACKEND_COMMIT}.json",
            "sha256": "3" * 64,
        },
        "semantic": {
            "authority_path": "/etc/trading-agent/research-input-manifests/phase4-v1.json",
            "policy_sha256": "4" * 64,
        },
        "safety": {
            "exporter_commit": APP_COMMIT,
            "snapshot_path": f"/run/user/{os.geteuid()}/trading-agent/safety-state.json",
            "source_fingerprint": safety_source_fingerprint(CANONICAL_SAFETY_SOURCE_ROOT),
        },
    }


def _canonical(document: dict[str, object]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def _install(tmp_path: Path, monkeypatch, raw: bytes | None = None):
    from packages.runtime_release import config

    root = tmp_path / "authority-root"
    root.mkdir(mode=0o755)
    path = root / "phase4-authority.json"
    if raw is not None:
        path.write_bytes(raw)
        path.chmod(0o444)
    monkeypatch.setattr(config, "AUTHORITY_PATH", path)
    monkeypatch.setattr(config, "_EXPECTED_UID", os.getuid())
    monkeypatch.setattr(config, "_EXPECTED_GID", os.getgid())
    monkeypatch.setattr(
        config,
        "_safe_directory",
        lambda metadata: stat.S_ISDIR(metadata.st_mode),
    )
    return path


def test_valid_exact_authority_is_descriptor_anchored_and_recheckable(tmp_path, monkeypatch):
    from packages.runtime_release.config import load_runtime_authority

    _install(tmp_path, monkeypatch, _canonical(_document()))
    authority = load_runtime_authority()

    assert authority.application.git_commit == APP_COMMIT
    assert authority.backend.git_commit == BACKEND_COMMIT
    assert authority.recheck() is authority


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b'{"manifest_version":1,"manifest_version":1}\n',
        _canonical({**_document(), "unexpected": True}),
        json.dumps(_document(), indent=2).encode() + b"\n",
        b" " * (64 * 1024 + 1),
    ],
    ids=["malformed", "duplicate-key", "extra-key", "noncanonical", "oversized"],
)
def test_invalid_authority_documents_fail_closed_without_disclosure(tmp_path, monkeypatch, raw):
    from packages.runtime_release.config import ProtectedAuthorityError, load_runtime_authority

    path = _install(tmp_path, monkeypatch, raw)
    with pytest.raises(ProtectedAuthorityError) as raised:
        load_runtime_authority()

    rendered = repr(raised.value) + str(raised.value)
    assert str(path) not in rendered
    assert "1" * 64 not in rendered


def test_missing_writable_symlink_and_misowned_authority_fail_closed(tmp_path, monkeypatch):
    from packages.runtime_release import config

    path = _install(tmp_path, monkeypatch)
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()

    path.write_bytes(_canonical(_document()))
    path.chmod(0o644)
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()

    path.unlink()
    target = path.with_suffix(".target")
    target.write_bytes(_canonical(_document()))
    target.chmod(0o444)
    path.symlink_to(target)
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()

    path.unlink()
    path.write_bytes(_canonical(_document()))
    path.chmod(0o444)
    monkeypatch.setattr(config, "_EXPECTED_UID", os.getuid() + 1)
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()


def test_symlinked_or_writable_authority_ancestor_fails_closed(tmp_path, monkeypatch):
    from packages.runtime_release import config

    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    file = real / "phase4-authority.json"
    file.write_bytes(_canonical(_document()))
    file.chmod(0o444)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(config, "AUTHORITY_PATH", alias / file.name)
    monkeypatch.setattr(config, "_EXPECTED_UID", os.getuid())
    monkeypatch.setattr(config, "_EXPECTED_GID", os.getgid())
    monkeypatch.setattr(config, "_safe_directory", lambda metadata: stat.S_ISDIR(metadata.st_mode))
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()

    monkeypatch.setattr(config, "AUTHORITY_PATH", file)
    selected = real.stat().st_ino
    monkeypatch.setattr(
        config,
        "_safe_directory",
        lambda metadata: stat.S_ISDIR(metadata.st_mode) and metadata.st_ino != selected,
    )
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()

@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("application", "git_commit", "f" * 39),
        ("backend", "release_root", "/opt/trading-agent-phase4/releases/backend-wrong"),
        ("application", "python_identity", "Python 3.11.13"),
        ("backend", "manifest_sha256", "A" * 64),
        ("command_manifest", "sha256", "x" * 64),
        ("semantic", "authority_path", "/tmp/semantic.json"),
        ("safety", "snapshot_path", "/tmp/safety.json"),
        ("safety", "snapshot_path", f"/run/user/{os.geteuid() + 1}/trading-agent/safety-state.json"),
        ("safety", "source_fingerprint", "0" * 63),
        ("safety", "source_fingerprint", "5" * 64),
        ("safety", "exporter_commit", "c" * 40),
    ],
)
def test_wrong_commit_path_python_digest_semantic_or_safety_binding_is_rejected(
    tmp_path, monkeypatch, section, field, value
):
    from packages.runtime_release.config import ProtectedAuthorityError, load_runtime_authority

    document = _document()
    document[section][field] = value  # type: ignore[index]
    _install(tmp_path, monkeypatch, _canonical(document))

    with pytest.raises(ProtectedAuthorityError):
        load_runtime_authority()


def test_authority_never_accepts_secret_or_environment_fields(tmp_path, monkeypatch):
    from packages.runtime_release.config import ProtectedAuthorityError, load_runtime_authority

    document = _document()
    document["job_api_token"] = "secret"
    _install(tmp_path, monkeypatch, _canonical(document))
    with pytest.raises(ProtectedAuthorityError):
        load_runtime_authority()


def test_safety_path_exactly_matches_exporter_fixed_runtime_path(tmp_path, monkeypatch):
    from packages.runtime_release.config import load_runtime_authority
    from services.safety_state_exporter.exporter import DEFAULT_SNAPSHOT_PATH

    _install(tmp_path, monkeypatch, _canonical(_document()))
    assert load_runtime_authority().safety.snapshot_path == DEFAULT_SNAPSHOT_PATH


def test_recheck_rejects_inode_or_content_rotation(tmp_path, monkeypatch):
    from packages.runtime_release.config import ProtectedAuthorityError, load_runtime_authority

    path = _install(tmp_path, monkeypatch, _canonical(_document()))
    authority = load_runtime_authority()
    replacement = path.with_suffix(".new")
    replacement.write_bytes(_canonical(_document()))
    replacement.chmod(0o444)
    replacement.replace(path)

    with pytest.raises(ProtectedAuthorityError):
        authority.recheck()


def test_application_attestation_rejects_wrong_running_interpreter(monkeypatch):
    from packages.runtime_release import config

    authority = object.__new__(config.RuntimeAuthority)
    release = config.ReleaseAuthority(
        APP_COMMIT,
        Path(f"/opt/trading-agent-phase4/releases/app-{APP_COMMIT}"),
        Path(f"/opt/trading-agent-phase4/manifests/app-{APP_COMMIT}.manifest.json"),
        "1" * 64,
        Path(f"/opt/trading-agent-phase4/releases/app-{APP_COMMIT}/.venv/bin/python3.11"),
        "CPython 3.11.13",
    )
    object.__setattr__(authority, "application", release)
    monkeypatch.setattr(config, "load_runtime_authority", lambda: authority)
    monkeypatch.setattr(config, "verify_release", lambda *args, **kwargs: True)
    monkeypatch.setattr(config, "_runtime_python_path", lambda: Path("/usr/bin/python3.11"), raising=False)

    with pytest.raises(config.ProtectedAuthorityError) as raised:
        config.attest_application_authority()
    assert raised.value.reason_code == "APPLICATION_RELEASE_INVALID"
