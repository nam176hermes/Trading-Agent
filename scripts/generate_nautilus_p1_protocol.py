#!/usr/bin/env python3
"""Generate the stdlib-only P1 Nautilus wire grammar."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.engine_contracts import canonical_json_bytes
from packages.nautilus_runtime_contracts import (
    P1EngineConfigurationV1,
    P1InstrumentCatalogV1,
    P1MarketDataManifestV1,
    P1TargetScheduleV1,
)
from packages.nautilus_runtime_contracts.events import P1_EVENT_ADAPTER, P1_EVENT_MODELS
from packages.nautilus_runtime_contracts.versions import (
    MAX_ENGINE_CONFIGURATION_BYTES,
    MAX_INSTRUMENT_CATALOG_BYTES,
    MAX_MARKET_DATA_MANIFEST_BYTES,
    MAX_TARGET_SCHEDULE_BYTES,
    P1_ENGINE_CONFIGURATION_SCHEMA,
    P1_INSTRUMENT_CATALOG_SCHEMA,
    P1_MARKET_DATA_MANIFEST_SCHEMA,
    P1_TARGET_SCHEDULE_SCHEMA,
)


ARTIFACT_MODELS = {
    "engine_configuration": P1EngineConfigurationV1,
    "instrument_catalog": P1InstrumentCatalogV1,
    "market_data_manifest": P1MarketDataManifestV1,
    "target_schedule": P1TargetScheduleV1,
}
CONTRACT_FIXTURES = {
    "engine_configuration": "engine-configuration.json",
    "instrument_catalog": "instrument-catalog.json",
    "market_data_manifest": "market-data-manifest.json",
    "target_schedule": "target-schedule.json",
}
ARTIFACT_SCHEMAS = {
    "engine_configuration": P1_ENGINE_CONFIGURATION_SCHEMA,
    "instrument_catalog": P1_INSTRUMENT_CATALOG_SCHEMA,
    "market_data_manifest": P1_MARKET_DATA_MANIFEST_SCHEMA,
    "target_schedule": P1_TARGET_SCHEDULE_SCHEMA,
}
MAX_DOCUMENT_BYTES = {
    "engine_configuration": MAX_ENGINE_CONFIGURATION_BYTES,
    "instrument_catalog": MAX_INSTRUMENT_CATALOG_BYTES,
    "market_data_manifest": MAX_MARKET_DATA_MANIFEST_BYTES,
    "target_schedule": MAX_TARGET_SCHEDULE_BYTES,
}
SUPPORTED_SEMANTIC_VALIDATORS = {
    "engine_configuration": ("_validate_amounts",),
    "instrument_catalog": ("_validate_increments",),
    "market_data_manifest": ("_validate_window",),
    "target_schedule": ("_validate_schedule",),
    "RunStarted": (),
    "TargetAccepted": ("_long_or_flat",),
    "TargetQuantityPlanned": ("_non_negative_quantity",),
    "OrderSubmitted": ("_positive_quantity",),
    "Fill": ("_valid_fill_amounts",),
    "PositionObserved": ("_long_or_flat",),
    "AccountObserved": ("_valid_account",),
    "RunCompleted": ("_valid_final_account",),
}


def _json(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode()


def _grammar() -> tuple[dict[str, tuple[str, ...]], dict[str, dict[str, object]]]:
    fields = {name: tuple(model.model_fields) for name, model in ARTIFACT_MODELS.items()}
    fields.update(
        {
            model.model_json_schema(mode="validation")["properties"]["event_type"]["const"]: tuple(model.model_fields)
            for model in P1_EVENT_MODELS
        }
    )
    constants: dict[str, dict[str, object]] = {}
    for name, model in (
        tuple(ARTIFACT_MODELS.items())
        + tuple(
            (
                model.model_json_schema(mode="validation")["properties"]["event_type"]["const"],
                model,
            )
            for model in P1_EVENT_MODELS
        )
    ):
        properties = model.model_json_schema(mode="validation")["properties"]
        constants[name] = {
            field_name: field_schema["const"]
            for field_name, field_schema in properties.items()
            if "const" in field_schema
        }
    return fields, constants


def _schemas() -> dict[str, dict[str, object]]:
    schemas = {
        name: model.model_json_schema(mode="validation")
        for name, model in ARTIFACT_MODELS.items()
    }
    schemas.update(
        {
            model.model_json_schema(mode="validation")["properties"]["event_type"]["const"]: model.model_json_schema(mode="validation")
            for model in P1_EVENT_MODELS
        }
    )
    return schemas


def _semantic_authority() -> dict[str, dict[str, object]]:
    entries = tuple(ARTIFACT_MODELS.items()) + tuple(
        (
            model.model_json_schema(mode="validation")["properties"]["event_type"]["const"],
            model,
        )
        for model in P1_EVENT_MODELS
    )
    authority: dict[str, dict[str, object]] = {}
    for kind, model in entries:
        names = tuple(model.__pydantic_decorators__.model_validators)
        if names != SUPPORTED_SEMANTIC_VALIDATORS[kind]:
            raise ValueError(f"unmapped semantic validator: {kind}")
        source = "".join(inspect.getsource(model.__dict__[name]) for name in names)
        authority[kind] = {
            "names": names,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        }
    return authority


def _module_bytes() -> bytes:
    fields, constants = _grammar()
    schemas = _schemas()
    semantic_authority = _semantic_authority()
    source = f'''# GENERATED by scripts/generate_nautilus_p1_protocol.py. DO NOT EDIT.
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

P1_EVENT_SCHEMA = "nautilus-p1-event-stream-v1"
ARTIFACT_SCHEMAS = {ARTIFACT_SCHEMAS!r}
MAX_DOCUMENT_BYTES = {MAX_DOCUMENT_BYTES!r}
MAX_EVENT_BYTES = max(MAX_DOCUMENT_BYTES.values())
SEMANTIC_VALIDATOR_AUTHORITY = {semantic_authority!r}
DECIMAL_PATTERN = r"^(?:0|-?[1-9]\\d*|-?(?:0|[1-9]\\d*)\\.\\d*[1-9])$"
TIMESTAMP_PATTERN = r"^\\d{{4}}-\\d{{2}}-\\d{{2}}T\\d{{2}}:\\d{{2}}:\\d{{2}}(?:\\.\\d{{1,6}})?Z$"
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{{0,127}}$"
SHA256_PATTERN = r"^[0-9a-f]{{64}}$"
DOCUMENT_FIELDS = {fields!r}
CONSTANT_FIELDS = {constants!r}
DOCUMENT_SCHEMAS = {schemas!r}


class ProtocolValidationError(ValueError):
    code = "E_PROTOCOL"


def _reject_float(_: str) -> object:
    raise ProtocolValidationError("floating JSON number")


def canonical_json_bytes(value: object) -> bytes:
    def walk(item: object) -> None:
        if isinstance(item, float):
            raise ProtocolValidationError("floating value")
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise ProtocolValidationError("non-string key")
            for child in item.values():
                walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)
        elif item is not None and not isinstance(item, (str, bool, int)):
            raise ProtocolValidationError("unsupported value")
    try:
        walk(value)
        return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    except (RecursionError, UnicodeEncodeError, ValueError) as exc:
        raise ProtocolValidationError("invalid Unicode value") from exc


def _load_canonical_json(raw: bytes) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {{}}
        for key, item in items:
            if key in value:
                raise ProtocolValidationError("duplicate key")
            value[key] = item
        return value
    if not raw.endswith(b"\\n") or raw.endswith(b"\\n\\n"):
        raise ProtocolValidationError("trailing newline")
    try:
        value = json.loads(raw[:-1], object_pairs_hook=pairs, parse_float=_reject_float, parse_constant=_reject_float)
    except (RecursionError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolValidationError("invalid JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) + b"\\n" != raw:
        raise ProtocolValidationError("noncanonical JSON")
    return value


def _validate(value: object, schema: dict[str, object], root: dict[str, object]) -> None:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        if not reference.startswith("#/$defs/"):
            raise ProtocolValidationError("external schema reference")
        target = root.get("$defs", {{}}).get(reference.removeprefix("#/$defs/"))
        if not isinstance(target, dict):
            raise ProtocolValidationError("missing schema reference")
        _validate(value, target, root)
        return
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(alternatives, list):
        matches = 0
        for alternative in alternatives:
            try:
                _validate(value, alternative, root)
            except ProtocolValidationError:
                continue
            matches += 1
        if matches != 1:
            raise ProtocolValidationError("schema union")
        return
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        raise ProtocolValidationError("constant field")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise ProtocolValidationError("enum field")
    expected_type = schema.get("type")
    type_checks = {{
        "object": lambda item: type(item) is dict,
        "array": lambda item: type(item) is list,
        "string": lambda item: type(item) is str,
        "integer": lambda item: type(item) is int,
        "boolean": lambda item: type(item) is bool,
        "null": lambda item: item is None,
    }}
    if isinstance(expected_type, str) and expected_type in type_checks and not type_checks[expected_type](value):
        raise ProtocolValidationError("field type")
    if type(value) is dict:
        properties = schema.get("properties", {{}})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ProtocolValidationError("object schema")
        if not set(required) <= set(value):
            raise ProtocolValidationError("missing field")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ProtocolValidationError("unknown field")
        for name, item in value.items():
            child = properties.get(name)
            if isinstance(child, dict):
                _validate(item, child, root)
    elif type(value) is list:
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise ProtocolValidationError("array length")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in value:
                _validate(item, item_schema, root)
    elif type(value) is str:
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
            raise ProtocolValidationError("string length")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, value, flags=re.ASCII) is None:
            raise ProtocolValidationError("string pattern")
        if schema.get("format") == "uuid":
            try:
                if str(uuid.UUID(value)) != value:
                    raise ValueError
            except ValueError as exc:
                raise ProtocolValidationError("UUID") from exc
        if schema.get("format") == "date-time":
            _timestamp(value)
    elif type(value) is int:
        if "minimum" in schema and value < schema["minimum"]:
            raise ProtocolValidationError("integer minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ProtocolValidationError("integer maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ProtocolValidationError("integer exclusive minimum")


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ProtocolValidationError("object value")
    return value


def _array(value: object) -> list[object]:
    if type(value) is not list:
        raise ProtocolValidationError("array value")
    return value


def _text(value: object) -> str:
    if type(value) is not str:
        raise ProtocolValidationError("string value")
    return value


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(_text(value))
    except InvalidOperation as exc:
        raise ProtocolValidationError("decimal value") from exc


def _timestamp(value: object) -> datetime:
    try:
        text = _text(value)
        parsed = datetime.fromisoformat(text.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ProtocolValidationError("timestamp value") from exc
    if parsed.isoformat().replace("+00:00", "Z") != text:
        raise ProtocolValidationError("noncanonical timestamp")
    return parsed


def _unique(values: list[object]) -> bool:
    return len(values) == len(set(_text(value) for value in values))


def _validate_semantics(kind: str, value: dict[str, object]) -> None:
    if kind == "engine_configuration":
        if value.get("starting_balance") != "1000000" or value.get("fee_rate") != "0.001":
            raise ProtocolValidationError("engine configuration amount")
    elif kind == "instrument_catalog":
        if any(
            _decimal(value.get(name)) <= 0
            for name in ("tick_size", "step_size", "min_quantity", "min_notional")
        ):
            raise ProtocolValidationError("instrument increment")
    elif kind == "market_data_manifest":
        if _timestamp(value.get("last_timestamp")) < _timestamp(value.get("first_timestamp")):
            raise ProtocolValidationError("market data window")
    elif kind == "target_schedule":
        targets = _array(value.get("targets"))
        target_ids: list[object] = []
        effective_times: list[object] = []
        for raw_target in targets:
            target = _object(raw_target)
            target_ids.append(target.get("target_id"))
            effective_times.append(target.get("effective_at"))
            signals = _array(target.get("source_signal_ids"))
            positions = _array(target.get("positions"))
            if not _unique(signals) or len(positions) != 1:
                raise ProtocolValidationError("target cardinality")
            position = _object(positions[0])
            instrument = _object(position.get("instrument"))
            if (
                instrument.get("product_type") != "crypto_spot"
                or instrument.get("symbol") != "BTCUSDT"
                or instrument.get("venue") != "BINANCE"
                or not Decimal(0) <= _decimal(position.get("target_weight")) <= Decimal(1)
            ):
                raise ProtocolValidationError("target position")
        if (
            not _unique(target_ids)
            or not _unique(effective_times)
            or [_timestamp(item) for item in effective_times]
            != sorted(_timestamp(item) for item in effective_times)
        ):
            raise ProtocolValidationError("target schedule")
    elif kind in {{"TargetAccepted", "OrderSubmitted"}}:
        if not _unique(_array(value.get("source_signal_ids"))):
            raise ProtocolValidationError("duplicate signal")
        if kind == "TargetAccepted" and not Decimal(0) <= _decimal(value.get("target_weight")) <= Decimal(1):
            raise ProtocolValidationError("target weight")
        if kind == "OrderSubmitted" and _decimal(value.get("quantity")) <= 0:
            raise ProtocolValidationError("order quantity")
    elif kind == "TargetQuantityPlanned" and _decimal(value.get("quantity")) < 0:
        raise ProtocolValidationError("planned quantity")
    elif kind == "Fill" and (
        _decimal(value.get("quantity")) <= 0
        or _decimal(value.get("price")) <= 0
        or _decimal(value.get("fee")) < 0
    ):
        raise ProtocolValidationError("fill amount")
    elif kind == "PositionObserved" and (
        _decimal(value.get("quantity")) < 0
        or _decimal(value.get("average_entry_price")) < 0
    ):
        raise ProtocolValidationError("position amount")
    elif kind == "AccountObserved" and (
        _decimal(value.get("cash_balance")) < 0 or _decimal(value.get("fees")) < 0
    ):
        raise ProtocolValidationError("account amount")
    elif kind == "RunCompleted" and (
        _decimal(value.get("final_cash")) < 0
        or _decimal(value.get("final_position")) < 0
        or _decimal(value.get("fees")) < 0
    ):
        raise ProtocolValidationError("completion amount")


def validate_document(kind: str, value: dict[str, object]) -> None:
    schema = DOCUMENT_SCHEMAS.get(kind)
    if schema is None:
        raise ProtocolValidationError("document kind")
    canonical_json_bytes(value)
    _validate(value, schema, schema)
    _validate_semantics(kind, value)


def load_document(kind: str, raw: bytes) -> dict[str, object]:
    maximum = MAX_DOCUMENT_BYTES.get(kind)
    if maximum is None or len(raw) > maximum:
        raise ProtocolValidationError("document size")
    value = _load_canonical_json(raw)
    validate_document(kind, value)
    return value


def load_event(raw: bytes) -> dict[str, object]:
    if len(raw) > MAX_EVENT_BYTES:
        raise ProtocolValidationError("event size")
    value = _load_canonical_json(raw)
    kind = value.get("event_type")
    if not isinstance(kind, str):
        raise ProtocolValidationError("event type")
    validate_document(kind, value)
    return value
'''
    return source.encode()


def _outputs() -> dict[Path, bytes]:
    outputs = {Path("engines/nautilus/runtime_v1/generated_protocol.py"): _module_bytes()}
    for name, model in ARTIFACT_MODELS.items():
        outputs[Path(f"schemas/nautilus-p1-{name.replace('_', '-')}-v1.schema.json")] = _json(
            model.model_json_schema(mode="validation")
        )
    outputs[Path("schemas/nautilus-p1-event-stream-v1.schema.json")] = _json(
        P1_EVENT_ADAPTER.json_schema(mode="validation")
    )
    fixture_root = ROOT / "tests/fixtures/p1_nautilus/contracts"
    for kind, filename in CONTRACT_FIXTURES.items():
        raw = (fixture_root / filename).read_bytes()
        outputs[Path(f"tests/fixtures/p1_nautilus/golden/positive/{kind}.json")] = raw
        value = json.loads(raw)
        if kind == "engine_configuration":
            value["starting_balance"] = "1"
        elif kind == "instrument_catalog":
            value["tick_size"] = "0"
        elif kind == "market_data_manifest":
            value["first_timestamp"] = "2026-08-05T12:02:00Z"
        else:
            value["targets"][0]["positions"][0]["target_weight"] = "-1"
        outputs[Path(f"tests/fixtures/p1_nautilus/golden/negative/{kind}.json")] = canonical_json_bytes(value) + b"\n"
    event_raw = (fixture_root / "event-stream.jsonl").read_bytes()
    event_lines = event_raw.splitlines()
    if not event_raw.endswith(b"\n") or not event_lines:
        raise ValueError("event stream fixture is not canonical JSONL")
    for line in event_lines:
        P1_EVENT_ADAPTER.validate_json(line)
    outputs[Path("tests/fixtures/p1_nautilus/golden/positive/event_stream.jsonl")] = event_raw
    invalid_event = json.loads(event_lines[1])
    invalid_event["target_weight"] = "2"
    outputs[Path("tests/fixtures/p1_nautilus/golden/negative/event_stream.jsonl")] = canonical_json_bytes(invalid_event) + b"\n"
    return outputs


def generate(output_root: Path, *, check: bool) -> int:
    stale: list[str] = []
    for relative, raw in _outputs().items():
        path = output_root / relative
        if check:
            if not path.is_file() or path.read_bytes() != raw:
                stale.append(relative.as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    if stale:
        print("stale P1 Nautilus generated files: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    return generate(args.output_root.resolve(), check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
