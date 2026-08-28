from __future__ import annotations

import json
from pathlib import Path
import subprocess

from test_instrument_factory_native import exact_g1_command, exact_g1_runtime


ROOT = Path(__file__).parents[2]


def test_exact_g1_executes_serial_targets_and_fails_closed_on_callbacks() -> None:
    with exact_g1_runtime() as runtime:
        if runtime is None:
            assert "nautilus_trader" not in subprocess.run(
                ["uv", "run", "python", "-c", "import sys;print(' '.join(sys.path))"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            return
        root, site_packages = runtime
        script = r'''
import hashlib
import json
from pathlib import Path
import sys
import warnings
warnings.filterwarnings("ignore", message="Timestamp.utcnow is deprecated.*")
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from runtime_v1.input_loader import ArtifactReference, RunBacktestRequest, RuntimeInputs
from runtime_v1.instrument_factory import build_instrument
from runtime_v1.market_data_loader import load_market_data
from runtime_v1.target_strategy import StrategyEventCollector, StrategyState, TargetStrategy, TargetStrategyConfig
assert nautilus_trader.__version__ == "1.231.0"
assert Path(nautilus_trader.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())

def canonical(value):
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()

catalog_raw = open(sys.argv[3], "rb").read()
catalog = tuple(sorted(json.loads(catalog_raw).items()))
configuration = tuple(sorted(json.loads(open(sys.argv[4], "rb").read()).items()))
rows = [
    {"ask":"100","bid":"99","close":"100","event_time":"2026-08-05T12:00:00Z","high":"101","low":"98","open":"99","quote_time":"2026-08-05T12:00:00Z","sequence":1,"volume":"10"},
    {"ask":"102","bid":"101","close":"102","event_time":"2026-08-05T12:01:00Z","high":"103","low":"100","open":"101","quote_time":"2026-08-05T12:01:00Z","sequence":2,"volume":"10"},
    {"ask":"104","bid":"103","close":"104","event_time":"2026-08-05T12:02:00Z","high":"105","low":"102","open":"103","quote_time":"2026-08-05T12:02:00Z","sequence":3,"volume":"10"},
]
raw = b"".join(canonical(row) + b"\n" for row in rows)
request = RunBacktestRequest(
    message_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    correlation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    causation_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    engine_run_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    stream_sequence=1,
    event_time="2026-08-05T12:00:00Z",
    initialization_time="2026-08-05T12:00:00Z",
    schema_version="1.0.0",
    producer_identity="worker-authority-1",
    source_commit="0123456789abcdef0123456789abcdef01234567",
    config_digest="1" * 64,
    payload_digest="2" * 64,
    command_type="RunBacktest",
    engine_configuration=ArtifactReference("11111111-1111-4111-8111-111111111111", "3" * 64, "application/json"),
    instrument_catalog=ArtifactReference("22222222-2222-4222-8222-222222222222", hashlib.sha256(catalog_raw).hexdigest(), "application/json"),
    strategy_configuration=ArtifactReference("33333333-3333-4333-8333-333333333333", "4" * 64, "application/json"),
    market_data=ArtifactReference("44444444-4444-4444-8444-444444444444", hashlib.sha256(raw).hexdigest(), "application/jsonl"),
    start_time="2026-08-05T12:00:00Z",
    end_time="2026-08-05T12:02:00Z",
)
inputs = RuntimeInputs(request, configuration, catalog, (), raw)
instrument = build_instrument(catalog)
batch = load_market_data(inputs, instrument)

def strategy():
    return TargetStrategy(
        TargetStrategyConfig(
            instrument_id=instrument.id,
            bar_type=batch.data[1].bar_type,
            target_schedule=(
                ("11111111-1111-4111-8111-111111111111", ("22222222-2222-4222-8222-222222222222",), "2026-08-05T12:00:00Z", "0.0001"),
                ("44444444-4444-4444-8444-444444444444", ("33333333-3333-4333-8333-333333333333",), "2026-08-05T12:01:00Z", "0"),
            ),
            fee_rate="0.001",
            leverage="1",
            min_notional=str(instrument.min_notional.as_decimal()),
            min_quantity=str(instrument.min_quantity.as_decimal()),
            step_size=str(instrument.size_increment.as_decimal()),
        )
    )

def engine_for(candidate, data, latency_model=None):
    engine = BacktestEngine(BacktestEngineConfig(load_state=False, logging=LoggingConfig(bypass_logging=True), run_analysis=False, save_state=False))
    engine.add_venue(Venue("BINANCE"), OmsType.NETTING, AccountType.CASH, [Money.from_str("1000000 USDT")], bar_execution=False, latency_model=latency_model)
    engine.add_instrument(instrument)
    engine.add_strategy(candidate)
    engine.add_data(list(data))
    return engine

candidate = strategy()
engine = engine_for(candidate, batch.data)
try:
    engine.run()
    orders = tuple(engine.cache.orders())
    positions = tuple(engine.cache.positions())
    completed = {
        "filled": [str(order.filled_qty) for order in orders],
        "order_count": len(orders),
        "position": str(positions[0].quantity) if positions else "0",
        "planned_order_quantity": candidate.planned_order_quantity,
        "state": candidate.state,
    }
    candidate.on_bar(batch.data[3])
    candidate.on_quote_tick(batch.data[2])
    duplicate_order_count = len(tuple(engine.cache.orders()))
    candidate.on_stop()
    collector_records = candidate.collector.snapshot()
    collector_kinds = [kind for kind, _ in collector_records]
    planned_quantities = [
        value
        for kind, value in collector_records
        if kind == "target_quantity_planned"
    ]
    fill_facts = [value for kind, value in collector_records if kind == "order_filled"]
    submitted_facts = [
        value for kind, value in collector_records if kind == "order_submitted"
    ]
    scalar_snapshot = all(
        not type(value).__module__.startswith("nautilus_trader")
        for _, value in collector_records
    )
    fill = next(
        event
        for order in orders
        for event in order.events
        if type(event).__name__ == "OrderFilled"
    )
    candidate.on_order_filled(fill)
    inconsistent_state = candidate.state
    candidate.on_reset()
    reset_state = candidate.state
    reset_clean = (
        candidate.collector.snapshot() == ()
        and not candidate.pending_order
        and candidate.processed_target_ids == ()
    )
finally:
    engine.dispose()

pending = strategy()
pending_engine = engine_for(
    pending,
    batch.data[:2],
    LatencyModel(base_latency_nanos=120_000_000_000),
)
try:
    pending_engine.run()
    pending_orders = tuple(pending_engine.cache.orders())
    pending_order = pending_orders[0]
    pending_state = pending.state
    pending_filled = str(pending_order.filled_qty)
    pending_kinds = [kind for kind, _ in pending.collector.snapshot()]
    account = pending_engine.cache.account_for_venue(Venue("BINANCE"))
    rejection = OrderRejected(
        trader_id=pending.trader_id,
        strategy_id=pending.id,
        instrument_id=instrument.id,
        client_order_id=pending_order.client_order_id,
        account_id=account.id,
        reason="native-test-rejection",
        event_id=UUID4(),
        ts_event=batch.data[1].ts_event,
        ts_init=batch.data[1].ts_init,
    )
    pending.on_order_rejected(rejection)
    rejected_state = pending.state
finally:
    pending_engine.dispose()

print(json.dumps({
    "collector_kinds": collector_kinds,
    "fill_facts": [
        [fact.client_order_id, fact.trade_id, fact.side, fact.quantity, fact.price, fact.commission, fact.commission_currency, fact.ts_event]
        for fact in fill_facts
    ],
    "planned_quantities": planned_quantities,
    "completed": completed,
    "duplicate_order_count": duplicate_order_count,
    "inconsistent_state": inconsistent_state,
    "pending_filled": pending_filled,
    "pending_kinds": pending_kinds,
    "pending_state": pending_state,
    "rejected_state": rejected_state,
    "reset_clean": reset_clean,
    "reset_state": reset_state,
    "scalar_snapshot": scalar_snapshot,
    "submitted_facts": [
        [fact.client_order_id, fact.target_id, list(fact.source_signal_ids), fact.side, fact.quantity, fact.order_type]
        for fact in submitted_facts
    ],
}, separators=(",", ":"), sort_keys=True))
'''
        completed = subprocess.run(
            exact_g1_command(
                root,
                site_packages,
                script,
                (
                    ROOT / "tests/fixtures/p1_nautilus/contracts/instrument-catalog.json",
                    "/inputs/instrument-catalog.json",
                ),
                (
                    ROOT / "tests/fixtures/p1_nautilus/contracts/engine-configuration.json",
                    "/inputs/engine-configuration.json",
                ),
            ),
            cwd="/",
            env={},
            check=False,
            capture_output=True,
            text=True,
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    observed = json.loads(completed.stdout)
    assert observed["completed"] == {
        "filled": ["1.000000", "1.000000"],
        "order_count": 2,
        "position": "0.000000",
        "planned_order_quantity": "1",
        "state": "COMPLETED",
    }
    assert observed["planned_quantities"] == ["1", "1"]
    assert observed["duplicate_order_count"] == 2
    assert observed["inconsistent_state"] == "FAILED"
    assert observed["pending_state"] == "EXIT_ONLY"
    assert observed["pending_filled"] == "0.000000"
    assert "stop_pending" in observed["pending_kinds"]
    assert observed["rejected_state"] == "FAILED"
    assert observed["reset_state"] == "WAITING_FOR_TARGET"
    assert observed["reset_clean"] is True
    assert observed["scalar_snapshot"] is True
    assert observed["collector_kinds"].count("order_submitted") == 2
    assert observed["collector_kinds"].count("order_filled") == 2
    assert [
        kind
        for kind in observed["collector_kinds"]
        if kind in {"order_submitted", "order_filled"}
    ] == ["order_submitted", "order_filled", "order_submitted", "order_filled"]
    assert observed["collector_kinds"][-1] == "stopped"
    assert [
        kind
        for kind in observed["pending_kinds"]
        if kind in {"order_submitted", "order_filled", "stop_pending"}
    ] == ["order_submitted", "stop_pending"]
    assert [fact[1:] for fact in observed["submitted_facts"]] == [
        ["11111111-1111-4111-8111-111111111111", ["22222222-2222-4222-8222-222222222222"], "BUY", "1", "MARKET"],
        ["44444444-4444-4444-8444-444444444444", ["33333333-3333-4333-8333-333333333333"], "SELL", "1", "MARKET"],
    ]
    assert [fact[0] for fact in observed["fill_facts"]] == [
        fact[0] for fact in observed["submitted_facts"]
    ]
    assert [fact[2:] for fact in observed["fill_facts"]] == [
        ["BUY", "1", "100", "0", "USDT", 1785931200000000000],
        ["SELL", "1", "101", "0", "USDT", 1785931260000000000],
    ]
    assert len({fact[1] for fact in observed["fill_facts"]}) == 2
