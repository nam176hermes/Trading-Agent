from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import audit_canonical_repo as audit
from tests.consolidation.test_audit_canonical_repo import (
    _remove_authority_repositories,
    _valid_root,
)


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts/audit_canonical_repo.py"
MANIFEST = ROOT / "ops/consolidation/p0-canonical-baseline.json"
SHA = re.compile(r"[0-9a-f]{40}\Z")


@pytest.fixture
def tmp_path() -> Path:
    with tempfile.TemporaryDirectory(prefix="p0-canonical-baseline-", dir="/tmp") as directory:
        yield Path(directory)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _p0_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, object]]:
    repository = tmp_path / "p0-baseline"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "P0 Test")
    _git(repository, "config", "user.email", "p0@example.invalid")
    (repository / "base").write_text("base\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "base")
    base = _git(repository, "rev-parse", "HEAD")
    (repository / "candidate").write_text("candidate\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "candidate")
    candidate = _git(repository, "rev-parse", "HEAD")
    document: dict[str, object] = {
        "schema_version": "p0-canonical-baseline/v1",
        "base_branch": "main",
        "base_sha": base,
        "candidate_source_branch": "codex/phase1-terra-autopilot-19627785c140",
        "candidate_start_sha": candidate,
        "qualified_sha": None,
        "promotion_mode": "fast-forward-only",
        "paper_only": True,
        "live_execution_authorized": False,
    }
    monkeypatch.setattr(
        audit,
        "_P0_BASELINE",
        {key: value for key, value in document.items() if key not in {"qualified_sha", "paper_only", "live_execution_authorized"}},
    )
    (repository / "ops/consolidation").mkdir(parents=True)
    (repository / audit._P0_BASELINE_PATH).write_text(json.dumps(document), encoding="utf-8")
    return repository, document


def _assert_baseline_error(
    repository: Path,
    document: dict[str, object],
    expected: str,
) -> None:
    (repository / audit._P0_BASELINE_PATH).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(audit.CliError, match=f"^{expected}$"):
        audit._audit_p0_baseline(repository, _git(repository, "rev-parse", "HEAD"))


def test_p0_baseline_manifest_is_approved_and_passes_the_portable_audit() -> None:
    assert MANIFEST.is_file(), "manifest missing"
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert document == {
        "schema_version": "p0-canonical-baseline/v1",
        "base_branch": "main",
        "base_sha": "19627785c140c502260f864e462fed9b9925436e",
        "candidate_source_branch": "codex/phase1-terra-autopilot-19627785c140",
        "candidate_start_sha": "417c17452ea31f0ca8c8e9893ac3c03a3a90a7c1",
        "qualified_sha": None,
        "promotion_mode": "fast-forward-only",
        "paper_only": True,
        "live_execution_authorized": False,
    }
    assert set(document) == audit._P0_BASELINE_KEYS
    assert all(SHA.fullmatch(document[key]) for key in ("base_sha", "candidate_start_sha"))

    result = subprocess.run(
        [sys.executable, str(AUDIT), "--portable", "--check-p0-baseline"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_p0_baseline_rejects_schema_and_authority_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, document = _p0_repository(tmp_path, monkeypatch)

    (repository / audit._P0_BASELINE_PATH).unlink()
    with pytest.raises(audit.CliError, match="^P0_BASELINE_MISSING$"):
        audit._audit_p0_baseline(repository, _git(repository, "rev-parse", "HEAD"))

    _assert_baseline_error(repository, {**document, "unknown": True}, "P0_BASELINE_SCHEMA_INVALID")
    _assert_baseline_error(repository, {**document, "base_sha": "not-a-sha"}, "P0_BASELINE_SCHEMA_INVALID")
    _assert_baseline_error(repository, {**document, "paper_only": False}, "P0_LIVE_AUTHORITY_FORBIDDEN")
    _assert_baseline_error(repository, {**document, "live_execution_authorized": True}, "P0_LIVE_AUTHORITY_FORBIDDEN")
    _assert_baseline_error(repository, {**document, "qualified_sha": _git(repository, "rev-parse", "HEAD")}, "P0_BASELINE_SCHEMA_INVALID")


def test_p0_baseline_rejects_missing_sha_and_nonancestor_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, document = _p0_repository(tmp_path, monkeypatch)
    missing = {**document, "base_sha": "f" * 40}
    monkeypatch.setitem(audit._P0_BASELINE, "base_sha", missing["base_sha"])
    _assert_baseline_error(repository, missing, "P0_BASELINE_SHA_MISSING")

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-q")
    _git(foreign, "config", "user.name", "P0 Test")
    _git(foreign, "config", "user.email", "p0@example.invalid")
    (foreign / "foreign").write_text("foreign\n", encoding="utf-8")
    _git(foreign, "add", ".")
    _git(foreign, "commit", "-qm", "foreign")
    foreign_sha = _git(foreign, "rev-parse", "HEAD")
    _git(repository, "fetch", "--no-tags", str(foreign), foreign_sha)
    nonancestor = {**document, "base_sha": foreign_sha}
    monkeypatch.setitem(audit._P0_BASELINE, "base_sha", foreign_sha)
    _assert_baseline_error(repository, nonancestor, "P0_BASELINE_ANCESTRY_INVALID")


def test_portable_p0_check_passes_without_external_authority_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _valid_root(tmp_path)
    _remove_authority_repositories(repository)
    head = _git(repository, "rev-parse", "HEAD")
    document: dict[str, object] = {
        "schema_version": "p0-canonical-baseline/v1",
        "base_branch": "main",
        "base_sha": head,
        "candidate_source_branch": "codex/phase1-terra-autopilot-19627785c140",
        "candidate_start_sha": head,
        "qualified_sha": None,
        "promotion_mode": "fast-forward-only",
        "paper_only": True,
        "live_execution_authorized": False,
    }
    monkeypatch.setattr(
        audit,
        "_P0_BASELINE",
        {key: value for key, value in document.items() if key not in {"qualified_sha", "paper_only", "live_execution_authorized"}},
    )
    manifest = repository / audit._P0_BASELINE_PATH
    manifest.write_text(json.dumps(document), encoding="utf-8")

    result = audit.audit(repository, release=False, portable_requested=True, check_p0_baseline=True)

    assert result["authority_mode"] == "portable"
    assert result["result"] == "PASS"
