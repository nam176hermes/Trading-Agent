"""CPython 3.12-only Nautilus backtest launcher.

This file is copied into the external runtime closure.  It intentionally does
not import any root-project package: the controller and engine communicate only
through the sealed command JSON plus its SHA-256 sidecar.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import importlib.machinery
import importlib.util
import json
import os
import re
import stat
import sys
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Context, Decimal, InvalidOperation, localcontext
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
_ZERO_ORDER_PAYLOAD_FIELDS = {
    "command_type",
    "engine_configuration",
    "instrument_catalog",
    "strategy_configuration",
    "market_data",
    "start_time",
    "end_time",
}
_SIMULATION_PAYLOAD_FIELDS = {
    *_ZERO_ORDER_PAYLOAD_FIELDS,
    "simulation_scenario",
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
_SIMULATION_EVENT_TYPE = "NautilusBacktestSimulationCompleted"
_ZERO_ORDER_INPUT_ARTIFACT_NAMES = (
    "engine_configuration",
    "instrument_catalog",
    "strategy_configuration",
    "market_data",
)
_SIMULATION_INPUT_ARTIFACT_NAMES = (
    *_ZERO_ORDER_INPUT_ARTIFACT_NAMES,
    "simulation_scenario",
)
_MAX_COMMAND_BYTES = 1_048_576
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_MAX_STRATEGY_BYTES = 1_048_576
_CLOSURE_MANIFEST_PATH = Path("/engine/closure-manifest.json")
_TARGET_PORTFOLIO_STRATEGY_PATH = Path(
    "/engine/launcher/target_portfolio_strategy.py"
)
_TARGET_PORTFOLIO_STRATEGY_TARGET = "/engine/launcher/target_portfolio_strategy.py"
_TARGET_PORTFOLIO_STRATEGY_SOURCE = (
    "files/engine/launcher/target_portfolio_strategy.py"
)
_TARGET_PORTFOLIO_STRATEGY_MODULE = "_sealed_target_portfolio_strategy"
_CLOSURE_FILE_FIELDS = {"mode", "path", "sha256", "size", "target"}
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
_SIMULATION_SCENARIO_IDS = {
    "long-accounting",
    "short-accounting",
    "partial-fill",
    "same-bar-stop-take-profit",
    "stale-quote",
    "zero-liquidity",
    "session-boundary",
    "event-digest",
}
_SIMULATION_CONFIGURATION = {
    "execution_mode": "execution-simulation",
    "run_analysis": False,
    "schema_version": "nautilus-backtest-engine-config-v1",
}
_SIMULATION_STRATEGY_FIELDS = {
    "effective_at",
    "positions",
    "schema_version",
}
_SIMULATION_POSITION_FIELDS = {"instrument", "target_quantity"}
_SIMULATION_INSTRUMENT = {
    "product_type": "crypto_spot",
    "symbol": "BTCUSDT",
    "venue": "BINANCE",
}
_SIMULATION_SCENARIO_FIELDS = {
    "catalog_sha256",
    "events",
    "fee_rate",
    "instrument",
    "liquidity_limit",
    "market_data_sha256",
    "scenario_id",
    "schema_version",
    "session_policy",
    "slippage_bps",
    "stale_quote_threshold_seconds",
    "stop_price",
    "stop_take_profit_precedence",
    "strategy_sha256",
    "take_profit_price",
}
_SIMULATION_EVENT_FIELDS = {
    "ask",
    "bid",
    "close",
    "event_time",
    "high",
    "low",
    "open",
    "quote_time",
    "sequence",
    "session_open",
    "volume",
}
_CANONICAL_DECIMAL = re.compile(
    r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$", re.ASCII
)
_SIMULATION_DECIMAL_CONTEXT = Context(prec=80, Emin=-999, Emax=999)
_MAX_DECIMAL_SIGNIFICANT_DIGITS = 38
_MAX_DECIMAL_EXPONENT_MAGNITUDE = 38
_NAUTILUS_PRICE_MAX = Decimal("17014118346046")
_NAUTILUS_QUANTITY_MAX = Decimal("34028236692093")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_closure_manifest_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii") + b"\n"


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


def _strict_json_document(raw: bytes) -> object:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("closure manifest contains a duplicate field")
            value[key] = item
        return value

    try:
        document = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("closure manifest JSON is invalid") from exc
    if _canonical_closure_manifest_bytes(document) != raw:
        raise ValueError("closure manifest bytes are not canonical")
    return document


def _verified_strategy_source(record: dict[str, object]) -> bytes:
    expected_size = record["size"]
    expected_sha256 = record["sha256"]
    assert isinstance(expected_size, int)
    assert isinstance(expected_sha256, str)
    descriptor = -1
    try:
        descriptor = os.open(
            _TARGET_PORTFOLIO_STRATEGY_PATH,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size != expected_size
            or opened.st_size > _MAX_STRATEGY_BYTES
            or stat.S_IMODE(opened.st_mode) != 0o400
        ):
            raise ValueError("target portfolio strategy identity is invalid")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise ValueError("target portfolio strategy read was incomplete")
            chunks.append(block)
            remaining -= len(block)
        value = b"".join(chunks)
        named = _TARGET_PORTFOLIO_STRATEGY_PATH.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(named.st_mode)
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            or named.st_size != opened.st_size
            or stat.S_IMODE(named.st_mode) != 0o400
            or not hmac.compare_digest(
                hashlib.sha256(value).hexdigest(), expected_sha256
            )
        ):
            raise ValueError("target portfolio strategy is not manifest-bound")
        return value
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("target portfolio strategy cannot be safely read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class _VerifiedStrategyLoader:
    def __init__(self, source: bytes) -> None:
        self._source = source

    def create_module(self, spec):
        del spec
        return None

    def exec_module(self, module) -> None:
        code = compile(
            self._source,
            str(_TARGET_PORTFOLIO_STRATEGY_PATH),
            "exec",
            dont_inherit=True,
        )
        exec(code, module.__dict__)


class _SealedWheelFinder:
    def __init__(self, roots: tuple[Path, ...]) -> None:
        self._search_path = [str(root) for root in reversed(roots)]

    def find_spec(self, fullname, path=None, target=None):
        del target
        if path is not None:
            return None
        spec = importlib.machinery.PathFinder.find_spec(
            fullname, self._search_path
        )
        if spec is not None or fullname in sys.stdlib_module_names:
            return spec
        raise ModuleNotFoundError(
            f"{fullname} is unavailable in the sealed wheel closure"
        )


@contextmanager
def _sealed_wheel_import_scope(roots: tuple[Path, ...]):
    """Resolve top-level dependencies only from sealed roots, then restore."""

    original_meta_path = tuple(sys.meta_path)
    original_modules = dict(sys.modules)
    finder = _SealedWheelFinder(roots)
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path[:] = original_meta_path
        for name in tuple(sys.modules):
            if name not in original_modules:
                sys.modules.pop(name, None)
        sys.modules.update(original_modules)


def _load_target_portfolio_strategy() -> tuple[type, type]:
    """Load only the exact strategy bytes named by the sealed closure manifest."""

    raw_manifest = _read_regular(_CLOSURE_MANIFEST_PATH)
    document = _strict_json_document(raw_manifest)
    if not isinstance(document, dict):
        raise ValueError("closure manifest must be an object")
    files = document.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("closure manifest files are invalid")
    matches: list[dict[str, object]] = []
    for record in files:
        if not isinstance(record, dict) or set(record) != _CLOSURE_FILE_FIELDS:
            raise ValueError("closure manifest file record is invalid")
        path = record["path"]
        target = record["target"]
        digest = record["sha256"]
        size = record["size"]
        mode = record["mode"]
        if (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or any(part in {"", ".", ".."} for part in Path(path).parts)
            or not isinstance(target, str)
            or not target.startswith("/")
            or any(part in {"", ".", ".."} for part in Path(target).parts)
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or mode not in {"0400", "0500"}
        ):
            raise ValueError("closure manifest file record is invalid")
        if target == _TARGET_PORTFOLIO_STRATEGY_TARGET:
            matches.append(record)
    if len(matches) != 1:
        raise ValueError("closure manifest must name exactly one target strategy")
    match = matches[0]
    if (
        match["path"] != _TARGET_PORTFOLIO_STRATEGY_SOURCE
        or match["mode"] != "0400"
        or not isinstance(match["size"], int)
        or match["size"] > _MAX_STRATEGY_BYTES
    ):
        raise ValueError("target portfolio strategy manifest record is invalid")

    source = _verified_strategy_source(match)
    try:
        syntax = ast.parse(
            source,
            filename=str(_TARGET_PORTFOLIO_STRATEGY_PATH),
            mode="exec",
        )
    except (SyntaxError, ValueError) as exc:
        raise ValueError("target portfolio strategy module syntax is invalid") from exc
    required_symbols = {
        "TargetPortfolioStrategy",
        "TargetPortfolioStrategyConfig",
    }
    if any(
        sum(
            isinstance(node, ast.ClassDef) and node.name == required
            for node in syntax.body
        )
        != 1
        for required in required_symbols
    ):
        raise ValueError("target portfolio strategy module symbols are invalid")
    loader = _VerifiedStrategyLoader(source)
    spec = importlib.util.spec_from_file_location(
        _TARGET_PORTFOLIO_STRATEGY_MODULE,
        _TARGET_PORTFOLIO_STRATEGY_PATH,
        loader=loader,
    )
    if (
        spec is None
        or spec.loader is not loader
        or spec.origin != str(_TARGET_PORTFOLIO_STRATEGY_PATH)
    ):
        raise ValueError("target portfolio strategy module identity is invalid")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(_TARGET_PORTFOLIO_STRATEGY_MODULE)
    sys.modules[_TARGET_PORTFOLIO_STRATEGY_MODULE] = module
    try:
        loader.exec_module(module)
    except BaseException as exc:
        if previous is None:
            sys.modules.pop(_TARGET_PORTFOLIO_STRATEGY_MODULE, None)
        else:
            sys.modules[_TARGET_PORTFOLIO_STRATEGY_MODULE] = previous
        raise ValueError("target portfolio strategy module cannot be executed") from exc
    strategy = getattr(module, "TargetPortfolioStrategy", None)
    configuration = getattr(module, "TargetPortfolioStrategyConfig", None)
    if (
        not isinstance(strategy, type)
        or strategy.__module__ != _TARGET_PORTFOLIO_STRATEGY_MODULE
        or not isinstance(configuration, type)
        or configuration.__module__ != _TARGET_PORTFOLIO_STRATEGY_MODULE
    ):
        if previous is None:
            sys.modules.pop(_TARGET_PORTFOLIO_STRATEGY_MODULE, None)
        else:
            sys.modules[_TARGET_PORTFOLIO_STRATEGY_MODULE] = previous
        raise ValueError("target portfolio strategy module symbols are invalid")
    return strategy, configuration


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


def _validate_request(
    value: object, raw: bytes, *, profile: str = "zero-order"
) -> dict[str, object]:
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
    if profile == "zero-order":
        payload_fields = _ZERO_ORDER_PAYLOAD_FIELDS
        command_type = "RunBacktest"
        artifact_names = _ZERO_ORDER_INPUT_ARTIFACT_NAMES
    elif profile == "execution-simulation":
        payload_fields = _SIMULATION_PAYLOAD_FIELDS
        command_type = "RunBacktestSimulation"
        artifact_names = _SIMULATION_INPUT_ARTIFACT_NAMES
    else:
        raise ValueError("launcher profile is invalid")
    if (
        not isinstance(payload, dict)
        or set(payload) != payload_fields
        or payload["command_type"] != command_type
    ):
        raise ValueError(f"only {command_type} is accepted")
    for field in artifact_names:
        _artifact(payload[field])
    if profile == "execution-simulation" and len(
        {_canonical_json_bytes(payload[field]) for field in artifact_names}
    ) != len(artifact_names):
        raise ValueError("simulation contains a duplicate artifact reference")
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


def validated_request(
    request_path: Path,
    sidecar_path: Path,
    *,
    profile: str = "zero-order",
) -> dict[str, object]:
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
    return _validate_request(document, raw, profile=profile)


def _input_artifact_path(
    name: str, reference: object, artifact_root: Path
) -> Path:
    if name not in _SIMULATION_INPUT_ARTIFACT_NAMES:
        raise ValueError("backtest input name is invalid")
    _artifact(reference)
    assert isinstance(reference, dict)
    extension = ".jsonl" if reference["media_type"] == "application/jsonl" else ".json"
    return artifact_root / f"{name}-{reference['sha256']}{extension}"


def validated_input_artifacts(
    request: dict[str, object],
    artifact_root: Path = Path("/inputs/artifacts"),
    *,
    profile: str = "zero-order",
) -> tuple[bytes, ...]:
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
    if profile == "zero-order" and payload.get("command_type") == "RunBacktest":
        artifact_names = _ZERO_ORDER_INPUT_ARTIFACT_NAMES
    elif (
        profile == "execution-simulation"
        and payload.get("command_type") == "RunBacktestSimulation"
    ):
        artifact_names = _SIMULATION_INPUT_ARTIFACT_NAMES
    else:
        raise ValueError("command does not match launcher profile")
    values: list[bytes] = []
    for name in artifact_names:
        reference = payload.get(name)
        path = _input_artifact_path(name, reference, artifact_root)
        value = _read_regular(path, maximum_size=_MAX_ARTIFACT_BYTES)
        assert isinstance(reference, dict)
        if not hmac.compare_digest(
            hashlib.sha256(value).hexdigest(), str(reference["sha256"])
        ):
            raise ValueError("engine artifact digest does not match command")
        values.append(value)
    return tuple(values)


def _input_artifacts_sha256(
    artifacts: tuple[bytes, ...], *, profile: str = "zero-order"
) -> str:
    names = (
        _ZERO_ORDER_INPUT_ARTIFACT_NAMES
        if profile == "zero-order"
        else _SIMULATION_INPUT_ARTIFACT_NAMES
        if profile == "execution-simulation"
        else ()
    )
    if not names or len(artifacts) != len(names):
        raise ValueError("input artifact set does not match launcher profile")
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                name: hashlib.sha256(value).hexdigest()
                for name, value in zip(names, artifacts, strict=True)
            }
        )
    ).hexdigest()


def _canonical_object(value: bytes, *, label: str) -> dict[str, object]:
    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, item in pairs:
            if name in result:
                raise ValueError(f"{label} contains a duplicate key")
            result[name] = item
        return result

    try:
        decoded = json.loads(value, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} JSON is invalid") from exc
    if not isinstance(decoded, dict) or _canonical_json_bytes(decoded) != value:
        raise ValueError(f"{label} must be a canonical JSON object")
    return decoded


def _decimal(value: object, *, label: str, positive: bool = False) -> Decimal:
    if (
        not isinstance(value, str)
        or _CANONICAL_DECIMAL.fullmatch(value) is None
        or value == "-0"
    ):
        raise ValueError(f"{label} must be a canonical decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a canonical decimal string") from exc
    decimal_tuple = parsed.as_tuple()
    if (
        len(decimal_tuple.digits) > _MAX_DECIMAL_SIGNIFICANT_DIGITS
        or not isinstance(decimal_tuple.exponent, int)
        or abs(decimal_tuple.exponent) > _MAX_DECIMAL_EXPONENT_MAGNITUDE
    ):
        raise ValueError(f"{label} exceeds the simulation decimal bound")
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise ValueError(f"{label} decimal is outside its allowed range")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("simulation produced a non-finite decimal")
    if value.is_zero():
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _require_nautilus_fixed_point_bound(
    value: Decimal, *, maximum: Decimal, label: str
) -> None:
    if not value.is_finite() or abs(value) > maximum:
        raise ValueError(f"{label} exceeds the Nautilus fixed-point bound")


def _require_nautilus_price(value: Decimal, *, label: str) -> None:
    _require_nautilus_fixed_point_bound(
        value, maximum=_NAUTILUS_PRICE_MAX, label=label
    )


def _require_nautilus_quantity(value: Decimal, *, label: str) -> None:
    _require_nautilus_fixed_point_bound(
        value, maximum=_NAUTILUS_QUANTITY_MAX, label=label
    )


def _require_precision(value: Decimal, precision: int, *, label: str) -> None:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -precision:
        raise ValueError(f"{label} exceeds the fixed instrument precision")


def _fixed_precision_text(value: Decimal, precision: int) -> str:
    _require_precision(value, precision, label="Nautilus fixture value")
    return format(value, f".{precision}f")


def _nautilus_price_text(value: Decimal, precision: int, *, label: str) -> str:
    _require_nautilus_price(value, label=label)
    return _fixed_precision_text(value, precision)


def _nautilus_quantity_text(value: Decimal, precision: int, *, label: str) -> str:
    _require_nautilus_quantity(value, label=label)
    return _fixed_precision_text(value, precision)


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


def _validated_simulation_catalog(
    catalog_bytes: bytes, market_bytes: bytes
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    catalog = _canonical_object(catalog_bytes, label="catalog manifest")
    if (
        set(catalog) != _CATALOG_FIELDS
        or catalog.get("schema_version") != "market-dataset-manifest-v1"
        or catalog.get("timeframe") != "1m"
        or catalog.get("instrument") != _SIMULATION_INSTRUMENT
    ):
        raise ValueError("simulation catalog manifest is invalid")
    row_count = catalog.get("row_count")
    if type(row_count) is not int or not 0 < row_count <= 32:
        raise ValueError("simulation catalog row_count is invalid")
    for name in (
        "canonical_rows_sha256",
        "content_digest",
        "parquet_sha256",
        "raw_evidence_sha256",
    ):
        _required_sha256(catalog.get(name), label=f"catalog {name}")
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
        or continuity["timeframe"] != "1m"
        or continuity["gap_report"] != []
        or continuity["duplicate_report"] != []
    ):
        raise ValueError("simulation catalog continuity is invalid")
    if not market_bytes.endswith(b"\n"):
        raise ValueError("simulation market fixture must use canonical JSONL")
    lines = market_bytes[:-1].split(b"\n")
    if len(lines) != row_count or any(not line for line in lines):
        raise ValueError("simulation market fixture row count is invalid")
    rows = tuple(
        _canonical_object(line, label="simulation market row") for line in lines
    )
    if any(set(row) != _MARKET_ROW_FIELDS for row in rows):
        raise ValueError("simulation market fixture row fields are invalid")
    for row in rows:
        _canonical_timestamp(row["open_time"], label="simulation market row")
        prices = {
            name: _decimal(row[name], label=f"simulation market {name}")
            for name in _MARKET_ROW_FIELDS - {"open_time"}
        }
        for name in ("open", "high", "low", "close"):
            _require_nautilus_price(
                prices[name], label=f"simulation market {name}"
            )
        _require_nautilus_quantity(
            prices["volume"], label="simulation market volume"
        )
        if (
            prices["open"] <= 0
            or prices["high"] <= 0
            or prices["low"] <= 0
            or prices["close"] <= 0
            or prices["volume"] < 0
            or prices["low"] > min(prices["open"], prices["close"])
            or prices["high"] < max(prices["open"], prices["close"])
        ):
            raise ValueError("simulation market price range is invalid")
    if not hmac.compare_digest(
        hashlib.sha256(_canonical_json_bytes(list(rows))).hexdigest(),
        str(catalog["canonical_rows_sha256"]),
    ):
        raise ValueError("simulation market fixture does not match catalog")
    return catalog, rows


def _validated_simulation_strategy(
    raw: bytes, *, start: datetime, end: datetime
) -> tuple[dict[str, object], Decimal]:
    strategy = _canonical_object(raw, label="simulation strategy")
    if (
        set(strategy) != _SIMULATION_STRATEGY_FIELDS
        or strategy.get("schema_version") != "nautilus-execution-target-v1"
        or type(strategy.get("positions")) is not list
        or len(strategy["positions"]) != 1
    ):
        raise ValueError("simulation strategy fields are invalid")
    effective_at = _canonical_timestamp(
        strategy["effective_at"], label="simulation strategy"
    )
    if not start <= effective_at < end:
        raise ValueError("simulation strategy is outside the command window")
    position = strategy["positions"][0]
    if (
        not isinstance(position, dict)
        or set(position) != _SIMULATION_POSITION_FIELDS
        or position.get("instrument") != _SIMULATION_INSTRUMENT
    ):
        raise ValueError("simulation strategy position is invalid")
    target = _decimal(position["target_quantity"], label="target quantity")
    _require_precision(target, 6, label="target quantity")
    _require_nautilus_quantity(target, label="target quantity")
    if target == 0:
        raise ValueError("execution simulation requires a non-zero target quantity")
    return strategy, target


def _validate_simulation_fixture_inputs(
    request: dict[str, object], artifacts: tuple[bytes, ...]
) -> dict[str, object]:
    """Validate the closed semantic grammar before any engine setup occurs."""

    if len(artifacts) != len(_SIMULATION_INPUT_ARTIFACT_NAMES):
        raise ValueError("simulation requires five hash-bound inputs")
    payload = request.get("payload")
    if (
        not isinstance(payload, dict)
        or payload.get("command_type") != "RunBacktestSimulation"
    ):
        raise ValueError("execution simulation requires RunBacktestSimulation")
    start = _canonical_timestamp(payload.get("start_time"), label="command start")
    end = _canonical_timestamp(payload.get("end_time"), label="command end")
    if end <= start:
        raise ValueError("simulation command window is invalid")
    configuration_raw, catalog_raw, strategy_raw, market_raw, scenario_raw = artifacts
    configuration = _canonical_object(
        configuration_raw, label="simulation engine configuration"
    )
    if configuration != _SIMULATION_CONFIGURATION:
        raise ValueError("simulation engine configuration fields are invalid")
    catalog, market_rows = _validated_simulation_catalog(catalog_raw, market_raw)
    strategy, target = _validated_simulation_strategy(
        strategy_raw, start=start, end=end
    )
    scenario = _canonical_object(scenario_raw, label="simulation scenario")
    if set(scenario) != _SIMULATION_SCENARIO_FIELDS:
        raise ValueError("simulation scenario fields are missing or unknown")
    scenario_id = scenario.get("scenario_id")
    if (
        scenario.get("schema_version") != "nautilus-execution-scenario-v1"
        or scenario_id not in _SIMULATION_SCENARIO_IDS
        or scenario.get("instrument") != _SIMULATION_INSTRUMENT
        or scenario.get("session_policy") != "explicit-open-flag-v1"
    ):
        raise ValueError("simulation scenario identity is invalid")
    if scenario.get("stop_take_profit_precedence") != "stop-first":
        raise ValueError("simulation stop/take-profit precedence is invalid")
    bindings = {
        "catalog_sha256": hashlib.sha256(catalog_raw).hexdigest(),
        "strategy_sha256": hashlib.sha256(strategy_raw).hexdigest(),
        "market_data_sha256": hashlib.sha256(market_raw).hexdigest(),
    }
    for name, expected in bindings.items():
        observed = scenario.get(name)
        if not isinstance(observed, str) or not hmac.compare_digest(observed, expected):
            label = name.removesuffix("_sha256").replace("_", " ")
            raise ValueError(f"simulation scenario {label} binding is invalid")
    fee_rate = _decimal(scenario["fee_rate"], label="fee rate")
    slippage_bps = _decimal(scenario["slippage_bps"], label="slippage bps")
    liquidity_limit = _decimal(
        scenario["liquidity_limit"], label="liquidity limit"
    )
    _require_nautilus_quantity(liquidity_limit, label="liquidity limit")
    if (
        not Decimal(0) <= fee_rate < Decimal(1)
        or not Decimal(0) <= slippage_bps < Decimal(10_000)
        or liquidity_limit < 0
    ):
        raise ValueError("simulation decimal parameter is outside its allowed range")
    threshold = scenario.get("stale_quote_threshold_seconds")
    if type(threshold) is not int or not 0 <= threshold <= 86_400:
        raise ValueError("simulation stale quote threshold is invalid")
    stop = (
        None
        if scenario["stop_price"] is None
        else _decimal(scenario["stop_price"], label="stop price", positive=True)
    )
    take_profit = (
        None
        if scenario["take_profit_price"] is None
        else _decimal(
            scenario["take_profit_price"],
            label="take-profit price",
            positive=True,
        )
    )
    if stop is not None:
        _require_precision(stop, 2, label="stop price")
        _require_nautilus_price(stop, label="stop price")
    if take_profit is not None:
        _require_precision(take_profit, 2, label="take-profit price")
        _require_nautilus_price(take_profit, label="take-profit price")
    raw_events = scenario.get("events")
    if type(raw_events) is not list or not 0 < len(raw_events) <= 32:
        raise ValueError("simulation scenario events are invalid")
    if len(raw_events) != len(market_rows):
        raise ValueError("simulation scenario events do not match market data")
    events: list[dict[str, object]] = []
    previous_time: datetime | None = None
    for index, (raw_event, row) in enumerate(
        zip(raw_events, market_rows, strict=True), start=1
    ):
        if not isinstance(raw_event, dict) or set(raw_event) != _SIMULATION_EVENT_FIELDS:
            raise ValueError("simulation event fields are missing or unknown")
        if raw_event.get("sequence") != index or type(raw_event.get("session_open")) is not bool:
            raise ValueError("simulation event sequence or session flag is invalid")
        event_time = _canonical_timestamp(
            raw_event["event_time"], label="simulation event"
        )
        quote_time = _canonical_timestamp(
            raw_event["quote_time"], label="simulation quote"
        )
        if not start <= event_time < end:
            raise ValueError("simulation event is outside the command window")
        if quote_time > event_time:
            raise ValueError("simulation quote occurs after its event")
        if previous_time is not None and event_time <= previous_time:
            raise ValueError("simulation events are not strictly ordered")
        previous_time = event_time
        decimals = {
            name: _decimal(raw_event[name], label=f"simulation event {name}")
            for name in ("ask", "bid", "close", "high", "low", "open", "volume")
        }
        for name in ("ask", "bid", "close", "high", "low", "open"):
            _require_precision(
                decimals[name], 2, label=f"simulation event {name}"
            )
            _require_nautilus_price(
                decimals[name], label=f"simulation event {name}"
            )
        _require_precision(
            decimals["volume"], 6, label="simulation event volume"
        )
        _require_nautilus_quantity(
            decimals["volume"], label="simulation event volume"
        )
        if (
            decimals["ask"] <= 0
            or decimals["bid"] <= 0
            or decimals["bid"] > decimals["ask"]
            or decimals["open"] <= 0
            or decimals["high"] <= 0
            or decimals["low"] <= 0
            or decimals["close"] <= 0
            or decimals["volume"] < 0
            or decimals["low"] > min(decimals["open"], decimals["close"])
            or decimals["high"] < max(decimals["open"], decimals["close"])
        ):
            raise ValueError("simulation event price or quantity range is invalid")
        expected_row = {
            "close": raw_event["close"],
            "high": raw_event["high"],
            "low": raw_event["low"],
            "open": raw_event["open"],
            "open_time": raw_event["event_time"],
            "volume": raw_event["volume"],
        }
        if expected_row != row:
            raise ValueError("simulation event does not match hash-bound market data")
        events.append(
            {
                **decimals,
                "event_time": event_time,
                "event_time_text": raw_event["event_time"],
                "quote_time": quote_time,
                "sequence": index,
                "session_open": raw_event["session_open"],
            }
        )
    if catalog["first_event_at"] != raw_events[0]["event_time"] or catalog[
        "last_event_at"
    ] != raw_events[-1]["event_time"]:
        raise ValueError("simulation catalog event boundary is invalid")
    _validate_executable_price_bounds(
        events=events,
        target=target,
        slippage_bps=slippage_bps,
        stop=stop,
        take_profit=take_profit,
    )
    _validate_scenario_semantic_preconditions(
        scenario_id=scenario_id,
        target=target,
        events=events,
        liquidity_limit=liquidity_limit,
        slippage_bps=slippage_bps,
        threshold=threshold,
        stop=stop,
        take_profit=take_profit,
    )
    return {
        "events": tuple(events),
        "fee_rate": fee_rate,
        "liquidity_limit": liquidity_limit,
        "scenario_id": scenario_id,
        "slippage_bps": slippage_bps,
        "stale_quote_threshold_seconds": threshold,
        "stop_price": stop,
        "stop_take_profit_precedence": "stop-first",
        "take_profit_price": take_profit,
        "target_quantity": target,
    }


def _validate_executable_price_bounds(
    *,
    events: list[dict[str, object]],
    target: Decimal,
    slippage_bps: Decimal,
    stop: Decimal | None,
    take_profit: Decimal | None,
) -> None:
    """Reject derived execution prices that Nautilus cannot represent."""

    rate = slippage_bps / Decimal(10_000)
    is_long = target > 0
    for event in events:
        quote = event["ask"] if is_long else event["bid"]
        assert isinstance(quote, Decimal)
        entry = quote * (Decimal(1) + rate if is_long else Decimal(1) - rate)
        _require_nautilus_price(entry, label="executable entry price")
    for trigger, label in (
        (stop, "executable stop exit price"),
        (take_profit, "executable take-profit exit price"),
    ):
        if trigger is not None:
            exit_price = trigger * (
                Decimal(1) - rate if is_long else Decimal(1) + rate
            )
            _require_nautilus_price(exit_price, label=label)


def _validate_scenario_semantic_preconditions(
    *,
    scenario_id: str,
    target: Decimal,
    events: list[dict[str, object]],
    liquidity_limit: Decimal,
    slippage_bps: Decimal,
    threshold: int,
    stop: Decimal | None,
    take_profit: Decimal | None,
) -> None:
    """Bind each declared scenario ID to one unambiguous execution cause."""

    def fresh(event: dict[str, object]) -> bool:
        return event["event_time"] - event["quote_time"] <= timedelta(
            seconds=threshold
        )

    def capacity(event: dict[str, object]) -> Decimal:
        volume = event["volume"]
        assert isinstance(volume, Decimal)
        return min(volume, liquidity_limit)

    def fail() -> NoReturn:
        raise ValueError(
            f"{scenario_id} scenario semantic precondition is invalid"
        )

    if scenario_id == "short-accounting":
        if target >= 0:
            fail()
    elif target <= 0:
        fail()

    if scenario_id != "same-bar-stop-take-profit" and (
        stop is not None or take_profit is not None
    ):
        fail()

    target_size = abs(target)
    if scenario_id in {"long-accounting", "short-accounting", "event-digest"}:
        if (
            not all(event["session_open"] is True and fresh(event) for event in events)
            or sum((capacity(event) for event in events), Decimal(0)) < target_size
        ):
            fail()
    elif scenario_id == "partial-fill":
        available = sum(
            (
                capacity(event)
                for event in events
                if event["session_open"] is True and fresh(event)
            ),
            Decimal(0),
        )
        if (
            not all(event["session_open"] is True and fresh(event) for event in events)
            or not Decimal(0) < available < target_size
        ):
            fail()
    elif scenario_id == "same-bar-stop-take-profit":
        first = events[0]
        ask = first["ask"]
        assert isinstance(ask, Decimal)
        entry = ask * (Decimal(1) + slippage_bps / Decimal(10_000))
        _require_nautilus_price(entry, label="executable entry price")
        if (
            len(events) != 1
            or first["session_open"] is not True
            or not fresh(first)
            or capacity(first) < target_size
            or stop is None
            or take_profit is None
            or not stop < entry < take_profit
            or not first["low"] <= stop <= first["high"]
            or not first["low"] <= take_profit <= first["high"]
        ):
            fail()
    elif scenario_id == "stale-quote":
        if liquidity_limit <= 0 or not all(
            event["session_open"] is True
            and not fresh(event)
            and capacity(event) > 0
            for event in events
        ):
            fail()
    elif scenario_id == "zero-liquidity":
        if liquidity_limit != 0 or not all(
            event["session_open"] is True
            and fresh(event)
            and event["volume"] > 0
            for event in events
        ):
            fail()
    elif scenario_id == "session-boundary":
        later = events[1:]
        if (
            len(events) < 2
            or events[0]["session_open"] is not False
            or liquidity_limit <= 0
            or not all(event["session_open"] is True and fresh(event) for event in later)
            or sum((capacity(event) for event in later), Decimal(0)) < target_size
        ):
            fail()


def validate_simulation_fixture_inputs(
    request: dict[str, object], artifacts: tuple[bytes, ...]
) -> dict[str, object]:
    """Validate semantics under an isolated, high-precision Decimal context."""

    with localcontext(_SIMULATION_DECIMAL_CONTEXT):
        return _validate_simulation_fixture_inputs(request, artifacts)


def _run_execution_simulation(fixture: dict[str, object]) -> dict[str, object]:
    """Run the bounded Decimal-only in-process fixture execution model."""

    events = fixture["events"]
    assert isinstance(events, tuple)
    target = fixture["target_quantity"]
    fee_rate = fixture["fee_rate"]
    slippage_bps = fixture["slippage_bps"]
    liquidity_limit = fixture["liquidity_limit"]
    threshold = fixture["stale_quote_threshold_seconds"]
    stop = fixture["stop_price"]
    take_profit = fixture["take_profit_price"]
    assert isinstance(target, Decimal)
    assert isinstance(fee_rate, Decimal)
    assert isinstance(slippage_bps, Decimal)
    assert isinstance(liquidity_limit, Decimal)
    assert type(threshold) is int
    assert stop is None or isinstance(stop, Decimal)
    assert take_profit is None or isinstance(take_profit, Decimal)
    side = Decimal(1) if target > 0 else Decimal(-1)
    remaining = target
    filled = Decimal(0)
    position = Decimal(0)
    entry_notional = Decimal(0)
    average_entry = Decimal(0)
    fees = Decimal(0)
    realized = Decimal(0)
    total_fills = 0
    total_orders = 0
    total_positions = 0
    event_records: list[dict[str, object]] = [
        {
            "event_type": "order-created",
            "quantity": _decimal_text(target),
            "sequence": 0,
        }
    ]
    rate = slippage_bps / Decimal(10_000)
    last_close = Decimal(0)
    for event in events:
        assert isinstance(event, dict)
        last_close = event["close"]
        assert isinstance(last_close, Decimal)
        sequence = event["sequence"]
        event_time_text = event["event_time_text"]
        if remaining == 0:
            break
        if event["session_open"] is not True:
            event_records.append(
                {
                    "event_type": "session-closed",
                    "market_sequence": sequence,
                    "sequence": len(event_records),
                }
            )
            continue
        age = event["event_time"] - event["quote_time"]
        if age > timedelta(seconds=threshold):
            event_records.append(
                {
                    "event_type": "quote-rejected",
                    "market_sequence": sequence,
                    "reason": "stale",
                    "sequence": len(event_records),
                }
            )
            continue
        volume = event["volume"]
        assert isinstance(volume, Decimal)
        available = min(volume, liquidity_limit, abs(remaining))
        _require_nautilus_quantity(available, label="fill quantity")
        if available <= 0:
            event_records.append(
                {
                    "event_type": "liquidity-rejected",
                    "market_sequence": sequence,
                    "reason": "zero",
                    "sequence": len(event_records),
                }
            )
            continue
        quote = event["ask"] if side > 0 else event["bid"]
        assert isinstance(quote, Decimal)
        price = quote * (Decimal(1) + rate if side > 0 else Decimal(1) - rate)
        _require_nautilus_price(price, label="executable entry price")
        quantity = side * available
        _require_nautilus_quantity(quantity, label="fill quantity")
        filled += quantity
        _require_nautilus_quantity(filled, label="fill quantity")
        remaining = target - filled
        _require_nautilus_quantity(remaining, label="remaining quantity")
        position += quantity
        _require_nautilus_quantity(position, label="position quantity")
        entry_notional += abs(quantity) * price
        average_entry = entry_notional / abs(filled)
        _require_nautilus_price(average_entry, label="average entry price")
        fees += abs(quantity) * price * fee_rate
        total_orders += 1
        total_fills += 1
        total_positions = 1
        event_records.append(
            {
                "event_time": event_time_text,
                "event_type": "fill",
                "price": _decimal_text(price),
                "quantity": _decimal_text(quantity),
                "sequence": len(event_records),
            }
        )
        stop_hit = stop is not None and (
            event["low"] <= stop if position > 0 else event["high"] >= stop
        )
        take_hit = take_profit is not None and (
            event["high"] >= take_profit
            if position > 0
            else event["low"] <= take_profit
        )
        exit_trigger = stop if stop_hit else take_profit if take_hit else None
        if exit_trigger is not None:
            total_orders += 1
            event_records.append(
                {
                    "event_type": "exit-order-created",
                    "reason": "stop" if stop_hit else "take-profit",
                    "sequence": len(event_records),
                }
            )
            exit_price = exit_trigger * (
                Decimal(1) - rate if position > 0 else Decimal(1) + rate
            )
            _require_nautilus_price(exit_price, label="executable exit price")
            close_quantity = -position
            _require_nautilus_quantity(close_quantity, label="fill quantity")
            realized += (exit_price - average_entry) * position
            fees += abs(close_quantity) * exit_price * fee_rate
            total_fills += 1
            event_records.append(
                {
                    "event_time": event_time_text,
                    "event_type": "fill",
                    "price": _decimal_text(exit_price),
                    "quantity": _decimal_text(close_quantity),
                    "sequence": len(event_records),
                }
            )
            position = Decimal(0)
            _require_nautilus_quantity(position, label="position quantity")
            event_records.append(
                {
                    "event_type": "position-closed",
                    "sequence": len(event_records),
                }
            )
            break
    unrealized = (
        (last_close - average_entry) * position if position != 0 else Decimal(0)
    )
    _require_nautilus_quantity(filled, label="fill quantity")
    _require_nautilus_quantity(remaining, label="remaining quantity")
    _require_nautilus_quantity(position, label="position quantity")
    _require_nautilus_price(average_entry, label="average entry price")
    canonical_result = _canonical_nautilus_result_record(
        strategy_events=event_records,
        iterations=len(events),
        order_count=total_orders,
        fill_count=total_fills,
        filled_quantity=filled,
        position_count=total_positions,
        position_quantity=position,
        average_entry_price=average_entry,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        account_balance_count=2 if total_fills > 0 or target < 0 else 1,
        commissions=fees,
    )
    return {
        "average_entry_price": _decimal_text(average_entry),
        "event_digest": hashlib.sha256(
            _canonical_json_bytes(canonical_result)
        ).hexdigest(),
        "fees": _decimal_text(fees),
        "filled_quantity": _decimal_text(filled),
        "iterations": len(events),
        "position_quantity": _decimal_text(position),
        "realized_pnl": _decimal_text(realized),
        "remaining_quantity": _decimal_text(remaining),
        "scenario_id": fixture["scenario_id"],
        "stop_take_profit_precedence": fixture[
            "stop_take_profit_precedence"
        ],
        "total_events": len(event_records),
        "total_fills": total_fills,
        "total_orders": total_orders,
        "total_positions": total_positions,
        "unrealized_pnl": _decimal_text(unrealized),
    }


def run_execution_simulation(fixture: dict[str, object]) -> dict[str, object]:
    """Run every derived operation under the fixed simulation Decimal context."""

    with localcontext(_SIMULATION_DECIMAL_CONTEXT):
        return _run_execution_simulation(fixture)


def _scenario_commission(
    *, fill_quantity: Decimal, fill_price: Decimal, fee_rate: Decimal
) -> Decimal:
    """Return the exact quote-currency commission for one actual fill."""

    return abs(fill_quantity) * fill_price * fee_rate


def _starting_balance_plan(target: Decimal) -> tuple[tuple[str, Decimal], ...]:
    """Return the reviewed cash inventory needed by the fixed scenario side.

    Long scenarios start with quote cash only. A short scenario starts with
    exactly its target amount of BTC inventory, so its SELL never relies on
    disabled cash borrowing and can produce actual balance/commission state.
    """

    if not isinstance(target, Decimal) or not target.is_finite() or target == 0:
        raise ValueError("simulation target cannot fund a cash account")
    balances = [("USDT", Decimal("1000000"))]
    if target < 0:
        balances.append(("BTC", abs(target)))
    return tuple(balances)


def _engine_decimal(value: object, *, label: str) -> Decimal:
    """Normalize one fixed-point value without stringifying arbitrary objects."""

    if isinstance(value, Decimal):
        parsed = value
    elif type(value) is int or isinstance(value, str) or type(value) is float:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Nautilus {label} is not numeric") from exc
    else:
        as_decimal = getattr(value, "as_decimal", None)
        if not callable(as_decimal):
            raise ValueError(f"Nautilus {label} shape is unsupported")
        parsed = as_decimal()
        if not isinstance(parsed, Decimal):
            raise ValueError(f"Nautilus {label} shape is unsupported")
    if not parsed.is_finite():
        raise ValueError(f"Nautilus {label} is not finite")
    return parsed


def _normalize_commissions(value: object) -> Decimal:
    """Sum only numeric values from ``CashAccount.commissions()``."""

    if type(value) is not dict:
        raise ValueError("Nautilus commission state must be a mapping")
    total = Decimal(0)
    for amount in value.values():
        total += _engine_decimal(amount, label="commission value")
    if not total.is_finite():
        raise ValueError("Nautilus commission total is not finite")
    return total


def _account_balance_count(account: object) -> int:
    """Validate and count the sealed account's explicit balance records."""

    balances = getattr(account, "balances", None)
    if not callable(balances):
        raise ValueError("Nautilus account balances API is unavailable")
    value = balances()
    if type(value) is dict:
        records = tuple(value.values())
    elif type(value) is list or type(value) is tuple:
        records = tuple(value)
    else:
        raise ValueError("Nautilus account balances shape is unsupported")
    for balance in records:
        total = _engine_decimal(
            getattr(balance, "total", None), label="account balance total"
        )
        locked = _engine_decimal(
            getattr(balance, "locked", None), label="account balance locked"
        )
        free = _engine_decimal(
            getattr(balance, "free", None), label="account balance free"
        )
        if total != locked + free:
            raise ValueError("Nautilus account balance arithmetic is invalid")
    return len(records)


