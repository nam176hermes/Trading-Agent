from __future__ import annotations

from dataclasses import replace

import pytest

from trading_control.phase3b_approval import (
    Phase3BApprovalContext,
    Phase3BApprovalRejected,
    validate_phase3b_approval,
)
from trading_control.phase3b_migrate import build_parser, main


def approved() -> Phase3BApprovalContext:
    return Phase3BApprovalContext(
        approval_enabled="true",
        actual_inventory_hash="a" * 64,
        approved_inventory_hash="a" * 64,
        actual_revision="0003_contract_lineage_repair",
        approved_revision="0003_contract_lineage_repair",
        actual_normalization_version="phase3b-v1",
        approved_normalization_version="phase3b-v1",
        database_host="127.0.0.1",
        database_port=55432,
        database_name="trading_agent",
        database_role="trading_migrator",
        requested_mode="paper",
        live_execution_enabled="false",
        live_trading_approved="false",
        kill_switch_active=False,
        production_credential_names=(),
    )


def test_approved_context_passes() -> None:
    validate_phase3b_approval(approved())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("approval_enabled", "false", "approval"),
        ("approved_inventory_hash", "b" * 64, "inventory"),
        ("approved_revision", "0002_quarantine_lineage", "revision"),
        ("approved_normalization_version", "other", "normalization"),
        ("database_port", 5432, "database identity"),
        ("requested_mode", "live", "paper mode"),
        ("live_execution_enabled", "true", "live gates"),
        ("live_trading_approved", "true", "live gates"),
        ("kill_switch_active", True, "kill switch"),
        ("production_credential_names", ("COINBASE_API_KEY",), "credential"),
    ],
)
def test_apply_context_rejects_every_guard_mismatch(
    field: str, value: object, message: str,
) -> None:
    with pytest.raises(Phase3BApprovalRejected, match=message):
        validate_phase3b_approval(replace(approved(), **{field: value}))


def test_cli_defaults_to_dry_run_and_supports_scoped_domains() -> None:
    parser = build_parser()
    default = parser.parse_args(["--source-root", "/tmp/source"])
    scoped = parser.parse_args([
        "--source-root", "/tmp/source", "--apply",
        "--domain", "decision-price", "--domain", "asset-lineage",
    ])
    resumed = parser.parse_args([
        "--source-root", "/tmp/source", "--domain", "cost-symbols",
        "--resume", "run-id",
    ])

    assert default.apply is False and default.domains is None
    assert scoped.apply is True
    assert scoped.domains == ["decision-price", "asset-lineage"]
    assert resumed.resume == "run-id"


def test_apply_is_rejected_before_source_or_database_access(monkeypatch) -> None:
    monkeypatch.delenv("TRADING_PHASE3B_APPLY_APPROVED", raising=False)

    with pytest.raises(SystemExit, match="Phase 3B apply approval is not enabled"):
        main(["--source-root", "/does/not/exist", "--apply"])
