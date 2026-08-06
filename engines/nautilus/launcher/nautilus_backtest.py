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
from datetime import datetime
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
_INPUT_ARTIFACT_NAMES = (
    "engine_configuration",
    "instrument_catalog",
    "strategy_configuration",
    "market_data",
)
_MAX_COMMAND_BYTES = 1_048_576
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_CATALOG_FIELDS = {
    "canonical_rows_sha256",
    "content_digest",
    "continuity",
    "fetched_at",
    "first_event_at",
    "importer_version",
    "instrument",
    "known_at",
    "last_event_at",
    "normalization_version",
    "observed_at",
    "parquet_sha256",
    "provider",
    "provenance_schema_version",
    "raw_evidence_sha256",
    "row_count",
    "schema_version",
    "snapshot_schema_version",
    "timeframe",
}
_MARKET_ROW_FIELDS = {"close", "high", "low", "open", "open_time", "volume"}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _read_regular(path: Path, *, maximum_size: int = _MAX_COMMAND_BYTES) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_size > maximum_size:
            raise ValueError("input must be a regular file")
        chunks: list[bytes] = []
        remaining = observed.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
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


def _input_artifact_path(
    name: str, reference: object, artifact_root: Path
) -> Path:
    if name not in _INPUT_ARTIFACT_NAMES:
        raise ValueError("backtest input name is invalid")
    _artifact(reference)
    assert isinstance(reference, dict)
    extension = ".jsonl" if reference["media_type"] == "application/jsonl" else ".json"
    return artifact_root / f"{name}-{reference['sha256']}{extension}"


def validated_input_artifacts(
    request: dict[str, object], artifact_root: Path = Path("/inputs/artifacts")
) -> tuple[bytes, bytes, bytes, bytes]:
    """Read exactly the request-bound artifact files from the private mount.

    The path is derived solely from each command reference.  No supplied
    filename, ambient workspace, or unbound catalog location is accepted.
    """

    if not artifact_root.is_absolute():
        raise ValueError("engine artifact root must be absolute")
    try:
        root = artifact_root.lstat()
    except OSError as exc:
        raise ValueError("engine artifact root is unavailable") from exc
    if stat.S_ISLNK(root.st_mode) or not stat.S_ISDIR(root.st_mode):
        raise ValueError("engine artifact root is unsafe")
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("command payload is invalid")
    values: list[bytes] = []
    for name in _INPUT_ARTIFACT_NAMES:
        reference = payload.get(name)
        path = _input_artifact_path(name, reference, artifact_root)
        value = _read_regular(path, maximum_size=_MAX_ARTIFACT_BYTES)
        assert isinstance(reference, dict)
        if not hmac.compare_digest(
            hashlib.sha256(value).hexdigest(), str(reference["sha256"])
        ):
            raise ValueError("engine artifact digest does not match command")
        values.append(value)
    return tuple(values)  # type: ignore[return-value]


def _input_artifacts_sha256(artifacts: tuple[bytes, bytes, bytes, bytes]) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                name: hashlib.sha256(value).hexdigest()
                for name, value in zip(_INPUT_ARTIFACT_NAMES, artifacts, strict=True)
            }
        )
    ).hexdigest()


def _canonical_object(value: bytes, *, label: str) -> dict[str, object]:
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} JSON is invalid") from exc
    if not isinstance(decoded, dict) or _canonical_json_bytes(decoded) != value:
        raise ValueError(f"{label} must be a canonical JSON object")
    return decoded


def _required_sha256(value: object, *, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} digest is invalid")


def _canonical_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{label} timestamp is not canonical")
    return parsed


