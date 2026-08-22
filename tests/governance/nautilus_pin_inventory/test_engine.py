"""Schema-v4 inventory engine controls using immutable in-memory snapshots."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from scripts.nautilus_pin_inventory.engine import PinInventoryEngine, PinInventoryError
from scripts.nautilus_pin_inventory.git_source import GitBlobSnapshot, GitTreeSnapshot
from scripts.nautilus_pin_inventory.model import (
    DynamicGovernedCheck,
    GovernedRelation,
    Observation,
    SourceSpan,
)
from scripts.nautilus_pin_inventory.registry import DEFAULT_REGISTRY


def _blob(path: str, data: bytes) -> GitBlobSnapshot:
    oid = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    return GitBlobSnapshot(path, 0o100644, oid, hashlib.sha256(data).hexdigest(), data)


def _sha256_blob(path: str, data: bytes) -> GitBlobSnapshot:
    oid = hashlib.sha256(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    return GitBlobSnapshot(path, 0o100644, oid, hashlib.sha256(data).hexdigest(), data)


def test_engine_serializes_a_canonical_v4_document_from_snapshot_only() -> None:
    """Break caught: generation reads a worktree or emits nondeterministic v4 bytes."""
    text = "".join(
        f"Nautilus {identity.family}: {identity.value}\n"
        for identity in DEFAULT_REGISTRY.allowed_identities
    ).encode("utf-8")
    snapshot = GitTreeSnapshot(
        commit_oid="b" * 40,
        tree_oid="c" * 40,
        object_format="sha1",
        blobs=(
            _blob("notes.txt", text),
            _blob("engines/nautilus/engine-build-policy.json", b"{}"),
            _blob("engines/nautilus/runtime-closure-policy.json", b"{}"),
        ),
    )

    engine = PinInventoryEngine()
    document = engine.generate(snapshot)
    serialized = engine.serialize(document)

    assert serialized.endswith(b"\n")
    assert b'"schema": "nautilus-pin-inventory/v4"' in serialized
    assert document.source_tree_oid == "c" * 40
    engine.verify(snapshot, serialized)


def _snapshot_for_relations() -> GitTreeSnapshot:
    """A complete in-memory snapshot; engine input never reaches this worktree."""
    root = Path(__file__).resolve().parents[3]
    identities = "".join(
        f"Nautilus {identity.family}: {identity.value}\n"
        for identity in DEFAULT_REGISTRY.allowed_identities
    ).encode("utf-8")
    paths = (
        "engines/nautilus/engine-build-policy.json",
        "engines/nautilus/runtime-closure-policy.json",
        "scripts/materialize_nautilus_runtime_closure.py",
        "services/job_worker/nautilus_closure.py",
    )
    return GitTreeSnapshot(
        commit_oid="a" * 40,
        tree_oid="b" * 40,
        object_format="sha1",
        blobs=tuple(_blob(path, (root / path).read_bytes()) for path in paths)
        + (_blob("evidence.txt", identities),),
    )


def test_engine_serializes_typed_relation_document_kinds_and_exact_v4_fields() -> None:
    """Break caught: cross-document relation provenance is silently lost in v4 bytes."""
    serialized = PinInventoryEngine().serialize(PinInventoryEngine().generate(_snapshot_for_relations()))
    parsed = json.loads(serialized)

    assert set(parsed) == {
        "schema", "threat_model", "source_tree_oid", "object_format", "entries",
        "dynamic_guards", "governed_relations",
    }
    assert parsed["schema"] == "nautilus-pin-inventory/v4"
    assert parsed["threat_model"] == "U00R_TRUSTED_HOST_COOPERATIVE_GIT_V1"
    assert parsed["governed_relations"]
    assert set(parsed["governed_relations"][0]) == {
        "id", "path", "source_blob_oid", "source_blob_sha256", "left_root",
        "left_document_kind", "left_field", "left_family", "operator", "right_root",
        "right_document_kind", "right_field", "right_family", "relation_kind",
        "binding_fingerprint", "syntax_fingerprint", "spans",
    }
    assert parsed["governed_relations"][0]["left_document_kind"].startswith("nautilus_")
    assert parsed["governed_relations"][0]["right_document_kind"].startswith("nautilus_")
    assert serialized == json.dumps(parsed, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def test_engine_rejects_dynamic_guard_id_collision() -> None:
    """Break caught: distinct guard fingerprints share the mandated guard ID unnoticed."""
    blob = _blob("guard.py", b"x\n")
    first = DynamicGovernedCheck(
        "guard.py", "policy", "source_commit", "==", "policy", "engine_upstream_commit",
        "syntax-a", SourceSpan.content("guard.py", 1, 1, 1, 2),
    )
    second = DynamicGovernedCheck(
        "guard.py", "policy", "source_commit", "==", "policy", "engine_upstream_commit",
        "syntax-b", SourceSpan.content("guard.py", 1, 1, 1, 2),
    )

    with pytest.raises(PinInventoryError, match="guard ID collision"):
        PinInventoryEngine()._guard_records(((blob, first), (blob, second)))


def test_engine_rejects_malformed_explicit_commit_oid() -> None:
    """Break caught: a snapshot claims a non-Git commit while its blobs look valid."""
    snapshot = replace(_snapshot_for_relations(), commit_oid="not-a-git-oid")

    with pytest.raises(PinInventoryError, match="snapshot commit OID is invalid"):
        PinInventoryEngine().generate(snapshot)


def test_engine_uses_compact_no_newline_canonical_id_preimages() -> None:
    """Break caught: a formatting newline changes the stable IDs for every record kind."""
    engine = PinInventoryEngine()
    entry = engine._entries((
        (_blob("pins.md", b"Nautilus engine_version: 1.227.0\n"), Observation(
            "engine_version", "1.227.0", SourceSpan.content("pins.md", 1, 27, 1, 34), "text"
        )),
    ))[0]
    guard = engine._guard_records((
        (_blob("guard.py", b"x\n"), DynamicGovernedCheck(
            "guard.py", "policy", "source_commit", "==", "policy", "engine_upstream_commit",
            "syntax-a", SourceSpan.content("guard.py", 1, 1, 1, 2),
        )),
    ))[0]
    relation = engine._relation_records((
        (_blob("relation.py", b"x\n"), GovernedRelation(
            "relation.py", "policy", "nautilus_runtime_closure_policy", "source_commit", "selected_source",
            "!=", "policy", "nautilus_runtime_closure_policy", "engine_upstream_commit", "upstream_commit",
            "cross_family_consistency_guard", "binding-a", "syntax-a", SourceSpan.content("relation.py", 1, 1, 1, 2),
        )),
    ))[0]

    assert entry.id == "PIN-B7AC64F62C5F241BEF12"
    assert guard.id == "GUARD-FAA7CDF59BBA6AD3C11A"
    assert relation.id == "REL-BF041945E72134DBF03D"


def test_engine_accepts_sha256_snapshots_and_rejects_tampered_blob_data() -> None:
    """Break caught: SHA-256 Git object receipts are skipped or mismatched data is trusted."""
    source = _snapshot_for_relations()
    snapshot = GitTreeSnapshot(
        commit_oid="a" * 64,
        tree_oid="b" * 64,
        object_format="sha256",
        blobs=tuple(_sha256_blob(blob.path, blob.data) for blob in source.blobs),
    )
    engine = PinInventoryEngine()
    document = engine.generate(snapshot)
    engine.verify(snapshot, engine.serialize(document))

    tampered = replace(snapshot, blobs=(replace(snapshot.blobs[0], data=b"tampered\n"),) + snapshot.blobs[1:])
    with pytest.raises(PinInventoryError, match="blob OID is inconsistent"):
        engine.generate(tampered)
