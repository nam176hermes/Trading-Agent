"""Deterministic conversion from canonical targets to engine target contracts."""

from __future__ import annotations

from pydantic import ValidationError

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
        validated = TargetPortfolio.model_validate(value)
        positions = tuple(
            EngineTargetPosition(
                instrument=EngineInstrumentId(
                    symbol=position.instrument.symbol,
                    product_type=position.instrument.product_type,
                    venue=position.instrument.venue,
                ),
                target_weight=position.target_weight,
            )
            for position in sorted(validated.positions, key=lambda item: item.instrument.canonical)
        )
        return EngineTargetPortfolio(
            target_id=validated.target_id,
            positions=positions,
            source_signal_ids=tuple(sorted(validated.source_signal_ids, key=str)),
            effective_at=validated.effective_at,
            schema_version=validated.schema_version,
        )
    except (ValidationError, ValueError) as exc:
        raise TargetStrategyBridgeError("invalid canonical target") from exc
