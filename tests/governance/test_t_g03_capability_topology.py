from __future__ import annotations

import pytest
from pathlib import Path

from scripts import t_g03_capability_topology as topology


def _receipt(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "t-g03a-capability-receipt/v1",
        "foundation_run_id": "31641536482",
        "foundation_head_sha": "18f22198c65c7bc735aeb848d8fda55209d01e78",
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "lane": "portable-source",
        "capability_or_authority_code": "SRC-SEMANTIC-FIXTURE-IDENTITY",
        "expected_node_ids": ["tests/runtime_release/test_semantic.py::test_one"],
        "collected_node_ids": ["tests/runtime_release/test_semantic.py::test_one"],
        "completeness_sha256": "",
        "preflight_state": "AVAILABLE",
        "redacted_fact_class": "SOURCE_TEST_EXECUTED",
        "outcome": "PASS",
        "receipt_sha256": "",
    }
    document.update(overrides)
    document["completeness_sha256"] = topology.completeness_sha256(document)
    document["receipt_sha256"] = topology.payload_sha256(document)
    return document


def test_receipt_parser_accepts_only_canonical_bytes_and_a_matching_self_hash() -> None:
    """Break caught: whitespace, reordered keys, or a forged payload digest becomes accepted."""
    receipt = _receipt()
    raw = topology.canonical_json_bytes(receipt)

    assert topology.parse_receipt(raw) == receipt

    with pytest.raises(topology.TopologyError, match="canonical"):
        topology.parse_receipt(raw + b"\n")
    receipt["outcome"] = "FAIL"
    with pytest.raises(topology.TopologyError, match="self-hash"):
        topology.parse_receipt(topology.canonical_json_bytes(receipt))


def test_locked_inventory_installs_exact_bytes_once_and_rejects_tampering(tmp_path: Path) -> None:
    """Break caught: an inventory or installed-evidence mapping can silently drift."""
    tracked = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
    rows = topology.load_inventory(tracked)
    installed = topology.install_inventory(tracked, tmp_path / "evidence")
    assert installed.read_bytes() == tracked.read_bytes()
    assert len(rows) == 62
    with pytest.raises(FileExistsError):
        topology.install_inventory(tracked, tmp_path / "evidence")
    changed = tmp_path / "changed.tsv"
    changed.write_bytes(tracked.read_bytes().replace(b"PORTABLE_SOURCE_DEFECT", b"PORTABLE_SOURCE_DEFEKT", 1))
    with pytest.raises(topology.TopologyError, match="hash drift"):
        topology.load_inventory(changed)


def test_aggregate_rejects_partial_and_execution_bearing_deferred_receipts(tmp_path: Path) -> None:
    """Break caught: partial or execution-bearing deferred evidence makes CI green."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    run, head = "31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78"
    paths: list[Path] = []
    for code in topology.CODE_CLASSIFICATION:
        lane, node_ids = topology._expected_rows(rows, code)
        state, outcome = {"portable-source": ("AVAILABLE", "PASS"), "native-capabilities": ("UNAVAILABLE", "DEFERRED"), "external-authorities": ("ABSENT", "DEFERRED")}[lane]
        receipt = _receipt(foundation_run_id=run, foundation_head_sha=head, lane=lane, capability_or_authority_code=code, expected_node_ids=list(node_ids), collected_node_ids=list(node_ids) if outcome == "PASS" else [], preflight_state=state, outcome=outcome)
        path = tmp_path / f"{code}.json"
        path.write_bytes(topology.canonical_json_bytes(receipt))
        paths.append(path)
    summary = topology.aggregate_receipts(paths, rows=rows, foundation_run_id=run, foundation_head_sha=head)
    assert summary["runtime_proof"] == "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS"
    forged = _receipt(foundation_run_id=run, foundation_head_sha=head, lane="external-authorities", capability_or_authority_code="EXT-PHASE3B-CORPUS", expected_node_ids=list(topology._expected_rows(rows, "EXT-PHASE3B-CORPUS")[1]), collected_node_ids=["tests/control_api/test_phase3b_backfill.py::test_real_backfill_plan_has_only_approved_evidence"], preflight_state="ABSENT", outcome="DEFERRED")
    with pytest.raises(topology.TopologyError, match="DEFERRED receipt selected"):
        topology.validate_receipt(topology.canonical_json_bytes(forged), rows=rows, foundation_run_id=run, foundation_head_sha=head)


def test_receipt_rejects_a_json_number_even_when_its_text_is_a_valid_run_id() -> None:
    """Break caught: alternate JSON types bypass the v1 string-only canonical protocol."""
    receipt = _receipt(foundation_run_id=31641536482)
    with pytest.raises(topology.TopologyError, match="foundation run"):
        topology.parse_receipt(topology.canonical_json_bytes(receipt))
