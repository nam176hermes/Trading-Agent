from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import OrderState
from packages.domain.runtime_halt import ConsumedSubmitAuthority
from packages.execution_sandbox import (
    SandboxConnectionState,
    SandboxOrderSnapshot,
    SandboxRecoveryCheckpoint,
    SandboxSnapshot,
    SandboxSubmitCustody,
)
from packages.runtime_risk import canonical_model_digest, canonical_model_json


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def uid(value: int) -> UUID:
    return UUID(int=value)


def consumed_authority(
    prepared_case: Any, **changes: object
) -> ConsumedSubmitAuthority:
    values: dict[str, object] = {
        "permit_id": prepared_case.permit.permit_id,
        "prepared_event_digest": prepared_case.permit.prepared_event_digest,
        "halt_stream_id": prepared_case.permit.halt_stream_id,
        "halt_generation": prepared_case.permit.halt_generation,
        "halt_transition_digest": prepared_case.permit.halt_transition_digest,
        "consumed_at": NOW + timedelta(seconds=1),
        "consumed_event_id": uid(30),
        "consumed_event_digest": "b" * 64,
        "schema_version": "consumed-submit-authority-v1",
    }
    values.update(changes)
    return ConsumedSubmitAuthority(**values)


def submit_custody(
    prepared_case: Any,
    *,
    command_id: UUID = uid(100),
    order_id: UUID = uid(1),
    client_order_id: str = "sandbox-client-1",
    prepared_permit: object | None = None,
    consumed: object | None = None,
) -> SandboxSubmitCustody:
    return SandboxSubmitCustody(
        command_id=command_id,
        order_id=order_id,
        client_order_id=client_order_id,
        prepared_permit=(
            prepared_case.permit if prepared_permit is None else prepared_permit
        ),
        consumed_authority=(
            consumed_authority(prepared_case) if consumed is None else consumed
        ),
    )


def order_snapshot(
    prepared_case: Any,
    *,
    order_id: UUID = uid(1),
    client_order_id: str = "sandbox-client-1",
) -> SandboxOrderSnapshot:
    intent = prepared_case.intent.model_copy(
        update={"intent_id": order_id, "client_order_id": client_order_id}
    )
    return SandboxOrderSnapshot(
        order_id=order_id,
        client_order_id=client_order_id,
        order_intent=intent,
        venue_state=OrderState(order_id=order_id),
        observed_state=OrderState(order_id=order_id),
    )


def snapshot(
    prepared_case: Any,
    *,
    orders: tuple[SandboxOrderSnapshot, ...] | None = None,
    current_time: datetime = NOW + timedelta(seconds=1),
) -> SandboxSnapshot:
    return SandboxSnapshot(
        connection_state=SandboxConnectionState.CONNECTED,
        current_time=current_time,
        orders=(order_snapshot(prepared_case),) if orders is None else orders,
    )


def checkpoint_values(
    prepared_case: Any,
    *,
    checkpoint_snapshot: object | None = None,
    executed_command_ids: tuple[UUID, ...] = (uid(100),),
    submit_custodies: tuple[SandboxSubmitCustody, ...] | None = None,
    **changes: object,
) -> dict[str, object]:
    values: dict[str, object] = {
        "checkpoint_id": uid(200),
        "scenario_digest": "a" * 64,
        "snapshot": (
            snapshot(prepared_case)
            if checkpoint_snapshot is None
            else checkpoint_snapshot
        ),
        "executed_command_ids": executed_command_ids,
        "submit_custodies": (
            (submit_custody(prepared_case),)
            if submit_custodies is None
            else submit_custodies
        ),
        "created_at": NOW + timedelta(seconds=2),
        "schema_version": "sandbox-recovery-checkpoint-v1",
    }
    values.update(changes)
    return values


def test_valid_canonical_custody_and_checkpoint_are_strict_immutable_evidence(
    prepared_case: Any,
) -> None:
    custody = submit_custody(prepared_case)
    checkpoint = SandboxRecoveryCheckpoint(**checkpoint_values(prepared_case))

    assert custody.command_id == uid(100)
    assert checkpoint.snapshot.orders[0].order_id == custody.order_id
    assert checkpoint.schema_version == "sandbox-recovery-checkpoint-v1"
    assert set(SandboxRecoveryCheckpoint.model_fields) == {
        "checkpoint_id",
        "scenario_digest",
        "snapshot",
        "executed_command_ids",
        "submit_custodies",
        "created_at",
        "schema_version",
    }
    assert "digest" not in SandboxRecoveryCheckpoint.model_fields
    with pytest.raises(ValidationError):
        checkpoint.checkpoint_id = uid(201)  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SandboxRecoveryCheckpoint(**checkpoint_values(prepared_case), unexpected=True)


