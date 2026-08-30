from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import BinaryIO
from uuid import UUID

import pytest

from engines.nautilus.runtime_v1.control_channel import (
    frame_payload,
    iter_payloads,
    parse_command,
)
from packages.engine_contracts import (
    ArtifactReference,
    EngineTargetPortfolio,
    StartPaperEngine,
    StopPaperEngine,
    SubmitTargetPortfolio,
    canonical_json_bytes,
    payload_digest,
)
from packages.nautilus_runtime_contracts.paper import (
    PAPER_PROTOCOL_SCHEMA,
    PaperCommandFrame,
    PaperSessionCheckpoint,
    PaperSessionJournal,
    PaperSessionState,
    paper_request_id,
    parse_paper_acknowledgement,
    parse_paper_event_frame,
)

from test_instrument_factory_native import exact_g1_command, exact_g1_runtime


ROOT = Path(__file__).parents[2]
SESSION = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
OWNER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CATALOG = ROOT / "tests/fixtures/p1_nautilus/contracts/instrument-catalog.json"
CONFIGURATION = ROOT / "tests/fixtures/p1_nautilus/contracts/engine-configuration.json"
SCHEDULE = ROOT / "tests/fixtures/p1_nautilus/contracts/target-schedule.json"


def _runtime_request(
    market_data: bytes,
) -> tuple[bytes, dict[str, bytes]]:
    artifacts = {
        "engine_configuration": CONFIGURATION.read_bytes(),
        "instrument_catalog": CATALOG.read_bytes(),
        "strategy_configuration": SCHEDULE.read_bytes(),
        "market_data": market_data,
    }
    references = {
        name: {
            "artifact_id": f"{str(index) * 8}-{str(index) * 4}-4{str(index) * 3}-8{str(index) * 3}-{str(index) * 12}",
            "media_type": (
                "application/jsonl" if name == "market_data" else "application/json"
            ),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        for index, (name, raw) in enumerate(artifacts.items(), 1)
    }
    payload = {
        "command_type": "RunBacktest",
        **references,
        "start_time": "2026-08-05T12:00:00Z",
        "end_time": "2026-08-05T12:01:00Z",
    }
    request = {
        "causation_id": str(OWNER),
        "config_digest": hashlib.sha256(
            canonical_json_bytes(
                {
                    name: references[name]
                    for name in (
                        "engine_configuration",
                        "instrument_catalog",
                        "strategy_configuration",
                    )
                }
            )
        ).hexdigest(),
        "correlation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "engine_run_id": str(SESSION),
        "event_time": "2026-08-05T12:01:00Z",
        "initialization_time": "2026-08-05T11:59:00Z",
        "message_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "payload": payload,
        "payload_digest": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        "producer_identity": "worker-authority-1",
        "schema_version": "1.0.0",
        "source_commit": "0123456789abcdef0123456789abcdef01234567",
        "stream_sequence": 1,
    }
    return canonical_json_bytes(request), artifacts


def _market_data() -> bytes:
    rows = (
        {
            "ask": "100",
            "bid": "99",
            "close": "100",
            "event_time": "2026-08-05T12:00:00Z",
            "high": "101",
            "low": "98",
            "open": "99",
            "quote_time": "2026-08-05T12:00:00Z",
            "sequence": 1,
            "volume": "1000000",
        },
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
            "volume": "1000000",
        },
    )
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def _read_frame(stream: BinaryIO) -> bytes:
    size = stream.read(4)
    assert len(size) == 4
    payload = stream.read(int.from_bytes(size, "big"))
    assert len(payload) == int.from_bytes(size, "big")
    return payload


def _start_frame() -> bytes:
    command = StartPaperEngine(
        command_type="StartPaperEngine",
        engine_configuration=ArtifactReference(
            artifact_id=UUID(int=1), sha256="1" * 64, media_type="application/json"
        ),
        instrument_catalog=ArtifactReference(
            artifact_id=UUID(int=2), sha256="2" * 64, media_type="application/json"
        ),
        strategy_configuration=ArtifactReference(
            artifact_id=UUID(int=3), sha256="3" * 64, media_type="application/json"
        ),
    )
    return _command_frame(command, 1)


