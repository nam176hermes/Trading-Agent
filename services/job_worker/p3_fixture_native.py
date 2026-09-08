"""Parent-owned execution of three pinned P3 native fixture replicas."""

from pathlib import Path
from dataclasses import dataclass
from datetime import UTC, datetime
import io
from uuid import NAMESPACE_URL, uuid5

from packages.engine_contracts import (
    CURRENT_SCHEMA_VERSION, EngineCommandEnvelope, RunBacktest, canonical_json_bytes,
    payload_digest,
)
from packages.job_contracts import AlphaCampaignPayload, JobType
from packages.nautilus_runtime_contracts.result import P1ValidatedResult, validate_p1_result
from services.job_store.worker_repository import ClaimedJob
from .artifacts import ArtifactWriter
from .engine_profiles import P1_REAL_BACKTEST_POLICY
from .engine_results import EngineResultValidator
from .p1_engine_spawn import P1EngineSpawnProvider
from .process_runner import ProcessOutcome, ProcessRunner
from .results import ResultValidator


from .p3_native_runner import PINNED_ENGINE_VERSION


class NativeFixtureError(RuntimeError):
    """A native fixture cannot establish qualification; retain actual child evidence."""

    def __init__(self, message, *, completed=(), outcome=None):
        super().__init__(message)
        self.completed = tuple(completed)
        self.outcome = outcome


@dataclass(frozen=True, slots=True)
class NativeFixtureRun:
    replica: int
    request: EngineCommandEnvelope
    outcome: ProcessOutcome
    result: P1ValidatedResult


def run_native_fixture(
    job: ClaimedJob, command: RunBacktest, provider: P1EngineSpawnProvider,
    *, artifact_root: Path, heartbeat, preflight,
) -> tuple[NativeFixtureRun, ...]:
    """Run three credential-free replicas; the parent owns SQL and final cleanup.

    The caller resolves the command from the approved fixture plan. These runs
    remain children of the same durable ALPHA_CAMPAIGN attempt, not new jobs.
    """
    now = datetime.now(UTC)
    if (
        type(job) is not ClaimedJob or job.job_type is not JobType.ALPHA_CAMPAIGN
        or type(job.payload) is not AlphaCampaignPayload
        or job.payload.operation != "PARITY"
        or job.payload.logical_trial_id != "p3-integration-fixture-v1"
        or job.lease_expires_at <= now
        or type(provider) is not P1EngineSpawnProvider
        or type(command) is not RunBacktest
        or P1_REAL_BACKTEST_POLICY.engine_version != PINNED_ENGINE_VERSION
    ):
        raise NativeFixtureError("exact current P3 fixture and pinned native authority required")
    command = RunBacktest.model_validate(command)
    results = []
    outcome = None
    try:
        for replica in range(1, 4):
            def identifier(purpose):
                return uuid5(NAMESPACE_URL, f"p3-fixture:{job.job_id}:{job.attempt_id}:{replica}:{purpose}")

            request = EngineCommandEnvelope(
                message_id=identifier("message"), correlation_id=identifier("correlation"),
                causation_id=identifier("causation"), engine_run_id=identifier("run"),
                stream_sequence=1, event_time=now, initialization_time=now,
                schema_version=CURRENT_SCHEMA_VERSION, producer_identity=job.worker_id,
                source_commit=job.payload.expected_source.commit_sha,
                config_digest=payload_digest({name: getattr(command, name) for name in (
                    "engine_configuration", "instrument_catalog", "strategy_configuration",
                )}), payload_digest=payload_digest(command), payload=command,
            )
            root = artifact_root / f"r{replica}"
            writer = ArtifactWriter(root)
            writer.capture_stream(job.job_id, job.attempt_id, "request", io.BytesIO(canonical_json_bytes(request)))
            outcome = ProcessRunner(writer).run(
                lambda: provider.prepare(request), None, None, heartbeat,
                job_id=job.job_id, attempt_id=job.attempt_id, preflight=preflight,
            )
            if (
                outcome.exit_code != 0 or outcome.termination_reason is not None
                or outcome.result_validator_id != P1_REAL_BACKTEST_POLICY.result_validator_id
            ):
                raise NativeFixtureError("native child failed or process cleanup was not proven")
            raw = ResultValidator(root, root, root)._read_p3_stream(job, outcome.stdout)
            events = EngineResultValidator._parse_canonical_batch(
                raw, request, lambda: preflight(), p1_event_stream=True,
            )
            result = validate_p1_result(
                request, events, raw=raw,
                expected_closure_digest=P1_REAL_BACKTEST_POLICY.closure_sha256,
            )
            if results and result.semantic_sha256 != results[0].result.semantic_sha256:
                raise NativeFixtureError("native fixture replicas disagree")
            results.append(NativeFixtureRun(replica, request, outcome, result))
    except Exception as error:
        raise NativeFixtureError(str(error), completed=results, outcome=outcome) from error
    return tuple(results)


