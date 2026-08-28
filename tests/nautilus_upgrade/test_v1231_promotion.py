"""P1-only baseline approval must not alter legacy Phase4 authority."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
UPGRADE = ROOT / "docs/implementation/p1-real-nautilus/upgrade"
RECEIPT = UPGRADE / "p1-engine-baseline-receipt.json"
INPUTS = {
    "u04_final_acceptance": UPGRADE / "u04-final-acceptance-receipt.json",
    "u05_qualification": UPGRADE / "u05-api-qualification-receipt.json",
    "u06_qualification": UPGRADE / "u06-regression-qualification-receipt.json",
    "u07_qualification": UPGRADE / "u07-dual-runtime-qualification-receipt.json",
}
INPUT_SHA256S = {
    "u04_final_acceptance": "61b10af4eff49f6f239e7ae6c6707b2697280c07c322bea34de226bcc64f2c92",
    "u05_qualification": "558115bfba04d3096e04837f0a6bb84db8e869f6c7ceb1629827fe7a79c0fcb9",
    "u06_qualification": "30f7eec088ef4635e29f79cf5958efd9b680d822c5c73332100353356185916f",
    "u07_qualification": "a6d93218218e30b872077a92265e2eb4b289aa704aca63351f71f58e2f773f60",
}
LEGACY = {
    "job_worker_loader_sha256": (
        ROOT / "services/job_worker/nautilus_closure.py",
        "d7a67c023a96344ce53b1d4ed001822eaccf70fba4ba554b2885570f6758df89",
    ),
    "execution_simulation_policy_sha256": (
        ROOT / "engines/nautilus/runtime-closure-policy.json",
        "746df241937f6e791f30d66f2b70d50c88c451d6e6575fd903a46ea63e6c3ae2",
    ),
    "paper_compatibility_policy_sha256": (
        ROOT / "engines/nautilus/paper-compatibility-runtime-closure-policy.json",
        "ab04b77042fb351a541764054e2bac7259097c749f6ff930c3fc68ef631d592c",
    ),
}
GENERATION_SHA = "2ea31eaca9cf19715fe2a73abc8c3d11c7731466e6e84e50e65db4979be46f8c"
CLOSURE_SHA = "24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255"
QUALIFICATION_COMMIT = "979db632d96a1e45df571f22a70e2a9244574d84"
QUALIFICATION_TREE = "afb9ae226c23f36643b661d8c42faef835f4cee6"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _loads(raw: bytes) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate receipt key")
            result[key] = value
        return result

    def reject_float(_value: str) -> object:
        raise ValueError("floating-point receipt values are forbidden")

    value = json.loads(
        raw,
        object_pairs_hook=reject_duplicates,
        parse_float=reject_float,
        parse_constant=reject_float,
    )
    if not isinstance(value, dict):
        raise ValueError("P1 baseline receipt must be an object")
    return value


def _validate(receipt: dict[str, object]) -> None:
    expected_keys = {
        "authority_limits",
        "candidate_closure_sha256",
        "candidate_generation_id",
        "candidate_generation_sha256",
        "engine_version",
        "input_receipt_sha256s",
        "legacy_phase4_authority",
        "legacy_phase4_profiles_unchanged",
        "operator_decision",
        "p1_00_status",
        "p1_product_closure_schema",
        "qualification_source_commit",
        "qualification_source_tree",
        "review_source_commit",
        "review_source_tree",
        "reviews",
        "schema",
        "scope",
        "status",
        "verdict",
    }
    limits = receipt.get("authority_limits")
    legacy = receipt.get("legacy_phase4_authority")
    reviews = receipt.get("reviews")
    review_commit = receipt.get("review_source_commit")
    review_tree = receipt.get("review_source_tree")
    if (
        set(receipt) != expected_keys
        or receipt.get("schema") != "trading-agent-p1-engine-baseline-receipt/v1"
        or receipt.get("status") != "P1_BASELINE_APPROVED"
        or receipt.get("verdict") != "PASS"
        or receipt.get("scope") != "P1_A_AND_P1_B_ONLY"
        or receipt.get("operator_decision") != "PROMOTE_1_231_FOR_P1"
        or receipt.get("engine_version") != "1.231.0"
        or receipt.get("candidate_generation_id") != "NT1231-U04-G1"
        or receipt.get("candidate_generation_sha256") != GENERATION_SHA
        or receipt.get("candidate_closure_sha256") != CLOSURE_SHA
        or receipt.get("qualification_source_commit") != QUALIFICATION_COMMIT
        or receipt.get("qualification_source_tree") != QUALIFICATION_TREE
        or not isinstance(review_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", review_commit) is None
        or not isinstance(review_tree, str)
        or re.fullmatch(r"[0-9a-f]{40}", review_tree) is None
        or receipt.get("legacy_phase4_profiles_unchanged") is not True
        or type(receipt.get("p1_product_closure_schema")) is not int
        or receipt.get("p1_product_closure_schema") != 8
        or receipt.get("p1_00_status") != "READY"
        or not isinstance(limits, dict)
        or set(limits)
        != {
            "candidate_active",
            "candidate_promoted",
            "live_authorized",
            "network_trading_authorized",
            "production_authorized",
        }
        or set(limits.values()) != {False}
        or not isinstance(legacy, dict)
        or set(legacy)
        != {
            "active_policy_changes",
            "engine_version",
            "execution_simulation_policy_sha256",
            "job_worker_loader_sha256",
            "paper_compatibility_policy_sha256",
            "schema_version",
        }
        or legacy.get("engine_version") != "1.227.0"
        or type(legacy.get("schema_version")) is not int
        or legacy.get("schema_version") != 6
        or type(legacy.get("active_policy_changes")) is not int
        or legacy.get("active_policy_changes") != 0
        or legacy.get("job_worker_loader_sha256") != LEGACY["job_worker_loader_sha256"][1]
        or legacy.get("execution_simulation_policy_sha256")
        != LEGACY["execution_simulation_policy_sha256"][1]
        or legacy.get("paper_compatibility_policy_sha256")
        != LEGACY["paper_compatibility_policy_sha256"][1]
        or not isinstance(reviews, list)
        or len(reviews) != 3
        or any(
            not isinstance(review, dict)
            or set(review)
            != {
                "critical",
                "important",
                "minor",
                "reviewer_id",
                "role",
                "source_commit",
                "source_tree",
                "verdict",
            }
            or review.get("verdict") != "PASS"
            or type(review.get("critical")) is not int
            or review.get("critical") != 0
            or type(review.get("important")) is not int
            or review.get("important") != 0
            or type(review.get("minor")) is not int
            or review.get("minor", -1) < 0
            or review.get("source_commit") != review_commit
            or review.get("source_tree") != review_tree
            for review in reviews
        )
        or {
            (review["reviewer_id"], review["role"])
            for review in reviews
        }
        != {
            ("u04c2_evidence_rereview", "evidence"),
            ("u04c2_security_rereview", "security_integrity"),
            ("u04c2_spec_rereview", "specification"),
        }
    ):
        raise ValueError("P1 baseline receipt is invalid")
    resolved_tree = subprocess.run(
        ["git", "rev-parse", f"{review_commit}^{{tree}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", review_commit, "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    receipt_parent = subprocess.run(
        ["git", "log", "-1", "--format=%P", "--", str(RECEIPT.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if (
        resolved_tree.returncode != 0
        or resolved_tree.stdout.strip() != review_tree
        or ancestor.returncode != 0
        or receipt_parent.returncode != 0
        or receipt_parent.stdout.strip().split() != [review_commit]
    ):
        raise ValueError("P1 baseline review source is invalid")
    input_hashes = receipt.get("input_receipt_sha256s")
    if (
        not isinstance(input_hashes, dict)
        or input_hashes != INPUT_SHA256S
        or any(_sha(INPUTS[name]) != digest for name, digest in INPUT_SHA256S.items())
    ):
        raise ValueError("P1 baseline receipt input chain is invalid")
    for path, expected_sha in LEGACY.values():
        if _sha(path) != expected_sha:
            raise ValueError("legacy Phase4 authority changed")
    input_receipts = {name: _loads(path.read_bytes()) for name, path in INPUTS.items()}
    u04 = input_receipts.pop("u04_final_acceptance")
    if (
        u04.get("status") != "U04_ACCEPTED_G1_INACTIVE"
        or u04.get("decision") != "ACCEPT_NT1231_U04_G1"
        or u04.get("candidate_generation_id") != "NT1231-U04-G1"
        or u04.get("candidate_generation_sha256") != GENERATION_SHA
        or u04.get("candidate_closure_sha256") != CLOSURE_SHA
        or any(u04.get("authority_limits", {}).values())
    ):
        raise ValueError("P1 baseline receipt mixes U04 authority")
    if any(
        document.get("candidate_generation_id") != "NT1231-U04-G1"
        or document.get("candidate_generation_sha256") != GENERATION_SHA
        or document.get("candidate_closure_sha256") != CLOSURE_SHA
        or document.get("verdict") != "PASS"
        for document in input_receipts.values()
    ):
        raise ValueError("P1 baseline receipt mixes qualification authority")


def test_committed_p1_baseline_receipt_is_closed_p1_only_and_legacy_safe() -> None:
    raw = RECEIPT.read_bytes()
    receipt = _loads(raw)
    assert raw == (
        json.dumps(receipt, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("ascii")
    _validate(receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("candidate_generation_id", "NT1231-U04-G2"),
        ("candidate_generation_sha256", "f" * 64),
        ("candidate_closure_sha256", "f" * 64),
        ("legacy_phase4_profiles_unchanged", False),
        ("p1_product_closure_schema", 7),
        ("scope", "GLOBAL"),
        ("operator_decision", "HOLD_P1"),
        ("qualification_source_commit", "f" * 40),
        ("qualification_source_tree", "f" * 40),
        ("review_source_commit", "f" * 40),
        ("review_source_tree", "f" * 40),
    ),
)
def test_promotion_mutations_fail_closed(field: str, value: object) -> None:
    receipt = _loads(RECEIPT.read_bytes())
    receipt[field] = value
    with pytest.raises(ValueError):
        _validate(receipt)


def test_live_or_legacy_authority_mutations_fail_closed() -> None:
    receipt = _loads(RECEIPT.read_bytes())
    for field in receipt["authority_limits"]:
        mutated = deepcopy(receipt)
        mutated["authority_limits"][field] = True
        with pytest.raises(ValueError):
            _validate(mutated)
    mutated = deepcopy(receipt)
    mutated["legacy_phase4_authority"]["engine_version"] = "1.231.0"
    with pytest.raises(ValueError):
        _validate(mutated)


def test_missing_or_unresolved_qualification_and_review_fail_closed() -> None:
    receipt = _loads(RECEIPT.read_bytes())
    missing = deepcopy(receipt)
    del missing["input_receipt_sha256s"]["u07_qualification"]
    with pytest.raises(ValueError):
        _validate(missing)
    unresolved = deepcopy(receipt)
    unresolved["reviews"][0]["important"] = 1
    with pytest.raises(ValueError):
        _validate(unresolved)

    float_schema = deepcopy(receipt)
    float_schema["p1_product_closure_schema"] = 8.0
    with pytest.raises(ValueError):
        _validate(float_schema)

    duplicate_reviewer = deepcopy(receipt)
    duplicate_reviewer["reviews"][1]["reviewer_id"] = duplicate_reviewer[
        "reviews"
    ][0]["reviewer_id"]
    with pytest.raises(ValueError):
        _validate(duplicate_reviewer)

    negative_minor = deepcopy(receipt)
    negative_minor["reviews"][0]["minor"] = -1
    with pytest.raises(ValueError):
        _validate(negative_minor)