def _command_frame(
    command: StartPaperEngine | SubmitTargetPortfolio | StopPaperEngine,
    sequence: int,
) -> bytes:
    return canonical_json_bytes(
        PaperCommandFrame(
            schema_version=PAPER_PROTOCOL_SCHEMA,
            frame_type="COMMAND",
            session_id=SESSION,
            owner_id=OWNER,
            request_id=paper_request_id(SESSION, sequence),
            command_sequence=sequence,
            command_digest=payload_digest(command),
            command=command,
        )
    )


def _native_commands() -> tuple[bytes, ...]:
    schedule = json.loads(SCHEDULE.read_bytes())
    references = (
        ArtifactReference(
            artifact_id=UUID("11111111-1111-4111-8111-111111111111"),
            sha256=hashlib.sha256(CONFIGURATION.read_bytes()).hexdigest(),
            media_type="application/json",
        ),
        ArtifactReference(
            artifact_id=UUID("22222222-2222-4222-8222-222222222222"),
            sha256=hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
            media_type="application/json",
        ),
        ArtifactReference(
            artifact_id=UUID("33333333-3333-4333-8333-333333333333"),
            sha256=hashlib.sha256(SCHEDULE.read_bytes()).hexdigest(),
            media_type="application/json",
        ),
    )
    commands = [
        StartPaperEngine(
            command_type="StartPaperEngine",
            engine_configuration=references[0],
            instrument_catalog=references[1],
            strategy_configuration=references[2],
        ),
        *(
            SubmitTargetPortfolio(
                command_type="SubmitTargetPortfolio",
                target_portfolio=EngineTargetPortfolio.model_validate_json(
                    canonical_json_bytes(target)
                ),
            )
            for target in schedule["targets"]
        ),
        StopPaperEngine(command_type="StopPaperEngine", target_engine_run_id=SESSION),
    ]
    return tuple(
        _command_frame(command, sequence)
        for sequence, command in enumerate(commands, 1)
    )


def test_control_channel_is_bounded_and_rejects_truncation() -> None:
    raw = _start_frame()
    assert iter_payloads(frame_payload(raw)) == (raw,)
    with pytest.raises(ValueError, match="truncated"):
        iter_payloads(frame_payload(raw)[:-1])
    with pytest.raises(ValueError, match="maximum"):
        frame_payload(b"x" * 65_537)


def test_control_channel_rejects_unknown_inner_command_fields() -> None:
    value = json.loads(_start_frame())
    value["command"]["unexpected_live_authority"] = True
    value["command_digest"] = hashlib.sha256(
        canonical_json_bytes(value["command"])
    ).hexdigest()
    with pytest.raises(ValueError, match="start command"):
        parse_command(canonical_json_bytes(value))


def test_paper_runtime_sources_have_no_network_or_provider_imports() -> None:
    forbidden = {"aiohttp", "httpx", "requests", "socket", "urllib", "websocket"}
    files = tuple(
        ROOT / "engines/nautilus/runtime_v1" / name
        for name in (
            "control_channel.py",
            "paper_main.py",
            "paper_runner.py",
            "paper_session.py",
        )
    )
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert imports.isdisjoint(forbidden)


