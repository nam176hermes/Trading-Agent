from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from packages import project_status
from packages.project_status import make_pass_receipt
from packages.pre_p3_provenance import (
    ProvenanceError,
    canonical_source_identity,
    make_candidate_certificate,
    make_promotion_receipt,
    make_v2_gate_receipt,
    payload_sha256,
    source_matches_current,
    validate_candidate_certificate,
    validate_promotion_receipt,
    validate_v2_gate_receipt,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=root, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test Operator")
    _git(root, "config", "user.email", "operator@example.invalid")
    (root / "README.md").write_text("base\n")
    _commit(root, "promotion base")
    (root / "packages").mkdir()
    (root / "packages/core.py").write_text("VALUE = 1\n")
    (root / "uv.lock").write_text("version = 1\n")
    (root / "contracts").mkdir()
    (root / "contracts/schema.json").write_text('{"version":1}\n')
    _commit(root, "qualified source")
    return root


def test_source_identity_is_stable_for_the_exact_commit(tmp_path: Path) -> None:
    """Break caught: one immutable commit produces inconsistent identity."""
    root = _repo(tmp_path)

    first = canonical_source_identity(root, "HEAD")
    second = canonical_source_identity(root, first["commit_sha"])

    assert first == second
    assert set(first) == {
        "closure_policy_sha256",
        "closure_schema_version",
        "closure_sha256",
        "commit_sha",
        "tree_sha",
    }
    assert first["closure_schema_version"] == "trading-agent-source-closure-v1"
    assert all(len(first[key]) == length for key, length in {
        "closure_policy_sha256": 64,
        "closure_sha256": 64,
        "commit_sha": 40,
        "tree_sha": 40,
    }.items())


def test_source_identity_ignores_ambient_git_repository_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: ambient Git variables redirect the qualified repository."""
    root = _repo(tmp_path)
    expected = _git(root, "rev-parse", "HEAD")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-b", "main")
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))

    assert canonical_source_identity(root)["commit_sha"] == expected


@pytest.mark.parametrize(
    "mutation",
    ("byte", "mode", "lockfile", "schema", "add", "delete"),
)
def test_source_identity_rejects_semantic_source_drift(
    tmp_path: Path, mutation: str
) -> None:
    """Break caught: changed executable source inherits stale qualification."""
    root = _repo(tmp_path)
    qualified = canonical_source_identity(root, "HEAD")

    if mutation == "byte":
        (root / "packages/core.py").write_text("VALUE = 2\n")
    elif mutation == "mode":
        os.chmod(root / "packages/core.py", 0o755)
    elif mutation == "lockfile":
        (root / "uv.lock").write_text("version = 2\n")
    elif mutation == "schema":
        (root / "contracts/schema.json").write_text('{"version":2}\n')
    elif mutation == "add":
        (root / "packages/added.py").write_text("ADDED = True\n")
    else:
        (root / "packages/core.py").unlink()
    _commit(root, mutation)

    assert canonical_source_identity(root, "HEAD")["closure_sha256"] != qualified[
        "closure_sha256"
    ]


def test_source_identity_rejects_symlink_and_gitlink_authority(tmp_path: Path) -> None:
    """Break caught: external symlink or submodule authority escapes the closure."""
    root = _repo(tmp_path)
    (root / "packages/link.py").symlink_to("core.py")
    _commit(root, "add symlink")

    with pytest.raises(ProvenanceError, match="symlink"):
        canonical_source_identity(root, "HEAD")


def test_only_exact_generated_status_path_is_excluded(tmp_path: Path) -> None:
    """Break caught: a broad output exclusion hides newly added source."""
    root = _repo(tmp_path)
    qualified = canonical_source_identity(root, "HEAD")
    status = root / "docs/implementation/project-status.json"
    status.parent.mkdir(parents=True)
    status.write_text('{"generated":true}\n')
    _commit(root, "generated status")
    after_status = canonical_source_identity(root, "HEAD")
    assert after_status["closure_sha256"] == qualified["closure_sha256"]

    unknown = root / "docs/implementation/pre-p3/receipts/unknown.json"
    unknown.parent.mkdir(parents=True)
    unknown.write_text('{"status":"PASS"}\n')
    _commit(root, "unknown receipt-like file")

    assert canonical_source_identity(root, "HEAD")["closure_sha256"] != qualified[
        "closure_sha256"
    ]


def _qualification() -> dict[str, str]:
    return {
        "completed_at_utc": "2026-09-01T12:00:00Z",
        "producer": "scripts/qualify_pre_p3.py",
        "run_attempt": "1",
        "run_id": "12345",
    }


def _promotion_run(promoted_commit: str, attempt: str = "1") -> dict[str, str]:
    return {
        "event": "push",
        "ref": "refs/heads/main",
        "repository": "nam176hermes/Trading-Agent",
        "run_attempt": attempt,
        "run_id": "67890",
        "sha": promoted_commit,
        "workflow": "Foundation",
        "workflow_ref": (
            "nam176hermes/Trading-Agent/.github/workflows/"
            "foundation.yml@refs/heads/main"
        ),
        "workflow_sha": promoted_commit,
    }


def _tracked_evidence(root: Path) -> tuple[dict[str, str], ...]:
    return (
        {
            "kind": "TRACKED_BLOB",
            "locator": "packages/core.py",
            "name": "core-source",
            "sha256": __import__("hashlib").sha256(b"VALUE = 1\n").hexdigest(),
        },
    )


def test_v2_gate_receipt_binds_canonical_source_and_tracked_evidence(
    tmp_path: Path,
) -> None:
    """Break caught: a PASS receipt does not prove its source or evidence bytes."""
    root = _repo(tmp_path)
    source = canonical_source_identity(root, "HEAD")

    receipt = make_v2_gate_receipt(
        "P2_SOURCE_COMPLETE",
        source=source,
        evidence=_tracked_evidence(root),
        qualification=_qualification(),
    )

    assert validate_v2_gate_receipt(
        receipt, "P2_SOURCE_COMPLETE", root=root
    ) == receipt
    assert receipt["schema_version"] == "pre-p3-gate-receipt-v2"
    assert receipt["source"] == source


def test_receipt_authority_payload_cannot_mutate_the_validator_baseline(
    tmp_path: Path,
) -> None:
    """Break caught: one caller mutation globally enables forbidden authority."""
    root = _repo(tmp_path)
    source = canonical_source_identity(root, "HEAD")
    first = make_v2_gate_receipt(
        "P2_SOURCE_COMPLETE",
        source=source,
        evidence=_tracked_evidence(root),
        qualification=_qualification(),
    )
    first["authority"]["network"] = True

    second = make_v2_gate_receipt(
        "P2_SOURCE_COMPLETE",
        source=source,
        evidence=_tracked_evidence(root),
        qualification=_qualification(),
    )

    assert second["authority"] == {
        "broker": False,
        "live": False,
        "network": False,
        "production": False,
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("status", "DEFERRED", "status"),
        ("authority", {"network": True}, "authority"),
        ("evidence", (), "evidence"),
        ("qualification", {"run_id": "12345"}, "qualification"),
    ),
)
def test_v2_gate_receipt_rejects_deferred_or_malformed_authority(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    """Break caught: incomplete or authority-bearing evidence becomes PASS."""
    root = _repo(tmp_path)
    receipt = make_v2_gate_receipt(
        "P2_SOURCE_COMPLETE",
        source=canonical_source_identity(root, "HEAD"),
        evidence=_tracked_evidence(root),
        qualification=_qualification(),
    )
    forged = receipt | {field: value}
    forged["receipt_sha256"] = payload_sha256(forged)

    with pytest.raises(ProvenanceError, match=message):
        validate_v2_gate_receipt(forged, "P2_SOURCE_COMPLETE", root=root)


def test_v2_gate_receipt_rejects_mismatched_tracked_evidence(tmp_path: Path) -> None:
    """Break caught: receipt evidence metadata names bytes it did not hash."""
    root = _repo(tmp_path)
    evidence = list(_tracked_evidence(root))
    evidence[0] = evidence[0] | {"sha256": "0" * 64}
    receipt = make_v2_gate_receipt(
        "P2_SOURCE_COMPLETE",
        source=canonical_source_identity(root, "HEAD"),
        evidence=tuple(evidence),
        qualification=_qualification(),
    )

    with pytest.raises(ProvenanceError, match="tracked evidence"):
        validate_v2_gate_receipt(receipt, "P2_SOURCE_COMPLETE", root=root)


def test_semantic_source_match_survives_rewrite_but_not_byte_change(
    tmp_path: Path,
) -> None:
    """Break caught: ancestry replaces byte equivalence, or drift inherits PASS."""
    root = _repo(tmp_path)
    qualified = canonical_source_identity(root, "HEAD")
    _git(root, "commit", "--amend", "-m", "rebased identity")

    assert _git(root, "rev-parse", "HEAD") != qualified["commit_sha"]
    assert source_matches_current(root, qualified)

    (root / "packages/core.py").write_text("VALUE = 2\n")
    _commit(root, "one byte changed")
    assert not source_matches_current(root, qualified)


def test_valid_v2_receipt_is_excluded_but_malformed_known_output_is_not(
    tmp_path: Path,
) -> None:
    """Break caught: output paths are broadly excluded without schema validation."""
    root = _repo(tmp_path)
    qualified = canonical_source_identity(root, "HEAD")
    receipt = make_v2_gate_receipt(
        "P2_SOURCE_COMPLETE",
        source=qualified,
        evidence=_tracked_evidence(root),
        qualification=_qualification(),
    )
    path = root / "docs/implementation/pre-p3/receipts/p2-source-complete-v2.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_json_bytes(receipt) + b"\n")
    _commit(root, "valid generated receipt")
    assert canonical_source_identity(root, "HEAD")["closure_sha256"] == qualified[
        "closure_sha256"
    ]

    path.write_text('{"status":"PASS"}\n')
    _commit(root, "malformed generated receipt")
    assert canonical_source_identity(root, "HEAD")["closure_sha256"] != qualified[
        "closure_sha256"
    ]


GATE_FILES = {
    "ALPHA_REGISTRY_FOUNDATION": "alpha-registry-foundation-v2.json",
    "P1_H_COMPLETE": "p1-h-complete-v2.json",
    "P1_LTS_READY": "p1-lts-ready-v2.json",
    "P2_SOURCE_COMPLETE": "p2-source-complete-v2.json",
    "P2_RUNTIME_QUALIFIED": "p2-runtime-qualified-v2.json",
    "P2_QUALIFIED": "p2-qualified-v2.json",
    "P3_BASELINES_FROZEN": "p3-baselines-frozen-v2.json",
    "P3_EVALUATION_PROTOCOL_FROZEN": "p3-evaluation-protocol-frozen-v2.json",
}


def _receipt_set(root: Path, source: dict[str, str]) -> dict[str, dict[str, object]]:
    receipts: dict[str, dict[str, object]] = {}
    for gate in GATE_FILES:
        evidence = list(_tracked_evidence(root))
        if gate == "P1_LTS_READY":
            evidence.append(
                {
                    "kind": "DERIVED_RECEIPT",
                    "locator": "P1_H_COMPLETE",
                    "name": "p1-h-complete",
                    "sha256": receipts["P1_H_COMPLETE"]["receipt_sha256"],
                }
            )
        elif gate == "P2_QUALIFIED":
            evidence.extend(
                {
                    "kind": "DERIVED_RECEIPT",
                    "locator": dependency,
                    "name": dependency.lower().replace("_", "-"),
                    "sha256": receipts[dependency]["receipt_sha256"],
                }
                for dependency in ("P2_SOURCE_COMPLETE", "P2_RUNTIME_QUALIFIED")
            )
        receipts[gate] = make_v2_gate_receipt(
            gate,
            source=source,
            evidence=tuple(evidence),
            qualification=_qualification(),
        )
    return receipts


def _write_receipts(root: Path, receipts: dict[str, dict[str, object]]) -> Path:
    receipt_dir = root / "docs/implementation/pre-p3/receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    for gate, payload in receipts.items():
        (receipt_dir / GATE_FILES[gate]).write_bytes(canonical_json_bytes(payload) + b"\n")
    return receipt_dir


def _legacy_bindings(root: Path, source: dict[str, str]) -> dict[str, str]:
    receipt_dir = root / "docs/implementation/pre-p3/receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    bindings: dict[str, str] = {}
    for index, (gate, v2_name) in enumerate(GATE_FILES.items(), start=1):
        path = receipt_dir / v2_name.replace("-v2.json", "-v1.json")
        path.write_bytes(
            canonical_json_bytes(
                make_pass_receipt(
                    gate,
                    source_sha=source["commit_sha"],
                    source_tree=source["tree_sha"],
                    evidence_sha256s=(f"{index:x}" * 64,),
                )
            )
            + b"\n"
        )
        bindings[gate] = hashlib.sha256(path.read_bytes()).hexdigest()
    return bindings


def test_candidate_certificate_requires_one_source_and_derived_receipt_chain(
    tmp_path: Path,
) -> None:
    """Break caught: P1-H/P2 artifacts from different closures are mixed."""
    root = _repo(tmp_path)
    source = canonical_source_identity(root, "HEAD")
    receipts = _receipt_set(root, source)
    receipt_dir = _write_receipts(root, receipts)
    candidate = make_candidate_certificate(
        receipts=receipts,
        legacy_receipts=_legacy_bindings(root, source),
        qualification=_qualification(),
        destination={
            "base_sha": _git(root, "rev-parse", "HEAD^"),
            "promotion_type": "SQUASH",
            "ref": "refs/heads/main",
            "repository": "nam176hermes/Trading-Agent",
        },
    )
    candidate_path = receipt_dir / "pre-p3-candidate-v2.json"
    candidate_path.write_bytes(canonical_json_bytes(candidate) + b"\n")

    assert validate_candidate_certificate(
        candidate, root=root, receipt_dir=receipt_dir
    ) == candidate
    assert candidate["status"] == "PRE_P3_CANDIDATE_QUALIFIED"
    assert candidate["p3_alpha_development_allowed"] is False

    forged_legacy = candidate | {
        "legacy_receipts": candidate["legacy_receipts"] | {"P1_H_COMPLETE": "0" * 64}
    }
    forged_legacy["receipt_sha256"] = payload_sha256(forged_legacy)
    with pytest.raises(ProvenanceError, match="legacy"):
        validate_candidate_certificate(
            forged_legacy, root=root, receipt_dir=receipt_dir
        )

    forged_receipts = dict(receipts)
    other_source = source | {"closure_sha256": "0" * 64}
    forged_receipts["P3_BASELINES_FROZEN"] = make_v2_gate_receipt(
        "P3_BASELINES_FROZEN",
        source=other_source,
        evidence=_tracked_evidence(root),
        qualification=_qualification(),
    )
    with pytest.raises(ProvenanceError, match="one source"):
        make_candidate_certificate(
            receipts=forged_receipts,
            legacy_receipts=_legacy_bindings(root, source),
            qualification=_qualification(),
            destination=candidate["destination"],
        )


def test_candidate_certificate_rejects_missing_derived_p2_binding(tmp_path: Path) -> None:
    """Break caught: final P2 PASS does not bind its source and runtime proofs."""
    root = _repo(tmp_path)
    source = canonical_source_identity(root, "HEAD")
    receipts = _receipt_set(root, source)
    receipts["P2_QUALIFIED"] = make_v2_gate_receipt(
        "P2_QUALIFIED",
        source=source,
        evidence=_tracked_evidence(root),
        qualification=_qualification(),
    )

    with pytest.raises(ProvenanceError, match="P2_QUALIFIED"):
        make_candidate_certificate(
            receipts=receipts,
            legacy_receipts=_legacy_bindings(root, source),
            qualification=_qualification(),
            destination={
                "base_sha": _git(root, "rev-parse", "HEAD^"),
                "promotion_type": "SQUASH",
                "ref": "refs/heads/main",
                "repository": "nam176hermes/Trading-Agent",
            },
        )


@pytest.mark.parametrize("promotion_type", ("SQUASH", "REBASE", "CHERRY_PICK"))
def test_promotion_receipt_accepts_identical_closure_across_history_rewrites(
    tmp_path: Path, promotion_type: str
) -> None:
    """Break caught: byte-identical promotion is rejected solely by ancestry shape."""
    root = _repo(tmp_path)
    qualified_commit = _git(root, "rev-parse", "HEAD")
    base_sha = _git(root, "rev-parse", "HEAD^")
    source = canonical_source_identity(root, qualified_commit)
    receipts = _receipt_set(root, source)
    receipt_dir = _write_receipts(root, receipts)
    candidate = make_candidate_certificate(
        receipts=receipts,
        legacy_receipts=_legacy_bindings(root, source),
        qualification=_qualification(),
        destination={
            "base_sha": base_sha,
            "promotion_type": promotion_type,
            "ref": "refs/heads/main",
            "repository": "nam176hermes/Trading-Agent",
        },
    )
    candidate_path = receipt_dir / "pre-p3-candidate-v2.json"
    candidate_path.write_bytes(canonical_json_bytes(candidate) + b"\n")
    _commit(root, "candidate receipts")
    candidate_tip = _git(root, "rev-parse", "HEAD")
    candidate_branch = _git(root, "branch", "--show-current")

    if promotion_type == "SQUASH":
        _git(root, "checkout", "-b", "promoted-main", base_sha)
        _git(root, "merge", "--squash", candidate_tip)
        promoted_commit = _commit(root, "squash promotion")
    elif promotion_type == "REBASE":
        _git(root, "checkout", "-b", "release-base", base_sha)
        status = root / "docs/implementation/project-status.json"
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text('{"status":"HELD"}\n')
        _commit(root, "receipt-only release base")
        _git(root, "checkout", candidate_branch)
        _git(root, "rebase", "--onto", "release-base", base_sha)
        promoted_commit = _git(root, "rev-parse", "HEAD")
    else:
        _git(root, "checkout", "-b", "promoted-main", base_sha)
        _git(root, "cherry-pick", qualified_commit, candidate_tip)
        promoted_commit = _git(root, "rev-parse", "HEAD")
    assert promoted_commit != qualified_commit
    assert source_matches_current(root, source)

    promotion = make_promotion_receipt(
        root=root,
        candidate_path=candidate_path,
        promoted_revision=promoted_commit,
        run=_promotion_run(promoted_commit),
    )
    promotion_path = root / f"docs/implementation/pre-p3/promotions/{promoted_commit}-v1.json"
    promotion_path.parent.mkdir(parents=True)
    promotion_path.write_bytes(canonical_json_bytes(promotion) + b"\n")
    _commit(root, "record promotion provenance")

    assert validate_promotion_receipt(
        promotion,
        root=root,
        candidate_path=candidate_path,
        current_revision="HEAD",
    ) == promotion

    forged = json.loads(json.dumps(promotion))
    forged["promoted_source"]["closure_sha256"] = "0" * 64
    forged["receipt_sha256"] = payload_sha256(forged)
    with pytest.raises(ProvenanceError, match="source binding"):
        validate_promotion_receipt(
            forged,
            root=root,
            candidate_path=candidate_path,
            current_revision="HEAD",
        )

    for field, value in {
        "ref": "refs/heads/replay",
        "repository": "attacker/Trading-Agent",
        "sha": qualified_commit,
        "workflow_ref": "attacker/workflow@refs/heads/main",
        "workflow_sha": qualified_commit,
    }.items():
        replay = json.loads(json.dumps(promotion))
        replay["run"][field] = value
        replay["receipt_sha256"] = payload_sha256(replay)
        with pytest.raises(ProvenanceError, match="run identity"):
            validate_promotion_receipt(
                replay,
                root=root,
                candidate_path=candidate_path,
                current_revision="HEAD",
            )


def test_promotion_receipt_rejects_source_drift_forgery_and_replay(tmp_path: Path) -> None:
    """Break caught: recomputed receipt hashes bless changed or unrelated source."""
    root = _repo(tmp_path)
    source = canonical_source_identity(root, "HEAD")
    receipts = _receipt_set(root, source)
    receipt_dir = _write_receipts(root, receipts)
    candidate = make_candidate_certificate(
        receipts=receipts,
        legacy_receipts=_legacy_bindings(root, source),
        qualification=_qualification(),
        destination={
            "base_sha": _git(root, "rev-parse", "HEAD^"),
            "promotion_type": "SQUASH",
            "ref": "refs/heads/main",
            "repository": "nam176hermes/Trading-Agent",
        },
    )
    candidate_path = receipt_dir / "pre-p3-candidate-v2.json"
    candidate_path.write_bytes(canonical_json_bytes(candidate) + b"\n")
    _commit(root, "candidate receipts")
    (root / "packages/core.py").write_text("VALUE = 9\n")
    promoted = _commit(root, "changed promotion")

    with pytest.raises(ProvenanceError, match="closure"):
        make_promotion_receipt(
            root=root,
            candidate_path=candidate_path,
            promoted_revision=promoted,
            run=_promotion_run(promoted),
        )


def _candidate_state(root: Path) -> tuple[Path, str, str]:
    qualified_commit = _git(root, "rev-parse", "HEAD")
    base_sha = _git(root, "rev-parse", "HEAD^")
    source = canonical_source_identity(root, qualified_commit)
    receipts = _receipt_set(root, source)
    receipt_dir = _write_receipts(root, receipts)
    candidate = make_candidate_certificate(
        receipts=receipts,
        legacy_receipts=_legacy_bindings(root, source),
        qualification=_qualification(),
        destination={
            "base_sha": base_sha,
            "promotion_type": "SQUASH",
            "ref": "refs/heads/main",
            "repository": "nam176hermes/Trading-Agent",
        },
    )
    candidate_path = receipt_dir / "pre-p3-candidate-v2.json"
    candidate_path.write_bytes(canonical_json_bytes(candidate) + b"\n")
    _commit(root, "candidate qualification")
    return candidate_path, base_sha, qualified_commit


def test_status_evaluation_keeps_candidate_pre_p3_held(tmp_path: Path) -> None:
    """Break caught: feature-branch qualification grants protected-main authority."""
    root = _repo(tmp_path)
    _candidate_state(root)

    result = project_status.evaluate_pre_p3_provenance(root)

    assert set(result["gates"].values()) == {"PASS"}
    assert result["pre_p3_ready"] == "HELD"
    assert result["provenance"]["candidate"]["status"] == "PASS"
    assert result["provenance"]["promotion"]["status"] == "HELD"


def test_status_evaluation_passes_only_after_recorded_main_promotion(
    tmp_path: Path,
) -> None:
    """Break caught: main status passes without checking the promoted commit."""
    root = _repo(tmp_path)
    candidate_path, base_sha, _ = _candidate_state(root)
    candidate_tip = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-b", "promoted-main", base_sha)
    _git(root, "merge", "--squash", candidate_tip)
    promoted_commit = _commit(root, "squash promotion")
    promotion = make_promotion_receipt(
        root=root,
        candidate_path=candidate_path,
        promoted_revision=promoted_commit,
        run=_promotion_run(promoted_commit),
    )
    promotion_path = root / f"docs/implementation/pre-p3/promotions/{promoted_commit}-v1.json"
    promotion_path.parent.mkdir(parents=True)
    promotion_path.write_bytes(canonical_json_bytes(promotion) + b"\n")
    _commit(root, "promotion provenance")

    result = project_status.evaluate_pre_p3_provenance(root)

    assert set(result["gates"].values()) == {"PASS"}
    assert result["pre_p3_ready"] == "PASS"
    assert result["provenance"]["promotion"]["status"] == "PASS"

    second_promoted_commit = _git(root, "rev-parse", "HEAD")
    second = make_promotion_receipt(
        root=root,
        candidate_path=candidate_path,
        promoted_revision=second_promoted_commit,
        run=_promotion_run(second_promoted_commit, "2"),
    )
    second_path = root / (
        f"docs/implementation/pre-p3/promotions/{second_promoted_commit}-v1.json"
    )
    second_path.write_bytes(canonical_json_bytes(second) + b"\n")
    _commit(root, "ambiguous second promotion")

    assert project_status.evaluate_pre_p3_provenance(root)["pre_p3_ready"] == "HELD"
