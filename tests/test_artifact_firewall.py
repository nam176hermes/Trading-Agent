from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import t_g03_capability_topology as topology

try:
    firewall = importlib.import_module("scripts.check_artifact_firewall")
except ImportError:
    firewall = None


HEAD = "9" * 40
TREE = "a" * 64


@pytest.fixture(autouse=True)
def _require_firewall_after_cli_red(request: pytest.FixtureRequest) -> None:
    if firewall is None and request.node.name != "test_cli_publishes_one_final_evidence_set":
        pytest.skip("CLI boundary must exist before API adversarial REDs execute")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _semantic(**updates: object) -> dict[str, object]:
    rows = topology.load_inventory(
        Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
    )
    policy = {
        **topology.PORTABLE_ROOT_POLICY,
        "native_custody_extension_identity": f"1:2:{os.geteuid()}:600:1",
        "native_custody_extension_sha256": "e" * 64,
    }
    value: dict[str, object] = {
        "foundation": {"head_sha": HEAD, "validation_date": "2026-08-13"},
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "closure_sha256": topology.LOCKED_CLOSURE_SHA256,
        "policy_sha256": topology._sha256(policy),
        "selected_tests": [],
        "statuses": {
            "portable_source_status": "PASS",
            "native_capabilities_status": "DEFERRED",
            "external_authorities_status": "DEFERRED",
            "runtime_proof": "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS",
            "portable_root_remainder_status": "PASS",
            "baseline_candidate_count": "62",
        },
        "receipt_results": [
            {
                "code": code,
                "outcome": "DEFERRED",
                "selected": 0,
                "passed": 0,
                "failed": 0,
                "unavailable": len(topology._expected_rows(rows, code)[1]),
            }
            for code in sorted(topology.CODE_CLASSIFICATION)
        ],
    }
    value.update(updates)
    return value


def _run_metadata(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "run_id": "1001",
        "attempt": "1",
        "generated_at_utc": "2026-08-13T12:00:00+00:00",
    }
    value.update(updates)
    return value