def _json_native_strategy_events(value: object) -> list[dict[str, object]]:
    """Copy an ordered strategy event list while rejecting native engine values."""

    def copy_json_native(item: object) -> object:
        if item is None or type(item) in (bool, int, str):
            return item
        if type(item) is list:
            return [copy_json_native(child) for child in item]
        if type(item) is dict and all(isinstance(key, str) for key in item):
            return {key: copy_json_native(child) for key, child in item.items()}
        raise ValueError("Nautilus strategy event is not JSON-native")

    if type(value) is not list:
        raise ValueError("Nautilus strategy events must be an ordered JSON list")
    copied = copy_json_native(value)
    assert isinstance(copied, list)
    if not all(isinstance(item, dict) for item in copied):
        raise ValueError("Nautilus strategy event list contains a non-record")
    return copied


def _gross_realized_pnl(strategy_events: object) -> Decimal:
    """Calculate gross price PnL from actual strategy fill callbacks.

    Commissions remain exclusively in the result's ``fees`` field. This
    converts Nautilus's commission-inclusive position accounting into the
    reviewed gross-PnL contract without consulting the root oracle.
    """

    with localcontext(_SIMULATION_DECIMAL_CONTEXT):
        return _gross_realized_pnl_in_context(strategy_events)


def _gross_realized_pnl_in_context(strategy_events: object) -> Decimal:
    """Calculate gross fill-ledger PnL under the fixed precision-80 context."""

    position = Decimal(0)
    average_entry = Decimal(0)
    realized = Decimal(0)
    for record in _json_native_strategy_events(strategy_events):
        if record.get("event_type") != "fill":
            continue
        quantity = _engine_decimal(record.get("quantity"), label="fill quantity")
        price = _engine_decimal(record.get("price"), label="fill price")
        if quantity == 0 or price <= 0:
            raise ValueError("Nautilus fill evidence is invalid")
        same_side = position == 0 or (position > 0) == (quantity > 0)
        if same_side:
            new_position = position + quantity
            average_entry = (
                abs(position) * average_entry + abs(quantity) * price
            ) / abs(new_position)
            position = new_position
            continue
        closing_quantity = min(abs(position), abs(quantity))
        position_side = Decimal(1) if position > 0 else Decimal(-1)
        realized += (price - average_entry) * closing_quantity * position_side
        new_position = position + quantity
        if new_position == 0:
            average_entry = Decimal(0)
        elif (new_position > 0) != (position > 0):
            average_entry = price
        position = new_position
    if not realized.is_finite():
        raise ValueError("Nautilus gross realized PnL is not finite")
    return realized


