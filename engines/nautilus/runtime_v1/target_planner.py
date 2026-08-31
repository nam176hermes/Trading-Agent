"""Pure long/flat sizing for the fixed P1 spot profile."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, DecimalException, ROUND_FLOOR, localcontext


_CONTEXT = Context(prec=96)
_PRICE_MAX = Decimal("17014118346046")
_QUANTITY_MAX = Decimal("34028236692093")
_MAX_PRECISION = 16


class TargetPlanError(ValueError):
    """The risk-approved target cannot be planned by the P1 profile."""

    @property
    def code(self) -> str:
        return str(self)


@dataclass(frozen=True, slots=True)
class TargetPlan:
    target_id: str
    source_signal_ids: tuple[str, ...]
    effective_at: str
    instrument_id: str
    current_quantity: str
    target_quantity: str
    delta: str
    side: str | None
    price_basis: str
    notional: str
    reason: str


def _number(
    value: Decimal,
    *,
    maximum: Decimal,
    negative_code: str,
    zero_code: str | None = None,
) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise TargetPlanError("INVALID_DECIMAL")
    if value < 0:
        raise TargetPlanError(negative_code)
    if zero_code is not None and value == 0:
        raise TargetPlanError(zero_code)
    if value > maximum:
        raise TargetPlanError("DECIMAL_RANGE")
    if max(0, -value.as_tuple().exponent) > _MAX_PRECISION:
        raise TargetPlanError("PRECISION_LIMIT")
    return value


def _text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def plan_target(
    *,
    target_id: str,
    source_signal_ids: tuple[str, ...],
    effective_at: str,
    target_instrument_id: str,
    instrument_id: str,
    target_weight: Decimal,
    account_equity: Decimal,
    available_cash: Decimal,
    current_quantity: Decimal,
    ask_price: Decimal,
    fee_rate: Decimal,
    step_size: Decimal,
    min_quantity: Decimal,
    min_notional: Decimal,
    leverage: Decimal,
) -> TargetPlan:
    """Return one immutable executable quantity or an explicit no-order plan."""

    if (
        type(target_id) is not str
        or not target_id
        or type(effective_at) is not str
        or not effective_at
        or type(source_signal_ids) is not tuple
        or not source_signal_ids
        or any(type(item) is not str or not item for item in source_signal_ids)
        or len(set(source_signal_ids)) != len(source_signal_ids)
    ):
        raise TargetPlanError("INVALID_IDENTITY")
    if target_instrument_id != instrument_id or type(instrument_id) is not str:
        raise TargetPlanError("CROSS_INSTRUMENT")

    if type(target_weight) is not Decimal or not target_weight.is_finite():
        raise TargetPlanError("INVALID_DECIMAL")
    if not Decimal(0) <= target_weight <= Decimal(1):
        raise TargetPlanError("TARGET_WEIGHT_OUT_OF_RANGE")
    weight = _number(
        target_weight,
        maximum=Decimal(1),
        negative_code="TARGET_WEIGHT_OUT_OF_RANGE",
    )
    if (
        type(leverage) is not Decimal
        or not leverage.is_finite()
        or leverage != Decimal(1)
    ):
        raise TargetPlanError("UNSUPPORTED_LEVERAGE")
    equity = _number(
        account_equity,
        maximum=_PRICE_MAX,
        negative_code="NEGATIVE_BALANCE",
    )
    cash = _number(
        available_cash,
        maximum=_PRICE_MAX,
        negative_code="NEGATIVE_BALANCE",
    )
    current = _number(
        current_quantity,
        maximum=_QUANTITY_MAX,
        negative_code="SHORT_POSITION",
    )
    price = _number(
        ask_price,
        maximum=_PRICE_MAX,
        negative_code="INVALID_PRICE",
        zero_code="INVALID_PRICE",
    )
    fee = _number(
        fee_rate,
        maximum=Decimal(1),
        negative_code="INVALID_FEE_RATE",
    )
    if fee == 1:
        raise TargetPlanError("INVALID_FEE_RATE")
    step = _number(
        step_size,
        maximum=_QUANTITY_MAX,
        negative_code="INVALID_INCREMENT",
        zero_code="INVALID_INCREMENT",
    )
    minimum_quantity = _number(
        min_quantity,
        maximum=_QUANTITY_MAX,
        negative_code="INVALID_MINIMUM",
        zero_code="INVALID_MINIMUM",
    )
    minimum_notional = _number(
        min_notional,
        maximum=_PRICE_MAX,
        negative_code="INVALID_MINIMUM",
        zero_code="INVALID_MINIMUM",
    )
    try:
        with localcontext(_CONTEXT):
            if current % step:
                raise TargetPlanError("CURRENT_QUANTITY_OFF_STEP")
            desired = _floor_step(equity * weight / price, step)
            if desired > _QUANTITY_MAX:
                raise TargetPlanError("DECIMAL_RANGE")
            if desired > current:
                affordable = _floor_step(cash / (price * (Decimal(1) + fee)), step)
                target = current + min(desired - current, affordable)
                if target == current:
                    return _plan(
                        target_id,
                        source_signal_ids,
                        effective_at,
                        instrument_id,
                        current,
                        current,
                        price,
                        "INSUFFICIENT_CASH",
                    )
            else:
                target = desired
            delta = target - current
            if delta == 0:
                return _plan(
                    target_id,
                    source_signal_ids,
                    effective_at,
                    instrument_id,
                    current,
                    target,
                    price,
                    "ALREADY_AT_TARGET",
                )
            if abs(delta) < minimum_quantity:
                target = current
                reason = "BELOW_MIN_QUANTITY"
            elif abs(delta) * price < minimum_notional:
                target = current
                reason = "BELOW_MIN_NOTIONAL"
            else:
                reason = "ORDER"
    except DecimalException as exc:
        raise TargetPlanError("DECIMAL_RANGE") from exc

    return _plan(
        target_id,
        source_signal_ids,
        effective_at,
        instrument_id,
        current,
        target,
        price,
        reason,
    )


def _plan(
    target_id: str,
    source_signal_ids: tuple[str, ...],
    effective_at: str,
    instrument_id: str,
    current: Decimal,
    target: Decimal,
    price: Decimal,
    reason: str,
) -> TargetPlan:
    try:
        with localcontext(_CONTEXT):
            delta = target - current
            return TargetPlan(
                target_id=target_id,
                source_signal_ids=source_signal_ids,
                effective_at=effective_at,
                instrument_id=instrument_id,
                current_quantity=_text(current),
                target_quantity=_text(target),
                delta=_text(delta),
                side="BUY" if delta > 0 else "SELL" if delta < 0 else None,
                price_basis=_text(price),
                notional=_text(abs(delta) * price),
                reason=reason,
            )
    except DecimalException as exc:
        raise TargetPlanError("DECIMAL_RANGE") from exc


__all__ = ["TargetPlan", "TargetPlanError", "plan_target"]
