"""Controller-owned adapter for the sealed P1 Nautilus paper child."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re

from engines.nautilus.runtime_v1.control_channel import iter_payloads
from packages.engine_contracts import (
    EngineCommandEnvelope,
    EngineEventEnvelope,
    RunBacktest,
    canonical_json_bytes,
    payload_digest,
)
from packages.engine_event_ledger import EngineEventBatchReceipt
from packages.engine_portfolio_projection.models import ProjectionAuthority
from packages.engine_portfolio_projection.parity import (
    P1PortfolioParityReceipt,
    verify_p1_portfolio_parity,
)
from packages.nautilus_runtime_contracts.events import (
    P1Event,
    P1RunStarted,
    event_message_id,
)
from packages.nautilus_runtime_contracts.paper import (
    PAPER_PROTOCOL_SCHEMA,
    PaperCommandFrame,
    PaperSessionCheckpoint,
    PaperSessionJournal,
    parse_paper_command_frame,
)
from packages.nautilus_runtime_contracts.result import (
    P1_RESULT_VALIDATOR_ID,
    validate_p1_result,
)
from services.job_store.engine_event_repository import EngineEventLedgerRepository
from services.job_worker.engine_results import ValidatedEngineEventBatch
from services.job_worker.safety_state import (
    SafetyEvidence,
    validate_current_safety_evidence,
)

from .nautilus_checkpoint import (
    NautilusCheckpointRecord,
    NautilusCheckpointStore,
    ZERO_CHECKPOINT_SHA256,
)
from .nautilus_child import (
    EngineSessionPort,
    is_issued_engine_session_port as _is_issued_child,
    issue_engine_session_port as _issue_engine_session_port,
)
from .nautilus_protocol import (
    NautilusRecoveryRecorder,
    paper_event_payload,
    paper_response_object,
)


_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_JOB_ID = re.compile(r"job_[0-9a-f]{32}\Z", re.ASCII)
_ATTEMPT_ID = re.compile(r"attempt_[0-9a-f]{32}\Z", re.ASCII)


class NautilusSessionRejected(RuntimeError):
    """The paper child or one of its current authorities failed closed."""


@dataclass(frozen=True, slots=True)
class NautilusSessionResult:
    state: str
    checkpoint: NautilusCheckpointRecord
    event_receipt: EngineEventBatchReceipt | None = None
    parity_receipt: P1PortfolioParityReceipt | None = None


class NautilusPaperSession:
    # One command stream, one child, one checkpoint chain, one durable result.

    def __init__(
        self,
        *,
        request: EngineCommandEnvelope,
        job_id: str,
        attempt_id: str,
        child: EngineSessionPort,
        safety_preflight: Callable[[], SafetyEvidence],
        clock: Callable[[], datetime],
        event_ledger: EngineEventLedgerRepository,
        projection_authority: ProjectionAuthority,
        checkpoints: NautilusCheckpointStore | None = None,
        recovery_recorder: NautilusRecoveryRecorder | None = None,
    ) -> None:
        if (
            type(request) is not EngineCommandEnvelope
            or type(request.payload) is not RunBacktest
            or _JOB_ID.fullmatch(job_id) is None
            or _ATTEMPT_ID.fullmatch(attempt_id) is None
            or not _is_issued_child(child)
            or child.session_id != request.engine_run_id
            or child.owner_id != request.causation_id
            or not callable(safety_preflight)
            or not callable(clock)
            or type(projection_authority) is not ProjectionAuthority
            or projection_authority.request_message_id != request.message_id
            or (
                checkpoints is not None
                and type(checkpoints) is not NautilusCheckpointStore
            )
            or any(
                not callable(getattr(event_ledger, name, None))
                for name in ("ingest", "load_projection")
            )
            or recovery_recorder is not None
            and not callable(getattr(recovery_recorder, "record", None))
        ):
            raise ValueError("Nautilus paper session authority is invalid")
        self._request = request
        self._job_id = job_id
        self._attempt_id = attempt_id
        self._child = child
        self._safety_preflight = safety_preflight
        self._clock = clock
        self._ledger = event_ledger
        self._projection_authority = projection_authority
        self._checkpoints = checkpoints if checkpoints is not None else NautilusCheckpointStore()
        self._recovery_recorder = recovery_recorder
        self._journal = PaperSessionJournal(
            session_id=request.engine_run_id,
            owner_id=request.causation_id,
        )
        self._events: list[P1Event] = []
        self._state = "CREATED"
        self._child_engaged = False
        self._exit_only = False

    def matches_controller(
        self,
        capability_sha256: str,
        custodian_authority_sha256: str,
    ) -> bool:
        return (
            _is_issued_child(self._child)
            and self._child.capability_sha256 == capability_sha256
            and self._child.custodian_authority_sha256
            == custodian_authority_sha256
        )

    @property
    def state(self) -> str:
        return self._state

    def _reject(self, message: str, *, reconcile: bool) -> NautilusSessionRejected:
        if self._child_engaged and self._state not in {
            "STOPPED",
            "RECONCILIATION_REQUIRED",
        }:
            try:
                self._child.abort()
            except BaseException:
                pass
        self._state = "RECONCILIATION_REQUIRED" if reconcile else "FAILED"
        return NautilusSessionRejected(message)

    def _current_checkpoint(self, expected: str) -> NautilusCheckpointRecord | None:
        current = self._checkpoints.load(self._request.engine_run_id)
        observed = ZERO_CHECKPOINT_SHA256 if current is None else current.checkpoint_sha256
        if (
            type(expected) is not str
            or _SHA256.fullmatch(expected) is None
            or expected != observed
        ):
            raise self._reject("paper checkpoint authority changed", reconcile=current is not None)
        return current

    def _preflight(self) -> None:
        validate_current_safety_evidence(self._safety_preflight(), self._clock())

    def _record_recovery(
        self,
        raw: bytes,
        checkpoint: NautilusCheckpointRecord | None,
        expected_checkpoint_sha256: str = ZERO_CHECKPOINT_SHA256,
    ) -> None:
        recorder = self._recovery_recorder
        if recorder is not None and checkpoint is None:
            recorder.begin(
                raw,
                expected_checkpoint_sha256=expected_checkpoint_sha256,
                engine_version=self._child.engine_version,
                closure_digest=self._child.closure_digest,
                source_commit=self._request.source_commit,
                config_digest=self._request.config_digest,
            )
        elif recorder is not None:
            assert checkpoint is not None
            recorder.record(
                raw,
                checkpoint,
                engine_version=self._child.engine_version,
                closure_digest=self._child.closure_digest,
                source_commit=self._request.source_commit,
                config_digest=self._request.config_digest,
            )

    def _response(
        self,
        raw: bytes,
        command: PaperCommandFrame,
        prior_sha256: str,
    ) -> NautilusCheckpointRecord:
        payloads = iter_payloads(raw)
        if len(payloads) < 2:
            raise ValueError("paper child response is incomplete")
        documents = tuple(paper_response_object(payload) for payload in payloads)
        if (
            documents[0].get("frame_type") != "ACK"
            or documents[-1].get("frame_type") != "CHECKPOINT"
            or any(item.get("frame_type") != "EVENT" for item in documents[1:-1])
        ):
            raise ValueError("paper child response ordering is invalid")
        self._journal.record_ack(payloads[0])
        for payload in payloads[1:-1]:
            frame = self._journal.record_event(payload)
            self._events.append(frame.event)
        final = documents[-1]
        if set(final) != {
            "checkpoint",
            "checkpoint_sha256",
            "frame_type",
            "schema_version",
        } or final["schema_version"] != PAPER_PROTOCOL_SCHEMA:
            raise ValueError("paper checkpoint frame is invalid")
        checkpoint_raw = canonical_json_bytes(final["checkpoint"])
        checkpoint = PaperSessionCheckpoint.model_validate_json(checkpoint_raw)
        if (
            final["checkpoint_sha256"]
            != hashlib.sha256(checkpoint_raw).hexdigest()
            or checkpoint.closure_digest != self._child.closure_digest
            or checkpoint.child_identity
            != self._child.process_authority_sha256
            or checkpoint.session_id != self._request.engine_run_id
            or checkpoint.owner_id != self._request.causation_id
            or checkpoint.last_request_id != command.request_id
            or checkpoint
            != self._journal.checkpoint(
                semantic_state_hash=checkpoint.semantic_state_hash,
                child_identity=checkpoint.child_identity,
                closure_digest=checkpoint.closure_digest,
                portfolio_state_hash=checkpoint.portfolio_state_hash,
            )
        ):
            raise ValueError("paper child closure or checkpoint does not match")
        self._validate_event_prefix_authority()
        return self._checkpoints.advance(
            checkpoint, expected_prior_sha256=prior_sha256
        )

    def _validate_event_prefix_authority(self) -> None:
        if not self._events or not isinstance(self._events[0], P1RunStarted):
            raise ValueError("paper event closure authority is unavailable")
        start = self._events[0]
        request = self._request.payload
        assert isinstance(request, RunBacktest)
        if (
            start.closure_digest != self._child.closure_digest
            or start.runtime_family != self._child.runtime_family
            or start.engine_version != self._child.engine_version
            or start.upstream_commit != self._child.engine_upstream_commit
            or start.config_digest != self._request.config_digest
            or start.catalog_digest != request.instrument_catalog.sha256
            or start.data_digest != request.market_data.sha256
        ):
            raise ValueError("paper event closure authority does not match")

    def _durable_result(
        self, checkpoint: NautilusCheckpointRecord
    ) -> tuple[
        NautilusCheckpointRecord,
        EngineEventBatchReceipt,
        P1PortfolioParityReceipt,
    ]:
        envelopes = tuple(
            EngineEventEnvelope(
                message_id=event_message_id(self._request.message_id, event),
                correlation_id=self._request.correlation_id,
                causation_id=self._request.causation_id,
                engine_run_id=self._request.engine_run_id,
                stream_sequence=event.sequence,
                event_time=self._request.event_time,
                initialization_time=self._request.initialization_time,
                schema_version=self._request.schema_version,
                producer_identity=self._request.producer_identity,
                source_commit=self._request.source_commit,
                config_digest=self._request.config_digest,
                payload_digest=payload_digest(payload),
                payload=payload,
            )
            for event in self._events
            for payload in (paper_event_payload(event),)
        )
        raw = b"".join(canonical_json_bytes(item) + b"\n" for item in envelopes)
        profile = validate_p1_result(
            self._request,
            envelopes,
            raw=raw,
            expected_closure_digest=self._child.closure_digest,
        )
        start = profile.events[0]
        if not isinstance(start, P1RunStarted):
            raise ValueError("paper durable result has no start authority")
        metadata: dict[str, object] = {
            "attempt_id": self._attempt_id,
            "config_digest": self._request.config_digest,
            "engine_request_sha256": hashlib.sha256(
                canonical_json_bytes(self._request)
            ).hexdigest(),
            "engine_run_id": str(self._request.engine_run_id),
            "engine_upstream_commit": start.upstream_commit,
            "engine_version": start.engine_version,
            "event_count": len(envelopes),
            "fees": str(profile.fees),
            "fill_count": profile.fill_count,
            "final_cash": str(profile.final_cash),
            "final_position": str(profile.final_position),
            "first_sequence": envelopes[0].stream_sequence,
            "job_id": self._job_id,
            "last_sequence": envelopes[-1].stream_sequence,
            "order_count": profile.order_count,
            "p1_product_closure_sha256": profile.product_closure_sha256,
            "realized_pnl": str(profile.realized_pnl),
            "request_message_id": str(self._request.message_id),
            "runtime_family": start.runtime_family,
            "semantic_digest": profile.semantic_sha256,
            "source_commit": self._request.source_commit,
            "target_count": profile.target_count,
            "unrealized_pnl": str(profile.unrealized_pnl),
            "validator_id": P1_RESULT_VALIDATOR_ID,
        }
        batch = ValidatedEngineEventBatch(
            artifact_type="engine_event_batch",
            relative_ref=(
                f"engine-results/{self._job_id}/{self._attempt_id}/"
                f"{profile.batch_sha256}.jsonl"
            ),
            sha256=profile.batch_sha256,
            size_bytes=len(raw),
            media_type="application/x-ndjson",
            truncated=False,
            validator_id=P1_RESULT_VALIDATOR_ID,
            validation_metadata=metadata,
            events=envelopes,
            profile_result=profile,
        )
        receipt = self._ledger.ingest(batch)
        projection = self._ledger.load_projection(self._request.engine_run_id)
        if projection is None:
            raise ValueError("paper durable engine projection is missing")
        parity = verify_p1_portfolio_parity(
            profile.events,
            self._projection_authority,
            projection,
            batch_sha256=receipt.batch_sha256,
        )
        return (
            self._checkpoints.bind_durable_result(
                checkpoint.checkpoint_sha256, receipt, parity
            ),
            receipt,
            parity,
        )

    def execute(
        self, raw: bytes, *, expected_checkpoint_sha256: str
    ) -> NautilusSessionResult:
        if self._state in {"FAILED", "STOPPED", "RECONCILIATION_REQUIRED"}:
            raise NautilusSessionRejected("paper session is terminal")
        try:
            command = parse_paper_command_frame(raw)
        except BaseException:
            raise self._reject(
                "paper command is invalid", reconcile=self._state != "CREATED"
            ) from None
        try:
            current = self._current_checkpoint(expected_checkpoint_sha256)
            if self._exit_only and command.command.command_type != "StopPaperEngine":
                raise NautilusSessionRejected("paper session is exit-only after safety engagement")
            if command.command.command_type in {
                "StartPaperEngine",
                "SubmitTargetPortfolio",
            }:
                try:
                    self._preflight()
                except BaseException:
                    if (
                        current is not None
                        and command.command.command_type == "SubmitTargetPortfolio"
                    ):
                        self._exit_only = True
                        self._state = "EXIT_ONLY"
                        raise NautilusSessionRejected(
                            "paper safety engagement requires exit-only stop"
                        ) from None
                    raise self._reject(
                        "paper safety evidence is not current",
                        reconcile=current is not None,
                    ) from None
            if (current is None) != (command.command.command_type == "StartPaperEngine"):
                raise self._reject("paper start authority is invalid", reconcile=current is not None)
            self._journal.accept_command(raw)
            self._child_engaged = True
            self._record_recovery(raw, None, expected_checkpoint_sha256)
            response = self._child.exchange(raw)
            record = self._response(response, command, expected_checkpoint_sha256)
            self._state = record.checkpoint.state.value
            if command.command.command_type != "StopPaperEngine":
                self._record_recovery(raw, record)
                return NautilusSessionResult(self._state, record)
            if self._child.close_input() != 0:
                raise ValueError("paper child did not stop cleanly")
            self._child_engaged = False
            self._journal.end_of_input()
            record, receipt, parity = self._durable_result(record)
            self._record_recovery(raw, record)
            self._state = "STOPPED"
            return NautilusSessionResult(self._state, record, receipt, parity)
        except NautilusSessionRejected:
            raise
        except BaseException as exc:
            message = str(exc)
            label = "closure" if "closure" in message else "paper session"
            raise self._reject(f"{label} authority is inconsistent", reconcile=True) from None


__all__ = [
    "EngineSessionPort",
    "NautilusPaperSession",
    "NautilusSessionRejected",
    "NautilusSessionResult",
    "_issue_engine_session_port",
]
