from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from pathlib import Path
from uuid import UUID

import pytest

from packages.engine_contracts import (
    ArtifactReference,
    CURRENT_SCHEMA_VERSION,
    EngineCommandEnvelope,
    EngineEventEnvelope,
    RunBacktest,
    RunBacktestSimulation,
    canonical_json_bytes,
    payload_digest,
)


LAUNCHER = Path("engines/nautilus/launcher/nautilus_backtest.py")


@pytest.fixture(scope="module")
def launcher_module():
    spec = importlib.util.spec_from_file_location("nautilus_backtest_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request() -> EngineCommandEnvelope:
    configuration = ArtifactReference(
        artifact_id=UUID("11111111-1111-4111-8111-111111111111"),
        sha256="1" * 64,
        media_type="application/json",
    )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=configuration,
        instrument_catalog=ArtifactReference(
            artifact_id=UUID("22222222-2222-4222-8222-222222222222"),
            sha256="2" * 64,
            media_type="application/json",
        ),
        strategy_configuration=ArtifactReference(
            artifact_id=UUID("33333333-3333-4333-8333-333333333333"),
            sha256="3" * 64,
            media_type="application/json",
        ),
        market_data=ArtifactReference(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            sha256="4" * 64,
            media_type="application/jsonl",
        ),
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    return EngineCommandEnvelope(
        message_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        correlation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        causation_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        engine_run_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        stream_sequence=1,
        event_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        initialization_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        schema_version=CURRENT_SCHEMA_VERSION,
        producer_identity="worker-authority-1",
        source_commit="0123456789abcdef0123456789abcdef01234567",
        config_digest=payload_digest(
            {
                "engine_configuration": command.engine_configuration,
                "instrument_catalog": command.instrument_catalog,
                "strategy_configuration": command.strategy_configuration,
            }
        ),
        payload_digest=payload_digest(command),
        payload=command,
    )


_SCENARIO_IDS = (
    "long-accounting",
    "short-accounting",
    "partial-fill",
    "same-bar-stop-take-profit",
    "stale-quote",
    "zero-liquidity",
    "session-boundary",
    "event-digest",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _simulation_fixture(scenario_id: str) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    assert scenario_id in _SCENARIO_IDS
    target_quantity = "-2" if scenario_id == "short-accounting" else "1"
    if scenario_id == "long-accounting":
        target_quantity = "2"
    elif scenario_id == "partial-fill":
        target_quantity = "3"
    configuration = _canonical(
        {
            "execution_mode": "execution-simulation",
            "run_analysis": False,
            "schema_version": "nautilus-backtest-engine-config-v1",
        }
    )
    strategy = _canonical(
        {
            "effective_at": "2026-08-05T12:00:00Z",
            "positions": [
                {
                    "instrument": {
                        "product_type": "crypto_spot",
                        "symbol": "BTCUSDT",
                        "venue": "BINANCE",
                    },
                    "target_quantity": target_quantity,
                }
            ],
            "schema_version": "nautilus-execution-target-v1",
        }
    )
    events = [
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
    ]
    liquidity_limit = "10"
    stop_price: str | None = None
    take_profit_price: str | None = None
    if scenario_id == "partial-fill":
        events[0]["volume"] = "1"
        liquidity_limit = "1"
    elif scenario_id == "same-bar-stop-take-profit":
        events[0]["high"] = "103"
        events[0]["low"] = "97"
        stop_price = "98"
        take_profit_price = "102"
    elif scenario_id == "stale-quote":
        events[0]["quote_time"] = "2026-08-05T11:58:00Z"
    elif scenario_id == "zero-liquidity":
        liquidity_limit = "0"
    elif scenario_id == "session-boundary":
        events[0]["session_open"] = False
        events.append(
            {
                "ask": "102",
                "bid": "101",
                "close": "102",
                "event_time": "2026-08-05T12:01:00Z",
                "high": "103",
                "low": "100",
                "open": "101",
                "quote_time": "2026-08-05T12:01:00Z",
                "sequence": 2,
                "session_open": True,
                "volume": "2",
            }
        )
    market_rows = [
        {
            "close": event["close"],
            "high": event["high"],
            "low": event["low"],
            "open": event["open"],
            "open_time": event["event_time"],
            "volume": event["volume"],
        }
        for event in events
    ]
    market = b"".join(_canonical(row) + b"\n" for row in market_rows)
    catalog = _canonical(
        {
            "canonical_rows_sha256": hashlib.sha256(_canonical(market_rows)).hexdigest(),
            "content_digest": "a" * 64,
            "continuity": {
                "duplicate_report": [],
                "gap_report": [],
                "timeframe": "1m",
            },
            "fetched_at": "2026-08-05T12:02:00Z",
            "first_event_at": events[0]["event_time"],
            "importer_version": "fixture-catalog-v1",
            "instrument": {
                "product_type": "crypto_spot",
                "symbol": "BTCUSDT",
                "venue": "BINANCE",
            },
            "known_at": "2026-08-05T12:02:00Z",
            "last_event_at": events[-1]["event_time"],
            "normalization_version": "market-normalization-v1",
            "observed_at": "2026-08-05T12:02:00Z",
            "parquet_sha256": "b" * 64,
            "provider": "deterministic-fixture-v1",
            "provenance_schema_version": "market-data-v1",
            "raw_evidence_sha256": "c" * 64,
            "row_count": len(events),
            "schema_version": "market-dataset-manifest-v1",
            "snapshot_schema_version": "market-snapshot-v1",
            "timeframe": "1m",
        }
    )
    scenario = _canonical(
        {
            "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
            "events": events,
            "fee_rate": "0.001",
            "instrument": {
                "product_type": "crypto_spot",
                "symbol": "BTCUSDT",
                "venue": "BINANCE",
            },
            "liquidity_limit": liquidity_limit,
            "market_data_sha256": hashlib.sha256(market).hexdigest(),
            "scenario_id": scenario_id,
            "schema_version": "nautilus-execution-scenario-v1",
            "session_policy": "explicit-open-flag-v1",
            "slippage_bps": "0",
            "stale_quote_threshold_seconds": 30,
            "stop_price": stop_price,
            "stop_take_profit_precedence": "stop-first",
            "strategy_sha256": hashlib.sha256(strategy).hexdigest(),
            "take_profit_price": take_profit_price,
        }
    )
    return configuration, catalog, strategy, market, scenario


def _simulation_request(
    artifacts: tuple[bytes, bytes, bytes, bytes, bytes],
) -> EngineCommandEnvelope:
    references = tuple(
        ArtifactReference(
            artifact_id=UUID(
                f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111"
            ),
            sha256=hashlib.sha256(value).hexdigest(),
            media_type="application/jsonl" if index == 4 else "application/json",
        )
        for index, value in enumerate(artifacts, start=1)
    )
    command = RunBacktestSimulation(
        command_type="RunBacktestSimulation",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        simulation_scenario=references[4],
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    return _request().model_copy(
        update={
            "config_digest": payload_digest(
                {
                    "engine_configuration": command.engine_configuration,
                    "instrument_catalog": command.instrument_catalog,
                    "strategy_configuration": command.strategy_configuration,
                }
            ),
            "payload_digest": payload_digest(command),
            "payload": command,
        }
    )


def _with_simulation_target(
    artifacts: tuple[bytes, bytes, bytes, bytes, bytes], target: str
) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    changed = list(artifacts)
    strategy = json.loads(changed[2])
    strategy["positions"][0]["target_quantity"] = target
    changed[2] = _canonical(strategy)
    scenario = json.loads(changed[4])
    scenario["strategy_sha256"] = hashlib.sha256(changed[2]).hexdigest()
    changed[4] = _canonical(scenario)
    return tuple(changed)


def _with_complete_simulation_bound(
    artifacts: tuple[bytes, bytes, bytes, bytes, bytes],
    *,
    price: str | None = None,
    quantity: str | None = None,
) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    """Rebind every affected canonical artifact after an audit-bound mutation."""

    assert (price is None) != (quantity is None)
    changed = list(artifacts)
    strategy = json.loads(changed[2])
    scenario = json.loads(changed[4])
    if price is not None:
        for field in ("ask", "bid", "close", "high", "low", "open"):
            scenario["events"][0][field] = price
    else:
        assert quantity is not None
        strategy["positions"][0]["target_quantity"] = quantity
        scenario["events"][0]["volume"] = quantity
        scenario["liquidity_limit"] = quantity
        changed[2] = _canonical(strategy)

    market_rows = [
        {
            "close": event["close"],
            "high": event["high"],
            "low": event["low"],
            "open": event["open"],
            "open_time": event["event_time"],
            "volume": event["volume"],
        }
        for event in scenario["events"]
    ]
    changed[3] = b"".join(_canonical(row) + b"\n" for row in market_rows)
    catalog = json.loads(changed[1])
    catalog["canonical_rows_sha256"] = hashlib.sha256(
        _canonical(market_rows)
    ).hexdigest()
    changed[1] = _canonical(catalog)
    scenario["catalog_sha256"] = hashlib.sha256(changed[1]).hexdigest()
    scenario["market_data_sha256"] = hashlib.sha256(changed[3]).hexdigest()
    scenario["strategy_sha256"] = hashlib.sha256(changed[2]).hexdigest()
    changed[4] = _canonical(scenario)
    return tuple(changed)


def _assert_complete_envelope_rejects_before_nautilus(
    launcher_module,
    monkeypatch: pytest.MonkeyPatch,
    artifacts: tuple[bytes, bytes, bytes, bytes, bytes],
) -> None:
    request = _simulation_request(artifacts).model_dump(mode="json")
    entered_nautilus = False

    def no_nautilus_run(_fixture: dict[str, object]) -> dict[str, object]:
        nonlocal entered_nautilus
        entered_nautilus = True
        raise AssertionError("invalid simulation reached Nautilus")

    monkeypatch.setattr(launcher_module, "validated_request", lambda *_args, **_kwargs: request)
    monkeypatch.setattr(
        launcher_module,
        "validated_input_artifacts",
        lambda *_args, **_kwargs: artifacts,
    )
    monkeypatch.setattr(
        launcher_module, "_run_nautilus_simulation_fixture", no_nautilus_run
    )

    with pytest.raises(SystemExit, match="1"):
        launcher_module.main(
            ["--profile", "execution-simulation", "request.json", "request.sha256"]
        )

    assert entered_nautilus is False


def test_launcher_accepts_only_hash_bound_canonical_run_backtest(
    launcher_module, tmp_path: Path
) -> None:
    request = canonical_json_bytes(_request())
    request_path = tmp_path / "request.json"
    sidecar_path = tmp_path / "request.sha256"
    request_path.write_bytes(request)
    sidecar_path.write_text(hashlib.sha256(request).hexdigest() + "\n", encoding="ascii")

    accepted = launcher_module.validated_request(request_path, sidecar_path)

    assert accepted["payload"]["command_type"] == "RunBacktest"


def test_launcher_rejects_request_digest_drift(launcher_module, tmp_path: Path) -> None:
    request = canonical_json_bytes(_request())
    request_path = tmp_path / "request.json"
    sidecar_path = tmp_path / "request.sha256"
    request_path.write_bytes(request)
    sidecar_path.write_text("0" * 64 + "\n", encoding="ascii")

    with pytest.raises(ValueError, match="digest"):
        launcher_module.validated_request(request_path, sidecar_path)


def test_launcher_reads_only_the_four_hash_bound_input_artifacts(
    launcher_module, tmp_path: Path
) -> None:
    artifact_values = (
        ("engine_configuration", b'{"mode":"zero-order"}\n', "application/json"),
        ("instrument_catalog", b'{"schema_version":"market-dataset-manifest-v1"}\n', "application/json"),
        ("strategy_configuration", b'{"positions":[]}\n', "application/json"),
        ("market_data", b'{"close":"1"}\n', "application/jsonl"),
    )
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    references: list[ArtifactReference] = []
    for index, (name, value, media_type) in enumerate(artifact_values, start=1):
        digest = hashlib.sha256(value).hexdigest()
        extension = ".jsonl" if media_type == "application/jsonl" else ".json"
        (artifact_root / f"{name}-{digest}{extension}").write_bytes(value)
        references.append(
            ArtifactReference(
                artifact_id=UUID(f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111"),
                sha256=digest,
                media_type=media_type,
            )
        )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    envelope = _request().model_copy(
        update={
            "config_digest": payload_digest(
                {
                    "engine_configuration": command.engine_configuration,
                    "instrument_catalog": command.instrument_catalog,
                    "strategy_configuration": command.strategy_configuration,
                }
            ),
            "payload_digest": payload_digest(command),
            "payload": command,
        }
    )

    loaded = launcher_module.validated_input_artifacts(
        envelope.model_dump(mode="json"), artifact_root
    )

    assert loaded == tuple(value for _name, value, _media_type in artifact_values)


def test_launcher_simulation_profile_reads_five_inputs_and_binds_stdout_event(
    launcher_module, tmp_path: Path
) -> None:
    artifact_names = (
        "engine_configuration",
        "instrument_catalog",
        "strategy_configuration",
        "market_data",
        "simulation_scenario",
    )
    artifact_values = _simulation_fixture("event-digest")
    artifact_root = tmp_path / "simulation-artifacts"
    artifact_root.mkdir()
    envelope = _simulation_request(artifact_values)
    assert isinstance(envelope.payload, RunBacktestSimulation)
    for name, value, reference in zip(
        artifact_names,
        artifact_values,
        (
            envelope.payload.engine_configuration,
            envelope.payload.instrument_catalog,
            envelope.payload.strategy_configuration,
            envelope.payload.market_data,
            envelope.payload.simulation_scenario,
        ),
        strict=True,
    ):
        media_type = reference.media_type
        digest = hashlib.sha256(value).hexdigest()
        extension = ".jsonl" if media_type == "application/jsonl" else ".json"
        (artifact_root / f"{name}-{digest}{extension}").write_bytes(value)
    raw_request = canonical_json_bytes(envelope)
    request_path = tmp_path / "simulation-request.json"
    sidecar_path = tmp_path / "simulation-request.sha256"
    request_path.write_bytes(raw_request)
    sidecar_path.write_text(
        hashlib.sha256(raw_request).hexdigest() + "\n", encoding="ascii"
    )

    accepted = launcher_module.validated_request(
        request_path,
        sidecar_path,
        profile="execution-simulation",
    )
    artifacts = launcher_module.validated_input_artifacts(
        accepted,
        artifact_root,
        profile="execution-simulation",
    )
    fixture = launcher_module.validate_simulation_fixture_inputs(accepted, artifacts)
    result = launcher_module.run_execution_simulation(fixture)
    event = launcher_module._simulation_event(accepted, artifacts, result)
    parsed = EngineEventEnvelope.model_validate_json(canonical_json_bytes(event))

    assert len(artifacts) == 5
    assert parsed.payload.event_type == "NautilusBacktestSimulationCompleted"
    attributes = {item.name: item.value for item in parsed.payload.attributes}
    assert attributes == {
        "average_entry_price": "100",
        "event_digest": result["event_digest"],
        "fees": "0.1",
        "filled_quantity": "1",
        "input_artifacts_sha256": hashlib.sha256(
            canonical_json_bytes(
                {
                    name: hashlib.sha256(value).hexdigest()
                    for name, value in zip(artifact_names, artifacts, strict=True)
                }
            )
        ).hexdigest(),
        "iterations": 1,
        "position_quantity": "1",
        "realized_pnl": "0",
        "remaining_quantity": "0",
        "scenario_digest": envelope.payload.simulation_scenario.sha256,
        "scenario_id": "event-digest",
        "stop_take_profit_precedence": "stop-first",
        "total_events": 2,
        "total_fills": 1,
        "total_orders": 1,
        "total_positions": 1,
        "unrealized_pnl": "1",
    }
    with pytest.raises(ValueError, match="RunBacktest"):
        launcher_module.validated_request(request_path, sidecar_path)


def test_launcher_rejects_duplicate_simulation_artifact_references(
    launcher_module, tmp_path: Path
) -> None:
    envelope = _request().model_dump(mode="json")
    payload = dict(envelope["payload"])
    payload["command_type"] = "RunBacktestSimulation"
    payload["simulation_scenario"] = payload["engine_configuration"]
    envelope["payload"] = payload
    envelope["payload_digest"] = hashlib.sha256(
        canonical_json_bytes(payload)
    ).hexdigest()
    raw_request = canonical_json_bytes(envelope)
    request_path = tmp_path / "duplicate-simulation-request.json"
    sidecar_path = tmp_path / "duplicate-simulation-request.sha256"
    request_path.write_bytes(raw_request)
    sidecar_path.write_text(
        hashlib.sha256(raw_request).hexdigest() + "\n", encoding="ascii"
    )

    with pytest.raises(ValueError, match="duplicate artifact"):
        launcher_module.validated_request(
            request_path,
            sidecar_path,
            profile="execution-simulation",
        )


@pytest.mark.parametrize(
    ("scenario_id", "expected"),
    [
        (
            "long-accounting",
            {
                "filled_quantity": "2",
                "fees": "0.2",
                "position_quantity": "2",
                "remaining_quantity": "0",
                "unrealized_pnl": "2",
            },
        ),
        (
            "short-accounting",
            {
                "filled_quantity": "-2",
                "fees": "0.198",
                "position_quantity": "-2",
                "remaining_quantity": "0",
                "unrealized_pnl": "-4",
            },
        ),
        (
            "partial-fill",
            {
                "filled_quantity": "1",
                "position_quantity": "1",
                "remaining_quantity": "2",
                "total_fills": 1,
            },
        ),
        (
            "same-bar-stop-take-profit",
            {
                "position_quantity": "0",
                "realized_pnl": "-2",
                "stop_take_profit_precedence": "stop-first",
                "total_fills": 2,
                "total_orders": 2,
            },
        ),
        (
            "stale-quote",
            {
                "filled_quantity": "0",
                "position_quantity": "0",
                "remaining_quantity": "1",
                "total_fills": 0,
            },
        ),
        (
            "zero-liquidity",
            {
                "filled_quantity": "0",
                "position_quantity": "0",
                "remaining_quantity": "1",
                "total_fills": 0,
            },
        ),
        (
            "session-boundary",
            {
                "average_entry_price": "102",
                "filled_quantity": "1",
                "iterations": 2,
                "position_quantity": "1",
            },
        ),
        (
            "event-digest",
            {
                "event_digest": "30b6de71b9d7a69f8e1038d1584efecd3c2bdfe4a944303a479c4680f078cd33",
                "total_events": 2,
            },
        ),
    ],
)
def test_execution_simulation_covers_the_fixed_scenario_matrix(
    launcher_module, scenario_id: str, expected: dict[str, object]
) -> None:
    artifacts = _simulation_fixture(scenario_id)
    request = _simulation_request(artifacts).model_dump(mode="json")

    fixture = launcher_module.validate_simulation_fixture_inputs(request, artifacts)
    result = launcher_module.run_execution_simulation(fixture)

    assert result["scenario_id"] == scenario_id
    assert result.items() >= expected.items()
    assert all(not isinstance(value, float) for value in result.values())


@pytest.mark.parametrize("scenario_id", _SCENARIO_IDS)
def test_each_complete_simulation_envelope_is_accepted_and_emitted(
    launcher_module, scenario_id: str
) -> None:
    artifacts = _simulation_fixture(scenario_id)
    envelope = _simulation_request(artifacts)
    raw = canonical_json_bytes(envelope)
    accepted = launcher_module._validate_request(
        json.loads(raw), raw, profile="execution-simulation"
    )
    fixture = launcher_module.validate_simulation_fixture_inputs(accepted, artifacts)
    result = launcher_module.run_execution_simulation(fixture)
    event = launcher_module._simulation_event(accepted, artifacts, result)

    parsed = EngineEventEnvelope.model_validate_json(canonical_json_bytes(event))
    attributes = {item.name: item.value for item in parsed.payload.attributes}

    assert parsed.payload.event_type == "NautilusBacktestSimulationCompleted"
    assert attributes["scenario_id"] == scenario_id
    assert set(attributes) == {
        "average_entry_price",
        "event_digest",
        "fees",
        "filled_quantity",
        "input_artifacts_sha256",
        "iterations",
        "position_quantity",
        "realized_pnl",
        "remaining_quantity",
        "scenario_digest",
        "scenario_id",
        "stop_take_profit_precedence",
        "total_events",
        "total_fills",
        "total_orders",
        "total_positions",
        "unrealized_pnl",
    }


def test_execution_simulation_profile_rejects_zero_order_envelope(
    launcher_module,
) -> None:
    envelope = _request()
    raw = canonical_json_bytes(envelope)

    with pytest.raises(ValueError, match="RunBacktestSimulation"):
        launcher_module._validate_request(
            json.loads(raw), raw, profile="execution-simulation"
        )


@pytest.mark.parametrize(
    ("scenario_id", "mutation"),
    [
        ("long-accounting", "zero-liquidity"),
        ("short-accounting", "closed-session"),
        ("partial-fill", "zero-liquidity"),
        ("same-bar-stop-take-profit", "closed-session"),
        ("stale-quote", "closed-session"),
        ("zero-liquidity", "stale-quote"),
        ("session-boundary", "zero-liquidity"),
        ("event-digest", "zero-liquidity"),
    ],
)
def test_scenario_identifiers_reject_semantic_precondition_violations(
    launcher_module, scenario_id: str, mutation: str
) -> None:
    artifacts = list(_simulation_fixture(scenario_id))
    scenario = json.loads(artifacts[4])
    if mutation == "zero-liquidity":
        scenario["liquidity_limit"] = "0"
    elif mutation == "closed-session":
        scenario["events"][-1]["session_open"] = False
    else:
        scenario["events"][0]["quote_time"] = "2026-08-05T11:58:00Z"
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="semantic precondition"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )


def test_same_bar_long_requires_stop_below_executable_entry_below_take(
    launcher_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = list(_simulation_fixture("same-bar-stop-take-profit"))
    scenario = json.loads(artifacts[4])
    scenario["stop_price"] = "102"
    scenario["take_profit_price"] = "98"
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="semantic precondition"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )

    _assert_complete_envelope_rejects_before_nautilus(
        launcher_module, monkeypatch, bound
    )


@pytest.mark.parametrize(
    ("value", "maximum", "label"),
    [
        ("17014118346047", "17014118346046", "price"),
        ("34028236692094", "34028236692093", "quantity"),
    ],
)
def test_nautilus_fixed_point_bound_is_fail_closed(
    launcher_module, value: str, maximum: str, label: str
) -> None:
    with pytest.raises(ValueError, match="Nautilus fixed-point"):
        launcher_module._require_nautilus_fixed_point_bound(
            Decimal(value), maximum=Decimal(maximum), label=label
        )


@pytest.mark.parametrize(
    ("price", "quantity"),
    [
        ("17014118346047", None),
        (None, "34028236692094"),
    ],
)
def test_complete_hash_rebound_over_limit_envelopes_reject_before_nautilus(
    launcher_module,
    monkeypatch: pytest.MonkeyPatch,
    price: str | None,
    quantity: str | None,
) -> None:
    bound = _with_complete_simulation_bound(
        _simulation_fixture("event-digest"), price=price, quantity=quantity
    )

    with pytest.raises(ValueError, match="Nautilus fixed-point"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )

    _assert_complete_envelope_rejects_before_nautilus(
        launcher_module, monkeypatch, bound
    )


@pytest.mark.parametrize(
    ("price", "quantity"),
    [
        ("17014118346046", None),
        (None, "34028236692093"),
    ],
)
def test_complete_hash_rebound_exact_fixed_point_limits_are_representable(
    launcher_module, price: str | None, quantity: str | None
) -> None:
    artifacts = _with_complete_simulation_bound(
        _simulation_fixture("event-digest"), price=price, quantity=quantity
    )

    fixture = launcher_module.validate_simulation_fixture_inputs(
        _simulation_request(artifacts).model_dump(mode="json"), artifacts
    )

    assert fixture["target_quantity"] != 0


def test_unrepresentable_slipped_entry_rejects_before_nautilus(
    launcher_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = list(_simulation_fixture("event-digest"))
    scenario = json.loads(artifacts[4])
    scenario["events"][0]["ask"] = "17014118346046"
    scenario["events"][0]["bid"] = "17014118346046"
    scenario["slippage_bps"] = "1"
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="Nautilus fixed-point"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )

    _assert_complete_envelope_rejects_before_nautilus(
        launcher_module, monkeypatch, bound
    )


def test_changed_strategy_digest_rejects_before_nautilus(
    launcher_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = list(_simulation_fixture("event-digest"))
    scenario = json.loads(artifacts[4])
    scenario["strategy_sha256"] = "0" * 64
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="strategy binding"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )

    _assert_complete_envelope_rejects_before_nautilus(
        launcher_module, monkeypatch, bound
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target", "123456789012345678901234567890123456789"),
        ("fee", "0.000000000000000000000000000000000000001"),
    ],
)
def test_simulation_rejects_decimal_coefficient_or_exponent_beyond_bound(
    launcher_module, field: str, value: str
) -> None:
    artifacts = _simulation_fixture("partial-fill")
    if field == "target":
        bound = _with_simulation_target(artifacts, value)
    else:
        changed = list(artifacts)
        scenario = json.loads(changed[4])
        scenario["fee_rate"] = value
        changed[4] = _canonical(scenario)
        bound = tuple(changed)

    with pytest.raises(ValueError, match="decimal bound"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )


def test_simulation_decimal_arithmetic_is_isolated_from_ambient_context(
    launcher_module,
) -> None:
    target = "34028236692093"
    artifacts = _with_simulation_target(_simulation_fixture("partial-fill"), target)
    request = _simulation_request(artifacts).model_dump(mode="json")

    with localcontext(Context(prec=6)):
        fixture = launcher_module.validate_simulation_fixture_inputs(request, artifacts)
        result = launcher_module.run_execution_simulation(fixture)

    assert result["filled_quantity"] == "1"
    assert result["remaining_quantity"] == "34028236692092"


def test_execution_simulation_replay_is_byte_identical(launcher_module) -> None:
    artifacts = _simulation_fixture("event-digest")
    request = _simulation_request(artifacts).model_dump(mode="json")
    fixture = launcher_module.validate_simulation_fixture_inputs(request, artifacts)

    first = launcher_module._simulation_event(
        request, artifacts, launcher_module.run_execution_simulation(fixture)
    )
    second = launcher_module._simulation_event(
        request, artifacts, launcher_module.run_execution_simulation(fixture)
    )

    assert launcher_module._canonical_json_bytes(first) == launcher_module._canonical_json_bytes(
        second
    )


@pytest.mark.parametrize("scenario_id", _SCENARIO_IDS)
def test_each_changed_scenario_identifier_contract_is_rejected_before_execution(
    launcher_module, scenario_id: str
) -> None:
    artifacts = list(_simulation_fixture(scenario_id))
    scenario = json.loads(artifacts[4])
    scenario["schema_version"] = "changed-scenario-contract"
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="scenario"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("float", "decimal"),
        ("unknown-key", "fields"),
        ("provider", "fields"),
        ("module", "fields"),
        ("writable-path", "fields"),
        ("catalog-drift", "catalog"),
        ("outside-window", "window"),
        ("instrument-precision", "precision"),
        ("unknown-precedence", "precedence"),
    ],
)
def test_simulation_scenario_forbidden_inputs_fail_closed(
    launcher_module, mutation: str, message: str
) -> None:
    artifacts = list(_simulation_fixture("event-digest"))
    scenario = json.loads(artifacts[4])
    if mutation == "float":
        scenario["fee_rate"] = 0.001
    elif mutation == "unknown-key":
        scenario["unexpected"] = True
    elif mutation == "provider":
        scenario["execution_provider"] = "exchange"
    elif mutation == "module":
        scenario["strategy_module"] = "arbitrary.module"
    elif mutation == "writable-path":
        scenario["output_path"] = "/tmp/result.json"
    elif mutation == "catalog-drift":
        scenario["catalog_sha256"] = "0" * 64
    elif mutation == "outside-window":
        scenario["events"][0]["event_time"] = "2026-08-05T13:00:00Z"
    elif mutation == "instrument-precision":
        scenario["events"][0]["ask"] = "100.001"
    else:
        scenario["stop_take_profit_precedence"] = "take-profit-first"
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match=message):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )


