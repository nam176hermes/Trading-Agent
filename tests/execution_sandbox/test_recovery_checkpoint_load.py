from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest

from packages.domain import EventEnvelope
from packages.domain.recovery import SandboxRecoveryCheckpointRecorded
from packages.event_ledger import InMemoryEventLedger, OutboxIntent
from packages.execution_sandbox import (
    SandboxRecoveryCheckpoint,
    SandboxRecoveryPersistenceError,
    encode_recovery_checkpoint,
)
from packages.execution_sandbox import recovery_persistence
from packages.runtime_risk import canonical_model_json

from tests.event_ledger.test_reducer import envelope, signal
from tests.execution_sandbox.test_recovery_contracts import checkpoint_values, uid
from tests.execution_sandbox.test_recovery_persistence_contracts import (
    checkpoint_event,
    record_with_json,
)


SESSION_ID = uid(600)


class HostileUUID(UUID):
    operations: list[str] = []

    def __eq__(self, other: object) -> bool:
        type(self).operations.append("equality")
        raise AssertionError("UUID equality must not run")

    def __hash__(self) -> int:
        type(self).operations.append("hashing")
        raise AssertionError("UUID hashing must not run")


class HostileText(str):
    operations: list[str] = []

    def __eq__(self, other: object) -> bool:
        type(self).operations.append("equality")
        raise AssertionError("text equality must not run")

    def __hash__(self) -> int:
        type(self).operations.append("hashing")
        raise AssertionError("text hashing must not run")


class HostileTuple(tuple):
    iterated = False

    def __iter__(self):  # type: ignore[override]
        type(self).iterated = True
        raise AssertionError("tuple-subclass iteration must not run")


class StaticStreamRepository:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[UUID] = []

    def load_stream_events(self, stream_id: UUID) -> object:
        self.calls.append(stream_id)
        return self.result


class RepositoryUnavailable(RuntimeError):
    pass


class FailingStreamRepository:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls: list[UUID] = []

    def load_stream_events(self, stream_id: UUID) -> object:
        self.calls.append(stream_id)
        raise self.error


def _checkpoint(
    prepared_case: Any,
    checkpoint_id: UUID = uid(200),
) -> SandboxRecoveryCheckpoint:
    return SandboxRecoveryCheckpoint(
        **checkpoint_values(prepared_case, checkpoint_id=checkpoint_id)
    )


def _checkpoint_event(
    prepared_case: Any,
    *,
    checkpoint_id: UUID = uid(200),
    recovery_session_id: UUID = SESSION_ID,
    sequence: int = 1,
) -> tuple[
    SandboxRecoveryCheckpoint,
    EventEnvelope[SandboxRecoveryCheckpointRecorded],
]:
    checkpoint = _checkpoint(prepared_case, checkpoint_id)
    record = encode_recovery_checkpoint(
        recovery_session_id=recovery_session_id,
        checkpoint=checkpoint,
    )
    return checkpoint, checkpoint_event(record, sequence=sequence)


def _append(
    repository: InMemoryEventLedger,
    event: EventEnvelope[object],
) -> None:
    repository.append(
        event,
        OutboxIntent(event_id=event.event_id, topic="recovery.checkpoint"),
    )


def _load(
    *,
    repository: object,
    recovery_session_id: object = SESSION_ID,
    checkpoint_id: object = uid(200),
) -> SandboxRecoveryCheckpoint | None:
    return recovery_persistence.load_recovery_checkpoint(
        repository=repository,
        recovery_session_id=recovery_session_id,
        checkpoint_id=checkpoint_id,
    )


def test_empty_recovery_stream_returns_none() -> None:
    repository = InMemoryEventLedger()

    assert _load(repository=repository) is None


def test_one_checkpoint_loads_exact_id_as_fresh_evidence(prepared_case: Any) -> None:
    repository = InMemoryEventLedger()
    source, event = _checkpoint_event(prepared_case)
    _append(repository, event)

    loaded = _load(repository=repository)

    assert type(loaded) is SandboxRecoveryCheckpoint
    assert loaded == source
    assert loaded is not source


