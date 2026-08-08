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

import nautilus_parity_adapter as adapter


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
        instrument = {
            "product_type": "crypto_spot",
            "symbol": "BTCUSDT",
            "venue": "BINANCE",
        }
        engine = _canonical(
            {
                "execution_mode": "execution-simulation",
                "run_analysis": False,
                "schema_version": "nautilus-backtest-engine-config-v1",
            }
        )
        catalog = _canonical(
            {
                "canonical_rows_sha256": "0" * 64,
                "content_digest": "a" * 64,
                "continuity": {
                    "duplicate_report": [],
                    "gap_report": [],
                    "timeframe": "1m",
                },
                "fetched_at": "2026-08-05T12:02:00Z",
                "first_event_at": "2026-08-05T12:00:00Z",
                "importer_version": "fixture-catalog-v1",
                "instrument": instrument,
                "known_at": "2026-08-05T12:02:00Z",
                "last_event_at": "2026-08-05T12:00:00Z",
                "normalization_version": "market-normalization-v1",
                "observed_at": "2026-08-05T12:02:00Z",
                "parquet_sha256": "b" * 64,
                "provenance_schema_version": "market-data-v1",
                "provider": "deterministic-fixture-v1",
                "raw_evidence_sha256": "c" * 64,
                "row_count": 1,
                "schema_version": "market-dataset-manifest-v1",
                "snapshot_schema_version": "market-snapshot-v1",
                "timeframe": "1m",
            }
        )
        strategy = _canonical(
            {
                "effective_at": "2026-08-05T12:00:00Z",
                "positions": [
                    {
                        "instrument": instrument,
                        "target_quantity": "2",
                    }
                ],
                "schema_version": "nautilus-execution-target-v1",
            }
        )
        market = (
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
            + b"\n"
        )
        scenario = _canonical(
            {
                "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
                "events": [
                    {
                        "ask": "100",
                        "bid": "99",
                        "close": "101",
                        "event_time": "2026-08-05T12:00:00Z",
                        "high": "102",
                        "low": "98",
                        "open": "100",
                        "quote_time": "2026-08-05T12:00:00Z",
                        "sequence": 1,
                        "session_open": True,
                        "volume": "2",
                    }
                ],
                "fee_rate": "0.001",
                "instrument": instrument,
                "liquidity_limit": "10",
                "market_data_sha256": hashlib.sha256(market).hexdigest(),
                "scenario_id": scenario_id,
                "schema_version": "nautilus-execution-scenario-v1",
                "session_policy": "explicit-open-flag-v1",
                "slippage_bps": "0",
                "stale_quote_threshold_seconds": 30,
                "stop_price": None,
                "stop_take_profit_precedence": "stop-first",
                "strategy_sha256": hashlib.sha256(strategy).hexdigest(),
                "take_profit_price": None,
            }
        )
        values = (engine, catalog, strategy, market, scenario)
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


def _mutate_artifact(
    campaign: Path,
    *,
    scenario_id: str,
    filename: str,
    value: bytes,
) -> None:
    campaign.chmod(0o700)
    scenario = campaign / scenario_id
    scenario.chmod(0o700)
    artifact = scenario / filename
    artifact.chmod(0o600)
    artifact.write_bytes(value)
    artifact.chmod(0o400)
    manifest_path = campaign / "campaign-manifest.json"
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_bytes())
    field = dict(FILES)[filename]
    for record in manifest["scenarios"]:
        if record["scenario_id"] == scenario_id:
            record[field] = hashlib.sha256(value).hexdigest()
            break
    manifest_path.write_bytes(_canonical(manifest) + b"\n")
    manifest_path.chmod(0o400)
    scenario.chmod(0o500)
    campaign.chmod(0o500)


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


@pytest.mark.parametrize("authority_name", ("campaign", "transport"))
def test_adapter_rejects_any_authority_beneath_the_canonical_checkout(
    authority: tuple[Path, Path],
    authority_name: str,
) -> None:
    campaign, transport = authority
    internal_parent = Path(
        tempfile.mkdtemp(prefix="legacy-boundary-", dir=ROOT.parents[1])
    )
    internal_parent.chmod(0o700)
    try:
        if authority_name == "campaign":
            internal_campaign = internal_parent / "campaign"
            shutil.copytree(campaign, internal_campaign, copy_function=shutil.copy2)
            campaign = internal_campaign
        else:
            internal_transport = internal_parent / "transport"
            internal_transport.mkdir(mode=0o700)
            transport = internal_transport

        completed = _run(campaign, transport)

        assert completed.returncode == 1
        assert completed.stdout == b""
        assert completed.stderr == b"error: legacy comparison did not complete\n"
        assert not (transport / "long-accounting.json").exists()
    finally:
        for directory, _children, filenames in os.walk(internal_parent):
            Path(directory).chmod(0o700)
            for filename in filenames:
                path = Path(directory) / filename
                if not path.is_symlink():
                    path.chmod(0o600)
        shutil.rmtree(internal_parent)


@pytest.mark.parametrize(
    ("filename", "value"),
    (
        ("simulation-scenario.json", b"[]"),
        (
            "market-data.json",
            _canonical(
                {
                    "close": "not-a-number",
                    "high": "102",
                    "low": "98",
                    "open": "100",
                    "open_time": "not-a-time",
                    "volume": "2",
                }
            )
            + b"\n",
        ),
    ),
)
def test_adapter_normalizes_self_consistent_malformed_inputs_to_generic_error(
    authority: tuple[Path, Path],
    filename: str,
    value: bytes,
) -> None:
    campaign, transport = authority
    _mutate_artifact(
        campaign,
        scenario_id="long-accounting",
        filename=filename,
        value=value,
    )

    completed = _run(campaign, transport)

    assert completed.returncode == 1
    assert completed.stdout == b""
    assert completed.stderr == b"error: legacy comparison did not complete\n"
    assert not (transport / "long-accounting.json").exists()


