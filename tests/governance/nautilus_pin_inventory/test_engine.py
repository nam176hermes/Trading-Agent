"""Schema-v4 inventory engine controls using immutable in-memory snapshots."""

from __future__ import annotations

import builtins
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts.nautilus_pin_inventory.engine import PinInventoryEngine, PinInventoryError
from scripts.nautilus_pin_inventory.git_source import GitBlobSnapshot, GitTreeSnapshot
from scripts.nautilus_pin_inventory.json_extractor import GOVERNED_JSON_PATHS
from scripts.nautilus_pin_inventory.model import (
    DynamicGovernedCheck,
    GovernedRelation,
    Observation,
    SourceSpan,
)
from scripts.nautilus_pin_inventory.registry import DEFAULT_REGISTRY, Registry


def _blob(path: str, data: bytes) -> GitBlobSnapshot:
    oid = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    return GitBlobSnapshot(path, 0o100644, oid, hashlib.sha256(data).hexdigest(), data)


def _sha256_blob(path: str, data: bytes) -> GitBlobSnapshot:
    oid = hashlib.sha256(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    return GitBlobSnapshot(path, 0o100644, oid, hashlib.sha256(data).hexdigest(), data)


def _complete_snapshot(*extra: GitBlobSnapshot) -> GitTreeSnapshot:
    """A hand-built complete receipt, intentionally independent of the worktree."""
    evidence = "".join(
        f"Nautilus {identity.family}: {identity.value}\n"
        for identity in DEFAULT_REGISTRY.allowed_identities
    ).encode("utf-8")
    policies = (
        _blob("engines/nautilus/README.md", b"engine-build-policy.json llvm-toolchain-policy.json wheel-cache-policy.json runtime-closure-policy.json\n"),
        _blob("engines/nautilus/engine-build-policy.json", b"{}\n"),
        _blob("engines/nautilus/runtime-closure-policy.json", b"{}\n"),
        _blob("engines/nautilus/llvm-toolchain-policy.json", b"{}\n"),
        _blob("engines/nautilus/wheel-cache-policy.json", b"{}\n"),
    )
    return GitTreeSnapshot("a" * 40, "b" * 40, "sha1", policies + (_blob("evidence.txt", evidence),) + extra)


def _replace_blob(snapshot: GitTreeSnapshot, path: str, data: bytes) -> GitTreeSnapshot:
    return replace(snapshot, blobs=tuple(_blob(path, data) if blob.path == path else blob for blob in snapshot.blobs))


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
            _blob("engines/nautilus/llvm-toolchain-policy.json", b"{}"),
            _blob("engines/nautilus/wheel-cache-policy.json", b"{}"),
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
        "engines/nautilus/llvm-toolchain-policy.json",
        "engines/nautilus/wheel-cache-policy.json",
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


@pytest.mark.parametrize(
    ("path", "data"),
    (
        ("unknown.py", b"# Nautilus engine_version: 9.999.0\n"),
        ("engines/nautilus/engine-build-policy.json", b'{"note":"Nautilus engine_version: 9.999.0"}\n'),
    ),
)
def test_engine_rejects_unregistered_identity_outside_specialized_ownership(path: str, data: bytes) -> None:
    """Break caught: specialized extraction erases an unrelated unknown literal."""
    snapshot = _replace_blob(_complete_snapshot(), path, data) if path.endswith("policy.json") else replace(
        _complete_snapshot(), blobs=_complete_snapshot().blobs + (_blob(path, data),)
    )

    with pytest.raises(PinInventoryError, match="unregistered governed identity"):
        PinInventoryEngine().generate(snapshot)


def test_engine_requires_every_readme_policy_to_have_the_explicit_scan_route() -> None:
    """Break caught: an existing README policy is accepted without an auditable scan route."""
    assert GOVERNED_JSON_PATHS == frozenset({
        "engines/nautilus/engine-build-policy.json",
        "engines/nautilus/runtime-closure-policy.json",
        "engines/nautilus/llvm-toolchain-policy.json",
        "engines/nautilus/wheel-cache-policy.json",
    })
    source = _complete_snapshot()
    snapshot = replace(
        source,
        blobs=tuple(
            _blob(blob.path, b"extra-policy.json\n") if blob.path.endswith("README.md") else blob
            for blob in source.blobs
        ) + (_blob("engines/nautilus/extra-policy.json", b"{}\n"),),
    )

    with pytest.raises(PinInventoryError, match="policy is not scanned"):
        PinInventoryEngine().generate(snapshot)


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    (
        (lambda: GitTreeSnapshot("a" * 40, "b" * 40, "sha1", list(_complete_snapshot().blobs)), "tuple"),
        (lambda: replace(_complete_snapshot(), blobs=_complete_snapshot().blobs + (_blob("../escape.bin", b"x"),)), "path"),
        (lambda: replace(_complete_snapshot(), blobs=_complete_snapshot().blobs + (_blob("évil.txt", b"Nautilus engine_version: 9.999.0\n"),)), "path"),
    ),
)
def test_engine_rejects_nonimmutable_or_unscannable_snapshot_paths(snapshot, reason: str) -> None:
    """Break caught: a snapshot path/container bypasses the extractor's path universe."""
    with pytest.raises(PinInventoryError, match=reason):
        PinInventoryEngine().generate(snapshot())