@pytest.mark.parametrize("requested_id", (uid(200), uid(201)))
def test_two_checkpoints_load_first_or_second_by_exact_id(
    requested_id: UUID,
    prepared_case: Any,
) -> None:
    repository = InMemoryEventLedger()
    first, first_event = _checkpoint_event(
        prepared_case,
        checkpoint_id=uid(200),
        sequence=1,
    )
    second, second_event = _checkpoint_event(
        prepared_case,
        checkpoint_id=uid(201),
        sequence=2,
    )
    _append(repository, first_event)
    _append(repository, second_event)

    loaded = _load(repository=repository, checkpoint_id=requested_id)

    assert loaded == (first if requested_id == uid(200) else second)
    assert loaded is not first
    assert loaded is not second


def test_valid_nonempty_stream_missing_requested_id_returns_none(
    prepared_case: Any,
) -> None:
    repository = InMemoryEventLedger()
    _, first = _checkpoint_event(prepared_case, checkpoint_id=uid(200), sequence=1)
    _, second = _checkpoint_event(prepared_case, checkpoint_id=uid(201), sequence=2)
    _append(repository, first)
    _append(repository, second)

    assert _load(repository=repository, checkpoint_id=uid(999)) is None


@pytest.mark.parametrize("field_name", ("recovery_session_id", "checkpoint_id"))
@pytest.mark.parametrize("variant", ("string", "uuid-subclass"))
def test_lookup_identities_are_exact_uuids_before_repository_access(
    field_name: str,
    variant: str,
) -> None:
    repository = StaticStreamRepository(())
    valid = SESSION_ID if field_name == "recovery_session_id" else uid(200)
    supplied: object = (
        str(valid)
        if variant == "string"
        else HostileUUID(str(valid))
    )
    HostileUUID.operations.clear()

    with pytest.raises(SandboxRecoveryPersistenceError, match=field_name):
        _load(repository=repository, **{field_name: supplied})

    assert repository.calls == []
    assert HostileUUID.operations == []


def test_well_formed_non_recovery_event_contaminates_dedicated_stream(
    prepared_case: Any,
) -> None:
    repository = InMemoryEventLedger()
    _, requested = _checkpoint_event(prepared_case, sequence=1)
    contaminant = envelope(
        signal(),
        event_number=900,
        stream_number=SESSION_ID.int,
        sequence=2,
    )
    _append(repository, requested)
    _append(repository, contaminant)

    with pytest.raises(SandboxRecoveryPersistenceError):
        _load(repository=repository)


@pytest.mark.parametrize(
    "record_change",
    (
        {"schema_version": "malformed-record-version"},
        {"checkpoint_digest": "0" * 64},
    ),
    ids=("malformed-record", "digest-mismatch"),
)
def test_malformed_or_digest_mismatched_record_fails_closed(
    record_change: dict[str, object],
    prepared_case: Any,
) -> None:
    _, event = _checkpoint_event(prepared_case)
    forged_record = event.payload.model_copy(update=record_change)
    forged_event = event.model_copy(update={"payload": forged_record})

    with pytest.raises(SandboxRecoveryPersistenceError):
        _load(repository=StaticStreamRepository((forged_event,)))


def test_valid_foreign_record_session_cannot_satisfy_requested_stream(
    prepared_case: Any,
) -> None:
    foreign_session = uid(601)
    _, foreign = _checkpoint_event(
        prepared_case,
        recovery_session_id=foreign_session,
    )
    repository = StaticStreamRepository((foreign,))

    with pytest.raises(SandboxRecoveryPersistenceError):
        _load(repository=repository, recovery_session_id=SESSION_ID)

    assert repository.calls == [SESSION_ID]


