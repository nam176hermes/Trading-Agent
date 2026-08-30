from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, model_validator

from packages.engine_contracts import (
    InspectEngineRun,
    RequestExecutionReconciliation,
    Sha256Hex,
    StartPaperEngine,
    StopPaperEngine,
    SubmitTargetPortfolio,
    canonical_json_bytes,
    payload_digest,
)

from .events import (
    P1AccountObserved,
    P1Event,
    P1Fill,
    P1PositionObserved,
    P1RunCompleted,
    P1RunStarted,
    P1TargetAccepted,
    P1TargetQuantityPlanned,
    P1OrderSubmitted,
)
from .state_machine import validate_event_stream
from .paper_causality import stop_event_allowed


PAPER_PROTOCOL_SCHEMA = "nautilus-paper-session-v2"
MAX_PAPER_FRAME_BYTES = 65_536


class PaperSessionState(StrEnum):
    CREATED = "CREATED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


def paper_request_id(session_id: UUID, command_sequence: int) -> UUID:
    if type(session_id) is not UUID or type(command_sequence) is not int or command_sequence < 1:
        raise ValueError("paper request identity inputs are invalid")
    return uuid5(session_id, f"{PAPER_PROTOCOL_SCHEMA}:command:{command_sequence}")


PaperCommand: TypeAlias = Annotated[
    StartPaperEngine
    | SubmitTargetPortfolio
    | StopPaperEngine
    | InspectEngineRun
    | RequestExecutionReconciliation,
    Field(discriminator="command_type"),
]
PaperCommandName: TypeAlias = Literal[
    "StartPaperEngine",
    "SubmitTargetPortfolio",
    "StopPaperEngine",
    "InspectEngineRun",
    "RequestExecutionReconciliation",
]


class _PaperModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


class PaperCommandFrame(_PaperModel):
    schema_version: Literal["nautilus-paper-session-v2"]
    frame_type: Literal["COMMAND"]
    session_id: UUID
    owner_id: UUID
    request_id: UUID
    command_sequence: Annotated[StrictInt, Field(gt=0)]
    command_digest: Sha256Hex
    command: PaperCommand

    @model_validator(mode="after")
    def _digest_matches(self) -> "PaperCommandFrame":
        if self.request_id != paper_request_id(self.session_id, self.command_sequence):
            raise ValueError("paper request identity does not match command sequence")
        if payload_digest(self.command) != self.command_digest:
            raise ValueError("paper command digest does not match command")
        return self


class PaperEventFrame(_PaperModel):
    schema_version: Literal["nautilus-paper-session-v2"]
    frame_type: Literal["EVENT"]
    session_id: UUID
    owner_id: UUID
    request_id: UUID
    event_sequence: Annotated[StrictInt, Field(gt=0)]
    event_digest: Sha256Hex
    event: P1Event

    @model_validator(mode="after")
    def _event_matches(self) -> "PaperEventFrame":
        if self.event.sequence != self.event_sequence:
            raise ValueError("paper event sequence does not match event")
        if payload_digest(self.event) != self.event_digest:
            raise ValueError("paper event digest does not match event")
        return self


class PaperCommandAcknowledgement(_PaperModel):
    schema_version: Literal["nautilus-paper-session-v2"]
    frame_type: Literal["ACK"]
    session_id: UUID
    owner_id: UUID
    request_id: UUID
    command_sequence: Annotated[StrictInt, Field(gt=0)]
    command_digest: Sha256Hex
    state: PaperSessionState
    accepted: bool
    reason_code: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")]

    @model_validator(mode="after")
    def _coherent_verdict(self) -> "PaperCommandAcknowledgement":
        if self.request_id != paper_request_id(self.session_id, self.command_sequence):
            raise ValueError("paper acknowledgement request identity is invalid")
        if self.accepted != (self.reason_code == "ACCEPTED"):
            raise ValueError("paper acknowledgement verdict is inconsistent")
        return self


