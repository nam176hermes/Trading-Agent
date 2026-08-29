"""Fail-closed validation for P1 projection inputs.

Duplicate execution identities and correction/bust reports are rejected.  The
P1 event vocabulary has no adjustment event, so accepting one would synthesize
accounting behavior outside the qualified engine contract.
"""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from uuid import UUID

from pydantic import BaseModel

from packages.domain import (
    Currency,
    InstrumentConstraints,
    LiquiditySide,
    PortfolioOpeningEntry,
    ReconciliationSource,
    decimal_to_scaled_integer,
)
from packages.engine_contracts import canonical_json_bytes
from packages.nautilus_runtime_contracts.artifacts import P1InstrumentCatalogV1
from packages.nautilus_runtime_contracts.events import (
    P1Event,
    P1_EVENT_ADAPTER,
    P1_EVENT_MODELS,
)
from packages.nautilus_runtime_contracts.state_machine import validate_event_stream

from .models import ProjectionAuthority


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$", re.ASCII)
_UNSUPPORTED_ADJUSTMENTS = frozenset({"DUPLICATE", "CORRECTION", "BUST"})
_MAX_DECIMAL_DIGITS = 128
_MAX_DECIMAL_SCALE = 18


class ProjectionError(ValueError):
    """P1 engine facts cannot be projected without inventing accounting."""


def _bounded_decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ProjectionError(f"{field} is not an exact Decimal")
    _, digits, raw_exponent = value.as_tuple()
    exponent = int(raw_exponent)
    if (
        len(digits) > _MAX_DECIMAL_DIGITS
        or exponent < -_MAX_DECIMAL_SCALE
        or len(digits) + max(0, exponent) > _MAX_DECIMAL_DIGITS
    ):
        raise ProjectionError(f"{field} exceeds Decimal bounds")
    try:
        decimal_to_scaled_integer(value, _MAX_DECIMAL_SCALE)
    except ValueError as exc:
        raise ProjectionError(f"{field} exceeds Decimal bounds") from exc
    return value


def exact_fraction(value: Decimal, *, field: str) -> Fraction:
    exact = _bounded_decimal(value, field=field)
    return Fraction(decimal_to_scaled_integer(exact, _MAX_DECIMAL_SCALE), 10**18)


def catalog_digest(value: P1InstrumentCatalogV1) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def _canonical_catalog(value: object) -> P1InstrumentCatalogV1:
    if type(value) is not P1InstrumentCatalogV1:
        raise ProjectionError("catalog authority is invalid")
    for name in P1InstrumentCatalogV1.model_fields:
        member = getattr(value, name)
        if isinstance(member, Decimal):
            _bounded_decimal(member, field=f"catalog {name}")
    try:
        return P1InstrumentCatalogV1.model_validate(value)
    except (TypeError, ValueError) as exc:
        raise ProjectionError("catalog authority is invalid") from exc


def canonical_authority(value: object) -> ProjectionAuthority:
    if type(value) is not ProjectionAuthority:
        raise ProjectionError("projection authority is invalid")
    if type(value.request_message_id) is not UUID:
        raise ProjectionError("request message ID is invalid")
    try:
        catalog = _canonical_catalog(value.catalog)
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
    if (
        instrument.instrument_id.canonical
        != f"{catalog.product_type}:{catalog.venue}:{catalog.symbol}"
        or instrument.raw_symbol != catalog.symbol
        or instrument.base_currency is None
        or instrument.base_currency.code != catalog.base_currency
        or instrument.quote_currency.code != catalog.quote_currency
        or instrument.tick_size.amount != catalog.tick_size
        or -int(catalog.tick_size.as_tuple().exponent) != catalog.price_precision
        or instrument.size_increment.value != catalog.step_size
        or instrument.size_increment.precision != catalog.size_precision
        or -int(catalog.step_size.as_tuple().exponent) != catalog.size_precision
        or instrument.minimum_quantity.value != catalog.min_quantity
        or -int(catalog.min_quantity.as_tuple().exponent) != catalog.size_precision
        or instrument.minimum_notional.amount != catalog.min_notional
        or instrument.multiplier != Decimal(1)
    ):
        raise ProjectionError("instrument does not match exact P1 catalog or multiplier")
    balances = tuple(
        balance for balance in opening.balances if balance.currency is settlement
    )
    if len(balances) != 1:
        raise ProjectionError("opening requires exactly one settlement balance")
    return ProjectionAuthority(
        request_message_id=value.request_message_id,
        catalog=catalog,
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
        if not isinstance(value, BaseModel):
            raise ProjectionError("P1 event is not root-valid")
        for name in type(value).model_fields:
            member = getattr(value, name)
            if isinstance(member, Decimal):
                _bounded_decimal(member, field=f"event {name}")
        try:
            canonical.append(P1_EVENT_ADAPTER.validate_python(value))
        except (TypeError, ValueError) as exc:
            raise ProjectionError("P1 event is not root-valid") from exc
    try:
        return validate_event_stream(tuple(canonical))
    except ValueError as exc:
        raise ProjectionError(str(exc)) from exc
