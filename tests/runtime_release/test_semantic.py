from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path

import pytest

from packages.runtime_release.config import SemanticAuthority
from packages.runtime_release.semantic import (
    CLASSIFICATION,
    COMMAND,
    LOGICAL_FILES,
    SEMANTIC_INPUT_ROOT,
    SemanticAttestationError,
    attest_current_semantic_inputs,
    semantic_policy_digest,
    semantic_policy_digest_v2,
)


BACKEND = "b" * 40
ACTIVE_PATH = Path("/etc/trading-agent/research-input-manifests/phase4-v1.json")
NOW = datetime(2026, 7, 12, 16, 0, tzinfo=UTC)


def test_semantic_policy_can_bind_a_service_owned_v2_input_root() -> None:
    active = Path(
        "/etc/trading-agent-v2/research-input-manifests/active.json"
    )
    service_root = Path("/var/lib/trading-agent-v2/research-input")

    service_digest = semantic_policy_digest(
        "a" * 40, active, input_root=service_root
    )
    legacy_digest = semantic_policy_digest("a" * 40, active)

    assert len(service_digest) == 64
    assert service_digest != legacy_digest


def test_v2_semantic_policy_digest_exactly_matches_static_producer_binding() -> None:
    from packages.runtime_release.v2 import RUNTIME_PATHS, _producer_bindings

    commit = "a" * 40
    digest = semantic_policy_digest_v2(
        commit,
        Path(RUNTIME_PATHS["semantic_active"]),
        input_root=Path(RUNTIME_PATHS["semantic_input_root"]),
    )

    assert digest == _producer_bindings(commit)["semantic_policy_sha256"]


def test_semantic_attestation_uses_the_v2_authority_input_root(
    monkeypatch,
) -> None:
    import packages.runtime_release.semantic as module

    monkeypatch.setattr(module.os, "geteuid", lambda: 4242)
    monkeypatch.setattr(module.os, "getegid", lambda: 4343)

    service_root = Path("/var/lib/trading-agent-v2/research-input")
    active_path = Path(
        "/etc/trading-agent-v2/research-input-manifests/active.json"
    )
    active, plan, manifest = _publication("v2-root")
    plan["destination_root"] = str(service_root)
    plan["active_authority_path"] = str(active_path)
    manifest["approved_root"] = str(
        service_root / active["input_directory"]
    )
    observed: list[Path] = []

    monkeypatch.setattr(
        module,
        "read_protected_canonical_json_current",
        lambda path: (active, "f" * 64),
    )
    monkeypatch.setattr(
        module,
        "read_protected_canonical_json",
        lambda path, digest: (
            plan if path.name == active["plan_path"] else manifest
        ),
    )
    monkeypatch.setattr(
        module,
        "_read_input",
        lambda path, digest, **kwargs: observed.append(path) or (10, digest),
    )
    monkeypatch.setattr(
        module,
        "_attest_exact_tree",
        lambda root: observed.append(root),
    )
    monkeypatch.setattr(
        module,
        "_current_parent_attestations",
        lambda active, **kwargs: (
            {"device": 1, "inode": 3, "uid": 0, "gid": 0, "mode": 0o755},
            {"device": 1, "inode": 2, "uid": 0, "gid": 0, "mode": 0o755},
        ),
    )
    authority = SemanticAuthority(
        active_path,
        semantic_policy_digest_v2(
            BACKEND, active_path, input_root=service_root
        ),
        service_root,
        "release-v2-semantic-policy/v1",
    )

    evidence = attest_current_semantic_inputs(
        authority, BACKEND, clock=lambda: NOW
    )

    expected_root = service_root / active["input_directory"]
    assert evidence.manifest_version == "v2-root"
    assert expected_root in observed
    assert all(
        path == expected_root or path.is_relative_to(expected_root)
        for path in observed
    )


