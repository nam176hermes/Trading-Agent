"""Deterministic conversion from canonical targets to engine target contracts."""

from __future__ import annotations

from packages.domain import TargetPortfolio
from packages.engine_contracts import (
    EngineInstrumentId,
    EngineTargetPortfolio,
    EngineTargetPosition,
)


class TargetStrategyBridgeError(ValueError):
    """A canonical target cannot cross the strategy contract boundary."""


def bridge_target_portfolio(value: TargetPortfolio) -> EngineTargetPortfolio:
    """Convert one approved target into a deterministic, order-free engine target."""

    if not isinstance(value, TargetPortfolio):
        raise TargetStrategyBridgeError("value must be a TargetPortfolio")
    try:
        positions = tuple(
            EngineTargetPosition(
                instrument=EngineInstrumentId(
                    symbol=position.instrument.symbol,
                    product_type=position.instrument.product_type,
                    venue=position.instrument.venue,
                ),
                target_weight=position.target_weight,
            )
            for position in sorted(value.positions, key=lambda item: item.instrument.canonical)
        )
        return EngineTargetPortfolio(
            target_id=value.target_id,
            positions=positions,
            source_signal_ids=tuple(sorted(value.source_signal_ids, key=str)),
            effective_at=value.effective_at,
            schema_version=value.schema_version,
        )
    except ValueError as exc:
        raise TargetStrategyBridgeError("target cannot cross the strategy boundary") from exc
