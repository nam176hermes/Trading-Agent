import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

from scripts import t_g03_capability_topology as topology


INVENTORY = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
CLOSURE = Path("docs/implementation/foundation-portable-defect-closure.tsv")


def test_portable_source_defects_are_closed_not_unresolved() -> None:
    rows = topology.load_inventory(INVENTORY)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    closure = topology.load_portable_defect_closure(head_sha=head)

    assert len(rows) == 30
    assert all(row.classification != "PORTABLE_SOURCE_DEFECT" for row in rows)
    assert len(closure) == 32
    assert {row.node_id for row in rows}.isdisjoint(row.node_id for row in closure)


def _closure_document() -> tuple[list[str], list[dict[str, str]]]:
    with CLOSURE.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        return list(reader.fieldnames or ()), list(reader)


def _closure_bytes(fields: list[str], rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda fields, rows: (fields[:-1], [{key: value for key, value in row.items() if key != fields[-1]} for row in rows]), "schema"),
        (lambda fields, rows: (fields, [*rows, rows[0].copy()]), "duplicate"),
        (lambda fields, rows: (fields, rows[:-1]), "count"),
    ),
)
def test_closure_rejects_missing_duplicate_or_wrong_count(
    mutation: object, message: str,
) -> None:
    fields, rows = _closure_document()
    changed_fields, changed_rows = mutation(fields, rows)  # type: ignore[operator]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    with pytest.raises(topology.TopologyError, match=message):
        topology.parse_portable_defect_closure(
            _closure_bytes(changed_fields, changed_rows), head_sha=head,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("source_file", "tests/wrong.py", "source file"),
        ("former_capability_code", "SRC-UNKNOWN", "former code"),
        ("proof_command", "shell-from-ledger", "proof command"),
        ("proof_result_digest", "0" * 64, "proof digest"),
        ("closed_at_foundation_date", "2026-08-14", "Foundation date"),
        ("fix_commit", "f" * 40, "fix commit"),
    ),
)
def test_closure_rejects_forged_row_bindings(field: str, value: str, message: str) -> None:
    fields, rows = _closure_document()
    rows[0][field] = value
    if field == "fix_commit":
        forged = topology.ClosureRow(
            rows[0]["test_node_id"], rows[0]["source_file"],
            rows[0]["former_capability_code"], rows[0]["fix_commit"],
            rows[0]["proof_command"], rows[0]["proof_result_digest"],
            rows[0]["closed_at_foundation_date"],
        )
        rows[0]["proof_result_digest"] = topology.closed_node_proof_digest(forged)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    with pytest.raises(topology.TopologyError, match=message):
        topology.parse_portable_defect_closure(_closure_bytes(fields, rows), head_sha=head)


def test_closure_rejects_a_count_preserving_former_code_swap() -> None:
    fields, rows = _closure_document()
    left = next(index for index, row in enumerate(rows) if row["former_capability_code"] == "SRC-PHASE4B-FAKEROOT-IDENTITY")
    right = next(index for index, row in enumerate(rows) if row["former_capability_code"] == "SRC-SEMANTIC-FIXTURE-IDENTITY")
    rows[left]["former_capability_code"], rows[right]["former_capability_code"] = (
        rows[right]["former_capability_code"], rows[left]["former_capability_code"],
    )
    for index in (left, right):
        row = rows[index]
        forged = topology.ClosureRow(
            row["test_node_id"], row["source_file"], row["former_capability_code"],
            row["fix_commit"], row["proof_command"], row["proof_result_digest"],
            row["closed_at_foundation_date"],
        )
        row["proof_result_digest"] = topology.closed_node_proof_digest(forged)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    with pytest.raises(topology.TopologyError, match="wrong former code for source"):
        topology.parse_portable_defect_closure(_closure_bytes(fields, rows), head_sha=head)


def test_governance_state_rejects_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    active = topology.load_inventory(INVENTORY)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    closure = list(topology.load_portable_defect_closure(head_sha=head))
    closure[0] = topology.ClosureRow(
        active[0].node_id, closure[0].source_file, closure[0].former_code,
        closure[0].fix_commit, closure[0].proof_command,
        closure[0].proof_result_digest, closure[0].closed_at_foundation_date,
    )
    monkeypatch.setattr(topology, "load_portable_defect_closure", lambda *_args, **_kwargs: tuple(closure))
    with pytest.raises(topology.TopologyError, match="overlaps"):
        topology.load_governance_state(INVENTORY, head_sha=head)


def _passing_exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
    report.write_text(json.dumps({
        "schema_version": 1,
        "component": "root",
        "pytest_exit_status": 0,
        "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
        "tests": [{
            "test_node_id": node, "component": "root", "outcome": "passed",
            "reason": "", "phase": "call",
        } for node in nodes],
    }), encoding="utf-8")
    return nodes


