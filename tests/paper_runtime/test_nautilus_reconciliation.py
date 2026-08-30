from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from packages.nautilus_runtime_contracts.paper import (
    PAPER_PROTOCOL_SCHEMA,
    PaperSessionCheckpoint,
    PaperSessionState,
    paper_request_id,
)
from packages.safety_evidence import CanonicalKillSwitchState
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
from services.paper_runtime.nautilus_checkpoint import (
    NautilusCheckpointRecord,
    checkpoint_sha256,
)
from services.paper_runtime.nautilus_reconciliation import (
    NautilusChildState,
    NautilusRecoveryDisposition,
    NautilusRecoveryEvidence,
    NautilusRecoveryReason,
    reconcile_nautilus_paper,
)


SESSION_ID = UUID("11111111-1111-4111-8111-111111111111")
OWNER_ID = UUID("22222222-2222-4222-8222-222222222222")
CLOSURE = P1_REAL_BACKTEST_POLICY.closure_sha256
SOURCE = "2" * 40
CONFIG = "3" * 64
CHILD = "4" * 64
EVENT = "5" * 64
PREFIX = "6" * 64
PORTFOLIO = "7" * 64
OBSERVATION = "8" * 64


def _checkpoint(
    *,
    state: PaperSessionState = PaperSessionState.RUNNING,
    durable: bool = False,
) -> NautilusCheckpointRecord:
    checkpoint = PaperSessionCheckpoint(
        schema_version=PAPER_PROTOCOL_SCHEMA,
        session_id=SESSION_ID,
        owner_id=OWNER_ID,
        state=state,
        last_accepted_command=2,
        last_request_id=paper_request_id(SESSION_ID, 2),
        last_command_type="SubmitTargetPortfolio",
        last_command_frame_sha256="9" * 64,
        last_command_digest="a" * 64,
        last_emitted_event=6,
        last_event_digest=EVENT,
        event_prefix_sha256=PREFIX,
        last_acknowledged_command=2,
        last_acknowledgement_sha256="b" * 64,
        semantic_state_hash=OBSERVATION,
        child_identity=CHILD,
        closure_digest=CLOSURE,
        portfolio_state_hash=PORTFOLIO,
    )
    return NautilusCheckpointRecord(
        checkpoint=checkpoint,
        checkpoint_sha256=checkpoint_sha256(checkpoint),
        event_batch_sha256="d" * 64 if durable else None,
        parity_receipt_sha256="e" * 64 if durable else None,
    )


