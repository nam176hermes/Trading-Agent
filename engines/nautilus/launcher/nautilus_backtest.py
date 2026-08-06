"""CPython 3.12-only Nautilus backtest launcher.

This file is copied into the external runtime closure.  It intentionally does
not import any root-project package: the controller and engine communicate only
through the sealed command JSON plus its SHA-256 sidecar.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path
from typing import NoReturn, Sequence
from uuid import UUID, uuid5


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.ASCII)
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.ASCII,
)
_ARTIFACT_FIELDS = {"artifact_id", "sha256", "media_type"}
_PAYLOAD_FIELDS = {
    "command_type",
    "engine_configuration",
    "instrument_catalog",
    "strategy_configuration",
    "market_data",
    "start_time",
    "end_time",
}
_ENVELOPE_FIELDS = {
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
_EVENT_TYPE = "NautilusBacktestCompleted"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _read_regular(path: Path) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode):
            raise ValueError("input must be a regular file")
        chunks: list[bytes] = []
        remaining = 1_048_576
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        if remaining == 0 and os.read(descriptor, 1):
            raise ValueError("input exceeds the bounded command size")
        return b"".join(chunks)
    except OSError as exc:
        raise ValueError("input cannot be safely read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _artifact(value: object) -> None:
    if not isinstance(value, dict) or set(value) != _ARTIFACT_FIELDS:
        raise ValueError("backtest artifact reference is invalid")
    if (
        not isinstance(value["artifact_id"], str)
        or _UUID.fullmatch(value["artifact_id"]) is None
        or not isinstance(value["sha256"], str)
        or _SHA256.fullmatch(value["sha256"]) is None
        or value["media_type"] not in {"application/json", "application/jsonl"}
    ):
        raise ValueError("backtest artifact reference is invalid")


def _validate_request(value: object, raw: bytes) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ENVELOPE_FIELDS:
        raise ValueError("command envelope fields are invalid")
    if _canonical_json_bytes(value) != raw:
        raise ValueError("command envelope bytes are not canonical")
    for field in ("message_id", "correlation_id", "causation_id", "engine_run_id"):
        if not isinstance(value[field], str) or _UUID.fullmatch(value[field]) is None:
            raise ValueError("command envelope UUID is invalid")
    if (
        isinstance(value["stream_sequence"], bool)
        or not isinstance(value["stream_sequence"], int)
        or value["stream_sequence"] <= 0
        or not isinstance(value["source_commit"], str)
        or _COMMIT.fullmatch(value["source_commit"]) is None
        or not isinstance(value["config_digest"], str)
        or _SHA256.fullmatch(value["config_digest"]) is None
        or not isinstance(value["payload_digest"], str)
        or _SHA256.fullmatch(value["payload_digest"]) is None
    ):
        raise ValueError("command envelope metadata is invalid")
    payload = value["payload"]
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS or payload["command_type"] != "RunBacktest":
        raise ValueError("only RunBacktest is accepted")
    for field in ("engine_configuration", "instrument_catalog", "strategy_configuration", "market_data"):
        _artifact(payload[field])
    if not isinstance(payload["start_time"], str) or not isinstance(payload["end_time"], str) or payload["end_time"] <= payload["start_time"]:
        raise ValueError("backtest window is invalid")
    if hashlib.sha256(_canonical_json_bytes(payload)).hexdigest() != value["payload_digest"]:
        raise ValueError("command payload digest is invalid")
    configuration = {
        name: payload[name]
        for name in ("engine_configuration", "instrument_catalog", "strategy_configuration")
    }
    if hashlib.sha256(_canonical_json_bytes(configuration)).hexdigest() != value["config_digest"]:
        raise ValueError("command configuration digest is invalid")
    return value


def validated_request(request_path: Path, sidecar_path: Path) -> dict[str, object]:
    """Read and validate the exact controller command envelope."""

    raw = _read_regular(request_path)
    sidecar = _read_regular(sidecar_path)
    try:
        token = sidecar.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("request digest sidecar must be ASCII") from exc
    if _SHA256.fullmatch(token) is None or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), token
    ):
        raise ValueError("request digest sidecar does not bind the command")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("command envelope JSON is invalid") from exc
    return _validate_request(document, raw)


def _run_nautilus() -> tuple[int, int, int, int]:
    """Execute a no-network, no-order Nautilus engine cycle.

    Catalog-data adapters and target-position strategy installation are mounted
    by later packets; this packet proves the isolated engine boundary with a
    zero-order backtest rather than fabricating an execution effect.
    """

    wheels_root = Path("/engine/wheels")
    extraction_root = Path("/tmp/nautilus-wheels")
    extraction_root.mkdir(mode=0o700)
    wheels = tuple(sorted(wheels_root.glob("*.whl"), key=lambda path: path.name))
    if not wheels:
        raise ValueError("Nautilus runtime wheel closure is missing")
    for wheel in wheels:
        destination = extraction_root / hashlib.sha256(wheel.name.encode("ascii")).hexdigest()
        destination.mkdir(mode=0o700)
        try:
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    relative = Path(member.filename)
                    if (
                        relative.is_absolute()
                        or not member.filename
                        or ".." in relative.parts
                        or stat.S_ISLNK(member.external_attr >> 16)
                        or member.is_dir()
                    ):
                        if member.is_dir() and member.filename and ".." not in relative.parts:
                            continue
                        raise ValueError("Nautilus wheel has an unsafe member")
                archive.extractall(destination)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError("Nautilus runtime wheel is unreadable") from exc
        sys.path.insert(0, str(destination))
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.common.config import LoggingConfig
    from nautilus_trader.config import BacktestEngineConfig

    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True),
            run_analysis=False,
        )
    )
    try:
        engine.run()
        result = engine.get_result()
        return (
            int(result.iterations),
            int(result.total_orders),
            int(result.total_positions),
            int(result.total_events),
        )
    finally:
        engine.dispose()


def _event(request: dict[str, object], result: tuple[int, int, int, int]) -> dict[str, object]:
    iterations, total_orders, total_positions, total_events = result
    payload = {
        "event_type": _EVENT_TYPE,
        "family": "ENGINE_LIFECYCLE",
        "attributes": [
            {"name": "iterations", "value": iterations},
            {"name": "total_events", "value": total_events},
            {"name": "total_orders", "value": total_orders},
            {"name": "total_positions", "value": total_positions},
        ],
    }
    return {
        "message_id": str(uuid5(UUID(str(request["message_id"])), _EVENT_TYPE)),
        "correlation_id": request["correlation_id"],
        "causation_id": request["message_id"],
        "engine_run_id": request["engine_run_id"],
        "stream_sequence": int(request["stream_sequence"]) + 1,
        "event_time": request["event_time"],
        "initialization_time": request["initialization_time"],
        "schema_version": request["schema_version"],
        "producer_identity": request["producer_identity"],
        "source_commit": request["source_commit"],
        "config_digest": request["config_digest"],
        "payload_digest": hashlib.sha256(_canonical_json_bytes(payload)).hexdigest(),
        "payload": payload,
    }


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        _fail("expected request.json and request.sha256")
    try:
        request = validated_request(Path(arguments[0]), Path(arguments[1]))
        print(_canonical_json_bytes(_event(request, _run_nautilus())).decode("utf-8"))
    except (ImportError, OSError, ValueError) as exc:
        _fail(str(exc))


if __name__ == "__main__":
    main()