def test_custody_revalidates_forged_prepared_permit(prepared_case: Any) -> None:
    forged = prepared_case.permit.model_copy()
    object.__setattr__(forged, "expires_at", forged.prepared_at)

    with pytest.raises(ValueError, match="prepared_permit"):
        submit_custody(prepared_case, prepared_permit=forged)


def test_custody_revalidates_forged_consumed_authority(prepared_case: Any) -> None:
    forged = consumed_authority(prepared_case).model_copy()
    object.__setattr__(forged, "consumed_at", datetime(2026, 8, 10, 12, 0))

    with pytest.raises(ValueError, match="consumed_authority"):
        submit_custody(prepared_case, consumed=forged)


def test_checkpoint_revalidates_forged_snapshot(prepared_case: Any) -> None:
    forged = snapshot(prepared_case).model_copy()
    object.__setattr__(forged, "current_time", datetime(2026, 8, 10, 12, 0))

    with pytest.raises(ValueError, match="snapshot"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(prepared_case, checkpoint_snapshot=forged)
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("permit_id", uid(901)),
        ("prepared_event_digest", "c" * 64),
        ("halt_stream_id", uid(902)),
        ("halt_generation", 2),
        ("halt_transition_digest", "d" * 64),
    ),
)
def test_custody_rejects_changed_prepared_consumed_lineage(
    field_name: str,
    replacement: object,
    prepared_case: Any,
) -> None:
    consumed = consumed_authority(prepared_case).model_copy(
        update={field_name: replacement}
    )

    with pytest.raises(ValueError, match=field_name):
        submit_custody(prepared_case, consumed=consumed)


def test_checkpoint_rejects_duplicate_executed_command_id(prepared_case: Any) -> None:
    with pytest.raises(ValueError, match="executed_command_ids"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(
                prepared_case,
                executed_command_ids=(uid(100), uid(100)),
            )
        )


def test_checkpoint_rejects_duplicate_custody_command_id(prepared_case: Any) -> None:
    first = submit_custody(prepared_case)
    second = submit_custody(
        prepared_case,
        command_id=uid(100),
        order_id=uid(2),
        client_order_id="sandbox-client-2",
    )
    two_orders = snapshot(
        prepared_case,
        orders=(
            order_snapshot(prepared_case),
            order_snapshot(
                prepared_case,
                order_id=uid(2),
                client_order_id="sandbox-client-2",
            ),
        ),
    )

    with pytest.raises(ValueError, match="custody command_id"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(
                prepared_case,
                checkpoint_snapshot=two_orders,
                executed_command_ids=(uid(100),),
                submit_custodies=(first, second),
            )
        )


def test_checkpoint_rejects_custody_command_absent_from_execution_order(
    prepared_case: Any,
) -> None:
    with pytest.raises(ValueError, match="executed_command_ids"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(
                prepared_case,
                executed_command_ids=(uid(101),),
            )
        )


def test_checkpoint_rejects_custody_order_absent_from_snapshot(
    prepared_case: Any,
) -> None:
    missing = submit_custody(prepared_case, order_id=uid(999))

    with pytest.raises(ValueError, match="snapshot order"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(prepared_case, submit_custodies=(missing,))
        )


def test_checkpoint_rejects_duplicate_custody_order_id(prepared_case: Any) -> None:
    first = submit_custody(prepared_case)
    second = submit_custody(prepared_case, command_id=uid(101))

    with pytest.raises(ValueError, match="custody order_id"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(
                prepared_case,
                executed_command_ids=(uid(100), uid(101)),
                submit_custodies=(first, second),
            )
        )


def test_checkpoint_rejects_mismatched_custody_client_order_id(
    prepared_case: Any,
) -> None:
    mismatched = submit_custody(
        prepared_case,
        client_order_id="sandbox-client-other",
    )

    with pytest.raises(ValueError, match="client_order_id"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(prepared_case, submit_custodies=(mismatched,))
        )


