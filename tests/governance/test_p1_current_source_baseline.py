"""P1 current-source receipt must remain exact and non-promotional."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / "docs/implementation/p1-real-nautilus/current-source-baseline.json"
DESIGN = ROOT / "docs/implementation/p1-real-nautilus/design.md"
REMOTE_COMMIT = "30b6017b07f1533d8d55abfbebec735c7f03f9e5"
REMOTE_TREE = "fa6c3caa15d44d59b5ff08b10c1e6a0a4a20633a"
SOURCE_COMMIT = "3f62908385be289999ccd14eed2e4007efdbf9e2"
SOURCE_TREE = "8b3e147d5c8b27fba989183fb0a8822ec40b97a1"
PREVIOUS_REMOTE_COMMIT = "52c8748b452076c75b2d3bcf7a5fc5af26d1e068"
PREVIOUS_REMOTE_TREE = "9cdac99246a3c40b057d358de9f84f775135a86d"
EVIDENCE_COMMIT = "199d5ae65a2d1798f1c3aee9761ff763237fe910"
EVIDENCE_TREE = "a093d41727b60b99cb57608a43854f87f69ac769"
RECONCILED_COMMITS = (
    "893e536b2c56f59c8620a96f0415e65c02baec16",
    "3c5ac6996f22a6329be4315d3d7613667b683d74",
    "27972a8173b0f42d48a87e87938fcb088b80c55e",
    "39fa2d94e3a2fd328a4dd43887c65c9c0a597eff",
    "ea394d776c04a93c904ffef5f75bacb0ec2f63d7",
    REMOTE_COMMIT,
    "81096a1afcd8ffcf7fd2c0b8c5ebc02b7bc3acbc",
    "3486df502a94724817e990cd9e2e6877bb79792b",
    "0e5ae5b1bb3fe875dc4ae8ca4cc0d236f8ca80ef",
    "2ff95963a8f6336b62f035c8c449f02be7701e58",
    "b12cf94a32839e159ec14b1bf087c59afd68827a",
    SOURCE_COMMIT,
)
CANDIDATE_PATHS = (
    "engines/nautilus/candidates/v1.231",
    "engines/nautilus/v1.231-provenance-policy.json",
    "native/package6_custodian/Makefile",
    "scripts/build_nautilus_engine.py",
    "scripts/materialize_nautilus_native_authority.py",
    "scripts/materialize_nautilus_runtime_closure.py",
    "scripts/nautilus_pin_inventory",
    "scripts/verify_nautilus_release_provenance.py",
    "scripts/write_nautilus_toolchain_inputs.py",
)
CLASSIFICATIONS = (
    "ROOT_API_SENTRY_ONLY",
    "CODEX_WORKFLOW_DESIGN_DOC",
    "CODEX_WORKFLOW_PLAN_DOC",
    "CODEX_WORKFLOW_ROUTING_DOC",
    "CODEX_DEFAULT_WORKFLOW_DOC",
    "SENTRY_DEPENDENCY_GOVERNANCE_TEST",
    "HOST_AUTHORITY_LEGACY_CLOSURE_PROVISIONING",
    "HOST_AUTHORITY_EVIDENCE_ISOLATION_INITIAL",
    "HOST_AUTHORITY_EVIDENCE_ISOLATION_RUNTIME_FIX",
    "HOST_AUTHORITY_TRUSTED_ANCESTRY_FIX",
    "P1_U04_HOST_SCOPE_VALIDATOR",
    "HOST_RUNTIME_RELEASE_XATTR_COVERAGE_FIX",
)


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def _load() -> tuple[dict[str, object], bytes]:
    raw = RECEIPT.read_bytes()

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    def reject_float(_value: str) -> object:
        raise ValueError("current-source receipt float input is forbidden")

    def reject_constant(_value: str) -> object:
        raise ValueError("current-source receipt non-finite input is forbidden")

    document = json.loads(
        raw,
        object_pairs_hook=reject_duplicates,
        parse_float=reject_float,
        parse_constant=reject_constant,
    )
    assert isinstance(document, dict)
    return document, raw


def _validate(document: dict[str, object]) -> None:
    assert set(document) == {
        "authority_limits",
        "candidate_boundary",
        "dependency_boundary",
        "p1_source_baseline",
        "previous_remote_canonical",
        "reconciled_commits",
        "recorded_at",
        "remote_canonical",
        "schema",
    }
    assert document["schema"] == "trading-agent-p1-current-source-baseline/v1"
    assert document["recorded_at"] == "2026-08-28"

    remote = document["remote_canonical"]
    source = document["p1_source_baseline"]
    previous = document["previous_remote_canonical"]
    boundary = document["candidate_boundary"]
    limits = document["authority_limits"]
    assert isinstance(remote, dict)
    assert isinstance(source, dict)
    assert isinstance(previous, dict)
    assert isinstance(boundary, dict)
    assert isinstance(limits, dict)
    assert remote == {
        "commit": REMOTE_COMMIT,
        "foundation_conclusion": "success",
        "foundation_run_id": 33152028092,
        "tree": REMOTE_TREE,
    }
    assert source == {
        "commit": SOURCE_COMMIT,
        "operator_decision": "ACCEPT_EXACT_P1_SOURCE_BASELINE",
        "relationship_to_remote": "DESCENDANT",
        "tree": SOURCE_TREE,
    }
    assert previous == {"commit": PREVIOUS_REMOTE_COMMIT, "tree": PREVIOUS_REMOTE_TREE}
    assert boundary == {
        "delta": "ZERO",
        "evidence_commit": EVIDENCE_COMMIT,
        "evidence_tree": EVIDENCE_TREE,
        "pathspecs": list(CANDIDATE_PATHS),
    }
    assert document["dependency_boundary"] == {
        "sealed_engine_receives_sentry": False,
        "sentry_scope": "ROOT_API_RUNTIMES_ONLY",
    }
    assert limits == {
        "candidate_active": False,
        "candidate_promoted": False,
        "live_authorized": False,
        "network_trading_authorized": False,
        "production_authorized": False,
    }

    for authority in (remote, source, previous):
        assert _git("rev-parse", f"{authority['commit']}^{{tree}}").stdout.strip() == authority["tree"]
    assert _git("merge-base", "--is-ancestor", remote["commit"], source["commit"], check=False).returncode == 0
    commits = document["reconciled_commits"]
    assert isinstance(commits, list)
    assert all(
        isinstance(item, dict) and set(item) == {"classification", "commit", "tree"}
        for item in commits
    )
    assert tuple(item["commit"] for item in commits) == RECONCILED_COMMITS
    assert tuple(item["classification"] for item in commits) == CLASSIFICATIONS
    for item in commits:
        assert _git("rev-parse", f"{item['commit']}^{{tree}}").stdout.strip() == item["tree"]
    assert tuple(
        _git("rev-list", "--reverse", f"{PREVIOUS_REMOTE_COMMIT}..{source['commit']}").stdout.splitlines()
    ) == RECONCILED_COMMITS


def test_current_source_receipt_is_exact_and_canonical() -> None:
    document, raw = _load()
    _validate(document)
    assert raw == (json.dumps(document, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()
    assert (
        "[current-source-baseline.json](./current-source-baseline.json)"
        in DESIGN.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("section", "field", "value"),
    (
        ("remote_canonical", "commit", PREVIOUS_REMOTE_COMMIT),
        ("remote_canonical", "tree", PREVIOUS_REMOTE_TREE),
        ("p1_source_baseline", "commit", PREVIOUS_REMOTE_COMMIT),
        ("candidate_boundary", "delta", "NONZERO"),
        ("authority_limits", "candidate_active", True),
    ),
)
def test_current_source_mutations_fail_closed(section: str, field: str, value: object) -> None:
    document, _ = _load()
    mutated = deepcopy(document)
    target = mutated[section]
    assert isinstance(target, dict)
    target[field] = value
    with pytest.raises(AssertionError):
        _validate(mutated)


def test_nested_extra_and_nonfinite_authority_fail_closed() -> None:
    document, _ = _load()

    nested_extra = deepcopy(document)
    boundary = nested_extra["candidate_boundary"]
    assert isinstance(boundary, dict)
    boundary["branch"] = "main"
    with pytest.raises(AssertionError):
        _validate(nested_extra)

    reconciled_extra = deepcopy(document)
    reconciled = reconciled_extra["reconciled_commits"]
    assert isinstance(reconciled, list)
    assert isinstance(reconciled[0], dict)
    reconciled[0]["range"] = "latest"
    with pytest.raises(AssertionError):
        _validate(reconciled_extra)

    nonfinite = deepcopy(document)
    nonfinite["recorded_at"] = float("nan")
    with pytest.raises(AssertionError):
        _validate(nonfinite)


@pytest.mark.parametrize(
    ("original", "replacement", "message"),
    (
        ('"recorded_at": "2026-08-28"', '"recorded_at": NaN', "non-finite"),
        ('"foundation_run_id": 33152028092', '"foundation_run_id": 33152028092.0', "float"),
    ),
)
def test_noncanonical_numeric_json_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original: str,
    replacement: str,
    message: str,
) -> None:
    malformed = tmp_path / "current-source-baseline.json"
    malformed.write_text(
        RECEIPT.read_text(encoding="utf-8").replace(original, replacement),
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "RECEIPT", malformed)
    with pytest.raises(ValueError, match=message):
        _load()


def test_candidate_bound_delta_is_zero() -> None:
    document, _ = _load()
    boundary = document["candidate_boundary"]
    source = document["p1_source_baseline"]
    assert isinstance(boundary, dict)
    assert isinstance(source, dict)
    pathspecs = boundary["pathspecs"]
    assert isinstance(pathspecs, list)
    result = _git(
        "diff",
        "--exit-code",
        boundary["evidence_commit"],
        source["commit"],
        "--",
        *pathspecs,
        check=False,
    )
    assert result.returncode == 0, result.stdout