def test_engine_rejects_unscanned_policy_and_eligible_decode_failure_but_keeps_binary_path_only() -> None:
    """Break caught: policy parsing or eligible UTF-8 validation is skipped for a blob."""
    bad_policy = _replace_blob(_complete_snapshot(), "engines/nautilus/llvm-toolchain-policy.json", b"{duplicate:}\n")
    with pytest.raises(Exception):
        PinInventoryEngine().generate(bad_policy)

    bad_text = replace(_complete_snapshot(), blobs=_complete_snapshot().blobs + (_blob("bad.md", b"\xff"),))
    with pytest.raises(PinInventoryError, match="strict UTF-8"):
        PinInventoryEngine().generate(bad_text)

    binary = replace(_complete_snapshot(), blobs=_complete_snapshot().blobs + (_blob("engines/nautilus/v1.231.0/image.bin", b"\xff"),))
    document = PinInventoryEngine().generate(binary)
    assert any(entry.path.endswith("image.bin") and entry.carrier == "PATH" for entry in document.entries)


@pytest.mark.parametrize("bad_bytes", (
    b"[]\n",
    b'{"schema":"nautilus-pin-inventory/v4","schema":"nautilus-pin-inventory/v4"}\n',
    b'\xef\xbb\xbf{}\n',
    b'\xff',
    b'{"unknown":null}\n',
    b'{ "schema": null }\n',
))
def test_engine_verify_rejects_schema_and_canonical_byte_violations(bad_bytes: bytes) -> None:
    """Break caught: parseable but malformed/noncanonical inventory bytes are accepted."""
    with pytest.raises(PinInventoryError):
        PinInventoryEngine().verify(_complete_snapshot(), bad_bytes)


@pytest.mark.parametrize("removed", DEFAULT_REGISTRY.allowed_identities)
def test_engine_requires_each_registered_identity_as_a_literal(removed) -> None:
    """Break caught: one registered identity can disappear without failing generation."""
    evidence = "".join(
        f"Nautilus {identity.family}: {identity.value}\n"
        for identity in DEFAULT_REGISTRY.allowed_identities if identity != removed
    ).encode("utf-8")
    source = _complete_snapshot()
    snapshot = _replace_blob(source, "evidence.txt", evidence)

    with pytest.raises(PinInventoryError, match="required identity is missing"):
        PinInventoryEngine().generate(snapshot)


