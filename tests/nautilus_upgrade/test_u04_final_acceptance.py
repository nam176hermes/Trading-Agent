"""Acceptance boundary for the immutable P1-U04 G1 review."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
UPGRADE = ROOT / "docs/implementation/p1-real-nautilus/upgrade"
RECEIPT = UPGRADE / "u04-final-review-receipt.json"
GENERATION = UPGRADE / "candidate-generations/NT1231-U04-G1.json"
SOURCE_COMMIT = "3f62908385be289999ccd14eed2e4007efdbf9e2"
SOURCE_TREE = "8b3e147d5c8b27fba989183fb0a8822ec40b97a1"
GENERATION_SHA256 = "2ea31eaca9cf19715fe2a73abc8c3d11c7731466e6e84e50e65db4979be46f8c"
CLOSURE_SHA256 = "24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255"


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode()


def _load_closed(path: Path) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            assert key not in value, "duplicate JSON key"
            value[key] = item
        return value

    def reject_float(_value: str) -> object:
        raise AssertionError("float/non-finite JSON is forbidden")

    raw = path.read_bytes()
    value = json.loads(
        raw,
        object_pairs_hook=no_duplicates,
        parse_float=reject_float,
        parse_constant=reject_float,
    )
    assert isinstance(value, dict)
    assert raw == _canonical(value)
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _p1_host_command() -> str:
    policy = json.loads(
        (
            ROOT
            / "engines/nautilus/candidates/v1.231/engine-build-policy.json"
        ).read_text(encoding="ascii")
    )
    cache = policy["external_cache_isolation"]["external_roots"][
        "candidate_input_root"
    ]
    return (
        "uv run python scripts/verify_p1_u04_host_authority.py "
        f"--evidence-cache {cache}"
    )


def _assert_keys(value: object, expected: set[str]) -> dict[str, object]:
    assert isinstance(value, dict)
    assert set(value) == expected
    return value


def _assert_valid(document: dict[str, object]) -> None:
    _assert_keys(
        document,
        {
            "authority_limits",
            "candidate_closure_sha256",
            "candidate_generation_id",
            "candidate_generation_sha256",
            "evidence",
            "evidence_sha256",
            "input_receipt_sha256s",
            "qualification_source_commit",
            "qualification_source_tree",
            "schema",
            "verdict",
        },
    )
    assert document["schema"] == "trading-agent-nautilus-u04-final-review/v1"
    assert document["verdict"] == "PASS"
    assert document["qualification_source_commit"] == SOURCE_COMMIT
    assert document["qualification_source_tree"] == SOURCE_TREE
    assert document["candidate_generation_id"] == "NT1231-U04-G1"
    assert document["candidate_generation_sha256"] == GENERATION_SHA256
    assert document["candidate_closure_sha256"] == CLOSURE_SHA256

    inputs = _assert_keys(
        document["input_receipt_sha256s"],
        {"candidate_generation", "u04_reproducibility", "u04_rollback_isolation"},
    )
    assert inputs == {
        "candidate_generation": _sha(GENERATION),
        "u04_reproducibility": _sha(UPGRADE / "u04-reproducibility-receipt.json"),
        "u04_rollback_isolation": _sha(UPGRADE / "u04-rollback-isolation-receipt.json"),
    }

    limits = _assert_keys(
        document["authority_limits"],
        {
            "candidate_active",
            "candidate_promoted",
            "live_authorized",
            "network_trading_authorized",
            "production_authorized",
        },
    )
    assert set(limits.values()) == {False}

    evidence = _assert_keys(
        document["evidence"],
        {
            "global_host",
            "p1_scoped_host",
            "p1_specific_host",
            "reviews",
            "runtime_release_host",
            "source_sha256s",
        },
    )
    assert document["evidence_sha256"] == hashlib.sha256(_canonical(evidence)).hexdigest()

    global_host = _assert_keys(
        evidence["global_host"],
        {
            "authority_status",
            "blockers",
            "conclusion",
            "head_sha",
            "job_id",
            "job_log_sha256",
            "run_attempt",
            "run_id",
            "run_url",
            "runner_labels",
            "workflow",
        },
    )
    assert global_host == {
        "authority_status": "DEFERRED",
        "blockers": [
            "EXT-DISPOSABLE-PG-GREEN",
            "EXT-DISPOSABLE-PG-RED",
            "EXT-DISPOSABLE-PG-RED-EVIDENCE",
        ],
        "conclusion": "failure",
        "head_sha": SOURCE_COMMIT,
        "job_id": 98943494030,
        "job_log_sha256": global_host["job_log_sha256"],
        "run_attempt": 1,
        "run_id": 33199025762,
        "run_url": "https://github.com/nam176hermes/Trading-Agent/actions/runs/33199025762",
        "runner_labels": ["self-hosted", "Linux", "X64", "trading-authority"],
        "workflow": "Host Authority",
    }
    assert isinstance(global_host["job_log_sha256"], str)
    assert len(global_host["job_log_sha256"]) == 64
    assert set(global_host["job_log_sha256"]) <= set("0123456789abcdef")
    assert global_host["job_log_sha256"] != "0" * 64

    scoped = _assert_keys(
        evidence["p1_scoped_host"],
        {
            "command",
            "external_outcomes",
            "foundation_head_sha",
            "foundation_run_id",
            "lane",
            "native_status",
            "outcome",
            "output_sha256",
            "portable_closure_status",
            "schema",
        },
    )
    assert scoped == {
        "command": (
            "GITHUB_RUN_ID=33199025762 uv run --frozen --offline python "
            "scripts/verify_p1_u04_host_authority.py "
            "--topology-evidence-root "
            "/tmp/trading-agent-host-authority.33199025762.1 "
            "--foundation-context-path "
            "/tmp/trading-agent-host-authority.33199025762.1/"
            "capability-topology/foundation-context.json"
        ),
        "external_outcomes": {
            "EXT-DISPOSABLE-PG-GREEN": "DEFERRED",
            "EXT-DISPOSABLE-PG-RED": "DEFERRED",
            "EXT-DISPOSABLE-PG-RED-EVIDENCE": "DEFERRED",
            "EXT-LEGACY-UV-AUTHORITY": "PASS",
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS": "PASS",
            "EXT-PHASE3B-CORPUS": "PASS",
        },
        "foundation_head_sha": SOURCE_COMMIT,
        "foundation_run_id": "33199025762",
        "lane": "P1_U04_HOST_TOPOLOGY",
        "native_status": "PASS",
        "outcome": "PASS",
        "output_sha256": scoped["output_sha256"],
        "portable_closure_status": "PASS",
        "schema": "p1-u04-host-topology-receipt-v1",
    }
    assert isinstance(scoped["output_sha256"], str)
    assert len(scoped["output_sha256"]) == 64
    assert set(scoped["output_sha256"]) <= set("0123456789abcdef")
    assert scoped["output_sha256"] != "0" * 64
    expected_scoped_output = (
        json.dumps(
            {
                key: value
                for key, value in scoped.items()
                if key not in {"command", "output_sha256"}
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    assert scoped["output_sha256"] == hashlib.sha256(expected_scoped_output).hexdigest()

    specific = _assert_keys(
        evidence["p1_specific_host"],
        {"command", "outcome", "output_sha256", "reason", "schema"},
    )
    assert specific == {
        "command": _p1_host_command(),
        "outcome": "PASS",
        "output_sha256": specific["output_sha256"],
        "reason": "HOST_TESTS_PASSED",
        "schema": "p1-u04-host-authority-receipt-v1",
    }
    expected_output = (
        b'{"lane":"HOST_EXTERNAL_AUTHORITY","outcome":"PASS",'
        b'"reason":"HOST_TESTS_PASSED",'
        b'"schema":"p1-u04-host-authority-receipt-v1"}\n'
    )
    assert specific["output_sha256"] == hashlib.sha256(expected_output).hexdigest()

    runtime_release = _assert_keys(
        evidence["runtime_release_host"],
        {"command", "outcome", "output_sha256", "passed", "skipped"},
    )
    assert runtime_release == {
        "command": "make test-runtime-release-host",
        "outcome": "PASS",
        "output_sha256": (
            "d1a264754af3f82605e55720e411ae299aa2b3fcf6c683a6f83f5d84969d18a5"
        ),
        "passed": 3,
        "skipped": 0,
    }

    source_sha256s = _assert_keys(
        evidence["source_sha256s"],
        {
            "acceptance_test",
            "generic_host_makefile",
            "generic_host_workflow",
            "p1_host_runner",
            "p1_host_tests",
            "p1_topology_test",
            "runtime_release_xattr_test",
        },
    )
    assert source_sha256s == {
        "acceptance_test": _sha(Path(__file__)),
        "generic_host_makefile": _sha(ROOT / "Makefile"),
        "generic_host_workflow": _sha(ROOT / ".github/workflows/host-authority.yml"),
        "p1_host_runner": _sha(ROOT / "scripts/verify_p1_u04_host_authority.py"),
        "p1_host_tests": _sha(
            ROOT / "tests/nautilus_upgrade/host_authority/p1_u04_host_authority.py"
        ),
        "p1_topology_test": _sha(
            ROOT / "tests/governance/test_p1_u04_host_topology.py"
        ),
        "runtime_release_xattr_test": _sha(ROOT / "tests/runtime_release/test_v2.py"),
    }

    reviews = evidence["reviews"]
    assert isinstance(reviews, list)
    assert reviews == [
        {
            "critical": 0,
            "important": 0,
            "minor": 0,
            "reviewer_id": "u04c2_spec_rereview",
            "role": "SPEC_COMPLIANCE",
            "verdict": "PASS",
        },
        {
            "critical": 0,
            "important": 0,
            "minor": 0,
            "reviewer_id": "u04c2_security_rereview",
            "role": "SECURITY_INTEGRITY",
            "verdict": "PASS",
        },
        {
            "critical": 0,
            "important": 0,
            "minor": 0,
            "reviewer_id": "u04c2_evidence_rereview",
            "role": "EVIDENCE_REPLAY",
            "verdict": "PASS",
        },
    ]


def test_u04_final_review_receipt_binds_one_passed_g1_review() -> None:
    document = _load_closed(RECEIPT)
    _assert_valid(document)


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("qualification_source_tree",), "0" * 40),
        (("candidate_generation_id",), "NT1231-U04-G2"),
        (("candidate_closure_sha256",), "0" * 64),
        (("evidence", "global_host", "authority_status"), "PASS"),
        (("evidence", "p1_scoped_host", "outcome"), "FAIL"),
        (("evidence", "p1_specific_host", "outcome"), "FAIL"),
        (("evidence", "runtime_release_host", "skipped"), 1),
        (("evidence", "reviews", "1", "important"), 1),
    ),
)
def test_u04_final_review_mutations_fail_closed(
    path: tuple[str, ...], value: object
) -> None:
    document = _load_closed(RECEIPT)
    mutated = deepcopy(document)
    target: object = mutated
    for key in path[:-1]:
        if isinstance(target, list):
            target = target[int(key)]
        else:
            assert isinstance(target, dict)
            target = target[key]
    assert isinstance(target, dict)
    target[path[-1]] = value
    with pytest.raises(AssertionError):
        _assert_valid(mutated)


def test_u04_final_review_requires_all_independent_reviewers() -> None:
    document = _load_closed(RECEIPT)
    mutated = deepcopy(document)
    evidence = mutated["evidence"]
    assert isinstance(evidence, dict)
    reviews = evidence["reviews"]
    assert isinstance(reviews, list)
    reviews.pop()
    with pytest.raises(AssertionError):
        _assert_valid(mutated)