def _publication(version: str):
    plan_digest = hashlib.sha256(version.encode()).hexdigest()
    plan_name = f"phase4-v1.{version}.plan.json"
    manifest_name = f"phase4-v1.{version}.manifest.json"
    input_directory = f"snapshot-{version}-0123456789abcdef"
    active = {
        "schema_version": 1,
        "classification": CLASSIFICATION,
        "generated_at": NOW.isoformat(),
        "manifest_version": version,
        "manifest_path": manifest_name,
        "manifest_sha256": "1" * 64,
        "input_directory": input_directory,
        "plan_digest": plan_digest,
        "plan_path": plan_name,
        "plan_sha256": plan_digest,
    }
    attestation = {"device": 1, "inode": 2, "uid": 0, "gid": 0, "mode": 0o755}
    plan = {
        "schema_version": "phase4-semantic-publication-plan/v1",
        "classification": CLASSIFICATION,
        "command": COMMAND,
        "destination_root": str(SEMANTIC_INPUT_ROOT),
        "active_authority_path": str(ACTIVE_PATH),
        "input_parent_attestation": {**attestation, "inode": 3},
        "authority_parent_attestation": attestation,
        "manifest_version": version,
        "backend_commit": BACKEND,
        "runtime_uid": os.geteuid(),
        "runtime_gid": os.getegid(),
        "generated_at": NOW.isoformat(),
        "validity_minutes": 30,
        "sources": {
            name: {
                "path": f"/legacy/semantic/{name}.json",
                "runtime_path": path,
                "device": index + 10,
                "inode": index + 20,
                "size": 10,
                "sha256": "2" * 64,
            }
            for index, (name, path) in enumerate(LOGICAL_FILES.items())
        },
    }
    manifest = {
        "schema_version": 1,
        "manifest_version": version,
        "classification": CLASSIFICATION,
        "command": COMMAND,
        "backend_commit": BACKEND,
        "approved_root": str(SEMANTIC_INPUT_ROOT / input_directory),
        "generated_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(minutes=30)).isoformat(),
        "plan_digest": plan_digest,
        "plan_path": plan_name,
        "plan_sha256": plan_digest,
        "files": {
            name: {"path": path, "sha256": "2" * 64, "required": True, "read_only": True}
            for name, path in LOGICAL_FILES.items()
        },
    }
    return active, plan, manifest


def test_stable_policy_accepts_valid_active_rotation_without_authority_rewrite(monkeypatch):
    import packages.runtime_release.semantic as module

    monkeypatch.setattr(module.os, "geteuid", lambda: 4242)
    monkeypatch.setattr(module.os, "getegid", lambda: 4343)

    publications = [_publication("v1"), _publication("v2")]
    selected = {item[0]["manifest_version"]: item for item in publications}
    current = iter(item[0] for item in publications)

    def read_current(path):
        active = next(current)
        read_current.active = active
        return active, "f" * 64

    def read_bound(path, digest):
        version = read_current.active["manifest_version"]
        active, plan, manifest = selected[version]
        return plan if path.name == active["plan_path"] else manifest

    monkeypatch.setattr(module, "read_protected_canonical_json_current", read_current)
    monkeypatch.setattr(module, "read_protected_canonical_json", read_bound)
    monkeypatch.setattr(module, "_read_input", lambda path, digest: (10, digest))
    monkeypatch.setattr(module, "_attest_exact_tree", lambda root: None)
    monkeypatch.setattr(
        module,
        "_current_parent_attestations",
        lambda active_path: (
            {"device": 1, "inode": 3, "uid": 0, "gid": 0, "mode": 0o755},
            {"device": 1, "inode": 2, "uid": 0, "gid": 0, "mode": 0o755},
        ),
        raising=False,
    )
    authority = SemanticAuthority(ACTIVE_PATH, semantic_policy_digest(BACKEND, ACTIVE_PATH))

    first = attest_current_semantic_inputs(authority, BACKEND, clock=lambda: NOW)
    second = attest_current_semantic_inputs(authority, BACKEND, clock=lambda: NOW)

    assert first != second
    assert authority.policy_sha256 == semantic_policy_digest(BACKEND, ACTIVE_PATH)


