from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HWC = ROOT / "docs" / "implementation" / "hwc"
INVENTORY = HWC / "hwc-authority-inventory-v1.json"
POLICY = HWC / "hwc-boundary-policy-v1.json"
CLOSURE = HWC / "hwc-closure-matrix-v1.json"
TOPOLOGY = ROOT / "docs/operations/hwc-process-topology.md"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_hwc_policy_freezes_every_current_route_and_authority_limit() -> None:
    """Break caught: an interface can gain unowned or runtime/live authority."""
    inventory = _load(INVENTORY)
    policy = _load(POLICY)
    closure = _load(CLOSURE)

    assert inventory["schema_version"] == "hwc-authority-inventory-v1"
    assert policy["schema_version"] == "hwc-boundary-policy-v1"
    assert closure["schema_version"] == "hwc-closure-matrix-v1"

    discovered = {
        f"/{path.parent.relative_to(ROOT / 'apps/dashboard/src/app/api/trading').as_posix()}".replace("/.", "")
        for path in (ROOT / "apps/dashboard/src/app/api/trading").glob("**/route.ts")
    }
    routes = inventory["dashboard_routes"]
    assert isinstance(routes, list)
    inventoried = [route["route"] for route in routes]
    assert len(inventoried) == len(set(inventoried))
    assert set(inventoried) == discovered

    authorities = inventory["authorities"]
    assert isinstance(authorities, list)
    canonical = [item for item in authorities if item["canonical_owner"] is not None]
    capabilities = [item["capability"] for item in canonical]
    assert len(capabilities) == len(set(capabilities))
    assert all(item["classification"] != "dead" for item in authorities)

    grandfathered = policy["grandfathered_state_writes"]
    assert isinstance(grandfathered, list)
    for item in grandfathered:
        assert set(item) == {
            "path",
            "classification",
            "git_blob_sha",
            "source_sha256",
            "owner",
            "authority",
            "migration_task",
            "expires_at_gate",
        }
        assert item["classification"] == "TEMPORARY_GRANDFATHERED_STATE_WRITE"
        assert len(item["git_blob_sha"]) == 40
        assert len(item["source_sha256"]) == 64
        assert item["migration_task"].startswith("T-HWC-")
        assert item["expires_at_gate"] == "HWC_DASHBOARD_AUTHORITY_REMOVED"

    assert closure["authority"] == {
        "broker": False,
        "live": False,
        "network": False,
        "production": False,
    }
    assert all(gate["source_only"] is True for gate in closure["gates"])


def test_future_hwc_topology_is_specific_and_all_deployment_authority_is_held() -> None:
    topology = TOPOLOGY.read_text(encoding="utf-8")
    closure = _load(CLOSURE)
    for required in (
        "trading-control-api      127.0.0.1:8400",
        "trading-job-api          127.0.0.1:8401",
        "trading-operator-api     127.0.0.1:8402",
        "SYSTEMD_SOURCE=HELD",
        "RELEASE_V2_INTEGRATION=HELD",
        "HOST_QUALIFICATION=HELD",
        "RUNTIME_ACTIVATION=HELD",
        "/home/thenam176/.hermes/crypto-research/.operator-commands",
        "same filesystem",
        "must not be a parent",
    ):
        assert required in topology
    assert closure["topology"] == "docs/operations/hwc-process-topology.md"
    assert closure["deployment"] == {
        "host_qualified": "HELD",
        "release_v2_integrated": "HELD",
        "runtime_active": "HELD",
        "systemd_source": "HELD",
    }
