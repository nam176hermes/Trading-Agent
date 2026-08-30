"""Deterministic, network-free restart classification for P1 paper sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from uuid import UUID

from packages.nautilus_runtime_contracts.paper import PaperSessionState
from packages.nautilus_runtime_contracts.result import P1_ENGINE_VERSION
from packages.safety_evidence import CanonicalKillSwitchState
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

from .nautilus_checkpoint import NautilusCheckpointRecord, checkpoint_sha256


_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_ZERO = "0" * 64


class NautilusChildState(StrEnum):
    ABSENT = "ABSENT"
    RUNNING = "RUNNING"
    GONE = "GONE"


class NautilusRecoveryDisposition(StrEnum):
    START_NEW = "START_NEW"
    KEEP_RUNNING = "KEEP_RUNNING"
    ALREADY_STOPPED = "ALREADY_STOPPED"
    RESUME_EXACT_PREFIX = "RESUME_EXACT_PREFIX"
    EXIT_ONLY = "EXIT_ONLY"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class NautilusRecoveryReason(StrEnum):
    NO_CHILD_NO_CHECKPOINT = "NO_CHILD_NO_CHECKPOINT"
    CHILD_RUNNING_CURRENT = "CHILD_RUNNING_CURRENT"
    CLEAN_STOP_DURABLE = "CLEAN_STOP_DURABLE"
    DURABLE_PREFIX_MATCH = "DURABLE_PREFIX_MATCH"
    KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"
    ENGINE_MIGRATION_REQUIRED = "ENGINE_MIGRATION_REQUIRED"
    AUTHORITY_DRIFT = "AUTHORITY_DRIFT"
    CHECKPOINT_MISSING = "CHECKPOINT_MISSING"
    CHECKPOINT_AHEAD_OF_LEDGER = "CHECKPOINT_AHEAD_OF_LEDGER"
    LEDGER_AHEAD_OF_CHECKPOINT = "LEDGER_AHEAD_OF_CHECKPOINT"
    EVENT_PREFIX_DRIFT = "EVENT_PREFIX_DRIFT"
    PORTFOLIO_DRIFT = "PORTFOLIO_DRIFT"
    TARGET_CURSOR_DRIFT = "TARGET_CURSOR_DRIFT"
    FINAL_OBSERVATION_DRIFT = "FINAL_OBSERVATION_DRIFT"
    CHILD_IDENTITY_DRIFT = "CHILD_IDENTITY_DRIFT"
    CHILD_OUTCOME_UNCERTAIN = "CHILD_OUTCOME_UNCERTAIN"
    SAFETY_AUTHORITY_UNKNOWN = "SAFETY_AUTHORITY_UNKNOWN"


@dataclass(frozen=True, slots=True)
class NautilusRecoveryEvidence:
    session_id: UUID
    engine_version: str
    expected_engine_version: str
    closure_digest: str
    expected_closure_digest: str
    source_commit: str
    expected_source_commit: str
    config_digest: str
    expected_config_digest: str
    child_state: NautilusChildState
    current_child_identity: str | None
    checkpoint: NautilusCheckpointRecord | None
    ledger_last_sequence: int
    ledger_last_event_digest: str
    ledger_event_prefix_sha256: str
    portfolio_state_hash: str
    target_schedule_cursor: int
    expected_target_schedule_cursor: int
    final_engine_observation_sha256: str
    child_outcome_proven: bool
    kill_switch_state: CanonicalKillSwitchState

    def __post_init__(self) -> None:
        digests = (
            self.closure_digest,
            self.expected_closure_digest,
            self.config_digest,
            self.expected_config_digest,
            self.ledger_last_event_digest,
            self.ledger_event_prefix_sha256,
            self.portfolio_state_hash,
            self.final_engine_observation_sha256,
        )
        checkpoint = self.checkpoint
        if (
            type(self.session_id) is not UUID
            or type(self.engine_version) is not str
            or type(self.expected_engine_version) is not str
            or any(type(value) is not str or _SHA256.fullmatch(value) is None for value in digests)
            or type(self.source_commit) is not str
            or _COMMIT.fullmatch(self.source_commit) is None
            or type(self.expected_source_commit) is not str
            or _COMMIT.fullmatch(self.expected_source_commit) is None
            or type(self.child_state) is not NautilusChildState
            or type(self.ledger_last_sequence) is not int
            or self.ledger_last_sequence < 0
            or type(self.target_schedule_cursor) is not int
            or self.target_schedule_cursor < 0
            or type(self.expected_target_schedule_cursor) is not int
            or self.expected_target_schedule_cursor < 0
            or type(self.child_outcome_proven) is not bool
            or type(self.kill_switch_state) is not CanonicalKillSwitchState
            or (checkpoint is not None and type(checkpoint) is not NautilusCheckpointRecord)
            or (
                self.current_child_identity is not None
                and (
                    type(self.current_child_identity) is not str
                    or _SHA256.fullmatch(self.current_child_identity) is None
                )
            )
            or (self.child_state is NautilusChildState.RUNNING)
            != (self.current_child_identity is not None)
        ):
            raise ValueError("Nautilus recovery evidence is invalid")
        if checkpoint is not None and (
            checkpoint.checkpoint.session_id != self.session_id
            or checkpoint.checkpoint_sha256 != checkpoint_sha256(checkpoint.checkpoint)
            or (checkpoint.event_batch_sha256 is None)
            != (checkpoint.parity_receipt_sha256 is None)
            or any(
                value is not None
                and (
                    type(value) is not str or _SHA256.fullmatch(value) is None
                )
                for value in (
                    checkpoint.event_batch_sha256,
                    checkpoint.parity_receipt_sha256,
                )
            )
        ):
            raise ValueError("Nautilus checkpoint evidence is invalid")


@dataclass(frozen=True, slots=True)
class NautilusRecoveryDecision:
    disposition: NautilusRecoveryDisposition
    reason_codes: tuple[NautilusRecoveryReason, ...]
    allows_automatic_resume: bool
    allows_new_opening_targets: bool
    requires_operator_reconciliation: bool
    network_query_allowed: bool = False

    def __post_init__(self) -> None:
        resumes = self.disposition in {
            NautilusRecoveryDisposition.START_NEW,
            NautilusRecoveryDisposition.KEEP_RUNNING,
            NautilusRecoveryDisposition.RESUME_EXACT_PREFIX,
        }
        blocked = (
            self.disposition
            is NautilusRecoveryDisposition.RECONCILIATION_REQUIRED
        )
        if (
            type(self.disposition) is not NautilusRecoveryDisposition
            or type(self.reason_codes) is not tuple
            or len(self.reason_codes) != 1
            or any(type(reason) is not NautilusRecoveryReason for reason in self.reason_codes)
            or self.allows_automatic_resume is not resumes
            or self.allows_new_opening_targets is not resumes
            or self.requires_operator_reconciliation is not blocked
            or self.network_query_allowed is not False
        ):
            raise ValueError("Nautilus recovery decision is inconsistent")


def _blocked(reason: NautilusRecoveryReason) -> NautilusRecoveryDecision:
    return NautilusRecoveryDecision(
        disposition=NautilusRecoveryDisposition.RECONCILIATION_REQUIRED,
        reason_codes=(reason,),
        allows_automatic_resume=False,
        allows_new_opening_targets=False,
        requires_operator_reconciliation=True,
    )


def _accepted(
    disposition: NautilusRecoveryDisposition,
    reason: NautilusRecoveryReason,
) -> NautilusRecoveryDecision:
    resumes = disposition in {
        NautilusRecoveryDisposition.START_NEW,
        NautilusRecoveryDisposition.KEEP_RUNNING,
        NautilusRecoveryDisposition.RESUME_EXACT_PREFIX,
    }
    return NautilusRecoveryDecision(
        disposition=disposition,
        reason_codes=(reason,),
        allows_automatic_resume=resumes,
        allows_new_opening_targets=resumes,
        requires_operator_reconciliation=False,
    )


def reconcile_nautilus_paper(
    evidence: NautilusRecoveryEvidence,
) -> NautilusRecoveryDecision:
    """Classify only locally provable recovery state; never infer venue state."""

    if type(evidence) is not NautilusRecoveryEvidence:
        raise TypeError("exact Nautilus recovery evidence is required")
    if evidence.engine_version != evidence.expected_engine_version:
        return _blocked(
            NautilusRecoveryReason.ENGINE_MIGRATION_REQUIRED
            if evidence.engine_version == "1.227.0"
            else NautilusRecoveryReason.AUTHORITY_DRIFT
        )
    if (
        evidence.expected_engine_version != P1_ENGINE_VERSION
        or evidence.expected_closure_digest != P1_REAL_BACKTEST_POLICY.closure_sha256
        or evidence.closure_digest != evidence.expected_closure_digest
        or evidence.source_commit != evidence.expected_source_commit
        or evidence.config_digest != evidence.expected_config_digest
    ):
        return _blocked(NautilusRecoveryReason.AUTHORITY_DRIFT)
    if evidence.kill_switch_state is CanonicalKillSwitchState.UNKNOWN:
        return _blocked(NautilusRecoveryReason.SAFETY_AUTHORITY_UNKNOWN)

    checkpoint_record = evidence.checkpoint
    if checkpoint_record is None:
        empty = (
            evidence.child_state is NautilusChildState.ABSENT
            and evidence.ledger_last_sequence == 0
            and evidence.ledger_last_event_digest == _ZERO
            and evidence.ledger_event_prefix_sha256 == _ZERO
            and evidence.portfolio_state_hash == _ZERO
            and evidence.target_schedule_cursor == 0
            and evidence.expected_target_schedule_cursor == 0
            and evidence.final_engine_observation_sha256 == _ZERO
        )
        if empty and evidence.kill_switch_state is CanonicalKillSwitchState.INACTIVE:
            return _accepted(
                NautilusRecoveryDisposition.START_NEW,
                NautilusRecoveryReason.NO_CHILD_NO_CHECKPOINT,
            )
        return _blocked(NautilusRecoveryReason.CHECKPOINT_MISSING)

    checkpoint = checkpoint_record.checkpoint
    if checkpoint.closure_digest != evidence.expected_closure_digest:
        return _blocked(NautilusRecoveryReason.AUTHORITY_DRIFT)
    if evidence.ledger_last_sequence < checkpoint.last_emitted_event:
        return _blocked(NautilusRecoveryReason.CHECKPOINT_AHEAD_OF_LEDGER)
    if evidence.ledger_last_sequence > checkpoint.last_emitted_event:
        return _blocked(NautilusRecoveryReason.LEDGER_AHEAD_OF_CHECKPOINT)
    if (
        evidence.ledger_last_event_digest != checkpoint.last_event_digest
        or evidence.ledger_event_prefix_sha256 != checkpoint.event_prefix_sha256
    ):
        return _blocked(NautilusRecoveryReason.EVENT_PREFIX_DRIFT)
    if evidence.portfolio_state_hash != checkpoint.portfolio_state_hash:
        return _blocked(NautilusRecoveryReason.PORTFOLIO_DRIFT)
    terminal_offset = 2 if checkpoint.state is PaperSessionState.STOPPING else 1
    if (
        evidence.target_schedule_cursor != evidence.expected_target_schedule_cursor
        or evidence.target_schedule_cursor
        != checkpoint.last_accepted_command - terminal_offset
    ):
        return _blocked(NautilusRecoveryReason.TARGET_CURSOR_DRIFT)
    if evidence.final_engine_observation_sha256 != checkpoint.semantic_state_hash:
        return _blocked(NautilusRecoveryReason.FINAL_OBSERVATION_DRIFT)
    if evidence.child_state is NautilusChildState.RUNNING and (
        evidence.current_child_identity != checkpoint.child_identity
    ):
        return _blocked(NautilusRecoveryReason.CHILD_IDENTITY_DRIFT)
    if evidence.kill_switch_state is CanonicalKillSwitchState.ACTIVE:
        return NautilusRecoveryDecision(
            disposition=NautilusRecoveryDisposition.EXIT_ONLY,
            reason_codes=(NautilusRecoveryReason.KILL_SWITCH_ENGAGED,),
            allows_automatic_resume=False,
            allows_new_opening_targets=False,
            requires_operator_reconciliation=False,
        )
    if evidence.child_state is NautilusChildState.RUNNING:
        return _accepted(
            NautilusRecoveryDisposition.KEEP_RUNNING,
            NautilusRecoveryReason.CHILD_RUNNING_CURRENT,
        )
    if (
        evidence.child_state is NautilusChildState.GONE
        and checkpoint.state is PaperSessionState.STOPPING
        and checkpoint.last_command_type == "StopPaperEngine"
        and checkpoint_record.event_batch_sha256 is not None
        and evidence.child_outcome_proven
    ):
        return _accepted(
            NautilusRecoveryDisposition.ALREADY_STOPPED,
            NautilusRecoveryReason.CLEAN_STOP_DURABLE,
        )
    if not evidence.child_outcome_proven:
        return _blocked(NautilusRecoveryReason.CHILD_OUTCOME_UNCERTAIN)
    if evidence.child_state is NautilusChildState.GONE:
        return _accepted(
            NautilusRecoveryDisposition.RESUME_EXACT_PREFIX,
            NautilusRecoveryReason.DURABLE_PREFIX_MATCH,
        )
    return _blocked(NautilusRecoveryReason.CHILD_OUTCOME_UNCERTAIN)


__all__ = [
    "NautilusChildState",
    "NautilusRecoveryDecision",
    "NautilusRecoveryDisposition",
    "NautilusRecoveryEvidence",
    "NautilusRecoveryReason",
    "reconcile_nautilus_paper",
]