def test_exact_g1_paper_entry_runs_as_the_isolated_child() -> None:
    with exact_g1_runtime() as runtime:
        if runtime is None:
            return
        root, site_packages = runtime
        lineage_value = os.environ.get("P1_NAUTILUS_PRODUCT_LINEAGE")
        assert lineage_value is not None
        lineage = Path(lineage_value)
        lineage_document = json.loads(lineage.read_bytes())
        request, artifacts = _runtime_request(_market_data())
        mounts = {
            "/inputs/request.json": request,
            "/inputs/request.sha256": (
                hashlib.sha256(request).hexdigest().encode("ascii") + b"\n"
            ),
            **{
                "/inputs/artifacts/"
                + name
                + "-"
                + hashlib.sha256(raw).hexdigest()
                + (".jsonl" if name == "market_data" else ".json"): raw
                for name, raw in artifacts.items()
            },
            **{
                f"/engine/wheels/{wheel.name}": wheel.read_bytes()
                for wheel in sorted((root / "files/engine/wheels").glob("*.whl"))
            },
        }
        command = exact_g1_command(
            root,
            site_packages,
            "",
            (lineage, "/engine/p1-product-lineage.json"),
        )
        command = command[: command.index("--")]
        wheel_source = str(root / "files/engine/wheels")
        wheel_bind = command.index(wheel_source) - 1
        del command[wheel_bind : wheel_bind + 3]
        command[wheel_bind:wheel_bind] = ["--dir", "/engine/wheels"]
        while "--setenv" in command:
            index = command.index("--setenv")
            del command[index : index + 3]
        command.extend(("--unsetenv", "PWD"))
        command.extend(
            (
                "--ro-bind",
                str(ROOT / "engines/nautilus/runtime_v1"),
                "/engine/runtime_v1",
                "--dir",
                "/inputs/artifacts",
            )
        )
        descriptors: list[BinaryIO] = []
        try:
            for target, raw in mounts.items():
                descriptor = tempfile.TemporaryFile()
                descriptor.write(raw)
                descriptor.seek(0)
                os.fchmod(descriptor.fileno(), 0o400)
                descriptors.append(descriptor)
                command.extend(
                    (
                        "--perms",
                        "0400",
                        "--ro-bind-data",
                        str(descriptor.fileno()),
                        target,
                    )
                )
            command.extend(
                (
                    "--",
                    "/usr/bin/python3.12",
                    "-I",
                    "-S",
                    "/engine/runtime_v1/paper_main.py",
                    "/inputs/request.json",
                    "/inputs/request.sha256",
                )
            )
            completed = subprocess.run(
                command,
                cwd="/",
                env={},
                input=b"".join(frame_payload(raw) for raw in _native_commands()),
                pass_fds=tuple(item.fileno() for item in descriptors),
                check=False,
                capture_output=True,
            )
            for descriptor in descriptors:
                descriptor.seek(0)
            child = subprocess.Popen(
                command,
                cwd="/",
                env={},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=tuple(item.fileno() for item in descriptors),
            )
            assert child.stdin is not None and child.stdout is not None
            killed_payloads: list[bytes] = []
            for raw, count in zip(_native_commands()[:2], (3, 6), strict=True):
                child.stdin.write(frame_payload(raw))
                child.stdin.flush()
                killed_payloads.extend(_read_frame(child.stdout) for _ in range(count))
            killed_prefix = tuple(killed_payloads)
            child.kill()
            assert child.wait(timeout=10) != 0
        finally:
            for descriptor in descriptors:
                descriptor.close()

    assert completed.returncode == 0, completed.stderr.decode()
    assert completed.stderr == b""
    payloads = iter_payloads(completed.stdout)
    assert sum(json.loads(raw)["frame_type"] == "ACK" for raw in payloads) == 4
    assert sum(json.loads(raw)["frame_type"] == "EVENT" for raw in payloads) > 4
    assert sum(json.loads(raw)["frame_type"] == "CHECKPOINT" for raw in payloads) == 4
    killed_journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    killed_checkpoint = None
    for raw in killed_prefix:
        value = json.loads(raw)
        if value["frame_type"] == "ACK":
            killed_journal.accept_command(
                _native_commands()[value["command_sequence"] - 1]
            )
            killed_journal.record_ack(raw)
        elif value["frame_type"] == "EVENT":
            killed_journal.record_event(raw)
        else:
            killed_checkpoint = PaperSessionCheckpoint.model_validate_json(
                canonical_json_bytes(value["checkpoint"])
            )
            assert hashlib.sha256(canonical_json_bytes(value["checkpoint"])).hexdigest() == value["checkpoint_sha256"]
    assert killed_checkpoint is not None
    assert killed_checkpoint.closure_digest == lineage_document["closure_sha256"]
    assert killed_checkpoint.child_identity != "0" * 64
    assert killed_journal.checkpoint(
        semantic_state_hash=killed_checkpoint.semantic_state_hash,
        child_identity=killed_checkpoint.child_identity,
        closure_digest=killed_checkpoint.closure_digest,
        portfolio_state_hash=killed_checkpoint.portfolio_state_hash,
    ) == killed_checkpoint
    with pytest.raises(ValueError, match="acknowledged stop"):
        killed_journal.end_of_input()
    assert killed_journal.state is PaperSessionState.RECONCILIATION_REQUIRED
    with pytest.raises(ValueError, match="terminal session"):
        killed_journal.accept_command(_native_commands()[2])


