"""Bounded canonical framing for the P1 local paper child."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import BinaryIO
from uuid import UUID, uuid5

from .generated_protocol import canonical_json_bytes, validate_document


PAPER_PROTOCOL_SCHEMA = "nautilus-paper-session-v2"
PAPER_SOURCE_NAMES = (
    "__init__.py", "backtest_runner.py", "bootstrap.py", "control_channel.py",
    "currency_metadata.py", "dependency_scope.py", "diagnostics.py", "errors.py",
    "event_collector.py", "event_projector.py", "final_state.py",
    "generated_protocol.py", "input_loader.py", "instrument_factory.py",
    "jsonl_writer.py", "main.py", "market_data_loader.py", "paper_main.py",
    "paper_prefix.py", "paper_runner.py", "paper_session.py", "profile.py",
    "session.py", "target_planner.py", "target_strategy.py",
)
MAX_FRAME_BYTES = 65_536
MAX_FRAMES = 4_096
_HEADER_BYTES = 4
_FRAME_KEYS = {
    "schema_version",
    "frame_type",
    "session_id",
    "owner_id",
    "request_id",
    "command_sequence",
    "command_digest",
    "command",
}
_COMMANDS = {
    "StartPaperEngine",
    "SubmitTargetPortfolio",
    "StopPaperEngine",
    "InspectEngineRun",
    "RequestExecutionReconciliation",
}
_REFERENCE_KEYS = {"artifact_id", "sha256", "media_type"}
_START_KEYS = {
    "command_type",
    "engine_configuration",
    "instrument_catalog",
    "strategy_configuration",
}


@dataclass(frozen=True, slots=True)
class PaperCommand:
    session_id: str
    owner_id: str
    request_id: str
    sequence: int
    digest: str
    command_type: str
    command: dict[str, object]


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ValueError("paper frame contains a duplicate key")
        value[key] = item
    return value


def _uuid(value: object, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"paper {label} is invalid")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"paper {label} is invalid") from exc
    return value


def request_id(session_id: str, sequence: int) -> str:
    return str(uuid5(UUID(session_id), f"{PAPER_PROTOCOL_SCHEMA}:command:{sequence}"))


def paper_child_identity(
    *,
    closure_digest: str,
    owner_id: str,
    paper_source_sha256: str,
    process_id: int,
    process_start_ticks: int,
    session_id: str,
) -> str:
    """Bind one paper child to its PID-namespace process generation."""

    if (
        type(closure_digest) is not str
        or len(closure_digest) != 64
        or any(character not in "0123456789abcdef" for character in closure_digest)
        or type(process_id) is not int
        or process_id < 1
        or type(process_start_ticks) is not int
        or process_start_ticks < 1
        or type(paper_source_sha256) is not str
        or len(paper_source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in paper_source_sha256)
    ):
        raise ValueError("paper child process authority is invalid")
    _uuid(owner_id, "owner ID")
    _uuid(session_id, "session ID")
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "closure_digest": closure_digest,
                "owner_id": owner_id,
                "paper_source_sha256": paper_source_sha256,
                "process_id": process_id,
                "process_start_ticks": process_start_ticks,
                "session_id": session_id,
            }
        )
    ).hexdigest()


def _reference(value: object) -> None:
    if type(value) is not dict or set(value) != _REFERENCE_KEYS:
        raise ValueError("paper artifact reference is invalid")
    digest = value.get("sha256")
    if (
        _uuid(value.get("artifact_id"), "artifact ID") != value["artifact_id"]
        or type(digest) is not str
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or value.get("media_type") != "application/json"
    ):
        raise ValueError("paper artifact reference is invalid")


def _validate_command(command: dict[str, object]) -> str:
    command_type = command.get("command_type")
    if command_type == "StartPaperEngine":
        if set(command) != _START_KEYS:
            raise ValueError("paper start command is invalid")
        for name in _START_KEYS - {"command_type"}:
            _reference(command[name])
    elif command_type == "SubmitTargetPortfolio":
        if set(command) != {"command_type", "target_portfolio"}:
            raise ValueError("paper target command is invalid")
        validate_document(
            "target_schedule",
            {
                "schema_version": "nautilus-p1-target-schedule-v1",
                "targets": [command["target_portfolio"]],
            },
        )
    elif command_type in {"StopPaperEngine", "InspectEngineRun"}:
        if set(command) != {"command_type", "target_engine_run_id"}:
            raise ValueError("paper session command is invalid")
        _uuid(command.get("target_engine_run_id"), "target engine run ID")
    elif command_type == "RequestExecutionReconciliation":
        if set(command) != {"command_type"}:
            raise ValueError("paper reconciliation command is invalid")
    else:
        raise ValueError("paper command type is invalid")
    return command_type


def frame_payload(payload: bytes) -> bytes:
    if type(payload) is not bytes or not payload or len(payload) > MAX_FRAME_BYTES:
        raise ValueError("paper frame exceeds maximum size")
    return len(payload).to_bytes(_HEADER_BYTES, "big") + payload


def iter_payloads(stream: bytes) -> tuple[bytes, ...]:
    if type(stream) is not bytes:
        raise ValueError("paper control stream must be bytes")
    payloads: list[bytes] = []
    cursor = 0
    while cursor < len(stream):
        if len(payloads) == MAX_FRAMES or len(stream) - cursor < _HEADER_BYTES:
            raise ValueError("paper control stream is truncated or oversized")
        size = int.from_bytes(stream[cursor : cursor + _HEADER_BYTES], "big")
        cursor += _HEADER_BYTES
        if size == 0 or size > MAX_FRAME_BYTES or len(stream) - cursor < size:
            raise ValueError("paper control stream is truncated or oversized")
        payloads.append(stream[cursor : cursor + size])
        cursor += size
    return tuple(payloads)


def read_payload(stream: BinaryIO) -> bytes | None:
    header = stream.read(_HEADER_BYTES)
    if header == b"":
        return None
    if len(header) != _HEADER_BYTES:
        raise ValueError("paper control stream is truncated or oversized")
    size = int.from_bytes(header, "big")
    if size == 0 or size > MAX_FRAME_BYTES:
        raise ValueError("paper control stream is truncated or oversized")
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ValueError("paper control stream is truncated or oversized")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def parse_command(raw: bytes) -> PaperCommand:
    if type(raw) is not bytes or not raw or len(raw) > MAX_FRAME_BYTES:
        raise ValueError("paper command frame is invalid")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("paper command frame is invalid") from exc
    if (
        type(value) is not dict
        or set(value) != _FRAME_KEYS
        or canonical_json_bytes(value) != raw
    ):
        raise ValueError("paper command frame is not canonical")
    if (
        value["schema_version"] != PAPER_PROTOCOL_SCHEMA
        or value["frame_type"] != "COMMAND"
    ):
        raise ValueError("paper command protocol is invalid")
    sequence = value["command_sequence"]
    command = value["command"]
    if type(sequence) is not int or sequence < 1 or type(command) is not dict:
        raise ValueError("paper command authority is invalid")
    command_type = _validate_command(command)
    digest = value["command_digest"]
    session_id = _uuid(value["session_id"], "session ID")
    if (
        command_type not in _COMMANDS
        or type(digest) is not str
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or hashlib.sha256(canonical_json_bytes(command)).hexdigest() != digest
        or _uuid(value["owner_id"], "owner ID") != value["owner_id"]
        or _uuid(value["request_id"], "request ID") != request_id(session_id, sequence)
    ):
        raise ValueError("paper command authority is invalid")
    return PaperCommand(
        session_id=session_id,
        owner_id=value["owner_id"],
        request_id=value["request_id"],
        sequence=sequence,
        digest=digest,
        command_type=command_type,
        command=command,
    )


def framed_document(document: dict[str, object]) -> bytes:
    return frame_payload(canonical_json_bytes(document))


__all__ = [
    "MAX_FRAME_BYTES",
    "PAPER_PROTOCOL_SCHEMA",
    "PaperCommand",
    "frame_payload",
    "framed_document",
    "iter_payloads",
    "parse_command",
    "read_payload",
    "request_id",
]
