"""Complete generated API-surface coverage for the sealed v1.231 probe."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from engines.nautilus.launcher import nautilus_v1231_probe as probe


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = (
    ROOT / "docs/implementation/p1-real-nautilus/upgrade/direct-api-contract.json"
)


def _contract() -> dict[str, object]:
    value = json.loads(CONTRACT.read_bytes())
    assert isinstance(value, dict)
    return value


def _symbols(contract: dict[str, object]) -> dict[str, object]:
    surfaces = contract["api_surfaces"]
    assert isinstance(surfaces, list)
    symbols: dict[str, object] = {}
    for surface in surfaces:
        assert isinstance(surface, dict)
        members = surface["required_members"]
        assert isinstance(members, list)
        symbols[str(surface["id"])] = type(
            str(surface["import_symbol"]),
            (),
            {str(member): object() for member in members},
        )
    return symbols


def test_manifest_covers_every_surface_member_and_local_invocation() -> None:
    contract = _contract()
    symbols = _symbols(contract)
    result = SimpleNamespace(
        iterations=0,
        summary={"iterations": "0"},
        total_events=0,
        total_orders=0,
        total_positions=0,
    )
    strategy = type(
        "TargetPortfolioStrategy",
        (),
        {
            "entry_filled_quantity": property(lambda self: None),
            "rejected": property(lambda self: False),
            "semantic_events": property(lambda self: []),
        },
    )

    document = probe.build_probe_manifest(
        contract,
        symbols=symbols,
        result=result,
        strategy_type=strategy,
        engine_version="1.231.0",
        lifecycle={
            "dispose_called": True,
            "reset_called": True,
            "reset_retained_instrument": True,
            "reset_retained_strategy": True,
        },
    )

    assert document["status"] == "PASS"
    assert document["api_surface_count"] == 33
    assert document["local_invocation_count"] == 175
    assert [case["id"] for case in document["surface_cases"]] == [
        surface["id"] for surface in contract["api_surfaces"]
    ]
    assert document["local_invocation_ids"] == sorted(
        invocation["id"] for invocation in contract["local_invocations"]
    )
    assert {case["case"] for case in document["surface_cases"]} == {
        "IMPORTED_SYMBOL",
        "RESULT_INSTANCE",
        "STRATEGY_SUBCLASS",
    }


def test_missing_surface_member_or_invocation_mapping_fails_closed() -> None:
    contract = _contract()
    symbols = _symbols(contract)
    del symbols["API-PRICE"].from_str
    with pytest.raises(probe.ApiProbeError, match="required member"):
        probe.build_probe_manifest(
            contract,
            symbols=symbols,
            result=SimpleNamespace(),
            strategy_type=type("Strategy", (), {}),
            engine_version="1.231.0",
            lifecycle={},
        )

    contract = _contract()
    invocation = contract["local_invocations"][0]
    assert isinstance(invocation, dict)
    invocation["surface_ids"] = ["API-NOT-MAPPED"]
    with pytest.raises(probe.ApiProbeError, match="invocation surface"):
        probe.build_probe_manifest(
            contract,
            symbols=_symbols(contract),
            result=SimpleNamespace(
                iterations=0,
                summary={},
                total_events=0,
                total_orders=0,
                total_positions=0,
            ),
            strategy_type=type(
                "Strategy",
                (),
                {
                    "entry_filled_quantity": None,
                    "rejected": False,
                    "semantic_events": (),
                },
            ),
            engine_version="1.231.0",
            lifecycle={
                "dispose_called": True,
                "reset_called": True,
                "reset_retained_instrument": True,
                "reset_retained_strategy": True,
            },
        )


def test_duplicate_or_omitted_surface_fails_closed() -> None:
    contract = _contract()
    surfaces = contract["api_surfaces"]
    assert isinstance(surfaces, list)
    surfaces.append(surfaces[0])
    with pytest.raises(probe.ApiProbeError, match="surface identity"):
        probe.build_probe_manifest(
            contract,
            symbols=_symbols(contract),
            result=SimpleNamespace(),
            strategy_type=type("Strategy", (), {}),
            engine_version="1.231.0",
            lifecycle={},
        )
