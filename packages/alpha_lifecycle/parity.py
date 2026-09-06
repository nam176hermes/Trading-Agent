"""Per-field P3 synthetic-reference/native comparison."""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from typing import Protocol

from packages.alpha_lifecycle.contracts.results import (
    ExecutableResult,
    NativeTraceRow,
    ParityResult,
    ReplayReceipt,
)
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


class ArtifactStore(Protocol):
    def read_bytes(self, ref: ArtifactRefV1) -> bytes: ...
    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1: ...


_EXACT = (
    "sequence", "event_time_ns", "init_time_ns", "kind", "side", "price",
    "quantity", "position_after", "source_day", "source_artifact_ref",
)


def _read(store: ArtifactStore, ref: ArtifactRefV1, model):
    return model.model_validate_json(store.read_bytes(ref))


def _trace(store: ArtifactStore, result: ExecutableResult) -> tuple[NativeTraceRow, ...]:
    value = json.loads(store.read_bytes(result.fill_trace_ref))
    return tuple(NativeTraceRow.model_validate(item) for item in value)


def compare_executable_results(
    reference_ref: ArtifactRefV1,
    native_result_refs: tuple[ArtifactRefV1, ArtifactRefV1, ArtifactRefV1],
    native_receipt_refs: tuple[ArtifactRefV1, ArtifactRefV1, ArtifactRefV1],
    store: ArtifactStore,
    *,
    quote_quantum: Decimal,
) -> ParityResult:
    reference = _read(store, reference_ref, ExecutableResult)
    reference_trace = _trace(store, reference)
    exact_failures: list[str] = []
    numeric_failures: list[str] = []
    native_bytes: list[bytes] = []
    if len(set(native_receipt_refs)) != 3:
        exact_failures.append("native-receipts-not-independent")
    for index, result_ref in enumerate(native_result_refs):
        receipt = _read(store, native_receipt_refs[index], ReplayReceipt)
        if receipt.replicate != f"R{index + 1}" or receipt.result_ref != result_ref:
            exact_failures.append(f"R{index + 1}:receipt-binding")
        raw = store.read_bytes(result_ref)
        native_bytes.append(raw)
        native = ExecutableResult.model_validate_json(raw)
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


__all__ = ["compare_executable_results"]
