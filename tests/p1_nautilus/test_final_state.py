from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import sys

import pytest


RUNTIME_PARENT = Path(__file__).parents[2] / "engines/nautilus"
sys.path.insert(0, str(RUNTIME_PARENT))

from runtime_v1.final_state import FinalStateError, validate_final_state  # noqa: E402
from test_instrument_factory_native import (  # noqa: E402
    exact_g1_command,
    exact_g1_runtime,
)


ROOT = Path(__file__).parents[2]


def _fact(kind: str, **values: str | int | None) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, attributes=tuple(values.items()))


def _inputs() -> SimpleNamespace:
    row = {
        "ask": "101",
        "bid": "100",
        "close": "101",
        "event_time": "2026-08-05T12:00:00Z",
        "high": "101",
        "low": "100",
        "open": "100",
        "quote_time": "2026-08-05T12:00:00Z",
        "sequence": 1,
        "volume": "1000",
    }
    return SimpleNamespace(
        engine_configuration=(
            ("starting_balance", "1000"),
            ("starting_currency", "USDT"),
        ),
        instrument_catalog=(
            ("base_currency", "BTC"),
            ("instrument_id", "BTCUSDT.BINANCE"),
            ("quote_currency", "USDT"),
        ),
        target_schedule=(
            ("schema_version", "nautilus-p1-target-schedule-v1"),
            (
                "targets",
                (
                    (
                        ("effective_at", "2026-08-05T12:00:00Z"),
                        ("positions", ()),
                        ("source_signal_ids", ("signal-1",)),
                        ("target_id", "target-1"),
                    ),
                ),
            ),
        ),
        market_data=(
            json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode(),
    )


def _run(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "engine_version": "1.231.0",
        "iterations": 2,
        "total_events": 2,
        "total_orders": 1,
        "total_positions": 1,
        "result_summary": (
            ("account.BINANCE.base_currency", "None"),
            ("account.BINANCE.event_count", "2"),
            ("account.BINANCE.type", "CASH"),
            ("iterations", "2"),
            ("orders.closed", "1"),
            ("orders.emulated", "0"),
            ("orders.inflight", "0"),
            ("orders.open", "0"),
            ("orders.total", "1"),
            ("positions.closed", "0"),
            ("positions.open", "1"),
            ("positions.snapshots", "0"),
            ("positions.total", "1"),
            ("positions.total_with_snapshots", "1"),
            ("total_events", "2"),
            ("venues.total", "1"),
        ),
        "account_count": 1,
        "account_event_count": 2,
        "instrument_ids": ("BTCUSDT.BINANCE",),
        "strategy_state": "COMPLETED",
        "processed_target_ids": ("target-1",),
        "pending_order_ids": (),
        "rejected_order_ids": (),
        "native_order_ids": ("target-1",),
        "native_fill_ids": ("trade-1",),
        "order_count": 1,
        "fill_count": 1,
        "order_facts": (("target-1", "BUY", "1", "1", "FILLED"),),
        "position_quantity": "1",
        "balance_currencies": ("BTC", "USDT"),
        "balance_facts": (
            ("BTC", "1", "0", "1"),
            ("USDT", "899.9", "0", "899.9"),
        ),
        "commission_facts": (("USDT", "0.1"),),
        "native_facts": (
            _fact(
                "quote",
                instrument_id="BTCUSDT.BINANCE",
                bid="100",
                ask="101",
                bid_size="1000",
                ask_size="1000",
                ts_event=1785931200000000000,
            ),
            _fact(
                "target_planned",
                target_id="target-1",
                source_signal_count=1,
                source_signal_id_0="signal-1",
                effective_at="2026-08-05T12:00:00Z",
                instrument_id="BTCUSDT.BINANCE",
                current_quantity="0",
                target_quantity="1",
                delta="1",
                side="BUY",
                price_basis="101",
                notional="101",
                reason="ORDER_REQUIRED",
            ),
            _fact("target_quantity_planned", quantity="1"),
            _fact(
                "order_submitted",
                client_order_id="target-1",
                target_id="target-1",
                source_signal_count=1,
                source_signal_id_0="signal-1",
                side="BUY",
                quantity="1",
                order_type="MARKET",
            ),
            _fact(
                "order_filled",
                client_order_id="target-1",
                trade_id="trade-1",
                side="BUY",
                quantity="1",
                price="100",
                commission="0.1",
                commission_currency="USDT",
                ts_event=1785931200000000000,
            ),
            _fact("stopped", state="COMPLETED"),
        ),
        "position_average_entry": "100",
        "position_realized_pnl": "0",
        "position_unrealized_pnl": "1",
        "final_market_price": "101",
        "last_market_timestamp": 1785931200000000000,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _lineage(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "profile_manifest_schema_version": 8,
        "runtime_family": "cython-v1",
        "engine_version": "1.231.0",
        "profile": "p1-real-backtest",
        "event_schema": "nautilus-p1-event-stream-v1",
        "closure_sha256": "a" * 64,
        "runtime_inventory_sha256": "b" * 64,
    }
    value.update(changes)
    return value


def test_final_state_builds_completion_only_from_consistent_scalar_proof() -> None:
    completion = validate_final_state(_inputs(), _lineage(), _run())

    assert completion.target_count == 1
    assert completion.order_count == completion.fill_count == 1
    assert completion.final_cash == "899.9"
    assert completion.final_position == "1"
    assert completion.fees == "0.1"
    assert completion.realized_pnl == "0"
    assert completion.unrealized_pnl == "1"


def test_final_state_accepts_zero_order_with_canonical_zero_fees() -> None:
    run = _run(
        total_events=0,
        total_orders=0,
        total_positions=0,
        result_summary=(
            ("account.BINANCE.base_currency", "None"),
            ("account.BINANCE.event_count", "1"),
            ("account.BINANCE.type", "CASH"),
            ("iterations", "2"),
            ("orders.closed", "0"),
            ("orders.emulated", "0"),
            ("orders.inflight", "0"),
            ("orders.open", "0"),
            ("orders.total", "0"),
            ("positions.closed", "0"),
            ("positions.open", "0"),
            ("positions.snapshots", "0"),
            ("positions.total", "0"),
            ("positions.total_with_snapshots", "0"),
            ("total_events", "0"),
            ("venues.total", "1"),
        ),
        account_event_count=1,
        processed_target_ids=("target-1",),
        native_order_ids=(),
        native_fill_ids=(),
        order_count=0,
        fill_count=0,
        order_facts=(),
        position_quantity="0",
        balance_currencies=("USDT",),
        balance_facts=(
            ("USDT", "1000", "0", "1000"),
        ),
        commission_facts=(),
        native_facts=(
            _run().native_facts[0],
            _fact(
                "target_planned",
                target_id="target-1",
                source_signal_count=1,
                source_signal_id_0="signal-1",
                effective_at="2026-08-05T12:00:00Z",
                instrument_id="BTCUSDT.BINANCE",
                current_quantity="0",
                target_quantity="0",
                delta="0",
                side=None,
                price_basis="101",
                notional="0",
                reason="TARGET_ALREADY_SATISFIED",
            ),
            _fact("target_quantity_planned", quantity="0"),
            _fact("stopped", state="COMPLETED"),
        ),
        position_average_entry="0",
        position_realized_pnl="0",
        position_unrealized_pnl="0",
    )

    completion = validate_final_state(_inputs(), _lineage(), run)

    assert completion.order_count == completion.fill_count == 0
    assert completion.final_cash == "1000"
    assert completion.final_position == "0"
    assert completion.fees == "0"


def test_final_state_rejects_empty_market_rows_as_stable_error() -> None:
    inputs = _inputs()
    inputs.market_data = b"\n"

    with pytest.raises(FinalStateError, match="final state"):
        validate_final_state(inputs, _lineage(), _run())


@pytest.mark.parametrize(
    "order_facts",
    (
        (("target-1", "SELL", "1", "1", "FILLED"),),
        (("target-1", "BUY", "2", "2", "FILLED"),),
        (
            ("target-1", "BUY", "1", "1", "FILLED"),
            ("target-1", "BUY", "1", "1", "FILLED"),
        ),
    ),
)
def test_final_state_rejects_order_cache_not_bound_to_native_execution(
    order_facts: tuple[tuple[str, str, str, str, str], ...],
) -> None:
    with pytest.raises(FinalStateError, match="final state"):
        validate_final_state(_inputs(), _lineage(), _run(order_facts=order_facts))


@pytest.mark.parametrize(
    ("field", "value", "summary_name"),
    (
        ("total_events", 8, "total_events"),
        ("account_event_count", 8, "account.BINANCE.event_count"),
    ),
)
def test_final_state_rejects_forged_counters_even_when_summary_matches(
    field: str, value: int, summary_name: str
) -> None:
    summary = tuple(
        (name, str(value) if name == summary_name else item)
        for name, item in _run().result_summary
    )

    with pytest.raises(FinalStateError, match="final state"):
        validate_final_state(
            _inputs(), _lineage(), _run(**{field: value}, result_summary=summary)
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"total_events": -1},
        {"total_events": 4},
        {"total_orders": 2},
        {"account_event_count": 4},
        {"position_quantity": "2"},
        {
            "balance_currencies": ("USDT",),
            "balance_facts": (("USDT", "899.9", "0", "899.9"),),
        },
        {
            "balance_currencies": ("BTC", "ETH", "USDT"),
            "balance_facts": (
                ("BTC", "1", "0", "1"),
                ("ETH", "0", "0", "0"),
                ("USDT", "899.9", "0", "899.9"),
            ),
        },
        {"commission_facts": (("USDT", "0.2"),)},
        {"pending_order_ids": ("target-1",)},
        {"rejected_order_ids": ("target-1",)},
        {"strategy_state": "FAILED"},
        {"processed_target_ids": ()},
        {"order_facts": (("target-1", "BUY", "1", "0", "ACCEPTED"),)},
        {"position_unrealized_pnl": "NaN"},
    ),
)
def test_final_state_rejects_mutated_native_or_cache_proof(
    changes: dict[str, object],
) -> None:
    with pytest.raises(FinalStateError, match="final state"):
        validate_final_state(_inputs(), _lineage(), _run(**changes))


