from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from packages.domain import InstrumentId, ProductType, TargetPortfolio, TargetPosition
from packages.strategy_bridge import TargetStrategyBridgeError, bridge_target_portfolio


TARGET_ID = UUID("00000000-0000-0000-0000-000000000101")
SIGNAL_A = UUID("00000000-0000-0000-0000-000000000001")
SIGNAL_B = UUID("00000000-0000-0000-0000-000000000002")
EFFECTIVE_AT = datetime(2026, 8, 6, 12, tzinfo=UTC)


def target(*, positions: tuple[TargetPosition, ...], signal_ids: tuple[UUID, ...] = (SIGNAL_B, SIGNAL_A)) -> TargetPortfolio:
    return TargetPortfolio(
        target_id=TARGET_ID,
        positions=positions,
        source_signal_ids=signal_ids,
        effective_at=EFFECTIVE_AT,
        schema_version="1.0.0",
    )


def position(symbol: str, weight: str) -> TargetPosition:
    return TargetPosition(
        instrument=InstrumentId(symbol, ProductType.CRYPTO_SPOT, "ALPACA"),
        target_weight=Decimal(weight),
    )


def test_bridge_maps_a_canonical_target_without_creating_an_order() -> None:
    result = bridge_target_portfolio(target(positions=(position("BTC-USD", "0.25"),)))

    assert result.target_id == TARGET_ID
    assert result.positions[0].instrument.symbol == "BTC-USD"
    assert result.positions[0].target_weight == Decimal("0.25")
    assert result.source_signal_ids == (SIGNAL_A, SIGNAL_B)
    assert result.effective_at == EFFECTIVE_AT
    assert result.schema_version == "1.0.0"
    assert "order" not in type(result).__name__.casefold()


def test_bridge_canonicalizes_semantically_unordered_inputs() -> None:
    first = target(positions=(position("ETH-USD", "-0.25"), position("BTC-USD", "0.25")))
    second = target(positions=(position("BTC-USD", "0.25"), position("ETH-USD", "-0.25")), signal_ids=(SIGNAL_A, SIGNAL_B))

    assert bridge_target_portfolio(first) == bridge_target_portfolio(second)


def test_bridge_rejects_non_target_input() -> None:
    with pytest.raises(TargetStrategyBridgeError, match="TargetPortfolio"):
        bridge_target_portfolio({})  # type: ignore[arg-type]


def test_bridge_rejects_a_forged_unvalidated_target_model() -> None:
    forged = TargetPortfolio.model_construct(
        target_id=TARGET_ID,
        positions="not-canonical-positions",
        source_signal_ids=(),
        effective_at="not-a-utc-datetime",
        schema_version="1.0.0",
    )

    with pytest.raises(TargetStrategyBridgeError, match="invalid canonical target"):
        bridge_target_portfolio(forged)


def test_strategy_bridge_has_no_provider_runtime_or_execution_imports() -> None:
    root = Path(__file__).resolve().parents[2] / "packages" / "strategy_bridge"
    forbidden = {"httpx", "nautilus_trader", "psycopg", "requests", "services", "socket", "sqlalchemy", "subprocess", "urllib"}
    imports: set[str] = set()
    for source in root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
    assert not any(name == blocked or name.startswith(f"{blocked}.") for name in imports for blocked in forbidden)


# CR01: the gross-weight cap must be exact at the domain, engine and bridge boundaries, in every Decimal context.
import decimal  # noqa: E402

from packages.engine_contracts.commands import (  # noqa: E402
    EngineInstrumentId,
    EngineTargetPortfolio,
    EngineTargetPosition,
)


def _engine_target(*weights: str) -> EngineTargetPortfolio:
    return EngineTargetPortfolio(
        target_id=TARGET_ID,
        positions=tuple(
            EngineTargetPosition(instrument=EngineInstrumentId(symbol=f"A{index}-USD", product_type=ProductType.CRYPTO_SPOT,
                                                               venue="ALPACA"),
                                 target_weight=Decimal(weight))
            for index, weight in enumerate(weights)
        ),
        source_signal_ids=(SIGNAL_A,),
        effective_at=EFFECTIVE_AT,
        schema_version="1.0.0",
    )


@pytest.mark.parametrize("precision", [2, 28, 80])
@pytest.mark.parametrize(("weights", "accepted"), [
    (("0.67", "0.334"), False),        # 1.004
    (("1", "1E-29"), False),           # 1 + 1e-29
    (("0.67", "0.33"), True),          # exactly 1
    (("-0.67", "0.33"), True),         # gross 1
])
def test_target_gross_weight_is_exact_at_domain_engine_and_bridge_boundaries(
    precision: int, weights: tuple[str, ...], accepted: bool,
) -> None:
    positions = tuple(position(f"A{index}-USD", weight) for index, weight in enumerate(weights))
    forged = TargetPortfolio.model_construct(
        target_id=TARGET_ID, positions=positions, source_signal_ids=(SIGNAL_A,), effective_at=EFFECTIVE_AT,
        schema_version="1.0.0",
    )
    with decimal.localcontext(decimal.Context(prec=precision)):
        if accepted:
            target(positions=positions)
            _engine_target(*weights)
            bridge_target_portfolio(forged)
        else:
            with pytest.raises(ValueError, match="total absolute target weight"):
                target(positions=positions)
            with pytest.raises(ValueError, match="total absolute target weight"):
                _engine_target(*weights)
            with pytest.raises(TargetStrategyBridgeError, match="invalid canonical target"):
                bridge_target_portfolio(forged)
