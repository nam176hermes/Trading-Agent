from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.runtime_release.config import (
    RuntimeAuthorityV2,
    RuntimePathsV2,
    SafetyAuthority,
    SemanticAuthority,
)
from packages.runtime_release.semantic import SemanticEvidence
from services.job_worker import command_registry as module
from services.job_worker.command_registry import CommandRegistryError


def _canonical_fragment(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _authority() -> RuntimeAuthorityV2:
    commit = "a" * 40
    root = Path(f"/opt/trading-agent-v2/releases/{commit}")
    backend = root / "backend"
    executable = backend / ".venv/bin/python3.11"
    commands = [
        {
            "argv": [
                str(executable),
                "-I",
                "-B",
                "main.py",
                "--mode",
                "snapshot",
                "--research-only",
            ],
            "cwd": str(backend),
            "environment_policy": "EMPTY_ALLOWLIST_RESEARCH_ONLY_V1",
            "executable": str(executable),
            "job_type": "SNAPSHOT",
            "shell": False,
        }
    ]
    now = datetime(2026, 7, 16, 18, 0, tzinfo=UTC)
    semantic_evidence = SemanticEvidence(
        active_authority_sha256="1" * 64,
        version_manifest_sha256="2" * 64,
        semantic_input_fingerprint="3" * 64,
        manifest_version="semantic-v2",
        generated_at=now,
        expires_at=now + timedelta(minutes=30),
        policy_sha256="4" * 64,
    )
    return RuntimeAuthorityV2(
        source_commit=commit,
        source_tree="b" * 40,
        installation_root=root,
        application_root=root / "application",
        application_python=root / "application/.venv/bin/python3.11",
        backend_root=backend,
        backend_python=executable,
        backend_artifact_sha256="5" * 64,
        command_manifest={
            "commands": commands,
            "manifest_sha256": hashlib.sha256(_canonical_fragment(commands)).hexdigest(),
            "schema_version": 2,
        },
        safety=SafetyAuthority(
            "c" * 40,
            Path("/run/trading-agent-v2/safety-state.json"),
            "6" * 64,
        ),
        semantic=SemanticAuthority(
            Path("/etc/trading-agent-v2/research-input-manifests/active.json"),
            "4" * 64,
            Path("/var/lib/trading-agent-v2/research-input"),
        ),
        runtime_paths=RuntimePathsV2(
            safety_snapshot=Path("/run/trading-agent-v2/safety-state.json"),
            semantic_authority=Path(
                "/etc/trading-agent-v2/research-input-manifests/active.json"
            ),
            semantic_input_root=Path("/var/lib/trading-agent-v2/research-input"),
            reports_root=Path("/var/lib/trading-agent-v2/research-output/reports"),
            signals_root=Path("/var/lib/trading-agent-v2/research-output/signals"),
            scratch_root=Path("/var/lib/trading-agent-v2/research-output/scratch"),
            artifact_root=Path("/var/lib/trading-agent-v2/job-artifacts"),
        ),
        safety_evidence=object(),
        semantic_evidence=semantic_evidence,
        _authority_pin=((11, 13), "7" * 64, (17, 19), "8" * 64),
        _dynamic_evidence_pin=("9" * 64, "1" * 64),
    )


def test_production_worker_attestation_is_v2_only(monkeypatch: pytest.MonkeyPatch) -> None:
    authority = _authority()
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_load_runtime_authority_v2",
        lambda: calls.append("v2-load") or authority,
    )
    monkeypatch.setattr(
        module,
        "_attest_application_release_v2",
        lambda selected: calls.append("v2-attest") or selected is authority,
    )
    monkeypatch.setattr(module, "_runtime_python_path", lambda: authority.application_python)
    monkeypatch.setattr(RuntimeAuthorityV2, "recheck", lambda self: self)
    monkeypatch.setattr(
        module,
        "_load_runtime_authority",
        lambda: (_ for _ in ()).throw(AssertionError("v1 fallback")),
    )

    selected = module.attest_worker_runtime_authority()

    assert selected.runtime_authority is authority
    assert selected.runtime_paths == authority.runtime_paths
    assert selected.application_revision == authority.source_commit
    assert selected.backend_revision == authority.source_commit
    assert calls == ["v2-load", "v2-attest"]


def test_v2_command_manifest_must_be_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    authority = _authority()
    authority.command_manifest["commands"][0]["shell"] = True  # type: ignore[index]
    monkeypatch.setattr(module, "_load_runtime_authority_v2", lambda: authority)
    monkeypatch.setattr(module, "_attest_application_release_v2", lambda _selected: True)
    monkeypatch.setattr(module, "_runtime_python_path", lambda: authority.application_python)

    with pytest.raises(CommandRegistryError) as raised:
        module.attest_worker_runtime_authority()

    assert raised.value.reason_code == "COMMAND_MANIFEST_MISMATCH"
