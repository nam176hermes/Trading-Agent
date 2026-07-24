from __future__ import annotations

from dataclasses import replace

import pytest

from trading_control.approval import (
    APPROVED_ALEMBIC_REVISION,
    APPROVED_INVENTORY_HASH,
    APPROVED_NORMALIZATION_VERSION,
    APPROVED_SOURCE_ROOT,
    ApprovalContext,
    validate_real_apply_approval,
)
from trading_control.writer import ApplyRejected


def approved_context() -> ApprovalContext:
    return ApprovalContext(
        explicit_apply=True,
        source_root=APPROVED_SOURCE_ROOT,
        actual_inventory_hash=APPROVED_INVENTORY_HASH,
        approval_enabled="true",
        approved_inventory_hash=APPROVED_INVENTORY_HASH,
        normalization_version=APPROVED_NORMALIZATION_VERSION,
        approved_normalization_version=APPROVED_NORMALIZATION_VERSION,
        alembic_revision=APPROVED_ALEMBIC_REVISION,
        approved_alembic_revision=APPROVED_ALEMBIC_REVISION,
        database_host="127.0.0.1",
        database_port=55432,
        database_name="trading_agent",
        database_role="trading_migrator",
        expected_canonical_rows=0,
        actual_canonical_rows=0,
        expected_quarantine_rows=0,
        actual_quarantine_rows=0,
        requested_mode="paper",
        live_execution_enabled="false",
        live_trading_approved="false",
        kill_switch_active=False,
        production_credential_names=(),
    )


def test_scoped_real_apply_approval_accepts_only_complete_exact_context() -> None:
    validate_real_apply_approval(approved_context())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("explicit_apply", False, "explicit --apply"),
        ("source_root", "/tmp/copy", "source root"),
        ("actual_inventory_hash", "changed", "inventory"),
        ("approval_enabled", "false", "approval"),
        ("approved_inventory_hash", "changed", "inventory"),
        ("approved_normalization_version", "phase3-v2", "normalization"),
        ("approved_alembic_revision", "0003", "Alembic"),
        ("database_host", "localhost", "database identity"),
        ("database_port", 5432, "database identity"),
        ("database_name", "other", "database identity"),
        ("database_role", "trading_owner", "database identity"),
        ("actual_canonical_rows", 1, "pre-apply counts"),
        ("requested_mode", "live", "paper"),
        ("live_execution_enabled", "true", "live gates"),
        ("live_trading_approved", "true", "live gates"),
        ("kill_switch_active", True, "kill switch"),
        ("production_credential_names", ("TRADING_MASTER_KEY",), "credential"),
    ],
)
def test_scoped_real_apply_approval_rejects_each_drift(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ApplyRejected, match=message):
        validate_real_apply_approval(replace(approved_context(), **{field: value}))
