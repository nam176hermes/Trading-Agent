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
    """A native fixture cannot establish qualification."""


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
    return tuple(results)


__all__ = ["NativeFixtureError", "NativeFixtureRun", "run_native_fixture"]
