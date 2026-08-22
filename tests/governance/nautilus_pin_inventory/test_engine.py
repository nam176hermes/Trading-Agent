"""Schema-v4 inventory engine controls using immutable in-memory snapshots."""

from __future__ import annotations

import builtins
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import random
import secrets
import socket
import subprocess
import time

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
    monkeypatch.setattr(os, "urandom", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(random, "random", forbidden)
    monkeypatch.setattr(secrets, "token_bytes", forbidden)
    engine = PinInventoryEngine()
    document = engine.generate(snapshot)
    engine.verify(snapshot, engine.serialize(document))


def test_engine_generates_from_the_exact_current_commit_source() -> None:
    """Break caught: the exact reviewed source tree cannot be inventoried end-to-end."""
    root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, text=True, capture_output=True, check=True)
    snapshot = GitTreeSnapshot.from_commit(root, completed.stdout.strip())

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


@pytest.mark.parametrize(
    ("path", "data"),
    (
        ("notes.txt", b"engine_version: 9.999.0\n"),
        ("notes.txt", b"upstream_commit: ffffffffffffffffffffffffffffffffffffffff\n"),
        ("ordinary.py", b'value = "engine_version: 9.999.0"\n'),
        ("ordinary.py", b"# upstream_commit: ffffffffffffffffffffffffffffffffffffffff\n"),
    ),
)
def test_engine_rejects_every_generic_registry_unknown_in_notes_and_python_payloads(path: str, data: bytes) -> None:
    """Break caught: a registry-governed unknown is discarded without extra spelling."""
    snapshot = replace(_complete_snapshot(), blobs=_complete_snapshot().blobs + (_blob(path, data),))
    with pytest.raises(PinInventoryError, match="unregistered governed identity"):
        PinInventoryEngine().generate(snapshot)


def test_engine_treats_test_python_mutation_payloads_as_non_governing() -> None:
    """Break caught: an intentional test mutation becomes source inventory authority."""
    snapshot = replace(
        _complete_snapshot(),
        blobs=_complete_snapshot().blobs + (_blob("tests/mutation_fixture.py", b'assert "engine_version: 9.999.0"\n'),),
    )
    PinInventoryEngine().generate(snapshot)


def test_engine_rejects_pin_and_relation_id_collisions() -> None:
    """Break caught: distinct source receipts share a stable PIN or REL identity."""
    engine = PinInventoryEngine()
    first = _blob("same.py", b"one")
    second = _blob("same.py", b"two")
    observation = Observation("engine_version", "1.227.0", SourceSpan.content("same.py", 1, 1, 1, 2), "text")
    with pytest.raises(PinInventoryError, match="pin inventory ID collision"):
        engine._entries(((first, observation), (second, observation)))

    relation = GovernedRelation(
        "same.py", "policy", "nautilus_runtime_closure_policy", "source_commit", "selected_source",
        "!=", "policy", "nautilus_runtime_closure_policy", "engine_upstream_commit", "upstream_commit",
        "cross_family_consistency_guard", "binding", "syntax", SourceSpan.content("same.py", 1, 1, 1, 2),
    )
    with pytest.raises(PinInventoryError, match="governed relation ID collision"):
        engine._relation_records(((first, relation), (second, relation)))


@pytest.mark.parametrize("identity", DEFAULT_REGISTRY.allowed_identities)
def test_engine_rejects_mutation_of_each_registered_literal(identity) -> None:
    """Break caught: a changed allowed identity remains valid inventory evidence."""
    evidence = "".join(
        f"Nautilus {item.family}: {item.value if item != identity else item.value + 'x'}\n"
        for item in DEFAULT_REGISTRY.allowed_identities
    ).encode("utf-8")
    with pytest.raises(PinInventoryError):
        PinInventoryEngine().generate(_replace_blob(_complete_snapshot(), "evidence.txt", evidence))


def test_engine_rejects_independent_blob_sha256_tamper_and_is_repeatable_across_blob_order() -> None:
    """Break caught: SHA-256 metadata or snapshot input order changes trusted output."""
    engine = PinInventoryEngine()
    source = _complete_snapshot()
    expected = engine.serialize(engine.generate(source))
    assert engine.serialize(engine.generate(replace(source, blobs=tuple(reversed(source.blobs))))) == expected
    tampered = replace(source, blobs=(replace(source.blobs[0], sha256="0" * 64),) + source.blobs[1:])
    with pytest.raises(PinInventoryError, match="SHA-256"):
        engine.generate(tampered)