def validate_zero_order_fixture_inputs(
    artifacts: tuple[bytes, bytes, bytes, bytes]
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    """Validate the 04A catalog and 04B target data used by 04C.

    04C deliberately accepts only a zero target.  It runs a real Nautilus
    engine cycle without creating orders; position sizing and execution-model
    adapters belong to later packets.
    """

    configuration, catalog_bytes, target_bytes, market_bytes = artifacts
    configuration_value = _canonical_object(configuration, label="engine configuration")
    if configuration_value != {
        "execution_mode": "zero-order",
        "run_analysis": False,
        "schema_version": "nautilus-backtest-engine-config-v1",
    }:
        raise ValueError("engine configuration is not the fixed zero-order model")
    catalog = _canonical_object(catalog_bytes, label="catalog manifest")
    if (
        set(catalog) != _CATALOG_FIELDS
        or catalog.get("schema_version") != "market-dataset-manifest-v1"
        or catalog.get("timeframe") != "1m"
    ):
        raise ValueError("catalog manifest is not the 04A schema")
    row_count = catalog.get("row_count")
    if type(row_count) is not int or row_count <= 0 or row_count > 4096:
        raise ValueError("catalog manifest row_count is invalid")
    _required_sha256(catalog.get("canonical_rows_sha256"), label="catalog rows")
    _required_sha256(catalog.get("parquet_sha256"), label="catalog parquet")
    for name in ("content_digest", "raw_evidence_sha256"):
        _required_sha256(catalog.get(name), label=f"catalog {name}")
    instrument = catalog.get("instrument")
    if instrument != {
        "product_type": "crypto_spot",
        "symbol": "BTCUSDT",
        "venue": "BINANCE",
    }:
        raise ValueError("catalog instrument is not the supported fixture")
    for name in (
        "first_event_at",
        "last_event_at",
        "observed_at",
        "fetched_at",
        "known_at",
    ):
        _canonical_timestamp(catalog.get(name), label=f"catalog {name}")
    continuity = catalog.get("continuity")
    if (
        not isinstance(continuity, dict)
        or set(continuity) != {"timeframe", "gap_report", "duplicate_report"}
        or continuity["timeframe"] != catalog["timeframe"]
        or not isinstance(continuity["gap_report"], list)
        or not isinstance(continuity["duplicate_report"], list)
    ):
        raise ValueError("catalog continuity is invalid")
    target = _canonical_object(target_bytes, label="strategy target")
    if set(target) != {
        "effective_at",
        "positions",
        "schema_version",
        "source_signal_ids",
        "target_id",
    }:
        raise ValueError("strategy target is not the 04B contract")
    if (
        not isinstance(target["target_id"], str)
        or _UUID.fullmatch(target["target_id"]) is None
        or target["schema_version"] != "1.0.0"
        or not isinstance(target["source_signal_ids"], list)
        or not target["source_signal_ids"]
        or any(
            not isinstance(signal_id, str) or _UUID.fullmatch(signal_id) is None
            for signal_id in target["source_signal_ids"]
        )
        or len(set(target["source_signal_ids"])) != len(target["source_signal_ids"])
    ):
        raise ValueError("strategy target is invalid")
    if type(target["positions"]) is not list or target["positions"]:
        raise ValueError("04C accepts only a zero target")
    _canonical_timestamp(target["effective_at"], label="strategy target")
    if not market_bytes.endswith(b"\n"):
        raise ValueError("market fixture must use canonical JSONL")
    lines = market_bytes[:-1].split(b"\n")
    if len(lines) != row_count or any(not line for line in lines):
        raise ValueError("market fixture does not match catalog row_count")
    rows = tuple(_canonical_object(line, label="market fixture row") for line in lines)
    if any(set(row) != _MARKET_ROW_FIELDS for row in rows):
        raise ValueError("market fixture row fields are invalid")
    for row in rows:
        _canonical_timestamp(row["open_time"], label="market fixture")
        if any(not isinstance(row[name], str) or not row[name] for name in _MARKET_ROW_FIELDS - {"open_time"}):
            raise ValueError("market fixture prices and volume are invalid")
    if not hmac.compare_digest(
        hashlib.sha256(_canonical_json_bytes(list(rows))).hexdigest(),
        str(catalog["canonical_rows_sha256"]),
    ):
        raise ValueError("market fixture does not match the 04A canonical rows")
    return catalog, rows


def _run_nautilus(
    fixture: tuple[dict[str, object], tuple[dict[str, object], ...]]
) -> tuple[int, int, int, int]:
    """Execute a no-network, no-order Nautilus engine cycle.

    04C uses the 04A canonical JSONL projection, hash-bound to the catalog's
    verified canonical-row digest.  The fixed zero target has no sizing/order
    strategy, so the engine receives real bar data while retaining zero orders.
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
    from nautilus_trader.model.currencies import USDT
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Money, Price, Quantity
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True),
            run_analysis=False,
        )
    )
    try:
        catalog, rows = fixture
        instrument = TestInstrumentProvider.btcusdt_binance()
        if str(instrument.id) != "BTCUSDT.BINANCE":
            raise ValueError("Nautilus fixture instrument is incompatible")
        engine.add_venue(
            venue=Venue("BINANCE"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(1_000_000, USDT)],
        )
        engine.add_instrument(instrument)
        bar_type = BarType.from_str("BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL")
        bars = []
        for row in rows:
            timestamp = _canonical_timestamp(row["open_time"], label="market fixture")
            timestamp_ns = int(timestamp.timestamp()) * 1_000_000_000 + timestamp.microsecond * 1_000
            bars.append(
                Bar(
                    bar_type,
                    Price.from_str(str(row["open"])),
                    Price.from_str(str(row["high"])),
                    Price.from_str(str(row["low"])),
                    Price.from_str(str(row["close"])),
                    Quantity.from_str(str(row["volume"])),
                    timestamp_ns,
                    timestamp_ns,
                )
            )
        if len(bars) != int(catalog["row_count"]):
            raise ValueError("fixture row count changed before Nautilus ingestion")
        engine.add_data(bars)
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


def _event(
    request: dict[str, object],
    artifacts: tuple[bytes, bytes, bytes, bytes],
    result: tuple[int, int, int, int],
) -> dict[str, object]:
    iterations, total_orders, total_positions, total_events = result
    payload = {
        "event_type": _EVENT_TYPE,
        "family": "ENGINE_LIFECYCLE",
        "attributes": [
            {"name": "input_artifacts_sha256", "value": _input_artifacts_sha256(artifacts)},
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
        artifacts = validated_input_artifacts(request)
        fixture = validate_zero_order_fixture_inputs(artifacts)
        print(
            _canonical_json_bytes(
                _event(request, artifacts, _run_nautilus(fixture))
            ).decode("utf-8")
        )
    except (ImportError, OSError, ValueError) as exc:
        _fail(str(exc))


if __name__ == "__main__":
    main()
