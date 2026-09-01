from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts import classify_p1_h_impact


ROOT = Path(__file__).resolve().parents[2]


def test_cli_holds_an_unknown_path(capsys: object) -> None:
    exit_code = classify_p1_h_impact.main(["new_engine_surface.py"])

    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert exit_code == 2
    assert output["change_class"] == "D"
    assert output["disposition"] == "HELD"


def test_cli_holds_changes_to_its_own_enforcement_source(capsys: object) -> None:
    exit_code = classify_p1_h_impact.main(["scripts/classify_p1_h_impact.py"])

    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert exit_code == 2
    assert output["change_class"] == "D"
    assert output["disposition"] == "HELD"


def test_script_entrypoint_imports_repository_packages() -> None:
    completed = subprocess.run(
        (
            sys.executable,
            str(ROOT / "scripts/classify_p1_h_impact.py"),
            "services/paper_runtime/nautilus_session.py",
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["change_class"] == "B"


def test_cli_routes_a_runtime_change_to_class_b(capsys: object) -> None:
    exit_code = classify_p1_h_impact.main(["services/paper_runtime/nautilus_session.py"])

    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert exit_code == 0
    assert output["change_class"] == "B"
    assert "P1N_PAPER" in output["required_node_ids"]


def test_cli_classifies_a_real_git_range(tmp_path: Path, capsys: object) -> None:
    subprocess.run(("git", "init", "-q", str(tmp_path)), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "config", "user.name", "test"), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"), check=True)
    tracked = tmp_path / "services/paper_runtime/nautilus_session.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(tmp_path), "add", "."), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "commit", "-qm", "base"), check=True)
    base = subprocess.check_output(("git", "-C", str(tmp_path), "rev-parse", "HEAD"), text=True).strip()
    tracked.write_text("after\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(tmp_path), "commit", "-qam", "change"), check=True)

    exit_code = classify_p1_h_impact.main(
        ["--repo", str(tmp_path), "--base", base, "--head", "HEAD"]
    )

    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert exit_code == 0
    assert output["change_class"] == "B"


def test_cli_ignores_a_git_range_outside_p1_h_ownership(
    tmp_path: Path, capsys: object
) -> None:
    subprocess.run(("git", "init", "-q", str(tmp_path)), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "config", "user.name", "test"), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"), check=True)
    tracked = tmp_path / "packages/security_master/models.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(tmp_path), "add", "."), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "commit", "-qm", "base"), check=True)
    base = subprocess.check_output(("git", "-C", str(tmp_path), "rev-parse", "HEAD"), text=True).strip()
    tracked.write_text("after\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(tmp_path), "commit", "-qam", "change"), check=True)

    exit_code = classify_p1_h_impact.main(
        ["--repo", str(tmp_path), "--base", base, "--head", "HEAD"]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"status": "NOT_APPLICABLE"}  # type: ignore[attr-defined]


def test_checkpoint_owner_is_class_c_and_lts_policy_is_class_d() -> None:
    from packages.nautilus_upgrade_authority.lts import classify_changed_paths

    recovery = classify_changed_paths(("packages/domain/recovery.py",))
    policy = classify_changed_paths(
        ("docs/implementation/p1-real-nautilus/lts/p1-engine-lts-policy-v2.json",)
    )
    policy_anchor = classify_changed_paths(
        ("packages/nautilus_upgrade_authority/lts.py",)
    )

    assert recovery.change_class.value == "C"
    assert recovery.disposition.value == "QUALIFIABLE"
    assert policy.change_class.value == "D"
    assert policy.disposition.value == "HELD"
    assert policy_anchor.change_class.value == "D"
    assert policy_anchor.disposition.value == "HELD"