def test_event_and_record_session_identity_mismatch_fails_closed(
    prepared_case: Any,
) -> None:
    _, event = _checkpoint_event(prepared_case)
    conflicting_record = event.payload.model_copy(
        update={"recovery_session_id": uid(601)}
    )
    conflicting_event = event.model_copy(update={"payload": conflicting_record})

    with pytest.raises(SandboxRecoveryPersistenceError):
        _load(repository=StaticStreamRepository((conflicting_event,)))


def test_event_to_record_to_checkpoint_identity_conflict_fails_closed(
    prepared_case: Any,
) -> None:
    _, event = _checkpoint_event(prepared_case)
    conflicting = event.model_copy(update={"event_id": uid(999)})

    with pytest.raises(SandboxRecoveryPersistenceError):
        _load(repository=StaticStreamRepository((conflicting,)))


def test_noncanonical_checkpoint_json_fails_closed(prepared_case: Any) -> None:
    _, event = _checkpoint_event(prepared_case)
    noncanonical = event.payload.checkpoint_json + " "
    changed_record = record_with_json(event.payload, noncanonical)
    changed_event = event.model_copy(update={"payload": changed_record})

    with pytest.raises(SandboxRecoveryPersistenceError):
        _load(repository=StaticStreamRepository((changed_event,)))


@pytest.mark.parametrize("duplicate_kind", ("requested", "unrelated"))
def test_duplicate_event_id_anywhere_in_session_fails_closed(
    duplicate_kind: str,
    prepared_case: Any,
) -> None:
    _, requested = _checkpoint_event(
        prepared_case,
        checkpoint_id=uid(200),
        sequence=1,
    )
    _, unrelated = _checkpoint_event(
        prepared_case,
        checkpoint_id=uid(201),
        sequence=2,
    )
    duplicated = requested if duplicate_kind == "requested" else unrelated
    events = (
        (requested, requested)
        if duplicate_kind == "requested"
        else (requested, unrelated, duplicated)
    )

    with pytest.raises(SandboxRecoveryPersistenceError, match="duplicate"):
        _load(repository=StaticStreamRepository(events))


@pytest.mark.parametrize("corrupt_position", ("prefix", "suffix"))
def test_requested_checkpoint_never_bypasses_corruption_elsewhere_in_stream(
    corrupt_position: str,
    prepared_case: Any,
) -> None:
    _, requested = _checkpoint_event(
        prepared_case,
        sequence=1 if corrupt_position == "suffix" else 2,
    )
    contaminant = envelope(
        signal(),
        event_number=901,
        stream_number=SESSION_ID.int,
        sequence=2 if corrupt_position == "suffix" else 1,
    )
    events = (
        (requested, contaminant)
        if corrupt_position == "suffix"
        else (contaminant, requested)
    )

    with pytest.raises(SandboxRecoveryPersistenceError):
        _load(repository=StaticStreamRepository(events))


