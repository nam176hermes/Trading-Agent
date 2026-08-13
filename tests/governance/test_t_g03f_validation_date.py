from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile

import pytest

from scripts import t_g03_capability_topology as topology
from scripts import check_test_governance as governance


def test_foundation_context_seals_the_capture_date_and_rejects_date_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a later wall clock or caller environment can change topology policy validation."""
    run_id = "31641536482"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        context_path = topology._capture_foundation_context(
            Path(raw),
            clock=lambda: datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc),
        )

        context = topology.load_foundation_context(
            context_path, run_id=run_id, head_sha=head_sha,
        )
        assert context["foundation_validation_date"] == "2026-10-31"
        assert topology.parse_foundation_validation_date(
            context["foundation_validation_date"],
        ).isoformat() == "2026-10-31"

        monkeypatch.setenv("FOUNDATION_VALIDATION_DATE", "2099-01-01")
        with pytest.raises(topology.TopologyError, match="validation date environment"):
            topology.load_foundation_context(context_path, run_id=run_id, head_sha=head_sha)


def test_ci_portable_captures_context_before_its_private_wrapper() -> None:
    """Break caught: the source route reads a clock after allocating its private wrapper."""
    makefile = Path("Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("ci-portable:\n", 1)[1].split("\nci-portable-private:", 1)[0]

    assert "_capture_foundation_context" in recipe
    assert recipe.index("_capture_foundation_context") < recipe.index("mktemp -d")
    assert "FOUNDATION_VALIDATION_DATE" not in recipe


def test_sealed_date_controls_policy_validation_and_binds_the_v3_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a late wall-clock read or a changed date can accept a baseline."""
    run_id = "31641536482"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 10, 31, tzinfo=timezone.utc),
        )
        extension = Path(raw) / "custody.so"
        extension.write_bytes(b"context-bound custody")
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
        monkeypatch.setenv(
            "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
            topology.hashlib.sha256(extension.read_bytes()).hexdigest(),
        )
        inventory = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
        rows = topology.load_inventory(inventory)
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head_sha,
            foundation_context_path=context_path,
        )
        baseline = topology.collect_portable_root_baseline(
            inventory=inventory, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
            foundation_context_path=context_path,
            collector=lambda: tuple(sorted(row.node_id for row in rows)),
        )

        assert baseline["schema_version"] == "t-g03a-portable-root-baseline/v3"
        assert baseline["foundation_validation_date"] == "2026-10-31"
        assert baseline["foundation_context_sha256"] == topology.load_foundation_context(
            context_path, run_id=run_id, head_sha=head_sha,
        )["foundation_context_sha256"]
        topology._validated_policy_snapshot(head_sha, datetime(2026, 10, 31).date())
        with pytest.raises(topology.TopologyError, match="POLICY_REVIEW_DATE_EXPIRED"):
            topology._validated_policy_snapshot(head_sha, datetime(2026, 11, 1).date())

        baseline_path = evidence / "capability-topology/portable-root-baseline.json"
        forged = topology.json.loads(baseline_path.read_text(encoding="utf-8"))
        forged["foundation_validation_date"] = "2026-11-01"
        forged["baseline_sha256"] = topology._baseline_payload_sha256(forged)
        baseline_path.write_bytes(topology.canonical_json_bytes(forged))
        with pytest.raises(topology.TopologyError, match="binding drift"):
            topology.load_portable_root_baseline(
                inventory=inventory, evidence_root=evidence, run_id=run_id,
                head_sha=head_sha, foundation_context_path=context_path,
            )


def test_topology_governance_rejects_a_cli_today_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: topology governance accepts a caller-selected historical policy date."""
    monkeypatch.setenv("GITHUB_RUN_ID", "31641536482")
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        context_path = topology._capture_foundation_context(
            Path(raw), clock=lambda: datetime(2026, 10, 31, tzinfo=timezone.utc),
        )
        assert governance.main([
            "--topology-audit", "--today", "2026-10-31",
            "--foundation-context-path", str(context_path),
        ]) == 1


def test_expired_sealed_date_publishes_no_acceptance_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: an expired context writes a snapshot, diagnostic, or PASS evidence."""
    run_id = "31641536482"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        extension = Path(raw) / "custody.so"
        extension.write_bytes(b"expired context custody")
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
        monkeypatch.setenv(
            "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
            topology.hashlib.sha256(extension.read_bytes()).hexdigest(),
        )
        inventory = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
        rows = topology.load_inventory(inventory)
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head_sha,
            foundation_context_path=context_path,
        )
        topology.collect_portable_root_baseline(
            inventory=inventory, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
            foundation_context_path=context_path,
            collector=lambda: tuple(sorted(row.node_id for row in rows)),
        )
        topology.prepare_portable_root_remainder(
            inventory=inventory, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
            foundation_context_path=context_path,
        )

        with pytest.raises(topology.TopologyError, match="POLICY_REVIEW_DATE_EXPIRED"):
            topology.execute_portable_root_remainder(
                inventory=inventory, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
                foundation_context_path=context_path,
            )
        topology_root = evidence / "capability-topology"
        assert not (topology_root / "portable-root-remainder.governance.json").exists()
        assert not (topology_root / "portable-root-remainder.failure-diagnostic.json").exists()
