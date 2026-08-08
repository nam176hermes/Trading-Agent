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


def _directory(path: Path, *, mode: int, label: str) -> tuple[Path, ...]:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path == Path("/")
        or ".." in path.parts
        or _is_beneath(path, _ROOT)
    ):
        raise LegacyParityAdapterError(f"{label} path is unsafe")
    try:
        observed = path.lstat()
        resolved = path.resolve(strict=True)
        entries = tuple(path.iterdir())
    except OSError as exc:
        raise LegacyParityAdapterError(f"{label} is unavailable") from exc
    if (
        resolved != path
        or stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != mode
    ):
        raise LegacyParityAdapterError(f"{label} is unsafe")
    return entries


def _sealed_bytes(path: Path, *, label: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
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
        named = path.stat(follow_symlinks=False)
        identity = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(named, name) != getattr(opened, name) for name in identity):
            raise LegacyParityAdapterError(f"{label} identity changed")
        return b"".join(chunks)
    except LegacyParityAdapterError:
        raise
    except OSError as exc:
        raise LegacyParityAdapterError(f"{label} is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


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
    entries = _directory(campaign, mode=0o500, label="campaign directory")
    if {entry.name for entry in entries} != {"campaign-manifest.json", *SCENARIO_IDS}:
        raise LegacyParityAdapterError("campaign inventory is invalid")
    manifest = _canonical_line_object(
        _sealed_bytes(campaign / "campaign-manifest.json", label="campaign manifest"),
        label="campaign manifest",
    )
    if (
        set(manifest) != _MANIFEST_FIELDS
        or manifest.get("schema_version") != "nautilus-phase4-campaign-v1"
        or manifest.get("paper_scenario_id") != "long-accounting"
        or not isinstance(manifest.get("scenarios"), list)
        or len(manifest["scenarios"]) != len(SCENARIO_IDS)
    ):
        raise LegacyParityAdapterError("campaign manifest is invalid")
    selected: dict[str, object] | None = None
    for expected, record in zip(SCENARIO_IDS, manifest["scenarios"], strict=True):
        if (
            not isinstance(record, dict)
            or set(record) != _SCENARIO_FIELDS
            or record.get("scenario_id") != expected
        ):
            raise LegacyParityAdapterError("campaign scenarios are incomplete or unordered")
        if expected == scenario_id:
            selected = record
    assert selected is not None
    scenario_directory = campaign / scenario_id
    scenario_entries = _directory(
        scenario_directory,
        mode=0o500,
        label="campaign scenario",
    )
    if {entry.name for entry in scenario_entries} != {
        name for name, _field in ARTIFACTS
    }:
        raise LegacyParityAdapterError("campaign scenario inventory is invalid")
    values: list[bytes] = []
    for filename, field in ARTIFACTS:
        value = _sealed_bytes(scenario_directory / filename, label=filename)
        digest = hashlib.sha256(value).hexdigest()
        if selected[field] != digest:
            raise LegacyParityAdapterError("campaign artifact digest does not match")
        values.append(value)
    return selected, tuple(values)


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
    try:
        strategy = json.loads(values[2])
        scenario = json.loads(values[4])
        rows = [json.loads(line) for line in values[3].splitlines()]
        target = strategy["positions"][0]["target_quantity"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise LegacyParityAdapterError("campaign semantics are invalid") from exc
    if scenario.get("scenario_id") != scenario_id or not rows:
        raise LegacyParityAdapterError("campaign scenario semantics are invalid")
    config = BacktestConfig()
    engine = BacktestEngine(
        "BTC/USDT",
        _TargetComparisonStrategy(config, target_quantity=str(target)),
        config,
        _SealedDataHandler(rows),
        "1m",
    )
    logging.disable(logging.CRITICAL)
    try:
        result = engine.run()
    finally:
        logging.disable(logging.NOTSET)
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
    entries = _directory(transport_root, mode=0o700, label="transport root")
    allowed = {f"{item}.json" for item in SCENARIO_IDS}
    if any(entry.name not in allowed for entry in entries):
        raise LegacyParityAdapterError("transport root contains an unknown entry")
    path = transport_root / f"{scenario_id}.json"
    value = _canonical(record) + b"\n"
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
        created = True
        os.fchmod(descriptor, 0o400)
        remaining = memoryview(value)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short legacy record write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise LegacyParityAdapterError("legacy comparison already exists") from exc
    except OSError as exc:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise LegacyParityAdapterError("legacy comparison cannot be sealed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


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
