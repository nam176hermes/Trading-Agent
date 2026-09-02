"""Concrete journaled operator command application service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from packages.operator_control.contracts import (
    CommandAppliedV1,
    CommandExecutionResultV1,
    CommandIntentV1,
    CommandReceiptV1,
    OperatorActorV1,
    OperatorSafetyEvidenceV1,
    OperatorSourceStateV1,
    SubmitOperatorCommandV1,
)
from packages.operator_control.hashing import (
    idempotency_key_sha256,
    journal_sha256,
    request_sha256,
)
from packages.operator_control.policy import (
    OperatorCommandRejected,
    OperatorMutationPlan,
    decide_operator_command,
)

from .journal import CommandJournal, JournalSnapshot
from .state_store import OperatorStateStore, RecoveryError, classify_recovery


class OperatorControlService:
    def __init__(
        self,
        *,
        state_store: OperatorStateStore,
        journal: CommandJournal,
        safety_provider: Callable[[], OperatorSafetyEvidenceV1],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.state_store = state_store
        self.journal = journal
        self.safety_provider = safety_provider
        self.clock = clock or (lambda: datetime.now(UTC))

    def read_state(self, actor: OperatorActorV1) -> OperatorSourceStateV1:
        if actor.interface != "CLI":
            raise OperatorCommandRejected("CAPABILITY_FORBIDDEN", 403)
        return self.state_store.read_state()

    def execute(
        self,
        actor: OperatorActorV1,
        request: SubmitOperatorCommandV1,
    ) -> CommandExecutionResultV1:
        key = idempotency_key_sha256(request.idempotency_key)
        expected_request = request_sha256(actor, request)
        with self.journal.locked():
            snapshot = self.journal.load(key)
            if snapshot.intent is not None:
                if snapshot.intent.request_sha256 != expected_request:
                    raise OperatorCommandRejected("IDEMPOTENCY_CONFLICT", 409)
                return self._resume(key, request, snapshot)

            accepted_at = self._now()
            current = self.state_store.read_state()
            safety = self._safety_for(request)
            plan = decide_operator_command(
                actor=actor,
                request=request,
                current=current,
                accepted_at=accepted_at,
                safety=safety,
            )
            intent = self._intent(actor, request, key, accepted_at, plan)
            self.journal.create_intent(intent)
            return self._apply(key, intent, plan, deduplicated=False)

    def _resume(
        self,
        key: str,
        request: SubmitOperatorCommandV1,
        snapshot: JournalSnapshot,
    ) -> CommandExecutionResultV1:
        intent = snapshot.intent
        assert intent is not None
        if snapshot.receipt is not None:
            return CommandExecutionResultV1(
                schema_version="operator-command-execution-result-v1",
                receipt=snapshot.receipt,
                deduplicated=True,
            )
        if snapshot.applied is not None:
            return self._receipt(key, intent, snapshot.applied, deduplicated=True)

        current = self.state_store.read_state()
        tombstone = self.state_store.tombstone_sha256(key)
        disposition = classify_recovery(
            intent, current, tombstone_sha256=tombstone
        )
        if disposition != "RETRY":
            applied = self._applied(
                intent,
                current.state_sha256,
                disposition,
                tombstone if disposition == "RECOVERED_KILL_SWITCH_CLEAR" else None,
            )
            self.journal.create_applied(key, applied)
            return self._receipt(key, intent, applied, deduplicated=True)

        safety = self._safety_for(request)
        plan = decide_operator_command(
            actor=intent.actor,
            request=request,
            current=current,
            accepted_at=intent.accepted_at,
            safety=safety,
        )
        self._require_same_plan(intent, plan)
        return self._apply(key, intent, plan, deduplicated=True)

    def _apply(
        self,
        key: str,
        intent: CommandIntentV1,
        plan: OperatorMutationPlan,
        *,
        deduplicated: bool,
    ) -> CommandExecutionResultV1:
        if intent.safety_evidence_sha256 is not None:
            fresh = self.safety_provider()
            if fresh.evidence_sha256 != intent.safety_evidence_sha256:
                raise OperatorCommandRejected("SAFETY_EVIDENCE_CHANGED", 409)
        current = self.state_store.read_state()
        if current.state_sha256 != intent.prior_state_sha256:
            raise RecoveryError("COMMAND_OUTCOME_UNKNOWN")

        tombstone = None
        if plan.operation == "NO_CHANGE":
            resulting = current
            application_kind = "NO_CHANGE"
        elif plan.operation == "WRITE_MODE":
            resulting = self.state_store.apply_mode(intent)
            application_kind = "MODE_REPLACED"
        elif plan.operation == "WRITE_KILL_SWITCH":
            if plan.desired_kill_switch_bytes is None:
                raise RecoveryError("COMMAND_OUTCOME_UNKNOWN")
            resulting = self.state_store.activate_kill_switch(
                intent, plan.desired_kill_switch_bytes
            )
            application_kind = "KILL_SWITCH_CREATED"
        else:
            cleared = self.state_store.clear_kill_switch(intent)
            resulting = cleared.state
            tombstone = cleared.tombstone_sha256
            application_kind = "KILL_SWITCH_CLEARED_TO_TOMBSTONE"

        applied = self._applied(
            intent, resulting.state_sha256, application_kind, tombstone
        )
        self.journal.create_applied(key, applied)
        return self._receipt(key, intent, applied, deduplicated=deduplicated)

    def _intent(
        self,
        actor: OperatorActorV1,
        request: SubmitOperatorCommandV1,
        key: str,
        accepted_at: datetime,
        plan: OperatorMutationPlan,
    ) -> CommandIntentV1:
        desired_state = (
            "PAPER"
            if request.command.command_type == "SET_REQUESTED_MODE"
            else f"KILL_SWITCH_{request.command.desired_state}"
        )
        base = CommandIntentV1(
            schema_version="operator-command-intent-v1",
            command_id=request.command_id,
            idempotency_key_sha256=key,
            correlation_id=request.correlation_id,
            request_sha256=plan.request_sha256,
            actor=actor,
            command_type=request.command.command_type,
            desired_state=desired_state,
            prior_state_sha256=plan.prior_state_sha256,
            expected_state_sha256=plan.expected_state_sha256,
            safety_evidence_sha256=plan.safety_evidence_sha256,
            reason_sha256=plan.reason_sha256,
            accepted_at=accepted_at,
            desired_file_sha256=plan.desired_file_sha256,
            intent_sha256="0" * 64,
        )
        return base.model_copy(
            update={"intent_sha256": journal_sha256(base, "intent_sha256")}
        )

    def _applied(
        self,
        intent: CommandIntentV1,
        resulting_state_sha256: str,
        application_kind: str,
        tombstone_sha256: str | None,
    ) -> CommandAppliedV1:
        applied_at = self._now()
        if applied_at < intent.accepted_at:
            raise OperatorCommandRejected("OPERATOR_CLOCK_INVALID", 503)
        base = CommandAppliedV1(
            schema_version="operator-command-applied-v1",
            intent_sha256=intent.intent_sha256,
            applied_at=applied_at,
            application_kind=application_kind,
            resulting_state_sha256=resulting_state_sha256,
            tombstone_sha256=tombstone_sha256,
            applied_sha256="0" * 64,
        )
        return base.model_copy(
            update={"applied_sha256": journal_sha256(base, "applied_sha256")}
        )

    def _receipt(
        self,
        key: str,
        intent: CommandIntentV1,
        applied: CommandAppliedV1,
        *,
        deduplicated: bool,
    ) -> CommandExecutionResultV1:
        completed_at = self._now()
        if completed_at < applied.applied_at:
            raise OperatorCommandRejected("OPERATOR_CLOCK_INVALID", 503)
        recovered = applied.application_kind.startswith("RECOVERED_")
        outcome = (
            "NO_CHANGE"
            if applied.application_kind == "NO_CHANGE"
            else "RECOVERED_APPLIED"
            if recovered
            else "APPLIED"
        )
        base = CommandReceiptV1(
            schema_version="operator-command-receipt-v1",
            command_id=intent.command_id,
            idempotency_key_sha256=intent.idempotency_key_sha256,
            correlation_id=intent.correlation_id,
            request_sha256=intent.request_sha256,
            actor=intent.actor,
            command_type=intent.command_type,
            desired_state=intent.desired_state,
            prior_state_sha256=intent.prior_state_sha256,
            expected_state_sha256=intent.expected_state_sha256,
            safety_evidence_sha256=intent.safety_evidence_sha256,
            reason_sha256=intent.reason_sha256,
            accepted_at=intent.accepted_at,
            applied_at=applied.applied_at,
            completed_at=completed_at,
            outcome=outcome,
            outcome_code=self._outcome_code(intent, applied),
            resulting_state_sha256=applied.resulting_state_sha256,
            intent_sha256=intent.intent_sha256,
            applied_sha256=applied.applied_sha256,
            receipt_sha256="0" * 64,
        )
        receipt = base.model_copy(
            update={"receipt_sha256": journal_sha256(base, "receipt_sha256")}
        )
        self.journal.create_receipt(receipt)
        final = self.journal.load(key).receipt
        if final is None:
            raise RecoveryError("COMMAND_OUTCOME_UNKNOWN")
        return CommandExecutionResultV1(
            schema_version="operator-command-execution-result-v1",
            receipt=final,
            deduplicated=deduplicated,
        )

    @staticmethod
    def _outcome_code(intent: CommandIntentV1, applied: CommandAppliedV1) -> str:
        if applied.application_kind == "NO_CHANGE":
            return (
                "MODE_ALREADY_PAPER"
                if intent.desired_state == "PAPER"
                else "KILL_SWITCH_ALREADY_ACTIVE"
            )
        return {
            "PAPER": "MODE_SET_PAPER",
            "KILL_SWITCH_ACTIVE": "KILL_SWITCH_ACTIVATED",
            "KILL_SWITCH_INACTIVE": "KILL_SWITCH_CLEARED",
        }[intent.desired_state]

    @staticmethod
    def _require_same_plan(
        intent: CommandIntentV1, plan: OperatorMutationPlan
    ) -> None:
        if (
            plan.request_sha256 != intent.request_sha256
            or plan.prior_state_sha256 != intent.prior_state_sha256
            or plan.expected_state_sha256 != intent.expected_state_sha256
            or plan.safety_evidence_sha256 != intent.safety_evidence_sha256
            or plan.reason_sha256 != intent.reason_sha256
            or plan.desired_file_sha256 != intent.desired_file_sha256
        ):
            raise RecoveryError("COMMAND_OUTCOME_UNKNOWN")

    def _safety_for(
        self, request: SubmitOperatorCommandV1
    ) -> OperatorSafetyEvidenceV1 | None:
        command = request.command
        if (
            command.command_type == "SET_KILL_SWITCH"
            and command.desired_state == "INACTIVE"
        ):
            return self.safety_provider()
        return None

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is not UTC:
            raise OperatorCommandRejected("OPERATOR_CLOCK_INVALID", 503)
        return value


__all__ = ["OperatorControlService"]
