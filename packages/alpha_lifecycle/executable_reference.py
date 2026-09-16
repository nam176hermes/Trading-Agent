"""Frozen next-open calculation; protected authority belongs to the parent."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
from typing import Annotated, Literal

from pydantic import Field

from packages.alpha_lifecycle.replica_store import ArtifactStore, _read
from packages.alpha_lifecycle.baselines import BaselineId, baseline_weights_with_reset
from packages.alpha_lifecycle.candidates import _next_weight
from packages.alpha_lifecycle.contracts.authority import FamilyReview, PrimarySelection
from packages.alpha_lifecycle.contracts.base import DecimalText, DigestModel, Sha256, SourceIdentity, StrictModel
from packages.alpha_lifecycle.contracts.data import BufferOpen, DailyBar, DatasetEvidence, Day
from packages.alpha_lifecycle.contracts.execution import EnvironmentIdentity, HoldoutManifest, InputSet, InstrumentSpec
from packages.alpha_lifecycle.contracts.lifecycle import RegistrationProof
from packages.alpha_lifecycle.contracts.policy import CandidateSpec
from packages.alpha_lifecycle.contracts.results import BaselineSelection, ExecutableResult, NativeTraceRow
from packages.alpha_lifecycle.data_view import to_daily_close
from packages.alpha_lifecycle.pit_evidence import _ReadBudget, _reference
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
        context.rounding = ROUND_HALF_EVEN
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


class ResearchInstrument(DigestModel):
    """Source-bound simulation assumptions; its digest grants no producer authority."""
    schema_version: Literal['p3-research-instrument-v1']
    source: SourceIdentity
    policy_digest: Sha256
    instrument: Literal['BTCUSDT.BINANCE']
    classification: Literal['APPROVED_RESEARCH_ASSUMPTION_NOT_CURRENT_OR_HISTORICAL_VENUE_FILTER']
    price_increment: Literal['0.01']
    size_increment: Literal['0.00001']
    quote_quantum: Literal['0.01']
    minimum_notional: Literal['10']


def _dataset(reader: ArtifactStore, ref: ArtifactRefV1, segment: str,
    start: date, end: date) -> DatasetEvidence:
    _reference(ref,2097152)
    value=_read(reader,ref,DatasetEvidence)
    if (value.segment!=segment or value.date_range.start!=start or value.date_range.end!=end
        or value.usable_rows!=(end-start).days+1
        or value.ordered_rows_digest!=hashlib.sha256(canonical_json_bytes(
            [row.content_sha256 for row in value.row_refs])).hexdigest()
        or len({row.content_sha256 for row in value.row_refs})!=value.usable_rows):
        raise ValueError('executable dataset role, dates or ordered row commitment differs')
    for row in value.row_refs:
        _reference(row,65536)
    return value


def _daily_rows(reader: ArtifactStore, dataset: DatasetEvidence,
    refs: tuple[ArtifactRefV1,...], first: date) -> tuple[DailyBar,...]:
    rows=tuple(_read(reader,ref,DailyBar) for ref in refs)
    for index,bar in enumerate(rows):
        day=first+timedelta(days=index)
        boundary=datetime(day.year,day.month,day.day,tzinfo=UTC)
        if (bar.date!=day or bar.opened_at!=boundary or bar.ingested_at>dataset.observed_cutoff):
            raise ValueError('executable daily row date, boundary or observation differs')
    return rows


def _execution_inputs(
    manifest: HoldoutManifest, spec: InstrumentSpec, reader: ArtifactStore,
) -> tuple[CandidateSpec, DatasetEvidence, DatasetEvidence, tuple[DailyBar, ...], tuple[DailyBar, ...], BufferOpen]:
    """Check the calculation view after parent-only publication/provenance reconstruction."""
    budget=_ReadBudget(reader)
    for ref in (manifest.candidate_spec_ref,manifest.research_registration_ref,
        manifest.primary_selection_ref,manifest.environment_ref,spec.security_master_ref):
        _reference(ref,65536)
    candidate=_read(budget,manifest.candidate_spec_ref,CandidateSpec)
    registration=_read(budget,manifest.research_registration_ref,RegistrationProof)
    primary=_read(budget,manifest.primary_selection_ref,PrimarySelection)
    for ref in (registration.input_set_ref,registration.baseline_selection_ref,primary.family_review_ref):
        _reference(ref,65536)
    inputs=_read(budget,registration.input_set_ref,InputSet)
    baseline=_read(budget,registration.baseline_selection_ref,BaselineSelection)
    family=_read(budget,primary.family_review_ref,FamilyReview)
    _read(budget,manifest.environment_ref,EnvironmentIdentity)
    instrument=_read(budget,spec.security_master_ref,ResearchInstrument)
    if (inputs.source!=manifest.source or inputs.policy_digest!=manifest.policy_digest
        or inputs.environment_ref!=manifest.environment_ref
        or inputs.dataset_evidence_ref!=manifest.context_dataset_ref
        or family.input_set_ref!=registration.input_set_ref
        or primary.outcome!='SELECTED' or primary.primary_alpha_id!=candidate.alpha_id
        or primary.primary_version!=candidate.version or primary.selection_policy_digest!=manifest.policy_digest
        or baseline.selection_policy_digest!=manifest.policy_digest
        or baseline.selected_id!=manifest.selected_baseline
        or baseline.selected_result.baseline_id.value!=manifest.selected_baseline
        or baseline.selected_result.baseline_version!='1.0.0'):
        raise ValueError('executable input, registration, primary or baseline binding differs')
    if (instrument.source!=manifest.source or instrument.policy_digest!=manifest.policy_digest
        or any(getattr(spec,name)!=getattr(instrument,name) for name in
            ('instrument','price_increment','size_increment','quote_quantum','minimum_notional'))):
        raise ValueError('executable simulation instrument differs from its source-bound assumptions')
    context=_dataset(budget,manifest.context_dataset_ref,'RESEARCH',date(2018,1,1),date(2025,8,31))
    holdout=_dataset(budget,manifest.holdout_dataset_ref,'HOLDOUT',date(2025,9,1),date(2026,8,31))
    buffer=_dataset(budget,manifest.buffer_ref,'BUFFER',date(2026,9,1),date(2026,9,1))
    all_refs=(*context.row_refs,*holdout.row_refs,*buffer.row_refs)
    if len({ref.content_sha256 for ref in all_refs})!=len(all_refs):
        raise ValueError('executable dataset roles overlap')
    if (baseline.selected_result.dataset_snapshot_sha256!=context.snapshot_ref.content_sha256
        or baseline.selected_result.cost_model_sha256!=hashlib.sha256(canonical_json_bytes(inputs.cost_model)).hexdigest()):
        raise ValueError('executable baseline data or cost identity differs')
    context_bars=_daily_rows(budget,context,context.row_refs[-301:],date(2024,11,4))
    holdout_bars=_daily_rows(budget,holdout,holdout.row_refs,date(2025,9,1))
    opening=_read(budget,buffer.row_refs[0],BufferOpen)
    if opening.date!=date(2026,9,1) or opening.observed_at>buffer.observed_cutoff:
        raise ValueError('executable buffer day or observation differs')
    # Snapshot/Parquet provenance is authenticated by the parent and excluded from child mounts.
    return candidate,context,holdout,context_bars,holdout_bars,opening


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
    return _run_reference(manifest,spec,reader,selected_baseline=False)


def run_selected_baseline_reference(
    manifest: HoldoutManifest, spec: InstrumentSpec, reader: ArtifactStore,
) -> ExecutableResult:
    return _run_reference(manifest,spec,reader,selected_baseline=True)


def _run_reference(manifest: HoldoutManifest, spec: InstrumentSpec,
    reader: ArtifactStore, *, selected_baseline: bool) -> ExecutableResult:
    with localcontext(prec=50,rounding=ROUND_HALF_EVEN):
        return _calculate_reference(manifest,spec,reader,selected_baseline=selected_baseline)


class NextOpenStep(StrictModel):
    target: Literal[0,1]
    open: DecimalText
    boundary_ns: Annotated[int,Field(gt=0)]
    source_day: Day
    source_artifact_ref: ArtifactRefV1


def next_open_steps(manifest: HoldoutManifest, spec: InstrumentSpec,
    reader: ArtifactStore, *, selected_baseline: bool) -> tuple[NextOpenStep,...]:
    with localcontext(prec=50,rounding=ROUND_HALF_EVEN):
        return _next_open_steps(manifest,spec,reader,selected_baseline=selected_baseline)


def _next_open_steps(manifest: HoldoutManifest, spec: InstrumentSpec,
    reader: ArtifactStore, *, selected_baseline: bool) -> tuple[NextOpenStep,...]:
    manifest = HoldoutManifest.model_validate(manifest)
    spec = InstrumentSpec.model_validate(spec)
    candidate,context,holdout,context_bars,holdout_bars,opening=_execution_inputs(manifest,spec,reader)
    bars = context_bars + holdout_bars
    if selected_baseline:
        closes=tuple(to_daily_close(bar) for bar in bars)
        weights=baseline_weights_with_reset(BaselineId(manifest.selected_baseline),closes,
            score_start=closes[len(context_bars)-1].closed_at)
        targets=[int(weight) for weight in weights[:-1]]
    else:
        weight = 0
        targets = []
        for index in range(len(context_bars)-1,len(bars)-1):
            weight = _next_weight(candidate.parameters, bars, index, weight)
            targets.append(weight)
    targets.append(0)
    decision_bars = (context_bars[-1], *holdout_bars)
    open_bars = (*holdout_bars, opening)
    return tuple(NextOpenStep.model_validate(dict(target=target,open=bar.open,boundary_ns=_ns(decision.closed_at_exclusive),
        source_day=decision.date,source_artifact_ref=ref))
        for target,bar,decision,ref in zip(targets,open_bars,decision_bars,
            (context.row_refs[-1],*holdout.row_refs),strict=True))


def _calculate_reference(manifest: HoldoutManifest, spec: InstrumentSpec,
    reader: ArtifactStore, *, selected_baseline: bool) -> ExecutableResult:
    steps=next_open_steps(manifest,spec,reader,selected_baseline=selected_baseline)
    rows = synthetic_next_open_accounting(
        targets=tuple(step.target for step in steps), opens=tuple(Decimal(step.open) for step in steps),
        boundary_times_ns=tuple(step.boundary_ns for step in steps),
        source_days=tuple(step.source_day.isoformat() for step in steps),
        price_increment=Decimal(spec.price_increment),size_increment=Decimal(spec.size_increment),
        quote_quantum=Decimal(spec.quote_quantum),minimum_notional=Decimal(spec.minimum_notional),
    )
    return executable_result_from_rows(manifest,spec,steps,rows,reader)


def executable_result_from_rows(manifest: HoldoutManifest,spec: InstrumentSpec,
    steps: tuple[NextOpenStep,...],rows: tuple[dict[str,object],...],reader: ArtifactStore,
) -> ExecutableResult:
    """Wrap observed calculation rows; this does not attest native execution."""
    with localcontext(prec=50,rounding=ROUND_HALF_EVEN):
        return _result_from_rows(manifest,spec,steps,rows,reader)


def _result_from_rows(manifest: HoldoutManifest,spec: InstrumentSpec,
    steps: tuple[NextOpenStep,...],rows: tuple[dict[str,object],...],reader: ArtifactStore,
) -> ExecutableResult:
    ref_by_day={step.source_day.isoformat():step.source_artifact_ref for step in steps}
    keys=set(NativeTraceRow.model_fields)-{'schema_version','digest','source_artifact_ref'}
    if not rows or len(rows)>len(steps)*5 or any(set(row)!=keys for row in rows):
        raise ValueError('native calculation row inventory differs')
    trace = []
    for row in rows:
        source_day = row["source_day"]
        if not isinstance(source_day, str) or source_day not in ref_by_day:
            raise ValueError("trace source day must be text")
        payload = {
            "schema_version":"p3-native-trace-row-v1", **row,
            "source_artifact_ref":ref_by_day[source_day],
        }
        payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        trace_row = NativeTraceRow.model_validate(payload)
        trace.append(trace_row)
    manifest_ref = _seal(reader, manifest)
    payload = {
        "schema_version":"p3-executable-result-v1","manifest_ref":manifest_ref,
        "parity_policy_digest":manifest.policy_digest,"instrument_spec_ref":_seal(reader,spec),
        "transition_trace_ref":_seal(reader,{"targets":[step.target for step in steps]}),
        "fill_trace_ref":_seal(reader,trace), **_trace_summary(tuple(trace)),
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return ExecutableResult.model_validate(payload)


def _trace_summary(trace: tuple[NativeTraceRow, ...]) -> dict[str, str]:
    if not trace:
        raise ValueError('executable summary requires a nonempty trace')
    with localcontext(prec=50, rounding=ROUND_HALF_EVEN):
        peak, drawdown = Decimal('100000'), Decimal(0)
        for row in trace:
            if row.kind in {'MARK', 'LIQUIDATION'}:
                if row.price is None:
                    raise ValueError('mark and liquidation traces require a price')
                equity = Decimal(row.cash_after) + Decimal(row.position_after) * Decimal(row.price)
                peak = max(peak, equity)
                drawdown = max(drawdown, (peak-equity)/peak)
        cash = Decimal(trace[-1].cash_after)
        return dict(ending_cash=_text(cash), ending_position=trace[-1].position_after,
            net_return=_text(cash/Decimal('100000')-1), max_drawdown=_text(drawdown))


def validate_executable_summary(result: ExecutableResult, reader: ArtifactStore) -> None:
    """Recompute summary fields; a canonical digest alone does not verify arithmetic."""
    from pydantic import TypeAdapter
    _reference(result.fill_trace_ref, 64*1024**2)
    raw = reader.read_bytes(result.fill_trace_ref)
    trace = TypeAdapter(tuple[NativeTraceRow, ...]).validate_json(raw)
    if canonical_json_bytes(trace) != raw or any(getattr(result, key) != value
        for key, value in _trace_summary(trace).items()):
        raise ValueError('executable summary differs from its retained trace')


__all__ = ["run_executable_reference", "run_selected_baseline_reference", "synthetic_next_open_accounting"]