def _result_counter(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"Nautilus {label} counter is invalid")
    return value


def _canonical_nautilus_result_record(
    *,
    strategy_events: object,
    iterations: object,
    order_count: object,
    fill_count: object,
    filled_quantity: Decimal,
    position_count: object,
    position_quantity: Decimal,
    average_entry_price: Decimal,
    realized_pnl: Decimal,
    unrealized_pnl: Decimal,
    account_balance_count: object,
    commissions: Decimal,
) -> dict[str, object]:
    """Build the deterministic JSON digest domain from actual engine state.

    Native ``BacktestResult`` objects, identifiers, run UUIDs, and engine clock
    fields are deliberately outside this record.
    """

    orders = _result_counter(order_count, label="order")
    fills = _result_counter(fill_count, label="fill")
    positions = _result_counter(position_count, label="position")
    balances = _result_counter(account_balance_count, label="account balance")
    if fills > orders:
        raise ValueError("Nautilus fill counter exceeds order counter")
    decimal_values = (
        filled_quantity,
        position_quantity,
        average_entry_price,
        realized_pnl,
        unrealized_pnl,
        commissions,
    )
    if not all(
        isinstance(value, Decimal) and value.is_finite()
        for value in decimal_values
    ):
        raise ValueError("Nautilus result decimal shape is unsupported")
    return {
        "account": {
            "balance_count": balances,
            "commissions": _decimal_text(commissions),
        },
        "engine": {"iterations": _result_counter(iterations, label="iteration")},
        "orders": {
            "count": orders,
            "filled_count": fills,
            "filled_quantity": _decimal_text(filled_quantity),
        },
        "positions": {
            "average_entry_price": _decimal_text(average_entry_price),
            "count": positions,
            "quantity": _decimal_text(position_quantity),
            "realized_pnl": _decimal_text(realized_pnl),
            "unrealized_pnl": _decimal_text(unrealized_pnl),
        },
        "schema_version": "nautilus-simulation-result-v1",
        "strategy_events": _json_native_strategy_events(strategy_events),
    }