class PaperSessionCheckpoint(_PaperModel):
    schema_version: Literal["nautilus-paper-session-v2"]
    session_id: UUID
    owner_id: UUID
    state: PaperSessionState
    last_accepted_command: Annotated[StrictInt, Field(gt=0)]
    last_request_id: UUID
    last_command_type: PaperCommandName
    last_command_frame_sha256: Sha256Hex
    last_command_digest: Sha256Hex
    last_emitted_event: Annotated[StrictInt, Field(ge=0)]
    last_event_digest: Sha256Hex
    event_prefix_sha256: Sha256Hex
    last_acknowledged_command: Annotated[StrictInt, Field(ge=0)]
    last_acknowledgement_sha256: Sha256Hex
    semantic_state_hash: Sha256Hex
    child_identity: Sha256Hex
    closure_digest: Sha256Hex
    portfolio_state_hash: Sha256Hex

    @model_validator(mode="after")
    def _request_matches_prefix(self) -> "PaperSessionCheckpoint":
        if self.last_request_id != paper_request_id(
            self.session_id, self.last_accepted_command
        ):
            raise ValueError("paper checkpoint request identity is invalid")
        if self.last_acknowledged_command > self.last_accepted_command:
            raise ValueError("paper checkpoint acknowledgement prefix is invalid")
        if self.last_accepted_command - self.last_acknowledged_command > 1:
            raise ValueError("paper checkpoint has multiple unacknowledged commands")
        return self


@dataclass(frozen=True, slots=True)
class _AcceptedCommand:
    frame_sha256: str
    sequence: int
    digest: str
    command_type: PaperCommandName
    expected_ack_state: PaperSessionState


_COMMAND_ADAPTER = TypeAdapter(PaperCommandFrame)
_EVENT_ADAPTER = TypeAdapter(PaperEventFrame)
_ACK_ADAPTER = TypeAdapter(PaperCommandAcknowledgement)
_ALLOWED_COMMANDS = frozenset(
    {
        "StartPaperEngine",
        "SubmitTargetPortfolio",
        "StopPaperEngine",
        "InspectEngineRun",
        "RequestExecutionReconciliation",
    }
)
_TERMINAL_STATES = frozenset({PaperSessionState.STOPPED, PaperSessionState.FAILED, PaperSessionState.RECONCILIATION_REQUIRED})
_FAILURE_TRANSITIONS = {PaperSessionState.FAILED, PaperSessionState.RECONCILIATION_REQUIRED}
_TRANSITIONS = {
    PaperSessionState.CREATED: _FAILURE_TRANSITIONS | {PaperSessionState.STARTING},
    PaperSessionState.STARTING: _FAILURE_TRANSITIONS
    | {PaperSessionState.RUNNING, PaperSessionState.STOPPING},
    PaperSessionState.RUNNING: _FAILURE_TRANSITIONS | {PaperSessionState.STOPPING},
    PaperSessionState.STOPPING: _FAILURE_TRANSITIONS | {PaperSessionState.STOPPED},
}


