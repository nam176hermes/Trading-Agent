"""Single-job worker orchestration over fenced repository operations."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Callable, Protocol, cast
from uuid import UUID, uuid4

from packages.job_contracts import (
    JobState,
    JobType,
    SnapshotPayload,
)
from .command_registry import (
    FULL_REATTESTATION_ROLLOUT_LIMIT_SECONDS,
    PRESPAWN_FULL_REATTESTATION_COUNT,
    prepare_immediate_spawn,
)
from .errors import WorkerBlockedError
from .engine_spawn_interface import EngineSpawnError
from .process_runner import (
    HeartbeatDecision,
    HeartbeatInstruction,
    ProcessOutcome,
    ProcessRunner,
)
from .results import ResultValidationError, ResultValidator
from .safety_state import SafetyEvidence

if TYPE_CHECKING:
    from packages.engine_contracts import EngineCommandEnvelope
    from packages.engine_event_ledger import EngineRunProjection, StoredEngineEvent
    from packages.engine_portfolio_projection.models import ProjectionAuthority
    from packages.engine_portfolio_projection.parity import P1PortfolioParityReceipt
    from packages.nautilus_runtime_contracts.events import P1Event
    from .engine_results import ValidatedEngineEventBatch


class P1ProjectionAuthorityFactory(Protocol):
    def from_request(self, request: EngineCommandEnvelope) -> ProjectionAuthority: ...


class P1PortfolioParityVerifier(Protocol):
    def __call__(
        self,
        events: tuple[P1Event, ...],
        authority: ProjectionAuthority,
        engine_run_projection: EngineRunProjection,
        *,
        batch_sha256: str,
    ) -> P1PortfolioParityReceipt: ...


class P1DurableEventReader(Protocol):
    def load_events(self, engine_run_id: UUID) -> tuple[StoredEngineEvent, ...]: ...

    def load_projection(self, engine_run_id: UUID) -> EngineRunProjection | None: ...


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


class _EngineEventIngestionBlocked(ResultValidationError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


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
        engine_authority_factory: object | None = None,
        engine_spawn_provider: object | None = None,
        engine_result_validator: object | None = None,
        engine_event_ingestor: object | None = None,
        p1_projection_authority_factory: P1ProjectionAuthorityFactory | None = None,
        p1_portfolio_parity_verifier: P1PortfolioParityVerifier | None = None,
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
        self._engine_authority_factory = engine_authority_factory
        self._engine_spawn_provider = engine_spawn_provider
        self._engine_result_validator = engine_result_validator
        self._engine_event_ingestor = engine_event_ingestor
        self._p1_projection_authority_factory = p1_projection_authority_factory
        self._p1_portfolio_parity_verifier = p1_portfolio_parity_verifier
        if (p1_projection_authority_factory is None) != (
            p1_portfolio_parity_verifier is None
        ):
            raise ValueError("complete P1 portfolio parity authority is required")
        engine_components = (
            engine_authority_factory,
            engine_spawn_provider,
            engine_result_validator,
            engine_event_ingestor,
        )
        if any(component is not None for component in engine_components) and not all(
            component is not None for component in engine_components
        ):
            raise ValueError(
                "complete engine execution and durable-ingestion authority is required"
            )
        self._engine_backtest_job_type = getattr(JobType, "BACKTEST", None)
        self._engine_backtest_enabled = (
            self._engine_backtest_job_type is not None
            and all(component is not None for component in engine_components)
        )
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
            allowed_job_types=(
                (JobType.SNAPSHOT, self._engine_backtest_job_type)
                if self._engine_backtest_enabled
                else (JobType.SNAPSHOT,)
            ),
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

        engine_request = None
        if (
            self._engine_backtest_job_type is not None
            and claimed.job_type is self._engine_backtest_job_type
        ):
            from packages.engine_event_ledger import EngineEventConflictError
            from packages.job_contracts import (
                EngineBacktestPayload,
                EngineBacktestSimulationPayload,
            )

            if (
                type(claimed.payload)
                not in {EngineBacktestPayload, EngineBacktestSimulationPayload}
                or not self._engine_backtest_enabled
            ):
                finalized = self._repository.finalize_execution(
                    claimed,
                    expected_state=JobState.CLAIMED,
                    expected_attempt_outcome="CLAIMED",
                    final_state=JobState.BLOCKED,
                    reason_code="ENGINE_BACKTEST_AUTHORITY_REQUIRED",
                    trace_id=trace_id,
                    outcome=None,
                    result=None,
                    stream_artifacts=(),
                )
                self._worker_heartbeat(
                    "IDLE" if finalized else "UNHEALTHY",
                    None if finalized else claimed,
                )
                return True
            try:
                engine_request = self._engine_authority_factory.from_claim(claimed)
            except (TypeError, ValueError):
                finalized = self._repository.finalize_execution(
                    claimed,
                    expected_state=JobState.CLAIMED,
                    expected_attempt_outcome="CLAIMED",
                    final_state=JobState.BLOCKED,
                    reason_code="ENGINE_BACKTEST_AUTHORITY_INVALID",
                    trace_id=trace_id,
                    outcome=None,
                    result=None,
                    stream_artifacts=(),
                )
                self._worker_heartbeat(
                    "IDLE" if finalized else "UNHEALTHY",
                    None if finalized else claimed,
                )
                return True
            try:
                prior_receipt = self._engine_event_ingestor.load_job_receipt(
                    claimed.job_id
                )
            except EngineEventConflictError:
                reason_code = "ENGINE_EVENT_IDENTITY_CONFLICT"
            except Exception:
                reason_code = "ENGINE_EVENT_RECONCILIATION_UNAVAILABLE"
            else:
                reason_code = (
                    "ENGINE_EVENT_RECONCILIATION_REQUIRED"
                    if prior_receipt is not None
                    else None
                )
            if reason_code is not None:
                finalized = self._repository.finalize_execution(
                    claimed,
                    expected_state=JobState.CLAIMED,
                    expected_attempt_outcome="CLAIMED",
                    final_state=JobState.BLOCKED,
                    reason_code=reason_code,
                    trace_id=trace_id,
                    outcome=None,
                    result=None,
                    stream_artifacts=(),
                )
                self._worker_heartbeat(
                    "IDLE" if finalized else "UNHEALTHY",
                    None if finalized else claimed,
                )
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
            prepare_spawn = (
                (lambda: self._engine_spawn_provider.prepare(engine_request))
                if engine_request is not None
                else (lambda: self._prepare_spawn(claimed))
            )
            outcome = self._runner.run(
                prepare_spawn,
                self._environment,
                (
                    None
                    if engine_request is not None
                    else self._command_timeout(claimed.job_type)
                ),
                heartbeat,
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
        except EngineSpawnError as exc:
            # Engine authority is consumed before Popen.  A closure, sandbox,
            # or protected transport refusal therefore leaves the attempt in
            # CLAIMED and is safe to finalize without process artifacts.
            finalized = self._repository.finalize_execution(
                claimed,
                expected_state=JobState.CLAIMED,
                expected_attempt_outcome="CLAIMED",
                final_state=JobState.BLOCKED,
                reason_code=exc.reason,
                trace_id=trace_id,
                outcome=None,
                result=None,
                stream_artifacts=(),
            )
            self._worker_heartbeat(
                "IDLE" if finalized else "UNHEALTHY",
                None if finalized else claimed,
            )
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
            if engine_request is not None:
                from packages.engine_event_ledger import (
                    EngineEventConflictError,
                    EngineEventSequenceBlockedError,
                    InvalidEngineEventBatchError,
                )

                result = self._engine_result_validator.validate(
                    outcome.result_validator_id,
                    claimed,
                    request=engine_request,
                    stdout=outcome.stdout,
                    exit_code=(
                        outcome.exit_code if outcome.exit_code is not None else -1
                    ),
                    progress=lambda: self._validation_progress(
                        claimed, safety_preflight
                    ),
                )
                from .engine_results import ValidatedEngineEventBatch

                if (
                    type(result) is not ValidatedEngineEventBatch
                    or type(result.validation_metadata) is not dict
                ):
                    raise _EngineEventIngestionBlocked(
                        "ENGINE_EVENT_BATCH_INVALID",
                        "engine result validator returned an invalid batch",
                    )
                sealed_validation_metadata = dict(result.validation_metadata)
                self._validation_progress(claimed, safety_preflight)
                try:
                    receipt = self._engine_event_ingestor.ingest_for_job(
                        result, claimed=claimed
                    )
                except EngineEventConflictError as exc:
                    raise _EngineEventIngestionBlocked(
                        "ENGINE_EVENT_IDENTITY_CONFLICT",
                        "durable engine-event identity conflicts with accepted state",
                    ) from exc
                except EngineEventSequenceBlockedError as exc:
                    raise _EngineEventIngestionBlocked(
                        f"ENGINE_EVENT_{exc.reason.value}",
                        "durable engine-event sequence cannot advance",
                    ) from exc
                except InvalidEngineEventBatchError as exc:
                    raise _EngineEventIngestionBlocked(
                        "ENGINE_EVENT_BATCH_INVALID",
                        "validated engine-event batch was rejected",
                    ) from exc
                except Exception as ingest_error:
                    try:
                        receipt = self._engine_event_ingestor.load_job_receipt(
                            claimed.job_id
                        )
                    except EngineEventConflictError as exc:
                        raise _EngineEventIngestionBlocked(
                            "ENGINE_EVENT_IDENTITY_CONFLICT",
                            "durable engine-event reconciliation conflicted",
                        ) from exc
                    except Exception:
                        raise _EngineEventIngestionBlocked(
                            "ENGINE_EVENT_RECONCILIATION_REQUIRED",
                            "durable engine-event ingestion failed and could not "
                            "be reconciled",
                        ) from ingest_error
                    if receipt is None:
                        raise _EngineEventIngestionBlocked(
                            "ENGINE_EVENT_RECONCILIATION_REQUIRED",
                            "durable engine-event ingestion failed without a receipt",
                        ) from ingest_error
                if result.profile_result is None:
                    self._validation_progress(claimed, safety_preflight)
                if result.validation_metadata != sealed_validation_metadata:
                    raise _EngineEventIngestionBlocked(
                        "ENGINE_EVENT_BATCH_INVALID",
                        "validated engine-event authority changed during ingestion",
                    )
                # Receipt attachment is in-memory only; exact P1 result metadata is
                # persisted by finalization after durable reload and parity below.
                result = self._with_engine_event_receipt(result, receipt)
                if result.profile_result is not None:
                    result = self._with_p1_portfolio_parity(
                        result,
                        receipt,
                        engine_request,
                    )
            else:
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
                # The paper-release projection intentionally excludes the
                # canonical market-data persistence package.  Resolve this
                # runtime-only type only when a P10 payload actually reaches
                # the injected core ingestor.
                from services.market_data import MarketDataPersistenceOutcome

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
            elif isinstance(exc, _EngineEventIngestionBlocked):
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
    def _with_engine_event_receipt(
        result: ValidatedEngineEventBatch,
        receipt: object,
    ) -> ValidatedEngineEventBatch:
        from packages.engine_contracts import canonical_json_bytes
        from packages.engine_event_ledger import EngineEventBatchReceipt
        from .engine_results import ValidatedEngineEventBatch

        if (
            type(result) is not ValidatedEngineEventBatch
            or type(receipt) is not EngineEventBatchReceipt
            or not result.events
        ):
            raise _EngineEventIngestionBlocked(
                "ENGINE_EVENT_RECEIPT_INVALID",
                "durable engine-event ingestion returned an invalid receipt",
            )
        try:
            first = result.events[0]
            last = result.events[-1]
            identity_bytes = canonical_json_bytes(
                {
                    "artifact_type": result.artifact_type,
                    "media_type": result.media_type,
                    "relative_ref": result.relative_ref,
                    "sha256": result.sha256,
                    "size_bytes": result.size_bytes,
                    "truncated": result.truncated,
                    "validation_metadata": result.validation_metadata,
                    "validator_id": result.validator_id,
                }
            )
            expected = {
                "batch_sha256": result.sha256,
                "ingestion_digest": hashlib.sha256(identity_bytes).hexdigest(),
                "job_id": result.validation_metadata.get("job_id"),
                "attempt_id": result.validation_metadata.get("attempt_id"),
                "engine_run_id": first.engine_run_id,
                "event_count": len(result.events),
                "first_sequence": first.stream_sequence,
                "last_sequence": last.stream_sequence,
                "last_digest": hashlib.sha256(
                    canonical_json_bytes(last)
                ).hexdigest(),
            }
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise _EngineEventIngestionBlocked(
                "ENGINE_EVENT_RECEIPT_INVALID",
                "durable engine-event receipt could not be verified",
            ) from exc
        if receipt.model_dump(mode="python") != expected:
            raise _EngineEventIngestionBlocked(
                "ENGINE_EVENT_RECEIPT_INVALID",
                "durable engine-event receipt differs from the validated batch",
            )
        return replace(
            result,
            validation_metadata={
                **result.validation_metadata,
                "engine_event_receipt": receipt.model_dump(mode="json"),
            },
        )

    def _with_p1_portfolio_parity(
        self,
        result: ValidatedEngineEventBatch,
        receipt: object,
        request: object,
    ) -> ValidatedEngineEventBatch:
        from packages.engine_contracts import EngineCommandEnvelope
        from packages.engine_event_ledger import (
            EngineEventBatchReceipt,
            EngineRunProjection,
            StoredEngineEvent,
        )
        from packages.engine_portfolio_projection.models import ProjectionAuthority
        from packages.engine_portfolio_projection.parity import (
            P1PortfolioParityError,
            P1PortfolioParityReceipt,
        )
        from packages.nautilus_runtime_contracts.result import P1ValidatedResult
        from .engine_results import ValidatedEngineEventBatch

        profile = result.profile_result
        if (
            type(result) is not ValidatedEngineEventBatch
            or type(profile) is not P1ValidatedResult
            or type(receipt) is not EngineEventBatchReceipt
            or type(request) is not EngineCommandEnvelope
            or self._p1_projection_authority_factory is None
            or self._p1_portfolio_parity_verifier is None
        ):
            raise _EngineEventIngestionBlocked(
                "P1_PORTFOLIO_PARITY_UNAVAILABLE",
                "complete P1 portfolio parity authority is unavailable",
            )
        try:
            expected_events = tuple(
                StoredEngineEvent.from_envelope(
                    event, batch_sha256=receipt.batch_sha256
                )
                for event in result.events
            )
            durable_reader = cast(P1DurableEventReader, self._engine_event_ingestor)
            durable_events = durable_reader.load_events(
                receipt.engine_run_id
            )
            durable_projection = durable_reader.load_projection(
                receipt.engine_run_id
            )
        except Exception as exc:
            raise _EngineEventIngestionBlocked(
                "P1_PORTFOLIO_PARITY_UNAVAILABLE",
                "durable P1 portfolio authority could not be reloaded",
            ) from exc
        if (
            durable_events != expected_events
            or type(durable_projection) is not EngineRunProjection
            or durable_projection.engine_run_id != receipt.engine_run_id
            or durable_projection.batch_sha256 != receipt.batch_sha256
            or durable_projection.semantic_digest != profile.semantic_sha256
            or durable_projection.request_message_id != request.message_id
            or durable_projection.event_count != receipt.event_count
            or durable_projection.last_sequence != receipt.last_sequence
            or durable_projection.last_digest != receipt.last_digest
            or profile.batch_sha256 != receipt.batch_sha256
        ):
            raise _EngineEventIngestionBlocked(
                "P1_PORTFOLIO_PARITY_UNAVAILABLE",
                "durable P1 portfolio authority differs from the validated batch",
            )
        try:
            authority = self._p1_projection_authority_factory.from_request(request)
        except Exception as exc:
            raise _EngineEventIngestionBlocked(
                "P1_PORTFOLIO_PARITY_UNAVAILABLE",
                "P1 portfolio projection authority could not be constructed",
            ) from exc
        if (
            type(authority) is not ProjectionAuthority
            or authority.request_message_id != durable_projection.request_message_id
        ):
            raise _EngineEventIngestionBlocked(
                "P1_PORTFOLIO_PARITY_UNAVAILABLE",
                "P1 portfolio projection authority is inconsistent",
            )
        try:
            parity = self._p1_portfolio_parity_verifier(
                profile.events,
                authority,
                durable_projection,
                batch_sha256=receipt.batch_sha256,
            )
        except P1PortfolioParityError as exc:
            raise _EngineEventIngestionBlocked(
                "P1_PORTFOLIO_PARITY_MISMATCH",
                "P1 engine and reducer portfolio state do not match",
            ) from exc
        except Exception as exc:
            raise _EngineEventIngestionBlocked(
                "P1_PORTFOLIO_PARITY_UNAVAILABLE",
                "P1 portfolio parity verification is unavailable",
            ) from exc
        try:
            closed_parity = P1PortfolioParityReceipt.model_validate(
                {
                    name: getattr(parity, name)
                    for name in P1PortfolioParityReceipt.model_fields
                }
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise _EngineEventIngestionBlocked(
                "P1_PORTFOLIO_PARITY_MISMATCH",
                "P1 portfolio parity receipt is invalid",
            ) from exc
        if (
            type(parity) is not P1PortfolioParityReceipt
            or closed_parity.engine_run_id != durable_projection.engine_run_id
            or closed_parity.batch_sha256 != receipt.batch_sha256
            or closed_parity.semantic_digest != durable_projection.semantic_digest
            or closed_parity.request_message_id != durable_projection.request_message_id
            or closed_parity.engine_event_count != durable_projection.event_count
            or closed_parity.engine_last_sequence != durable_projection.last_sequence
            or closed_parity.engine_last_digest != durable_projection.last_digest
        ):
            raise _EngineEventIngestionBlocked(
                "P1_PORTFOLIO_PARITY_MISMATCH",
                "P1 portfolio parity receipt differs from durable authority",
            )
        return replace(
            result,
            validation_metadata={
                **result.validation_metadata,
                "p1_portfolio_parity": closed_parity.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _market_data_payload(claimed: object) -> SnapshotPayload | None:
        payload = getattr(claimed, "payload", None)
        if (
            isinstance(payload, SnapshotPayload)
            and getattr(payload, "market_data", None) is not None
        ):
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
