from __future__ import annotations

import json
from pathlib import Path
import subprocess

from test_instrument_factory_native import exact_g1_command, exact_g1_runtime


ROOT = Path(__file__).parents[2]


def test_exact_g1_projects_only_real_submissions_and_fills() -> None:
    with exact_g1_runtime() as runtime:
        if runtime is None:
            assert (
                "nautilus_trader"
                not in subprocess.run(
                    [
                        "uv",
                        "run",
                        "python",
                        "-c",
                        "import sys;print(' '.join(sys.path))",
                    ],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            return
        root, site_packages = runtime
        script = r"""
import hashlib
import json
from pathlib import Path
import sys
import warnings
warnings.filterwarnings("ignore", message="Timestamp.utcnow is deprecated.*")
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import nautilus_trader
from runtime_v1.backtest_runner import run_backtest
from runtime_v1.event_projector import CompletionAuthority, project_event_stream
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
run = run_backtest(inputs)
balances = {currency: total for currency, total, _, _ in run.balance_facts}
commissions = dict(run.commission_facts)
authority = CompletionAuthority(
    target_count=len(run.processed_target_ids),
    order_count=run.order_count,
    fill_count=run.fill_count,
    final_cash=balances["USDT"],
    final_position=run.position_quantity,
    fees=commissions["USDT"],
    realized_pnl=run.position_realized_pnl,
    unrealized_pnl=run.position_unrealized_pnl,
)
stream = project_event_stream(
    inputs,
    run,
    authority,
    closure_digest="a" * 64,
    upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
)
orders = [event for event in stream.events if event["event_type"] == "OrderSubmitted"]
fills = [event for event in stream.events if event["event_type"] == "Fill"]
assert tuple(event["native_order_id"] for event in orders) == run.native_order_ids
assert tuple(event["native_fill_id"] for event in fills) == run.native_fill_ids
assert tuple(event["client_order_id"] for event in orders) == run.processed_target_ids
assert tuple(event["client_order_id"] for event in fills) == run.processed_target_ids
assert tuple(event["quantity"] for event in orders) == tuple(event["quantity"] for event in fills)
assert all(event["origin"] == "NAUTILUS_CALLBACK" for event in fills)
assert stream.events[-3]["origin"] == stream.events[-2]["origin"] == "NAUTILUS_CACHE_OBSERVATION"
assert stream.events[-1]["event_type"] == "RunCompleted"
assert stream.raw_sha256 != stream.semantic_sha256
assert len(stream.envelopes) == len(stream.events) == 12
print(json.dumps({
    "fill_ids": [event["native_fill_id"] for event in fills],
    "order_ids": [event["native_order_id"] for event in orders],
    "raw_sha256": stream.raw_sha256,
    "semantic_sha256": stream.semantic_sha256,
}, separators=(",", ":"), sort_keys=True))
"""
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
    observed = json.loads(completed.stdout)
    assert len(observed["order_ids"]) == len(set(observed["order_ids"])) == 2
    assert len(observed["fill_ids"]) == len(set(observed["fill_ids"])) == 2
    assert observed["raw_sha256"] != observed["semantic_sha256"]