def test_adapter_rejects_campaign_root_substitution_during_snapshot(
    authority: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign, transport = authority
    replacement = campaign.parent / "replacement-campaign"
    displaced = campaign.parent / "displaced-campaign"
    shutil.copytree(campaign, replacement, copy_function=shutil.copy2)
    real_open = os.open
    swapped = False

    def swap_root(path: object, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if not swapped and os.fspath(path).endswith("engine-configuration.json"):
            campaign.rename(displaced)
            replacement.rename(campaign)
            swapped = True
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(adapter.os, "open", swap_root)

    with pytest.raises(adapter.LegacyParityAdapterError, match="identity|changed"):
        adapter.run_legacy_comparison(
            campaign_directory=campaign,
            transport_root=transport,
            scenario_id="long-accounting",
        )

    assert swapped is True
    assert not (transport / "long-accounting.json").exists()


def test_adapter_rejects_scenario_substitution_during_snapshot(
    authority: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign, transport = authority
    displaced = campaign / "displaced-long-accounting"
    staged_source = campaign.parent / "staged-long-accounting"
    shutil.copytree(
        campaign / "long-accounting", staged_source, copy_function=shutil.copy2
    )
    real_open = os.open
    swapped = False

    def swap_scenario(path: object, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if not swapped and os.fspath(path).endswith("engine-configuration.json"):
            swapped = True
            campaign.chmod(0o700)
            staged = campaign / "staged-long-accounting"
            shutil.copytree(staged_source, staged, copy_function=shutil.copy2)
            (campaign / "long-accounting").rename(displaced)
            staged.rename(campaign / "long-accounting")
            campaign.chmod(0o500)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(adapter.os, "open", swap_scenario)

    with pytest.raises(adapter.LegacyParityAdapterError, match="identity|changed"):
        adapter.run_legacy_comparison(
            campaign_directory=campaign,
            transport_root=transport,
            scenario_id="long-accounting",
        )

    assert swapped is True
    assert not (transport / "long-accounting.json").exists()


def test_adapter_rejects_transport_root_substitution_during_publication(
    authority: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign, transport = authority
    displaced = transport.parent / "displaced-transport"
    replacement = transport.parent / "replacement-transport"
    replacement.mkdir(mode=0o700)
    real_fsync = os.fsync
    swapped = False

    def swap_transport(descriptor: int) -> None:
        nonlocal swapped
        if not swapped and (transport / "long-accounting.json").exists():
            transport.rename(displaced)
            replacement.rename(transport)
            swapped = True
        real_fsync(descriptor)

    monkeypatch.setattr(adapter.os, "fsync", swap_transport)

    with pytest.raises(adapter.LegacyParityAdapterError, match="identity|changed"):
        adapter.run_legacy_comparison(
            campaign_directory=campaign,
            transport_root=transport,
            scenario_id="long-accounting",
        )

    assert swapped is True
    assert not (transport / "long-accounting.json").exists()
    retained = displaced / "long-accounting.json"
    assert retained.exists()
    assert stat.S_IMODE(retained.stat().st_mode) == 0o400


def test_adapter_preserves_replacement_record_inode_on_publication_failure(
    authority: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign, transport = authority
    record = transport / "long-accounting.json"
    real_fsync = os.fsync
    replacement_identity: tuple[int, int] | None = None

    def replace_record(descriptor: int) -> None:
        nonlocal replacement_identity
        if replacement_identity is None and record.exists():
            record.unlink()
            record.write_bytes(b"replacement")
            record.chmod(0o400)
            observed = record.stat()
            replacement_identity = (observed.st_dev, observed.st_ino)
        real_fsync(descriptor)

    monkeypatch.setattr(adapter.os, "fsync", replace_record)

    with pytest.raises(adapter.LegacyParityAdapterError, match="identity|changed"):
        adapter.run_legacy_comparison(
            campaign_directory=campaign,
            transport_root=transport,
            scenario_id="long-accounting",
        )

    assert replacement_identity is not None
    observed = record.stat()
    assert (observed.st_dev, observed.st_ino) == replacement_identity


def test_adapter_retains_its_sealed_partial_record_after_short_write_failure(
    authority: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign, transport = authority
    real_write = os.write
    calls = 0

    def partial_then_fail(descriptor: int, value) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, value[:5])
        raise OSError("inert partial legacy record write")

    monkeypatch.setattr(adapter.os, "write", partial_then_fail)

    with pytest.raises(adapter.LegacyParityAdapterError, match="cannot be sealed"):
        adapter.run_legacy_comparison(
            campaign_directory=campaign,
            transport_root=transport,
            scenario_id="long-accounting",
        )

    assert calls == 2
    retained = transport / "long-accounting.json"
    assert retained.read_bytes() != b""
    assert stat.S_IMODE(retained.stat().st_mode) == 0o400


def test_adapter_failure_never_calls_production_unlink(
    authority: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign, transport = authority
    real_write = os.write
    calls = 0
    unlink_calls: list[object] = []

    def partial_then_fail(descriptor: int, value) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, value[:5])
        raise OSError("inert partial legacy record write")

    monkeypatch.setattr(adapter.os, "write", partial_then_fail)
    monkeypatch.setattr(
        adapter.os,
        "unlink",
        lambda path, **_kwargs: unlink_calls.append(path),
    )

    with pytest.raises(adapter.LegacyParityAdapterError, match="cannot be sealed"):
        adapter.run_legacy_comparison(
            campaign_directory=campaign,
            transport_root=transport,
            scenario_id="long-accounting",
        )

    assert unlink_calls == []
    assert (transport / "long-accounting.json").exists()