def test_exact_id_lookup_starts_only_after_entire_stream_validates(
    prepared_case: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryEventLedger()
    first, first_event = _checkpoint_event(
        prepared_case,
        checkpoint_id=uid(200),
        sequence=1,
    )
    _, second_event = _checkpoint_event(
        prepared_case,
        checkpoint_id=uid(201),
        sequence=2,
    )
    _append(repository, first_event)
    _append(repository, second_event)
    validated_ids: list[UUID] = []
    lookup_observations: list[tuple[UUID, ...]] = []
    expected_validated_ids = (uid(200), uid(201))

    class LookupCanary:
        def __eq__(self, other: object) -> bool:
            observed = tuple(validated_ids)
            lookup_observations.append(observed)
            assert observed == expected_validated_ids, (
                "exact-ID lookup started before complete stream validation"
            )
            return other == uid(200)

    real_concrete_uuid = recovery_persistence._concrete_uuid
    lookup_canary = LookupCanary()

    def concrete_uuid_with_lookup_canary(
        value: object,
        field_name: str,
    ) -> object:
        validated = real_concrete_uuid(value, field_name)
        return lookup_canary if field_name == "checkpoint_id" else validated

    real_validator = recovery_persistence.validate_recovery_checkpoint_event

    def tracking_validator(event: object) -> SandboxRecoveryCheckpoint:
        checkpoint = real_validator(event)
        validated_ids.append(checkpoint.checkpoint_id)
        return checkpoint

    monkeypatch.setattr(
        recovery_persistence,
        "_concrete_uuid",
        concrete_uuid_with_lookup_canary,
    )
    monkeypatch.setattr(
        recovery_persistence,
        "validate_recovery_checkpoint_event",
        tracking_validator,
    )

    loaded = _load(repository=repository, checkpoint_id=uid(200))

    assert loaded == first
    assert lookup_observations == [
        expected_validated_ids,
        expected_validated_ids,
    ]


def test_repository_operational_error_propagates_unchanged() -> None:
    error = RepositoryUnavailable("stream read unavailable")
    repository = FailingStreamRepository(error)

    with pytest.raises(RepositoryUnavailable) as raised:
        _load(repository=repository)

    assert raised.value is error
    assert repository.calls == [SESSION_ID]


def test_repeated_loads_reconstruct_fresh_checkpoints_without_mutation(
    prepared_case: Any,
) -> None:
    repository = InMemoryEventLedger()
    source, event = _checkpoint_event(prepared_case)
    source_before = canonical_model_json(source)
    event_before = canonical_model_json(event)
    _append(repository, event)

    first = _load(repository=repository)
    second = _load(repository=repository)

    assert first == second == source
    assert first is not second
    assert first is not source
    assert second is not source
    assert first is not None and second is not None
    assert first.snapshot is not second.snapshot
    assert canonical_model_json(source) == source_before
    assert canonical_model_json(event) == event_before


@pytest.mark.parametrize("forgery", ("envelope", "record"))
def test_copy_forged_envelope_or_record_extra_fails_closed(
    forgery: str,
    prepared_case: Any,
) -> None:
    _, event = _checkpoint_event(prepared_case)
    if forgery == "envelope":
        forged = event.model_copy(update={"restore": True})
    else:
        forged_record = event.payload.model_copy(update={"restore": True})
        forged = event.model_copy(update={"payload": forged_record})

    with pytest.raises(SandboxRecoveryPersistenceError):
        _load(repository=StaticStreamRepository((forged,)))


def test_hostile_event_type_is_rejected_before_dispatch_or_identity_operations(
    prepared_case: Any,
) -> None:
    _, event = _checkpoint_event(prepared_case)
    forged = event.model_copy()
    object.__setattr__(forged, "event_type", HostileText(event.event_type))
    HostileText.operations.clear()

    with pytest.raises(SandboxRecoveryPersistenceError):
        _load(repository=StaticStreamRepository((forged,)))

    assert HostileText.operations == []


def _empty_generator() -> Iterator[object]:
    if False:
        yield object()


@pytest.mark.parametrize(
    "container",
    ([], set(), _empty_generator(), HostileTuple(())),
    ids=("list", "set", "generator", "tuple-subclass"),
)
def test_repository_result_must_be_an_exact_tuple_without_hostile_iteration(
    container: object,
) -> None:
    HostileTuple.iterated = False

    with pytest.raises(SandboxRecoveryPersistenceError, match="tuple"):
        _load(repository=StaticStreamRepository(container))

    assert HostileTuple.iterated is False


@pytest.mark.parametrize(
    "repository",
    (object(), type("NonCallableRepository", (), {"load_stream_events": ()})()),
    ids=("missing-method", "non-callable-method"),
)
def test_clearly_invalid_repository_fails_through_narrow_persistence_error(
    repository: object,
) -> None:
    with pytest.raises(SandboxRecoveryPersistenceError, match="repository"):
        _load(repository=repository)
