"""Source-only contract for the sealed, external Nautilus strategy mapping."""

from __future__ import annotations

import ast
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest

from test_launcher_protocol import _simulation_fixture


LAUNCHER = Path("engines/nautilus/launcher/nautilus_backtest.py")


def _launcher_module():
    spec = importlib.util.spec_from_file_location("strategy_plan_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("scenario_id", "expected"),
    (
        ("long-accounting", ((True, "2", "100", None),)),
        ("short-accounting", ((True, "2", "99", None),)),
        ("partial-fill", ((True, "1", "100", None),)),
        ("same-bar-stop-take-profit", ((True, "1", "100", "98"),)),
        ("stale-quote", ((False, "0", None, None),)),
        ("zero-liquidity", ((False, "0", None, None),)),
        (
            "session-boundary",
            ((False, "0", None, None), (True, "1", "102", None)),
        ),
        ("event-digest", ((True, "1", "100", None),)),
    ),
)
def test_launcher_builds_substantive_execution_plan_for_each_scenario(
    scenario_id: str,
    expected: tuple[tuple[bool, str, str | None, str | None], ...],
) -> None:
    """Removing a semantic branch would change its actual order plan."""
    launcher = _launcher_module()
    artifacts = _simulation_fixture(scenario_id)
    request = {
        "payload": {
            "command_type": "RunBacktestSimulation",
            "start_time": "2026-08-05T12:00:00Z",
            "end_time": "2026-08-05T12:30:00Z",
        }
    }
    fixture = launcher.validate_simulation_fixture_inputs(request, artifacts)

    plan = launcher._build_target_portfolio_execution_plan(fixture)

    assert tuple(
        (
            item["eligible"],
            item["fill_quantity"],
            item["entry_price"],
            item["exit_price"],
        )
        for item in plan
    ) == expected


def test_launcher_execution_plan_applies_fee_and_slippage_inputs() -> None:
    """Ignoring fee or slippage would make validated mutations semantically inert."""
    launcher = _launcher_module()
    artifacts = list(_simulation_fixture("event-digest"))
    scenario = json.loads(artifacts[4])
    scenario["fee_rate"] = "0.002"
    scenario["slippage_bps"] = "25"
    artifacts[4] = launcher._canonical_json_bytes(scenario)
    request = {
        "payload": {
            "command_type": "RunBacktestSimulation",
            "start_time": "2026-08-05T12:00:00Z",
            "end_time": "2026-08-05T12:30:00Z",
        }
    }

    fixture = launcher.validate_simulation_fixture_inputs(request, tuple(artifacts))
    plan = launcher._build_target_portfolio_execution_plan(fixture)

    assert fixture["fee_rate"] == Decimal("0.002")
    assert plan[0]["entry_price"] == "100.25"


def test_launcher_scenario_commission_applies_validated_fee_rate() -> None:
    """Dropping the custom fee calculation would return default venue fees."""
    launcher = _launcher_module()

    assert launcher._scenario_commission(
        fill_quantity=Decimal("1.5"),
        fill_price=Decimal("100.25"),
        fee_rate=Decimal("0.002"),
    ) == Decimal("0.300750")


def test_fixed_strategy_config_carries_every_validated_execution_semantic() -> None:
    """Removing any control would make an accepted scenario unmappable."""
    source = Path("engines/nautilus/launcher/target_portfolio_strategy.py")
    module = ast.parse(source.read_text(encoding="utf-8"))
    config = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "TargetPortfolioStrategyConfig")
    fields = {node.target.id for node in config.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}

    assert {"target_quantity", "scenario_id", "event_semantics", "fee_rate", "slippage_bps", "liquidity_limit", "stale_quote_threshold_seconds", "stop_price", "take_profit_price", "stop_take_profit_precedence"} <= fields
