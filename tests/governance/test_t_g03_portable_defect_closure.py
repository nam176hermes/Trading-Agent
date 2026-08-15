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

    assert len(rows) == 317
    assert all(row.classification != "PORTABLE_SOURCE_DEFECT" for row in rows)
    assert len(closure) == 49
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


@pytest.mark.parametrize("line_ending", ("\r\n", "quoted"))
def test_closure_rejects_nonliteral_tsv_encodings(line_ending: str) -> None:
    raw = CLOSURE.read_bytes()
    changed = raw.replace(b"\n", b"\r\n") if line_ending == "\r\n" else raw.replace(
        b"test_node_id", b'"test_node_id"', 1,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    with pytest.raises(topology.TopologyError, match="nonliteral|noncanonical"):
        topology.parse_portable_defect_closure(changed, head_sha=head)


def _mutate_fix_commit(rows: list[dict[str, str]], commit: str) -> None:
    rows[0]["fix_commit"] = commit
    row = rows[0]
    changed = topology.ClosureRow(
        row["test_node_id"], row["source_file"], row["former_capability_code"],
        row["fix_commit"], row["proof_command"], row["proof_result_digest"],
        row["closed_at_foundation_date"],
    )
    rows[0]["proof_result_digest"] = topology.closed_node_proof_digest(changed)


def test_closure_rejects_an_existing_nonancestor_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fields, rows = _closure_document()
    repository = tmp_path / "repository"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(topology.ROOT), str(repository)],
        check=True,
    )
    empty_tree = subprocess.run(
        ["git", "mktree"], cwd=repository, input="", capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    commit_environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "P0 fixture",
        "GIT_AUTHOR_EMAIL": "p0-fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2026-08-13T00:00:00+00:00",
        "GIT_COMMITTER_NAME": "P0 fixture",
        "GIT_COMMITTER_EMAIL": "p0-fixture@example.invalid",
        "GIT_COMMITTER_DATE": "2026-08-13T00:00:00+00:00",
    }
    nonancestor = subprocess.run(
        ["git", "commit-tree", empty_tree, "-m", "deterministic nonancestor"],
        cwd=repository, env=commit_environment, capture_output=True, text=True,
        check=True,
    ).stdout.strip()
    _mutate_fix_commit(rows, nonancestor)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setattr(topology, "ROOT", repository)
    with pytest.raises(topology.TopologyError, match="nonancestor"):
        topology.parse_portable_defect_closure(_closure_bytes(fields, rows), head_sha=head)


def test_closure_rejects_ancestor_commit_that_does_not_touch_declared_source() -> None:
    fields, rows = _closure_document()
    commit = "62e11f2180331f865f80b5d73a3cc961b28a95b3"
    _mutate_fix_commit(rows, commit)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    with pytest.raises(topology.TopologyError, match="does not touch"):
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
        @topology.contextmanager
        def absent_native_probe(code: str):
            if code in topology.NATIVE_MULTI_CODES:
                yield topology.NativeMultiAuthoritySession(
                    code, "UNAVAILABLE", "NATIVE_COMPONENT_ABSENT",
                    topology._native_multi_probe_record(
                        code, exit_code=topology.NATIVE_PROBE_NOT_EXECUTED,
                    ),
                    topology._nautilus_multi_authority(code), (), lambda: None,
                )
                return
            yield topology.NativeProbeSession(
                code, "UNAVAILABLE", "NATIVE_COMPONENT_ABSENT",
                topology._native_probe_record(
                    code, exit_code=topology.NATIVE_PROBE_NOT_EXECUTED,
                ),
                -1, None, None, None, None,
            )

        topology.run_lane(
            lane="native-capabilities", inventory=INVENTORY, evidence_root=evidence,
            run_id=run_id, head_sha=head, foundation_context_path=context,
            native_probe_factory=absent_native_probe,
        )

        @topology.contextmanager
        def absent_external_session(code: str):
            if code == "EXT-PHASE3B-CORPUS":
                authority = topology._phase3b_absent_authority()
                fact = "AUTHORITY_ROOT_ABSENT"
            elif code == "EXT-LEGACY-UV-AUTHORITY":
                authority = topology._legacy_absent_authority()
                fact = "AUTHORITY_EXECUTABLE_ABSENT"
            elif code in topology.DISPOSABLE_PG_CODES:
                authority = topology._absent_disposable_pg_authority(code)
                fact = "AUTHORITY_RECORD_ABSENT"
            else:
                authority = topology._absent_nautilus_external_authority()
                fact = "AUTHORITY_ROOT_ABSENT"
            yield topology.ExternalAuthoritySession(
                code, "ABSENT", fact, authority, (), lambda: None,
            )

        topology.run_lane(
            lane="external-authorities", inventory=INVENTORY, evidence_root=evidence,
            run_id=run_id, head_sha=head, foundation_context_path=context,
            external_session_factory=absent_external_session,
        )
        result = topology.reconcile_portable_root_accounting(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
            head_sha=head, foundation_context_path=context,
        )
        assert result["portable_source_status"] == "PASS"
        assert result["baseline_candidate_count"] == "366"
        assert not list((evidence / "capability-topology").glob("SRC-*.json"))
        monkeypatch.delenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH")
        monkeypatch.delenv("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256")
        validated = topology.validate_portable_defect_closure(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
            head_sha=head, foundation_context_path=context,
        )
        assert validated["outcome"] == "PASS"


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
        sealed_custody = topology._validate_custody_policy(document["custody_policy"])
        document["closure_node_ids"] = document["closure_node_ids"][:-1]
        proof.write_bytes(topology.canonical_json_bytes(document))
        with pytest.raises(topology.TopologyError, match="binding drift"):
            topology.validate_portable_closure_proof(
                proof, foundation_run_id=run_id, foundation_head_sha=head,
                foundation_context=topology.load_foundation_context(
                    context, run_id=run_id, head_sha=head,
                ),
                sealed_custody=sealed_custody,
            )


