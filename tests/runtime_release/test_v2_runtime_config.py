from __future__ import annotations

from pathlib import Path

import pytest

import packages.runtime_release.job_plane as job_plane
from packages.runtime_release import (
    ProtectedAuthorityError,
    RELEASE_ACTIVATION_V2_PATH,
    RUNTIME_AUTHORITY_V2_PATH,
    RuntimeAuthorityV2,
    load_runtime_authority_v2,
    validate_job_plane_authority,
)


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
