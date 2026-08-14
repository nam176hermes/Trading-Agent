from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import check_artifact_firewall as firewall
from scripts import t_g03_capability_topology as topology


INVENTORY = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
RAW_REASON = "fixture reason"
TREE = "a" * 64


def _failure_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, str, str]:
    raw_root = tmp_path / "raw"
    run_id = "31827924223"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    custody = tmp_path / "custody.so"
    custody.write_bytes(b"P0-12R8 retained custody fixture")
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(custody))
    monkeypatch.setenv(
        "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
        hashlib.sha256(custody.read_bytes()).hexdigest(),
    )
    context_path = topology._capture_foundation_context(
        raw_root, clock=lambda: datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    inventory = topology.load_inventory(INVENTORY)
    closure = topology.load_portable_defect_closure(head_sha=head_sha)
    passed = "tests/ordinary/test_r8.py::test_passed"
    skipped = "tests/ordinary/test_r8.py::test_skipped"
    candidates = tuple(sorted(
        {row.node_id for row in inventory}
        | {row.node_id for row in closure}
        | {passed, skipped}
    ))
    topology.reserve_topology_evidence(
        raw_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=context_path,
    )
    topology.collect_portable_root_baseline(
        inventory=INVENTORY, evidence_root=raw_root, run_id=run_id,
        head_sha=head_sha, collector=lambda: candidates,
        foundation_context_path=context_path,
    )
    topology.prepare_portable_root_remainder(
        inventory=INVENTORY, evidence_root=raw_root, run_id=run_id,
        head_sha=head_sha, foundation_context_path=context_path,
    )

    def run_exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
        report.write_text(json.dumps({
            "schema_version": 1,
            "component": "root",
            "pytest_exit_status": 0,
            "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
            "tests": [
                {
                    "test_node_id": node,
                    "component": "root",
                    "outcome": "skipped" if node == skipped else "passed",
                    "reason": RAW_REASON if node == skipped else "",
                    "phase": "call",
                }
                for node in nodes
            ],
        }), encoding="utf-8")
        return nodes

    with pytest.raises(topology.TopologyError, match="^EXACT_EXECUTION_NONPASS$"):
        topology.execute_portable_root_remainder(
            inventory=INVENTORY, evidence_root=raw_root, run_id=run_id,
            head_sha=head_sha, exact_runner=run_exact,
            foundation_context_path=context_path,
        )
    return raw_root, run_id, head_sha, skipped


def _publish_failure(
    raw_root: Path, destination: Path, monkeypatch: pytest.MonkeyPatch,
    **kwargs: object,
) -> dict[str, object]:
    monkeypatch.setattr(firewall, "_source_tree_identity", lambda _root, _head: TREE)
    return firewall.publish_root_remainder_failure(
        raw_root=raw_root,
        destination=destination,
        inventory=INVENTORY,
        foundation_context_path=raw_root / "capability-topology/foundation-context.json",
        repository_root=Path.cwd(),
        **kwargs,
    )


def test_failure_publisher_emits_only_a_sealed_diagnostic_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root, run_id, head_sha, skipped = _failure_source(tmp_path, monkeypatch)
    destination = tmp_path / "runtime/state/ci-portable"
    manifest = _publish_failure(raw_root, destination, monkeypatch)

    assert manifest["schema_version"] == "portable-ci-failure-evidence-manifest/v1"
    assert manifest["head_sha"] == head_sha
    assert manifest["run_metadata"]["run_id"] == run_id
    assert set(path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()) == {
        "SHA256SUMS", "manifest.json", "root-remainder-failure.json",
    }
    projection_raw = (destination / "root-remainder-failure.json").read_bytes()
    projection = json.loads(projection_raw)
    assert projection["schema_version"] == "portable-ci-root-remainder-failure/v1"
    assert projection["diagnostic_only"] is True
    assert projection["failure_class"] == "EXACT_EXECUTION_NONPASS"
    assert projection["remainder_node_count"] == 2
    assert projection["passed_count"] == 1
    assert projection["skipped_count"] == 1
    assert [item["test_node_id"] for item in projection["skipped_observations"]] == [skipped]
    assert RAW_REASON.encode() not in projection_raw
    assert b'"status":"PASS"' not in projection_raw
    assert b'"outcome":"PASS"' not in projection_raw
    assert not (destination / "capability-topology/aggregate.json").exists()
    assert not (destination / "test-governance").exists()
    assert (destination.stat().st_mode & 0o777) == 0o500
    assert all((path.stat().st_mode & 0o777) == 0o400 for path in destination.iterdir())


@pytest.mark.parametrize("mutation", ["malformed", "stale", "foreign"])
def test_failure_publisher_rejects_malformed_stale_and_foreign_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    diagnostic = raw_root / "capability-topology/portable-root-remainder.failure-diagnostic.json"
    if mutation == "malformed":
        diagnostic.write_bytes(diagnostic.read_bytes() + b"\n")
    else:
        document = json.loads(diagnostic.read_bytes())
        if mutation == "stale":
            document["foundation_run_id"] = "31827924224"
        else:
            document["observations"][-1]["test_node_id"] = (
                "tests/ordinary/test_r8.py::test_foreign"
            )
        document["diagnostic_sha256"] = topology._sha256({
            key: value for key, value in document.items()
            if key != "diagnostic_sha256"
        })
        diagnostic.write_bytes(topology.canonical_json_bytes(document))
    destination = tmp_path / "final"
    with pytest.raises(firewall.FirewallError):
        _publish_failure(raw_root, destination, monkeypatch)
    assert not destination.exists()


@pytest.mark.parametrize("authority", ["context", "reservation", "baseline", "remainder"])
def test_failure_publisher_revalidates_every_partial_foundation_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authority: str,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    topology_root = raw_root / "capability-topology"
    names = {
        "context": "foundation-context.json",
        "reservation": ".reservation",
        "baseline": "portable-root-baseline.json",
        "remainder": "portable-root-remainder.json",
    }
    path = topology_root / names[authority]
    document = json.loads(path.read_bytes())
    if authority == "context":
        document["foundation_run_id"] = "31827924224"
        document["foundation_context_sha256"] = topology._sha256({
            key: value for key, value in document.items()
            if key != "foundation_context_sha256"
        })
    elif authority == "reservation":
        document["foundation_run_id"] = "31827924224"
    elif authority == "baseline":
        document["collector_policy"]["native_custody_extension_sha256"] = "f" * 64
        document["baseline_sha256"] = topology._baseline_payload_sha256(document)
    else:
        document["remainder_node_ids"] = list(reversed(document["remainder_node_ids"]))
        document["remainder_sha256"] = topology._remainder_payload_sha256(document)
    path.write_bytes(topology.canonical_json_bytes(document))
    destination = tmp_path / "final"
    with pytest.raises(firewall.FirewallError):
        _publish_failure(raw_root, destination, monkeypatch)
    assert not destination.exists()


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "mode", "owner"])
def test_failure_publisher_rejects_unsafe_source_leaf_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    diagnostic = raw_root / "capability-topology/portable-root-remainder.failure-diagnostic.json"
    if attack == "symlink":
        copy = tmp_path / "diagnostic-copy.json"
        copy.write_bytes(diagnostic.read_bytes())
        diagnostic.unlink()
        diagnostic.symlink_to(copy)
    elif attack == "hardlink":
        os.link(diagnostic, tmp_path / "diagnostic-hardlink.json")
    elif attack == "mode":
        diagnostic.chmod(0o640)
    else:
        monkeypatch.setattr(firewall.os, "geteuid", lambda: os.getuid() + 1)
    destination = tmp_path / "final"
    with pytest.raises(firewall.FirewallError):
        _publish_failure(raw_root, destination, monkeypatch)
    assert not destination.exists()