def test_exact_g1_streaming_paper_runtime_matches_one_shot(tmp_path: Path) -> None:
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
        product_lineage_value = os.environ.get("P1_NAUTILUS_PRODUCT_LINEAGE")
        assert product_lineage_value is not None
        product_lineage = Path(product_lineage_value)
        lineage_document = json.loads(product_lineage.read_bytes())
        assert lineage_document["closure_sha256"] == (
            "97185d4c0b6090353ba51c1aab25ed4ea4dfab08113b655fac623af9e7db2b80"
        )
        commands = _native_commands()
        command_stream = tmp_path / "commands.bin"
        command_stream.write_bytes(b"".join(frame_payload(raw) for raw in commands))
        market_data = tmp_path / "market-data.jsonl"
        market_data.write_bytes(_market_data())
        script = r"""
import base64
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys
import warnings
warnings.filterwarnings("ignore", message="Timestamp.utcnow is deprecated.*")
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import nautilus_trader
from runtime_v1.backtest_runner import run_backtest
from runtime_v1.instrument_factory import build_instrument
from runtime_v1.input_loader import ArtifactReference, RunBacktestRequest, RuntimeInputs
from runtime_v1.market_data_loader import load_market_data
from runtime_v1.control_channel import iter_payloads, request_id
from runtime_v1.paper_main import open_paper_loop
from runtime_v1.paper_runner import PaperRuntimeRejected
from runtime_v1.paper_session import verify_checkpoint
from runtime_v1.session import create_session
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
configuration_raw = open(sys.argv[4], "rb").read()
schedule_raw = open(sys.argv[5], "rb").read()
market_raw = open(sys.argv[6], "rb").read()
commands = open(sys.argv[7], "rb").read()
request = RunBacktestRequest(
    message_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    correlation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    causation_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    engine_run_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    stream_sequence=1,
    event_time="2026-08-05T12:01:00Z",
    initialization_time="2026-08-05T11:59:00Z",
    schema_version="1.0.0",
    producer_identity="worker-authority-1",
    source_commit="0123456789abcdef0123456789abcdef01234567",
    config_digest="1" * 64,
    payload_digest="2" * 64,
    command_type="RunBacktest",
    engine_configuration=ArtifactReference("11111111-1111-4111-8111-111111111111", hashlib.sha256(configuration_raw).hexdigest(), "application/json"),
    instrument_catalog=ArtifactReference("22222222-2222-4222-8222-222222222222", hashlib.sha256(catalog_raw).hexdigest(), "application/json"),
    strategy_configuration=ArtifactReference("33333333-3333-4333-8333-333333333333", hashlib.sha256(schedule_raw).hexdigest(), "application/json"),
    market_data=ArtifactReference("44444444-4444-4444-8444-444444444444", hashlib.sha256(market_raw).hexdigest(), "application/jsonl"),
    start_time="2026-08-05T12:00:00Z",
    end_time="2026-08-05T12:01:00Z",
)
inputs = RuntimeInputs(
    request,
    freeze(json.loads(configuration_raw)),
    freeze(json.loads(catalog_raw)),
    freeze(json.loads(schedule_raw)),
    market_raw,
)
baseline = run_backtest(inputs)
instrument = build_instrument(inputs.instrument_catalog)
reset_session = create_session(inputs, instrument, load_market_data(inputs, instrument))
try:
    reset_session.engine.cache.set_mark_xrate(instrument.base_currency, instrument.quote_currency, 2.0)
    assert reset_session.engine.cache.get_mark_xrate(instrument.base_currency, instrument.quote_currency) == 2.0
    reset_session.run()
    first_cycle_ids = tuple(str(order.client_order_id) for order in reset_session.engine.cache.orders())
    assert len(reset_session.engine.cache.accounts()) == 1
    assert len(first_cycle_ids) == len(set(first_cycle_ids)) == 2
    reset_session.engine.reset()
    assert reset_session.engine.cache.get_mark_xrate(instrument.base_currency, instrument.quote_currency) is None
    assert reset_session.engine.cache.accounts() == []
    reset_session.run()
    second_cycle_ids = tuple(str(order.client_order_id) for order in reset_session.engine.cache.orders())
    assert len(reset_session.engine.cache.accounts()) == 1
    assert len(second_cycle_ids) == len(set(second_cycle_ids)) == 2
finally:
    reset_session.dispose()
loop = open_paper_loop(inputs)
steps = [loop.accept(raw) for raw in iter_payloads(commands)]
assert [len(iter_payloads(step.response_stream)) for step in steps] == [3, 6, 6, 5]
loop.close_input()
run = steps[-1].run
projected = steps[-1].projected_stream
assert run == baseline and projected is not None
checkpoints = [step.checkpoint for step in steps]
assert [checkpoint.last_accepted_command for checkpoint in checkpoints] == [1, 2, 3, 4]
checkpoint_hashes = [checkpoint.semantic_state_hash for checkpoint in checkpoints]
assert len(set(checkpoint_hashes)) == 4, checkpoint_hashes
last = checkpoints[-1]
checkpoint_sha256 = hashlib.sha256(canonical(asdict(last))).hexdigest()
verify_checkpoint(
    last,
    checkpoint_sha256=checkpoint_sha256,
    session_id=last.session_id,
    owner_id=last.owner_id,
    child_identity=last.child_identity,
    closure_digest=last.closure_digest,
)
try:
    verify_checkpoint(
        last,
        checkpoint_sha256="d" * 64,
        session_id=last.session_id,
        owner_id=last.owner_id,
        child_identity=last.child_identity,
        closure_digest=last.closure_digest,
    )
except ValueError:
    checkpoint_mismatch_rejected = True
else:
    checkpoint_mismatch_rejected = False
bad_loop = open_paper_loop(inputs)
command_payloads = iter_payloads(commands)
bad_loop.accept(command_payloads[0])
bad_frame = json.loads(command_payloads[1])
bad_frame["command"]["target_portfolio"]["positions"][0]["target_weight"] = "0"
bad_frame["command_digest"] = hashlib.sha256(canonical(bad_frame["command"])).hexdigest()
try:
    bad_loop.accept(canonical(bad_frame))
except PaperRuntimeRejected as error:
    rejected_payload = json.loads(iter_payloads(error.response_stream)[0])
    uncertain_target_blocked = (
        rejected_payload["accepted"] is False
        and rejected_payload["state"] == "RECONCILIATION_REQUIRED"
        and error.checkpoint is None
    )
else:
    uncertain_target_blocked = False
bad_start_loop = open_paper_loop(inputs)
bad_start = json.loads(command_payloads[0])
bad_start["command"]["engine_configuration"]["artifact_id"] = "99999999-9999-4999-8999-999999999999"
bad_start["command_digest"] = hashlib.sha256(canonical(bad_start["command"])).hexdigest()
try:
    bad_start_loop.accept(canonical(bad_start))
except PaperRuntimeRejected as error:
    start_identity_blocked = error.reason_code == "ENGINE_STATE_UNCERTAIN"
else:
    start_identity_blocked = False
empty_loop = open_paper_loop(inputs)
empty_loop.accept(command_payloads[0])
empty_stop = json.loads(command_payloads[-1])
empty_stop["command_sequence"] = 2
empty_stop["request_id"] = request_id(empty_stop["session_id"], 2)
try:
    empty_loop.accept(canonical(empty_stop))
except PaperRuntimeRejected as error:
    empty_stop_blocked = error.reason_code == "ENGINE_STATE_UNCERTAIN"
else:
    empty_stop_blocked = False
early_loop = open_paper_loop(inputs)
early_stop = json.loads(command_payloads[-1])
early_stop["command_sequence"] = 3
early_stop["request_id"] = request_id(early_stop["session_id"], 3)
early_commands = (
    command_payloads[0],
    command_payloads[1],
    canonical(early_stop),
)
early_steps = [early_loop.accept(raw) for raw in early_commands]
early_loop.close_input()
early_run = early_steps[-1].run
assert early_run == baseline
future_targets = list(json.loads(schedule_raw)["targets"])
future_targets.extend((
    {**future_targets[0], "effective_at": "2026-08-05T12:02:00Z", "source_signal_ids": ["55555555-5555-4555-8555-555555555555"], "target_id": "66666666-6666-4666-8666-666666666666"},
    {**future_targets[1], "effective_at": "2026-08-05T12:03:00Z", "source_signal_ids": ["77777777-7777-4777-8777-777777777777"], "target_id": "88888888-8888-4888-8888-888888888888"},
))
future_schedule_raw = canonical({"schema_version": "nautilus-p1-target-schedule-v1", "targets": future_targets})
future_inputs = RuntimeInputs(
    replace(request, strategy_configuration=ArtifactReference(request.strategy_configuration.artifact_id, hashlib.sha256(future_schedule_raw).hexdigest(), request.strategy_configuration.media_type)),
    inputs.engine_configuration,
    inputs.instrument_catalog,
    freeze(json.loads(future_schedule_raw)),
    market_raw,
)
future_start = json.loads(command_payloads[0])
future_start["command"]["strategy_configuration"]["sha256"] = hashlib.sha256(future_schedule_raw).hexdigest()
future_start["command_digest"] = hashlib.sha256(canonical(future_start["command"])).hexdigest()
future_loop = open_paper_loop(future_inputs)
future_steps = [future_loop.accept(raw) for raw in (canonical(future_start), command_payloads[1], early_commands[-1])]
future_loop.close_input()
future_run = future_steps[-1].run
assert future_run is not None and future_run.processed_target_ids == baseline.processed_target_ids
future_flat_loop = open_paper_loop(future_inputs)
future_flat_steps = [future_flat_loop.accept(raw) for raw in (canonical(future_start), command_payloads[1], command_payloads[2], command_payloads[-1])]
future_flat_loop.close_input()
future_flat_run = future_flat_steps[-1].run
assert future_flat_run is not None and future_flat_run.processed_target_ids == baseline.processed_target_ids
print(json.dumps({
    "account_count": run.account_count,
    "balance_currencies": run.balance_currencies,
    "checkpoint_mismatch_rejected": checkpoint_mismatch_rejected,
    "early_response_stream": base64.b64encode(b"".join(step.response_stream for step in early_steps)).decode(),
    "fill_ids": run.native_fill_ids,
    "future_reentry_cancelled": future_run.processed_target_ids,
    "future_reentry_cancelled_while_flat": future_flat_run.processed_target_ids,
    "early_stop_processed_targets": early_run.processed_target_ids,
    "empty_stop_blocked": empty_stop_blocked,
    "order_ids": run.native_order_ids,
    "position": run.position_quantity,
    "processed_targets": run.processed_target_ids,
    "reset_cycle_order_counts": [len(first_cycle_ids), len(second_cycle_ids)],
    "response_stream": base64.b64encode(b"".join(step.response_stream for step in steps)).decode(),
    "semantic_sha256": projected.semantic_sha256,
    "start_identity_blocked": start_identity_blocked,
    "uncertain_target_blocked": uncertain_target_blocked,
}, separators=(",", ":"), sort_keys=True))
"""
        completed = subprocess.run(
            exact_g1_command(
                root,
                site_packages,
                script,
                (CATALOG, "/inputs/instrument-catalog.json"),
                (CONFIGURATION, "/inputs/engine-configuration.json"),
                (SCHEDULE, "/inputs/target-schedule.json"),
                (market_data, "/inputs/market-data.jsonl"),
                (command_stream, "/inputs/commands.bin"),
                (product_lineage, "/engine/p1-product-lineage.json"),
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
    response_payloads = iter_payloads(base64.b64decode(observed.pop("response_stream")))
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    for raw in response_payloads:
        value = json.loads(raw)
        if value["frame_type"] == "ACK":
            command_raw = commands[value["command_sequence"] - 1]
            journal.accept_command(command_raw)
            parse_paper_acknowledgement(raw)
            journal.record_ack(raw)
        elif value["frame_type"] == "EVENT":
            parse_paper_event_frame(raw)
            journal.record_event(raw)
        else:
            checkpoint = PaperSessionCheckpoint.model_validate_json(
                canonical_json_bytes(value["checkpoint"])
            )
            assert hashlib.sha256(
                canonical_json_bytes(value["checkpoint"])
            ).hexdigest() == value["checkpoint_sha256"]
            assert checkpoint.last_accepted_command == journal.last_accepted_command
    journal.end_of_input()
    assert journal.state is PaperSessionState.STOPPED
    early_commands = (
        commands[0],
        commands[1],
        _command_frame(
            StopPaperEngine(
                command_type="StopPaperEngine", target_engine_run_id=SESSION
            ),
            3,
        ),
    )
    early_journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    for raw in iter_payloads(base64.b64decode(observed.pop("early_response_stream"))):
        value = json.loads(raw)
        if value["frame_type"] == "ACK":
            early_journal.accept_command(early_commands[value["command_sequence"] - 1])
            early_journal.record_ack(raw)
        elif value["frame_type"] == "EVENT":
            early_journal.record_event(raw)
        else:
            PaperSessionCheckpoint.model_validate_json(
                canonical_json_bytes(value["checkpoint"])
            )
    early_journal.end_of_input()
    assert early_journal.state is PaperSessionState.STOPPED
    assert observed == {
        "account_count": 1,
        "balance_currencies": ["BTC", "USDT"],
        "checkpoint_mismatch_rejected": True,
        "early_stop_processed_targets": [
            "11111111-1111-4111-8111-111111111111",
            "44444444-4444-4444-8444-444444444444",
        ],
        "empty_stop_blocked": True,
        "fill_ids": observed["fill_ids"],
        "future_reentry_cancelled": [
            "11111111-1111-4111-8111-111111111111",
            "44444444-4444-4444-8444-444444444444",
        ],
        "future_reentry_cancelled_while_flat": [
            "11111111-1111-4111-8111-111111111111",
            "44444444-4444-4444-8444-444444444444",
        ],
        "order_ids": observed["order_ids"],
        "position": "0",
        "processed_targets": [
            "11111111-1111-4111-8111-111111111111",
            "44444444-4444-4444-8444-444444444444",
        ],
        "reset_cycle_order_counts": [2, 2],
        "semantic_sha256": observed["semantic_sha256"],
        "start_identity_blocked": True,
        "uncertain_target_blocked": True,
    }
    assert len(observed["order_ids"]) == len(set(observed["order_ids"])) == 2
    assert len(observed["fill_ids"]) == len(set(observed["fill_ids"])) == 2
