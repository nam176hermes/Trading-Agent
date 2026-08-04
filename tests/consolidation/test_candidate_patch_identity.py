from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/candidate_patch_identity.py"


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "candidate"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Candidate Test")
    _git(repository, "config", "user.email", "candidate@example.test")
    (repository / ".gitignore").write_text("apps/dashboard/.next/\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("before\n", encoding="utf-8")
    (repository / "deleted.txt").write_text("remove\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "base")
    return repository


def _tool(repository: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repository), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def test_candidate_identity_matches_after_candidate_is_committed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked.txt").write_text("after\n", encoding="utf-8")
    (repository / "deleted.txt").unlink()
    (repository / "new.py").write_text("value = 1\n", encoding="utf-8")
    ignored = repository / "apps" / "dashboard" / ".next"
    ignored.mkdir(parents=True)
    (ignored / "build.txt").write_text("ignored\n", encoding="utf-8")

    source = json.loads(
        _tool(
            repository,
            "--allow-untracked",
            "new.py",
            "--require-complete",
        ).stdout
    )
    assert source["complete"] is True
    assert source["unapproved_untracked"] == []
    assert [entry["path"] for entry in source["entries"]] == [
        "deleted.txt",
        "new.py",
        "tracked.txt",
    ]
    assert source["entries"][0] == {"path": "deleted.txt", "state": "deleted"}
    assert all(".next" not in entry["path"] for entry in source["entries"])

    _git(repository, "add", "-A")
    _git(repository, "commit", "-qm", "candidate")
    evidence = json.loads(
        _tool(repository, "--base", "HEAD^", "--require-complete").stdout
    )

    assert evidence["complete"] is True
    assert evidence["candidate_sha256"] == source["candidate_sha256"]
    assert evidence["base_tree"] == source["base_tree"]
    assert evidence["entries"] == source["entries"]


def test_require_complete_reports_unapproved_untracked_regular_files(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "unapproved.txt").write_text("candidate?\n", encoding="utf-8")

    result = _tool(repository, "--require-complete", check=False)

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["complete"] is False
    assert report["unapproved_untracked"] == ["unapproved.txt"]


def test_explicitly_allowed_untracked_symlink_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "outside.txt").write_text("outside\n", encoding="utf-8")
    (repository / "linked.txt").symlink_to("outside.txt")

    result = _tool(
        repository,
        "--allow-untracked",
        "linked.txt",
        "--require-complete",
        check=False,
    )

    assert result.returncode == 2
    assert "regular file" in result.stderr