def test_failure_publisher_rejects_source_replacement_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    diagnostic = raw_root / "capability-topology/portable-root-remainder.failure-diagnostic.json"

    def replace(_boundary: str) -> None:
        replacement = tmp_path / "replacement.json"
        replacement.write_bytes(diagnostic.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, diagnostic)

    destination = tmp_path / "final"
    with pytest.raises(firewall.FirewallError):
        _publish_failure(
            raw_root, destination, monkeypatch, source_boundary_hook=replace,
        )
    assert not destination.exists()


@pytest.mark.parametrize(
    "relative",
    [
        "capability-topology/aggregate.json",
        "capability-topology/NATIVE-BWRAP-OS-SANDBOX.json",
        "capability-topology/policy-validation-nonacceptance.json",
        "capability-topology/unsafe-raw-reason-nonacceptance.json",
        "test-governance-topology/test-governance.json",
    ],
)
def test_failure_publisher_rejects_acceptance_or_later_stage_coexistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    path = raw_root / relative
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(b"{}")
    path.chmod(0o600)
    destination = tmp_path / "final"
    with pytest.raises(firewall.FirewallError):
        _publish_failure(raw_root, destination, monkeypatch)
    assert not destination.exists()


def test_failure_publisher_never_clobbers_an_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    destination = tmp_path / "runtime/state/ci-portable"
    destination.mkdir(parents=True, mode=0o700)
    sentinel = destination / "existing"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(firewall.FirewallError, match="destination already exists"):
        _publish_failure(raw_root, destination, monkeypatch)
    assert sentinel.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize(
    ("inner_status", "create_diagnostic", "expected_publish_count"),
    [(0, False, 0), (23, False, 0), (37, True, 1)],
)
def test_ci_portable_catch_is_single_shot_and_preserves_the_original_status(
    tmp_path: Path, inner_status: int, create_diagnostic: bool,
    expected_publish_count: int,
) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    invocation_log = tmp_path / "publish.log"
    fake_make = tmp_path / "fake-make"
    fake_make.write_text(
        "#!/bin/sh\n"
        + (
            'mkdir -p "$TEST_EVIDENCE_DIR/capability-topology"\n'
            'touch "$TEST_EVIDENCE_DIR/capability-topology/portable-root-remainder.failure-diagnostic.json"\n'
            if create_diagnostic else ""
        )
        + f"exit {inner_status}\n",
        encoding="utf-8",
    )
    fake_make.chmod(0o700)
    fake_uv = binary / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "case \" $* \" in\n"
        "  *\" python -c \"*)\n"
        "    for root do :; done\n"
        "    mkdir -p \"$root/capability-topology\"\n"
        "    context=\"$root/capability-topology/foundation-context.json\"\n"
        "    : > \"$context\"\n"
        "    echo \"$context\"\n"
        "    exit 0;;\n"
        "esac\n"
        "printf '%s\\n' \"$*\" >> \"$INVOCATION_LOG\"\n"
        "exit 91\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o700)
    result = subprocess.run(
        ["make", "--no-print-directory", "ci-portable", f"MAKE={fake_make}"],
        cwd=Path.cwd(), capture_output=True, text=True, check=False,
        env={
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "INVOCATION_LOG": str(invocation_log),
        },
    )
    published = [] if not invocation_log.exists() else invocation_log.read_text().splitlines()
    assert len(published) == expected_publish_count
    if inner_status == 0:
        assert result.returncode == 0
    else:
        assert result.returncode != 0
        assert f"Error {inner_status}" in result.stderr
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "original_status=$$?" in makefile
    assert 'exit "$$original_status"' in makefile
