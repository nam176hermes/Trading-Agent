#!/usr/bin/env python3
"""Run one sealed campaign member through the comparison-only legacy engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    Bar,
    Signal,
    Strategy,
)


SCENARIO_IDS = (
    "long-accounting",
    "short-accounting",
    "partial-fill",
    "same-bar-stop-take-profit",
    "stale-quote",
    "zero-liquidity",
    "session-boundary",
    "event-digest",
)
ARTIFACTS = (
    ("engine-configuration.json", "engine_configuration_sha256"),
    ("instrument-catalog.json", "instrument_catalog_sha256"),
    ("strategy-configuration.json", "strategy_configuration_sha256"),
    ("market-data.json", "market_data_sha256"),
    ("simulation-scenario.json", "simulation_scenario_sha256"),
)
_ROOT = Path(__file__).resolve().parent
_CHECKOUT = _ROOT.parents[1]
_MANIFEST_FIELDS = {
    "paper_scenario_id",
    "scenarios",
    "schema_version",
    "strategy_source_sha256",
}
_SCENARIO_FIELDS = {"scenario_id", *(field for _name, field in ARTIFACTS)}
_MAX_BYTES = 8 * 1024 * 1024


class LegacyParityAdapterError(ValueError):
    """The legacy comparison cannot be derived from exact sealed inputs."""


@dataclass(frozen=True, slots=True)
class _DirectoryHandle:
    descriptor: int
    path: Path
    identity: tuple[int, ...]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--campaign-directory", required=True, type=Path)
    parser.add_argument("--transport-root", required=True, type=Path)
    parser.add_argument("--scenario-id", required=True, choices=SCENARIO_IDS)
    return parser


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_uid",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


def _identity(observed: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(observed, field) for field in _IDENTITY_FIELDS)


def _require_external_path(path: Path, *, label: str) -> None:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path == Path("/")
        or ".." in path.parts
        or _is_beneath(path, _CHECKOUT)
    ):
        raise LegacyParityAdapterError(f"{label} path is unsafe")


def _open_directory(
    path: Path,
    *,
    mode: int,
    label: str,
) -> tuple[int, tuple[int, ...], _DirectoryHandle]:
    _require_external_path(path, label=label)
    parent_descriptor = -1
    descriptor = -1
    try:
        observed_parent = path.parent.lstat()
        resolved_parent = path.parent.resolve(strict=True)
        if (
            resolved_parent != path.parent
            or stat.S_ISLNK(observed_parent.st_mode)
            or not stat.S_ISDIR(observed_parent.st_mode)
            or observed_parent.st_uid != os.geteuid()
            or observed_parent.st_mode & 0o077
        ):
            raise LegacyParityAdapterError(f"{label} parent is unsafe")
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_parent = os.fstat(parent_descriptor)
        if _identity(opened_parent) != _identity(observed_parent):
            raise LegacyParityAdapterError(f"{label} parent identity changed")
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        named = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        full = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != mode
            or not (_identity(opened) == _identity(named) == _identity(full))
        ):
            raise LegacyParityAdapterError(f"{label} identity or mode changed")
        return (
            parent_descriptor,
            _identity(opened_parent),
            _DirectoryHandle(descriptor, path, _identity(opened)),
        )
    except LegacyParityAdapterError:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise LegacyParityAdapterError(f"{label} is unavailable") from exc


def _open_scenario(root: _DirectoryHandle, scenario_id: str) -> _DirectoryHandle:
    descriptor = -1
    try:
        descriptor = os.open(
            scenario_id,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root.descriptor,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o500
        ):
            raise LegacyParityAdapterError("campaign scenario is not sealed")
        named = os.stat(
            scenario_id,
            dir_fd=root.descriptor,
            follow_symlinks=False,
        )
        if _identity(opened) != _identity(named):
            raise LegacyParityAdapterError("campaign scenario identity changed")
        return _DirectoryHandle(
            descriptor,
            root.path / scenario_id,
            _identity(opened),
        )
    except LegacyParityAdapterError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise LegacyParityAdapterError("campaign scenario is unavailable") from exc


def _sealed_bytes_at(
    directory: _DirectoryHandle,
    name: str,
    *,
    label: str,
) -> tuple[bytes, tuple[int, ...]]:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory.descriptor,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o400
            or opened.st_size <= 0
            or opened.st_size > _MAX_BYTES
        ):
            raise LegacyParityAdapterError(f"{label} is not sealed")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise LegacyParityAdapterError(f"{label} read was incomplete")
            chunks.append(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise LegacyParityAdapterError(f"{label} changed while being read")
        named = os.stat(
            name,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )
        if _identity(named) != _identity(opened):
            raise LegacyParityAdapterError(f"{label} identity changed")
        return b"".join(chunks), _identity(opened)
    except LegacyParityAdapterError:
        raise
    except OSError as exc:
        raise LegacyParityAdapterError(f"{label} is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_snapshot(
    *,
    parent_descriptor: int,
    parent_identity: tuple[int, ...],
    root: _DirectoryHandle,
    scenario: _DirectoryHandle,
    root_files: dict[str, tuple[int, ...]],
    scenario_files: dict[str, tuple[int, ...]],
) -> None:
    try:
        parent = os.fstat(parent_descriptor)
        named_parent = root.path.parent.lstat()
        opened_root = os.fstat(root.descriptor)
        named_root = os.stat(
            root.path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        full_root = root.path.lstat()
        opened_scenario = os.fstat(scenario.descriptor)
        named_scenario = os.stat(
            scenario.path.name,
            dir_fd=root.descriptor,
            follow_symlinks=False,
        )
        full_scenario = scenario.path.lstat()
        if not (
            _identity(parent) == _identity(named_parent) == parent_identity
        ) or not (
            _identity(opened_root)
            == _identity(named_root)
            == _identity(full_root)
            == root.identity
        ):
            raise LegacyParityAdapterError("campaign root identity changed")
        if not (
            _identity(opened_scenario)
            == _identity(named_scenario)
            == _identity(full_scenario)
            == scenario.identity
        ):
            raise LegacyParityAdapterError("campaign scenario identity changed")
        for name, expected in root_files.items():
            if _identity(
                os.stat(name, dir_fd=root.descriptor, follow_symlinks=False)
            ) != expected:
                raise LegacyParityAdapterError("campaign manifest identity changed")
        for name, expected in scenario_files.items():
            if _identity(
                os.stat(name, dir_fd=scenario.descriptor, follow_symlinks=False)
            ) != expected:
                raise LegacyParityAdapterError("campaign artifact identity changed")
    except LegacyParityAdapterError:
        raise
    except OSError as exc:
        raise LegacyParityAdapterError("campaign snapshot identity changed") from exc


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_line_object(raw: bytes, *, label: str) -> dict[str, object]:
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise LegacyParityAdapterError(f"{label} is not one canonical line")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise LegacyParityAdapterError(f"{label} contains duplicate fields")
            result[name] = value
        return result

    try:
        value = json.loads(raw[:-1], object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LegacyParityAdapterError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict) or _canonical(value) + b"\n" != raw:
        raise LegacyParityAdapterError(f"{label} is not canonical")
    return value


def _load_member(campaign: Path, scenario_id: str) -> tuple[dict[str, object], tuple[bytes, ...]]:
    parent_descriptor, parent_identity, root = _open_directory(
        campaign,
        mode=0o500,
        label="campaign directory",
    )
    scenario: _DirectoryHandle | None = None
    root_files: dict[str, tuple[int, ...]] = {}
    scenario_files: dict[str, tuple[int, ...]] = {}
    try:
        if set(os.listdir(root.descriptor)) != {
            "campaign-manifest.json",
            *SCENARIO_IDS,
        }:
            raise LegacyParityAdapterError("campaign inventory is invalid")
        manifest_raw, manifest_identity = _sealed_bytes_at(
            root,
            "campaign-manifest.json",
            label="campaign manifest",
        )
        root_files["campaign-manifest.json"] = manifest_identity
        manifest = _canonical_line_object(
            manifest_raw,
            label="campaign manifest",
        )
        if (
            set(manifest) != _MANIFEST_FIELDS
            or manifest.get("schema_version") != "nautilus-phase4-campaign-v1"
            or manifest.get("paper_scenario_id") != "long-accounting"
            or not isinstance(manifest.get("strategy_source_sha256"), str)
            or len(manifest["strategy_source_sha256"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in manifest["strategy_source_sha256"]
            )
            or not isinstance(manifest.get("scenarios"), list)
            or len(manifest["scenarios"]) != len(SCENARIO_IDS)
        ):
            raise LegacyParityAdapterError("campaign manifest is invalid")
        selected: dict[str, object] | None = None
        for expected, record in zip(
            SCENARIO_IDS, manifest["scenarios"], strict=True
        ):
            if (
                not isinstance(record, dict)
                or set(record) != _SCENARIO_FIELDS
                or record.get("scenario_id") != expected
            ):
                raise LegacyParityAdapterError(
                    "campaign scenarios are incomplete or unordered"
                )
            for _filename, field in ARTIFACTS:
                digest = record.get(field)
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    raise LegacyParityAdapterError(
                        "campaign artifact digest is invalid"
                    )
            if expected == scenario_id:
                selected = record
        assert selected is not None
        scenario = _open_scenario(root, scenario_id)
        if set(os.listdir(scenario.descriptor)) != {
            name for name, _field in ARTIFACTS
        }:
            raise LegacyParityAdapterError(
                "campaign scenario inventory is invalid"
            )
        values: list[bytes] = []
        for filename, field in ARTIFACTS:
            value, artifact_identity = _sealed_bytes_at(
                scenario,
                filename,
                label=filename,
            )
            scenario_files[filename] = artifact_identity
            digest = hashlib.sha256(value).hexdigest()
            if selected[field] != digest:
                raise LegacyParityAdapterError(
                    "campaign artifact digest does not match"
                )
            values.append(value)
        _verify_snapshot(
            parent_descriptor=parent_descriptor,
            parent_identity=parent_identity,
            root=root,
            scenario=scenario,
            root_files=root_files,
            scenario_files=scenario_files,
        )
        return selected, tuple(values)
    except LegacyParityAdapterError:
        raise
    except OSError as exc:
        raise LegacyParityAdapterError("campaign snapshot is unavailable") from exc
    finally:
        if scenario is not None:
            os.close(scenario.descriptor)
        os.close(root.descriptor)
        os.close(parent_descriptor)


def _object_bytes(raw: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LegacyParityAdapterError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise LegacyParityAdapterError(f"{label} schema is invalid")
    return value


def _timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise LegacyParityAdapterError(f"{label} timestamp is invalid")
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LegacyParityAdapterError(f"{label} timestamp is invalid") from exc
    if observed.tzinfo is None:
        raise LegacyParityAdapterError(f"{label} timestamp is invalid")
    return observed


def _decimal(value: object, *, label: str) -> Decimal:
    if not isinstance(value, str):
        raise LegacyParityAdapterError(f"{label} decimal is invalid")
    try:
        observed = Decimal(value)
    except InvalidOperation as exc:
        raise LegacyParityAdapterError(f"{label} decimal is invalid") from exc
    if not observed.is_finite():
        raise LegacyParityAdapterError(f"{label} decimal is invalid")
    return observed


def _digest(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise LegacyParityAdapterError(f"{label} digest is invalid")
    return value


def _instrument(value: object, *, label: str) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != {"product_type", "symbol", "venue"}
        or value.get("product_type") != "crypto_spot"
        or value.get("symbol") != "BTCUSDT"
        or value.get("venue") != "BINANCE"
    ):
        raise LegacyParityAdapterError(f"{label} instrument is invalid")
    return value


def _validated_inputs(
    scenario_id: str,
    values: tuple[bytes, ...],
) -> tuple[str, list[dict[str, object]]]:
    engine = _object_bytes(values[0], label="engine configuration")
    if engine != {
        "execution_mode": "execution-simulation",
        "run_analysis": False,
        "schema_version": "nautilus-backtest-engine-config-v1",
    }:
        raise LegacyParityAdapterError("engine configuration semantics are invalid")

    catalog = _object_bytes(values[1], label="instrument catalog")
    catalog_fields = {
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
        "provenance_schema_version",
        "provider",
        "raw_evidence_sha256",
        "row_count",
        "schema_version",
        "snapshot_schema_version",
        "timeframe",
    }
    continuity = catalog.get("continuity")
    if (
        set(catalog) != catalog_fields
        or catalog.get("schema_version") != "market-dataset-manifest-v1"
        or catalog.get("snapshot_schema_version") != "market-snapshot-v1"
        or catalog.get("provenance_schema_version") != "market-data-v1"
        or catalog.get("normalization_version") != "market-normalization-v1"
        or catalog.get("timeframe") != "1m"
        or type(catalog.get("row_count")) is not int
        or not isinstance(continuity, dict)
        or set(continuity) != {"duplicate_report", "gap_report", "timeframe"}
        or continuity.get("duplicate_report") != []
        or continuity.get("gap_report") != []
        or continuity.get("timeframe") != "1m"
    ):
        raise LegacyParityAdapterError("instrument catalog semantics are invalid")
    catalog_instrument = _instrument(
        catalog.get("instrument"), label="instrument catalog"
    )
    for field in (
        "canonical_rows_sha256",
        "content_digest",
        "parquet_sha256",
        "raw_evidence_sha256",
    ):
        _digest(catalog.get(field), label=f"instrument catalog {field}")
    for field in (
        "fetched_at",
        "first_event_at",
        "known_at",
        "last_event_at",
        "observed_at",
    ):
        _timestamp(catalog.get(field), label=f"instrument catalog {field}")

    strategy = _object_bytes(values[2], label="strategy configuration")
    positions = strategy.get("positions")
    if (
        set(strategy) != {"effective_at", "positions", "schema_version"}
        or strategy.get("schema_version") != "nautilus-execution-target-v1"
        or not isinstance(positions, list)
        or len(positions) != 1
        or not isinstance(positions[0], dict)
        or set(positions[0]) != {"instrument", "target_quantity"}
        or _instrument(
            positions[0].get("instrument"), label="strategy configuration"
        )
        != catalog_instrument
    ):
        raise LegacyParityAdapterError("strategy configuration semantics are invalid")
    _timestamp(strategy.get("effective_at"), label="strategy configuration")
    target_quantity = positions[0].get("target_quantity")
    if _decimal(target_quantity, label="target quantity") == 0:
        raise LegacyParityAdapterError("target quantity is invalid")

    if not values[3].endswith(b"\n") or not values[3].strip():
        raise LegacyParityAdapterError("market data schema is invalid")
    rows: list[dict[str, object]] = []
    for index, line in enumerate(values[3].splitlines(), start=1):
        row = _object_bytes(line, label=f"market data row {index}")
        if set(row) != {"close", "high", "low", "open", "open_time", "volume"}:
            raise LegacyParityAdapterError("market data row schema is invalid")
        open_price = _decimal(row.get("open"), label="market data open")
        high = _decimal(row.get("high"), label="market data high")
        low = _decimal(row.get("low"), label="market data low")
        close = _decimal(row.get("close"), label="market data close")
        volume = _decimal(row.get("volume"), label="market data volume")
        _timestamp(row.get("open_time"), label="market data")
        if low > min(open_price, close) or high < max(open_price, close) or volume < 0:
            raise LegacyParityAdapterError("market data row semantics are invalid")
        rows.append(row)
    if catalog.get("row_count") != len(rows):
        raise LegacyParityAdapterError("market data row count is invalid")

    scenario = _object_bytes(values[4], label="simulation scenario")
    scenario_fields = {
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
    events = scenario.get("events")
    if (
        set(scenario) != scenario_fields
        or scenario.get("schema_version") != "nautilus-execution-scenario-v1"
        or scenario.get("scenario_id") != scenario_id
        or scenario.get("session_policy") != "explicit-open-flag-v1"
        or scenario.get("stop_take_profit_precedence") != "stop-first"
        or type(scenario.get("stale_quote_threshold_seconds")) is not int
        or scenario.get("stale_quote_threshold_seconds", -1) < 0
        or _instrument(scenario.get("instrument"), label="simulation scenario")
        != catalog_instrument
        or not isinstance(events, list)
        or len(events) != len(rows)
        or _digest(scenario.get("catalog_sha256"), label="scenario catalog")
        != hashlib.sha256(values[1]).hexdigest()
        or _digest(scenario.get("strategy_sha256"), label="scenario strategy")
        != hashlib.sha256(values[2]).hexdigest()
        or _digest(scenario.get("market_data_sha256"), label="scenario market data")
        != hashlib.sha256(values[3]).hexdigest()
    ):
        raise LegacyParityAdapterError("campaign scenario semantics are invalid")
    for field in (
        "fee_rate",
        "liquidity_limit",
        "slippage_bps",
    ):
        _decimal(scenario.get(field), label=f"scenario {field}")
    for field in ("stop_price", "take_profit_price"):
        value = scenario.get(field)
        if value is not None:
            _decimal(value, label=f"scenario {field}")
    event_fields = {
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
    for event in events:
        if (
            not isinstance(event, dict)
            or set(event) != event_fields
            or type(event.get("sequence")) is not int
            or event.get("sequence", 0) <= 0
            or type(event.get("session_open")) is not bool
        ):
            raise LegacyParityAdapterError("scenario event schema is invalid")
        for field in ("ask", "bid", "close", "high", "low", "open", "volume"):
            _decimal(event.get(field), label=f"scenario event {field}")
        _timestamp(event.get("event_time"), label="scenario event")
        _timestamp(event.get("quote_time"), label="scenario quote")
    return str(target_quantity), rows


class _SealedDataHandler:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def load(self) -> pd.DataFrame:
        frame = pd.DataFrame(self._rows)
        frame["timestamp"] = pd.to_datetime(frame.pop("open_time"), utc=True)
        return frame.set_index("timestamp")

    def to_bars(self, frame: pd.DataFrame) -> list[Bar]:
        return [
            Bar(
                timestamp=index.to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for index, row in frame.iterrows()
        ]


class _TargetComparisonStrategy(Strategy):
    def __init__(self, config: BacktestConfig, *, target_quantity: str) -> None:
        super().__init__(config)
        self._action = "BUY" if float(target_quantity) > 0 else "SELL"

    def next(self, i: int, bar: Bar, positions: list[object]) -> Signal:
        action = self._action if i == 0 else "HOLD"
        return Signal(
            action=action,
            confidence=1.0,
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
        )


def _derive_record(
    scenario_id: str,
    identity: dict[str, object],
    values: tuple[bytes, ...],
) -> dict[str, object]:
    target, rows = _validated_inputs(scenario_id, values)
    previous_logging_disable = logging.root.manager.disable
    try:
        config = BacktestConfig()
        engine = BacktestEngine(
            "BTC/USDT",
            _TargetComparisonStrategy(config, target_quantity=target),
            config,
            _SealedDataHandler(rows),
            "1m",
        )
        logging.disable(logging.CRITICAL)
        result = engine.run()
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        raise LegacyParityAdapterError("legacy engine comparison failed") from exc
    finally:
        logging.disable(previous_logging_disable)
    aggregate = result.get("aggregate")
    if (
        len(rows) >= 50
        or not isinstance(aggregate, dict)
        or aggregate.get("total_trades") != 0
        or result.get("equity_curve") != []
    ):
        raise LegacyParityAdapterError("legacy result has no reviewed classification")
    result_domain = {
        "aggregate": aggregate,
        "bar_count": len(rows),
        "engine": "preserved-backtest-engine-v1",
        "scenario_id": scenario_id,
    }
    result_sha256 = hashlib.sha256(_canonical(result_domain)).hexdigest()
    event_sha256 = hashlib.sha256(
        _canonical(
            {
                "classification": "legacy-minimum-50-bars",
                "result_sha256": result_sha256,
                "scenario_id": scenario_id,
            }
        )
    ).hexdigest()
    return {
        "engine_configuration_sha256": identity["engine_configuration_sha256"],
        "instrument_catalog_sha256": identity["instrument_catalog_sha256"],
        "legacy_classification": "legacy-minimum-50-bars",
        "legacy_disposition": "explained-difference",
        "legacy_event_sha256": event_sha256,
        "legacy_result_sha256": result_sha256,
        "legacy_selected": False,
        "market_data_sha256": identity["market_data_sha256"],
        "scenario_id": scenario_id,
        "schema_version": "nautilus-legacy-scenario-comparison-v1",
        "simulation_scenario_sha256": identity["simulation_scenario_sha256"],
        "strategy_configuration_sha256": identity["strategy_configuration_sha256"],
    }


def _publish(transport_root: Path, scenario_id: str, record: dict[str, object]) -> None:
    parent_descriptor, parent_identity, root = _open_directory(
        transport_root,
        mode=0o700,
        label="transport root",
    )
    name = f"{scenario_id}.json"
    value = _canonical(record) + b"\n"
    descriptor = -1
    created_identity: tuple[int, ...] | None = None
    published = False
    try:
        allowed = {f"{item}.json" for item in SCENARIO_IDS}
        if any(entry not in allowed for entry in os.listdir(root.descriptor)):
            raise LegacyParityAdapterError(
                "transport root contains an unknown entry"
            )
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=root.descriptor,
        )
        os.fchmod(descriptor, 0o400)
        created_identity = _identity(os.fstat(descriptor))
        remaining = memoryview(value)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short legacy record write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        parent = os.fstat(parent_descriptor)
        named_parent = transport_root.parent.lstat()
        opened_root = os.fstat(root.descriptor)
        named_root = os.stat(
            transport_root.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        full_root = transport_root.lstat()
        opened = os.fstat(descriptor)
        named = os.stat(
            name,
            dir_fd=root.descriptor,
            follow_symlinks=False,
        )
        if not (
            _identity(parent) == _identity(named_parent) == parent_identity
            and (opened_root.st_dev, opened_root.st_ino)
            == (named_root.st_dev, named_root.st_ino)
            == (full_root.st_dev, full_root.st_ino)
            == (root.identity[0], root.identity[1])
            and stat.S_IMODE(opened_root.st_mode) == 0o700
            and _identity(opened) == _identity(named)
            and opened.st_uid == os.geteuid()
            and opened.st_nlink == 1
            and stat.S_IMODE(opened.st_mode) == 0o400
            and opened.st_size == len(value)
        ):
            raise LegacyParityAdapterError(
                "legacy comparison or transport identity changed"
            )
        published = True
    except FileExistsError as exc:
        raise LegacyParityAdapterError("legacy comparison already exists") from exc
    except LegacyParityAdapterError:
        raise
    except OSError as exc:
        raise LegacyParityAdapterError("legacy comparison cannot be sealed") from exc
    finally:
        try:
            if descriptor >= 0 and created_identity is not None and not published:
                try:
                    opened = os.fstat(descriptor)
                    named = os.stat(
                        name,
                        dir_fd=root.descriptor,
                        follow_symlinks=False,
                    )
                    if (
                        _identity(opened) == _identity(named)
                        and (opened.st_dev, opened.st_ino)
                        == (created_identity[0], created_identity[1])
                    ):
                        os.unlink(name, dir_fd=root.descriptor)
                except OSError:
                    pass
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(root.descriptor)
            os.close(parent_descriptor)


def run_legacy_comparison(
    *,
    campaign_directory: Path,
    transport_root: Path,
    scenario_id: str,
) -> None:
    identity, values = _load_member(campaign_directory, scenario_id)
    record = _derive_record(scenario_id, identity, values)
    _publish(transport_root, scenario_id, record)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        run_legacy_comparison(**vars(arguments))
    except (LegacyParityAdapterError, OSError, ValueError):
        print("error: legacy comparison did not complete", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
