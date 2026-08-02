from __future__ import annotations

from copy import deepcopy

import pytest

from packages.runtime_release.supervisor_v2 import (
    ReleaseAuthorityV2ActivationUnavailable,
    ReleaseAuthorityV2SupervisorError,
    issue_release_authority_v2_supervisor,
    validate_release_authority_v2_supervisor,
)


def _authority() -> dict[str, object]:
    return {
        "schema_version": 1,
        "authority_kind": "RELEASE_AUTHORITY_V2_SYSTEM_MANAGER_SUPERVISOR",
        "manager_scope": "SYSTEM",
        "cgroup_authority": "SYSTEM_MANAGER_EXCLUSIVE",
        "credential_delivery": "SYSTEMD_LOAD_CREDENTIAL",
        "native_custodian_status": "RETIRED_TEST_ONLY",
        "production_launch_mechanism": "SYSTEMD_UNITS",
        "activation_status": "UNAVAILABLE",
        "launch_authorized": False,
        "live_execution_approved": False,
        "live_trading_approved": False,
        "units": {
            "job_api": {
                "name": "trading-job-api.service",
                "service_user": "trading-job-api",
                "service_group": "trading-job-api",
                "kill_mode": "control-group",
                "protect_control_groups": True,
                "credential_names": [
                    "database-host",
                    "database-name",
                    "database-password",
                    "database-port",
                    "job-api-principal-id",
                    "job-api-principal-type",
                    "job-api-token",
                ],
            },
            "worker": {
                "name": "trading-job-worker.service",
                "service_user": "trading-job-worker",
                "service_group": "trading-job-worker",
                "kill_mode": "control-group",
                "protect_control_groups": True,
                "credential_names": [
                    "database-host",
                    "database-name",
                    "database-password",
                    "database-port",
                ],
            },
        },
    }


def test_v2_supervisor_authority_accepts_only_distinct_system_units() -> None:
    authority = validate_release_authority_v2_supervisor(_authority())

    assert authority.manager_scope == "SYSTEM"
    assert authority.activation_status == "UNAVAILABLE"
    assert authority.launch_authorized is False
    assert authority.units["job_api"].service_user == "trading-job-api"
    assert authority.units["worker"].service_user == "trading-job-worker"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda d: d.update(manager_scope="USER"),
        lambda d: d.update(cgroup_authority="CLIENT_DESCRIPTOR"),
        lambda d: d.update(credential_delivery="PATH_REOPEN"),
        lambda d: d.update(native_custodian_status="PRODUCTION"),
        lambda d: d.update(production_launch_mechanism="NATIVE_HELPER"),
        lambda d: d.update(activation_status="ACTIVE"),
        lambda d: d.update(launch_authorized=True),
        lambda d: d.update(live_execution_approved=True),
        lambda d: d.update(live_trading_approved=True),
        lambda d: d["units"]["worker"].update(service_user="trading-job-api"),
        lambda d: d["units"]["worker"].update(service_group="trading-job-api"),
        lambda d: d["units"]["worker"].update(protect_control_groups=False),
        lambda d: d["units"]["worker"].update(kill_mode="process"),
        lambda d: d["units"]["worker"].update(credential_names=["/etc/secret"]),
    ),
)
def test_v2_supervisor_authority_rejects_ambient_or_same_identity_control(
    mutation: object,
) -> None:
    document = deepcopy(_authority())
    mutation(document)  # type: ignore[operator]

    with pytest.raises(ReleaseAuthorityV2SupervisorError):
        validate_release_authority_v2_supervisor(document)


def test_v2_supervisor_issuance_is_unavailable_without_activation_lifecycle() -> None:
    authority = validate_release_authority_v2_supervisor(_authority())

    with pytest.raises(ReleaseAuthorityV2ActivationUnavailable):
        issue_release_authority_v2_supervisor(authority)