def _build_target_portfolio_execution_plan(
    fixture: dict[str, object],
) -> tuple[dict[str, object], ...]:
    """Map the validated v2 semantics into exact engine order instructions."""

    with localcontext(_SIMULATION_DECIMAL_CONTEXT):
        return _build_target_portfolio_execution_plan_in_context(fixture)


def _build_target_portfolio_execution_plan_in_context(
    fixture: dict[str, object],
) -> tuple[dict[str, object], ...]:
    """Build the plan while the fixed precision-80 context is active."""

    events = fixture["events"]
    target = fixture["target_quantity"]
    liquidity_limit = fixture["liquidity_limit"]
    slippage_bps = fixture["slippage_bps"]
    threshold = fixture["stale_quote_threshold_seconds"]
    stop = fixture["stop_price"]
    take_profit = fixture["take_profit_price"]
    assert isinstance(events, tuple)
    assert isinstance(target, Decimal)
    assert isinstance(liquidity_limit, Decimal)
    assert isinstance(slippage_bps, Decimal)
    assert type(threshold) is int
    assert stop is None or isinstance(stop, Decimal)
    assert take_profit is None or isinstance(take_profit, Decimal)

    side = Decimal(1) if target > 0 else Decimal(-1)
    remaining = abs(target)
    rate = slippage_bps / Decimal(10_000)
    plan: list[dict[str, object]] = []
    for event in events:
        assert isinstance(event, dict)
        age = event["event_time"] - event["quote_time"]
        skip_reason: str | None = None
        if event["session_open"] is not True:
            skip_reason = "session-closed"
        elif age > timedelta(seconds=threshold):
            skip_reason = "stale-quote"
        volume = event["volume"]
        assert isinstance(volume, Decimal)
        available = min(volume, liquidity_limit, remaining)
        if skip_reason is None and available <= 0:
            skip_reason = "zero-liquidity"
        eligible = skip_reason is None and remaining > 0
        entry_price: Decimal | None = None
        exit_price: Decimal | None = None
        exit_trigger: Decimal | None = None
        exit_reason: str | None = None
        if eligible:
            quote = event["ask"] if side > 0 else event["bid"]
            assert isinstance(quote, Decimal)
            entry_price = quote * (
                Decimal(1) + rate if side > 0 else Decimal(1) - rate
            )
            _require_nautilus_price(entry_price, label="executable entry price")
            remaining -= available
            stop_hit = stop is not None and (
                event["low"] <= stop if side > 0 else event["high"] >= stop
            )
            take_hit = take_profit is not None and (
                event["high"] >= take_profit
                if side > 0
                else event["low"] <= take_profit
            )
            if stop_hit:
                exit_reason, exit_trigger = "stop", stop
            elif take_hit:
                exit_reason, exit_trigger = "take-profit", take_profit
            if exit_trigger is not None:
                exit_price = exit_trigger * (
                    Decimal(1) - rate if side > 0 else Decimal(1) + rate
                )
                _require_nautilus_price(exit_price, label="executable exit price")
        plan.append(
            {
                "eligible": eligible,
                "entry_price": (
                    None if entry_price is None else _decimal_text(entry_price)
                ),
                "event_time": event["event_time_text"],
                "exit_price": (
                    None if exit_price is None else _decimal_text(exit_price)
                ),
                "exit_reason": exit_reason,
                "exit_trigger": (
                    None if exit_trigger is None else _decimal_text(exit_trigger)
                ),
                "fill_quantity": _decimal_text(
                    available if eligible else Decimal(0)
                ),
                "market_sequence": event["sequence"],
                "quote_age_seconds": int(age.total_seconds()),
                "session_open": event["session_open"],
                "skip_reason": skip_reason,
            }
        )
    return tuple(plan)


