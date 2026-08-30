from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts import qualify_p1_nautilus as qualification
from scripts.qualify_p1_nautilus import QualificationError, load_matrix, qualify


def _write_matrix(root: Path, *, nodeid: str, scenario: str = "parser") -> Path:
    matrix = root / "matrix.json"
    matrix.write_text(
        json.dumps(
            {
                "ceilings": {
                    "case_output_bytes": 100_000,
                    "case_peak_memory_kib": 1_000_000,
                    "case_runtime_seconds": 30,
                    "total_runtime_seconds": 60,
                },
                "scenarios": [
                    {
                        "boundary": "INPUT_PROTOCOL",
                        "expected_class": "EXPECTED_REJECTION_ASSERTED",
                        "nodeids": [nodeid],
                        "scenario": scenario,
                    }
                ],
                "schema": "trading-agent-p1-adversarial-matrix/v1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return matrix


def test_matrix_is_closed_and_rejects_duplicate_or_skip_authority(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "tests/test_case.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    matrix = _write_matrix(tmp_path, nodeid="tests/test_case.py::test_ok")
    assert load_matrix(matrix, root=tmp_path)["scenarios"][0]["scenario"] == "parser"

    document = json.loads(matrix.read_bytes())
    document["scenarios"].append(document["scenarios"][0])
    matrix.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(QualificationError, match="duplicated"):
        load_matrix(matrix, root=tmp_path)

    document["scenarios"] = [
        {**document["scenarios"][0], "scenario": "skip-parser"}
    ]
    matrix.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(QualificationError, match="skip"):
        load_matrix(matrix, root=tmp_path)


def test_runner_records_exact_pass_evidence_and_fails_on_skip(tmp_path: Path) -> None:
    test_file = tmp_path / "tests/test_case.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    matrix = _write_matrix(tmp_path, nodeid="tests/test_case.py::test_ok")

    receipt = qualify(
        matrix,
        root=tmp_path,
        environment={},
        source_identity=("a" * 40, "b" * 40),
    )

    result = receipt["scenarios"][0]
    assert receipt["verdict"] == "PASS"
    assert result["command"][-1] == "tests/test_case.py::test_ok"
    assert result["exit_status"] == 0
    assert result["observed_code"] == "EXPECTED_REJECTION_ASSERTED"
    assert result["test_counts"] == {
        "errors": 0,
        "failures": 0,
        "skipped": 0,
        "tests": 1,
    }
    assert len(result["evidence_sha256"]) == 64

    test_file.write_text(
        "import pytest\n@pytest.mark.skip(reason='forbidden')\ndef test_ok():\n    pass\n",
        encoding="utf-8",
    )
    with pytest.raises(QualificationError, match="did not pass exactly"):
        qualify(
            matrix,
            root=tmp_path,
            environment={},
            source_identity=("a" * 40, "b" * 40),
        )


def test_source_identity_rejects_a_dirty_checkout(tmp_path: Path) -> None:
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    subprocess.run(
        ("git", "config", "user.email", "p1-test@example.invalid"),
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "P1 Test"), cwd=tmp_path, check=True
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("accepted\n", encoding="utf-8")
    subprocess.run(("git", "add", "tracked.txt"), cwd=tmp_path, check=True)
    subprocess.run(("git", "commit", "-q", "-m", "accepted"), cwd=tmp_path, check=True)

    commit, tree = qualification._source_identity(tmp_path)
    assert len(commit) == len(tree) == 40

    (tmp_path / "untracked.txt").write_text("drift\n", encoding="utf-8")
    with pytest.raises(QualificationError, match="not clean"):
        qualification._source_identity(tmp_path)


def test_qualification_authority_binds_schema8_and_rejects_mixed_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = qualification._qualification_authority()
    assert authority["p1_product_closure_schema"] == 8
    assert authority["p1_product_closure_sha256"] == (
        "b3bbb22552b896612ef93f78a61087d95fb1c061afb6102753e9f4d614b3963b"
    )

    mixed = tmp_path / "baseline.json"
    baseline = json.loads(qualification.BASELINE.read_bytes())
    baseline["candidate_generation_id"] = "NT1231-U04-G2"
    mixed.write_text(json.dumps(baseline), encoding="utf-8")
    monkeypatch.setattr(qualification, "BASELINE", mixed)
    with pytest.raises(QualificationError, match="mixed"):
        qualification._qualification_authority()
