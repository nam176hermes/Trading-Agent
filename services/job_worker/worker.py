"""Single-job worker orchestration over fenced repository operations."""

from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import replace
from enum import StrEnum
from typing import Callable
from uuid import uuid4

from packages.job_contracts import JobState, JobType, SnapshotPayload
from services.market_data import MarketDataPersistenceOutcome

from .command_registry import (
    FULL_REATTESTATION_ROLLOUT_LIMIT_SECONDS,
    PRESPAWN_FULL_REATTESTATION_COUNT,
    prepare_immediate_spawn,
)
from .errors import WorkerBlockedError
from .process_runner import (
    HeartbeatDecision,
    HeartbeatInstruction,
    ProcessOutcome,
    ProcessRunner,
)
from .results import ResultValidationError, ResultValidator
from .safety_state import SafetyEvidence


class WorkerControl(StrEnum):
    CONTINUE = "CONTINUE"
    CANCEL = "CANCEL"
    STALE = "STALE"


# The claim cannot be heartbeated until three full immutable/semantic scans
# finish and the child identity is proven.  Ten minutes is code-owned: it
# exceeds the reviewed 3 x 120s rollout scan gate with explicit margin.
WORKER_LEASE_SECONDS = 600
assert WORKER_LEASE_SECONDS > (
    PRESPAWN_FULL_REATTESTATION_COUNT
    * FULL_REATTESTATION_ROLLOUT_LIMIT_SECONDS
)


class _ValidationCancelled(ResultValidationError):
    pass


class _ValidationStale(ResultValidationError):
    pass


class _ValidationSafetyDrift(ResultValidationError):
    def __init__(self, reason_code: object) -> None:
        super().__init__("safety evidence changed during result validation")
        self.reason_code = HeartbeatInstruction.safety_drift(reason_code).reason_code