@pytest.mark.parametrize(
    "lineage",
    (
        _lineage(engine_version="1.227.0"),
        _lineage(profile_manifest_schema_version=7),
        _lineage(closure_sha256="A" * 64),
    ),
)
def test_final_state_rejects_wrong_release_or_closure_lineage(
    lineage: dict[str, object],
) -> None:
    with pytest.raises(FinalStateError, match="lineage"):
        validate_final_state(_inputs(), lineage, _run())


def test_exact_g1_final_state_accepts_real_run_and_rejects_scalar_mutation() -> None:
    with exact_g1_runtime() as runtime:
        if runtime is None:
            return
        root, site_packages = runtime
        script = r'''
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import sys
import warnings
warnings.filterwarnings("ignore", message="Timestamp.utcnow is deprecated.*")
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import nautilus_trader
from runtime_v1.backtest_runner import run_backtest
from runtime_v1.event_projector import CompletionAuthority, project_event_stream
from runtime_v1.final_state import FinalStateError, validate_final_state
from runtime_v1.input_loader import ArtifactReference, RunBacktestRequest, RuntimeInputs

assert nautilus_trader.__version__ == "1.231.0"
assert Path(nautilus_trader.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())

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
schedule_document = json.loads(open(sys.argv[5], "rb").read())
schedule = freeze(schedule_document)
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
    event_time="2026-08-05T12:02:00Z",
    initialization_time="2026-08-05T11:59:00Z",
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
lineage = {
    "profile_manifest_schema_version": 8,
    "runtime_family": "cython-v1",
    "engine_version": "1.231.0",
    "profile": "p1-real-backtest",
    "event_schema": "nautilus-p1-event-stream-v1",
    "closure_sha256": "a" * 64,
    "runtime_inventory_sha256": "b" * 64,
}
run = run_backtest(inputs)
completion = validate_final_state(inputs, lineage, run)
assert type(completion) is CompletionAuthority
assert completion.final_position == run.position_quantity == "0"
assert completion.order_count == completion.fill_count == 2
for invalid_order_facts in (
    (run.order_facts[0], run.order_facts[0]),
    (
        (
            run.order_facts[0][0],
            "SELL" if run.order_facts[0][1] == "BUY" else "BUY",
            run.order_facts[0][2],
            run.order_facts[0][3],
            run.order_facts[0][4],
        ),
        run.order_facts[1],
    ),
    (
        (
            run.order_facts[0][0],
            run.order_facts[0][1],
            "1",
            "1",
            run.order_facts[0][4],
        ),
        run.order_facts[1],
    ),
):
    try:
        validate_final_state(inputs, lineage, replace(run, order_facts=invalid_order_facts))
    except FinalStateError:
        pass
    else:
        raise AssertionError("forged native order cache was accepted")

def forged_summary(name, value):
    return tuple(
        (key, str(value) if key == name else item)
        for key, item in run.result_summary
    )

for field, name in (
    ("total_events", "total_events"),
    ("account_event_count", "account.BINANCE.event_count"),
):
    try:
        validate_final_state(
            inputs,
            lineage,
            replace(run, **{field: 99}, result_summary=forged_summary(name, 99)),
        )
    except FinalStateError:
        pass
    else:
        raise AssertionError("forged native counter was accepted")
zero_targets = []
for target in schedule_document["targets"]:
    zero_targets.append({
        **target,
        "positions": [
            {**position, "target_weight": "0"}
            for position in target["positions"]
        ],
    })
zero_inputs = RuntimeInputs(
    request,
    configuration,
    catalog,
    freeze({**schedule_document, "targets": zero_targets}),
    raw,
)
zero_run = run_backtest(zero_inputs)
zero_completion = validate_final_state(zero_inputs, lineage, zero_run)
assert zero_run.total_events == 0
assert zero_completion.final_position == "0"
assert zero_completion.order_count == zero_completion.fill_count == 0
assert zero_completion.fees == "0"
zero_stream = project_event_stream(
    zero_inputs,
    zero_run,
    zero_completion,
    closure_digest="a" * 64,
    upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
)
assert zero_stream.events[-1]["event_type"] == "RunCompleted"
assert zero_stream.events[-1]["fees"] == "0"
try:
    project_event_stream(
        inputs,
        replace(run, commission_facts=()),
        completion,
        closure_digest="a" * 64,
        upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
    )
except ValueError:
    pass
else:
    raise AssertionError("missing commission facts on an executed run were accepted")
for invalid_events in (-1, 1):
    try:
        validate_final_state(
            zero_inputs,
            lineage,
            replace(zero_run, total_events=invalid_events),
        )
    except FinalStateError:
        pass
    else:
        raise AssertionError("invalid zero-order event count was accepted")
try:
    validate_final_state(inputs, lineage, replace(run, position_quantity="1"))
except FinalStateError:
    pass
else:
    raise AssertionError("mutated terminal position was accepted")
print(json.dumps({
    "cash": completion.final_cash,
    "fees": completion.fees,
}, separators=(",", ":"), sort_keys=True))
'''
        completed = subprocess.run(
            exact_g1_command(
                root,
                site_packages,
                script,
                (
                    ROOT
                    / "tests/fixtures/p1_nautilus/contracts/instrument-catalog.json",
                    "/inputs/instrument-catalog.json",
                ),
                (
                    ROOT
                    / "tests/fixtures/p1_nautilus/contracts/engine-configuration.json",
                    "/inputs/engine-configuration.json",
                ),
                (
                    ROOT / "tests/fixtures/p1_nautilus/contracts/target-schedule.json",
                    "/inputs/target-schedule.json",
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
    assert json.loads(completed.stdout) == {
        "cash": "1007982.01798201",
        "fees": "2007.99200799",
    }
