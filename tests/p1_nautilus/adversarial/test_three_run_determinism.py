from __future__ import annotations

from argparse import Namespace
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import time

from packages.job_contracts import JobState
from packages.nautilus_runtime_contracts.result import P1_RESULT_VALIDATOR_ID
from scripts.run_p1_nautilus_vertical_slice import (
    END,
    START,
    _FixedProjectionAuthorityFactory,
    _artifact_references,
)
from services.job_worker.artifacts import ArtifactMetadata
from services.job_worker.engine_authority import BacktestEngineAuthorityFactory
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
from services.job_worker.engine_results import EngineResultValidator
from tests.jobs.test_engine_result_validation import CODE_COMMIT, NOW
from tests.jobs.test_engine_worker_lifecycle import Repository
from tests.jobs.test_p1_parity_composition import (
    _LoggedIngestor,
    _P1Validator,
    _final,
    _worker,
)
from tests.nautilus_runtime_contracts.test_result import _p1_claim
from tests.p1_nautilus.test_instrument_factory_native import (
    exact_g1_command,
    exact_g1_runtime,
)


ROOT = Path(__file__).parents[3]
FIXTURES = ROOT / "tests/fixtures/p1_nautilus"


def _stdout(root: Path, claim: object, raw: bytes) -> ArtifactMetadata:
    job_id = getattr(claim, "job_id")
    attempt_id = getattr(claim, "attempt_id")
    path = root / job_id / attempt_id / "stdout.log"
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_bytes(raw)
    path.chmod(0o600)
    return ArtifactMetadata(
        "stdout",
        f"{job_id}/{attempt_id}/stdout.log",
        hashlib.sha256(raw).hexdigest(),
        len(raw),
        "application/octet-stream",
        False,
    )


def _claims_and_requests() -> tuple[tuple[object, object], ...]:
    base = _p1_claim()
    payload = base.payload.engine_backtest.model_copy(
        update={
            **_artifact_references(),
            "start_time": datetime.fromisoformat(START.replace("Z", "+00:00")),
            "end_time": datetime.fromisoformat(END.replace("Z", "+00:00")),
        }
    )
    factory = BacktestEngineAuthorityFactory(code_commit=CODE_COMMIT, clock=lambda: NOW)
    result = []
    for index in range(1, 4):
        claim = replace(
            base,
            job_id=f"job_{index:032x}",
            attempt_id=f"attempt_{index:032x}",
            payload=base.payload.model_copy(update={"engine_backtest": payload}),
        )
        result.append((claim, factory.from_claim(claim)))
    return tuple(result)


