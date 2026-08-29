"""Fail-closed validation for P1 projection inputs.

Duplicate execution identities and correction/bust reports are rejected.  The
P1 event vocabulary has no adjustment event, so accepting one would synthesize
accounting behavior outside the qualified engine contract.
"""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from uuid import UUID

from packages.domain import (
    Currency,
    InstrumentConstraints,
    LiquiditySide,
    PortfolioOpeningEntry,
    ReconciliationSource,
)
from packages.nautilus_runtime_contracts.events import (
    P1Event,
    P1_EVENT_ADAPTER,
    P1_EVENT_MODELS,
)
from packages.nautilus_runtime_contracts.state_machine import validate_event_stream

from .models import ProjectionAuthority


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$", re.ASCII)
_UNSUPPORTED_ADJUSTMENTS = frozenset({"DUPLICATE", "CORRECTION", "BUST"})


class ProjectionError(ValueError):
    """P1 engine facts cannot be projected without inventing accounting."""


def exact_fraction(value: Decimal, *, field: str) -> Fraction:
    try:
        result = Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ProjectionError(f"{field} is not an exact Decimal") from exc
    return result


def canonical_authority(value: object) -> ProjectionAuthority:
    if type(value) is not ProjectionAuthority:
        raise ProjectionError("projection authority is invalid")
    if type(value.request_message_id) is not UUID:
        raise ProjectionError("request message ID is invalid")
    try:
        instrument = InstrumentConstraints(value.instrument).definition
        opening = PortfolioOpeningEntry.model_validate(
            {name: getattr(value.opening, name) for name in PortfolioOpeningEntry.model_fields}
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProjectionError("catalog or opening account authority is invalid") from exc
    if (
        _IDENTIFIER.fullmatch(value.strategy_id) is None
        or type(value.liquidity_side) is not LiquiditySide
        or type(value.reconciliation_source) is not ReconciliationSource
    ):
        raise ProjectionError("strategy or execution authority is invalid")
    settlement = instrument.settlement_currency
    if settlement is not Currency.USDT or opening.reporting_currency is not settlement:
        raise ProjectionError("opening and catalog settlement currency must be USDT")
    balances = tuple(
        balance for balance in opening.balances if balance.currency is settlement
    )
    if len(balances) != 1:
        raise ProjectionError("opening requires exactly one settlement balance")
    return ProjectionAuthority(
        request_message_id=value.request_message_id,
        instrument=instrument,
        opening=opening,
        strategy_id=value.strategy_id,
        liquidity_side=value.liquidity_side,
        reconciliation_source=value.reconciliation_source,
    )


def canonical_events(values: object) -> tuple[P1Event, ...]:
    if type(values) is not tuple:
        raise ProjectionError("P1 event stream must be an immutable tuple")
    canonical: list[P1Event] = []
    for value in values:
        event_type = (
            value.get("event_type")
            if type(value) is dict
            else getattr(value, "event_type", None)
        )
        if isinstance(event_type, str) and event_type.upper() in _UNSUPPORTED_ADJUSTMENTS:
            raise ProjectionError("duplicate/correction/bust reports are unsupported")
        if type(value) not in P1_EVENT_MODELS:
            raise ProjectionError(
                "duplicate/correction/bust reports are unsupported by the typed P1 stream"
            )
        try:
            canonical.append(P1_EVENT_ADAPTER.validate_python(value))
        except (TypeError, ValueError) as exc:
            raise ProjectionError("P1 event is not root-valid") from exc
    try:
        return validate_event_stream(tuple(canonical))
    except ValueError as exc:
        raise ProjectionError(str(exc)) from exc