def test_checkpoint_rejects_duplicate_custody_client_order_id(
    prepared_case: Any,
) -> None:
    first = submit_custody(prepared_case)
    second = submit_custody(
        prepared_case,
        command_id=uid(101),
        order_id=uid(2),
        client_order_id="sandbox-client-1",
    )
    two_orders = snapshot(
        prepared_case,
        orders=(
            order_snapshot(prepared_case),
            order_snapshot(
                prepared_case,
                order_id=uid(2),
                client_order_id="sandbox-client-2",
            ),
        ),
    )

    with pytest.raises(ValueError, match="custody client_order_id"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(
                prepared_case,
                checkpoint_snapshot=two_orders,
                executed_command_ids=(uid(100), uid(101)),
                submit_custodies=(first, second),
            )
        )


def test_checkpoint_rejects_snapshot_intent_not_bound_to_permit(
    prepared_case: Any,
) -> None:
    changed_permit = prepared_case.permit.model_copy(
        update={"intent_digest": "f" * 64}
    )
    custody = submit_custody(prepared_case, prepared_permit=changed_permit)

    with pytest.raises(ValueError, match="intent_digest"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(prepared_case, submit_custodies=(custody,))
        )


@pytest.mark.parametrize("scenario_digest", ("a" * 63, "A" * 64, "g" * 64))
def test_checkpoint_rejects_malformed_scenario_sha256(
    scenario_digest: str,
    prepared_case: Any,
) -> None:
    with pytest.raises(ValueError, match="scenario_digest"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(prepared_case, scenario_digest=scenario_digest)
        )


def test_checkpoint_rejects_non_utc_created_at(prepared_case: Any) -> None:
    with pytest.raises(ValueError, match="UTC"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(
                prepared_case,
                created_at=datetime(2026, 8, 10, 12, 0),
            )
        )


def test_checkpoint_rejects_unsupported_schema_version(prepared_case: Any) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(
                prepared_case,
                schema_version="sandbox-recovery-checkpoint-v2",
            )
        )


def test_checkpoint_rejects_consumption_after_snapshot_logical_time(
    prepared_case: Any,
) -> None:
    custody = submit_custody(
        prepared_case,
        consumed=consumed_authority(
            prepared_case,
            consumed_at=NOW + timedelta(seconds=2),
        ),
    )

    with pytest.raises(ValueError, match="consumed_at"):
        SandboxRecoveryCheckpoint(
            **checkpoint_values(prepared_case, submit_custodies=(custody,))
        )


def test_same_checkpoint_facts_have_identical_canonical_bytes_and_digest(
    prepared_case: Any,
) -> None:
    left = SandboxRecoveryCheckpoint(**checkpoint_values(prepared_case))
    right = SandboxRecoveryCheckpoint(**checkpoint_values(prepared_case))

    assert canonical_model_json(left) == canonical_model_json(right)
    assert left.digest == right.digest == canonical_model_digest(left)


def test_changing_economic_command_identity_changes_checkpoint_digest(
    prepared_case: Any,
) -> None:
    original = SandboxRecoveryCheckpoint(**checkpoint_values(prepared_case))
    changed = SandboxRecoveryCheckpoint(
        **checkpoint_values(
            prepared_case,
            executed_command_ids=(uid(101),),
            submit_custodies=(
                submit_custody(prepared_case, command_id=uid(101)),
            ),
        )
    )

    assert changed.digest != original.digest


def test_construction_rebuilds_without_mutating_caller_owned_nested_models(
    prepared_case: Any,
) -> None:
    original_snapshot = snapshot(prepared_case)
    original_custody = submit_custody(prepared_case)
    snapshot_before = original_snapshot.model_dump(mode="python")
    permit_before = original_custody.prepared_permit.model_dump(mode="python")
    authority_before = original_custody.consumed_authority.model_dump(mode="python")

    checkpoint = SandboxRecoveryCheckpoint(
        **checkpoint_values(
            prepared_case,
            checkpoint_snapshot=original_snapshot,
            submit_custodies=(original_custody,),
        )
    )

    assert original_snapshot.model_dump(mode="python") == snapshot_before
    assert original_custody.prepared_permit.model_dump(mode="python") == permit_before
    assert (
        original_custody.consumed_authority.model_dump(mode="python")
        == authority_before
    )
    assert checkpoint.snapshot == original_snapshot
    assert checkpoint.snapshot is not original_snapshot
    assert checkpoint.submit_custodies[0] == original_custody
    assert checkpoint.submit_custodies[0] is not original_custody
