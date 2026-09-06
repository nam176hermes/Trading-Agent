"""Deterministic disclosed trial accounting for a P3 family."""

from __future__ import annotations

from packages.alpha_lifecycle.contracts.results import TrialOutcome
from packages.engine_contracts.serialization import canonical_json_bytes


def build_trial_accounting(outcomes: tuple[TrialOutcome, ...]) -> bytes:
    values = tuple(TrialOutcome.model_validate(item) for item in outcomes)
    return canonical_json_bytes({
        "schema_version": "p3-trial-accounting-v1",
        "attempted": len(values),
        "outcomes": [
            {
                "trial_key": item.trial_key,
                "status": item.status,
                "result_ref": item.result_ref,
                "execution_receipt_refs": item.execution_receipt_refs,
            }
            for item in sorted(values, key=lambda item: item.trial_key)
        ],
    })


__all__ = ["build_trial_accounting"]
