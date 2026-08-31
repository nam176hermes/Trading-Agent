from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from tests.foundation._package6_staging_fixture import (
    Package6StagingLease,
    package6_staging_lease,
)
import packages.runtime_release.config as runtime_config
import packages.runtime_release.staging_v2 as staging_v2
import packages.runtime_release.job_plane as job_plane
from packages.runtime_release.paper_application import (
    runtime_release_config as projected_runtime_config,
)
from services.job_worker.environment import (
    ResearchEnvironmentSettings,
    build_child_environment,
)
from packages.runtime_release import (
    ProtectedAuthorityError,
    RELEASE_ACTIVATION_V2_PATH,
    RUNTIME_AUTHORITY_V2_PATH,
    RuntimeAuthorityV2,
    attest_application_release_v2,
    load_runtime_authority_v2,
    validate_job_plane_authority,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _stage_entries(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: os.fsencode(item.relative_to(root))):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISDIR(info.st_mode):
            digest = hashlib.sha256(b"").hexdigest()
            size = 0
            kind = "directory"
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            size = info.st_size
            kind = "file"
        entries.append(
            {
                "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "path": relative,
                "sha256": digest,
                "size": size,
                "type": kind,
            }
        )
    return entries


def _component_digest(entries: list[dict[str, object]], prefix: str) -> str:
    selected = [
        item
        for item in entries
        if item["path"] == prefix or str(item["path"]).startswith(prefix + "/")
    ]
    return _digest(selected)


def _write_fixed(path: Path, value: object, mode: int = 0o444) -> str:
    raw = _canonical(value)
    path.write_bytes(raw)
    path.chmod(mode)
    return hashlib.sha256(raw).hexdigest()


def _staging_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    lease: Package6StagingLease,
):
    lease.assert_valid()
    private = lease.root
    stage = private / "stage"
    app = stage / "application"
    backend = stage / "backend"
    for root in (app / ".venv/bin", backend / ".venv/bin"):
        root.mkdir(parents=True, mode=0o700)
    app_python = app / ".venv/bin/python3.11"
    backend_python = backend / ".venv/bin/python3.11"
    app_python.write_bytes(b"application-python\n")
    backend_python.write_bytes(b"backend-python\n")
    (app / "worker.py").write_bytes(b"APPLICATION = True\n")
    (backend / "paper_main.py").write_bytes(b"PAPER_ONLY = True\n")
    for path in sorted(stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() or path.name == "python3.11" else 0o444)
    stage.chmod(0o555)
    entries = _stage_entries(stage)

    runtime_root = private / "runtime"
    semantic_root = private / "semantic"
    authority_root = private / "authority"
    for path in (runtime_root, semantic_root, authority_root):
        path.mkdir(mode=0o700)
    semantic_input = semantic_root / "input"
    semantic_input.mkdir(mode=0o711)
    for name in ("reports", "signals", "scratch", "artifacts"):
        (runtime_root / name).mkdir(mode=0o700)

    commit = "a" * 40
    tree = "b" * 40
    approval_sha256 = "c" * 64
    generated = datetime.now(UTC).replace(microsecond=0)
    expires = generated + timedelta(minutes=10)

    def timestamp(value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    safety = {
        "effective_mode": "PAPER",
        "expires_at": timestamp(generated + timedelta(seconds=30)),
        "exporter_commit": commit,
        "generated_at": timestamp(generated),
        "kill_switch_state": "INACTIVE",
        "live_execution_enabled": False,
        "live_trading_approved": False,
        "requested_mode": "PAPER",
        "schema_version": 1,
        "source_fingerprint": "d" * 64,
    }
    safety_path = runtime_root / "safety-state.json"
    safety_sha256 = _write_fixed(safety_path, safety, 0o600)
    semantic = {
        "classification": "PACKAGE6_PROVIDER_FREE_SEMANTIC",
        "schema_version": 1,
        "source_commit": commit,
    }
    semantic_path = semantic_root / "active.json"
    semantic_sha256 = _write_fixed(semantic_path, semantic)

    commands = [
        {
            "argv": [str(backend_python), "-I", "-B", "paper_main.py"],
            "cwd": str(backend),
            "environment_policy": "CANONICAL_PAPER_CHILD_V1",
            "executable": str(backend_python),
            "job_type": "SNAPSHOT",
            "shell": False,
        }
    ]
    command_manifest = {
        "commands": commands,
        "manifest_sha256": hashlib.sha256(
            json.dumps(commands, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "schema_version": 3,
    }
    authority = {
        "authority_kind": "PACKAGE6_STAGING_RELEASE_AUTHORITY_V2",
        "command_manifest": command_manifest,
        "components": {
            "application": {
                "artifact_root": "application",
                "artifact_set_sha256": _component_digest(entries, "application"),
            },
            "backend": {
                "artifact_root": "backend",
                "artifact_set_sha256": _component_digest(entries, "backend"),
            },
        },
        "constraints": {
            "live_execution_approved": False,
            "live_trading_approved": False,
            "live_trading_enabled": False,
            "network_policy": "LOOPBACK_ONLY",
            "persistent_services_allowed": False,
            "systemd_allowed": False,
        },
        "disposable_root": str(private),
        "installation_root": str(stage),
        "interpreters": {
            "application": {
                "path": str(app_python),
                "sha256": hashlib.sha256(app_python.read_bytes()).hexdigest(),
            },
            "backend": {
                "path": str(backend_python),
                "sha256": hashlib.sha256(backend_python.read_bytes()).hexdigest(),
            },
        },
        "production_release_authority_sha256": "e" * 64,
        "runtime_paths": {
            "artifact_root": str(runtime_root / "artifacts"),
            "reports_root": str(runtime_root / "reports"),
            "safety_snapshot": str(safety_path),
            "scratch_root": str(runtime_root / "scratch"),
            "semantic_authority": str(semantic_path),
            "semantic_input_root": str(semantic_input),
            "signals_root": str(runtime_root / "signals"),
        },
        "schema_version": 1,
        "scope": "PACKAGE6_STAGING_ONLY",
        "source": {"commit": commit, "tree": tree},
        "stage": {"entries": entries, "file_set_sha256": _digest(entries)},
        "validity": {
            "expires_at_utc": timestamp(expires),
            "generated_at_utc": timestamp(generated),
        },
    }
    authority_path = authority_root / "release-authority-v2.json"
    authority_sha256 = _write_fixed(authority_path, authority)
    activation = {
        "activation_kind": "PACKAGE6_STAGING_RELEASE_ACTIVATION_V2",
        "authority_sha256": authority_sha256,
        "constraints": authority["constraints"],
        "package6_approval_sha256": approval_sha256,
        "safety": {
            "exporter_commit": commit,
            "snapshot_sha256": safety_sha256,
            "source_fingerprint": "d" * 64,
        },
        "schema_version": 1,
        "scope": "PACKAGE6_STAGING_ONLY",
        "semantic": {
            "active_authority_sha256": semantic_sha256,
            "expires_at": timestamp(expires),
            "generated_at": timestamp(generated),
            "manifest_version": "package6-provider-free-v1",
            "policy_sha256": "f" * 64,
            "semantic_input_fingerprint": "1" * 64,
            "version_manifest_sha256": semantic_sha256,
        },
        "validity": authority["validity"],
    }
    activation_path = authority_root / "release-activation-v2.json"
    _write_fixed(activation_path, activation)
    monkeypatch.setenv("TRADING_PACKAGE6_STAGING_SCOPE", "PACKAGE6_STAGING_ONLY")
    monkeypatch.setenv("TRADING_PACKAGE6_STAGING_AUTHORITY_PATH", str(authority_path))
    monkeypatch.setenv("TRADING_PACKAGE6_STAGING_ACTIVATION_PATH", str(activation_path))
    monkeypatch.setenv("TRADING_PACKAGE6_APPROVAL_SHA256", approval_sha256)
    return stage, app_python, backend_python


def _authority(
    pin: tuple[object, ...], dynamic_pin: tuple[object, ...] = ("7" * 64, "8" * 64)
) -> RuntimeAuthorityV2:
    authority = object.__new__(RuntimeAuthorityV2)
    object.__setattr__(authority, "_authority_pin", pin)
    object.__setattr__(authority, "_dynamic_evidence_pin", dynamic_pin)
    return authority


def test_v2_protected_paths_are_code_owned_and_not_operator_home_bound() -> None:
    assert RUNTIME_AUTHORITY_V2_PATH == Path(
        "/etc/trading-agent-v2/release-authority-v2.json"
    )
    assert RELEASE_ACTIVATION_V2_PATH == Path(
        "/etc/trading-agent-v2/release-activation-v2.json"
    )
    for path in (RUNTIME_AUTHORITY_V2_PATH, RELEASE_ACTIVATION_V2_PATH):
        assert path.is_absolute()
        assert "/home/" not in str(path)
        assert "/run/user/" not in str(path)


def test_v2_promotion_loader_is_deliberately_no_go_until_reviewed() -> None:
    with pytest.raises(ProtectedAuthorityError) as raised:
        load_runtime_authority_v2()

    assert raised.value.reason_code == "RUNTIME_AUTHORITY_V2_UNAVAILABLE"


def test_v2_staging_loader_accepts_only_explicit_candidate_bound_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    stage, application_python, backend_python = _staging_authority(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )

    authority = load_runtime_authority_v2()

    assert authority.scope == "PACKAGE6_STAGING_ONLY"
    assert authority.application_root == stage / "application"
    assert authority.backend_root == stage / "backend"
    assert authority.application_python == application_python
    assert authority.backend_python == backend_python
    assert authority.runtime_paths.artifact_root.is_relative_to(package6_staging_lease.root)
    monkeypatch.setattr(
        runtime_config, "_runtime_python_path", lambda: application_python
    )
    assert attest_application_release_v2(authority) is True


def test_staging_environment_retains_exact_authority_semantic_and_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    _staging_authority(tmp_path, monkeypatch, lease=package6_staging_lease)
    authority = load_runtime_authority_v2()

    child = build_child_environment(
        ResearchEnvironmentSettings.from_authority(authority, {})
    )

    assert child["TRADING_DATA_ROOT"] == str(
        authority.runtime_paths.semantic_input_root
    )
    assert child["TRADING_REPORTS_DIR"] == str(authority.runtime_paths.reports_root)
    assert child["TRADING_SIGNAL_OUTPUT_DIR"] == str(
        authority.runtime_paths.signals_root
    )
    assert child["HOME"] == str(authority.runtime_paths.scratch_root)
    assert child["TRADING_SEMANTIC_AUTHORITY_PATH"] == str(
        authority.runtime_paths.semantic_authority
    )


def test_projected_v2_staging_loader_uses_the_same_candidate_bound_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    stage, application_python, backend_python = _staging_authority(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )

    authority = projected_runtime_config.load_runtime_authority_v2()

    assert authority.scope == "PACKAGE6_STAGING_ONLY"
    assert authority.application_root == stage / "application"
    assert authority.backend_root == stage / "backend"
    assert authority.application_python == application_python
    assert authority.backend_python == backend_python
    monkeypatch.setattr(
        projected_runtime_config,
        "_runtime_python_path",
        lambda: application_python,
    )
    assert projected_runtime_config.attest_application_release_v2(authority) is True


def test_v2_staging_loader_rejects_package6_approval_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    _staging_authority(tmp_path, monkeypatch, lease=package6_staging_lease)
    monkeypatch.setenv("TRADING_PACKAGE6_APPROVAL_SHA256", "9" * 64)

    with pytest.raises(ProtectedAuthorityError) as raised:
        load_runtime_authority_v2()

    assert raised.value.reason_code == "RUNTIME_AUTHORITY_V2_UNAVAILABLE"


def test_v2_staging_loader_rejects_sealed_stage_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    stage, _application_python, _backend_python = _staging_authority(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    target = stage / "application/worker.py"
    target.chmod(0o644)
    target.write_bytes(b"APPLICATION = False\n")
    target.chmod(0o444)

    with pytest.raises(ProtectedAuthorityError) as raised:
        load_runtime_authority_v2()

    assert raised.value.reason_code == "RUNTIME_AUTHORITY_V2_UNAVAILABLE"


def test_v2_staging_loader_rejects_any_live_authority_bit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    stage, _application_python, _backend_python = _staging_authority(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    authority_path = stage.parent / "authority/release-authority-v2.json"
    activation_path = stage.parent / "authority/release-activation-v2.json"
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    activation = json.loads(activation_path.read_text(encoding="utf-8"))
    authority["constraints"]["live_trading_enabled"] = True
    authority_path.chmod(0o644)
    authority_sha256 = _write_fixed(authority_path, authority)
    activation["authority_sha256"] = authority_sha256
    activation["constraints"]["live_trading_enabled"] = True
    activation_path.chmod(0o644)
    _write_fixed(activation_path, activation)

    with pytest.raises(ProtectedAuthorityError) as raised:
        load_runtime_authority_v2()

    assert raised.value.reason_code == "RUNTIME_AUTHORITY_V2_UNAVAILABLE"


@pytest.mark.parametrize("offset_seconds", (-7, 1))
def test_dynamic_refresh_rejects_stale_or_future_safety_evidence(
    tmp_path: Path,
    offset_seconds: int,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    generated = now + timedelta(seconds=offset_seconds)
    safety_path = tmp_path / "safety.json"
    semantic_path = tmp_path / "semantic.json"
    safety = {
        "effective_mode": "PAPER",
        "expires_at": (generated + timedelta(seconds=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "exporter_commit": "a" * 40,
        "generated_at": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kill_switch_state": "INACTIVE",
        "live_execution_enabled": False,
        "live_trading_approved": False,
        "requested_mode": "PAPER",
        "schema_version": 1,
        "source_fingerprint": "b" * 64,
    }
    safety_sha = _write_fixed(safety_path, safety, 0o600)
    semantic_sha = _write_fixed(semantic_path, {"provider_free": True})

    with pytest.raises(ValueError):
        staging_v2._validate_dynamic_files(
            runtime_paths={
                "safety_snapshot": safety_path,
                "semantic_authority": semantic_path,
            },
            safety={
                "snapshot_sha256": safety_sha,
                "exporter_commit": "a" * 40,
                "source_fingerprint": "b" * 64,
            },
            semantic={"active_authority_sha256": semantic_sha},
            now=now,
        )


def test_unapproved_v2_promotion_blocks_job_api_before_repository_exists() -> None:
    from apps.job_api.config import JobApiSettings

    with pytest.raises(ProtectedAuthorityError) as raised:
        JobApiSettings().load_authority()

    assert raised.value.reason_code == "JOB_PLANE_AUTHORITY_INVALID"


def test_default_job_plane_authority_uses_v2_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _authority(((1, 2), "a" * 64, (3, 4), "b" * 64))
    calls: list[str] = []

    monkeypatch.setattr(
        job_plane,
        "load_runtime_authority_v2",
        lambda: calls.append("v2-load") or current,
    )
    monkeypatch.setattr(
        job_plane,
        "attest_application_release_v2",
        lambda selected: calls.append("v2-attest") or selected is current,
    )

    capability = validate_job_plane_authority()

    assert capability.recheck_mutation() is capability
    assert "v2-load" in calls
    assert "v2-attest" in calls

    monkeypatch.setattr(
        job_plane,
        "load_runtime_authority_v2",
        lambda: (_ for _ in ()).throw(RuntimeError("v2 absent")),
    )
    with pytest.raises(ProtectedAuthorityError):
        validate_job_plane_authority()


def test_v2_job_plane_pin_rejects_activation_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {
        "value": _authority(((11, 13), "c" * 64, (17, 19), "d" * 64))
    }
    monkeypatch.setattr(
        job_plane, "load_runtime_authority_v2", lambda: current["value"]
    )
    monkeypatch.setattr(
        job_plane, "attest_application_release_v2", lambda _authority: True
    )
    capability = validate_job_plane_authority()

    current["value"] = _authority(
        ((11, 13), "c" * 64, (23, 29), "e" * 64)
    )

    with pytest.raises(ProtectedAuthorityError) as raised:
        capability.recheck_mutation()
    assert raised.value.reason_code == "JOB_PLANE_AUTHORITY_CHANGED"


def test_v2_job_plane_allows_rotation_between_mutations_but_not_during_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    immutable = ((11, 13), "c" * 64, (17, 19), "d" * 64)
    first = _authority(immutable, ("1" * 64, "2" * 64))
    second = _authority(immutable, ("3" * 64, "4" * 64))
    third = _authority(immutable, ("5" * 64, "6" * 64))
    observed = iter((first, first, second, second, second, third))
    monkeypatch.setattr(job_plane, "load_runtime_authority_v2", lambda: next(observed))
    monkeypatch.setattr(
        job_plane, "attest_application_release_v2", lambda _authority: True
    )

    capability = validate_job_plane_authority()
    assert capability.recheck_mutation() is capability
    with pytest.raises(ProtectedAuthorityError) as raised:
        capability.recheck_mutation()

    assert raised.value.reason_code == "JOB_PLANE_AUTHORITY_CHANGED"


def test_explicit_isolated_authority_loader_failure_is_sanitized() -> None:
    def fail_loader():
        raise RuntimeError("private authority path and digest")

    with pytest.raises(ProtectedAuthorityError) as raised:
        validate_job_plane_authority(
            authority_loader=fail_loader,
            application_attestor=lambda _authority: True,
        )

    assert str(raised.value) == "protected runtime authority is unavailable"
    assert "private" not in repr(raised.value)