def _rehash_proof(proof: Path, governance: Path, mutate: object) -> None:
    proof_document = json.loads(proof.read_text(encoding="utf-8"))
    governance_document = json.loads(governance.read_text(encoding="utf-8"))
    mutate(proof_document, governance_document)  # type: ignore[operator]
    governance.write_bytes(topology.canonical_json_bytes(governance_document))
    proof_document["governance_report_sha256"] = topology.hashlib.sha256(
        governance.read_bytes(),
    ).hexdigest()
    proof_document["closure_proof_sha256"] = topology._closure_proof_payload_sha256(
        proof_document,
    )
    proof.write_bytes(topology.canonical_json_bytes(proof_document))


def test_aggregate_rejects_proof_custody_forged_away_from_sealed_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head, context_path = _sealed_empty_remainder(monkeypatch, evidence, raw)
        proof = topology.execute_portable_defect_closure(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
            head_sha=head, foundation_context_path=context_path,
            exact_runner=_passing_exact,
        )
        governance = proof.parent / "portable-defect-closure.governance.json"
        baseline = topology.load_portable_root_baseline(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        sealed_custody = topology._validate_custody_policy(baseline["collector_policy"])

        def forge_custody(proof_document: dict[str, object], governance_document: dict[str, object]) -> None:
            forged = dict(sealed_custody)
            forged["native_custody_extension_identity"] = "9:9:9:600:9"
            proof_document["custody_policy"] = forged
            proof_document["custody_policy_sha256"] = topology._sha256(forged)
            governance_document["custody_policy"] = forged

        _rehash_proof(proof, governance, forge_custody)
        rows = topology.load_inventory(INVENTORY)
        context = topology.load_foundation_context(context_path, run_id=run_id, head_sha=head)
        receipt_paths = [proof.parent / f"{code}.json" for code in sorted(topology.CODE_CLASSIFICATION)]
        with pytest.raises(topology.TopologyError, match="closure proof"):
            topology.aggregate_receipts(
                receipt_paths, rows=rows, foundation_run_id=run_id,
                foundation_head_sha=head, foundation_context=context,
                closure_proof_path=proof,
            )
        with pytest.raises(topology.TopologyError, match="sealed custody"):
            topology.aggregate_receipts(
                receipt_paths, rows=rows, foundation_run_id=run_id,
                foundation_head_sha=head, foundation_context=context,
                closure_proof_path=proof, sealed_custody=sealed_custody,
            )


@pytest.mark.parametrize(
    "attack",
    (
        "proof-symlink", "governance-symlink", "proof-mode", "governance-mode",
        "owner", "replacement",
    ),
)
def test_closure_artifact_reader_rejects_unsafe_identity(
    monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head, context_path = _sealed_empty_remainder(monkeypatch, evidence, raw)
        proof = topology.execute_portable_defect_closure(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
            head_sha=head, foundation_context_path=context_path,
            exact_runner=_passing_exact,
        )
        governance = proof.parent / "portable-defect-closure.governance.json"
        context = topology.load_foundation_context(context_path, run_id=run_id, head_sha=head)
        baseline = topology.load_portable_root_baseline(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        custody = topology._validate_custody_policy(baseline["collector_policy"])
        if attack == "proof-symlink":
            saved = proof.with_name("saved-proof.json")
            proof.rename(saved)
            proof.symlink_to(saved.name)
        elif attack == "governance-symlink":
            saved = governance.with_name("saved-governance.json")
            governance.rename(saved)
            governance.symlink_to(saved.name)
        elif attack == "proof-mode":
            proof.chmod(0o640)
        elif attack == "governance-mode":
            governance.chmod(0o640)
        elif attack == "owner":
            monkeypatch.setattr(topology.os, "geteuid", lambda: proof.stat().st_uid + 1)
        else:
            replacement = governance.with_name("replacement-governance.json")
            replacement.write_bytes(governance.read_bytes())
            replacement.chmod(0o600)
            real_reader = topology._read_descriptor_bytes
            calls = 0

            def swap_reader(descriptor: int) -> bytes:
                nonlocal calls
                calls += 1
                if calls == 2:
                    os.replace(replacement, governance)
                return real_reader(descriptor)

            monkeypatch.setattr(topology, "_read_descriptor_bytes", swap_reader)
        with pytest.raises(
            topology.TopologyError,
            match="private regular|private artifact directory|identity changed",
        ):
            topology.validate_portable_closure_proof(
                proof, foundation_run_id=run_id, foundation_head_sha=head,
                foundation_context=context, sealed_custody=custody,
            )


def test_closure_artifact_set_rejects_symlinked_replacement_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head, context_path = _sealed_empty_remainder(monkeypatch, evidence, raw)
        proof = topology.execute_portable_defect_closure(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
            head_sha=head, foundation_context_path=context_path,
            exact_runner=_passing_exact,
        )
        context = topology.load_foundation_context(context_path, run_id=run_id, head_sha=head)
        baseline = topology.load_portable_root_baseline(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        custody = topology._validate_custody_policy(baseline["collector_policy"])
        topology_root = proof.parent
        retained = topology_root.with_name("retained-capability-topology")
        topology_root.rename(retained)
        topology_root.symlink_to(retained.name, target_is_directory=True)
        with pytest.raises(topology.TopologyError, match="private artifact directory"):
            topology.validate_portable_closure_proof(
                proof, foundation_run_id=run_id, foundation_head_sha=head,
                foundation_context=context, sealed_custody=custody,
            )


def test_closure_artifact_set_rejects_proof_replaced_while_governance_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head, context_path = _sealed_empty_remainder(monkeypatch, evidence, raw)
        proof = topology.execute_portable_defect_closure(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
            head_sha=head, foundation_context_path=context_path,
            exact_runner=_passing_exact,
        )
        context = topology.load_foundation_context(context_path, run_id=run_id, head_sha=head)
        baseline = topology.load_portable_root_baseline(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        custody = topology._validate_custody_policy(baseline["collector_policy"])
        replacement = proof.with_name("replacement-proof.json")
        replacement.write_bytes(proof.read_bytes())
        replacement.chmod(0o600)
        real_reader = topology._read_descriptor_bytes
        calls = 0

        def swap_proof_while_governance_is_read(descriptor: int) -> bytes:
            nonlocal calls
            calls += 1
            if calls == 2:
                os.replace(replacement, proof)
            return real_reader(descriptor)

        monkeypatch.setattr(topology, "_read_descriptor_bytes", swap_proof_while_governance_is_read)
        with pytest.raises(topology.TopologyError, match="identity changed"):
            topology.validate_portable_closure_proof(
                proof, foundation_run_id=run_id, foundation_head_sha=head,
                foundation_context=context, sealed_custody=custody,
            )


def test_closure_proof_recomputes_each_digest_from_actual_governance_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head, context_path = _sealed_empty_remainder(monkeypatch, evidence, raw)
        proof = topology.execute_portable_defect_closure(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id,
            head_sha=head, foundation_context_path=context_path,
            exact_runner=_passing_exact,
        )
        governance = proof.parent / "portable-defect-closure.governance.json"
        baseline = topology.load_portable_root_baseline(
            inventory=INVENTORY, evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        custody = topology._validate_custody_policy(baseline["collector_policy"])

        def alter_record(
            proof_document: dict[str, object], governance_document: dict[str, object],
        ) -> None:
            record = governance_document["tests"][0]  # type: ignore[index]
            record["phase"] = "setup"
            first_row = sorted(
                topology.load_portable_defect_closure(head_sha=head),
                key=lambda row: row.node_id,
            )[0]
            proof_document["proof_result_digests"][0] = (  # type: ignore[index]
                topology.closed_node_proof_digest(first_row, record)
            )

        _rehash_proof(proof, governance, alter_record)
        context = topology.load_foundation_context(context_path, run_id=run_id, head_sha=head)
        with pytest.raises(topology.TopologyError, match="observed proof digest"):
            topology.validate_portable_closure_proof(
                proof, foundation_run_id=run_id, foundation_head_sha=head,
                foundation_context=context, sealed_custody=custody,
            )
