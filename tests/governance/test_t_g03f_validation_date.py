from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

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


@pytest.mark.parametrize(
    ("source_bytes", "tracked_bytes", "expected"),
    [
        (b'{"schema_version":1}', b'{"schema_version":2}', "POLICY_SOURCE_DRIFT"),
        (b"\xef\xbb\xbf{}", b"\xef\xbb\xbf{}", "POLICY_SOURCE_DRIFT"),
        (b"\xff", b"\xff", "POLICY_SOURCE_DRIFT"),
        (None, b"", "POLICY_SOURCE_DRIFT"),
    ],
    ids=("source-drift", "utf8-bom", "non-utf8", "unavailable-source"),
)
def test_policy_source_prerequisites_expose_only_closed_classes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_bytes: bytes | None,
    tracked_bytes: bytes,
    expected: str,
) -> None:
    """Break caught: policy source detail escapes before redaction or mints acceptance evidence."""
    source = tmp_path / "tests/skip-allowlist.yaml"
    source.parent.mkdir()
    if source_bytes is not None:
        source.write_bytes(source_bytes)
    monkeypatch.setattr(topology, "ROOT", tmp_path)
    monkeypatch.setattr(
        topology.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=tracked_bytes),
    )

    with pytest.raises(topology.TopologyError) as raised:
        topology._validated_policy_snapshot("a" * 40, datetime(2026, 10, 31).date())

    assert str(raised.value) == f"policy validation failed: {expected}"
    assert "allowlist" not in str(raised.value).lower()
    assert not list(tmp_path.rglob("*.governance.json"))
    assert not list(tmp_path.rglob("*.failure-diagnostic.json"))
    assert not list(tmp_path.rglob("SRC-*.json"))


def test_topology_cli_prints_only_the_closed_policy_class(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Break caught: a source prerequisite error leaks raw detail through the topology CLI."""
    monkeypatch.setattr(topology, "_active_foundation_identity", lambda: ("31641536482", "a" * 40))
    monkeypatch.setattr(topology, "load_foundation_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        topology,
        "_allowlist_bytes_at_head",
        lambda _head: (_ for _ in ()).throw(topology.TopologyError("allowlist source drifted /private/token")),
    )
    monkeypatch.setattr(
        topology,
        "reconcile_portable_root_accounting",
        lambda **_kwargs: topology._validated_policy_snapshot("a" * 40, datetime(2026, 10, 31).date()),
    )

    assert topology.main([
        "aggregate", "--evidence-root", "/tmp/evidence",
        "--foundation-context-path", "/tmp/foundation-context.json",
    ]) == 2
    assert capsys.readouterr().err == (
        "t-g03 capability topology: policy validation failed: POLICY_SOURCE_DRIFT\n"
    )