def _evidence(**changes: object) -> NautilusRecoveryEvidence:
    values: dict[str, object] = {
        "session_id": SESSION_ID,
        "engine_version": "1.231.0",
        "expected_engine_version": "1.231.0",
        "closure_digest": CLOSURE,
        "expected_closure_digest": CLOSURE,
        "source_commit": SOURCE,
        "expected_source_commit": SOURCE,
        "config_digest": CONFIG,
        "expected_config_digest": CONFIG,
        "child_state": NautilusChildState.GONE,
        "current_child_identity": None,
        "checkpoint": _checkpoint(),
        "ledger_last_sequence": 6,
        "ledger_last_event_digest": EVENT,
        "ledger_event_prefix_sha256": PREFIX,
        "portfolio_state_hash": PORTFOLIO,
        "target_schedule_cursor": 1,
        "expected_target_schedule_cursor": 1,
        "final_engine_observation_sha256": OBSERVATION,
        "child_outcome_proven": True,
        "kill_switch_state": CanonicalKillSwitchState.INACTIVE,
    }
    values.update(changes)
    return NautilusRecoveryEvidence(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("evidence", "disposition", "reason"),
    [
        (
            _evidence(
                child_state=NautilusChildState.ABSENT,
                checkpoint=None,
                ledger_last_sequence=0,
                ledger_last_event_digest="0" * 64,
                ledger_event_prefix_sha256="0" * 64,
                portfolio_state_hash="0" * 64,
                target_schedule_cursor=0,
                expected_target_schedule_cursor=0,
                final_engine_observation_sha256="0" * 64,
            ),
            NautilusRecoveryDisposition.START_NEW,
            NautilusRecoveryReason.NO_CHILD_NO_CHECKPOINT,
        ),
        (
            _evidence(child_state=NautilusChildState.RUNNING, current_child_identity=CHILD),
            NautilusRecoveryDisposition.KEEP_RUNNING,
            NautilusRecoveryReason.CHILD_RUNNING_CURRENT,
        ),
        (
            _evidence(checkpoint=_checkpoint(state=PaperSessionState.STOPPING, durable=True)),
            NautilusRecoveryDisposition.ALREADY_STOPPED,
            NautilusRecoveryReason.CLEAN_STOP_DURABLE,
        ),
        (
            _evidence(),
            NautilusRecoveryDisposition.RESUME_EXACT_PREFIX,
            NautilusRecoveryReason.DURABLE_PREFIX_MATCH,
        ),
        (
            _evidence(kill_switch_state=CanonicalKillSwitchState.ACTIVE),
            NautilusRecoveryDisposition.EXIT_ONLY,
            NautilusRecoveryReason.KILL_SWITCH_ENGAGED,
        ),
    ],
)
def test_restart_matrix_accepts_only_proven_local_outcomes(
    evidence: NautilusRecoveryEvidence,
    disposition: NautilusRecoveryDisposition,
    reason: NautilusRecoveryReason,
) -> None:
    decision = reconcile_nautilus_paper(evidence)

    assert decision.disposition is disposition
    assert decision.reason_codes == (reason,)
    assert decision.network_query_allowed is False
    assert decision.allows_new_opening_targets is (
        disposition
        in {
            NautilusRecoveryDisposition.START_NEW,
            NautilusRecoveryDisposition.KEEP_RUNNING,
            NautilusRecoveryDisposition.RESUME_EXACT_PREFIX,
        }
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"engine_version": "1.227.0"}, NautilusRecoveryReason.ENGINE_MIGRATION_REQUIRED),
        ({"closure_digest": "f" * 64}, NautilusRecoveryReason.AUTHORITY_DRIFT),
        ({"source_commit": "f" * 40}, NautilusRecoveryReason.AUTHORITY_DRIFT),
        ({"config_digest": "f" * 64}, NautilusRecoveryReason.AUTHORITY_DRIFT),
        ({"ledger_last_sequence": 5}, NautilusRecoveryReason.CHECKPOINT_AHEAD_OF_LEDGER),
        ({"ledger_last_sequence": 7}, NautilusRecoveryReason.LEDGER_AHEAD_OF_CHECKPOINT),
        ({"ledger_last_event_digest": "f" * 64}, NautilusRecoveryReason.EVENT_PREFIX_DRIFT),
        ({"ledger_event_prefix_sha256": "f" * 64}, NautilusRecoveryReason.EVENT_PREFIX_DRIFT),
        ({"portfolio_state_hash": "f" * 64}, NautilusRecoveryReason.PORTFOLIO_DRIFT),
        ({"target_schedule_cursor": 2}, NautilusRecoveryReason.TARGET_CURSOR_DRIFT),
        ({"final_engine_observation_sha256": "f" * 64}, NautilusRecoveryReason.FINAL_OBSERVATION_DRIFT),
        ({"child_outcome_proven": False}, NautilusRecoveryReason.CHILD_OUTCOME_UNCERTAIN),
        ({"kill_switch_state": CanonicalKillSwitchState.UNKNOWN}, NautilusRecoveryReason.SAFETY_AUTHORITY_UNKNOWN),
        (
            {"child_state": NautilusChildState.RUNNING, "current_child_identity": "f" * 64},
            NautilusRecoveryReason.CHILD_IDENTITY_DRIFT,
        ),
    ],
)
def test_any_uncertain_or_changed_authority_is_terminally_blocked(
    changes: dict[str, object], reason: NautilusRecoveryReason
) -> None:
    decision = reconcile_nautilus_paper(_evidence(**changes))

    assert decision.disposition is NautilusRecoveryDisposition.RECONCILIATION_REQUIRED
    assert reason in decision.reason_codes
    assert decision.requires_operator_reconciliation is True
    assert decision.allows_automatic_resume is False
    assert decision.allows_new_opening_targets is False
    assert decision.network_query_allowed is False


def test_checkpoint_without_child_is_not_retryable_when_target_was_accepted() -> None:
    checkpoint = _checkpoint()
    changed = checkpoint.checkpoint.model_copy(
        update={"last_acknowledged_command": 1}
    )
    decision = reconcile_nautilus_paper(
        _evidence(
            checkpoint=replace(
                checkpoint,
                checkpoint=changed,
                checkpoint_sha256=checkpoint_sha256(changed),
            ),
            child_outcome_proven=False,
        )
    )

    assert decision.disposition is NautilusRecoveryDisposition.RECONCILIATION_REQUIRED
    assert NautilusRecoveryReason.CHILD_OUTCOME_UNCERTAIN in decision.reason_codes
