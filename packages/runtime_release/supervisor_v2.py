"""Fail-closed Release Authority v2 system-manager supervisor contract.

This module validates offline source authority only. It deliberately cannot issue
runtime launch authority until the separately reviewed activation lifecycle exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping, NoReturn

from .v2 import UNIT_NAMES, _credential_references, _unit_specs


_AUTHORITY_FIELDS = frozenset(
    {
        "schema_version",
        "authority_kind",
        "manager_scope",
        "cgroup_authority",
        "credential_delivery",
        "native_custodian_status",
        "production_launch_mechanism",
        "activation_status",
        "launch_authorized",
        "live_execution_approved",
        "live_trading_approved",
        "units",
    }
)
_UNIT_FIELDS = frozenset(
    {
        "name",
        "service_user",
        "service_group",
        "kill_mode",
        "protect_control_groups",
        "credential_names",
    }
)
_CANONICAL_UNIT_SPECS = _unit_specs(Path("/opt/trading-agent-v2/releases/SEALED"))
_EXPECTED_UNITS = MappingProxyType(
    {
        component: (
            unit_name,
            str(_CANONICAL_UNIT_SPECS[unit_name]["service_user"]),
            str(_CANONICAL_UNIT_SPECS[unit_name]["service_group"]),
            tuple(sorted(_credential_references(unit_name))),
        )
        for component, unit_name in zip(("job_api", "worker"), UNIT_NAMES, strict=True)
    }
)
_CREDENTIAL_NAME = re.compile(r"[a-z][a-z0-9-]{2,63}\Z")


class ReleaseAuthorityV2SupervisorError(ValueError):
    """The static supervisor authority is malformed or weaker than required."""


class ReleaseAuthorityV2ActivationUnavailable(RuntimeError):
    """Runtime supervisor issuance is unavailable until v2 activation exists."""


def _reject() -> NoReturn:
    raise ReleaseAuthorityV2SupervisorError(
        "release authority v2 supervisor authority is invalid"
    )


def _exact_mapping(
    value: object,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        _reject()
    return value


@dataclass(frozen=True, slots=True)
class ReleaseAuthorityV2UnitAuthority:
    name: str
    service_user: str
    service_group: str
    kill_mode: str
    protect_control_groups: bool
    credential_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReleaseAuthorityV2SupervisorAuthority:
    schema_version: int
    authority_kind: str
    manager_scope: str
    cgroup_authority: str
    credential_delivery: str
    native_custodian_status: str
    production_launch_mechanism: str
    activation_status: str
    launch_authorized: bool
    live_execution_approved: bool
    live_trading_approved: bool
    units: Mapping[str, ReleaseAuthorityV2UnitAuthority]


def validate_release_authority_v2_supervisor(
    value: object,
) -> ReleaseAuthorityV2SupervisorAuthority:
    """Validate exact offline authority without creating ambient launch power."""

    document = _exact_mapping(value, _AUTHORITY_FIELDS)
    units = _exact_mapping(document["units"], frozenset(_EXPECTED_UNITS))
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["authority_kind"]
        != "RELEASE_AUTHORITY_V2_SYSTEM_MANAGER_SUPERVISOR"
        or document["manager_scope"] != "SYSTEM"
        or document["cgroup_authority"] != "SYSTEM_MANAGER_EXCLUSIVE"
        or document["credential_delivery"] != "SYSTEMD_LOAD_CREDENTIAL"
        or document["native_custodian_status"] != "RETIRED_TEST_ONLY"
        or document["production_launch_mechanism"] != "SYSTEMD_UNITS"
        or document["activation_status"] != "UNAVAILABLE"
        or document["launch_authorized"] is not False
        or document["live_execution_approved"] is not False
        or document["live_trading_approved"] is not False
    ):
        _reject()

    validated: dict[str, ReleaseAuthorityV2UnitAuthority] = {}
    identities: set[tuple[str, str]] = set()
    for component, expected in _EXPECTED_UNITS.items():
        unit = _exact_mapping(units[component], _UNIT_FIELDS)
        name, service_user, service_group, credential_names = expected
        raw_names = unit["credential_names"]
        if (
            unit["name"] != name
            or unit["service_user"] != service_user
            or unit["service_group"] != service_group
            or unit["kill_mode"] != "control-group"
            or unit["protect_control_groups"] is not True
            or not isinstance(raw_names, list)
            or tuple(raw_names) != credential_names
            or any(
                not isinstance(item, str)
                or _CREDENTIAL_NAME.fullmatch(item) is None
                for item in raw_names
            )
        ):
            _reject()
        identity = (service_user, service_group)
        if identity in identities:
            _reject()
        identities.add(identity)
        validated[component] = ReleaseAuthorityV2UnitAuthority(
            name=name,
            service_user=service_user,
            service_group=service_group,
            kill_mode="control-group",
            protect_control_groups=True,
            credential_names=credential_names,
        )

    return ReleaseAuthorityV2SupervisorAuthority(
        schema_version=1,
        authority_kind="RELEASE_AUTHORITY_V2_SYSTEM_MANAGER_SUPERVISOR",
        manager_scope="SYSTEM",
        cgroup_authority="SYSTEM_MANAGER_EXCLUSIVE",
        credential_delivery="SYSTEMD_LOAD_CREDENTIAL",
        native_custodian_status="RETIRED_TEST_ONLY",
        production_launch_mechanism="SYSTEMD_UNITS",
        activation_status="UNAVAILABLE",
        launch_authorized=False,
        live_execution_approved=False,
        live_trading_approved=False,
        units=MappingProxyType(validated),
    )


def issue_release_authority_v2_supervisor(
    authority: ReleaseAuthorityV2SupervisorAuthority,
) -> NoReturn:
    """Fail closed until a separate v2 activation implementation is reviewed."""

    if not isinstance(authority, ReleaseAuthorityV2SupervisorAuthority):
        raise TypeError("validated v2 supervisor authority is required")
    raise ReleaseAuthorityV2ActivationUnavailable(
        "release authority v2 activation is unavailable"
    )


__all__ = [
    "ReleaseAuthorityV2ActivationUnavailable",
    "ReleaseAuthorityV2SupervisorAuthority",
    "ReleaseAuthorityV2SupervisorError",
    "ReleaseAuthorityV2UnitAuthority",
    "issue_release_authority_v2_supervisor",
    "validate_release_authority_v2_supervisor",
]
