"""Command-at-a-time P1 paper loop over one streaming native session."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
import time

from .backtest_runner import BacktestRun
from .bootstrap import require_product_lineage
from .control_channel import (
    PAPER_PROTOCOL_SCHEMA,
    PaperCommand,
    framed_document,
    iter_payloads,
    parse_command,
)
from .event_collector import CollectedExecution
from .event_projector import (
    ProjectedEventStream,
    _event,
    _schedule,
    _target_events,
    project_event_stream,
)
from .final_state import validate_final_state
from .generated_protocol import canonical_json_bytes
from .input_loader import RuntimeInputs
from .paper_session import (
    PaperCheckpoint,
    PaperEngineSession,
    _NativeCheckpoint,
    bind_checkpoint,
)


_ZERO_DIGEST = "0" * 64
_UPSTREAM_COMMIT = "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"


@dataclass(frozen=True, slots=True)
class PaperStep:
    response_stream: bytes
    checkpoint: PaperCheckpoint
    run: BacktestRun | None = None
    projected_stream: ProjectedEventStream | None = None


@dataclass(frozen=True, slots=True)
class PaperExecution:
    response_stream: bytes
    run: BacktestRun
    projected_stream: ProjectedEventStream
    checkpoints: tuple[PaperCheckpoint, ...]


class PaperRuntimeRejected(ValueError):
    reason_code: str
    response_stream: bytes
    checkpoint: PaperCheckpoint | None


def _ack(
    command: PaperCommand,
    state: str,
    *,
    accepted: bool = True,
    reason: str = "ACCEPTED",
) -> bytes:
    return framed_document(
        {
            "accepted": accepted,
            "command_digest": command.digest,
            "command_sequence": command.sequence,
            "frame_type": "ACK",
            "owner_id": command.owner_id,
            "reason_code": reason,
            "request_id": command.request_id,
            "schema_version": PAPER_PROTOCOL_SCHEMA,
            "session_id": command.session_id,
            "state": state,
        }
    )


def _checkpoint_frame(checkpoint: PaperCheckpoint) -> bytes:
    value = asdict(checkpoint)
    return framed_document(
        {
            "checkpoint": value,
            "checkpoint_sha256": hashlib.sha256(
                canonical_json_bytes(value)
            ).hexdigest(),
            "frame_type": "CHECKPOINT",
            "schema_version": PAPER_PROTOCOL_SCHEMA,
        }
    )


def _validate_start(command: PaperCommand, inputs: RuntimeInputs) -> None:
    expected = {
        name: {
            "artifact_id": reference.artifact_id,
            "media_type": reference.media_type,
            "sha256": reference.sha256,
        }
        for name, reference in (
            ("engine_configuration", inputs.request.engine_configuration),
            ("instrument_catalog", inputs.request.instrument_catalog),
            ("strategy_configuration", inputs.request.strategy_configuration),
        )
    }
    if command.command_type != "StartPaperEngine" or any(
        command.command.get(name) != reference for name, reference in expected.items()
    ):
        raise ValueError("paper start command does not match sealed inputs")


def _event_request_ids(
    events: tuple[dict[str, object], ...],
    start: PaperCommand,
    targets: dict[str, PaperCommand],
    trigger: PaperCommand,
) -> tuple[str, ...]:
    order_requests: dict[str, str] = {}
    result: list[str] = []
    active_target_request: str | None = None
    for event in events:
        event_type = event["event_type"]
        if event_type == "RunStarted":
            request = start.request_id
        elif event_type in {
            "TargetAccepted",
            "TargetQuantityPlanned",
            "OrderSubmitted",
        }:
            target = targets.get(str(event["target_id"]))
            request = (target or trigger).request_id
            active_target_request = request
            if event_type == "OrderSubmitted":
                order_requests[str(event["client_order_id"])] = request
        elif event_type == "Fill":
            request = order_requests[str(event["client_order_id"])]
        elif event_type in {"PositionObserved", "AccountObserved", "RunCompleted"}:
            request = trigger.request_id
        elif active_target_request is not None:
            request = active_target_request
        else:
            raise ValueError("paper projected event causality is invalid")
        result.append(request)
    return tuple(result)


def _event_frames(
    events: tuple[dict[str, object], ...],
    start: PaperCommand,
    targets: dict[str, PaperCommand],
    trigger: PaperCommand,
) -> tuple[bytes, str]:
    request_ids = _event_request_ids(events, start, targets, trigger)
    frames: list[bytes] = []
    last_digest = _ZERO_DIGEST
    for event, request_id in zip(events, request_ids, strict=True):
        last_digest = hashlib.sha256(canonical_json_bytes(event)).hexdigest()
        frames.append(
            framed_document(
                {
                    "event": event,
                    "event_digest": last_digest,
                    "event_sequence": event["sequence"],
                    "frame_type": "EVENT",
                    "owner_id": start.owner_id,
                    "request_id": request_id,
                    "schema_version": PAPER_PROTOCOL_SCHEMA,
                    "session_id": start.session_id,
                }
            )
        )
    return b"".join(frames), last_digest


def _project_prefix(
    inputs: RuntimeInputs,
    executions: tuple[CollectedExecution, ...],
    closure_digest: str,
) -> tuple[dict[str, object], ...]:
    request = inputs.request
    events = [
        _event(
            "RunStarted",
            2,
            request.start_time,
            origin="CONTROL_PLANE",
            native_type=None,
            runtime_family="cython-v1",
            engine_version="1.231.0",
            upstream_commit=_UPSTREAM_COMMIT,
            closure_digest=closure_digest,
            config_digest=request.config_digest,
            catalog_digest=request.instrument_catalog.sha256,
            data_digest=request.market_data.sha256,
        )
    ]
    schedule = _schedule(inputs)
    target_ids = tuple(str(dict(item.plan)["target_id"]) for item in executions)
    if target_ids != tuple(schedule)[: len(target_ids)]:
        raise ValueError("paper projected target prefix is invalid")
    for execution in executions:
        events.extend(_target_events(execution, schedule, len(events) + 2))
    return tuple(events)


class PaperCommandLoop:
    """One owner and one native engine, advanced by exactly one command at a time."""

    def __init__(self, inputs: RuntimeInputs, lineage: dict[str, object]) -> None:
        require_product_lineage(lineage)
        self._inputs = inputs
        self._lineage = lineage
        self._closure = str(lineage["closure_sha256"])
        self._session: PaperEngineSession | None = None
        self._start: PaperCommand | None = None
        self._targets: dict[str, PaperCommand] = {}
        self._last_sequence = 0
        self._state = "CREATED"
        self._last_emitted_event = 0
        self._last_event_digest = _ZERO_DIGEST
        self._event_prefix_sha256 = _ZERO_DIGEST
        self._child_identity = _ZERO_DIGEST
        self._events: tuple[dict[str, object], ...] = ()

    def _identity(self, command: PaperCommand) -> None:
        if (
            command.session_id != self._inputs.request.engine_run_id
            or command.owner_id != self._inputs.request.causation_id
        ):
            raise ValueError("paper command identity is not input-bound")

    def _active_session(self) -> PaperEngineSession:
        if self._session is None:
            raise ValueError("paper native session is unavailable")
        return self._session

    def _checkpoint(
        self,
        raw: bytes,
        command: PaperCommand,
        acknowledgement: bytes,
        native: _NativeCheckpoint,
        state: str,
    ) -> PaperCheckpoint:
        acknowledgement_payloads = iter_payloads(acknowledgement)
        if len(acknowledgement_payloads) != 1:
            raise ValueError("paper acknowledgement framing is invalid")
        return bind_checkpoint(
            native,
            state=state,
            session_id=command.session_id,
            owner_id=command.owner_id,
            last_request_id=command.request_id,
            last_command_type=command.command_type,
            last_command_frame_sha256=hashlib.sha256(raw).hexdigest(),
            last_command_digest=command.digest,
            last_emitted_event=self._last_emitted_event,
            last_event_digest=self._last_event_digest,
            event_prefix_sha256=self._event_prefix_sha256,
            last_acknowledgement_sha256=hashlib.sha256(
                acknowledgement_payloads[0]
            ).hexdigest(),
            child_identity=self._child_identity,
            closure_digest=self._closure,
        )

    def _reject(self, command: PaperCommand, reason: str) -> PaperRuntimeRejected:
        state = "RECONCILIATION_REQUIRED" if self._session is not None else "FAILED"
        acknowledgement = _ack(command, state, accepted=False, reason=reason)
        checkpoint = None
        if self._session is not None:
            try:
                self._session.dispose()
            finally:
                self._session = None
        self._state = state
        rejection = PaperRuntimeRejected(f"paper command rejected: {reason}")
        rejection.reason_code = reason
        rejection.response_stream = acknowledgement
        rejection.checkpoint = checkpoint
        return rejection

    def _emit_prefix(
        self,
        prefix: tuple[dict[str, object], ...],
        command: PaperCommand,
    ) -> bytes:
        if prefix[: len(self._events)] != self._events or len(prefix) < len(
            self._events
        ):
            raise ValueError("paper event prefix changed")
        delta = prefix[len(self._events) :]
        if self._start is None:
            raise ValueError("paper start authority is unavailable")
        frames, digest = _event_frames(delta, self._start, self._targets, command)
        self._events = prefix
        if delta:
            self._last_emitted_event = int(delta[-1]["sequence"])
            self._last_event_digest = digest
            self._event_prefix_sha256 = hashlib.sha256(
                canonical_json_bytes(prefix)
            ).hexdigest()
        return frames

    def accept(self, raw: bytes) -> PaperStep:
        try:
            command = parse_command(raw)
        except BaseException:
            if self._session is not None:
                self._session.dispose()
                self._session = None
            self._state = "RECONCILIATION_REQUIRED"
            raise
        try:
            self._identity(command)
            if command.sequence != self._last_sequence + 1 or self._state not in {
                "CREATED",
                "RUNNING",
            }:
                raise ValueError("paper command sequence or state is invalid")
            if self._state == "CREATED":
                _validate_start(command, self._inputs)
                session = PaperEngineSession(self._inputs)
                try:
                    native = session.start(command.sequence)
                except BaseException as primary:
                    session.dispose(primary)
                    raise AssertionError("unreachable")
                self._session = session
                self._start = command
                self._child_identity = hashlib.sha256(
                    canonical_json_bytes(
                        {
                            "closure_digest": self._closure,
                            "monotonic_generation": time.monotonic_ns(),
                            "owner_id": command.owner_id,
                            "process_id": os.getpid(),
                            "session_id": command.session_id,
                        }
                    )
                ).hexdigest()
                state, acknowledgement, run, projected = (
                    "RUNNING",
                    _ack(command, "RUNNING"),
                    None,
                    None,
                )
                events = self._emit_prefix(
                    _project_prefix(self._inputs, (), self._closure), command
                )
            elif command.command_type == "SubmitTargetPortfolio":
                native = self._active_session().submit_target(
                    command.command, command.sequence
                )
                portfolio = command.command["target_portfolio"]
                if type(portfolio) is not dict:
                    raise ValueError("paper target command is invalid")
                target_id = str(portfolio["target_id"])
                if target_id in self._targets:
                    raise ValueError("paper target command is duplicated")
                self._targets[target_id] = command
                state, acknowledgement, run, projected = (
                    "RUNNING",
                    _ack(command, "RUNNING"),
                    None,
                    None,
                )
                events = self._emit_prefix(
                    _project_prefix(
                        self._inputs,
                        self._active_session().executions(),
                        self._closure,
                    ),
                    command,
                )
            elif command.command_type == "InspectEngineRun":
                if command.command["target_engine_run_id"] != command.session_id:
                    raise ValueError("paper inspect command targets another session")
                native = self._active_session().inspect(command.sequence)
                state, acknowledgement, events, run, projected = (
                    "RUNNING",
                    _ack(command, "RUNNING"),
                    b"",
                    None,
                    None,
                )
            elif command.command_type == "StopPaperEngine":
                if self._start is None:
                    raise ValueError("paper start authority is unavailable")
                if command.command["target_engine_run_id"] != command.session_id:
                    raise ValueError("paper stop command targets another session")
                session = self._active_session()
                run, native = session.stop(command.sequence)
                completion = validate_final_state(
                    self._inputs,
                    self._lineage,
                    run,
                    accepted_target_prefix=session.processed_target_ids,
                )
                projected = project_event_stream(
                    self._inputs,
                    run,
                    completion,
                    closure_digest=self._closure,
                    upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
                    accepted_target_prefix=session.processed_target_ids,
                )
                events = self._emit_prefix(projected.events, command)
                state, acknowledgement = "STOPPING", _ack(command, "STOPPING")
            else:
                raise ValueError("paper command is unsupported in native session")
            self._last_sequence = command.sequence
            self._state = state
            checkpoint = self._checkpoint(raw, command, acknowledgement, native, state)
            return PaperStep(
                acknowledgement + events + _checkpoint_frame(checkpoint),
                checkpoint,
                run,
                projected,
            )
        except PaperRuntimeRejected:
            raise
        except BaseException:
            raise self._reject(command, "ENGINE_STATE_UNCERTAIN") from None

    def close_input(self) -> None:
        if self._state != "STOPPING" or self._session is None:
            if self._session is not None:
                self._session.dispose()
                self._session = None
            self._state = "RECONCILIATION_REQUIRED"
            raise ValueError("paper input ended without an accepted stop")
        self._session.dispose()
        self._session = None
        self._state = "STOPPED"

    def abort(self) -> None:
        if self._session is not None:
            self._session.dispose()
            self._session = None
        if self._state != "STOPPED":
            self._state = "RECONCILIATION_REQUIRED"


def run_commands(
    inputs: RuntimeInputs, command_stream: bytes, lineage: dict[str, object]
) -> PaperExecution:
    loop = PaperCommandLoop(inputs, lineage)
    responses: list[bytes] = []
    checkpoints: list[PaperCheckpoint] = []
    run: BacktestRun | None = None
    projected: ProjectedEventStream | None = None
    for raw in iter_payloads(command_stream):
        step = loop.accept(raw)
        responses.append(step.response_stream)
        checkpoints.append(step.checkpoint)
        run = step.run or run
        projected = step.projected_stream or projected
    loop.close_input()
    if run is None or projected is None:
        raise ValueError("paper command stream is incomplete")
    return PaperExecution(b"".join(responses), run, projected, tuple(checkpoints))


__all__ = [
    "PaperCommandLoop",
    "PaperExecution",
    "PaperRuntimeRejected",
    "PaperStep",
    "run_commands",
]
