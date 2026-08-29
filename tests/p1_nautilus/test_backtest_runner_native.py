from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess

from test_instrument_factory_native import exact_g1_command, exact_g1_runtime


ROOT = Path(__file__).parents[2]
RUNNER = ROOT / "engines/nautilus/runtime_v1/backtest_runner.py"
SESSION = ROOT / "engines/nautilus/runtime_v1/session.py"


def test_runner_source_is_fixed_profile_and_has_no_io_or_network() -> None:
    sources = (RUNNER.read_text(encoding="utf-8"), SESSION.read_text(encoding="utf-8"))
    trees = tuple(ast.parse(source) for source in sources)
    imported = {
        (node.module or "").split(".")[0]
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert imported.isdisjoint(
        {"asyncio", "http", "os", "pathlib", "requests", "socket", "subprocess", "urllib"}
    )
    assert "BacktestEngineConfig(" in sources[1]
    for setting in (
        "load_state=False",
        "run_analysis=False",
        "save_state=False",
        "use_position_ids=True",
        "use_random_ids=False",
        "allow_cash_borrowing=False",
        "bar_execution=False",
    ):
        assert setting in sources[1]


def test_exact_g1_runner_is_deterministic_scalar_only_and_disposes() -> None:
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
from decimal import Decimal
from pathlib import Path
import sys
import warnings
warnings.filterwarnings("ignore", message="Timestamp.utcnow is deprecated.*")
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import nautilus_trader
from runtime_v1.backtest_runner import BacktestRun, BacktestRunError, run_backtest
from runtime_v1.input_loader import ArtifactReference, RunBacktestRequest, RuntimeInputs
from runtime_v1.session import BacktestEngine, BacktestEngineSession, create_session, dispose_session

assert nautilus_trader.__version__ == "1.231.0"
assert Path(nautilus_trader.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())
assert Path(sys.modules[BacktestEngine.__module__].__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())

def canonical(value):
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()

def freeze(value):
    if type(value) is dict:
        return tuple((key, freeze(item)) for key, item in sorted(value.items()))
    if type(value) is list:
        return tuple(freeze(item) for item in value)
    return value

catalog_raw = open(sys.argv[3], "rb").read()
catalog = tuple(sorted(json.loads(catalog_raw).items()))
configuration = tuple(sorted(json.loads(open(sys.argv[4], "rb").read()).items()))
schedule = freeze(json.loads(open(sys.argv[5], "rb").read()))
rows = [
    {"ask":"100","bid":"99","close":"100","event_time":"2026-08-05T12:00:00Z","high":"101","low":"98","open":"99","quote_time":"2026-08-05T12:00:00Z","sequence":1,"volume":"1000000"},
    {"ask":"102","bid":"101","close":"102","event_time":"2026-08-05T12:01:00Z","high":"103","low":"100","open":"101","quote_time":"2026-08-05T12:01:00Z","sequence":2,"volume":"1000000"},
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
    end_time="2026-08-05T12:01:00Z",
)
inputs = RuntimeInputs(request, configuration, catalog, schedule, raw)
first = run_backtest(inputs)
second = run_backtest(inputs)
assert type(first) is BacktestRun and first == second
assert first.strategy_state == "COMPLETED"
assert first.processed_target_ids == (
    "11111111-1111-4111-8111-111111111111",
    "44444444-4444-4444-8444-444444444444",
)
assert first.total_orders == 2 and first.total_positions == 1
assert first.order_count == first.fill_count == 2
assert first.pending_order_ids == first.rejected_order_ids == ()
assert first.position_quantity == "0"
assert first.position_average_entry == "0"
assert first.position_realized_pnl == "9990.00999"
assert first.position_unrealized_pnl == "0"
assert first.final_market_price == "102"
assert first.account_count == 1
assert first.balance_currencies == ("BTC", "USDT")
assert len(first.native_order_ids) == len(set(first.native_order_ids)) == 2
assert len(first.native_fill_ids) == len(set(first.native_fill_ids)) == 2
assert first.last_market_timestamp > 0
native_kinds = [fact.kind for fact in first.native_facts]
assert native_kinds == [
    "quote",
    "target_planned",
    "target_quantity_planned",
    "order_submitted",
    "order_filled",
    "quote",
    "target_planned",
    "target_quantity_planned",
    "order_submitted",
    "order_filled",
    "stopped",
], native_kinds
assert all(
    type(value) in {str, int, type(None)}
    for fact in first.native_facts
    for _, value in fact.attributes
)
facts = [(fact.kind, dict(fact.attributes)) for fact in first.native_facts]
plans = [value for kind, value in facts if kind == "target_planned"]
submitted = [value for kind, value in facts if kind == "order_submitted"]
fills = [value for kind, value in facts if kind == "order_filled"]
assert submitted[1]["quantity"] == plans[1]["delta"].removeprefix("-")
assert submitted[1]["quantity"] == fills[1]["quantity"]
assert all(
    {"client_order_id", "trade_id", "side", "quantity", "price", "commission", "commission_currency", "ts_event"} <= value.keys()
    for value in fills
)
assert all(
    Decimal(total) == Decimal(locked) + Decimal(free)
    for _, total, locked, free in first.balance_facts
)
assert all(
    type(value) in {bool, int, str, tuple}
    for value in (
        first.engine_version,
        first.iterations,
        first.total_events,
        first.order_facts,
        first.balance_facts,
        first.commission_facts,
    )
)

class FailingEngine:
    def __init__(self, cleanup=None):
        self.cleanup = cleanup
        self.disposed = 0
    def run(self):
        raise ValueError("engine")
    def dispose(self):
        self.disposed += 1
        if self.cleanup is not None:
            raise self.cleanup

failing_engine = FailingEngine()
try:
    run_backtest(inputs, lambda *_: BacktestEngineSession(failing_engine, None, None))
except ValueError as error:
    assert str(error) == "engine" and failing_engine.disposed == 1
else:
    raise AssertionError("engine failure was accepted")

combined_engine = FailingEngine(RuntimeError("cleanup"))
try:
    run_backtest(inputs, lambda *_: BacktestEngineSession(combined_engine, None, None))
except ExceptionGroup as error:
    assert [str(item) for item in error.exceptions] == ["engine", "cleanup"]
    assert combined_engine.disposed == 1
else:
    raise AssertionError("engine and cleanup failure was accepted")

class RejectingSession:
    def __init__(self, actual):
        self.engine = actual.engine
        self.strategy = actual.strategy
        self.batch = actual.batch
    def run(self):
        self.engine.run()
        self.strategy._fail("injected_rejection")
    def dispose(self, primary=None):
        dispose_session(self.engine, primary)

try:
    run_backtest(inputs, lambda *args: RejectingSession(create_session(*args)))
except BacktestRunError as error:
    assert "strategy" in str(error)
else:
    raise AssertionError("strategy rejection was accepted")

class FakeEngine:
    def __init__(self, failure=None):
        self.failure = failure
        self.disposed = 0
    def dispose(self):
        self.disposed += 1
        if self.failure is not None:
            raise self.failure

ok = FakeEngine()
dispose_session(ok)
assert ok.disposed == 1
primary = ValueError("primary")
try:
    dispose_session(FakeEngine(RuntimeError("cleanup")), primary)
except ExceptionGroup as error:
    assert [type(item).__name__ for item in error.exceptions] == ["ValueError", "RuntimeError"]
    assert [str(item) for item in error.exceptions] == ["primary", "cleanup"]
else:
    raise AssertionError("combined failure was accepted")
try:
    dispose_session(FakeEngine(RuntimeError("cleanup")))
except BacktestRunError as error:
    assert str(error) == "native session disposal failed"
else:
    raise AssertionError("cleanup failure was accepted")

print(json.dumps({
    "account_events": first.account_event_count,
    "balances": first.balance_facts,
    "commissions": first.commission_facts,
    "orders": first.order_facts,
}, separators=(",", ":"), sort_keys=True))
'''
        completed = subprocess.run(
            exact_g1_command(
                root,
                site_packages,
                script,
                (ROOT / "tests/fixtures/p1_nautilus/contracts/instrument-catalog.json", "/inputs/instrument-catalog.json"),
                (ROOT / "tests/fixtures/p1_nautilus/contracts/engine-configuration.json", "/inputs/engine-configuration.json"),
                (ROOT / "tests/fixtures/p1_nautilus/contracts/target-schedule.json", "/inputs/target-schedule.json"),
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
    assert observed["account_events"] >= 3
    assert len(observed["orders"]) == 2
    assert observed["balances"] == [
        ["BTC", "0", "0", "0"],
        ["USDT", "1007982.01798201", "0", "1007982.01798201"],
    ]
    assert observed["commissions"] == [["USDT", "2007.99200799"]]
