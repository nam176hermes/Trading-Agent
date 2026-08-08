from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "nautilus_parity_adapter.py"
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
FILES = (
    ("engine-configuration.json", "engine_configuration_sha256"),
    ("instrument-catalog.json", "instrument_catalog_sha256"),
    ("strategy-configuration.json", "strategy_configuration_sha256"),
    ("market-data.json", "market_data_sha256"),
    ("simulation-scenario.json", "simulation_scenario_sha256"),
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@pytest.fixture
def authority() -> tuple[Path, Path]:
    parent = Path(tempfile.mkdtemp(prefix="legacy-nautilus-adapter-", dir="/tmp"))
    parent.chmod(0o700)
    campaign = parent / "campaign"
    transport = parent / "transport"
    campaign.mkdir(mode=0o700)
    transport.mkdir(mode=0o700)
    records: list[dict[str, object]] = []
    for scenario_id in SCENARIO_IDS:
        directory = campaign / scenario_id
        directory.mkdir(mode=0o700)
        values = (
            _canonical({"execution_mode": "execution-simulation"}),
            _canonical({"known_at": "2026-08-05T12:02:00Z"}),
            _canonical(
                {
                    "positions": [
                        {
                            "instrument": {
                                "product_type": "crypto_spot",
                                "symbol": "BTCUSDT",
                                "venue": "BINANCE",
                            },
                            "target_quantity": "2",
                        }
                    ]
                }
            ),
            _canonical(
                {
                    "close": "101",
                    "high": "102",
                    "low": "98",
                    "open": "100",
                    "open_time": "2026-08-05T12:00:00Z",
                    "volume": "2",
                }
            )
            + b"\n",
            _canonical({"scenario_id": scenario_id}),
        )
        record: dict[str, object] = {"scenario_id": scenario_id}
        for (filename, field), value in zip(FILES, values, strict=True):
            path = directory / filename
            path.write_bytes(value)
            path.chmod(0o400)
            record[field] = hashlib.sha256(value).hexdigest()
        directory.chmod(0o500)
        records.append(record)
    manifest = {
        "paper_scenario_id": "long-accounting",
        "scenarios": records,
        "schema_version": "nautilus-phase4-campaign-v1",
        "strategy_source_sha256": "a" * 64,
    }
    manifest_path = campaign / "campaign-manifest.json"
    manifest_path.write_bytes(_canonical(manifest) + b"\n")
    manifest_path.chmod(0o400)
    campaign.chmod(0o500)
    try:
        yield campaign, transport
    finally:
        for directory, _children, filenames in os.walk(parent):
            Path(directory).chmod(0o700)
            for filename in filenames:
                path = Path(directory) / filename
                if not path.is_symlink():
                    path.chmod(0o600)
        shutil.rmtree(parent)


def _run(campaign: Path, transport: Path, *extra: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            str(ADAPTER),
            "--campaign-directory",
            str(campaign),
            "--transport-root",
            str(transport),
            "--scenario-id",
            "long-accounting",
            *extra,
        ],
        cwd=ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_adapter_runs_preserved_backtest_and_writes_one_comparison_only_record(
    authority: tuple[Path, Path],
) -> None:
    campaign, transport = authority

    completed = _run(campaign, transport)

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stdout == b""
    assert completed.stderr == b""
    assert tuple(path.name for path in transport.iterdir()) == ("long-accounting.json",)
    path = transport / "long-accounting.json"
    raw = path.read_bytes()
    record = json.loads(raw)
    assert raw == _canonical(record) + b"\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    assert record["schema_version"] == "nautilus-legacy-scenario-comparison-v1"
    assert record["scenario_id"] == "long-accounting"
    assert record["legacy_disposition"] == "explained-difference"
    assert record["legacy_classification"] == "legacy-minimum-50-bars"
    assert record["legacy_selected"] is False
    assert record["legacy_result_sha256"] != record["legacy_event_sha256"]
    assert set(record) == {
        "engine_configuration_sha256",
        "instrument_catalog_sha256",
        "legacy_classification",
        "legacy_disposition",
        "legacy_event_sha256",
        "legacy_result_sha256",
        "legacy_selected",
        "market_data_sha256",
        "scenario_id",
        "schema_version",
        "simulation_scenario_sha256",
        "strategy_configuration_sha256",
    }


def test_adapter_is_no_clobber_and_has_no_record_or_arbitrary_scenario_interface(
    authority: tuple[Path, Path],
) -> None:
    campaign, transport = authority

    assert _run(campaign, transport).returncode == 0
    first = (transport / "long-accounting.json").read_bytes()

    assert _run(campaign, transport).returncode == 1
    assert _run(campaign, transport, "--record", "/tmp/forbidden.json").returncode == 2
    unknown = subprocess.run(
        [
            sys.executable,
            str(ADAPTER),
            "--campaign-directory",
            str(campaign),
            "--transport-root",
            str(transport),
            "--scenario-id",
            "unknown",
        ],
        cwd=ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert unknown.returncode == 2
    assert (transport / "long-accounting.json").read_bytes() == first
