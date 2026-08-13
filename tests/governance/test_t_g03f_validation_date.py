from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

from scripts import t_g03_capability_topology as topology
from scripts import check_test_governance as governance


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv"
ALLOWLIST = ROOT / "tests/skip-allowlist.yaml"
RUN_ID = "31641536482"


def _governance_topology_command(
    *, evidence: Path, report_dir: Path, context_path: Path,
    allowlist: Path = ALLOWLIST,
) -> list[str]:
    return [
        "uv", "run", "python", "-m", "scripts.check_test_governance",
        "--allowlist", str(allowlist),
        "--topology-audit",
        "--report-dir", str(report_dir),
        "--topology-evidence-root", str(evidence),
        "--inventory", str(INVENTORY),
        "--foundation-context-path", str(context_path),
    ]


def _capture_valid_context(
    evidence: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str]:
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", RUN_ID)
    monkeypatch.delenv("FOUNDATION_VALIDATION_DATE", raising=False)
    context_path = topology._capture_foundation_context(
        evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    return context_path, head_sha


def _write_passing_exact_report(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
    report.write_text(
        topology.json.dumps(
            {
                "schema_version": 1,
                "component": "root",
                "pytest_exit_status": 0,
                "custody_policy": topology.json.loads(
                    os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]
                ),
                "tests": [
                    {
                        "test_node_id": node,
                        "component": "root",
                        "outcome": "passed",
                        "reason": "",
                        "phase": "call",
                    }
                    for node in nodes
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return nodes


def _write_component_process_fixtures(root: Path) -> Path:
    """Isolate the date-boundary subprocess from unrelated component test execution."""
    fake_bin = root / "bin"
    fake_bin.mkdir(mode=0o700)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"schema_version\":1,\"tests\":[]}' "
        "> \"$TEST_GOVERNANCE_REPORT\"\n",
        encoding="utf-8",
    )
    fake_node = fake_bin / "node"
    fake_node.write_text(
        "#!/bin/sh\n"
        "if test \"$1\" = tests/run-test-inventory.mjs; then\n"
        "  printf '%s\\n' "
        "'{\"schema_version\":1,\"node_tests\":[\"tests/api-access-boundary.test.mjs\"],"
        "\"integration_tests\":[\"tests/dashboard-security.integration.sh\"]}'\n"
        "else\n"
        "  printf '%s\\n' 'TAP version 13' '# Subtest: sealed date boundary' "
        "'ok 1 - sealed date boundary' '1..1'\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_bash = fake_bin / "bash"
    fake_bash.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'isolated integration: PASS'\n",
        encoding="utf-8",
    )
    for fixture in (fake_uv, fake_node, fake_bash):
        fixture.chmod(0o700)
    return fake_bin


@pytest.mark.parametrize(
    "module",
    ("scripts.t_g03_capability_topology", "scripts.check_test_governance"),
)
def test_t_g03_package_module_entrypoints_preserve_the_help_contract(module: str) -> None:
    """Break caught: a Make-routed T-G03 package module cannot import from the repository root."""
    completed = subprocess.run(
        ["uv", "run", "python", "-m", module, "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "ModuleNotFoundError" not in completed.stderr
    assert "SHARED_VALIDATOR_IMPORT" not in completed.stderr


@pytest.mark.parametrize(
    ("source", "expected_sources"),
    (
        (
            "cli",
            {
                "cli_today_present": True,
                "environment_override_present": False,
                "sealed_context_present": True,
                "sealed_context_valid": True,
            },
        ),
        (
            "environment",
            {
                "cli_today_present": False,
                "environment_override_present": True,
                "sealed_context_present": True,
                "sealed_context_valid": True,
            },
        ),
    ),
)
def test_topology_governance_subprocess_rejects_date_overrides_with_safe_diagnostics(
    source: str,
    expected_sources: dict[str, bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a date override lacks bounded provenance or leaks its injected value."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        evidence = root / "evidence"
        report_dir = root / "report"
        context_path, _head_sha = _capture_valid_context(evidence, monkeypatch)
        command = _governance_topology_command(
            evidence=evidence, report_dir=report_dir, context_path=context_path,
        )
        environment = os.environ.copy()
        injected_value = "P0_02_INJECTED_ENVIRONMENT_VALUE"
        environment["P0_02_UNRELATED_ENVIRONMENT_VALUE"] = injected_value
        if source == "cli":
            command.extend(("--today", "2026-08-13"))
        else:
            environment["FOUNDATION_VALIDATION_DATE"] = "2026-08-13"

        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        artifact_path = report_dir / "test-governance-error.json"
        artifact_bytes = artifact_path.read_bytes()
        artifact = topology.json.loads(artifact_bytes)
        assert completed.returncode == 1
        assert completed.stdout == ""
        assert completed.stderr == (
            "TEST_GOVERNANCE_ERROR: policy validation failed: "
            "POLICY_DATE_CONTEXT_MISMATCH\n"
        )
        assert artifact["error"] == (
            "policy validation failed: POLICY_DATE_CONTEXT_MISMATCH"
        )
        assert set(artifact) == {
            "schema_version",
            "status",
            "generated_at_utc",
            "error",
            "suite_exit_codes",
            "date_context_sources",
        }
        assert artifact["date_context_sources"] == expected_sources
        assert set(artifact["date_context_sources"]) == {
            "cli_today_present",
            "environment_override_present",
            "sealed_context_present",
            "sealed_context_valid",
        }
        assert all(type(value) is bool for value in artifact["date_context_sources"].values())
        assert injected_value not in completed.stdout
        assert injected_value not in completed.stderr
        assert injected_value.encode("utf-8") not in artifact_bytes


def test_topology_governance_date_diagnostic_does_not_treat_presence_as_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: sealed_context_valid is true for bytes that never passed v1 validation."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        evidence = root / "evidence"
        report_dir = root / "report"
        context_path = evidence / "capability-topology/foundation-context.json"
        context_path.parent.mkdir(parents=True, mode=0o700)
        context_path.write_bytes(b"{}")
        monkeypatch.setenv("GITHUB_RUN_ID", RUN_ID)
        monkeypatch.delenv("FOUNDATION_VALIDATION_DATE", raising=False)
        completed = subprocess.run(
            [
                *_governance_topology_command(
                    evidence=evidence,
                    report_dir=report_dir,
                    context_path=context_path,
                ),
                "--today", "2026-08-13",
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
        )

        artifact = topology.json.loads(
            (report_dir / "test-governance-error.json").read_text(encoding="utf-8")
        )
        assert completed.returncode == 1
        assert "POLICY_DATE_CONTEXT_MISMATCH" in completed.stderr
        assert artifact["date_context_sources"] == {
            "cli_today_present": True,
            "environment_override_present": False,
            "sealed_context_present": True,
            "sealed_context_valid": False,
        }


def test_topology_governance_production_subprocess_accepts_clean_sealed_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: the real governance module rejects its sole sealed validation-date authority."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        evidence = root / "evidence"
        report_dir = root / "report"
        context_path, head_sha = _capture_valid_context(evidence, monkeypatch)
        custody = root / "custody.so"
        custody.write_bytes(b"p0-02 subprocess custody fixture")
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(custody))
        monkeypatch.setenv(
            "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
            topology.hashlib.sha256(custody.read_bytes()).hexdigest(),
        )
        topology.reserve_topology_evidence(
            evidence,
            run_id=RUN_ID,
            head_sha=head_sha,
            foundation_context_path=context_path,
        )
        rows = topology.load_inventory(INVENTORY)
        candidates = tuple(sorted(
            [
                *(row.node_id for row in rows),
                (
                    "tests/governance/test_t_g03f_validation_date.py::"
                    "test_topology_governance_production_subprocess_accepts_clean_sealed_context"
                ),
            ]
        ))
        topology.collect_portable_root_baseline(
            inventory=INVENTORY,
            evidence_root=evidence,
            run_id=RUN_ID,
            head_sha=head_sha,
            collector=lambda: candidates,
            foundation_context_path=context_path,
        )
        topology.prepare_portable_root_remainder(
            inventory=INVENTORY,
            evidence_root=evidence,
            run_id=RUN_ID,
            head_sha=head_sha,
            foundation_context_path=context_path,
        )
        topology.execute_portable_root_remainder(
            inventory=INVENTORY,
            evidence_root=evidence,
            run_id=RUN_ID,
            head_sha=head_sha,
            exact_runner=_write_passing_exact_report,
            foundation_context_path=context_path,
        )
        topology.run_lane(
            lane="portable-source",
            inventory=INVENTORY,
            evidence_root=evidence,
            run_id=RUN_ID,
            head_sha=head_sha,
            exact_runner=_write_passing_exact_report,
            foundation_context_path=context_path,
        )
        monkeypatch.setattr(
            topology,
            "_native_preflight",
            lambda _code: ("UNAVAILABLE", "NATIVE_COMPONENT_ABSENT"),
        )
        topology.run_lane(
            lane="native-capabilities",
            inventory=INVENTORY,
            evidence_root=evidence,
            run_id=RUN_ID,
            head_sha=head_sha,
            foundation_context_path=context_path,
        )
        topology.run_lane(
            lane="external-authorities",
            inventory=INVENTORY,
            evidence_root=evidence,
            run_id=RUN_ID,
            head_sha=head_sha,
            external_preflight=lambda _code: ("ABSENT", "AUTHORITY_ROOT_ABSENT"),
            foundation_context_path=context_path,
        )
        environment = os.environ.copy()
        environment.pop("FOUNDATION_VALIDATION_DATE", None)
        environment.pop("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", None)
        environment.pop("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256", None)
        fake_bin = _write_component_process_fixtures(root)
        environment["PATH"] = os.pathsep.join((str(fake_bin), "/usr/bin", "/bin"))
        allowlist = root / "allowlist.json"
        allowlist.write_text(
            '{"schema_version":1,"entries":[]}', encoding="utf-8",
        )
        command = _governance_topology_command(
            evidence=evidence,
            report_dir=report_dir,
            context_path=context_path,
            allowlist=allowlist,
        )
        uv = shutil.which("uv", path=os.environ.get("PATH"))
        assert uv is not None
        command[0] = uv
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr
        assert "POLICY_DATE_CONTEXT_MISMATCH" not in completed.stderr
        report = topology.json.loads(
            (report_dir / "test-governance.json").read_text(encoding="utf-8")
        )
        assert report["status"] == "pass"
        assert report["capability_topology"] == {
            "portable_source_status": "PASS",
            "native_capabilities_status": "DEFERRED",
            "external_authorities_status": "DEFERRED",
            "runtime_proof": "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS",
        }
        assert not (report_dir / "test-governance-error.json").exists()


def test_t_g03_topology_module_reaches_policy_validation_before_absent_custody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: module entrypoint fails at its reciprocal import instead of the sealed custody boundary."""
    run_id = "31641536482"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        custody = Path(raw) / "custody.so"
        custody.write_bytes(b"module probe custody fixture")
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(custody))
        monkeypatch.setenv(
            "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
            topology.hashlib.sha256(custody.read_bytes()).hexdigest(),
        )
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        rows = topology.load_inventory(INVENTORY)
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head_sha,
            foundation_context_path=context_path,
        )
        topology.collect_portable_root_baseline(
            inventory=INVENTORY,
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
            foundation_context_path=context_path,
            collector=lambda: tuple(sorted([
                *(row.node_id for row in rows),
                "tests/ordinary/test_module_probe.py::test_unreachable_runner",
            ])),
        )
        topology.prepare_portable_root_remainder(
            inventory=INVENTORY,
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
            foundation_context_path=context_path,
        )

        environment = os.environ.copy()
        environment.pop("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", None)
        environment.pop("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256", None)
        completed = subprocess.run(
            [
                "uv", "run", "python", "-m", "scripts.t_g03_capability_topology",
                "run-remainder",
                "--evidence-root", str(evidence),
                "--foundation-context-path", str(context_path),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        topology_root = evidence / "capability-topology"
        assert completed.returncode == 2
        assert completed.stdout == ""
        assert completed.stderr == (
            "t-g03 capability topology: portable root collection requires native custody identity\n"
        )
        assert "SHARED_VALIDATOR_IMPORT" not in completed.stderr
        assert not (topology_root / "portable-root-remainder.governance.json").exists()
        assert not (topology_root / "portable-root-remainder.failure-diagnostic.json").exists()
        assert not (topology_root / "policy-validation-nonacceptance.json").exists()
        assert not any((topology_root / f"{code}.json").exists() for code in topology.CODE_CLASSIFICATION)


def test_t_g03_topology_module_reaches_the_shared_validator_before_custody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: module launch no longer reaches the shared policy validator before any runner or custody use."""
    run_id = "31641536482"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        custody = Path(raw) / "custody.so"
        custody.write_bytes(b"module validator probe custody fixture")
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(custody))
        monkeypatch.setenv(
            "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
            topology.hashlib.sha256(custody.read_bytes()).hexdigest(),
        )
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        rows = topology.load_inventory(INVENTORY)
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head_sha,
            foundation_context_path=context_path,
        )
        topology.collect_portable_root_baseline(
            inventory=INVENTORY,
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
            foundation_context_path=context_path,
            collector=lambda: tuple(sorted([
                *(row.node_id for row in rows),
                "tests/ordinary/test_module_validator_probe.py::test_unreachable_runner",
            ])),
        )
        topology.prepare_portable_root_remainder(
            inventory=INVENTORY,
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
            foundation_context_path=context_path,
        )

        environment = os.environ.copy()
        environment.pop("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", None)
        environment.pop("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256", None)
        completed = subprocess.run(
            [
                "uv", "run", "python", "-m", "scripts.t_g03_capability_topology",
                "run-remainder",
                "--evidence-root", str(evidence),
                "--foundation-context-path", str(context_path),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        topology_root = evidence / "capability-topology"
        assert completed.returncode == 2
        assert completed.stdout == ""
        assert completed.stderr == (
            "t-g03 capability topology: policy validation failed: POLICY_REVIEW_DATE_EXPIRED\n"
        )
        nonacceptance = topology.parse_policy_validation_nonacceptance(
            (topology_root / "policy-validation-nonacceptance.json").read_bytes(),
        )
        assert nonacceptance["policy_validation_stage"] == "SHARED_ALLOWLIST_VALIDATION"
        assert nonacceptance["policy_validation_class"] == "POLICY_REVIEW_DATE_EXPIRED"
        assert not (topology_root / "portable-root-remainder.governance.json").exists()
        assert not (topology_root / "portable-root-remainder.failure-diagnostic.json").exists()
        assert not any((topology_root / f"{code}.json").exists() for code in topology.CODE_CLASSIFICATION)


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


def test_ci_portable_passes_its_resolved_evidence_root_to_capture() -> None:
    """Break caught: Make defaults are not necessarily exported to inline Python."""
    environment = os.environ.copy()
    environment.pop("TEST_EVIDENCE_DIR", None)
    environment["GITHUB_RUN_ID"] = "31668147300"
    makefile = Path("Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("ci-portable:\n", 1)[1].split("\nci-portable-private:", 1)[0]
    capture_code = recipe.split("foundation_context_path=$$(uv run python -c '", 1)[1].split(
        "' \"$(TEST_EVIDENCE_DIR)\"); \\",
        1,
    )[0]

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        captured = subprocess.run(
            ["uv", "run", "python", "-c", capture_code, str(Path(raw) / "evidence")],
            capture_output=True,
            text=True,
            check=True,
            env=environment,
        )

        raw_path = Path(raw)
        capture_arguments = raw_path / "capture-arguments"
        fake_bin = raw_path / "bin"
        fake_bin.mkdir()
        fake_uv = fake_bin / "uv"
        fake_uv.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE_ARGUMENTS\"\n",
            encoding="utf-8",
        )
        fake_uv.chmod(0o755)
        seam_makefile = raw_path / "Makefile"
        seam_makefile.write_text(
            "TEST_EVIDENCE_DIR ?= /tmp/trading-agent-test-evidence\n"
            "capture:\n"
            f"\t@uv run python -c '{capture_code}' \"$(TEST_EVIDENCE_DIR)\"\n",
            encoding="utf-8",
        )
        seam_environment = environment | {
            "CAPTURE_ARGUMENTS": str(capture_arguments),
            "PATH": f"{fake_bin}:{environment['PATH']}",
        }
        subprocess.run(
            ["make", "--no-print-directory", "-f", str(seam_makefile), "capture"],
            check=True,
            env=seam_environment,
        )
        assert capture_arguments.read_text(encoding="utf-8").splitlines()[-1] == (
            "/tmp/trading-agent-test-evidence"
        )

        override = "/tmp/t-g03f evidence;not-a-command"
        subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-f",
                str(seam_makefile),
                "capture",
                f"TEST_EVIDENCE_DIR={override}",
            ],
            check=True,
            env=seam_environment,
        )
        assert capture_arguments.read_text(encoding="utf-8").splitlines()[-1] == override

    assert 'Path(sys.argv[1])' in capture_code
    assert 'Path(os.environ["TEST_EVIDENCE_DIR"])' not in capture_code
    assert 'TEST_EVIDENCE_DIR ?= /tmp/trading-agent-test-evidence' in makefile
    assert '"$(TEST_EVIDENCE_DIR)"' in recipe
    assert Path(captured.stdout.strip()).name == "foundation-context.json"


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
