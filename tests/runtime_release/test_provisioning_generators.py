from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace

import pytest
import subprocess
import sys

from packages.runtime_release.provisioning import (
    ProvisioningDocumentError,
    build_command_manifest_document,
    build_runtime_authority_document,
    canonical_document_bytes,
    publish_canonical_document,
    read_canonical_document_file,
)
from packages.safety_evidence import CANONICAL_SAFETY_SOURCE_ROOT, safety_source_fingerprint
from packages.runtime_release.semantic import semantic_policy_digest


COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
DIGEST = "d" * 64


@pytest.fixture
def linux_tmp_path() -> Path:
    path = Path(tempfile.mkdtemp(prefix="phase4-generator-", dir="/home/thenam176/.cache"))
    path.chmod(0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _release(kind: str, commit: str):
    root = Path(f"/opt/trading-agent-phase4/releases/{kind}-{commit}")
    return SimpleNamespace(
        git_commit=commit,
        release_root=root,
        manifest_path=Path(f"/opt/trading-agent-phase4/manifests/{kind}-{commit}.manifest.json"),
        manifest_sha256=DIGEST,
        python_path=root / ".venv/bin/python3.11",
        python_identity="CPython 3.11.15",
    )


def _authority():
    return SimpleNamespace(application=_release("app", COMMIT_A), backend=_release("backend", COMMIT_B))


def test_command_manifest_is_exact_code_owned_registry_document() -> None:
    document = build_command_manifest_document(_authority())

    assert tuple(document) == ("manifest_version", "backend_commit", "commands", "aggregate_sha256")
    assert tuple(document["commands"]) == ("SNAPSHOT",)
    for command in document["commands"].values():
        assert tuple(command) == (
            "executable", "cwd", "argv_prefix", "timeout_seconds",
            "max_attempts", "result_validator", "shell",
        )
        assert command["shell"] is False
        assert command["argv_prefix"][:2] == ["-I", "-B"]
        assert command["executable"] == str(_authority().backend.python_path)
        assert command["cwd"] == str(_authority().backend.release_root)
    encoded = json.dumps(document["commands"], ensure_ascii=False, separators=(",", ":")).encode()
    assert document["aggregate_sha256"] == hashlib.sha256(encoded).hexdigest()


def test_canonical_publisher_is_dry_run_by_default_and_create_only(linux_tmp_path: Path) -> None:
    output = linux_tmp_path / "commands.json"
    document = {"manifest_version": 1, "commands": {}}

    planned = publish_canonical_document(
        document, output, apply=False, expected_uid=os.getuid(), expected_gid=os.getgid(),
    )
    assert not output.exists()
    assert planned == hashlib.sha256(b'{"manifest_version":1,"commands":{}}\n').hexdigest()

    applied = publish_canonical_document(
        document, output, apply=True, expected_uid=os.getuid(), expected_gid=os.getgid(),
    )
    assert applied == planned
    assert output.read_bytes() == b'{"manifest_version":1,"commands":{}}\n'
    with pytest.raises(ProvisioningDocumentError):
        publish_canonical_document(
            document, output, apply=True, expected_uid=os.getuid(), expected_gid=os.getgid(),
        )


def test_canonical_publisher_handles_short_writes(linux_tmp_path: Path, monkeypatch) -> None:
    from packages.runtime_release import provisioning

    real_write = os.write
    monkeypatch.setattr(
        provisioning.os, "write",
        lambda fd, content: real_write(fd, bytes(content[: max(1, len(content) // 2)])),
    )
    output = linux_tmp_path / "authority.json"
    document = {"manifest_version": 1, "value": "x" * 200}

    publish_canonical_document(
        document, output, apply=True,
        expected_uid=os.getuid(), expected_gid=os.getgid(),
    )

    assert output.read_bytes() == canonical_document_bytes(document)


def test_runtime_authority_binds_every_exact_runtime_identity() -> None:
    authority = _authority()
    document = build_runtime_authority_document(
        application=authority.application,
        backend=authority.backend,
        command_manifest_path=Path(
            f"/opt/trading-agent-phase4/manifests/commands-{COMMIT_B}.json"
        ),
        command_manifest_sha256="c" * 64,
        semantic_authority_path=Path(
            "/etc/trading-agent/research-input-manifests/phase4-v1.json"
        ),
        safety_snapshot_path=Path(
            f"/run/user/{os.geteuid()}/trading-agent/safety-state.json"
        ),
        exporter_commit=COMMIT_A,
        safety_source_fingerprint=safety_source_fingerprint(CANONICAL_SAFETY_SOURCE_ROOT),
        runtime_uid=os.geteuid(),
    )

    assert document["application"]["git_commit"] == COMMIT_A
    assert document["backend"]["git_commit"] == COMMIT_B
    assert document["command_manifest"]["sha256"] == "c" * 64
    semantic_path = Path("/etc/trading-agent/research-input-manifests/phase4-v1.json")
    assert document["semantic"]["policy_sha256"] == semantic_policy_digest(COMMIT_B, semantic_path)
    assert document["safety"]["exporter_commit"] == COMMIT_A
    assert not any(
        marker in json.dumps(document).lower()
        for marker in ("token", "password", "credential", "database_url", "environment")
    )


def test_runtime_authority_rejects_wrong_backend_command_path() -> None:
    authority = _authority()
    with pytest.raises(ProvisioningDocumentError):
        build_runtime_authority_document(
            application=authority.application,
            backend=authority.backend,
            command_manifest_path=Path("/tmp/commands.json"),
            command_manifest_sha256="c" * 64,
            semantic_authority_path=Path("/etc/trading-agent/research-input-manifests/phase4-v1.json"),
            safety_snapshot_path=Path(f"/run/user/{os.geteuid()}/trading-agent/safety-state.json"),
            exporter_commit=COMMIT_A,
            safety_source_fingerprint=safety_source_fingerprint(CANONICAL_SAFETY_SOURCE_ROOT),
            runtime_uid=os.geteuid(),
        )


def test_root_generator_binds_safety_path_to_runtime_uid_not_generator_euid(monkeypatch) -> None:
    authority = _authority()
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    document = build_runtime_authority_document(
        application=authority.application, backend=authority.backend,
        command_manifest_path=Path(f"/opt/trading-agent-phase4/manifests/commands-{COMMIT_B}.json"),
        command_manifest_sha256="c" * 64,
        semantic_authority_path=Path("/etc/trading-agent/research-input-manifests/phase4-v1.json"),
        safety_snapshot_path=Path("/run/user/1000/trading-agent/safety-state.json"),
        exporter_commit=COMMIT_A,
        safety_source_fingerprint=safety_source_fingerprint(CANONICAL_SAFETY_SOURCE_ROOT),
        runtime_uid=1000,
    )

    assert document["safety"]["snapshot_path"] == "/run/user/1000/trading-agent/safety-state.json"


def test_provisioning_reader_rejects_symlink_even_with_matching_digest(linux_tmp_path: Path) -> None:
    target = linux_tmp_path / "target.json"
    raw = b'{"manifest_version":1}\n'
    target.write_bytes(raw)
    link = linux_tmp_path / "commands.json"
    link.symlink_to(target)

    with pytest.raises(ProvisioningDocumentError):
        read_canonical_document_file(link, hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize(
    "script",
    ("generate_phase4_command_manifest.py", "generate_phase4_runtime_authority.py"),
)
def test_provisioning_generator_cli_help_is_checkout_runnable(script: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).parents[2] / "scripts" / script), "--help"],
        check=True, capture_output=True, text=True,
    )
    assert "--apply" in completed.stdout