def _write_leaf(root: Path, relative: str, value: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(value if isinstance(value, bytes) else _canonical(value))
    path.chmod(0o600)


def _fixture_staging(tmp_path: Path) -> Path:
    root = tmp_path / "raw-final-projection"
    root.mkdir(parents=True, mode=0o700)
    _write_leaf(
        root,
        "capability-topology/foundation-context.json",
        {"schema_version": "fixture/v1", "status": "PASS"},
    )
    _write_leaf(
        root,
        "capability-topology/NATIVE-BWRAP-OS-SANDBOX.json",
        {"capability_or_authority_code": "NATIVE-BWRAP-OS-SANDBOX", "outcome": "DEFERRED"},
    )
    marker = (root / "capability-topology/NATIVE-BWRAP-OS-SANDBOX.json").read_bytes()
    _write_leaf(
        root,
        "capability-topology/NATIVE-BWRAP-OS-SANDBOX.artifacts/receipt.json",
        marker,
    )
    _write_leaf(
        root,
        "capability-topology/NATIVE-BWRAP-OS-SANDBOX.artifacts/manifest.json",
        {"schema_version": "fixture-manifest/v1", "outcome": "DEFERRED"},
    )
    _write_leaf(
        root,
        "test-governance/summary.json",
        {"schema_version": "fixture-summary/v1", "status": "pass", "tests": []},
    )
    return root


def _staging(tmp_path: Path) -> Path:
    root = tmp_path / "raw-final-projection"
    root.mkdir(parents=True, mode=0o700)
    run_id = "1001"
    context: dict[str, object] = {
        "schema_version": topology.FOUNDATION_CONTEXT_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": HEAD,
        "foundation_validation_date": "2026-08-13",
        "foundation_context_sha256": "",
    }
    context["foundation_context_sha256"] = topology._sha256({
        key: value for key, value in context.items()
        if key != "foundation_context_sha256"
    })
    context_hash = str(context["foundation_context_sha256"])
    policy = {
        **topology.PORTABLE_ROOT_POLICY,
        "native_custody_extension_identity": f"1:2:{os.geteuid()}:600:1",
        "native_custody_extension_sha256": "e" * 64,
    }
    inventory_path = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
    inventory_raw = inventory_path.read_bytes()
    closure_path = Path("docs/implementation/foundation-portable-defect-closure.tsv")
    closure_raw = closure_path.read_bytes()
    inventory_rows = topology.load_inventory(inventory_path)
    closure_lines = closure_raw.decode("utf-8").splitlines()
    closure_header = closure_lines[0].split("\t")
    closure_records = [dict(zip(closure_header, line.split("\t"), strict=True)) for line in closure_lines[1:]]
    governed_nodes = tuple(sorted(
        {row.node_id for row in inventory_rows}
        | {record["test_node_id"] for record in closure_records}
    ))
    candidate_raw = ("\n".join(governed_nodes) + "\n").encode()
    collection = {
        "schema_version": 1,
        "component": "root",
        "collection_only": True,
        "pytest_exit_status": 0,
        "tests": [
            {
                "test_node_id": node,
                "component": "root",
                "outcome": "collected",
                "reason": "",
                "phase": "collection",
            }
            for node in governed_nodes
        ],
    }
    collection_raw = _canonical(collection)
    baseline: dict[str, object] = {
        "schema_version": topology.BASELINE_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": HEAD,
        "foundation_validation_date": "2026-08-13",
        "foundation_context_sha256": context_hash,
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "closure_sha256": topology.LOCKED_CLOSURE_SHA256,
        "collector_policy": policy,
        "candidate_node_ids": list(governed_nodes),
        "candidate_file_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "collection_report_sha256": hashlib.sha256(collection_raw).hexdigest(),
        "baseline_sha256": "",
    }
    baseline["baseline_sha256"] = topology._baseline_payload_sha256(baseline)
    empty_governance = {
        "schema_version": 1,
        "component": "root",
        "pytest_exit_status": 0,
        "custody_policy": policy,
        "tests": [],
    }
    empty_governance_raw = _canonical(empty_governance)
    remainder: dict[str, object] = {
        "schema_version": topology.REMAINDER_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": HEAD,
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "closure_sha256": topology.LOCKED_CLOSURE_SHA256,
        "baseline_sha256": baseline["baseline_sha256"],
        "remainder_node_ids": [],
        "remainder_file_sha256": hashlib.sha256(b"").hexdigest(),
        "remainder_sha256": "",
    }
    remainder["remainder_sha256"] = topology._remainder_payload_sha256(remainder)
    closure_governance = {
        "schema_version": 1,
        "component": "root",
        "pytest_exit_status": 0,
        "custody_policy": policy,
        "tests": [
            {
                "test_node_id": record["test_node_id"],
                "component": "root",
                "outcome": "passed",
                "reason": "",
                "phase": "call",
            }
            for record in sorted(closure_records, key=lambda item: item["test_node_id"])
        ],
    }
    closure_governance_raw = _canonical(closure_governance)
    closure_nodes = [record["test_node_id"] for record in sorted(closure_records, key=lambda item: item["test_node_id"])]
    closure_proof: dict[str, object] = {
        "schema_version": topology.PORTABLE_CLOSURE_PROOF_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": HEAD,
        "foundation_validation_date": "2026-08-13",
        "foundation_context_sha256": context_hash,
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "closure_sha256": topology.LOCKED_CLOSURE_SHA256,
        "closure_node_ids": closure_nodes,
        "closure_node_ids_sha256": topology._ids_sha256(tuple(closure_nodes)),
        "proof_command": topology.CLOSURE_PROOF_COMMAND,
        "proof_result_digests": [
            record["proof_result_digest"]
            for record in sorted(closure_records, key=lambda item: item["test_node_id"])
        ],
        "custody_policy": policy,
        "custody_policy_sha256": topology._sha256(policy),
        "governance_report_sha256": hashlib.sha256(closure_governance_raw).hexdigest(),
        "outcome": "PASS",
        "closure_proof_sha256": "",
    }
    closure_proof["closure_proof_sha256"] = topology._closure_proof_payload_sha256(closure_proof)
    reservation = {
        "schema_version": topology.RESERVATION_SCHEMA,
        "foundation_head_sha": HEAD,
        "foundation_run_id": run_id,
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "closure_sha256": topology.LOCKED_CLOSURE_SHA256,
        "foundation_context_sha256": context_hash,
    }
    aggregate = {
        "portable_source_status": "PASS",
        "native_capabilities_status": "DEFERRED",
        "external_authorities_status": "DEFERRED",
        "runtime_proof": "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS",
        "portable_root_remainder_status": "PASS",
        "baseline_candidate_count": str(len(governed_nodes)),
    }
    fixed: dict[str, object] = {
        ".reservation": reservation,
        "aggregate.json": aggregate,
        "foundation-context.json": context,
        "foundation-portable-defect-closure.tsv": closure_raw,
        "portable-defect-closure-proof.json": closure_proof,
        "portable-defect-closure.governance.json": closure_governance_raw,
        "portable-root-baseline.json": baseline,
        "portable-root-candidates.txt": candidate_raw,
        "portable-root-collection.governance.json": collection_raw,
        "portable-root-remainder.governance.json": empty_governance_raw,
        "portable-root-remainder.json": remainder,
        "portable-root-remainder.txt": b"",
        "t-g03a-hosted-failure-inventory.tsv": inventory_raw,
    }
    for name, value in fixed.items():
        _write_leaf(root, f"capability-topology/{name}", value)
    receipt_results: list[dict[str, object]] = []
    for code in sorted(topology.CODE_CLASSIFICATION):
        expected = topology._expected_rows(inventory_rows, code)[1]
        if code.startswith("NATIVE-"):
            session = SimpleNamespace(
                state="UNAVAILABLE",
                fact="NATIVE_COMPONENT_ABSENT",
                probe=topology._native_probe_record(
                    code, exit_code=topology.NATIVE_PROBE_NOT_EXECUTED,
                ),
            )
            receipt = topology.make_native_receipt(
                context=context, code=code, expected=expected, collected=(),
                session=session, outcome="DEFERRED", selected_test_count=0,
                passed=0, failed=0, unavailable=len(expected),
            )
            manifest = topology._native_artifact_manifest(
                receipt, topology.canonical_json_bytes(receipt), None,
            )
        else:
            authority = (
                topology._phase3b_absent_authority()
                if code == "EXT-PHASE3B-CORPUS"
                else topology._legacy_absent_authority()
            )
            session = SimpleNamespace(
                state="ABSENT",
                fact=(
                    "AUTHORITY_ROOT_ABSENT"
                    if code == "EXT-PHASE3B-CORPUS"
                    else "AUTHORITY_EXECUTABLE_ABSENT"
                ),
                authority=authority,
            )
            receipt = topology.make_external_receipt(
                context=context, code=code, expected=expected, collected=(),
                session=session, outcome="DEFERRED", selected_test_count=0,
                passed=0, failed=0, unavailable=len(expected),
            )
            manifest = topology._external_artifact_manifest(
                receipt, topology.canonical_json_bytes(receipt), None,
            )
        receipt_raw = topology.canonical_json_bytes(receipt)
        _write_leaf(root, f"capability-topology/{code}.json", receipt_raw)
        _write_leaf(root, f"capability-topology/{code}.artifacts/receipt.json", receipt_raw)
        _write_leaf(root, f"capability-topology/{code}.artifacts/manifest.json", manifest)
        receipt_results.append({
            "code": code,
            "outcome": "DEFERRED",
            "selected": 0,
            "passed": 0,
            "failed": 0,
            "unavailable": len(expected),
        })
    summary = {
        "schema_version": "test-governance-final-summary/v1",
        "status": "pass",
        "summary": {"passed": 1},
        "postgres_disclosure": {"status": "not_applicable"},
        "capability_topology": aggregate,
        "tests": [],
        "suite_exit_codes": {"root": 0},
        "allowlist": "tests/skip-allowlist.yaml",
        "generated_at_utc": "2026-08-13T12:00:00+00:00",
    }
    _write_leaf(root, "test-governance/summary.json", summary)
    return root


def _publish(staging: Path, destination: Path, **kwargs: object) -> dict[str, object]:
    return firewall.publish_evidence_set(
        staging_root=staging,
        destination=destination,
        head_sha=HEAD,
        source_tree_sha256=TREE,
        semantic_projection=_semantic(),
        run_metadata=_run_metadata(),
        **kwargs,
    )


def test_cli_publishes_one_final_evidence_set(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    destination = tmp_path / "runtime/state/ci-portable"
    semantic_path = tmp_path / "semantic.json"
    run_metadata_path = tmp_path / "run.json"
    semantic_path.write_bytes(_canonical(_semantic()))
    run_metadata_path.write_bytes(_canonical(_run_metadata()))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.check_artifact_firewall",
            "publish-projection",
            "--staging-root",
            str(staging),
            "--destination",
            str(destination),
            "--head-sha",
            HEAD,
            "--source-tree-sha256",
            TREE,
            "--semantic-projection",
            str(semantic_path),
            "--run-metadata",
            str(run_metadata_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert destination.is_dir()
    assert (destination / "manifest.json").is_file()


def test_semantic_digest_excludes_run_metadata_and_changes_for_each_semantic_mutation() -> None:
    base = _semantic()
    first = firewall.semantic_result_sha256(base)
    assert first == firewall.semantic_result_sha256(dict(base))
    assert first == firewall.semantic_result_sha256(base, run_metadata=_run_metadata())
    assert first == firewall.semantic_result_sha256(
        base,
        run_metadata=_run_metadata(
            run_id="2002", attempt="9", generated_at_utc="2026-08-14T00:00:00+00:00",
        ),
    )
    for key, replacement in (
        ("inventory_sha256", "e" * 64),
        ("closure_sha256", "f" * 64),
        ("policy_sha256", "0" * 64),
        ("selected_tests", [{"node_id": "tests/test_safe.py::test_one", "outcome": "failed"}]),
        ("statuses", {"portable_source": "FAIL"}),
        ("receipt_results", [{"code": "NATIVE-BWRAP-OS-SANDBOX", "outcome": "PASS", "selected": 8, "passed": 8, "failed": 0, "unavailable": 0}]),
    ):
        changed = dict(base)
        changed[key] = replacement
        assert firewall.semantic_result_sha256(changed) != first


def test_publisher_binds_source_tree_digest_into_canonical_semantic_projection(
    tmp_path: Path,
) -> None:
    first = firewall.publish_evidence_set(
        staging_root=_staging(tmp_path / "first"),
        destination=tmp_path / "first-final",
        head_sha=HEAD,
        source_tree_sha256=TREE,
        semantic_projection=_semantic(),
        run_metadata=_run_metadata(),
    )
    changed_tree = "f" * 64
    second = firewall.publish_evidence_set(
        staging_root=_staging(tmp_path / "second"),
        destination=tmp_path / "second-final",
        head_sha=HEAD,
        source_tree_sha256=changed_tree,
        semantic_projection=_semantic(),
        run_metadata=_run_metadata(),
    )
    assert first["semantic_projection"]["source_tree_sha256"] == TREE
    assert second["semantic_projection"]["source_tree_sha256"] == changed_tree
    assert first["semantic_result_sha256"] != second["semantic_result_sha256"]


@pytest.mark.parametrize(
    ("document", "category"),
    [
        ({"stdout": {"nested": [{"password": "not-redacted"}]}}, "PASSWORD"),
        ({"stderr": [{"authorization": "Bearer sensitive-material"}]}, "AUTHORIZATION"),
        ({"stderr": "-----BEGIN PRIVATE KEY-----\nmaterial"}, "PRIVATE_KEY"),
        ({"stdout": "postgresql://alice:credential@db.example/trading"}, "DATABASE_URL"),
        ({"exchange": {"api_key": "exchange-key-material"}}, "API_KEY"),
        ({"broker_credentials": {"token": "broker-token-material"}}, "BROKER_CREDENTIAL"),
        ({"TRADING_MASTER_KEY": "master-key-material"}, "TRADING_MASTER_KEY"),
        ({"stdout": "LIVE_EXECUTION_ENABLED=true"}, "LIVE_EXECUTION_GATE"),
        ({"stderr": "LIVE_TRADING_ENABLED=true"}, "LIVE_TRADING_GATE"),
    ],
)
def test_firewall_rejects_structured_and_known_secret_formats_without_disclosure(
    document: object, category: str,
) -> None:
    raw = _canonical(document)
    digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(firewall.FirewallError) as caught:
        firewall.scan_json_bytes("test-governance/summary.json", raw)
    error = caught.value
    assert error.code == "ARTIFACT_SECRET_REJECTED"
    assert error.category == category
    assert error.relative_path == "test-governance/summary.json"
    assert error.sha256 == digest
    assert "sensitive-material" not in str(error)
    assert "master-key-material" not in str(error)
    assert "broker-token-material" not in str(error)


@pytest.mark.parametrize(
    ("field", "line", "category"),
    [
        ("stdout", "TRADING_MASTER_KEY=synthetic-value", "TRADING_MASTER_KEY"),
        ("stderr", "password: synthetic-value", "PASSWORD"),
        ("stdout", "secret = synthetic-value", "SECRET"),
        ("stderr", "api_key=synthetic-value", "API_KEY"),
        ("stdout", "Authorization: Bearer synthetic-value", "AUTHORIZATION"),
    ],
)
def test_firewall_rejects_contextual_stdout_stderr_assignments_without_value_disclosure(
    field: str, line: str, category: str,
) -> None:
    raw = _canonical({field: line})
    with pytest.raises(firewall.FirewallError) as caught:
        firewall.scan_json_bytes("test-governance/summary.json", raw)
    assert caught.value.category == category
    assert "synthetic-value" not in str(caught.value)


@pytest.mark.parametrize(
    ("line", "category"),
    [
        ("TRADING_MASTER_KEY=synthetic-value\n", "TRADING_MASTER_KEY"),
        ("password: synthetic-value\n", "PASSWORD"),
        ("secret = synthetic-value\n", "SECRET"),
        ("api_key=synthetic-value\n", "API_KEY"),
        ("Authorization: Bearer synthetic-value\n", "AUTHORIZATION"),
    ],
)
def test_firewall_rejects_anchored_assignments_in_non_json_text_leaves(
    line: str, category: str,
) -> None:
    raw = line.encode()
    with pytest.raises(firewall.FirewallError) as caught:
        firewall.scan_artifact_bytes("test-governance/root.log", raw)
    assert caught.value.category == category
    assert "synthetic-value" not in str(caught.value)


@pytest.mark.parametrize(
    "line",
    [
        "documentation: password and secret are prohibited\n",
        "tests/test_api_key_rotation.py::test_authorization_is_redacted\n",
        "password: [REDACTED]\n",
        "secret=<redacted>\n",
        "api_key=NOT_CONFIGURED\n",
        "Authorization: <redacted>\n",
    ],
)
def test_firewall_allows_documentation_and_redacted_assignment_near_misses(
    line: str,
) -> None:
    firewall.scan_artifact_bytes("test-governance/root.log", line.encode())


@pytest.mark.parametrize(
    "document",
    [
        {"documentation": "password and secret are prohibited in evidence"},
        {"test_node_id": "tests/test_api_key_rotation.py::test_secret_is_redacted"},
        {"password": "[REDACTED]", "authorization": "<redacted>"},
        {"secret_status": "ABSENT", "api_key_status": "NOT_CONFIGURED"},
        {"stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64},
        {"database_url": "postgresql://localhost/trading"},
        {"live_execution_enabled": False, "live_trading_enabled": False},
    ],
)
def test_firewall_allows_redacted_and_safe_near_misses(document: object) -> None:
    firewall.scan_json_bytes("test-governance/summary.json", _canonical(document))


def test_publisher_scans_known_secret_formats_in_every_manifested_leaf(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    secret_raw = b"-----BEGIN PRIVATE KEY-----\nmaterial"
    _write_leaf(
        staging,
        "phase-evidence/result.txt",
        secret_raw,
    )
    _write_leaf(
        staging,
        "phase-evidence/manifest.json",
        {
            "schema_version": "phase-evidence-manifest/v1",
            "files": [{
                "path": "result.txt",
                "sha256": hashlib.sha256(secret_raw).hexdigest(),
                "size": len(secret_raw),
                "mode": "0400",
            }],
        },
    )
    with pytest.raises(firewall.FirewallError) as caught:
        _publish(staging, tmp_path / "runtime/state/ci-portable")
    assert caught.value.category == "PRIVATE_KEY"


def test_publisher_creates_canonical_manifest_and_exact_checksums(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    destination = tmp_path / "runtime/state/ci-portable"
    manifest = _publish(staging, destination)

    assert manifest["semantic_result_sha256"] == firewall.semantic_result_sha256({
        **_semantic(), "source_tree_sha256": TREE,
    })
    assert manifest["run_metadata"] == _run_metadata()
    assert manifest["manifest_payload_sha256"] == firewall.manifest_payload_sha256(manifest)
    assert stat_mode(destination) == 0o500
    leaves = sorted(
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*") if path.is_file()
    )
    assert leaves == sorted(["manifest.json", "SHA256SUMS", *[item["path"] for item in manifest["files"]]])
    checksum_lines = (destination / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    expected_checksum_paths = sorted(path for path in leaves if path != "SHA256SUMS")
    assert [line.split("  ", 1)[1] for line in checksum_lines] == expected_checksum_paths
    assert len(checksum_lines) == len(set(checksum_lines))
    firewall.validate_published_evidence(destination)


def test_hidden_reservation_survives_artifact_round_trip_with_checksum_binding(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "runtime/state/ci-portable"
    _publish(_staging(tmp_path), destination)
    checksums = (destination / "SHA256SUMS").read_text(encoding="ascii")
    assert "capability-topology/.reservation" in checksums
    downloaded = tmp_path / "downloaded/ci-portable"
    shutil.copytree(destination, downloaded, copy_function=shutil.copy2)
    manifest = firewall.validate_published_evidence(downloaded)
    assert any(
        item["path"] == "capability-topology/.reservation"
        for item in manifest["files"]
    )


def test_error_projection_contains_only_closed_error_and_never_pass_summary(
    tmp_path: Path,
) -> None:
    staging = _staging(tmp_path)
    summary = staging / "test-governance/summary.json"
    summary.unlink()
    error_semantic = {
        "error_code": "SUITE_FAILURE",
        "suite_exit_codes": {"root": 1},
    }
    _write_leaf(
        staging,
        "test-governance/error.json",
        {
            "schema_version": "test-governance-final-error/v1",
            "status": "error",
            "generated_at_utc": "2026-08-13T12:00:00+00:00",
            **error_semantic,
        },
    )
    semantic = _semantic()
    semantic.pop("selected_tests")
    semantic["governance_error"] = error_semantic
    destination = tmp_path / "runtime/state/ci-portable"
    manifest = firewall.publish_evidence_set(
        staging_root=staging,
        destination=destination,
        head_sha=HEAD,
        source_tree_sha256=TREE,
        semantic_projection=semantic,
        run_metadata=_run_metadata(),
    )
    assert (destination / "test-governance/error.json").is_file()
    assert not (destination / "test-governance/summary.json").exists()
    assert "selected_tests" not in manifest["semantic_projection"]


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _rehash_published_tree(destination: Path) -> None:
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload_paths = sorted(
        path for path in destination.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS"}
    )
    manifest["files"] = [
        {
            "path": path.relative_to(destination).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": len(path.read_bytes()),
            "mode": "0400",
        }
        for path in payload_paths
    ]
    manifest["manifest_payload_sha256"] = firewall.manifest_payload_sha256(manifest)
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(_canonical(manifest))
    manifest_path.chmod(0o400)
    checksums = destination / "SHA256SUMS"
    checksums.chmod(0o600)
    checksum_paths = sorted([manifest_path, *payload_paths])
    checksums.write_bytes(b"".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(destination).as_posix()}\n".encode()
        for path in checksum_paths
    ))
    checksums.chmod(0o400)


def test_existing_destination_is_conflict_and_is_never_overwritten(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    destination = tmp_path / "runtime/state/ci-portable"
    destination.mkdir(parents=True)
    foreign = destination / "foreign"
    foreign.write_bytes(b"preserve")
    with pytest.raises(firewall.FirewallError, match="destination already exists"):
        _publish(staging, destination)
    assert foreign.read_bytes() == b"preserve"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_publisher_rejects_symlink_hardlink_and_special_leaves(
    tmp_path: Path, kind: str,
) -> None:
    staging = _staging(tmp_path)
    leaf = staging / "test-governance/summary.json"
    original = leaf.read_bytes()
    leaf.unlink()
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(original)
        leaf.symlink_to(target)
    elif kind == "hardlink":
        target = tmp_path / "target"
        target.write_bytes(original)
        os.link(target, leaf)
    else:
        os.mkfifo(leaf, mode=0o600)
    with pytest.raises(firewall.FirewallError):
        _publish(staging, tmp_path / "runtime/state/ci-portable")


def test_publisher_rejects_unsafe_staging_and_final_parent_symlinks(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    alias = tmp_path / "staging-alias"
    alias.symlink_to(staging, target_is_directory=True)
    with pytest.raises(firewall.FirewallError):
        _publish(alias, tmp_path / "runtime/state/ci-portable")

    final_parent = tmp_path / "runtime/state"
    final_parent.parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    final_parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(firewall.FirewallError):
        _publish(staging, final_parent / "ci-portable")


def test_publisher_rejects_extra_missing_and_architecture_a_fallback(tmp_path: Path) -> None:
    for mutation in ("extra", "missing", "flat", "bundle_missing"):
        case = tmp_path / mutation
        case.mkdir()
        staging = _staging(case)
        if mutation == "extra":
            _write_leaf(staging, "unexpected.json", {})
        elif mutation == "missing":
            (staging / "test-governance/summary.json").unlink()
        elif mutation == "flat":
            _write_leaf(staging, "capability-topology/native-capability-receipt.json", {})
        else:
            bundle = staging / "capability-topology/NATIVE-BWRAP-OS-SANDBOX.artifacts"
            os.replace(bundle, case / "moved-bundle")
        with pytest.raises(firewall.FirewallError):
            _publish(staging, case / "runtime/state/ci-portable")


def test_publisher_rejects_fixture_schemas_and_incomplete_topology(tmp_path: Path) -> None:
    staging = _fixture_staging(tmp_path)
    destination = tmp_path / "runtime/state/ci-portable"
    with pytest.raises(firewall.FirewallError, match="schema|inventory|topology"):
        _publish(staging, destination)
    assert not destination.exists()


def test_publisher_rejects_receipt_filename_code_and_bundle_manifest_drift(
    tmp_path: Path,
) -> None:
    for mutation in ("wrong-code", "wrong-outcome", "fake-manifest"):
        case = tmp_path / mutation
        case.mkdir()
        staging = _staging(case)
        marker = staging / "capability-topology/NATIVE-BWRAP-OS-SANDBOX.json"
        receipt = marker.parent / "NATIVE-BWRAP-OS-SANDBOX.artifacts/receipt.json"
        manifest = marker.parent / "NATIVE-BWRAP-OS-SANDBOX.artifacts/manifest.json"
        if mutation == "wrong-code":
            raw = _canonical({
                "capability_or_authority_code": "NATIVE-USERNS-ROOT-PROVISION",
                "outcome": "DEFERRED",
            })
            marker.write_bytes(raw)
            receipt.write_bytes(raw)
            marker.chmod(0o600)
            receipt.chmod(0o600)
        elif mutation == "wrong-outcome":
            manifest.write_bytes(_canonical({
                "schema_version": "fixture-manifest/v1", "outcome": "PASS",
            }))
            manifest.chmod(0o600)
        else:
            manifest.write_bytes(_canonical({
                "schema_version": "fake-native-artifact-manifest/v99",
                "outcome": "DEFERRED",
            }))
            manifest.chmod(0o600)
        with pytest.raises(firewall.FirewallError, match="schema|code|outcome|manifest|topology"):
            _publish(staging, case / "runtime/state/ci-portable")


def test_publisher_rejects_phase_manifest_that_does_not_bind_its_entries(
    tmp_path: Path,
) -> None:
    staging = _staging(tmp_path)
    _write_leaf(
        staging,
        "phase-evidence/manifest.json",
        {"schema_version": "phase-evidence-manifest/v1", "files": []},
    )
    _write_leaf(staging, "phase-evidence/result.txt", b"PASS\n")
    with pytest.raises(firewall.FirewallError, match="phase|manifest"):
        _publish(staging, tmp_path / "runtime/state/ci-portable")


def test_publisher_rejects_unmanifested_empty_phase_directory(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    result = b"PASS\n"
    _write_leaf(staging, "phase-evidence/result.txt", result)
    _write_leaf(
        staging,
        "phase-evidence/manifest.json",
        {
            "schema_version": "phase-evidence-manifest/v1",
            "files": [{
                "path": "result.txt",
                "sha256": hashlib.sha256(result).hexdigest(),
                "size": len(result),
                "mode": "0400",
            }],
        },
    )
    (staging / "phase-evidence/unmanifested-empty").mkdir(mode=0o700)

    with pytest.raises(firewall.FirewallError, match="phase|manifest|inventory"):
        _publish(staging, tmp_path / "runtime/state/ci-portable")


def test_publisher_rejects_marker_bundle_mismatch_without_mutating_either(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    marker = staging / "capability-topology/NATIVE-BWRAP-OS-SANDBOX.json"
    bundle = staging / "capability-topology/NATIVE-BWRAP-OS-SANDBOX.artifacts/receipt.json"
    marker_before = marker.read_bytes()
    bundle.write_bytes(_canonical({"outcome": "PASS"}))
    bundle.chmod(0o600)
    with pytest.raises(firewall.FirewallError, match="marker/bundle"):
        _publish(staging, tmp_path / "runtime/state/ci-portable")
    assert marker.read_bytes() == marker_before
    assert bundle.read_bytes() == _canonical({"outcome": "PASS"})


def test_publisher_rejects_mutation_after_snapshot_and_never_publishes_partial_set(
    tmp_path: Path,
) -> None:
    staging = _staging(tmp_path)
    leaf = staging / "test-governance/summary.json"

    def mutate(boundary: str) -> None:
        if boundary == "after-manifest":
            leaf.write_bytes(_canonical({"status": "mutated"}))
            leaf.chmod(0o600)

    destination = tmp_path / "runtime/state/ci-portable"
    with pytest.raises(firewall.FirewallError, match="changed"):
        _publish(staging, destination, boundary_hook=mutate)
    assert not destination.exists()


def test_publisher_rejects_staging_ancestor_replacement_after_lineage_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    legitimate_parent = tmp_path / "legitimate"
    staging = _staging(legitimate_parent)
    attacker_parent = tmp_path / "attacker"
    _staging(attacker_parent)
    displaced_parent = tmp_path / "displaced"
    original = firewall._validate_lineage
    staging_checks = 0

    def replace_after_validation(path: Path, *, create: bool) -> object:
        nonlocal staging_checks
        retained = original(path, create=create)
        if path.absolute() == staging.absolute():
            staging_checks += 1
            if staging_checks == 2:
                os.rename(legitimate_parent, displaced_parent)
                os.rename(attacker_parent, legitimate_parent)
        return retained

    monkeypatch.setattr(firewall, "_validate_lineage", replace_after_validation)
    destination = tmp_path / "runtime/state/ci-portable"
    with pytest.raises(firewall.FirewallError, match="identity|changed|lineage"):
        _publish(staging, destination)
    assert not destination.exists()


def test_publisher_rejects_named_child_directory_replacement_after_snapshot(
    tmp_path: Path,
) -> None:
    staging = _staging(tmp_path)
    displaced = tmp_path / "displaced-test-governance"

    def replace_child(boundary: str) -> None:
        if boundary == "after-manifest":
            original = staging / "test-governance"
            os.rename(original, displaced)
            replacement = staging / "test-governance"
            replacement.mkdir(mode=0o700)
            _write_leaf(
                staging,
                "test-governance/summary.json",
                {"schema_version": "fixture-summary/v1", "status": "pass", "tests": []},
            )

    destination = tmp_path / "runtime/state/ci-portable"
    with pytest.raises(firewall.FirewallError, match="identity|changed"):
        _publish(staging, destination, boundary_hook=replace_child)
    assert not destination.exists()


def test_publisher_rejects_byte_identical_child_inode_replacement_after_mode_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = _staging(tmp_path / "source")
    destination = tmp_path / "runtime/state/ci-portable"
    displaced = tmp_path / "displaced-summary.json"
    original = firewall._seal_candidate_modes
    replaced = False

    def seal_then_replace(**kwargs: object) -> object:
        nonlocal replaced
        result = original(**kwargs)
        candidate = kwargs["candidate"]
        assert isinstance(candidate, Path)
        parent = candidate / "test-governance"
        leaf = parent / "summary.json"
        raw = leaf.read_bytes()
        parent.chmod(0o700)
        os.rename(leaf, displaced)
        leaf.write_bytes(raw)
        leaf.chmod(0o400)
        parent.chmod(0o500)
        replaced = True
        return result

    monkeypatch.setattr(firewall, "_seal_candidate_modes", seal_then_replace)
    with pytest.raises(firewall.FirewallError, match="identity|changed"):
        _publish(staging, destination)
    assert replaced


def test_publisher_rejects_named_child_directory_replacement_after_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = _staging(tmp_path / "source")
    foreign_staging = _staging(tmp_path / "foreign-source")
    foreign = tmp_path / "foreign/state/ci-portable"
    _publish(foreign_staging, foreign)
    foreign.chmod(0o700)
    (foreign / "test-governance").chmod(0o700)
    replacement = tmp_path / "replacement-test-governance"
    os.rename(foreign / "test-governance", replacement)
    original = firewall._renameat2_noreplace
    destination = tmp_path / "runtime/state/ci-portable"
    replaced = False

    def rename_then_replace_child(*args: object) -> None:
        nonlocal replaced
        original(*args)
        destination.chmod(0o700)
        current = destination / "test-governance"
        current.chmod(0o700)
        displaced = tmp_path / "displaced-final-child"
        os.rename(current, displaced)
        displaced.chmod(0o500)
        os.rename(replacement, destination / "test-governance")
        (destination / "test-governance").chmod(0o500)
        destination.chmod(0o500)
        replaced = True

    monkeypatch.setattr(firewall, "_renameat2_noreplace", rename_then_replace_child)
    with pytest.raises(firewall.FirewallError, match="identity|changed"):
        _publish(staging, destination)
    assert replaced


def test_publisher_rejects_final_root_identity_swap_before_reopened_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = _staging(tmp_path / "source")
    foreign_staging = _staging(tmp_path / "foreign-source")
    foreign = tmp_path / "foreign/state/ci-portable"
    _publish(foreign_staging, foreign)
    destination = tmp_path / "runtime/state/ci-portable"
    original = firewall.validate_published_evidence
    swapped = False

    def swap_then_validate(path: Path, **kwargs: object) -> dict[str, object]:
        nonlocal swapped
        if path == destination and not swapped:
            swapped = True
            destination.chmod(0o700)
            foreign.chmod(0o700)
            displaced = tmp_path / "displaced-final-root"
            os.rename(destination, displaced)
            displaced.chmod(0o500)
            os.rename(foreign, destination)
            destination.chmod(0o500)
        return original(path, **kwargs)

    monkeypatch.setattr(firewall, "validate_published_evidence", swap_then_validate)
    with pytest.raises(firewall.FirewallError, match="identity|changed"):
        _publish(staging, destination)
    assert swapped


def test_snapshot_closes_child_descriptor_when_recursive_registration_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = _staging(tmp_path)
    original_open = os.open
    original_close = os.close
    original_listdir = os.listdir
    target_descriptor: int | None = None
    closed: set[int] = set()

    def observe_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal target_descriptor
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == "test-governance" and flags & firewall._DIRECTORY:
            target_descriptor = descriptor
            closed.discard(descriptor)
        return descriptor

    def fail_child_traversal(path: object) -> list[str]:
        if path == target_descriptor:
            raise OSError("forced traversal failure")
        return original_listdir(path)

    def observe_close(descriptor: int) -> None:
        closed.add(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(firewall.os, "open", observe_open)
    monkeypatch.setattr(firewall.os, "listdir", fail_child_traversal)
    monkeypatch.setattr(firewall.os, "close", observe_close)
    try:
        with pytest.raises(firewall.FirewallError):
            with firewall._snapshot_tree(
                staging, directory_mode=0o700, file_mode=0o600,
            ):
                pass
        assert target_descriptor is not None
        assert target_descriptor in closed
    finally:
        if target_descriptor is not None and target_descriptor not in closed:
            original_close(target_descriptor)


def test_ambiguous_success_accepts_only_exact_identity_and_never_unlinks_foreign_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = _staging(tmp_path)
    destination = tmp_path / "runtime/state/ci-portable"
    original = firewall._renameat2_noreplace

    def rename_then_raise(*args: object) -> None:
        original(*args)
        raise OSError(5, "ambiguous fixture")

    monkeypatch.setattr(firewall, "_renameat2_noreplace", rename_then_raise)
    _publish(staging, destination)
    firewall.validate_published_evidence(destination)

    second = _staging(tmp_path / "second")
    foreign_destination = tmp_path / "foreign/state/ci-portable"

    def install_foreign_then_raise(
        _old_fd: int, _old_name: str, new_fd: int, new_name: str,
    ) -> None:
        os.mkdir(new_name, mode=0o700, dir_fd=new_fd)
        raise OSError(5, "ambiguous foreign fixture")

    monkeypatch.setattr(firewall, "_renameat2_noreplace", install_foreign_then_raise)
    with pytest.raises(firewall.FirewallError, match="unresolved"):
        _publish(second, foreign_destination)
    assert foreign_destination.is_dir()


def test_validator_rejects_partial_manifest_checksum_mismatch_and_duplicates(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    destination = tmp_path / "runtime/state/ci-portable"
    _publish(staging, destination)

    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = manifest["files"][:-1]
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(_canonical(manifest))
    manifest_path.chmod(0o400)
    with pytest.raises(firewall.FirewallError):
        firewall.validate_published_evidence(destination)

    other = tmp_path / "other"
    other_staging = _staging(other)
    other_destination = other / "runtime/state/ci-portable"
    _publish(other_staging, other_destination)
    checksums = other_destination / "SHA256SUMS"
    lines = checksums.read_bytes().splitlines()
    checksums.chmod(0o600)
    checksums.write_bytes(b"\n".join([lines[0], lines[0], *lines[1:]]) + b"\n")
    checksums.chmod(0o400)
    with pytest.raises(firewall.FirewallError, match="checksum"):
        firewall.validate_published_evidence(other_destination)


def test_binding_validator_rejects_stale_head_tree_inventory_context_and_policy(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    destination = tmp_path / "runtime/state/ci-portable"
    _publish(staging, destination)
    expected = _semantic()
    firewall.validate_published_evidence(
        destination,
        expected_head_sha=HEAD,
        expected_source_tree_sha256=TREE,
        expected_semantic_projection=expected,
    )
    for kwargs in (
        {"expected_head_sha": "8" * 40},
        {"expected_source_tree_sha256": "7" * 64},
        {"expected_semantic_projection": _semantic(inventory_sha256="6" * 64)},
        {"expected_semantic_projection": _semantic(foundation={"head_sha": HEAD, "validation_date": "2026-08-14"})},
        {"expected_semantic_projection": _semantic(policy_sha256="5" * 64)},
    ):
        with pytest.raises(firewall.FirewallError, match="binding"):
            firewall.validate_published_evidence(destination, **kwargs)


def test_validator_rejects_self_consistent_but_nonallowlisted_published_layout(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    destination = tmp_path / "runtime/state/ci-portable"
    _publish(staging, destination)
    destination.chmod(0o700)
    extra = destination / "unexpected.json"
    extra.write_bytes(_canonical({"status": "PASS"}))
    extra.chmod(0o400)
    _rehash_published_tree(destination)
    destination.chmod(0o500)

    with pytest.raises(firewall.FirewallError, match="projection root"):
        firewall.validate_published_evidence(destination)


@pytest.mark.parametrize(
    "mutation",
    ["missing-fixed", "fake-context", "wrong-code", "bundle-outcome"],
)
def test_validator_rejects_rehashed_topology_schema_and_inventory_forgery(
    tmp_path: Path, mutation: str,
) -> None:
    case = tmp_path / mutation
    destination = case / "runtime/state/ci-portable"
    _publish(_staging(case), destination)
    topology_root = destination / "capability-topology"
    if mutation == "missing-fixed":
        topology_root.chmod(0o700)
        (topology_root / ".reservation").unlink()
        topology_root.chmod(0o500)
    elif mutation == "fake-context":
        path = topology_root / "foundation-context.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["schema_version"] = "fake-foundation-context/v99"
        document["foundation_context_sha256"] = topology._sha256({
            key: value for key, value in document.items()
            if key != "foundation_context_sha256"
        })
        path.chmod(0o600)
        path.write_bytes(_canonical(document))
        path.chmod(0o400)
    elif mutation == "wrong-code":
        marker = topology_root / "NATIVE-BWRAP-OS-SANDBOX.json"
        receipt = json.loads(marker.read_text(encoding="utf-8"))
        receipt["capability_or_authority_code"] = "NATIVE-USERNS-ROOT-PROVISION"
        receipt["completeness_sha256"] = topology.native_completeness_sha256(receipt)
        receipt["receipt_sha256"] = topology.payload_sha256(receipt)
        for path in (
            marker,
            topology_root / "NATIVE-BWRAP-OS-SANDBOX.artifacts/receipt.json",
        ):
            path.chmod(0o600)
            path.write_bytes(_canonical(receipt))
            path.chmod(0o400)
    else:
        path = topology_root / "NATIVE-BWRAP-OS-SANDBOX.artifacts/manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["outcome"] = "PASS"
        manifest["manifest_sha256"] = topology._sha256({
            key: value for key, value in manifest.items()
            if key != "manifest_sha256"
        })
        path.chmod(0o600)
        path.write_bytes(_canonical(manifest))
        path.chmod(0o400)
    _rehash_published_tree(destination)
    with pytest.raises(firewall.FirewallError):
        firewall.validate_published_evidence(destination)
