"""Decimal synthetic next-open accounting used by the P3 parity fixture."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore
from packages.alpha_lifecycle.candidates import _next_weight
from packages.alpha_lifecycle.contracts.data import DailyBar, DatasetEvidence
from packages.alpha_lifecycle.contracts.execution import HoldoutManifest, InstrumentSpec
from packages.alpha_lifecycle.contracts.policy import CandidateSpec
from packages.alpha_lifecycle.contracts.results import ExecutableResult, NativeTraceRow
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def _text(value: Decimal) -> str:
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if Decimal(result) == 0 else result


def _grid(value: Decimal, increment: Decimal, rounding: str) -> Decimal:
    return (value / increment).to_integral_value(rounding=rounding) * increment


def synthetic_next_open_accounting(
    *,
    targets: tuple[int, ...],
    opens: tuple[Decimal, ...],
    boundary_times_ns: tuple[int, ...],
    source_days: tuple[str, ...],
    price_increment: Decimal,
    size_increment: Decimal,
    quote_quantum: Decimal,
    minimum_notional: Decimal,
    initial_cash: Decimal = Decimal("100000"),
) -> tuple[dict[str, object], ...]:
    if not (len(targets) == len(opens) == len(boundary_times_ns) == len(source_days)):
        raise ValueError("synthetic input lengths differ")
    if any(target not in (0, 1) for target in targets):
        raise ValueError("targets must be flat or long")
    rows: list[dict[str, object]] = []
    cash, position = initial_cash, Decimal(0)

    def add(kind: str, side: str, time_ns: int, day: str, *, price=None, quantity=None, fee=None) -> None:
        rows.append({
            "sequence": len(rows), "event_time_ns": time_ns, "init_time_ns": time_ns,
            "kind": kind, "side": side,
            "price": None if price is None else _text(price),
            "quantity": None if quantity is None else _text(quantity),
            "fee_quote": None if fee is None else _text(fee),
            "cash_after": _text(cash), "position_after": _text(position),
            "source_day": day,
        })

    with localcontext() as context:
        context.prec = 50
        for target, open_price, boundary, day in zip(
            targets, opens, boundary_times_ns, source_days, strict=True
        ):
            if open_price <= 0:
                raise ValueError("synthetic open price must be positive")
            current = int(position > 0)
            add("SIGNAL", "NONE", boundary, day)
            if target != current:
                side = "BUY" if target else "SELL"
                add("ORDER", side, boundary, day)
                price = _grid(
                    open_price * (Decimal("1.001") if target else Decimal("0.999")),
                    price_increment,
                    ROUND_CEILING if target else ROUND_FLOOR,
                )
                add("QUOTE", side, boundary + 1, day, price=price)
                if target:
                    quantity = _grid(
                        cash * Decimal("0.9975") / (price * Decimal("1.001")),
                        size_increment,
                        ROUND_FLOOR,
                    )
                else:
                    quantity = position
                if quantity <= 0 or quantity * price < minimum_notional:
                    raise ValueError("minimum notional or nonzero-size policy failed")
                fee = (quantity * price * Decimal("0.001")).quantize(
                    quote_quantum, rounding=ROUND_HALF_EVEN
                )
                if target:
                    cash -= quantity * price + fee
                    position = quantity
                else:
                    cash += quantity * price - fee
                    position = Decimal(0)
                add("FILL", side, boundary + 1, day, price=price, quantity=quantity, fee=fee)
            mark = _grid(open_price, price_increment, ROUND_HALF_EVEN)
            add("LIQUIDATION" if target == 0 and current == 1 and boundary == boundary_times_ns[-1] else "MARK",
                "NONE", boundary + 1, day, price=mark)
    return tuple(rows)


def _read(store: ArtifactStore, ref: ArtifactRefV1, model):
    return model.model_validate_json(store.read_bytes(ref))


def _ns(value: datetime) -> int:
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _seal(store: ArtifactStore, value: object) -> ArtifactRefV1:
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def run_executable_reference(
    manifest: HoldoutManifest,
    spec: InstrumentSpec,
    reader: ArtifactStore,
) -> ExecutableResult:
    manifest = HoldoutManifest.model_validate(manifest)
    spec = InstrumentSpec.model_validate(spec)
    candidate = _read(reader, manifest.candidate_spec_ref, CandidateSpec)
    context = _read(reader, manifest.context_dataset_ref, DatasetEvidence)
    holdout = _read(reader, manifest.holdout_dataset_ref, DatasetEvidence)
    buffer = _read(reader, manifest.buffer_ref, DatasetEvidence)
    context_bars = tuple(_read(reader, ref, DailyBar) for ref in context.row_refs)
    holdout_bars = tuple(_read(reader, ref, DailyBar) for ref in holdout.row_refs)
    buffer_bars = tuple(_read(reader, ref, DailyBar) for ref in buffer.row_refs)
    if not context_bars or not holdout_bars or len(buffer_bars) != 1:
        raise ValueError("holdout execution requires context, holdout, and one buffer bar")
    bars = context_bars + holdout_bars
    first = len(context_bars) - 1
    weight = 0
    targets = []
    for index in range(first, len(bars) - 1):
        weight = _next_weight(candidate.parameters, bars, index, weight)
        targets.append(weight)
    targets.append(0)
    decision_bars = (context_bars[-1], *holdout_bars)
    open_bars = (*holdout_bars, buffer_bars[0])
    rows = synthetic_next_open_accounting(
        targets=tuple(targets), opens=tuple(Decimal(bar.open) for bar in open_bars),
        boundary_times_ns=tuple(_ns(bar.closed_at_exclusive) for bar in decision_bars),
        source_days=tuple(bar.date.isoformat() for bar in decision_bars),
        price_increment=Decimal(spec.price_increment),size_increment=Decimal(spec.size_increment),
        quote_quantum=Decimal(spec.quote_quantum),minimum_notional=Decimal(spec.minimum_notional),
    )
    ref_by_day = {
        bar.date.isoformat(): ref
        for bar, ref in zip(decision_bars, (context.row_refs[-1], *holdout.row_refs), strict=True)
    }
    trace = []
    equities = [Decimal("100000")]
    for row in rows:
        payload = {
            "schema_version":"p3-native-trace-row-v1", **row,
            "source_artifact_ref":ref_by_day[row["source_day"]],
        }
        payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        trace.append(NativeTraceRow.model_validate(payload))
        if row["kind"] in {"MARK","LIQUIDATION"}:
            equities.append(Decimal(row["cash_after"])+Decimal(row["position_after"])*Decimal(row["price"]))
    ending_cash = Decimal(rows[-1]["cash_after"])
    peak = equities[0]
    drawdown = Decimal(0)
    for equity in equities:
        peak = max(peak,equity)
        drawdown = max(drawdown,(peak-equity)/peak)
    manifest_ref = _seal(reader, manifest)
    payload = {
        "schema_version":"p3-executable-result-v1","manifest_ref":manifest_ref,
        "parity_policy_digest":manifest.policy_digest,"instrument_spec_ref":_seal(reader,spec),
        "transition_trace_ref":_seal(reader,{"targets":targets}),
        "fill_trace_ref":_seal(reader,trace),"ending_cash":_text(ending_cash),
        "ending_position":rows[-1]["position_after"],
        "net_return":_text(ending_cash/Decimal("100000")-1),"max_drawdown":_text(drawdown),
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return ExecutableResult.model_validate(payload)


__all__ = ["run_executable_reference", "synthetic_next_open_accounting"]