class JobWorker:
    def __init__(
        self,
        repository,
        runner: ProcessRunner,
        validator: ResultValidator,
        *,
        worker_id: str,
        code_commit: str,
        environment: object,
        safety_preflight: Callable[[], SafetyEvidence],
        prepare_spawn: Callable[[object], object] = prepare_immediate_spawn,
        market_data_ingestor: object | None = None,
        lease_seconds: int = WORKER_LEASE_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_seconds != WORKER_LEASE_SECONDS:
            raise ValueError("worker lease is code-owned and cannot be overridden")
        self._repository = repository
        self._runner = runner
        self._validator = validator
        self._worker_id = worker_id
        self._code_commit = code_commit
        self._environment = environment
        self._safety_preflight = safety_preflight
        self._prepare_spawn = prepare_spawn
        self._market_data_ingestor = market_data_ingestor
        self._lease_seconds = lease_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def _worker_heartbeat(self, status: str, claim=None) -> None:
        self._repository.worker_heartbeat(
            self._worker_id,
            self._code_commit,
            status,
            current_job_id=None if claim is None else claim.job_id,
            current_attempt_id=None if claim is None else claim.attempt_id,
            metadata={},
        )

    def run_once(self) -> bool:
        trace_id = f"worker:{uuid4().hex}"
        latest_safety: SafetyEvidence | None = None

        def safety_preflight() -> SafetyEvidence:
            nonlocal latest_safety
            evidence = self._safety_preflight()
            if not isinstance(evidence, SafetyEvidence):
                raise TypeError("worker safety preflight returned invalid evidence")
            latest_safety = evidence
            return evidence

        def bind_latest_safety(outcome: ProcessOutcome) -> ProcessOutcome:
            if latest_safety is None:
                return outcome
            return replace(
                outcome,
                lineage=outcome.lineage.with_final_safety(latest_safety),
            )

        # Authority and its current paper-only safety evidence must still be
        # valid before even an IDLE heartbeat or claim transaction.  A
        # post-startup rotation therefore stops the loop without writing job
        # state under stale authority.
        safety_preflight()
        self._worker_heartbeat("IDLE")
        claimed = self._repository.claim_next(
            self._worker_id,
            self._lease_seconds,
            trace_id,
            allowed_job_types=(JobType.SNAPSHOT,),
        )
        if claimed is None:
            self._worker_heartbeat("IDLE")
            return False
        self._worker_heartbeat("BUSY", claimed)
        started_at = self._clock()
        started = False

        try:
            safety_preflight()
        except WorkerBlockedError as exc:
            finalized = self._repository.finalize_execution(
                claimed, expected_state=JobState.CLAIMED,
                expected_attempt_outcome="CLAIMED", final_state=JobState.BLOCKED,
                reason_code=exc.reason_code, trace_id=trace_id,
                outcome=None, result=None, stream_artifacts=(),
            )
            self._worker_heartbeat("IDLE" if finalized else "UNHEALTHY", None if finalized else claimed)
            return True

        pre_spawn = WorkerControl(self._repository.pre_spawn_control(
            claimed.job_id, claimed.attempt_id, self._worker_id,
            claimed.lease_token, self._lease_seconds,
        ))
        if pre_spawn is WorkerControl.STALE:
            self._worker_heartbeat("UNHEALTHY", claimed)
            return True
        if pre_spawn is WorkerControl.CANCEL:
            finalized = self._repository.finalize_execution(
                claimed, expected_state=JobState.CANCEL_REQUESTED,
                expected_attempt_outcome="CLAIMED", final_state=JobState.CANCELLED,
                reason_code="CANCELLED", trace_id=trace_id,
                outcome=None, result=None, stream_artifacts=(),
            )
            self._worker_heartbeat("IDLE" if finalized else "UNHEALTHY", None if finalized else claimed)
            return True

        market_data_payload = self._market_data_payload(claimed)
        if market_data_payload is not None and self._market_data_ingestor is None:
            finalized = self._repository.finalize_execution(
                claimed, expected_state=JobState.CLAIMED,
                expected_attempt_outcome="CLAIMED", final_state=JobState.BLOCKED,
                reason_code="MARKET_DATA_INGESTOR_UNAVAILABLE", trace_id=trace_id,
                outcome=None, result=None, stream_artifacts=(),
            )
            self._worker_heartbeat(
                "IDLE" if finalized else "UNHEALTHY", None if finalized else claimed
            )
            return True

        def heartbeat(identity) -> HeartbeatDecision | HeartbeatInstruction:
            nonlocal started
            # Every lease/start heartbeat is conditional on a newly opened,
            # still-fresh snapshot.  Do not record RUNNING first and inspect
            # safety afterward.
            try:
                safety_preflight()
            except WorkerBlockedError as exc:
                return HeartbeatInstruction.safety_drift(exc.reason_code)
            if not started:
                if not self._repository.start_attempt(
                    claimed.job_id, claimed.attempt_id, self._worker_id,
                    claimed.lease_token, identity, trace_id,
                ):
                    control = WorkerControl(self._repository.pre_spawn_control(
                        claimed.job_id, claimed.attempt_id, self._worker_id,
                        claimed.lease_token, self._lease_seconds,
                    ))
                    if control is WorkerControl.CANCEL:
                        return HeartbeatDecision.CANCEL
                    return HeartbeatDecision.STALE_LEASE
                started = True
            control = WorkerControl(self._repository.heartbeat_control(
                claimed.job_id, claimed.attempt_id, self._worker_id,
                claimed.lease_token, self._lease_seconds,
            ))
            if control is WorkerControl.CANCEL:
                return HeartbeatDecision.CANCEL
            if control is WorkerControl.STALE:
                return HeartbeatDecision.STALE_LEASE
            return HeartbeatDecision.CONTINUE

        try:
            outcome = self._runner.run(
                lambda: self._prepare_spawn(claimed), self._environment,
                self._command_timeout(claimed.job_type), heartbeat,
                preflight=safety_preflight,
                job_id=claimed.job_id, attempt_id=claimed.attempt_id,
            )
        except WorkerBlockedError as exc:
            # The runner's last-moment preflight and environment revalidation
            # occur before Popen, so the attempt is still CLAIMED here.
            finalized = self._repository.finalize_execution(
                claimed, expected_state=JobState.CLAIMED,
                expected_attempt_outcome="CLAIMED", final_state=JobState.BLOCKED,
                reason_code=exc.reason_code, trace_id=trace_id,
                outcome=None, result=None, stream_artifacts=(),
            )
            self._worker_heartbeat("IDLE" if finalized else "UNHEALTHY", None if finalized else claimed)
            return True
        outcome = bind_latest_safety(outcome)
        if outcome.termination_reason == "STALE_LEASE":
            self._worker_heartbeat("UNHEALTHY", claimed)
            return True
        if outcome.termination_reason is not None:
            finalized = self._finalize_termination(claimed, outcome, trace_id, started=started)
            self._worker_heartbeat("IDLE" if finalized else "UNHEALTHY", None if finalized else claimed)
            return True
        try:
            self._validation_progress(claimed, safety_preflight)
            result = self._validator.validate(
                outcome.result_validator_id, claimed,
                exit_code=outcome.exit_code if outcome.exit_code is not None else -1,
                attempt_started_at=started_at,
                backend_commit=outcome.backend_revision,
                semantic_input_fingerprint=(
                    outcome.lineage.command["semantic_input_fingerprint"]
                ),
                progress=lambda: self._validation_progress(claimed, safety_preflight),
            )
            if market_data_payload is not None:
                self._validation_progress(claimed, safety_preflight)
                try:
                    outcome_metadata = self._market_data_ingestor.ingest(market_data_payload)
                except Exception as exc:
                    raise ResultValidationError("market-data ingestion failed") from exc
                if not isinstance(outcome_metadata, MarketDataPersistenceOutcome):
                    raise ResultValidationError("market-data ingestion returned invalid outcome")
                result = replace(
                    result,
                    validation_metadata={
                        **result.validation_metadata,
                        "market_data_snapshot_digest": outcome_metadata.snapshot_digest,
                        "market_data_inserted": outcome_metadata.inserted,
                    },
                )
            # Validation can be long enough for a previously fresh snapshot to
            # expire. Recheck once more before any SUCCEEDED transition.
            self._validation_progress(claimed, safety_preflight)
            outcome = bind_latest_safety(outcome)
        except ResultValidationError as exc:
            outcome = bind_latest_safety(outcome)
            if isinstance(exc, _ValidationCancelled):
                finalized = self._repository.finalize_execution(
                    claimed, expected_state=JobState.CANCEL_REQUESTED,
                    expected_attempt_outcome="RUNNING", final_state=JobState.CANCELLED,
                    reason_code="CANCELLED", trace_id=trace_id, outcome=outcome,
                    result=None, stream_artifacts=(outcome.stdout, outcome.stderr),
                )
            elif isinstance(exc, _ValidationStale):
                self._worker_heartbeat("UNHEALTHY", claimed)
                return True
            elif isinstance(exc, _ValidationSafetyDrift):
                finalized = self._repository.finalize_execution(
                    claimed, expected_state=JobState.RUNNING,
                    expected_attempt_outcome="RUNNING", final_state=JobState.BLOCKED,
                    reason_code=exc.reason_code, trace_id=trace_id,
                    outcome=outcome, result=None,
                    stream_artifacts=(outcome.stdout, outcome.stderr),
                )
            elif exc.reconciliation_required:
                finalized = self._repository.finalize_execution(
                    claimed, expected_state=JobState.RUNNING,
                    expected_attempt_outcome="RUNNING", final_state=JobState.BLOCKED,
                    reason_code="RESULT_RECONCILIATION_REQUIRED", trace_id=trace_id,
                    outcome=outcome, result=None,
                    stream_artifacts=(outcome.stdout, outcome.stderr),
                )
            elif claimed.attempt_number < claimed.max_attempts:
                finalized = self._repository.finalize_retry(
                    claimed, reason_code="RESULT_VALIDATION_FAILED",
                    trace_id=trace_id, outcome=outcome,
                    stream_artifacts=(outcome.stdout, outcome.stderr),
                )
            else:
                finalized = self._repository.finalize_execution(
                    claimed, expected_state=JobState.RUNNING,
                    expected_attempt_outcome="RUNNING", final_state=JobState.FAILED,
                    reason_code="RESULT_VALIDATION_FAILED", trace_id=trace_id,
                    outcome=outcome, result=None,
                    stream_artifacts=(outcome.stdout, outcome.stderr),
                )
        else:
            outcome = bind_latest_safety(outcome)
            finalized = self._repository.finalize_execution(
                claimed, expected_state=JobState.RUNNING,
                expected_attempt_outcome="RUNNING", final_state=JobState.SUCCEEDED,
                reason_code="RESULT_VALIDATED", trace_id=trace_id,
                outcome=outcome, result=result,
                stream_artifacts=(outcome.stdout, outcome.stderr),
            )
        self._worker_heartbeat("IDLE" if finalized else "UNHEALTHY", None if finalized else claimed)
        return True

    @staticmethod
    def _market_data_payload(claimed: object) -> SnapshotPayload | None:
        payload = getattr(claimed, "payload", None)
        if isinstance(payload, SnapshotPayload) and payload.market_data is not None:
            return payload
        return None

    def _validation_progress(
        self, claimed, safety_preflight: Callable[[], object] | None = None,
    ) -> None:
        try:
            (safety_preflight or self._safety_preflight)()
        except WorkerBlockedError as exc:
            raise _ValidationSafetyDrift(exc.reason_code) from exc
        control = WorkerControl(self._repository.heartbeat_control(
            claimed.job_id, claimed.attempt_id, self._worker_id,
            claimed.lease_token, self._lease_seconds,
        ))
        if control is WorkerControl.CANCEL:
            raise _ValidationCancelled("cancellation requested during result validation")
        if control is WorkerControl.STALE:
            raise _ValidationStale("lease lost during result validation")

    @staticmethod
    def _command_timeout(job_type) -> int:
        from .command_registry import COMMAND_REGISTRY

        return COMMAND_REGISTRY[job_type].timeout_seconds

    def _finalize_termination(
        self, claimed, outcome: ProcessOutcome, trace_id: str, *, started: bool,
    ) -> bool:
        reason = outcome.termination_reason
        if reason == "CANCELLED":
            expected, attempt_outcome = JobState.CANCEL_REQUESTED, "RUNNING" if started else "CLAIMED"
            final, code = JobState.CANCELLED, "CANCELLED"
        elif reason == "TIMEOUT":
            expected, attempt_outcome = JobState.RUNNING, "RUNNING"
            final, code = JobState.TIMED_OUT, "PROCESS_TIMEOUT"
        elif reason == "SAFETY_DRIFT":
            expected = JobState.RUNNING if started else JobState.CLAIMED
            attempt_outcome = "RUNNING" if started else "CLAIMED"
            final = JobState.BLOCKED
            code = outcome.safety_reason_code or "SAFETY_DRIFT"
        else:
            expected = JobState.RUNNING if started else JobState.CLAIMED
            attempt_outcome = "RUNNING" if started else "CLAIMED"
            final, code = JobState.BLOCKED, reason or "PROCESS_TERMINATION_UNPROVEN"
        return self._repository.finalize_execution(
            claimed, expected_state=expected, expected_attempt_outcome=attempt_outcome,
            final_state=final, reason_code=code, trace_id=trace_id,
            outcome=outcome, result=None,
            stream_artifacts=(outcome.stdout, outcome.stderr),
        )


__all__ = ["JobWorker", "WorkerControl"]
