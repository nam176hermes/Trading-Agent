from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import qualify_p1_nautilus_paper as qualification


SAFE = {
    "live_authorized": False,
    "network_trading_authorized": False,
    "production_authorized": False,
}


def test_paper_qualification_binds_exact_p1_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        qualification,
        "_run_suite",
        lambda suite, environment: {
            "duration_milliseconds": 1,
            "evidence_sha256": suite["id"][0] * 64,
            "source_sha256": suite["id"][-1] * 64,
            "test_counts": {"errors": 0, "failures": 0, "skipped": 0, "tests": 2},
        },
    )

    receipt = qualification.qualify(
        environment={
            "P1_NAUTILUS_CLOSURE_MANIFEST": "/sealed/closure.json",
            "P1_NAUTILUS_PRODUCT_LINEAGE": "/sealed/lineage.json",
            "P1_NAUTILUS_PYTHON": "/sealed/python",
        },
        source_identity=("a" * 40, "b" * 40),
    )

    assert receipt["schema"] == "trading-agent-p1-paper-qualification/v1"
    assert receipt["status"] == "P1_LOCAL_SOURCE_CERTIFIED"
    assert receipt["verdict"] == "PASS"
    assert receipt["authority_limits"] == SAFE
    assert receipt["engine_version"] == "1.231.0"
    assert receipt["paper_protocol"] == "nautilus-paper-session-v2"
    assert receipt["p1_product_closure_schema"] == 8
    assert receipt["p1_product_closure_sha256"] == (
        "97185d4c0b6090353ba51c1aab25ed4ea4dfab08113b655fac623af9e7db2b80"
    )
    assert receipt["p1_29_source_commit"] == qualification.P1_29_COMMIT
    assert receipt["p1_29_source_tree"] == qualification.P1_29_TREE
    assert receipt["test_count"] == 4
    assert receipt["skipped_count"] == 0


def test_paper_qualification_rejects_skipped_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        qualification,
        "_run_suite",
        lambda _suite, _environment: {
            "duration_milliseconds": 1,
            "evidence_sha256": "a" * 64,
            "source_sha256": "b" * 64,
            "test_counts": {"errors": 0, "failures": 0, "skipped": 1, "tests": 2},
        },
    )

    with pytest.raises(qualification.QualificationError, match="pass exactly"):
        qualification.qualify(
            environment={
                "P1_NAUTILUS_CLOSURE_MANIFEST": "/sealed/closure.json",
                "P1_NAUTILUS_PRODUCT_LINEAGE": "/sealed/lineage.json",
                "P1_NAUTILUS_PYTHON": "/sealed/python",
            },
            source_identity=("a" * 40, "b" * 40),
        )


def test_cli_defers_only_when_all_native_authority_is_absent() -> None:
    command = (sys.executable, str(Path(qualification.__file__)))
    absent = subprocess.run(
        command,
        cwd=qualification.ROOT,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert absent.returncode == 0
    assert json.loads(absent.stdout) == {
        "authority_limits": SAFE,
        "schema": "trading-agent-p1-paper-qualification/v1",
        "verdict": "DEFERRED",
    }

    partial = subprocess.run(
        command,
        cwd=qualification.ROOT,
        env={
            "P1_NAUTILUS_PYTHON": "/private/missing/python",
            "PATH": "/usr/bin:/bin",
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert partial.returncode == 2
    assert "partial" in partial.stderr
