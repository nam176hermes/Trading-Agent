"""Validation of one complete request-bound P1 result stream."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
from typing import get_args

from packages.engine_contracts import (
    EngineCommandEnvelope,
    EngineEventEnvelope,
    EventFamily,
    RunBacktest,
    canonical_json_bytes,
    payload_digest,
)

from .events import (
    P1Event,
    P1RunCompleted,
    P1RunStarted,
    P1_EVENT_ADAPTER,
    P1_EVENT_MODELS,
    event_message_id,
)
from .state_machine import validate_event_stream


P1_RESULT_VALIDATOR_ID = "nautilus-p1-event-stream-v1"
P1_ENGINE_VERSION = "1.231.0"
P1_RUNTIME_FAMILY = "cython-v1"
P1_UPSTREAM_COMMIT = "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
P1_EVENT_FAMILIES = {
    "RunStarted": EventFamily.ENGINE_LIFECYCLE,
    "TargetAccepted": EventFamily.STRATEGY_LIFECYCLE,
    "TargetQuantityPlanned": EventFamily.STRATEGY_LIFECYCLE,
    "OrderSubmitted": EventFamily.ORDER_LIFECYCLE,
    "Fill": EventFamily.FILLS,
    "PositionObserved": EventFamily.POSITIONS,
    "AccountObserved": EventFamily.ACCOUNT_STATE,
    "RunCompleted": EventFamily.ENGINE_LIFECYCLE,
}
_EXPECTED_ATTRIBUTES = {
    str(get_args(model.model_fields["event_type"].annotation)[0]): frozenset(
        name
        for name, field in model.model_fields.items()
        if name != "event_type" and field.annotation is not type(None)
    )
    for model in P1_EVENT_MODELS
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


@dataclass(frozen=True, slots=True)
class P1ValidatedResult:
    batch_sha256: str
    semantic_sha256: str
    product_closure_sha256: str
    event_count: int
    target_count: int
    order_count: int
    fill_count: int
    final_cash: Decimal
    final_position: Decimal
    fees: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    events: tuple[P1Event, ...]


def _decode_event(envelope: EngineEventEnvelope) -> P1Event:
    event_type = envelope.payload.event_type
    try:
        expected_family = P1_EVENT_FAMILIES[event_type]
    except KeyError as exc:
        raise ValueError("P1 event type is not allowlisted") from exc
    if envelope.payload.family is not expected_family:
        raise ValueError("P1 event family is invalid")
    expected_attributes = _EXPECTED_ATTRIBUTES[event_type]
    attribute_names = tuple(
        attribute.name for attribute in envelope.payload.attributes
    )
    if (
        len(attribute_names) != len(set(attribute_names))
        or frozenset(attribute_names) != expected_attributes
    ):
        raise ValueError("P1 event attribute set is invalid")
    document: dict[str, object] = {"event_type": event_type}
    if "native_type" not in expected_attributes:
        document["native_type"] = None
    for attribute in envelope.payload.attributes:
        value: object = attribute.value
        if attribute.name == "source_signal_ids":
            if type(value) is not str:
                raise ValueError("P1 source signal IDs are invalid")
            try:
                value = json.loads(value)
            except (RecursionError, TypeError, ValueError) as exc:
                raise ValueError("P1 source signal IDs are invalid") from exc
            if (
                type(value) is not list
                or any(type(item) is not str for item in value)
                or canonical_json_bytes(value).decode() != attribute.value
            ):
                raise ValueError("P1 source signal IDs are invalid")
        document[attribute.name] = value
    return P1_EVENT_ADAPTER.validate_json(canonical_json_bytes(document))


def _validate_p1_result(
    request: EngineCommandEnvelope,
    envelopes: tuple[EngineEventEnvelope, ...],
    *,
    raw: bytes,
    expected_closure_digest: str,
) -> P1ValidatedResult:
    """Validate exact P1 lineage, request authority, state and final summary."""

    if (
        type(request) is not EngineCommandEnvelope
        or type(request.payload) is not RunBacktest
        or type(envelopes) is not tuple
        or not envelopes
        or any(type(envelope) is not EngineEventEnvelope for envelope in envelopes)
        or payload_digest(request.payload) != request.payload_digest
        or type(raw) is not bytes
        or not isinstance(expected_closure_digest, str)
        or _SHA256.fullmatch(expected_closure_digest) is None
    ):
        raise ValueError("P1 result authority is invalid")
    expected_raw = b"".join(
        canonical_json_bytes(envelope) + b"\n" for envelope in envelopes
    )
    if raw != expected_raw:
        raise ValueError("P1 result bytes do not match the event batch")

    events: list[P1Event] = []
    for envelope in envelopes:
        if payload_digest(envelope.payload) != envelope.payload_digest:
            raise ValueError("P1 event payload digest is invalid")
        event = _decode_event(envelope)
        if (
            envelope.message_id != event_message_id(request.message_id, event)
            or envelope.correlation_id != request.correlation_id
            or envelope.causation_id != request.causation_id
            or envelope.engine_run_id != request.engine_run_id
            or envelope.stream_sequence != event.sequence
            or envelope.event_time != request.event_time
            or envelope.initialization_time != request.initialization_time
            or envelope.schema_version != request.schema_version
            or envelope.producer_identity != request.producer_identity
            or envelope.source_commit != request.source_commit
            or envelope.config_digest != request.config_digest
        ):
            raise ValueError("P1 event authority does not match the request")
        events.append(event)
    validated = validate_event_stream(tuple(events))
    start = validated[0]
    completion = validated[-1]
    if not isinstance(start, P1RunStarted) or not isinstance(completion, P1RunCompleted):
        raise ValueError("P1 result lifecycle is invalid")
    if (
        start.runtime_family != P1_RUNTIME_FAMILY
        or start.engine_version != P1_ENGINE_VERSION
        or start.upstream_commit != P1_UPSTREAM_COMMIT
        or start.closure_digest != expected_closure_digest
        or start.config_digest != request.config_digest
        or start.catalog_digest != request.payload.instrument_catalog.sha256
        or start.data_digest != request.payload.market_data.sha256
        or completion.runtime_family != P1_RUNTIME_FAMILY
        or completion.engine_version != P1_ENGINE_VERSION
        or completion.upstream_commit != P1_UPSTREAM_COMMIT
        or completion.closure_digest != expected_closure_digest
    ):
        raise ValueError("P1 result lineage does not match the product authority")
    return P1ValidatedResult(
        batch_sha256=hashlib.sha256(raw).hexdigest(),
        semantic_sha256=completion.semantic_digest,
        product_closure_sha256=expected_closure_digest,
        event_count=len(validated),
        target_count=completion.target_count,
        order_count=completion.order_count,
        fill_count=completion.fill_count,
        final_cash=completion.final_cash,
        final_position=completion.final_position,
        fees=completion.fees,
        realized_pnl=completion.realized_pnl,
        unrealized_pnl=completion.unrealized_pnl,
        events=validated,
    )


def validate_p1_result(
    request: EngineCommandEnvelope,
    envelopes: tuple[EngineEventEnvelope, ...],
    *,
    raw: bytes,
    expected_closure_digest: str,
) -> P1ValidatedResult:
    """Contain parser recursion/type failures at the P1 trust boundary."""

    try:
        return _validate_p1_result(
            request,
            envelopes,
            raw=raw,
            expected_closure_digest=expected_closure_digest,
        )
    except (RecursionError, TypeError) as exc:
        raise ValueError("P1 result parser input is invalid") from exc


__all__ = [
    "P1_EVENT_FAMILIES",
    "P1_ENGINE_VERSION",
    "P1_RESULT_VALIDATOR_ID",
    "P1_RUNTIME_FAMILY",
    "P1_UPSTREAM_COMMIT",
    "P1ValidatedResult",
    "validate_p1_result",
]
