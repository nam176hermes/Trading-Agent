from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.verify_p1_u04_host_authority import _validate_p1_external_outcomes


def _receipts() -> list[dict[str, object]]:
    return [
        {
            "capability_or_authority_code": code,
            "outcome": outcome,
            "preflight_state": state,
            "redacted_fact_class": fact,
        }
        for code, outcome, state, fact in (
            (
                "EXT-PHASE3B-CORPUS",
                "PASS",
                "VALID",
                "AUTHORITY_COMPLETE_VALIDATED",
            ),
            (
                "EXT-LEGACY-UV-AUTHORITY",
                "PASS",
                "VALID",
                "AUTHORITY_COMPLETE_VALIDATED",
            ),
            (
                "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS",
                "PASS",
                "VALID",
                "AUTHORITY_COMPLETE_VALIDATED",
            ),
            (
                "EXT-DISPOSABLE-PG-GREEN",
                "DEFERRED",
                "ABSENT",
                "AUTHORITY_RECORD_ABSENT",
            ),
            (
                "EXT-DISPOSABLE-PG-RED",
                "DEFERRED",
                "ABSENT",
                "AUTHORITY_RECORD_ABSENT",
            ),
            (
                "EXT-DISPOSABLE-PG-RED-EVIDENCE",
                "DEFERRED",
                "ABSENT",
                "AUTHORITY_RECORD_ABSENT",
            ),
        )
    ]


def test_p1_scope_requires_relevant_pass_and_postgres_absent_deferred() -> None:
    assert _validate_p1_external_outcomes(_receipts()) == {
        "EXT-DISPOSABLE-PG-GREEN": "DEFERRED",
        "EXT-DISPOSABLE-PG-RED": "DEFERRED",
        "EXT-DISPOSABLE-PG-RED-EVIDENCE": "DEFERRED",
        "EXT-LEGACY-UV-AUTHORITY": "PASS",
        "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS": "PASS",
        "EXT-PHASE3B-CORPUS": "PASS",
    }


@pytest.mark.parametrize(
    ("index", "field", "value"),
    (
        (0, "outcome", "DEFERRED"),
        (2, "preflight_state", "ABSENT"),
        (3, "outcome", "PASS"),
        (4, "preflight_state", "VALID"),
        (5, "redacted_fact_class", "AUTHORITY_RECORD_INVALID"),
        (0, "capability_or_authority_code", "EXT-DISPOSABLE-PG-GREEN"),
    ),
)
def test_p1_scope_rejects_authority_mutations(
    index: int, field: str, value: str,
) -> None:
    receipts = deepcopy(_receipts())
    receipts[index][field] = value

    with pytest.raises(ValueError, match="P1_U04_EXTERNAL_AUTHORITY_INVALID"):
        _validate_p1_external_outcomes(receipts)


def test_p1_scope_rejects_missing_receipt() -> None:
    with pytest.raises(ValueError, match="P1_U04_EXTERNAL_AUTHORITY_INVALID"):
        _validate_p1_external_outcomes(_receipts()[:-1])
