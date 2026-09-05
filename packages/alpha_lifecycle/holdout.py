"""Fail-closed validation for one authorized historical holdout access."""

from __future__ import annotations

from datetime import datetime

from packages.alpha_lifecycle.contracts.authority import (
    CustodyRecord,
    HoldoutRequest,
    PrimarySelection,
)
from packages.alpha_lifecycle.contracts.base import SourceIdentity


def validate_holdout_access(
    request: HoldoutRequest,
    selection: PrimarySelection,
    custody: CustodyRecord,
    *,
    expected_source: SourceIdentity,
    now: datetime,
) -> None:
    request = HoldoutRequest.model_validate(request)
    selection = PrimarySelection.model_validate(selection)
    custody = CustodyRecord.model_validate(custody)
    if selection.outcome != "SELECTED":
        raise ValueError("holdout requires exactly one selected primary")
    if request.source != expected_source or not request.issued_at <= now < request.expires_at:
        raise ValueError("holdout source or request validity is stale")
    if request.policy_digest != selection.selection_policy_digest:
        raise ValueError("holdout policy differs from primary selection")
    if custody.research_identity == custody.custodian_identity:
        raise ValueError("holdout custody separation is invalid")


__all__ = ["validate_holdout_access"]
