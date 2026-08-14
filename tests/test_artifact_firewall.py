from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

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
    value: dict[str, object] = {
        "foundation": {"head_sha": HEAD, "validation_date": "2026-08-13"},
        "inventory_sha256": "b" * 64,
        "closure_sha256": "c" * 64,
        "policy_sha256": "d" * 64,
        "selected_tests": [
            {"node_id": "tests/test_safe.py::test_one", "outcome": "passed"},
        ],
        "statuses": {
            "portable_source": "PASS",
            "native_capabilities": "DEFERRED",
            "external_authorities": "DEFERRED",
        },
        "receipt_results": [
            {"code": "NATIVE-BWRAP-OS-SANDBOX", "outcome": "DEFERRED", "selected": 0, "passed": 0, "failed": 0, "unavailable": 8},
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


def _staging(tmp_path: Path) -> Path:
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
    _write_leaf(
        staging,
        "capability-topology/portable-root-candidates.txt",
        b"-----BEGIN PRIVATE KEY-----\nmaterial",
    )
    with pytest.raises(firewall.FirewallError) as caught:
        _publish(staging, tmp_path / "runtime/state/ci-portable")
    assert caught.value.category == "PRIVATE_KEY"


def test_publisher_creates_canonical_manifest_and_exact_checksums(tmp_path: Path) -> None:
    staging = _staging(tmp_path)
    destination = tmp_path / "runtime/state/ci-portable"
    manifest = _publish(staging, destination)

    assert manifest["semantic_result_sha256"] == firewall.semantic_result_sha256(_semantic())
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


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


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
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append({
        "path": "unexpected.json",
        "sha256": hashlib.sha256(extra.read_bytes()).hexdigest(),
        "size": len(extra.read_bytes()),
        "mode": "0400",
    })
    manifest["files"].sort(key=lambda item: item["path"])
    manifest["manifest_payload_sha256"] = firewall.manifest_payload_sha256(manifest)
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(_canonical(manifest))
    manifest_path.chmod(0o400)
    checksums = destination / "SHA256SUMS"
    checksums.chmod(0o600)
    leaves = sorted(
        path for path in destination.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    checksums.write_bytes(b"".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(destination).as_posix()}\n".encode()
        for path in leaves
    ))
    checksums.chmod(0o400)
    destination.chmod(0o500)

    with pytest.raises(firewall.FirewallError, match="projection root"):
        firewall.validate_published_evidence(destination)