def test_simulation_scenario_rejects_duplicate_json_keys(launcher_module) -> None:
    artifacts = list(_simulation_fixture("event-digest"))
    artifacts[4] = artifacts[4].replace(
        b'{"catalog_sha256":', b'{"scenario_id":"event-digest","catalog_sha256":'
    )
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="duplicate"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )


def test_launcher_accepts_only_a_zero_order_04a_catalog_and_04b_target(
    launcher_module,
) -> None:
    configuration = json.dumps(
        {
            "execution_mode": "zero-order",
            "run_analysis": False,
            "schema_version": "nautilus-backtest-engine-config-v1",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    market_data = (
        b'{"close":"101.00","high":"102.00","low":"99.00","open":"100.00",'
        b'"open_time":"2026-08-05T12:00:00Z","volume":"12.500000"}\n'
    )
    catalog = json.dumps(
        {
            "canonical_rows_sha256": hashlib.sha256(
                b"[" + market_data[:-1] + b"]"
            ).hexdigest(),
            "content_digest": "a" * 64,
            "continuity": {"duplicate_report": [], "gap_report": [], "timeframe": "1m"},
            "fetched_at": "2026-08-05T12:01:00Z",
            "first_event_at": "2026-08-05T12:00:00Z",
            "importer_version": "fixture-catalog-v1",
            "instrument": {"product_type": "crypto_spot", "symbol": "BTCUSDT", "venue": "BINANCE"},
            "known_at": "2026-08-05T12:01:00Z",
            "last_event_at": "2026-08-05T12:00:00Z",
            "normalization_version": "market-normalization-v1",
            "observed_at": "2026-08-05T12:01:00Z",
            "parquet_sha256": "b" * 64,
            "provider": "deterministic-fixture-v1",
            "provenance_schema_version": "market-data-v1",
            "raw_evidence_sha256": "c" * 64,
            "row_count": 1,
            "schema_version": "market-dataset-manifest-v1",
            "snapshot_schema_version": "market-snapshot-v1",
            "timeframe": "1m",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    target = json.dumps(
        {
            "effective_at": "2026-08-05T12:00:00Z",
            "positions": [],
            "schema_version": "1.0.0",
            "source_signal_ids": ["22222222-2222-4222-8222-222222222222"],
            "target_id": "11111111-1111-4111-8111-111111111111",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    launcher_module.validate_zero_order_fixture_inputs(
        (configuration, catalog, target, market_data)
    )

    with pytest.raises(ValueError, match="zero target"):
        launcher_module.validate_zero_order_fixture_inputs(
            (configuration, catalog, target.replace(b"[]", b'[{}]'), market_data)
        )
    with pytest.raises(ValueError, match="strategy target"):
        launcher_module.validate_zero_order_fixture_inputs(
            (
                configuration,
                catalog,
                target.replace(b"2026-08-05T12:00:00Z", b"not-a-timestamp-Z"),
                market_data,
            )
        )
    with pytest.raises(ValueError, match="canonical rows"):
        launcher_module.validate_zero_order_fixture_inputs(
            (
                configuration,
                catalog,
                target,
                market_data.replace(b'"101.00"', b'"999.00"'),
            )
        )