def test_attestation_exposes_every_exact_dynamic_semantic_identity(monkeypatch):
    import packages.runtime_release.semantic as module

    monkeypatch.setattr(module.os, "geteuid", lambda: 4242)
    monkeypatch.setattr(module.os, "getegid", lambda: 4343)

    active, plan, manifest = _publication("v1")
    active_sha256 = "f" * 64
    monkeypatch.setattr(
        module,
        "read_protected_canonical_json_current",
        lambda path: (active, active_sha256),
    )
    monkeypatch.setattr(
        module,
        "read_protected_canonical_json",
        lambda path, digest: plan if path.name == active["plan_path"] else manifest,
    )
    monkeypatch.setattr(module, "_read_input", lambda path, digest: (10, digest))
    monkeypatch.setattr(module, "_attest_exact_tree", lambda root: None)
    monkeypatch.setattr(
        module,
        "_current_parent_attestations",
        lambda active_path: (
            {"device": 1, "inode": 3, "uid": 0, "gid": 0, "mode": 0o755},
            {"device": 1, "inode": 2, "uid": 0, "gid": 0, "mode": 0o755},
        ),
    )
    authority = SemanticAuthority(
        ACTIVE_PATH, semantic_policy_digest(BACKEND, ACTIVE_PATH),
    )

    evidence = attest_current_semantic_inputs(authority, BACKEND, clock=lambda: NOW)
    expected_fingerprint = hashlib.sha256(
        module._canonical(
            {
                "manifest_sha256": active["manifest_sha256"],
                "input_version": active["manifest_version"],
                "files": manifest["files"],
            }
        )
    ).hexdigest()

    assert evidence.active_authority_sha256 == active_sha256
    assert evidence.version_manifest_sha256 == active["manifest_sha256"]
    assert evidence.semantic_input_fingerprint == expected_fingerprint
    assert evidence.manifest_version == "v1"
    assert evidence.generated_at == NOW
    assert evidence.expires_at == NOW + timedelta(minutes=30)
    assert evidence.policy_sha256 == authority.policy_sha256


def test_semantic_attestation_rejects_runtime_identity_drift(monkeypatch) -> None:
    """Changing a fixture plan identity must still fail the production check."""
    import packages.runtime_release.semantic as module

    active, plan, manifest = _publication("v1")
    plan["runtime_uid"] = os.geteuid() + 1
    monkeypatch.setattr(
        module,
        "read_protected_canonical_json_current",
        lambda path: (active, "f" * 64),
    )
    monkeypatch.setattr(
        module,
        "read_protected_canonical_json",
        lambda path, digest: plan if path.name == active["plan_path"] else manifest,
    )
    monkeypatch.setattr(module, "_read_input", lambda path, digest: (10, digest))
    monkeypatch.setattr(module, "_attest_exact_tree", lambda root: None)
    monkeypatch.setattr(
        module,
        "_current_parent_attestations",
        lambda active_path: (
            {"device": 1, "inode": 3, "uid": 0, "gid": 0, "mode": 0o755},
            {"device": 1, "inode": 2, "uid": 0, "gid": 0, "mode": 0o755},
        ),
    )
    authority = SemanticAuthority(
        ACTIVE_PATH, semantic_policy_digest(BACKEND, ACTIVE_PATH),
    )

    with pytest.raises(SemanticAttestationError):
        attest_current_semantic_inputs(authority, BACKEND, clock=lambda: NOW)


