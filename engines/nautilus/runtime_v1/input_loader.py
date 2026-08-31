"""Descriptor-safe loading for the fixed P1 RunBacktest inputs."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime

from .bootstrap import REQUEST, SIDECAR, RuntimeBootstrapError, _read_regular
from .generated_protocol import (
    MAX_DOCUMENT_BYTES,
    ProtocolValidationError,
    canonical_json_bytes,
    load_document,
)


ARTIFACT_ROOT = "/inputs/artifacts"
_MAX_REQUEST_BYTES = 65_536
_ENVELOPE_KEYS = {
    "message_id",
    "correlation_id",
    "causation_id",
    "engine_run_id",
    "stream_sequence",
    "event_time",
    "initialization_time",
    "schema_version",
    "producer_identity",
    "source_commit",
    "config_digest",
    "payload_digest",
    "payload",
}
_PAYLOAD_KEYS = {
    "command_type",
    "engine_configuration",
    "instrument_catalog",
    "strategy_configuration",
    "market_data",
    "start_time",
    "end_time",
}


def _expected_artifact_link_count() -> int:
    return 0 if ARTIFACT_ROOT == "/inputs/artifacts" else 1


_REFERENCE_KEYS = {"artifact_id", "sha256", "media_type"}
_ARTIFACT_KINDS = {
    "engine_configuration": "engine_configuration",
    "instrument_catalog": "instrument_catalog",
    "strategy_configuration": "target_schedule",
}
_MAX_MARKET_DATA_BYTES = 8 * 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SOURCE_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.ASCII)
_PRODUCER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", re.ASCII)
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", re.ASCII
)


class InputLoadError(ValueError):
    """A request or mounted artifact failed closed validation."""


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    artifact_id: str
    sha256: str
    media_type: str


@dataclass(frozen=True, slots=True)
class RunBacktestRequest:
    message_id: str
    correlation_id: str
    causation_id: str
    engine_run_id: str
    stream_sequence: int
    event_time: str
    initialization_time: str
    schema_version: str
    producer_identity: str
    source_commit: str
    config_digest: str
    payload_digest: str
    command_type: str
    engine_configuration: ArtifactReference
    instrument_catalog: ArtifactReference
    strategy_configuration: ArtifactReference
    market_data: ArtifactReference
    start_time: str
    end_time: str


@dataclass(frozen=True, slots=True)
class RuntimeInputs:
    request: RunBacktestRequest
    engine_configuration: tuple[tuple[str, object], ...]
    instrument_catalog: tuple[tuple[str, object], ...]
    target_schedule: tuple[tuple[str, object], ...]
    market_data: bytes


def _reject_number(_value: str) -> object:
    raise InputLoadError("runtime request contains a noncanonical number")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise InputLoadError("runtime request contains a duplicate key")
        value[key] = item
    return value


def _object(value: object, keys: set[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise InputLoadError(f"runtime {label} shape is invalid")
    return value


def _text(value: object, pattern: re.Pattern[str], label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise InputLoadError(f"runtime {label} is invalid")
    return value


def _uuid(value: object, label: str) -> str:
    if type(value) is not str:
        raise InputLoadError(f"runtime {label} is invalid")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError as exc:
        raise InputLoadError(f"runtime {label} is invalid") from exc
    return value


def _timestamp(value: object, label: str) -> tuple[str, datetime]:
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise InputLoadError(f"runtime {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise InputLoadError(f"runtime {label} is invalid") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise InputLoadError(f"runtime {label} is invalid")
    return value, parsed


def _reference(
    value: object, label: str, expected_media_type: str
) -> ArtifactReference:
    observed = _object(value, _REFERENCE_KEYS, f"{label} reference")
    media_type = observed["media_type"]
    if media_type != expected_media_type:
        raise InputLoadError(f"runtime {label} media type is invalid")
    return ArtifactReference(
        artifact_id=_uuid(observed["artifact_id"], f"{label} artifact ID"),
        sha256=_text(observed["sha256"], _DIGEST, f"{label} digest"),
        media_type=media_type,
    )


def _parse_request(raw: bytes) -> RunBacktestRequest:
    try:
        observed = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except InputLoadError:
        raise
    except (RecursionError, UnicodeDecodeError, ValueError) as exc:
        raise InputLoadError("runtime request JSON is invalid") from exc
    envelope = _object(observed, _ENVELOPE_KEYS, "request")
    try:
        if canonical_json_bytes(envelope) != raw:
            raise InputLoadError("runtime request JSON is not canonical")
    except ProtocolValidationError as exc:
        raise InputLoadError("runtime request JSON is invalid") from exc

    payload = _object(envelope["payload"], _PAYLOAD_KEYS, "command")
    if payload["command_type"] != "RunBacktest":
        raise InputLoadError("runtime command type is invalid")
    references = {
        name: _reference(payload[name], name, "application/json")
        for name in _ARTIFACT_KINDS
    }
    references["market_data"] = _reference(
        payload["market_data"], "market_data", "application/jsonl"
    )
    if len({item.artifact_id for item in references.values()}) != 4 or len(
        {item.sha256 for item in references.values()}
    ) != 4:
        raise InputLoadError("runtime command contains duplicate artifact identity")

    start_time, start = _timestamp(payload["start_time"], "command start time")
    end_time, end = _timestamp(payload["end_time"], "command end time")
    event_time, event = _timestamp(envelope["event_time"], "event time")
    initialization_time, initialization = _timestamp(
        envelope["initialization_time"], "initialization time"
    )
    if end <= start or initialization > event:
        raise InputLoadError("runtime request time window is invalid")
    sequence = envelope["stream_sequence"]
    if type(sequence) is not int or sequence <= 0:
        raise InputLoadError("runtime stream sequence is invalid")
    if envelope["schema_version"] != "1.0.0":
        raise InputLoadError("runtime request schema version is invalid")

    payload_digest = _text(
        envelope["payload_digest"], _DIGEST, "payload digest"
    )
    expected_payload_digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    config_digest = _text(envelope["config_digest"], _DIGEST, "config digest")
    expected_config_digest = hashlib.sha256(
        canonical_json_bytes(
            {
                name: payload[name]
                for name in (
                    "engine_configuration",
                    "instrument_catalog",
                    "strategy_configuration",
                )
            }
        )
    ).hexdigest()
    if not hmac.compare_digest(payload_digest, expected_payload_digest) or not hmac.compare_digest(
        config_digest, expected_config_digest
    ):
        raise InputLoadError("runtime request digest binding is invalid")

    return RunBacktestRequest(
        message_id=_uuid(envelope["message_id"], "message ID"),
        correlation_id=_uuid(envelope["correlation_id"], "correlation ID"),
        causation_id=_uuid(envelope["causation_id"], "causation ID"),
        engine_run_id=_uuid(envelope["engine_run_id"], "engine run ID"),
        stream_sequence=sequence,
        event_time=event_time,
        initialization_time=initialization_time,
        schema_version="1.0.0",
        producer_identity=_text(
            envelope["producer_identity"], _PRODUCER, "producer identity"
        ),
        source_commit=_text(
            envelope["source_commit"], _SOURCE_COMMIT, "source commit"
        ),
        config_digest=config_digest,
        payload_digest=payload_digest,
        command_type="RunBacktest",
        engine_configuration=references["engine_configuration"],
        instrument_catalog=references["instrument_catalog"],
        strategy_configuration=references["strategy_configuration"],
        market_data=references["market_data"],
        start_time=start_time,
        end_time=end_time,
    )


def _read_artifact(directory: int, name: str, maximum: int, digest: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        opened = os.fstat(descriptor)
        expected_links = _expected_artifact_link_count()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != expected_links
            or stat.S_IMODE(opened.st_mode) != 0o400
            or opened.st_size <= 0
            or opened.st_size > maximum
        ):
            raise InputLoadError("runtime artifact identity is invalid")
        chunks: list[bytes] = []
        total = 0
        while block := os.read(descriptor, min(65_536, maximum + 1 - total)):
            total += len(block)
            if total > maximum:
                raise InputLoadError("runtime artifact is oversized")
            chunks.append(block)
        raw = b"".join(chunks)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        final = os.fstat(descriptor)
        if (
            not stat.S_ISREG(named.st_mode)
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            or (final.st_dev, final.st_ino) != (opened.st_dev, opened.st_ino)
            or named.st_nlink != expected_links
            or final.st_nlink != expected_links
            or named.st_size != opened.st_size
            or final.st_size != opened.st_size
            or len(raw) != opened.st_size
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest)
        ):
            raise InputLoadError("runtime artifact identity or digest changed")
        return raw
    except InputLoadError:
        raise
    except OSError as exc:
        raise InputLoadError("runtime artifact is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _freeze(value: object) -> object:
    if type(value) is dict:
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def load_inputs() -> RuntimeInputs:
    """Load the exact request, three documents, and raw market-data bytes."""

    try:
        request_raw = _read_regular(REQUEST, _MAX_REQUEST_BYTES)
        sidecar = _read_regular(SIDECAR, 65)
    except RuntimeBootstrapError as exc:
        raise InputLoadError("runtime request authority is unavailable") from exc
    request_digest = hashlib.sha256(request_raw).hexdigest()
    if sidecar != request_digest.encode("ascii") + b"\n":
        raise InputLoadError("runtime request sidecar is invalid")
    request = _parse_request(request_raw)

    references = {
        "engine_configuration": request.engine_configuration,
        "instrument_catalog": request.instrument_catalog,
        "strategy_configuration": request.strategy_configuration,
        "market_data": request.market_data,
    }
    filenames = {
        f"{name}-{reference.sha256}.{'jsonl' if reference.media_type == 'application/jsonl' else 'json'}"
        for name, reference in references.items()
    }
    directory = -1
    try:
        directory = os.open(
            ARTIFACT_ROOT,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY,
        )
        opened = os.fstat(directory)
        if not stat.S_ISDIR(opened.st_mode) or set(os.listdir(directory)) != filenames:
            raise InputLoadError("runtime artifact records are incomplete or unexpected")
        documents: dict[str, dict[str, object]] = {}
        for name, kind in _ARTIFACT_KINDS.items():
            reference = references[name]
            raw = _read_artifact(
                directory,
                f"{name}-{reference.sha256}.json",
                MAX_DOCUMENT_BYTES[kind],
                reference.sha256,
            )
            try:
                documents[kind] = load_document(kind, raw)
            except ProtocolValidationError as exc:
                raise InputLoadError("runtime artifact grammar is invalid") from exc
        market_reference = references["market_data"]
        market_data = _read_artifact(
            directory,
            f"market_data-{market_reference.sha256}.jsonl",
            _MAX_MARKET_DATA_BYTES,
            market_reference.sha256,
        )
        final = os.fstat(directory)
        if (
            (final.st_dev, final.st_ino) != (opened.st_dev, opened.st_ino)
            or set(os.listdir(directory)) != filenames
        ):
            raise InputLoadError("runtime artifact records changed")
    except InputLoadError:
        raise
    except OSError as exc:
        raise InputLoadError("runtime artifact root is unavailable") from exc
    finally:
        if directory >= 0:
            os.close(directory)

    schedule = documents["target_schedule"]
    targets = schedule["targets"]
    _, start = _timestamp(request.start_time, "command start time")
    _, end = _timestamp(request.end_time, "command end time")
    if type(targets) is not list or any(
        not start
        <= _timestamp(target["effective_at"], "target effective time")[1]
        <= end
        for target in targets
        if type(target) is dict
    ):
        raise InputLoadError("runtime target schedule is outside the command window")

    return RuntimeInputs(
        request=request,
        engine_configuration=_freeze(documents["engine_configuration"]),  # type: ignore[arg-type]
        instrument_catalog=_freeze(documents["instrument_catalog"]),  # type: ignore[arg-type]
        target_schedule=_freeze(schedule),  # type: ignore[arg-type]
        market_data=market_data,
    )


__all__ = [
    "ArtifactReference",
    "InputLoadError",
    "RunBacktestRequest",
    "RuntimeInputs",
    "load_inputs",
]
