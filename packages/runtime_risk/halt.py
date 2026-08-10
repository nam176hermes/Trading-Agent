"""Deterministic global breaker, durable transitions, recovery, and replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
import json
from typing import Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel

from packages.domain.clock import require_utc
from packages.domain.events import EventEnvelope
from packages.domain.runtime_halt import (
    GlobalHaltReasonCode,
    GlobalHaltRecoveryAuthorization,
    GlobalHaltState,
    GlobalHaltStatus,
    GlobalHaltTransition,
    GlobalSafetyObservation,
    PreparedSubmitPermit,
    SubmitPermitConsumed,
    SubmitPermitPrepared,
)
from packages.domain.runtime_risk import RuntimeRiskObservation, RuntimeRiskPolicy
from packages.event_ledger.models import AppendOutcome, OutboxIntent
from packages.event_ledger.replay import (
    ReplayError,
    deserialize_event,
    event_digest,
    serialize_event,
)
from packages.event_ledger.repository import EventLedgerRepository
from packages.safety_evidence import CanonicalKillSwitchState

from .canonical import canonical_model_digest, canonical_model_json
from .safety import (
    GlobalHaltAuthorityError,
    GlobalSafetyAuthorityVerifier,
    global_safety_binding_digest,
    verify_global_safety_observation,
)


_TRANSITION_EVENT_SCHEMA = "global-halt-transition-event-v1"
_PREPARED_EVENT_SCHEMA = "submit-permit-prepared-event-v1"
_CONSUMED_EVENT_SCHEMA = "submit-permit-consumed-event-v1"
_EVENT_SOURCE = "runtime-risk"
_TRANSITION_OUTBOX_TOPIC = "runtime-risk.global-halt-transitions"
_TRANSITION_EVENT_LIFETIME = timedelta(minutes=5)
_CONSUMED_EVENT_LIFETIME = timedelta(minutes=5)
_CANONICAL_ERRORS = (AttributeError, OverflowError, ReplayError, TypeError, ValueError)
_REPOSITORY_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
_AUTHORITY_FAILURE = "global halt authority is invalid"
_RECORDING_FAILURE = "global halt transition recording failed"
_RECOVERY_FAILURE = "global halt recovery failed"
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class GlobalHaltRecoveryError(GlobalHaltAuthorityError):
    """Recovery cannot prove exact current operator authority."""


class GlobalHaltRecoveryAuthorityVerifier(Protocol):
    def verify(
        self,
        *,
        authorization: GlobalHaltRecoveryAuthorization,
        state: GlobalHaltState,
        observation: RuntimeRiskObservation,
        policy: RuntimeRiskPolicy,
        safety: GlobalSafetyObservation,
        verified_at: datetime,
    ) -> GlobalHaltRecoveryAuthorization: ...


@dataclass(frozen=True)
class GlobalHaltReplay:
    state: GlobalHaltState | None
    prepared: tuple[PreparedSubmitPermit, ...]
    consumed_permit_ids: tuple[UUID, ...]
    retired_permit_ids: tuple[UUID, ...]
    head_sequence: int
    head_event_id: UUID | None
    head_event_digest: str | None
    head_authority_at: datetime | None


def _canonical(value: object, expected_type: type[_ModelT], field: str) -> _ModelT:
    if type(value) is not expected_type:
        raise ValueError(f"{field} has the wrong concrete type")
    return expected_type.model_validate_json(canonical_model_json(value))


def _canonical_authority(
    value: object, expected_type: type[_ModelT], field: str
) -> _ModelT:
    try:
        return _canonical(value, expected_type, field)
    except _CANONICAL_ERRORS as exc:
        raise GlobalHaltAuthorityError(_AUTHORITY_FAILURE) from exc


def evaluate_global_breaker(
    *,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
) -> tuple[GlobalHaltReasonCode, ...]:
    """Return every exact global breaker cause in canonical order."""

    observation = _canonical_authority(observation, RuntimeRiskObservation, "observation")
    policy = _canonical_authority(policy, RuntimeRiskPolicy, "policy")
    safety = _canonical_authority(safety, GlobalSafetyObservation, "safety")
    reporting = observation.portfolio.reporting_currency
    if any(
        value.currency is not reporting
        for value in (
            observation.daily_pnl,
            observation.current_equity,
            observation.peak_equity,
            policy.max_daily_loss,
            policy.max_drawdown,
        )
    ):
        raise GlobalHaltAuthorityError("global halt accounting authority is invalid")
    drawdown = max(
        Fraction(observation.peak_equity.amount)
        - Fraction(observation.current_equity.amount),
        Fraction(0),
    )
    checks = (
        (
            GlobalHaltReasonCode.SAFETY_AUTHORITY_UNKNOWN,
            safety.kill_switch_state is CanonicalKillSwitchState.UNKNOWN,
        ),
        (
            GlobalHaltReasonCode.KILL_SWITCH_ACTIVE,
            safety.kill_switch_state is CanonicalKillSwitchState.ACTIVE,
        ),
        (
            GlobalHaltReasonCode.DAILY_LOSS_LIMIT,
            Fraction(observation.daily_pnl.amount)
            < -Fraction(policy.max_daily_loss.amount),
        ),
        (
            GlobalHaltReasonCode.DRAWDOWN_LIMIT,
            drawdown > Fraction(policy.max_drawdown.amount),
        ),
    )
    return tuple(reason for reason, failed in checks if failed)


def _canonical_envelope(event: object) -> tuple[EventEnvelope[object], str]:
    text = serialize_event(event)
    return deserialize_event(text), text


def _valid_common_event(
    event: EventEnvelope[object],
    *,
    schema_version: str,
    at: datetime,
    correlation_id: UUID,
    causation_id: UUID,
    trace_id: UUID,
    expires_at: datetime,
) -> bool:
    return (
        event.schema_version == schema_version
        and event.source == _EVENT_SOURCE
        and event.observed_at == at
        and event.ingested_at == at
        and event.produced_at == at
        and event.effective_at == at
        and event.expires_at == expires_at
        and event.correlation_id == correlation_id
        and event.causation_id == causation_id
        and event.trace_id == trace_id
    )


def _require_event_bindings(event: EventEnvelope[object]) -> None:
    payload = event.payload
    if type(payload) is GlobalHaltTransition:
        valid = _valid_common_event(
            event,
            schema_version=_TRANSITION_EVENT_SCHEMA,
            at=payload.decided_at,
            correlation_id=payload.transition_id,
            causation_id=payload.transition_id,
            trace_id=payload.transition_id,
            expires_at=payload.decided_at + _TRANSITION_EVENT_LIFETIME,
        )
    elif type(payload) is SubmitPermitPrepared:
        valid = _valid_common_event(
            event,
            schema_version=_PREPARED_EVENT_SCHEMA,
            at=payload.prepared_at,
            correlation_id=payload.permit_id,
            causation_id=payload.approval_event_id,
            trace_id=payload.permit_id,
            expires_at=payload.expires_at,
        )
    elif type(payload) is SubmitPermitConsumed:
        valid = _valid_common_event(
            event,
            schema_version=_CONSUMED_EVENT_SCHEMA,
            at=payload.consumed_at,
            correlation_id=payload.permit_id,
            causation_id=payload.permit_id,
            trace_id=payload.permit_id,
            expires_at=payload.consumed_at + _CONSUMED_EVENT_LIFETIME,
        )
    else:
        raise ValueError("foreign payload in global halt authority stream")
    if not valid:
        raise ValueError("global halt event authority bindings are invalid")


def _state_from_transition(
    *,
    stream_id: UUID,
    event: EventEnvelope[object],
    canonical_text: str,
    prior: GlobalHaltState | None,
) -> GlobalHaltState:
    payload = event.payload
    if type(payload) is not GlobalHaltTransition:
        raise ValueError("transition payload has the wrong concrete type")
    if prior is None:
        if payload.prior_generation != 0 or payload.prior_transition_digest is not None:
            raise ValueError("initial transition has invalid lineage")
    else:
        if (
            payload.prior_generation != prior.generation
            or payload.prior_transition_digest != prior.transition_digest
            or payload.next_generation != prior.generation + 1
            or payload.decided_at < prior.transitioned_at
            or (
                prior.status is GlobalHaltStatus.ACTIVE
                and payload.next_status is not GlobalHaltStatus.HALTED
            )
            or (
                prior.status is GlobalHaltStatus.HALTED
                and payload.next_status is not GlobalHaltStatus.ACTIVE
            )
        ):
            raise ValueError("global halt transition is impossible")
    return GlobalHaltState(
        stream_id=stream_id,
        generation=payload.next_generation,
        status=payload.next_status,
        transition_event_id=event.event_id,
        transition_digest=event_digest(canonical_text),
        prior_transition_event_id=None if prior is None else prior.transition_event_id,
        prior_transition_digest=None if prior is None else prior.transition_digest,
        runtime_policy_digest=payload.runtime_policy_digest,
        runtime_observation_digest=payload.runtime_observation_digest,
        portfolio_digest=payload.portfolio_digest,
        safety_observation_digest=payload.safety_observation_digest,
        reason_codes=payload.reason_codes,
        transitioned_at=payload.decided_at,
        schema_version="global-halt-state-v1",
    )


def replay_global_halt_authority(
    *, events: tuple[EventEnvelope[object], ...], stream_id: UUID
) -> GlobalHaltReplay:
    """Replay one dedicated authority stream, rejecting every contradiction."""

    try:
        if type(events) is not tuple or type(stream_id) is not UUID:
            raise ValueError("replay inputs have invalid concrete types")
        selected: list[tuple[EventEnvelope[object], str]] = []
        for supplied in events:
            event, text = _canonical_envelope(supplied)
            if event.stream_id == stream_id:
                selected.append((event, text))
        state: GlobalHaltState | None = None
        prepared: dict[UUID, PreparedSubmitPermit] = {}
        consumed: list[UUID] = []
        retired: list[UUID] = []
        event_digests: dict[UUID, str] = {}
        head_event_id: UUID | None = None
        head_digest: str | None = None
        head_authority_at: datetime | None = None
        for expected_sequence, (event, text) in enumerate(selected, start=1):
            if event.sequence != expected_sequence:
                raise ValueError("global halt authority sequence is not contiguous")
            digest = event_digest(text)
            if event.event_id in event_digests:
                raise ValueError("global halt authority event_id is not unique")
            event_digests[event.event_id] = digest
            _require_event_bindings(event)
            payload = event.payload
            if type(payload) is GlobalHaltTransition:
                authority_at = payload.decided_at
            elif type(payload) is SubmitPermitPrepared:
                authority_at = payload.prepared_at
            elif type(payload) is SubmitPermitConsumed:
                authority_at = payload.consumed_at
            else:
                raise ValueError("foreign payload in global halt authority stream")
            if head_authority_at is not None and authority_at < head_authority_at:
                raise ValueError("global halt authority time is not nondecreasing")
            if type(payload) is GlobalHaltTransition:
                if state is not None:
                    retired.extend(prepared)
                    prepared.clear()
                state = _state_from_transition(
                    stream_id=stream_id,
                    event=event,
                    canonical_text=text,
                    prior=state,
                )
            elif type(payload) is SubmitPermitPrepared:
                if (
                    state is None
                    or state.status is not GlobalHaltStatus.ACTIVE
                    or payload.halt_stream_id != stream_id
                    or payload.halt_generation != state.generation
                    or payload.halt_transition_event_id != state.transition_event_id
                    or payload.halt_transition_digest != state.transition_digest
                    or payload.prepared_at < state.transitioned_at
                    or payload.permit_id in prepared
                    or payload.permit_id in consumed
                    or payload.permit_id in retired
                ):
                    raise ValueError("prepared permit contradicts halt authority")
                prepared[payload.permit_id] = PreparedSubmitPermit(
                    **{
                        **payload.model_dump(mode="python"),
                        "prepared_event_id": event.event_id,
                        "prepared_event_digest": event_digest(text),
                        "schema_version": "prepared-submit-permit-v1",
                    }
                )
            elif type(payload) is SubmitPermitConsumed:
                authority = prepared.get(payload.permit_id)
                if (
                    authority is None
                    or state is None
                    or state.status is not GlobalHaltStatus.ACTIVE
                    or payload.prepared_event_digest != authority.prepared_event_digest
                    or payload.halt_stream_id != authority.halt_stream_id
                    or payload.halt_generation != authority.halt_generation
                    or payload.halt_transition_digest != authority.halt_transition_digest
                    or state.stream_id != authority.halt_stream_id
                    or state.generation != authority.halt_generation
                    or state.transition_event_id != authority.halt_transition_event_id
                    or state.transition_digest != authority.halt_transition_digest
                    or state.transitioned_at > payload.consumed_at
                    or not (
                        authority.prepared_at
                        <= payload.consumed_at
                        <= authority.expires_at
                    )
                ):
                    raise ValueError("consumed permit contradicts halt authority")
                del prepared[payload.permit_id]
                consumed.append(payload.permit_id)
            else:
                raise ValueError("foreign payload in global halt authority stream")
            head_event_id = event.event_id
            head_digest = digest
            head_authority_at = authority_at
        return GlobalHaltReplay(
            state=state,
            prepared=tuple(prepared.values()),
            consumed_permit_ids=tuple(consumed),
            retired_permit_ids=tuple(retired),
            head_sequence=len(selected),
            head_event_id=head_event_id,
            head_event_digest=head_digest,
            head_authority_at=head_authority_at,
        )
    except GlobalHaltAuthorityError:
        raise
    except _CANONICAL_ERRORS as exc:
        raise GlobalHaltAuthorityError(_AUTHORITY_FAILURE) from exc


def _load_events(
    repository: EventLedgerRepository,
    error_type: type[GlobalHaltAuthorityError],
) -> tuple[EventEnvelope[object], ...]:
    try:
        events = repository.load_events()
        if type(events) is not tuple:
            raise ValueError("ledger read-back must be a tuple")
        return events
    except _REPOSITORY_ERRORS as exc:
        message = (
            _RECORDING_FAILURE
            if error_type is GlobalHaltAuthorityError
            else _RECOVERY_FAILURE
        )
        raise error_type(message) from exc


def _append_and_read_back(
    *,
    repository: EventLedgerRepository,
    event: EventEnvelope[object],
    outbox: OutboxIntent,
    error_type: type[GlobalHaltAuthorityError],
) -> tuple[EventEnvelope[object], ...]:
    message = _RECORDING_FAILURE if error_type is GlobalHaltAuthorityError else _RECOVERY_FAILURE
    try:
        outcome = repository.append(event, outbox)
        canonical_outcome = AppendOutcome.model_validate(outcome.model_dump(mode="python"))
        if canonical_outcome.event_id != event.event_id:
            raise ValueError("append receipt does not match event")
    except _REPOSITORY_ERRORS as exc:
        raise error_type(message) from exc
    loaded = _load_events(repository, error_type)
    try:
        matches = tuple(
            item
            for item in loaded
            if getattr(item, "event_id", None) == event.event_id
        )
        if len(matches) != 1:
            raise ValueError("ledger must contain exactly one matching event")
    except _REPOSITORY_ERRORS as exc:
        raise error_type(message) from exc
    try:
        expected_text = serialize_event(event)
        actual_event, actual_text = _canonical_envelope(matches[0])
        if (
            actual_text.encode("utf-8") != expected_text.encode("utf-8")
            or event_digest(actual_text) != event_digest(expected_text)
            or actual_event != event
        ):
            raise ValueError("ledger read-back is not byte-identical")
    except _CANONICAL_ERRORS as exc:
        raise error_type(message) from exc
    return loaded


def _require_transition_arguments(
    *,
    stream_id: object,
    transition_id: object,
    event_id: object,
    decided_at: object,
) -> None:
    if (
        type(stream_id) is not UUID
        or type(transition_id) is not UUID
        or type(event_id) is not UUID
        or type(decided_at) is not datetime
    ):
        raise ValueError("global halt transition arguments have invalid types")
    require_utc(decided_at)


def _transition_event(
    *,
    stream_id: UUID,
    sequence: int,
    event_id: UUID,
    payload: GlobalHaltTransition,
) -> EventEnvelope[GlobalHaltTransition]:
    return EventEnvelope[GlobalHaltTransition](
        event_id=event_id,
        event_type="GlobalHaltTransition",
        schema_version=_TRANSITION_EVENT_SCHEMA,
        source=_EVENT_SOURCE,
        stream_id=stream_id,
        sequence=sequence,
        observed_at=payload.decided_at,
        ingested_at=payload.decided_at,
        produced_at=payload.decided_at,
        effective_at=payload.decided_at,
        expires_at=payload.decided_at + _TRANSITION_EVENT_LIFETIME,
        correlation_id=payload.transition_id,
        causation_id=payload.transition_id,
        trace_id=payload.transition_id,
        payload=payload,
    )


def _transition_outbox(event: EventEnvelope[object]) -> OutboxIntent:
    payload = event.payload
    if type(payload) is not GlobalHaltTransition:
        raise ValueError("transition outbox requires a transition payload")
    return OutboxIntent(
        event_id=event.event_id,
        topic=_TRANSITION_OUTBOX_TOPIC,
        payload_json=json.dumps(
            {"transition_id": str(payload.transition_id)},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _new_transition_payload(
    *,
    replayed: GlobalHaltReplay,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
    transition_id: UUID,
    decided_at: datetime,
    status: GlobalHaltStatus,
    reasons: tuple[GlobalHaltReasonCode, ...],
    recovery_authorization_digest: str | None = None,
) -> GlobalHaltTransition:
    state = replayed.state
    return GlobalHaltTransition(
        transition_id=transition_id,
        prior_generation=0 if state is None else state.generation,
        prior_transition_digest=None if state is None else state.transition_digest,
        next_generation=1 if state is None else state.generation + 1,
        next_status=status,
        reason_codes=reasons,
        runtime_policy_digest=canonical_model_digest(policy),
        runtime_observation_digest=canonical_model_digest(observation),
        portfolio_digest=canonical_model_digest(observation.portfolio),
        safety_observation_digest=canonical_model_digest(safety),
        recovery_authorization_digest=recovery_authorization_digest,
        decided_at=decided_at,
        schema_version="global-halt-transition-v1",
    )


def _global_authority_is_current(
    *,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
    decided_at: datetime,
) -> bool:
    portfolio = observation.portfolio
    timestamps = [
        observation.observed_at,
        portfolio.observed_at,
        *(balance.observed_at for balance in portfolio.balances),
        *(position.observed_at for position in portfolio.positions),
        *(
            position.mark.marked_at
            for position in portfolio.positions
            if position.mark is not None
        ),
        safety.observed_at,
    ]
    maximum_age = timedelta(seconds=policy.portfolio_max_age_seconds)
    return all(
        timestamp <= decided_at and decided_at - timestamp <= maximum_age
        for timestamp in timestamps
    )


def _recovery_bindings_match(
    *,
    authorization: GlobalHaltRecoveryAuthorization,
    state: GlobalHaltState,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
    decided_at: datetime,
) -> bool:
    return all(
        (
            authorization.halted_generation == state.generation,
            authorization.halted_transition_digest == state.transition_digest,
            authorization.runtime_policy_digest == canonical_model_digest(policy),
            authorization.runtime_observation_digest
            == canonical_model_digest(observation),
            authorization.portfolio_digest
            == canonical_model_digest(observation.portfolio),
            authorization.safety_binding_digest
            == global_safety_binding_digest(safety),
            state.transitioned_at
            <= observation.observed_at
            <= authorization.issued_at
            <= safety.observed_at
            <= decided_at,
            authorization.issued_at <= decided_at < authorization.expires_at,
            _global_authority_is_current(
                observation=observation,
                policy=policy,
                safety=safety,
                decided_at=decided_at,
            ),
            not evaluate_global_breaker(
                observation=observation,
                policy=policy,
                safety=safety,
            ),
        )
    )


def _verify_recovery_authorization(
    *,
    verifier: GlobalHaltRecoveryAuthorityVerifier,
    authorization: GlobalHaltRecoveryAuthorization,
    state: GlobalHaltState,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
    decided_at: datetime,
) -> bool:
    try:
        verified = verifier.verify(
            authorization=authorization,
            state=state,
            observation=observation,
            policy=policy,
            safety=safety,
            verified_at=decided_at,
        )
    except _REPOSITORY_ERRORS as exc:
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE) from exc
    try:
        verified = _canonical(
            verified, GlobalHaltRecoveryAuthorization, "verified authorization"
        )
        exact_verifier_result = canonical_model_json(verified) == canonical_model_json(
            authorization
        )
    except _CANONICAL_ERRORS as exc:
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE) from exc
    return exact_verifier_result and _recovery_bindings_match(
        authorization=authorization,
        state=state,
        observation=observation,
        policy=policy,
        safety=safety,
        decided_at=decided_at,
    )


def _already_durable_recovery(
    *,
    events: tuple[EventEnvelope[object], ...],
    replayed: GlobalHaltReplay,
    stream_id: UUID,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
    authorization: GlobalHaltRecoveryAuthorization,
    verifier: GlobalHaltRecoveryAuthorityVerifier,
    transition_id: UUID,
    event_id: UUID,
    decided_at: datetime,
) -> GlobalHaltState | None:
    current = replayed.state
    if (
        current is None
        or current.status is not GlobalHaltStatus.ACTIVE
        or current.transition_event_id != event_id
    ):
        return None
    selected: list[EventEnvelope[object]] = []
    match: EventEnvelope[object] | None = None
    match_text: str | None = None
    for supplied in events:
        event, text = _canonical_envelope(supplied)
        if event.stream_id != stream_id:
            continue
        selected.append(event)
        if event.event_id == event_id:
            match = event
            match_text = text
    if (
        match is None
        or match_text is None
        or type(match.payload) is not GlobalHaltTransition
    ):
        return None
    prefix = tuple(event for event in selected if event.sequence < match.sequence)
    prior_replay = replay_global_halt_authority(events=prefix, stream_id=stream_id)
    prior = prior_replay.state
    if prior is None or prior.status is not GlobalHaltStatus.HALTED:
        return None
    if not _verify_recovery_authorization(
        verifier=verifier,
        authorization=authorization,
        state=prior,
        observation=observation,
        policy=policy,
        safety=safety,
        decided_at=decided_at,
    ):
        return None
    expected_payload = _new_transition_payload(
        replayed=prior_replay,
        observation=observation,
        policy=policy,
        safety=safety,
        transition_id=transition_id,
        decided_at=decided_at,
        status=GlobalHaltStatus.ACTIVE,
        reasons=(GlobalHaltReasonCode.RECOVERY_AUTHORIZED,),
        recovery_authorization_digest=authorization.authorization_digest,
    )
    expected_event = _transition_event(
        stream_id=stream_id,
        sequence=match.sequence,
        event_id=event_id,
        payload=expected_payload,
    )
    if serialize_event(expected_event) != match_text:
        return None
    return current


def record_global_halt_observation(
    *,
    repository: EventLedgerRepository,
    stream_id: UUID,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
    safety_verifier: GlobalSafetyAuthorityVerifier,
    transition_id: UUID,
    event_id: UUID,
    decided_at: datetime,
) -> GlobalHaltState:
    """Initialize or rotate the durable global halt state when required."""

    try:
        _require_transition_arguments(
            stream_id=stream_id,
            transition_id=transition_id,
            event_id=event_id,
            decided_at=decided_at,
        )
    except _CANONICAL_ERRORS as exc:
        raise GlobalHaltAuthorityError(_AUTHORITY_FAILURE) from exc
    observation = _canonical_authority(observation, RuntimeRiskObservation, "observation")
    policy = _canonical_authority(policy, RuntimeRiskPolicy, "policy")
    safety = _canonical_authority(safety, GlobalSafetyObservation, "safety")
    try:
        safety = verify_global_safety_observation(
            verifier=safety_verifier,
            observation=safety,
        )
    except GlobalHaltAuthorityError as exc:
        raise GlobalHaltAuthorityError(_AUTHORITY_FAILURE) from exc
    events = _load_events(repository, GlobalHaltAuthorityError)
    replayed = replay_global_halt_authority(events=events, stream_id=stream_id)
    reasons = evaluate_global_breaker(observation=observation, policy=policy, safety=safety)
    if not reasons and not _global_authority_is_current(
        observation=observation,
        policy=policy,
        safety=safety,
        decided_at=decided_at,
    ):
        raise GlobalHaltAuthorityError(_AUTHORITY_FAILURE)
    if replayed.state is not None:
        if replayed.state.status is GlobalHaltStatus.HALTED or not reasons:
            return replayed.state
        status = GlobalHaltStatus.HALTED
        transition_reasons = reasons
    elif reasons:
        status = GlobalHaltStatus.HALTED
        transition_reasons = reasons
    else:
        status = GlobalHaltStatus.ACTIVE
        transition_reasons = (GlobalHaltReasonCode.INITIALIZED_SAFE,)
    if (
        replayed.head_authority_at is not None
        and decided_at < replayed.head_authority_at
    ):
        raise GlobalHaltAuthorityError(_AUTHORITY_FAILURE)
    try:
        payload = _new_transition_payload(
            replayed=replayed,
            observation=observation,
            policy=policy,
            safety=safety,
            transition_id=transition_id,
            decided_at=decided_at,
            status=status,
            reasons=transition_reasons,
        )
        event = _transition_event(
            stream_id=stream_id,
            sequence=replayed.head_sequence + 1,
            event_id=event_id,
            payload=payload,
        )
        outbox = _transition_outbox(event)
    except _CANONICAL_ERRORS as exc:
        raise GlobalHaltAuthorityError(_RECORDING_FAILURE) from exc
    loaded = _append_and_read_back(
        repository=repository,
        event=event,
        outbox=outbox,
        error_type=GlobalHaltAuthorityError,
    )
    state = replay_global_halt_authority(events=loaded, stream_id=stream_id).state
    if state is None:
        raise GlobalHaltAuthorityError(_RECORDING_FAILURE)
    return state


def recover_global_halt(
    *,
    repository: EventLedgerRepository,
    stream_id: UUID,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
    safety_verifier: GlobalSafetyAuthorityVerifier,
    authorization: GlobalHaltRecoveryAuthorization,
    verifier: GlobalHaltRecoveryAuthorityVerifier,
    transition_id: UUID,
    event_id: UUID,
    decided_at: datetime,
) -> GlobalHaltState:
    """Rotate a halted generation only with exact, current verified authority."""

    try:
        _require_transition_arguments(
            stream_id=stream_id,
            transition_id=transition_id,
            event_id=event_id,
            decided_at=decided_at,
        )
        observation = _canonical(observation, RuntimeRiskObservation, "observation")
        policy = _canonical(policy, RuntimeRiskPolicy, "policy")
        safety = _canonical(safety, GlobalSafetyObservation, "safety")
        authorization = _canonical(
            authorization, GlobalHaltRecoveryAuthorization, "authorization"
        )
    except _CANONICAL_ERRORS as exc:
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE) from exc
    try:
        safety = verify_global_safety_observation(
            verifier=safety_verifier,
            observation=safety,
        )
    except GlobalHaltAuthorityError as exc:
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE) from exc
    events = _load_events(repository, GlobalHaltRecoveryError)
    try:
        replayed = replay_global_halt_authority(events=events, stream_id=stream_id)
    except GlobalHaltAuthorityError as exc:
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE) from exc
    state = replayed.state
    if state is not None and state.status is GlobalHaltStatus.ACTIVE:
        try:
            durable = _already_durable_recovery(
                events=events,
                replayed=replayed,
                stream_id=stream_id,
                observation=observation,
                policy=policy,
                safety=safety,
                authorization=authorization,
                verifier=verifier,
                transition_id=transition_id,
                event_id=event_id,
                decided_at=decided_at,
            )
        except GlobalHaltRecoveryError:
            raise
        except _CANONICAL_ERRORS as exc:
            raise GlobalHaltRecoveryError(_RECOVERY_FAILURE) from exc
        if durable is not None:
            return durable
    if state is None or state.status is not GlobalHaltStatus.HALTED:
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE)
    if not _verify_recovery_authorization(
        verifier=verifier,
        authorization=authorization,
        state=state,
        observation=observation,
        policy=policy,
        safety=safety,
        decided_at=decided_at,
    ):
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE)
    if (
        replayed.head_authority_at is not None
        and decided_at < replayed.head_authority_at
    ):
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE)
    try:
        payload = _new_transition_payload(
            replayed=replayed,
            observation=observation,
            policy=policy,
            safety=safety,
            transition_id=transition_id,
            decided_at=decided_at,
            status=GlobalHaltStatus.ACTIVE,
            reasons=(GlobalHaltReasonCode.RECOVERY_AUTHORIZED,),
            recovery_authorization_digest=authorization.authorization_digest,
        )
        event = _transition_event(
            stream_id=stream_id,
            sequence=replayed.head_sequence + 1,
            event_id=event_id,
            payload=payload,
        )
        outbox = _transition_outbox(event)
    except _CANONICAL_ERRORS as exc:
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE) from exc
    loaded = _append_and_read_back(
        repository=repository,
        event=event,
        outbox=outbox,
        error_type=GlobalHaltRecoveryError,
    )
    try:
        recovered = replay_global_halt_authority(events=loaded, stream_id=stream_id).state
    except GlobalHaltAuthorityError as exc:
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE) from exc
    if recovered is None:
        raise GlobalHaltRecoveryError(_RECOVERY_FAILURE)
    return recovered


__all__ = [
    "GlobalHaltAuthorityError",
    "GlobalHaltRecoveryAuthorityVerifier",
    "GlobalHaltRecoveryError",
    "GlobalHaltReplay",
    "evaluate_global_breaker",
    "recover_global_halt",
    "record_global_halt_observation",
    "replay_global_halt_authority",
]
