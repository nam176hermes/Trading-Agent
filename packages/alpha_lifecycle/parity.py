"""Per-field P3 synthetic-reference/native comparison."""

from __future__ import annotations

from decimal import Decimal
import hashlib
from typing import Literal
from pydantic import TypeAdapter

from packages.alpha_lifecycle.pit_evidence import _reference
from packages.alpha_lifecycle.contracts.models import DigestModel

from packages.alpha_lifecycle.contracts.results import (
    ExecutableResult,
    NativeTraceRow,
    ParityResult,
    ReplayReceipt,
)
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.alpha_lifecycle.replica_store import ArtifactStore, ReadbackStore, _read


_EXACT = (
    "sequence", "event_time_ns", "init_time_ns", "kind", "side", "price",
    "quantity", "position_after", "source_day", "source_artifact_ref",
)


def _trace(store: ArtifactStore, result: ExecutableResult) -> tuple[NativeTraceRow, ...]:
    _reference(result.fill_trace_ref, 64 * 1024**2)
    raw = store.read_bytes(result.fill_trace_ref)
    rows = TypeAdapter(tuple[NativeTraceRow, ...]).validate_json(raw)
    if canonical_json_bytes(rows) != raw:
        raise ValueError("native trace artifact is not canonical")
    return rows


def compare_executable_results(
    reference_ref: ArtifactRefV1,
    native_result_refs: tuple[ArtifactRefV1, ArtifactRefV1, ArtifactRefV1],
    native_receipt_refs: tuple[ArtifactRefV1, ArtifactRefV1, ArtifactRefV1],
    store: ArtifactStore,
    *,
    quote_quantum: Decimal,
) -> ParityResult:
    if not isinstance(quote_quantum, Decimal) or not quote_quantum.is_finite() or quote_quantum <= 0:
        raise ValueError("parity quote quantum must be finite and positive")
    if len(native_result_refs) != 3 or len(native_receipt_refs) != 3:
        raise ValueError("parity requires exactly three native replicas")
    reference = _read(store, reference_ref, ExecutableResult)
    reference_trace = _trace(store, reference)
    exact_failures: list[str] = []
    numeric_failures: list[str] = []
    native_bytes: list[bytes] = []
    if len(set(native_receipt_refs)) != 3:
        exact_failures.append("native-receipts-not-independent")
    receipt_identity = None
    for index, result_ref in enumerate(native_result_refs):
        receipt = _read(store, native_receipt_refs[index], ReplayReceipt)
        identity = (receipt.source, receipt.environment_ref, receipt.sandbox_policy_digest,
            receipt.logical_trial_id, receipt.output_inventory_digest)
        if (receipt.replicate != f"R{index + 1}" or receipt.result_ref != result_ref
            or receipt.manifest_digest != reference.manifest_ref.content_sha256
            or receipt.completed_at < receipt.started_at
            or (receipt_identity is not None and identity != receipt_identity)):
            exact_failures.append(f"R{index + 1}:receipt-binding")
        receipt_identity = identity
        native = _read(store, result_ref, ExecutableResult)
        native_bytes.append(canonical_json_bytes(native))
        if (
            native.manifest_ref != reference.manifest_ref
            or native.parity_policy_digest != reference.parity_policy_digest
            or native.instrument_spec_ref != reference.instrument_spec_ref
            or native.transition_trace_ref != reference.transition_trace_ref
        ):
            exact_failures.append(f"R{index + 1}:identity")
        rows = _trace(store, native)
        if len(rows) != len(reference_trace):
            exact_failures.append(f"R{index + 1}:row-count")
            continue
        for row_index, (expected, observed) in enumerate(zip(reference_trace, rows, strict=True)):
            if any(getattr(expected, field) != getattr(observed, field) for field in _EXACT):
                exact_failures.append(f"R{index + 1}:row-{row_index}")
            expected_fee = Decimal(expected.fee_quote or "0")
            observed_fee = Decimal(observed.fee_quote or "0")
            if abs(expected_fee - observed_fee) > quote_quantum:
                numeric_failures.append(f"R{index + 1}:fee-{row_index}")
            # Exact position/price comparison makes this also the per-row
            # equity difference; checking only ending cash misses a forged path.
            if abs(Decimal(expected.cash_after) - Decimal(observed.cash_after)) > 2 * quote_quantum:
                numeric_failures.append(f"R{index + 1}:equity-{row_index}")
        if native.ending_position != reference.ending_position:
            exact_failures.append(f"R{index + 1}:ending-position")
        if abs(Decimal(native.ending_cash) - Decimal(reference.ending_cash)) > 2 * quote_quantum:
            numeric_failures.append(f"R{index + 1}:ending-equity")
    if len(set(native_bytes)) != 1:
        exact_failures.append("native-replicas-differ")
    comparison = {
        "schema_version": "p3-parity-comparison-v1",
        "exact_failures": tuple(sorted(set(exact_failures))),
        "numeric_failures": tuple(sorted(set(numeric_failures))),
    }
    comparison_ref = store.put_bytes(canonical_json_bytes(comparison), media_type="application/json")
    payload = {
        "schema_version": "p3-parity-result-v1", "reference_ref": reference_ref,
        "native_result_refs": native_result_refs, "native_receipt_refs": native_receipt_refs,
        "comparison_ref": comparison_ref, "exact_fields_pass": not exact_failures,
        "numeric_fields_pass": not numeric_failures,
        "verdict": "PASS" if not exact_failures and not numeric_failures else "FAIL",
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return ParityResult.model_validate(payload)


class ParityPair(DigestModel):
    """Private retained pair; parent launch custody remains a separate obligation."""

    schema_version: Literal['p3-parity-pair-v1']
    manifest_ref: ArtifactRefV1
    instrument_spec_ref: ArtifactRefV1
    primary_reference_ref: ArtifactRefV1
    baseline_reference_ref: ArtifactRefV1
    primary_parity_ref: ArtifactRefV1
    baseline_parity_ref: ArtifactRefV1
    verdict: Literal['PASS', 'FAIL']


def build_parity_pair(*, manifest_ref: ArtifactRefV1, instrument_spec_ref: ArtifactRefV1,
    primary_reference_ref: ArtifactRefV1, baseline_reference_ref: ArtifactRefV1,
    primary_parity_ref: ArtifactRefV1, baseline_parity_ref: ArtifactRefV1,
    store: ArtifactStore,
) -> ParityPair:
    """Recompute both retained comparisons against exact expected role bindings."""
    from packages.alpha_lifecycle.contracts.execution import HoldoutManifest, InstrumentSpec, EnvironmentIdentity
    from packages.alpha_lifecycle.executable_reference import validate_executable_summary
    from packages.alpha_lifecycle.pit_evidence import _ReadBudget
    reader = ReadbackStore(_ReadBudget(store), store)
    for ref in (manifest_ref, instrument_spec_ref, primary_reference_ref, baseline_reference_ref,
        primary_parity_ref, baseline_parity_ref):
        _reference(ref, 65536)
    manifest = _read(reader, manifest_ref, HoldoutManifest)
    spec = _read(reader, instrument_spec_ref, InstrumentSpec)
    _reference(manifest.environment_ref, 65536)
    environment = _read(reader, manifest.environment_ref, EnvironmentIdentity)
    seen: set[str] = set()
    comparisons: list[ParityResult] = []
    for reference_ref, parity_ref in ((primary_reference_ref, primary_parity_ref),
        (baseline_reference_ref, baseline_parity_ref)):
        reference = _read(reader, reference_ref, ExecutableResult)
        validate_executable_summary(reference, reader)
        parity = _read(reader, parity_ref, ParityResult)
        if (reference.manifest_ref != manifest_ref or reference.instrument_spec_ref != instrument_spec_ref
            or reference.parity_policy_digest != manifest.policy_digest or parity.reference_ref != reference_ref):
            raise ValueError('native pair role or manifest binding differs')
        for ref in parity.native_receipt_refs:
            _reference(ref, 65536)
            receipt = _read(reader, ref, ReplayReceipt)
            if (ref.content_sha256 in seen or receipt.source != manifest.source
                or receipt.environment_ref != manifest.environment_ref
                or receipt.sandbox_policy_digest != environment.sandbox_policy_digest
                or receipt.logical_trial_id != 'p3-native-parity-v1'):
                raise ValueError('native pair needs six independently bound receipts')
            seen.add(ref.content_sha256)
        for ref in parity.native_result_refs:
            _reference(ref, 65536)
            validate_executable_summary(_read(reader, ref, ExecutableResult), reader)
        first, second, third = parity.native_result_refs
        r1, r2, r3 = parity.native_receipt_refs
        recomputed = compare_executable_results(reference_ref, (first, second, third), (r1, r2, r3),
            reader, quote_quantum=Decimal(spec.quote_quantum))
        if recomputed != parity:
            raise ValueError('native pair comparison differs from retained evidence')
        comparisons.append(parity)
    payload = dict(schema_version='p3-parity-pair-v1', manifest_ref=manifest_ref,
        instrument_spec_ref=instrument_spec_ref, primary_reference_ref=primary_reference_ref,
        baseline_reference_ref=baseline_reference_ref, primary_parity_ref=primary_parity_ref,
        baseline_parity_ref=baseline_parity_ref,
        verdict='PASS' if all(item.verdict == 'PASS' for item in comparisons) else 'FAIL')
    return ParityPair.model_validate({**payload, 'digest':hashlib.sha256(canonical_json_bytes(payload)).hexdigest()})


def validate_parity_pair(ref: ArtifactRefV1, *, manifest_ref: ArtifactRefV1,
    instrument_spec_ref: ArtifactRefV1, primary_reference_ref: ArtifactRefV1,
    baseline_reference_ref: ArtifactRefV1, store: ArtifactStore,
) -> ParityPair:
    _reference(ref, 65536)
    pair = _read(store, ref, ParityPair)
    expected = build_parity_pair(manifest_ref=manifest_ref, instrument_spec_ref=instrument_spec_ref,
        primary_reference_ref=primary_reference_ref, baseline_reference_ref=baseline_reference_ref,
        primary_parity_ref=pair.primary_parity_ref, baseline_parity_ref=pair.baseline_parity_ref, store=store)
    if expected != pair:
        raise ValueError('native pair differs from its expected phase-exit bindings')
    return pair


__all__ = ["compare_executable_results", "build_parity_pair", "validate_parity_pair", "ParityPair"]