def _run_nautilus_simulation_fixture(
    fixture: dict[str, object],
) -> dict[str, object]:
    """Ingest the finite semantic feed in the sealed Nautilus runtime.

    This is intentionally distinct from the root Decimal oracle.  The returned
    values come from the fixed strategy's real Nautilus orders/fills/cache and
    account result, never from ``_run_execution_simulation``.
    """

    wheels_root = Path("/engine/wheels")
    extraction_root = Path("/tmp/nautilus-simulation-wheels")
    extraction_root.mkdir(mode=0o700)
    wheels = tuple(sorted(wheels_root.glob("*.whl"), key=lambda path: path.name))
    if not wheels:
        raise ValueError("Nautilus runtime wheel closure is missing")
    extracted_roots: list[Path] = []
    for wheel in wheels:
        destination = extraction_root / hashlib.sha256(
            wheel.name.encode("ascii")
        ).hexdigest()
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
                        if (
                            member.is_dir()
                            and member.filename
                            and ".." not in relative.parts
                        ):
                            continue
                        raise ValueError("Nautilus wheel has an unsafe member")
                archive.extractall(destination)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError("Nautilus runtime wheel is unreadable") from exc
        extracted_roots.append(destination)
    with _sealed_wheel_import_scope(tuple(extracted_roots)):
        return _run_nautilus_simulation_fixture_loaded(fixture)