def _sealed_empty_remainder(
    monkeypatch: pytest.MonkeyPatch, evidence: Path, raw: str,
) -> tuple[str, str, Path]:
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    context = topology._capture_foundation_context(
        evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    extension = Path(raw) / "custody.so"
    extension.write_bytes(b"portable closure custody fixture")
    monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
    monkeypatch.setenv(
        "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
        topology.hashlib.sha256(extension.read_bytes()).hexdigest(),
    )
    topology.reserve_topology_evidence(
        evidence, run_id=run_id, head_sha=head, foundation_context_path=context,
    )
    active, closure = topology.load_governance_state(INVENTORY, head_sha=head)
    baseline = topology.collect_portable_root_baseline(
        inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head,
        collector=lambda: tuple(sorted(
            {row.node_id for row in active} | {row.node_id for row in closure}
        )),
        foundation_context_path=context,
    )
    remainder = topology.prepare_portable_root_remainder(
        inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head,
        foundation_context_path=context,
    )
    assert remainder["remainder_node_ids"] == []
    (evidence / "capability-topology/portable-root-remainder.governance.json").write_text(
        json.dumps({
            "schema_version": 1, "component": "root", "pytest_exit_status": 0,
            "custody_policy": baseline["collector_policy"], "tests": [],
        }),
        encoding="utf-8",
    )
    return run_id, head, context


def test_closure_proof_is_required_directly_and_accounts_every_node_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head, context = _sealed_empty_remainder(monkeypatch, evidence, raw)
        rows = topology.load_inventory(INVENTORY)
        with pytest.raises(topology.TopologyError, match="closure proof"):
            topology.aggregate_receipts(
                [
                    evidence / "capability-topology" / f"{code}.json"
                    for code in sorted(topology.CODE_CLASSIFICATION)
                ],
                rows=rows, foundation_run_id=run_id, foundation_head_sha=head,
            )
        assert topology.run_lane(
            lane="portable-source", inventory=INVENTORY, evidence_root=evidence,
            run_id=run_id, head_sha=head, foundation_context_path=context,
            exact_runner=_passing_exact,
        ) == []
        monkeypatch.setattr(
            topology, "_native_preflight",
            lambda _code: ("UNAVAILABLE", "NATIVE_COMPONENT_ABSENT"),
        )
        topology.run_lane(
            lane="native-capabilities", inventory=INVENTORY, evidence_root=evidence,
            run_id=run_id, head_sha=head, foundation_context_path=context,
        )
        topology.run_lane(
            lane="external-authorities", inventory=INVENTORY, evidence_root=evidence,
            run_id=run_id, head_sha=head, foundation_context_path=context,
            external_preflight=lambda _code: ("ABSENT", "AUTHORITY_ROOT_ABSENT"),
        )
        result = topology.reconcile_portable_root_accounting(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
            head_sha=head, foundation_context_path=context,
        )
        assert result["portable_source_status"] == "PASS"
        assert result["baseline_candidate_count"] == "62"
        assert not list((evidence / "capability-topology").glob("SRC-*.json"))


def test_closure_proof_rejects_failure_stale_artifact_and_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head, context = _sealed_empty_remainder(monkeypatch, evidence, raw)

        def missing_node(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
            return _passing_exact(nodes[:-1], report)

        with pytest.raises(topology.TopologyError):
            topology.execute_portable_defect_closure(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
                head_sha=head, foundation_context_path=context,
                exact_runner=missing_node,
            )
        assert not (evidence / "capability-topology/portable-defect-closure-proof.json").exists()

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head, context = _sealed_empty_remainder(monkeypatch, evidence, raw)
        stale = evidence / "capability-topology/SRC-SEALEDUV-BWRAP-PREFLIGHT.json"
        stale.write_bytes(b"{}")
        with pytest.raises(topology.TopologyError, match="stale closed-code"):
            topology.execute_portable_defect_closure(
                inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
                head_sha=head, foundation_context_path=context,
                exact_runner=_passing_exact,
            )

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head, context = _sealed_empty_remainder(monkeypatch, evidence, raw)
        proof = topology.execute_portable_defect_closure(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
            head_sha=head, foundation_context_path=context,
            exact_runner=_passing_exact,
        )
        document = json.loads(proof.read_text(encoding="utf-8"))
        document["closure_node_ids"] = document["closure_node_ids"][:-1]
        proof.write_bytes(topology.canonical_json_bytes(document))
        with pytest.raises(topology.TopologyError, match="binding drift"):
            topology.validate_portable_closure_proof(
                proof, foundation_run_id=run_id, foundation_head_sha=head,
                foundation_context=topology.load_foundation_context(
                    context, run_id=run_id, head_sha=head,
                ),
            )
