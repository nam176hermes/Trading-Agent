"""Immutable authority tests for NT1231-U04-G1."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, is_dataclass
import hashlib
import importlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
GENERATION_PATH = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/candidate-generations"
    / "NT1231-U04-G1.json"
)


def _module():  # type: ignore[no-untyped-def]
    return importlib.import_module("packages.nautilus_upgrade_authority.generation")


def _write(document: dict[str, object], path: Path) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _replace(document: dict[str, object], keys: tuple[str, ...], value: object) -> None:
    target: dict[str, object] = document
    for key in keys[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[keys[-1]] = value


def test_loads_exact_frozen_engine_neutral_generation() -> None:
    module = _module()
    generation = module.load_candidate_generation(GENERATION_PATH)

    assert is_dataclass(generation)
    assert generation.generation_id == "NT1231-U04-G1"
    assert generation.schema == "trading-agent-nautilus-candidate-generation/v1"
    assert generation.engine_identity.version == "1.231.0"
    assert generation.repository_source.build_commit == (
        "7aa1e69a40f1160174f9ef32c1d3ef056720e4b0"
    )
    assert generation.closure.manifest_sha256 == (
        "24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255"
    )
    assert generation.rollback.version == "1.227.0"
    assert generation.record_sha256 == hashlib.sha256(GENERATION_PATH.read_bytes()).hexdigest()
    assert all(value is False for value in asdict(generation.authority_limits).values())
    assert not any(isinstance(value, Path) for value in asdict(generation).values())
    with pytest.raises(FrozenInstanceError):
        generation.generation_id = "NT1231-U04-G2"


def test_generation_matches_committed_u04_receipts() -> None:
    document = json.loads(GENERATION_PATH.read_bytes())
    reproducibility = json.loads(
        (ROOT / "docs/implementation/p1-real-nautilus/upgrade/u04-reproducibility-receipt.json").read_bytes()
    )
    rollback = json.loads(
        (ROOT / "docs/implementation/p1-real-nautilus/upgrade/u04-rollback-isolation-receipt.json").read_bytes()
    )

    assert document["repository_source"] == {
        "build_commit": reproducibility["source"]["head"],
        "build_tree": reproducibility["source"]["tree"],
        "evidence_commit": "199d5ae65a2d1798f1c3aee9761ff763237fe910",
        "evidence_tree": "a093d41727b60b99cb57608a43854f87f69ac769",
    }
    assert document["artifact"]["wheel_sha256"] == reproducibility["build_a"]["wheel_sha256"]
    assert document["artifact"]["wheel_size"] == reproducibility["build_a"]["wheel_size"]
    assert document["artifact"]["artifact_manifest_sha256"] == (
        reproducibility["reproducibility"]["final_artifact_manifest_sha256"]
    )
    assert document["closure"]["manifest_sha256"] == (
        reproducibility["candidate_closure"]["closure_manifest_sha256"]
    )
    assert document["rollback"]["closure_sha256"] == (
        rollback["rollback_authority"]["closure_sha256"]
    )
    assert rollback["active_authority"]["active_policy_changes"] == 0
    assert rollback["active_authority"]["candidate_activation"] is False


@pytest.mark.parametrize(
    ("keys", "value"),
    (
        (("engine_identity", "version"), ">=1.231"),
        (("repository_source", "build_tree"), "0" * 40),
        (("repository_source", "evidence_tree"), "0" * 40),
        (("authority_digests", "x4_authority_receipt_sha256"), "0" * 64),
        (("artifact", "wheel_sha256"), "0" * 64),
        (("artifact", "wheel_size"), 1),
        (("artifact", "native_object_count"), 0),
        (("closure", "regular_file_count"), 87),
        (("closure", "symlink_count"), 1),
        (("rollback", "version"), "latest"),
        (("authority_limits", "candidate_active"), True),
    ),
)
def test_load_bearing_mutations_fail_closed(
    tmp_path: Path,
    keys: tuple[str, ...],
    value: object,
) -> None:
    module = _module()
    document = json.loads(GENERATION_PATH.read_bytes())
    _replace(document, keys, value)
    mutated = tmp_path / "NT1231-U04-G1.json"
    _write(document, mutated)

    with pytest.raises(module.CandidateGenerationError):
        module.load_candidate_generation(mutated)


def test_extra_missing_duplicate_and_noncanonical_keys_fail_closed(tmp_path: Path) -> None:
    module = _module()
    document = json.loads(GENERATION_PATH.read_bytes())

    extra = tmp_path / "extra.json"
    _write({**document, "branch": "main"}, extra)
    with pytest.raises(module.CandidateGenerationError):
        module.load_candidate_generation(extra)

    missing_document = dict(document)
    del missing_document["rollback"]
    missing = tmp_path / "missing.json"
    _write(missing_document, missing)
    with pytest.raises(module.CandidateGenerationError):
        module.load_candidate_generation(missing)

    raw = GENERATION_PATH.read_bytes()
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(
        raw.replace(
            b"{\n",
            b'{\n  "schema": "trading-agent-nautilus-candidate-generation/v1",\n',
            1,
        )
    )
    with pytest.raises(module.CandidateGenerationError, match="duplicate"):
        module.load_candidate_generation(duplicate)

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_bytes(b" " + raw)
    with pytest.raises(module.CandidateGenerationError, match="canonical"):
        module.load_candidate_generation(noncanonical)

    nonfinite_document = json.loads(GENERATION_PATH.read_bytes())
    nonfinite_artifact = nonfinite_document["artifact"]
    assert isinstance(nonfinite_artifact, dict)
    nonfinite_artifact["wheel_size"] = float("nan")
    nonfinite = tmp_path / "nonfinite.json"
    _write(nonfinite_document, nonfinite)
    with pytest.raises(module.CandidateGenerationError, match="float"):
        module.load_candidate_generation(nonfinite)


def test_generation_contains_no_moving_or_path_authority() -> None:
    document = json.loads(GENERATION_PATH.read_bytes())
    serialized = json.dumps(document, sort_keys=True)
    forbidden = (
        "branch",
        "latest",
        "path",
        "range",
        "rebaseline_parent",
    )
    assert all(term not in serialized for term in forbidden)
