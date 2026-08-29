from __future__ import annotations

from dataclasses import replace
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
from packages.runtime_release.v2 import paper_command_manifest
from services.job_worker import command_registry as module
from services.job_worker.command_registry import CommandRegistryError


def _authority() -> RuntimeAuthorityV2:
    commit = "a" * 40
    root = Path(f"/opt/trading-agent-v2/releases/{commit}")
    backend = root / "backend"
    executable = backend / ".venv/bin/python3.11"

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
        command_manifest=paper_command_manifest(root),
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


def _worker_authority(authority: RuntimeAuthorityV2) -> module.WorkerRuntimeAuthority:
    return module.WorkerRuntimeAuthority(
        application_revision=authority.source_commit,
        backend_revision=authority.source_commit,
        safety_snapshot_path=authority.safety.snapshot_path,
        safety_exporter_commit=authority.safety.exporter_commit,
        safety_source_fingerprint=authority.safety.source_fingerprint,
        semantic_evidence=authority.semantic_evidence,
        authority_identity=(11, 13),
        authority_document_sha256="7" * 64,
        authority_pin=authority._authority_pin,
        runtime_paths=authority.runtime_paths,
        runtime_authority=authority,
    )


def test_staging_dynamic_refresh_reuses_exact_static_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = replace(_authority(), scope="PACKAGE6_STAGING_ONLY")
    refreshed = replace(
        original,
        safety_evidence="e" * 64,
        _dynamic_evidence_pin=("e" * 64, "1" * 64, "2" * 64),
    )
    calls: list[RuntimeAuthorityV2] = []
    monkeypatch.setattr(
        module,
        "_refresh_runtime_authority_v2",
        lambda selected: calls.append(selected) or refreshed,
    )
    monkeypatch.setattr(module, "_runtime_python_path", lambda: original.application_python)
    monkeypatch.setattr(
        module,
        "_attest_application_release_v2",
        lambda _selected: (_ for _ in ()).throw(AssertionError("stage rewalk")),
    )

    result = module.refresh_staging_worker_runtime_authority(
        _worker_authority(original)
    )

    assert calls == [original, refreshed]
    assert result.runtime_authority is refreshed
    assert result.authority_pin == original._authority_pin


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_tree", "9" * 40),
        ("application_python", Path("/tmp/other-python")),
        ("backend_artifact_sha256", "9" * 64),
        ("package6_approval_sha256", "9" * 64),
        ("_authority_pin", ((99, 99), "9" * 64)),
    ],
)
def test_staging_dynamic_refresh_rejects_static_authority_drift(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    original = replace(_authority(), scope="PACKAGE6_STAGING_ONLY")
    changed = replace(original, **{field: value})
    monkeypatch.setattr(module, "_refresh_runtime_authority_v2", lambda _old: changed)
    monkeypatch.setattr(module, "_runtime_python_path", lambda: original.application_python)

    with pytest.raises(CommandRegistryError) as raised:
        module.refresh_staging_worker_runtime_authority(_worker_authority(original))

    assert raised.value.reason_code == "RUNTIME_AUTHORITY_CHANGED"


def test_staging_dynamic_refresh_rejects_rotating_evidence_during_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = replace(_authority(), scope="PACKAGE6_STAGING_ONLY")
    first = replace(original, _dynamic_evidence_pin=("1" * 64, "2" * 64, "3" * 64))
    second = replace(original, _dynamic_evidence_pin=("4" * 64, "5" * 64, "6" * 64))
    values = iter((first, second))
    monkeypatch.setattr(module, "_refresh_runtime_authority_v2", lambda _old: next(values))
    monkeypatch.setattr(module, "_runtime_python_path", lambda: original.application_python)

    with pytest.raises(CommandRegistryError) as raised:
        module.refresh_staging_worker_runtime_authority(_worker_authority(original))

    assert raised.value.reason_code == "RUNTIME_AUTHORITY_CHANGED"
