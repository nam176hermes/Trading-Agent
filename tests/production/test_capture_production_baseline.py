from __future__ import annotations

import inspect
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import capture_production_baseline


ROOT = Path(__file__).resolve().parents[2]
BASELINE_HEAD = "e304d83da260d11120ac648d67882359645c68a5"
BASELINE_TREE = "bf4d1fb20944670df8110fc7eee3dbe3bc390b55"
OTHER_HEAD = "0" * 40
OTHER_TREE = "f" * 40
HISTORICAL_SOURCE_ROOT = "/home/thenam176/projects/trading-agent"


def _stub_git_identity(
    monkeypatch: pytest.MonkeyPatch,
    observed_head: str = BASELINE_HEAD,
    observed_tree: str = BASELINE_TREE,
) -> None:
    def run(arguments: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert arguments in (
            ["git", "rev-parse", "HEAD"],
            ["git", "rev-parse", "HEAD^{tree}"],
        )
        assert options == {
            "cwd": ROOT,
            "check": True,
            "capture_output": True,
            "text": True,
        }
        observed = observed_head if arguments[-1] == "HEAD" else observed_tree
        return subprocess.CompletedProcess(
            arguments,
            returncode=0,
            stdout=f"{observed}\n",
            stderr="",
        )

    monkeypatch.setattr(capture_production_baseline.subprocess, "run", run)


def _build_baseline(
    *,
    requested_mode: str = "paper",
    effective_mode: str = "paper",
    live_execution_enabled: bool = False,
    live_trading_approved: bool = False,
) -> dict[str, object]:
    return capture_production_baseline.build_baseline(
        repo_root=ROOT,
        head=BASELINE_HEAD,
        tree=BASELINE_TREE,
        requested_mode=requested_mode,
        effective_mode=effective_mode,
        live_execution_enabled=live_execution_enabled,
        live_trading_approved=live_trading_approved,
    )


def _expected_baseline(*, source_root: str | None = None) -> dict[str, object]:
    return {
        "schema_version": 2,
        "source": {
            "binding": "HISTORICAL_BASELINE",
            "root": source_root or str(ROOT),
            "commit": BASELINE_HEAD,
            "tree": BASELINE_TREE,
        },
        "safety": {
            "requested_mode": "paper",
            "effective_mode": "paper",
            "live_execution_enabled": False,
            "live_trading_approved": False,
        },
        "decision": "NO_GO",
        "completed_gates": [],
        "deployment_evidence": {
            "state": "UNAVAILABLE",
            "schema_path": "ops/evidence/source-release-unit-pid.schema.json",
            "path": None,
            "sha256": None,
        },
    }


def test_baseline_is_exactly_bound_to_source_and_paper_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_git_identity(monkeypatch)

    assert _build_baseline() == _expected_baseline()
    assert _build_baseline()["decision"] == "NO_GO"


def test_baseline_capture_has_no_go_override_surface() -> None:
    assert "decision" not in inspect.signature(
        capture_production_baseline.build_baseline
    ).parameters


def test_baseline_rejects_source_head_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_git_identity(monkeypatch, observed_head=OTHER_HEAD)

    with pytest.raises(ValueError, match="source head changed during baseline capture"):
        _build_baseline()


def test_baseline_rejects_source_tree_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_git_identity(monkeypatch, observed_tree=OTHER_TREE)

    with pytest.raises(ValueError, match="source tree changed during baseline capture"):
        _build_baseline()


@pytest.mark.parametrize(
    (
        "requested_mode",
        "effective_mode",
        "live_execution_enabled",
        "live_trading_approved",
        "expected_error",
    ),
    (
        ("live", "paper", False, False, "promotion baseline must remain paper-only"),
        ("paper", "live", False, False, "promotion baseline must remain paper-only"),
        ("paper", "paper", True, False, "live gates must be false"),
        ("paper", "paper", False, True, "live gates must be false"),
    ),
)
def test_baseline_rejects_non_paper_modes_and_live_gates(
    monkeypatch: pytest.MonkeyPatch,
    requested_mode: str,
    effective_mode: str,
    live_execution_enabled: bool,
    live_trading_approved: bool,
    expected_error: str,
) -> None:
    _stub_git_identity(monkeypatch)

    with pytest.raises(ValueError, match=expected_error):
        _build_baseline(
            requested_mode=requested_mode,
            effective_mode=effective_mode,
            live_execution_enabled=live_execution_enabled,
            live_trading_approved=live_trading_approved,
        )


def test_main_writes_deterministic_full_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "evidence" / "promotion-status.json"
    arguments = SimpleNamespace(
        root=ROOT,
        requested_mode="paper",
        effective_mode="paper",
        live_execution_enabled=False,
        live_trading_approved=False,
        output=output,
    )
    _stub_git_identity(monkeypatch)
    monkeypatch.setattr(capture_production_baseline, "_arguments", lambda: arguments)

    capture_production_baseline.main()

    expected = _expected_baseline()
    assert output.read_text(encoding="utf-8") == (
        json.dumps(expected, indent=2, sort_keys=True) + "\n"
    )
    assert json.loads(output.read_text(encoding="utf-8")) == expected


def test_committed_status_is_the_historical_no_go_v2_record() -> None:
    status_path = ROOT / "docs/production/promotion-status.json"

    assert json.loads(status_path.read_text(encoding="utf-8")) == _expected_baseline(
        source_root=HISTORICAL_SOURCE_ROOT
    )


def test_readiness_doc_makes_pydantic_semantics_normative_and_non_authorizing() -> None:
    baseline = (
        ROOT / "docs/production/production-readiness-baseline.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(baseline.split())

    assert "schema-only validation is not authoritative" in normalized
    assert (
        "`packages.deployment_evidence.DeploymentEvidence` is the normative "
        "semantic validator"
    ) in normalized
    assert "Observational evidence does not authorize promotion" in normalized