def test_engine_is_repeatable_under_reordered_registry_input_and_never_reads_the_host(monkeypatch) -> None:
    """Break caught: input ordering or post-receipt host access changes inventory bytes."""
    snapshot = _complete_snapshot()
    reordered = Registry(
        family_specs=tuple(reversed(DEFAULT_REGISTRY.family_specs)),
        allowed_identities=tuple(reversed(DEFAULT_REGISTRY.allowed_identities)),
    )
    expected = PinInventoryEngine().serialize(PinInventoryEngine().generate(snapshot))
    assert PinInventoryEngine(reordered).serialize(PinInventoryEngine(reordered).generate(snapshot)) == expected

    def forbidden(*_args, **_kwargs):
        raise AssertionError("engine accessed the host after receiving a snapshot")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    engine = PinInventoryEngine()
    document = engine.generate(snapshot)
    engine.verify(snapshot, engine.serialize(document))


def test_engine_generates_from_the_exact_current_commit_source() -> None:
    """Break caught: the exact reviewed source tree cannot be inventoried end-to-end."""
    root = Path(__file__).resolve().parents[3]
    snapshot = GitTreeSnapshot.from_commit(root, "b614454a7d039508b6c92e8e0938250c2c90414d")

    document = PinInventoryEngine().generate(snapshot)
    PinInventoryEngine().verify(snapshot, PinInventoryEngine().serialize(document))


def test_engine_emits_exact_entry_and_guard_field_sets_with_sorted_deduplicated_spans() -> None:
    """Break caught: a v4 record drops a field or emits duplicate/unstable evidence spans."""
    document = PinInventoryEngine().generate(_snapshot_for_relations())
    parsed = json.loads(PinInventoryEngine().serialize(document))
    assert set(parsed["entries"][0]) == {
        "id", "path", "source_blob_oid", "source_blob_sha256", "carrier", "family",
        "value", "role", "syntax", "spans",
    }
    assert set(parsed["dynamic_guards"][0]) == {
        "id", "path", "source_blob_oid", "source_blob_sha256", "left_root", "left_field",
        "operator", "right_root", "right_field", "syntax_fingerprint", "spans",
    }
    entry = PinInventoryEngine()._entries((
        (_blob("pins.md", b"x"), Observation("engine_version", "1.227.0", SourceSpan.content("pins.md", 2, 1, 2, 8), "text")),
        (_blob("pins.md", b"x"), Observation("engine_version", "1.227.0", SourceSpan.content("pins.md", 1, 1, 1, 8), "text")),
        (_blob("pins.md", b"x"), Observation("engine_version", "1.227.0", SourceSpan.content("pins.md", 1, 1, 1, 8), "text")),
    ))
    assert len(entry) == 1
    assert entry[0].spans == (
        SourceSpan.content("pins.md", 1, 1, 1, 8), SourceSpan.content("pins.md", 2, 1, 2, 8),
    )


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    (
        (lambda: replace(_complete_snapshot(), object_format="sha512"), "object format"),
        (lambda: replace(_complete_snapshot(), tree_oid="z" * 40), "tree OID"),
        (lambda: replace(_complete_snapshot(), blobs=_complete_snapshot().blobs + (_complete_snapshot().blobs[0],)), "duplicated"),
        (lambda: replace(_complete_snapshot(), blobs=(replace(_complete_snapshot().blobs[0], mode=0o120000),) + _complete_snapshot().blobs[1:]), "non-regular"),
    ),
)
def test_engine_rejects_invalid_snapshot_receipt_shapes(snapshot, reason: str) -> None:
    """Break caught: invalid snapshot metadata/tree/blob receipts reach extraction."""
    with pytest.raises(PinInventoryError, match=reason):
        PinInventoryEngine().generate(snapshot())


def test_engine_rejects_a_missing_mapped_policy() -> None:
    """Break caught: a required governed policy can disappear without detection."""
    snapshot = _complete_snapshot()
    without_llvm = replace(snapshot, blobs=tuple(blob for blob in snapshot.blobs if blob.path != "engines/nautilus/llvm-toolchain-policy.json"))
    with pytest.raises(PinInventoryError, match="governed policy is absent"):
        PinInventoryEngine().generate(without_llvm)