__all__ = ["NativeFixtureError", "NativeFixtureRun", "run_native_fixture"]


def build_native_fixture_inputs(inputs_root: Path):
    """Materialize the exact four-input synthetic P1 native qualification fixture."""
    import hashlib
    from uuid import UUID
    from .engine_artifacts import EngineArtifactBinding
    from packages.engine_contracts import ArtifactReference

    values = (
        (
            "engine_configuration",
            b'{"account_type":"CASH","allow_leverage":false,"allow_short":false,"bar_execution":false,"fee_model":"fixed-rate","fee_rate":"0.001","fill_model":"deterministic","load_state":false,"logging_bypass":true,"network_access":false,"oms_type":"NETTING","run_analysis":false,"save_state":false,"schema_version":"nautilus-p1-engine-configuration-v1","starting_balance":"1000000","starting_currency":"USDT","venue":"BINANCE"}\n',
            "application/json",
        ),
        (
            "instrument_catalog",
            b'{"base_currency":"BTC","instrument_id":"BTCUSDT.BINANCE","min_notional":"10","min_quantity":"0.000001","price_precision":2,"product_type":"crypto_spot","provenance_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","quote_currency":"USDT","schema_version":"nautilus-p1-instrument-catalog-v1","size_precision":6,"step_size":"0.000001","symbol":"BTCUSDT","tick_size":"0.01","venue":"BINANCE"}\n',
            "application/json",
        ),
        (
            "strategy_configuration",
            b'{"schema_version":"nautilus-p1-target-schedule-v1","targets":[{"effective_at":"2026-08-05T12:00:00Z","positions":[{"instrument":{"product_type":"crypto_spot","symbol":"BTCUSDT","venue":"BINANCE"},"target_weight":"1"}],"schema_version":"1.0.0","source_signal_ids":["22222222-2222-4222-8222-222222222222"],"target_id":"11111111-1111-4111-8111-111111111111"},{"effective_at":"2026-08-05T12:01:00Z","positions":[{"instrument":{"product_type":"crypto_spot","symbol":"BTCUSDT","venue":"BINANCE"},"target_weight":"0"}],"schema_version":"1.0.0","source_signal_ids":["33333333-3333-4333-8333-333333333333"],"target_id":"44444444-4444-4444-8444-444444444444"}]}\n',
            "application/json",
        ),
        (
            "market_data",
            b'{"ask":"100","bid":"99","close":"100","event_time":"2026-08-05T12:00:00Z","high":"101","low":"98","open":"99","quote_time":"2026-08-05T12:00:00Z","sequence":1,"volume":"1000000"}\n'
            b'{"ask":"102","bid":"101","close":"102","event_time":"2026-08-05T12:01:00Z","high":"103","low":"100","open":"101","quote_time":"2026-08-05T12:01:00Z","sequence":2,"volume":"1000000"}\n',
            "application/jsonl",
        ),
    )
    references: list[ArtifactReference] = []
    bindings: list[EngineArtifactBinding] = []
    for index, (name, raw, media_type) in enumerate(values, start=1):
        source = inputs_root / name
        source.write_bytes(raw)
        source.chmod(0o400)
        reference = ArtifactReference(
            artifact_id=UUID(
                f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111"
            ),
            sha256=hashlib.sha256(raw).hexdigest(),
            media_type=media_type,
        )
        references.append(reference)
        bindings.append(EngineArtifactBinding(reference, source))
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 1, tzinfo=UTC),
    )
    return command, tuple(bindings)