def _run_nautilus_simulation_fixture_loaded(
    fixture: dict[str, object],
) -> dict[str, object]:
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import FeeModel
    from nautilus_trader.common.config import LoggingConfig
    from nautilus_trader.config import BacktestEngineConfig
    from nautilus_trader.model.currencies import BTC, USDT
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Money, Price, Quantity
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    TargetPortfolioStrategy, TargetPortfolioStrategyConfig = (
        _load_target_portfolio_strategy()
    )

    class ScenarioFeeModel(FeeModel):
        """Charge the validated scenario rate on every engine fill."""

        def __init__(self, fee_rate: Decimal) -> None:
            self._fee_rate = fee_rate

        def get_commission(self, order, fill_qty, fill_px, instrument):
            del order
            commission = _scenario_commission(
                fill_quantity=Decimal(str(fill_qty)),
                fill_price=Decimal(str(fill_px)),
                fee_rate=self._fee_rate,
            )
            return Money(commission, instrument.quote_currency)

    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True),
            run_analysis=False,
        )
    )
    try:
        instrument = TestInstrumentProvider.btcusdt_binance()
        if str(instrument.id) != "BTCUSDT.BINANCE":
            raise ValueError("Nautilus simulation fixture instrument is incompatible")
        target = fixture["target_quantity"]
        assert isinstance(target, Decimal)
        currencies = {"BTC": BTC, "USDT": USDT}
        engine.add_venue(
            venue=Venue("BINANCE"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[
                Money(amount, currencies[currency])
                for currency, amount in _starting_balance_plan(target)
            ],
            fee_model=ScenarioFeeModel(fixture["fee_rate"]),
        )
        engine.add_instrument(instrument)
        execution_plan = _build_target_portfolio_execution_plan(fixture)
        bar_type = BarType.from_str("BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL")
        strategy = TargetPortfolioStrategy(
            TargetPortfolioStrategyConfig(
                instrument_id=instrument.id,
                bar_type=bar_type,
                target_quantity=_decimal_text(target),
                scenario_id=str(fixture["scenario_id"]),
                execution_plan=execution_plan,
                event_semantics=tuple(
                    {
                        "ask": _decimal_text(event["ask"]),
                        "bid": _decimal_text(event["bid"]),
                        "high": _decimal_text(event["high"]),
                        "low": _decimal_text(event["low"]),
                        "quote_age_seconds": int(
                            (
                                event["event_time"] - event["quote_time"]
                            ).total_seconds()
                        ),
                        "session_open": event["session_open"],
                        "volume": _decimal_text(event["volume"]),
                    }
                    for event in fixture["events"]
                ),
                fee_rate=_decimal_text(fixture["fee_rate"]),
                slippage_bps=_decimal_text(fixture["slippage_bps"]),
                liquidity_limit=_decimal_text(fixture["liquidity_limit"]),
                stale_quote_threshold_seconds=int(
                    fixture["stale_quote_threshold_seconds"]
                ),
                stop_price=(
                    None
                    if fixture["stop_price"] is None
                    else _decimal_text(fixture["stop_price"])
                ),
                take_profit_price=(
                    None
                    if fixture["take_profit_price"] is None
                    else _decimal_text(fixture["take_profit_price"])
                ),
                stop_take_profit_precedence=str(
                    fixture["stop_take_profit_precedence"]
                ),
            )
        )
        engine.add_strategy(strategy)
        bars = []
        events = fixture["events"]
        assert isinstance(events, tuple)
        for event in events:
            assert isinstance(event, dict)
            timestamp = event["event_time"]
            assert isinstance(timestamp, datetime)
            timestamp_ns = (
                int(timestamp.timestamp()) * 1_000_000_000
                + timestamp.microsecond * 1_000
            )
            bars.append(
                Bar(
                    bar_type,
                    Price.from_str(
                        _nautilus_price_text(
                            event["open"], 2, label="simulation event open"
                        )
                    ),
                    Price.from_str(
                        _nautilus_price_text(
                            event["high"], 2, label="simulation event high"
                        )
                    ),
                    Price.from_str(
                        _nautilus_price_text(
                            event["low"], 2, label="simulation event low"
                        )
                    ),
                    Price.from_str(
                        _nautilus_price_text(
                            event["close"], 2, label="simulation event close"
                        )
                    ),
                    Quantity.from_str(
                        _nautilus_quantity_text(
                            event["volume"], 6, label="simulation event volume"
                        )
                    ),
                    timestamp_ns,
                    timestamp_ns,
                )
            )
        engine.add_data(bars)
        engine.run()
        engine_result = engine.get_result()
        iterations = _result_counter(
            getattr(engine_result, "iterations", None), label="iteration"
        )
        if iterations != len(events):
            raise ValueError("Nautilus simulation fixture iteration count changed")
        # The cache/account accesses are intentional: this result is evidence
        # of the strategy-driven Nautilus execution state, not a parallel
        # arithmetic calculation.  Qualification compares it to the root
        # oracle only after a separately reviewed sealed runtime is published.
        orders = tuple(engine.cache.orders())
        positions = tuple(engine.cache.positions())
        account = engine.cache.account_for_venue(Venue("BINANCE"))
        if account is None:
            raise ValueError("Nautilus simulation account state is unavailable")
        if len(positions) > 1:
            raise ValueError("Nautilus simulation produced more than one position")
        if strategy.rejected:
            raise ValueError("Nautilus simulation strategy order was rejected")
        position = positions[0] if positions else None
        quantity = (
            _engine_decimal(position.quantity, label="position quantity")
            if position is not None
            else Decimal(0)
        )
        if target < 0:
            quantity = -quantity
        average = (
            _engine_decimal(position.avg_px_open, label="average entry price")
            if position is not None
            else Decimal(0)
        )
        strategy_events = strategy.semantic_events
        realized = _gross_realized_pnl(strategy_events)
        if position is None or quantity == 0:
            unrealized = Decimal(0)
        else:
            unrealized_value = position.unrealized_pnl
            if callable(unrealized_value):
                if not bars:
                    raise ValueError("Nautilus simulation has no final bar")
                unrealized_value = unrealized_value(bars[-1].close)
            unrealized = _engine_decimal(unrealized_value, label="unrealized PnL")
        commissions = getattr(account, "commissions", None)
        if not callable(commissions):
            raise ValueError("Nautilus account commissions API is unavailable")
        fees = _normalize_commissions(commissions())
        filled = (
            Decimal(0)
            if strategy.entry_filled_quantity is None
            else _engine_decimal(
                strategy.entry_filled_quantity, label="entry filled quantity"
            )
        )
        if target < 0:
            filled = -filled
        total_fills = sum(
            _engine_decimal(order.filled_qty, label="order filled quantity") > 0
            for order in orders
        )
        canonical_result = _canonical_nautilus_result_record(
            strategy_events=strategy_events,
            iterations=iterations,
            order_count=len(orders),
            fill_count=total_fills,
            filled_quantity=filled,
            position_count=len(positions),
            position_quantity=quantity,
            average_entry_price=average,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            account_balance_count=_account_balance_count(account),
            commissions=fees,
        )
        return {
            "average_entry_price": _decimal_text(average),
            "event_digest": hashlib.sha256(
                _canonical_json_bytes(canonical_result)
            ).hexdigest(),
            "fees": _decimal_text(fees),
            "filled_quantity": _decimal_text(filled),
            "iterations": iterations,
            "position_quantity": _decimal_text(quantity),
            "realized_pnl": _decimal_text(realized),
            "remaining_quantity": _decimal_text(target - filled),
            "scenario_id": fixture["scenario_id"],
            "stop_take_profit_precedence": fixture["stop_take_profit_precedence"],
            "total_events": len(strategy_events),
            "total_fills": total_fills,
            "total_orders": len(orders),
            "total_positions": len(positions),
            "unrealized_pnl": _decimal_text(unrealized),
        }
    finally:
        engine.dispose()


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
    extracted_roots: list[Path] = []
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
        extracted_roots.append(destination)
    with _sealed_wheel_import_scope(tuple(extracted_roots)):
        return _run_nautilus_loaded(fixture)


def _run_nautilus_loaded(
    fixture: tuple[dict[str, object], tuple[dict[str, object], ...]]
) -> tuple[int, int, int, int]:
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


def _simulation_event(
    request: dict[str, object],
    artifacts: tuple[bytes, ...],
    result: dict[str, object],
) -> dict[str, object]:
    if len(artifacts) != len(_SIMULATION_INPUT_ARTIFACT_NAMES):
        raise ValueError("simulation requires five hash-bound inputs")
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("simulation request payload is invalid")
    scenario = payload.get("simulation_scenario")
    _artifact(scenario)
    assert isinstance(scenario, dict)
    event_payload = {
        "event_type": _SIMULATION_EVENT_TYPE,
        "family": "ENGINE_LIFECYCLE",
        "attributes": [
            {
                "name": "input_artifacts_sha256",
                "value": _input_artifacts_sha256(
                    artifacts, profile="execution-simulation"
                ),
            },
            {"name": "scenario_digest", "value": scenario["sha256"]},
            *[
                {"name": name, "value": result[name]}
                for name in (
                    "scenario_id",
                    "event_digest",
                    "iterations",
                    "total_events",
                    "total_orders",
                    "total_fills",
                    "total_positions",
                    "filled_quantity",
                    "remaining_quantity",
                    "position_quantity",
                    "average_entry_price",
                    "fees",
                    "realized_pnl",
                    "unrealized_pnl",
                    "stop_take_profit_precedence",
                )
            ],
        ],
    }
    return {
        "message_id": str(
            uuid5(UUID(str(request["message_id"])), _SIMULATION_EVENT_TYPE)
        ),
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
        "payload_digest": hashlib.sha256(
            _canonical_json_bytes(event_payload)
        ).hexdigest(),
        "payload": event_payload,
    }


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 2:
        profile = "zero-order"
        request_argument, sidecar_argument = arguments
    elif arguments[:2] == ["--profile", "execution-simulation"] and len(arguments) == 4:
        profile = "execution-simulation"
        request_argument, sidecar_argument = arguments[2:]
    else:
        _fail("expected the attested launcher profile and request inputs")
    try:
        request = validated_request(
            Path(request_argument), Path(sidecar_argument), profile=profile
        )
        artifacts = validated_input_artifacts(request, profile=profile)
        if profile == "zero-order":
            if len(artifacts) != 4:
                raise ValueError("zero-order requires four hash-bound inputs")
            fixture = validate_zero_order_fixture_inputs(artifacts)  # type: ignore[arg-type]
            event = _event(
                request, artifacts, _run_nautilus(fixture)  # type: ignore[arg-type]
            )
        else:
            fixture = validate_simulation_fixture_inputs(request, artifacts)
            event = _simulation_event(
                request,
                artifacts,
                _run_nautilus_simulation_fixture(fixture),
            )
        print(_canonical_json_bytes(event).decode("utf-8"))
    except (ImportError, OSError, ValueError) as exc:
        _fail(str(exc))


if __name__ == "__main__":
    main()
