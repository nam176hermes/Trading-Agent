"""Independent Decimal reference oracle for the fixed simulation scenarios."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext

from .scenarios import BacktestScenarioV1


_CONTEXT = Context(prec=80, Emin=-999, Emax=999)


def _canonical(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def calculate_reference_outcome(scenario: BacktestScenarioV1):
    """Calculate accounting independently of the engine-owned Nautilus strategy."""
    from .result import BacktestExpectedOutcomeV1

    if type(scenario) is not BacktestScenarioV1:
        raise TypeError("exact BacktestScenarioV1 is required")
    with localcontext(_CONTEXT):
        target = scenario.target_quantity
        side = Decimal(1) if target > 0 else Decimal(-1)
        remaining, filled, position = target, Decimal(0), Decimal(0)
        entry_notional = average_entry = fees = realized = Decimal(0)
        total_fills, total_orders, total_positions = 0, 1, 0
        records: list[dict[str, object]] = [{"event_type": "order-created", "quantity": _text(target), "sequence": 0}]
        rate = scenario.slippage_bps / Decimal(10_000)
        last_close = Decimal(0)
        for event in scenario.events:
            last_close = Decimal(event.close)
            if remaining == 0:
                break
            if not event.session_open:
                records.append({"event_type": "session-closed", "market_sequence": event.sequence, "sequence": len(records)})
                continue
            if _time(event.event_time) - _time(event.quote_time) > timedelta(seconds=scenario.stale_quote_threshold_seconds):
                records.append({"event_type": "quote-rejected", "market_sequence": event.sequence, "reason": "stale", "sequence": len(records)})
                continue
            available = min(Decimal(event.volume), scenario.liquidity_limit, abs(remaining))
            if available <= 0:
                records.append({"event_type": "liquidity-rejected", "market_sequence": event.sequence, "reason": "zero", "sequence": len(records)})
                continue
            quote = Decimal(event.ask if side > 0 else event.bid)
            price = quote * (Decimal(1) + rate if side > 0 else Decimal(1) - rate)
            quantity = side * available
            filled += quantity
            remaining = target - filled
            position += quantity
            entry_notional += abs(quantity) * price
            average_entry = entry_notional / abs(filled)
            fees += abs(quantity) * price * scenario.fee_rate
            total_fills += 1
            total_positions = 1
            records.append({"event_time": event.event_time, "event_type": "fill", "price": _text(price), "quantity": _text(quantity), "sequence": len(records)})
            stop_hit = scenario.stop_price is not None and (Decimal(event.low) <= scenario.stop_price if position > 0 else Decimal(event.high) >= scenario.stop_price)
            take_hit = scenario.take_profit_price is not None and (Decimal(event.high) >= scenario.take_profit_price if position > 0 else Decimal(event.low) <= scenario.take_profit_price)
            exit_trigger = scenario.stop_price if stop_hit else scenario.take_profit_price if take_hit else None
            if exit_trigger is not None:
                total_orders += 1
                records.append({"event_type": "exit-order-created", "reason": "stop" if stop_hit else "take-profit", "sequence": len(records)})
                exit_price = exit_trigger * (Decimal(1) - rate if position > 0 else Decimal(1) + rate)
                close_quantity = -position
                realized += (exit_price - average_entry) * position
                fees += abs(close_quantity) * exit_price * scenario.fee_rate
                total_fills += 1
                records.append({"event_time": event.event_time, "event_type": "fill", "price": _text(exit_price), "quantity": _text(close_quantity), "sequence": len(records)})
                position = Decimal(0)
                records.append({"event_type": "position-closed", "sequence": len(records)})
                break
        unrealized = (last_close - average_entry) * position if position else Decimal(0)
        return BacktestExpectedOutcomeV1(
            scenario_id=scenario.scenario_id,
            scenario_digest=scenario.scenario_digest,
            event_digest=hashlib.sha256(_canonical(records)).hexdigest(),
            iterations=len(scenario.events), total_events=len(records), total_orders=total_orders,
            total_fills=total_fills, total_positions=total_positions,
            filled_quantity=filled, remaining_quantity=remaining, position_quantity=position,
            average_entry_price=average_entry, fees=fees, realized_pnl=realized,
            unrealized_pnl=unrealized, stop_take_profit_precedence="stop-first",
        )