def test_corrupted_rotating_active_fails_under_unchanged_stable_policy(monkeypatch):
    import packages.runtime_release.semantic as module

    active, _, _ = _publication("v1")
    active["classification"] = "UNTRUSTED"
    monkeypatch.setattr(module, "read_protected_canonical_json_current", lambda path: (active, "f" * 64))
    authority = SemanticAuthority(ACTIVE_PATH, semantic_policy_digest(BACKEND, ACTIVE_PATH))

    with pytest.raises(SemanticAttestationError):
        attest_current_semantic_inputs(authority, BACKEND, clock=lambda: NOW)


@pytest.mark.parametrize(
    "probe",
    [
        "unknown_plan", "missing_plan", "generated_at", "validity",
        "parent_attestation", "source_unknown", "source_missing",
        "source_fields", "runtime_path", "source_hash", "duplicate_inode",
        "source_size", "source_path", "source_double_slash", "source_dot",
        "source_trailing_slash", "source_backslash",
    ],
)
def test_main_rejects_every_noncanonical_or_unbound_task4_plan_shape(monkeypatch, probe):
    import packages.runtime_release.semantic as module

    active, plan, manifest = _publication("v1")
    if probe == "unknown_plan":
        plan["unexpected"] = True
    elif probe == "missing_plan":
        plan.pop("runtime_uid")
    elif probe == "generated_at":
        plan["generated_at"] = (NOW - timedelta(seconds=1)).isoformat()
    elif probe == "validity":
        plan["validity_minutes"] = 29
    elif probe == "parent_attestation":
        plan["input_parent_attestation"]["unexpected"] = True
    elif probe == "source_unknown":
        plan["sources"]["unknown"] = dict(plan["sources"]["macro_report"])
    elif probe == "source_missing":
        plan["sources"].pop("fred_cache")
    elif probe == "source_fields":
        plan["sources"]["macro_report"]["unexpected"] = True
    elif probe == "runtime_path":
        plan["sources"]["macro_report"]["runtime_path"] = "reports/other.json"
    elif probe == "source_hash":
        plan["sources"]["macro_report"]["sha256"] = "3" * 64
    elif probe == "duplicate_inode":
        plan["sources"]["sentiment_report"]["device"] = plan["sources"]["macro_report"]["device"]
        plan["sources"]["sentiment_report"]["inode"] = plan["sources"]["macro_report"]["inode"]
    elif probe == "source_size":
        plan["sources"]["macro_report"]["size"] = -1
    elif probe == "source_path":
        plan["sources"]["macro_report"]["path"] = "relative.json"
    elif probe == "source_double_slash":
        plan["sources"]["macro_report"]["path"] = "/legacy//semantic/macro_report.json"
    elif probe == "source_dot":
        plan["sources"]["macro_report"]["path"] = "/legacy/./semantic/macro_report.json"
    elif probe == "source_trailing_slash":
        plan["sources"]["macro_report"]["path"] = "/legacy/semantic/macro_report.json/"
    else:
        plan["sources"]["macro_report"]["path"] = "/legacy/semantic\\macro_report.json"

    monkeypatch.setattr(module, "read_protected_canonical_json_current", lambda path: (active, "f" * 64))
    monkeypatch.setattr(
        module,
        "read_protected_canonical_json",
        lambda path, digest: plan if path.name == active["plan_path"] else manifest,
    )
    monkeypatch.setattr(module, "_read_input", lambda path, digest: (10, digest))
    monkeypatch.setattr(module, "_attest_exact_tree", lambda root: None)
    monkeypatch.setattr(
        module,
        "_current_parent_attestations",
        lambda active_path: (
            {"device": 1, "inode": 3, "uid": 0, "gid": 0, "mode": 0o755},
            {"device": 1, "inode": 2, "uid": 0, "gid": 0, "mode": 0o755},
        ),
        raising=False,
    )
    authority = SemanticAuthority(ACTIVE_PATH, semantic_policy_digest(BACKEND, ACTIVE_PATH))

    with pytest.raises(SemanticAttestationError):
        attest_current_semantic_inputs(authority, BACKEND, clock=lambda: NOW)