def _native_streams(
    tmp_path: Path, pairs: tuple[tuple[object, object], ...]
) -> tuple[tuple[bytes, ...], dict[str, int]] | None:
    request_path = tmp_path / "requests.json"
    request_path.write_text(
        json.dumps(
            [request.model_dump(mode="json") for _, request in pairs],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="ascii",
    )
    entered = time.monotonic()
    with exact_g1_runtime() as runtime:
        if runtime is None:
            assert "nautilus_trader" not in subprocess.run(
                ["uv", "run", "python", "-c", "import sys;print(' '.join(sys.path))"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            return None
        closure_attestation_ms = round((time.monotonic() - entered) * 1000)
        root, site_packages = runtime
        script = r'''
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

def freeze(value):
    if type(value) is dict:
        return tuple((key, freeze(item)) for key, item in sorted(value.items()))
    if type(value) is list:
        return tuple(freeze(item) for item in value)
    return value

records = json.loads(open(sys.argv[3], "rb").read())
configuration = tuple(sorted(json.loads(open(sys.argv[4], "rb").read()).items()))
catalog = tuple(sorted(json.loads(open(sys.argv[5], "rb").read()).items()))
schedule = freeze(json.loads(open(sys.argv[6], "rb").read()))
market_data = open(sys.argv[7], "rb").read()
streams = []
for record in records:
    payload = record.pop("payload")
    request = RunBacktestRequest(
        **record,
        command_type=payload["command_type"],
        engine_configuration=ArtifactReference(**payload["engine_configuration"]),
        instrument_catalog=ArtifactReference(**payload["instrument_catalog"]),
        strategy_configuration=ArtifactReference(**payload["strategy_configuration"]),
        market_data=ArtifactReference(**payload["market_data"]),
        start_time=payload["start_time"],
        end_time=payload["end_time"],
    )
    inputs = RuntimeInputs(request, configuration, catalog, schedule, market_data)
    run = run_backtest(inputs)
    balances = {currency: total for currency, total, _, _ in run.balance_facts}
    commissions = dict(run.commission_facts)
    stream = project_event_stream(
        inputs,
        run,
        CompletionAuthority(
            target_count=len(run.processed_target_ids),
            order_count=run.order_count,
            fill_count=run.fill_count,
            final_cash=balances["USDT"],
            final_position=run.position_quantity,
            fees=commissions["USDT"],
            realized_pnl=run.position_realized_pnl,
            unrealized_pnl=run.position_unrealized_pnl,
        ),
        closure_digest="97185d4c0b6090353ba51c1aab25ed4ea4dfab08113b655fac623af9e7db2b80",
        upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
    )
    streams.append(stream.jsonl.decode("ascii"))
print(json.dumps({"streams": streams}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
'''
        command = exact_g1_command(
            root,
            site_packages,
            script,
            (request_path, "/inputs/requests.json"),
            (
                FIXTURES / "contracts/engine-configuration.json",
                "/inputs/engine-configuration.json",
            ),
            (
                FIXTURES / "contracts/instrument-catalog.json",
                "/inputs/instrument-catalog.json",
            ),
            (
                FIXTURES / "contracts/target-schedule.json",
                "/inputs/target-schedule.json",
            ),
            (FIXTURES / "e2e/btcusdt-1m.jsonl", "/inputs/market-data.jsonl"),
        )
        native_started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd="/",
            env={},
            check=False,
            capture_output=True,
            text=True,
        )
        native_execution_ms = round((time.monotonic() - native_started) * 1000)
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    streams = tuple(
        value.encode("ascii") for value in json.loads(completed.stdout)["streams"]
    )
    assert len(streams) == 3
    return streams, {
        "closure_attestation_milliseconds": closure_attestation_ms,
        "native_execution_milliseconds": native_execution_ms,
        "product_peak_memory_kib": resource.getrusage(
            resource.RUSAGE_CHILDREN
        ).ru_maxrss,
    }


def test_three_distinct_jobs_preserve_native_semantics_and_durable_portfolio(
    tmp_path: Path,
) -> None:
    pairs = _claims_and_requests()
    native = _native_streams(tmp_path, pairs)
    if native is None:
        return
    streams, metrics = native
    authority_factory = _FixedProjectionAuthorityFactory(
        Namespace(instrument_catalog=FIXTURES / "contracts/instrument-catalog.json")
    )
    results = []
    replay_milliseconds = 0
    for index, ((claim, request), raw) in enumerate(zip(pairs, streams, strict=True)):
        artifact_root = tmp_path / f"run-{index}"
        validated = EngineResultValidator(
            artifact_root,
            p1_product_closure_sha256=P1_REAL_BACKTEST_POLICY.closure_sha256,
        ).validate(
            P1_RESULT_VALIDATOR_ID,
            claim,
            request=request,
            stdout=_stdout(artifact_root, claim, raw),
            exit_code=0,
        )
        calls: list[str] = []
        ingestor = _LoggedIngestor(calls)

        def verify(events, authority, projection, *, batch_sha256):
            nonlocal replay_milliseconds
            from packages.engine_portfolio_projection.parity import (
                verify_p1_portfolio_parity,
            )

            started = time.monotonic()
            receipt = verify_p1_portfolio_parity(
                events, authority, projection, batch_sha256=batch_sha256
            )
            replay_milliseconds += round((time.monotonic() - started) * 1000)
            return receipt

        repository = Repository(claim)
        assert _worker(
            repository,
            _P1Validator(validated),
            ingestor,
            authority_factory=authority_factory,
            parity_verifier=verify,
        ).run_once()
        final = _final(repository)
        assert final["final_state"] is JobState.SUCCEEDED
        result = final["result"]
        parity = result.validation_metadata["p1_portfolio_parity"]
        business_state = {
            name: parity[name]
            for name in (
                "account_currency",
                "terminal_average_entry_price",
                "terminal_cash",
                "terminal_fees",
                "terminal_mark_price",
                "terminal_position",
                "terminal_realized_pnl",
                "terminal_unrealized_pnl",
            )
        }
        results.append(
            {
                "attempt_id": claim.attempt_id,
                "batch_sha256": validated.sha256,
                "event_count": validated.profile_result.event_count,
                "job_id": claim.job_id,
                "output_bytes": len(raw),
                "portfolio_state_sha256": hashlib.sha256(
                    json.dumps(
                        business_state,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("ascii")
                ).hexdigest(),
                "raw_portfolio_state_sha256": parity["portfolio_state_hash"],
                "request_sha256": result.validation_metadata[
                    "engine_request_sha256"
                ],
                "semantic_sha256": validated.profile_result.semantic_sha256,
            }
        )

    assert len({result["job_id"] for result in results}) == 3
    assert len({result["attempt_id"] for result in results}) == 3
    assert len({result["request_sha256"] for result in results}) == 3
    assert len({result["batch_sha256"] for result in results}) == 3
    assert {result["semantic_sha256"] for result in results} == {
            "728d439596c540683afa524fc8090cb8de4878878e96e1d21283f222318681b9"
    }
    assert len({result["portfolio_state_sha256"] for result in results}) == 1
    evidence_path = os.environ.get("P1_QUALIFICATION_METRICS_PATH")
    if evidence_path is not None:
        Path(evidence_path).write_text(
            json.dumps(
                {
                    **metrics,
                    "replay_milliseconds": replay_milliseconds,
                    "runs": results,
                    "schema": "trading-agent-p1-product-metrics/v1",
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
            encoding="ascii",
        )