def test_engine_verify_rejects_nested_record_unknown_field_and_wrong_type() -> None:
    """Break caught: a canonical-looking nested v4 record can bypass verification."""
    engine = PinInventoryEngine()
    snapshot = _complete_snapshot()
    parsed = json.loads(engine.serialize(engine.generate(snapshot)))
    parsed["entries"][0]["unknown"] = True
    unknown = json.dumps(parsed, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    with pytest.raises(PinInventoryError):
        engine.verify(snapshot, unknown)
    parsed["entries"][0].pop("unknown")
    parsed["entries"][0]["spans"] = {}
    wrong_type = json.dumps(parsed, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    with pytest.raises(PinInventoryError):
        engine.verify(snapshot, wrong_type)


class _StaticJson:
    def __init__(self, observations: tuple[Observation, ...]) -> None:
        self._observations = observations

    def extract(self, path: str, text: str) -> tuple[Observation, ...]:
        return self._observations if path.endswith("engine-build-policy.json") else ()


def test_engine_rejects_conflicting_specialized_and_generic_ownership() -> None:
    """Break caught: two extractors claim one source coordinate with different meanings."""
    path = "engines/nautilus/engine-build-policy.json"
    text = '{"engine_version":"1.227.0"}\n'
    generic = PinInventoryEngine()._text.extract_content(path, text)
    assert len(generic) == 1
    conflicting = Observation("upstream_commit", "1.227.0", generic[0].span, "json")
    engine = PinInventoryEngine()
    object.__setattr__(engine, "_json", _StaticJson((conflicting,)))
    with pytest.raises(PinInventoryError, match="specialized and generic"):
        engine.generate(_replace_blob(_complete_snapshot(), path, text.encode("utf-8")))


def test_engine_rejects_conflicting_specialized_ownership() -> None:
    """Break caught: two specialized claims share a coordinate without rejection."""
    path = "engines/nautilus/engine-build-policy.json"
    span = SourceSpan.content(path, 1, 1, 1, 2)
    engine = PinInventoryEngine()
    object.__setattr__(engine, "_json", _StaticJson((
        Observation("engine_version", "1.227.0", span, "json"),
        Observation("upstream_commit", "280ae1762df51a492a4ce71506a40b5c8706def5", span, "json"),
    )))
    with pytest.raises(PinInventoryError, match="conflicting specialized"):
        engine.generate(_complete_snapshot())


def test_engine_bytes_are_identical_across_python_hash_seeds() -> None:
    """Break caught: process hash randomization changes canonical inventory bytes."""
    script = "\n".join((
        "import hashlib",
        "from scripts.nautilus_pin_inventory.engine import PinInventoryEngine",
        "from scripts.nautilus_pin_inventory.git_source import GitBlobSnapshot, GitTreeSnapshot",
        "from scripts.nautilus_pin_inventory.registry import DEFAULT_REGISTRY",
        "def blob(path, data): return GitBlobSnapshot(path, 0o100644, hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\\0' + data).hexdigest(), hashlib.sha256(data).hexdigest(), data)",
        "evidence = ''.join(f'Nautilus {i.family}: {i.value}\\n' for i in DEFAULT_REGISTRY.allowed_identities).encode()",
        "snapshot = GitTreeSnapshot('a' * 40, 'b' * 40, 'sha1', (blob('evidence.txt', evidence), blob('engines/nautilus/engine-build-policy.json', b'{}'), blob('engines/nautilus/runtime-closure-policy.json', b'{}'), blob('engines/nautilus/llvm-toolchain-policy.json', b'{}'), blob('engines/nautilus/wheel-cache-policy.json', b'{}')))",
        "engine = PinInventoryEngine()",
        "print(engine.serialize(engine.generate(snapshot)).hex())",
    ))
    outputs = []
    for seed in ("1", "777"):
        completed = subprocess.run(
            ["uv", "run", "python", "-c", script],
            cwd=Path(__file__).resolve().parents[3],
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            capture_output=True,
            check=True,
        )
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize(
    ("path", "data"),
    (
        (
            "services/pins.py",
            b'known = "engine_version: 1.227.0"\nvalue = "engine_version: 9.999.0"\n',
        ),
        (
            "services/pins.py",
            b'# engine_version: 1.227.0\n# engine_version: 9.999.0\n',
        ),
        (
            "config/pins.toml",
            b"engine_version = '1.227.0'\nengine_version = '9.999.0'\n",
        ),
        (
            "notes.md",
            b"engine_version: 1.227.0\nengine_version: 9.999.0\n",
        ),
    ),
)
def test_engine_rejects_unknown_mutations_on_production_governing_carriers(path: str, data: bytes) -> None:
    """Break caught: a production carrier records a known pin but drops its unknown mutation."""
    snapshot = replace(_complete_snapshot(), blobs=_complete_snapshot().blobs + (_blob(path, data),))

    with pytest.raises(PinInventoryError, match="unregistered governed identity"):
        PinInventoryEngine().generate(snapshot)


def test_engine_ignores_python_code_tokens_but_bounds_test_fixture_exemption_to_tests_prefix() -> None:
    """Break caught: code identifiers govern pins, or a test-only exemption leaks into production."""
    ordinary_code = replace(
        _complete_snapshot(),
        blobs=_complete_snapshot().blobs + (_blob("services/pins.py", b"engine_version = dynamic_value\n"),),
    )
    PinInventoryEngine().generate(ordinary_code)

    fixture = replace(
        _complete_snapshot(),
        blobs=_complete_snapshot().blobs + (_blob("tests/mutation_fixture.py", b'value = "engine_version: 9.999.0"\n'),),
    )
    PinInventoryEngine().generate(fixture)

    production = replace(
        _complete_snapshot(),
        blobs=_complete_snapshot().blobs + (_blob("services/mutation_fixture.py", b'value = "engine_version: 9.999.0"\n'),),
    )
    with pytest.raises(PinInventoryError, match="unregistered governed identity"):
        PinInventoryEngine().generate(production)


def test_engine_verify_rejects_semantically_exact_minified_inventory_bytes() -> None:
    """Break caught: verify accepts the exact document after formatting-only canonical-byte drift."""
    engine = PinInventoryEngine()
    snapshot = _complete_snapshot()
    canonical = engine.serialize(engine.generate(snapshot))
    minified = json.dumps(json.loads(canonical), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    assert minified != canonical

    with pytest.raises(PinInventoryError, match="noncanonical"):
        engine.verify(snapshot, minified)


def test_engine_groups_guard_and_relation_spans_in_sorted_deduplicated_records() -> None:
    """Break caught: GUARD or REL records preserve duplicate or input-ordered source spans."""
    engine = PinInventoryEngine()
    guard_blob = _blob("guard.py", b"x\ny\n")
    relation_blob = _blob("relation.py", b"x\ny\n")
    guard_one = DynamicGovernedCheck(
        "guard.py", "policy", "source_commit", "==", "policy", "engine_upstream_commit",
        "syntax", SourceSpan.content("guard.py", 2, 1, 2, 2),
    )
    guard_two = replace(guard_one, span=SourceSpan.content("guard.py", 1, 1, 1, 2))
    relation_one = GovernedRelation(
        "relation.py", "policy", "nautilus_runtime_closure_policy", "source_commit", "selected_source",
        "!=", "policy", "nautilus_runtime_closure_policy", "engine_upstream_commit", "upstream_commit",
        "cross_family_consistency_guard", "binding", "syntax", SourceSpan.content("relation.py", 2, 1, 2, 2),
    )
    relation_two = replace(relation_one, span=SourceSpan.content("relation.py", 1, 1, 1, 2))

    guards = engine._guard_records(((guard_blob, guard_one), (guard_blob, guard_two), (guard_blob, guard_two)))
    relations = engine._relation_records(((relation_blob, relation_one), (relation_blob, relation_two), (relation_blob, relation_two)))

    assert guards[0].spans == (
        SourceSpan.content("guard.py", 1, 1, 1, 2), SourceSpan.content("guard.py", 2, 1, 2, 2),
    )
    assert relations[0].spans == (
        SourceSpan.content("relation.py", 1, 1, 1, 2), SourceSpan.content("relation.py", 2, 1, 2, 2),
    )


def test_engine_treats_python_mapping_literal_values_as_non_governing_metadata() -> None:
    """Break caught: an ordinary Python schema mapping is mistaken for a pin declaration."""
    snapshot = replace(
        _complete_snapshot(),
        blobs=_complete_snapshot().blobs + (_blob(
            "packages/schema.py",
            b'schema = {"validator": "packages.deployment_evidence.DeploymentEvidence"}\n',
        ),),
    )

    document = PinInventoryEngine().generate(snapshot)

    assert not any(entry.path == "packages/schema.py" for entry in document.entries)


def test_engine_rejects_unknown_only_nested_text_carriers() -> None:
    """Break caught: a nested text carrier becomes non-governing when its only pin is unknown."""
    source = _complete_snapshot()
    snapshot = replace(
        source,
        blobs=source.blobs + (_blob("config/pins.txt", b"engine_version: 9.999.0\n"),),
    )

    with pytest.raises(PinInventoryError, match="unregistered governed identity"):
        PinInventoryEngine().generate(snapshot)


def test_engine_excludes_all_generic_test_python_observations_from_authority() -> None:
    """Break caught: a test Python literal creates an entry or supplies a required identity."""
    source = _complete_snapshot()
    fixture = _blob("tests/only_required_identity.py", b'value = "engine_version: 1.231.0"\n')
    document = PinInventoryEngine().generate(replace(source, blobs=source.blobs + (fixture,)))
    assert not any(entry.path == "tests/only_required_identity.py" for entry in document.entries)

    evidence = b"".join(
        f"Nautilus {identity.family}: {identity.value}\n".encode("utf-8")
        for identity in DEFAULT_REGISTRY.allowed_identities
        if (identity.family, identity.value) != ("engine_version", "1.231.0")
    )
    without_identity = _replace_blob(source, "evidence.txt", evidence)
    missing = replace(without_identity, blobs=without_identity.blobs + (fixture,))
    with pytest.raises(PinInventoryError, match="required identity is missing: engine_version=1.231.0"):
        PinInventoryEngine().generate(missing)