def _canonical_object(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not raw:
        raise ValueError("paper frame must be non-empty bytes")
    if len(raw) > MAX_PAPER_FRAME_BYTES:
        raise ValueError("paper frame exceeds maximum size")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("paper frame contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("paper frame is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise ValueError("paper frame is not canonical JSON")
    return value


def parse_paper_command_frame(raw: bytes) -> PaperCommandFrame:
    value = _canonical_object(raw)
    command = value.get("command")
    command_type = command.get("command_type") if isinstance(command, dict) else None
    if command_type not in _ALLOWED_COMMANDS:
        raise ValueError(f"unsupported paper command: {command_type!r}")
    return _COMMAND_ADAPTER.validate_json(raw)


def parse_paper_event_frame(raw: bytes) -> PaperEventFrame:
    _canonical_object(raw)
    return _EVENT_ADAPTER.validate_json(raw)


def parse_paper_acknowledgement(raw: bytes) -> PaperCommandAcknowledgement:
    _canonical_object(raw)
    return _ACK_ADAPTER.validate_json(raw)


def _event_prefix_sha256(events: tuple[P1Event, ...]) -> str:
    if not events:
        return "0" * 64
    return hashlib.sha256(canonical_json_bytes(events)).hexdigest()


def _validate_restored_event_prefix(
    checkpoint: PaperSessionCheckpoint, events: tuple[P1Event, ...]
) -> None:
    if checkpoint.last_emitted_event == 0:
        if events or {checkpoint.last_event_digest, checkpoint.event_prefix_sha256} != {
            "0" * 64
        }:
            raise ValueError("paper checkpoint event prefix does not match")
        return
    if (
        len(events) != checkpoint.last_emitted_event - 1
        or not events
        or not isinstance(events[0], P1RunStarted)
        or any(event.sequence != sequence for sequence, event in enumerate(events, start=2))
        or any(isinstance(event, P1RunStarted) for event in events[1:])
        or any(
            current.simulation_time < previous.simulation_time
            for previous, current in zip(events, events[1:], strict=False)
        )
        or payload_digest(events[-1]) != checkpoint.last_event_digest
        or _event_prefix_sha256(events) != checkpoint.event_prefix_sha256
    ):
        raise ValueError("paper checkpoint event prefix does not match")
    completions = [index for index, event in enumerate(events) if isinstance(event, P1RunCompleted)]
    if completions:
        if completions != [len(events) - 1]:
            raise ValueError("paper checkpoint event prefix does not match")
        validate_event_stream(events)


class PaperSessionJournal:
    def __init__(self, *, session_id: UUID, owner_id: UUID) -> None:
        self.session_id = session_id
        self.owner_id = owner_id
        self.state = PaperSessionState.CREATED
        self.last_accepted_command = 0
        self.last_command_digest = "0" * 64
        self.last_emitted_event = 0
        self.last_event_digest = "0" * 64
        self.last_acknowledged_command = 0
        self.last_acknowledgement_sha256 = "0" * 64
        self.semantic_state_hash = "0" * 64
        self._requests: dict[UUID, _AcceptedCommand] = {}
        self._events: list[P1Event] = []

    def _identity(self, session_id: UUID, owner_id: UUID) -> None:
        if session_id != self.session_id or owner_id != self.owner_id:
            raise ValueError("paper session identity or owner does not match")

    def accept_command(self, raw: bytes) -> PaperCommandFrame:
        value = _canonical_object(raw)
        request_value = value.get("request_id")
        try:
            request_id = UUID(request_value) if isinstance(request_value, str) else None
        except ValueError:
            request_id = None
        raw_digest = hashlib.sha256(raw).hexdigest()
        if request_id is not None and request_id in self._requests:
            if self._requests[request_id].frame_sha256 != raw_digest:
                raise ValueError("paper request replay used changed bytes")
            raise ValueError("duplicate request")
        frame = parse_paper_command_frame(raw)
        self._identity(frame.session_id, frame.owner_id)
        if self.state in _TERMINAL_STATES:
            raise ValueError("paper command rejected in terminal session state")
        if self.last_acknowledged_command != self.last_accepted_command:
            raise ValueError("previous paper command is not acknowledged")
        expected = self.last_accepted_command + 1
        if frame.command_sequence != expected:
            self.state = PaperSessionState.RECONCILIATION_REQUIRED
            raise ValueError("paper command sequence gap")
        self._validate_command_state(frame.command)
        command_type = frame.command.command_type
        expected_ack_state = (
            PaperSessionState.RUNNING
            if isinstance(frame.command, StartPaperEngine)
            else self.state
        )
        self._requests[frame.request_id] = _AcceptedCommand(
            frame_sha256=raw_digest,
            sequence=frame.command_sequence,
            digest=frame.command_digest,
            command_type=command_type,
            expected_ack_state=expected_ack_state,
        )
        self.last_accepted_command = frame.command_sequence
        self.last_command_digest = frame.command_digest
        return frame

    def _validate_command_state(self, command: PaperCommand) -> None:
        if isinstance(command, StartPaperEngine):
            if self.state is not PaperSessionState.CREATED:
                raise ValueError(f"StartPaperEngine not accepted in {self.state.value}")
            self.state = PaperSessionState.STARTING
            return
        if isinstance(command, SubmitTargetPortfolio):
            if self.state is not PaperSessionState.RUNNING:
                raise ValueError(f"SubmitTargetPortfolio not accepted in {self.state.value}")
            return
        if isinstance(command, (StopPaperEngine, InspectEngineRun)):
            if command.target_engine_run_id != self.session_id:
                raise ValueError("paper command target does not match session")
            if isinstance(command, StopPaperEngine):
                if self.state is not PaperSessionState.RUNNING:
                    raise ValueError(f"StopPaperEngine not accepted in {self.state.value}")
                self.state = PaperSessionState.STOPPING

    def record_event(self, raw: bytes) -> PaperEventFrame:
        frame = parse_paper_event_frame(raw)
        self._identity(frame.session_id, frame.owner_id)
        if self.state is PaperSessionState.CREATED:
            raise ValueError("paper event requires a started session")
        if self.state in _TERMINAL_STATES:
            raise ValueError("paper event rejected in terminal session state")
        if frame.request_id not in self._requests:
            raise ValueError("paper event causal request was not accepted")
        command_type = self._requests[frame.request_id].command_type
        expected = 2 if self.last_emitted_event == 0 else self.last_emitted_event + 1
        if frame.event_sequence != expected:
            self.state = PaperSessionState.RECONCILIATION_REQUIRED
            raise ValueError("paper event sequence gap")
        if self.last_emitted_event == 0 and not isinstance(frame.event, P1RunStarted):
            self.state = PaperSessionState.RECONCILIATION_REQUIRED
            raise ValueError("paper event stream must begin with P1 RunStarted")
        if self.last_emitted_event != 0 and isinstance(frame.event, P1RunStarted):
            self.state = PaperSessionState.RECONCILIATION_REQUIRED
            raise ValueError("paper event stream contains duplicate RunStarted")
        if command_type == "StopPaperEngine":
            allowed = stop_event_allowed(tuple(self._events), frame.event)
        else:
            allowed = (
                command_type == "StartPaperEngine"
                if isinstance(frame.event, P1RunStarted)
                else command_type == "SubmitTargetPortfolio"
                if isinstance(
                    frame.event,
                    (
                        P1TargetAccepted,
                        P1TargetQuantityPlanned,
                        P1OrderSubmitted,
                        P1Fill,
                        P1PositionObserved,
                        P1AccountObserved,
                    ),
                )
                else False
            )
        if not allowed:
            self.state = PaperSessionState.RECONCILIATION_REQUIRED
            raise ValueError("paper command/event causality is invalid")
        self.last_emitted_event = frame.event_sequence
        self.last_event_digest = frame.event_digest
        self._events.append(frame.event)
        return frame

    def record_ack(self, raw: bytes) -> PaperCommandAcknowledgement:
        acknowledgement = parse_paper_acknowledgement(raw)
        self._identity(acknowledgement.session_id, acknowledgement.owner_id)
        command = self._requests.get(acknowledgement.request_id)
        if command is None or (
            command.sequence != acknowledgement.command_sequence
            or command.digest != acknowledgement.command_digest
        ):
            raise ValueError("paper acknowledgement does not match an accepted command")
        if acknowledgement.command_sequence <= self.last_acknowledged_command:
            raise ValueError("duplicate paper acknowledgement")
        if acknowledgement.command_sequence != self.last_acknowledged_command + 1:
            self.state = PaperSessionState.RECONCILIATION_REQUIRED
            raise ValueError("paper acknowledgement sequence gap")
        if acknowledgement.accepted:
            if acknowledgement.state is not command.expected_ack_state:
                raise ValueError("paper acknowledgement state is invalid for command")
            if acknowledgement.state is not self.state:
                self.transition(acknowledgement.state)
        elif acknowledgement.state not in {
            PaperSessionState.FAILED,
            PaperSessionState.RECONCILIATION_REQUIRED,
        }:
            raise ValueError("rejected paper acknowledgement state is invalid")
        else:
            self.transition(acknowledgement.state)
        self.last_acknowledged_command = acknowledgement.command_sequence
        self.last_acknowledgement_sha256 = hashlib.sha256(raw).hexdigest()
        return acknowledgement

    def end_of_input(self) -> None:
        last_request_id = paper_request_id(self.session_id, self.last_accepted_command)
        last_command = self._requests.get(last_request_id)
        if (
            self.state is PaperSessionState.STOPPING
            and last_command is not None
            and last_command.command_type == "StopPaperEngine"
            and self.last_acknowledged_command == self.last_accepted_command
        ):
            try:
                validate_event_stream(tuple(self._events))
            except ValueError as exc:
                self.state = PaperSessionState.RECONCILIATION_REQUIRED
                raise ValueError("paper EOF requires a complete P1 event stream") from exc
            self.state = PaperSessionState.STOPPED
            return
        if self.state not in _TERMINAL_STATES:
            self.state = PaperSessionState.RECONCILIATION_REQUIRED
        raise ValueError("paper EOF requires an acknowledged stop command")

    def transition(self, state: PaperSessionState) -> None:
        if state not in _TRANSITIONS.get(self.state, frozenset()):
            raise ValueError(f"invalid paper session transition: {self.state.value}->{state.value}")
        self.state = state

    def checkpoint(
        self,
        *,
        semantic_state_hash: str,
        child_identity: str,
        closure_digest: str,
        portfolio_state_hash: str,
    ) -> PaperSessionCheckpoint:
        last_request_id = paper_request_id(self.session_id, self.last_accepted_command)
        checkpoint = PaperSessionCheckpoint(
            schema_version=PAPER_PROTOCOL_SCHEMA,
            session_id=self.session_id,
            owner_id=self.owner_id,
            state=self.state,
            last_accepted_command=self.last_accepted_command,
            last_request_id=last_request_id,
            last_command_type=self._requests[last_request_id].command_type,
            last_command_frame_sha256=self._requests[last_request_id].frame_sha256,
            last_command_digest=self.last_command_digest,
            last_emitted_event=self.last_emitted_event,
            last_event_digest=self.last_event_digest,
            event_prefix_sha256=_event_prefix_sha256(tuple(self._events)),
            last_acknowledged_command=self.last_acknowledged_command,
            last_acknowledgement_sha256=self.last_acknowledgement_sha256,
            semantic_state_hash=semantic_state_hash,
            child_identity=child_identity,
            closure_digest=closure_digest,
            portfolio_state_hash=portfolio_state_hash,
        )
        self.semantic_state_hash = checkpoint.semantic_state_hash
        return checkpoint

    @classmethod
    def restore(
        cls,
        checkpoint: PaperSessionCheckpoint,
        *,
        session_id: UUID,
        owner_id: UUID,
        semantic_state_hash: str,
        child_identity: str,
        closure_digest: str,
        portfolio_state_hash: str,
        event_prefix: tuple[P1Event, ...],
    ) -> "PaperSessionJournal":
        supplied = (
            session_id,
            owner_id,
            semantic_state_hash,
            child_identity,
            closure_digest,
            portfolio_state_hash,
        )
        recorded = (
            checkpoint.session_id,
            checkpoint.owner_id,
            checkpoint.semantic_state_hash,
            checkpoint.child_identity,
            checkpoint.closure_digest,
            checkpoint.portfolio_state_hash,
        )
        if supplied != recorded:
            raise ValueError("paper checkpoint authority does not match")
        _validate_restored_event_prefix(checkpoint, event_prefix)
        journal = cls(session_id=session_id, owner_id=owner_id)
        journal.state = checkpoint.state
        journal.last_accepted_command = checkpoint.last_accepted_command
        expected_ack_state = (
            PaperSessionState.RUNNING
            if checkpoint.last_command_type == "StartPaperEngine"
            else checkpoint.state
        )
        journal._requests[checkpoint.last_request_id] = _AcceptedCommand(
            frame_sha256=checkpoint.last_command_frame_sha256,
            sequence=checkpoint.last_accepted_command,
            digest=checkpoint.last_command_digest,
            command_type=checkpoint.last_command_type,
            expected_ack_state=expected_ack_state,
        )
        journal.last_command_digest = checkpoint.last_command_digest
        journal.last_emitted_event = checkpoint.last_emitted_event
        journal.last_event_digest = checkpoint.last_event_digest
        journal._events.extend(event_prefix)
        journal.last_acknowledged_command = checkpoint.last_acknowledged_command
        journal.last_acknowledgement_sha256 = checkpoint.last_acknowledgement_sha256
        journal.semantic_state_hash = checkpoint.semantic_state_hash
        return journal
