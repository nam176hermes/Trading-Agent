"""Canonical Gate A status after accepting NT1231-U04-G1."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
P1 = ROOT / "docs/implementation/p1-real-nautilus"
UPGRADE = P1 / "upgrade"
FINAL_REVIEW = UPGRADE / "u04-final-review-receipt.json"
ACCEPTANCE = UPGRADE / "u04-final-acceptance-receipt.json"
GENERATION_SHA256 = "2ea31eaca9cf19715fe2a73abc8c3d11c7731466e6e84e50e65db4979be46f8c"
CLOSURE_SHA256 = "24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255"


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode()


def _load_acceptance() -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            assert key not in value, "duplicate JSON key"
            value[key] = item
        return value

    def reject_float(_value: str) -> object:
        raise AssertionError("float/non-finite JSON is forbidden")

    raw = ACCEPTANCE.read_bytes()
    document = json.loads(
        raw,
        object_pairs_hook=no_duplicates,
        parse_float=reject_float,
        parse_constant=reject_float,
    )
    assert isinstance(document, dict)
    assert raw == _canonical(document)
    return document


def test_gate_a_acceptance_binds_review_without_self_reference() -> None:
    document = _load_acceptance()
    assert set(document) == {
        "accepted_tasks",
        "authority_limits",
        "candidate_closure_sha256",
        "candidate_generation_id",
        "candidate_generation_sha256",
        "decision",
        "final_review_receipt_sha256",
        "legacy_rollback",
        "next_task",
        "qualification_source_commit",
        "qualification_source_tree",
        "schema",
        "status",
    }
    assert document["schema"] == "trading-agent-nautilus-u04-final-acceptance/v1"
    assert document["decision"] == "ACCEPT_NT1231_U04_G1"
    assert document["status"] == "U04_ACCEPTED_G1_INACTIVE"
    assert document["qualification_source_commit"] == (
        "3f62908385be289999ccd14eed2e4007efdbf9e2"
    )
    assert document["qualification_source_tree"] == (
        "8b3e147d5c8b27fba989183fb0a8822ec40b97a1"
    )
    assert document["candidate_generation_id"] == "NT1231-U04-G1"
    assert document["candidate_generation_sha256"] == GENERATION_SHA256
    assert document["candidate_closure_sha256"] == CLOSURE_SHA256
    assert document["final_review_receipt_sha256"] == hashlib.sha256(
        FINAL_REVIEW.read_bytes()
    ).hexdigest()
    assert document["accepted_tasks"] == [
        "P1-R0",
        "P1-U01",
        "P1-U02",
        "P1-U03",
        "P1-U04",
        "P1-U04C",
    ]
    assert document["next_task"] == "P1-U05"
    assert document["legacy_rollback"] == {
        "closure_sha256": (
            "14d4fd990dccfdbb8b6dfe964a04ae9e80fefb30914cf433de1bc503b8ad03fa"
        ),
        "schema_version": 6,
        "version": "1.227.0",
    }
    limits = document["authority_limits"]
    assert isinstance(limits, dict)
    assert set(limits) == {
        "candidate_active",
        "candidate_promoted",
        "live_authorized",
        "network_trading_authorized",
        "production_authorized",
    }
    assert set(limits.values()) == {False}
    assert "final_commit" not in json.dumps(document)
    assert "final_tree" not in json.dumps(document)


def test_canonical_u04_documents_have_one_nonpromotional_status() -> None:
    ledger = (P1 / "task-ledger.md").read_text(encoding="utf-8")
    evidence = (UPGRADE / "candidate-build-evidence.md").read_text(encoding="utf-8")
    assert "Status: `U04_ACCEPTED_G1_INACTIVE`" in evidence
    assert "`NT1231-U04-G1`" in evidence
    assert "| P1-R0 | None | ACCEPTED |" in ledger
    assert "| P1-U01 | P1-R0 | ACCEPTED |" in ledger
    assert "| P1-U02 | P1-U01 | ACCEPTED |" in ledger
    assert "| P1-U03 | P1-U02 | ACCEPTED |" in ledger
    assert "| P1-U04 | P1-U03 | ACCEPTED |" in ledger
    assert "| P1-U04C | P1-U04 | ACCEPTED |" in ledger
    assert "| P1-U05 | P1-U04C | READY |" in ledger
    assert "IMPLEMENTED_UNACCEPTED" not in ledger
    assert "X3_EVIDENCE_RECONCILED_RE_REVIEW_REQUIRED" not in ledger
    assert "CANDIDATE_CONTEXT_ONLY" not in ledger
    assert "candidate remains inactive" in ledger
    assert "live, network-trading, or production authority" in ledger


def test_task_matrix_keeps_full_graph_and_adds_only_rebaseline_edges() -> None:
    matrix_path = P1 / "agent-task-matrix.csv"
    with matrix_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    by_id = {row["task_id"]: row for row in rows}
    assert len(rows) == len(by_id) == 42
    assert by_id["P1-R0"]["depends_on"] == "None"
    assert by_id["P1-U04C"]["depends_on"] == "P1-U04"
    assert by_id["P1-U05"]["depends_on"] == "P1-U01, P1-U04C"
    assert by_id["P1-03"]["depends_on"] == "P1-02"
    assert by_id["P1-05"]["depends_on"] == "P1-03, P1-04"
    assert by_id["P1-12"]["depends_on"] == (
        "P1-07, P1-08, P1-09, P1-10, P1-11"
    )
    assert by_id["P1-22"]["depends_on"] == (
        "P1-16, P1-17, P1-18, P1-19, P1-20, P1-21"
    )
    assert by_id["P1-30"]["depends_on"] == "P1-29"

    matrix = matrix_path.read_text(encoding="utf-8")
    plan = (
        ROOT / "docs/superpowers/plans/2026-08-16-p1-real-nautilus-v1.231.md"
    ).read_text(encoding="utf-8")
    wrong_name = "2026-08-16-p1-real-nautilus-v1.231-engine-vertical-slice.md"
    assert wrong_name not in matrix
    assert wrong_name not in plan
    assert "2026-08-16-p1-real-nautilus-v1.231.md" in matrix
    assert "2026-08-16-p1-real-nautilus-v1.231.md" in plan
