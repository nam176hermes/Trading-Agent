"""Deterministic schema-v4 inventory generation from immutable Git snapshots."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
from io import StringIO
import json
import re
import tokenize
from typing import Iterable

from .git_source import GitBlobSnapshot, GitTreeSnapshot
from .json_extractor import GOVERNED_JSON_PATHS, JsonExtractor
from .model import Carrier, GovernedRelation, Observation, SourceSpan
from .python_extractor import PythonExtractor
from .registry import DEFAULT_REGISTRY, Registry
from .text_extractor import TextAndPathExtractor


INVENTORY_PATH = "docs/implementation/p1-real-nautilus/upgrade/pin-inventory.json"
_TEXT_SUFFIXES = frozenset({
    ".cfg", ".csv", ".css", ".go", ".html", ".ini", ".js", ".json", ".lock",
    ".md", ".py", ".rs", ".sh", ".toml", ".ts", ".tsv", ".tsx", ".txt", ".yaml", ".yml",
})
_TEXT_NAMES = frozenset({"Makefile", "Dockerfile"})
_INTRINSIC_GOVERNING_SUFFIXES = frozenset({".cfg", ".ini", ".lock", ".py", ".sh", ".toml", ".yaml", ".yml"})
_POLICY_REFERENCE = re.compile(r"(?P<name>[A-Za-z0-9_.-]+-policy\.json)")
_GOVERNED_PYTHON_PATHS = frozenset({
    "scripts/materialize_nautilus_runtime_closure.py",
    "services/job_worker/nautilus_closure.py",
})


class PinInventoryError(ValueError):
    """The immutable source snapshot cannot produce a valid v4 inventory."""


def _canonical(value: object, *, indent: int | None = None) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=indent, separators=None if indent is not None else (",", ":")) + "\n").encode("utf-8")


def _compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git_oid(data: bytes, object_format: str) -> str:
    digest = hashlib.new(object_format)
    digest.update(b"blob " + str(len(data)).encode("ascii") + b"\0" + data)
    return digest.hexdigest()


def _is_oid(value: object, width: int) -> bool:
    return type(value) is str and len(value) == width and all(character in "0123456789abcdef" for character in value)


def _is_scannable_path(path: object) -> bool:
    if type(path) is not str or not path or not path.isascii() or len(path.encode("utf-8")) > 4_096:
        return False
    if path.startswith("/") or "\\" in path or any(ord(character) < 0x20 or ord(character) == 0x7f for character in path):
        return False
    return all(part and part not in (".", "..") for part in path.split("/"))


def _python_payload_spans(path: str, text: str) -> tuple[SourceSpan, ...]:
    try:
        tokens = tokenize.generate_tokens(StringIO(text).readline)
        return tuple(
            SourceSpan.content(path, token.start[0], token.start[1] + 1, token.end[0], token.end[1] + 1)
            for token in tokens if token.type in (tokenize.COMMENT, tokenize.STRING)
        )
    except (tokenize.TokenError, IndentationError) as exc:
        raise PinInventoryError("Python text extraction is invalid") from exc


def _python_ast_span(path: str, text: str, node: ast.AST) -> SourceSpan:
    if not isinstance(node, ast.expr) or node.end_lineno is None or node.end_col_offset is None:
        raise PinInventoryError("Python metadata position is invalid")
    lines = text.splitlines()

    def column(line_number: int, byte_offset: int) -> int:
        try:
            return len(lines[line_number - 1].encode("utf-8")[:byte_offset].decode("utf-8")) + 1
        except (IndexError, UnicodeDecodeError) as exc:
            raise PinInventoryError("Python metadata position is invalid") from exc

    return SourceSpan.content(
        path,
        node.lineno,
        column(node.lineno, node.col_offset),
        node.end_lineno,
        column(node.end_lineno, node.end_col_offset),
    )


def _python_mapping_value_spans(path: str, text: str) -> tuple[SourceSpan, ...]:
    """Return literal mapping values, which are code metadata rather than pin declarations."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ()
    spans: list[SourceSpan] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            spans.extend(
                _python_ast_span(path, text, value)
                for value in node.values
                if isinstance(value, ast.Constant) and type(value.value) is str
            )
    return tuple(spans)


def _markdown_prose_spans(path: str, text: str) -> tuple[SourceSpan, ...]:
    """Return Markdown prose lines; fenced examples are non-authoritative data."""
    spans: list[SourceSpan] = []
    fence: str | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in ("```", "~~~"):
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is None and line:
            spans.append(SourceSpan.content(path, line_number, 1, line_number, len(line) + 1))
    return tuple(spans)


def _contains(container: SourceSpan, value: SourceSpan) -> bool:
    return (
        container.path == value.path
        and container.carrier is value.carrier
        and (container.start_line, container.start_column) <= (value.start_line, value.start_column)
        and (value.end_line, value.end_column) <= (container.end_line, container.end_column)
    )


@dataclass(frozen=True)
class PinInventoryEntry:
    id: str
    path: str
    source_blob_oid: str
    source_blob_sha256: str
    carrier: str
    family: str
    value: str
    role: str
    syntax: str
    spans: tuple[SourceSpan, ...]


@dataclass(frozen=True)
class DynamicGuardRecord:
    id: str
    path: str
    source_blob_oid: str
    source_blob_sha256: str
    left_root: str
    left_field: str
    operator: str
    right_root: str
    right_field: str
    syntax_fingerprint: str
    spans: tuple[SourceSpan, ...]


@dataclass(frozen=True)
class GovernedRelationRecord:
    id: str
    path: str
    source_blob_oid: str
    source_blob_sha256: str
    left_root: str
    left_document_kind: str
    left_field: str
    left_family: str
    operator: str
    right_root: str
    right_document_kind: str
    right_field: str
    right_family: str
    relation_kind: str
    binding_fingerprint: str
    syntax_fingerprint: str
    spans: tuple[SourceSpan, ...]


@dataclass(frozen=True)
class PinInventoryDocument:
    schema: str
    threat_model: str
    source_tree_oid: str
    object_format: str
    entries: tuple[PinInventoryEntry, ...]
    dynamic_guards: tuple[DynamicGuardRecord, ...]
    governed_relations: tuple[GovernedRelationRecord, ...]


def _span_key(span: SourceSpan) -> tuple[object, ...]:
    return (span.path, span.carrier.value, span.start_line, span.start_column, span.end_line, span.end_column)


def _spans(values: Iterable[SourceSpan]) -> tuple[SourceSpan, ...]:
    return tuple(sorted(set(values), key=_span_key))


def _span_json(span: SourceSpan) -> dict[str, object]:
    return {
        "path": span.path, "carrier": span.carrier.value,
        "start_line": span.start_line, "start_column": span.start_column,
        "end_line": span.end_line, "end_column": span.end_column,
    }


class PinInventoryEngine:
    """Pure snapshot-to-document engine; no repository or host access after input."""

    __slots__ = ("_registry", "_json", "_python", "_text")

    def __init__(self, registry: Registry = DEFAULT_REGISTRY) -> None:
        if type(registry) is not Registry:
            raise ValueError("inventory registry must be a Registry")
        self._registry = registry
        self._json = JsonExtractor(registry)
        self._python = PythonExtractor(registry)
        self._text = TextAndPathExtractor(registry)

    @staticmethod
    def _text_path(path: str) -> bool:
        leaf = path.rsplit("/", 1)[-1]
        return leaf in _TEXT_NAMES or any(leaf.endswith(suffix) for suffix in _TEXT_SUFFIXES)

    def _governs_generic_content(self, path: str) -> bool:
        """Identify a carrier that can establish generic inventory authority."""
        if path in GOVERNED_JSON_PATHS:
            return True
        leaf = path.rsplit("/", 1)[-1]
        if leaf in _TEXT_NAMES or any(leaf.endswith(suffix) for suffix in _INTRINSIC_GOVERNING_SUFFIXES):
            return not path.startswith("tests/")
        if path.startswith("config/") and leaf.endswith(".txt"):
            return True
        if "/" not in path and leaf.endswith((".md", ".txt")):
            return True
        return False

    def _validate_snapshot(self, snapshot: GitTreeSnapshot) -> dict[str, GitBlobSnapshot]:
        if type(snapshot) is not GitTreeSnapshot:
            raise PinInventoryError("inventory generation requires a GitTreeSnapshot")
        if snapshot.object_format not in ("sha1", "sha256"):
            raise PinInventoryError("snapshot object format is invalid")
        width = 40 if snapshot.object_format == "sha1" else 64
        if not _is_oid(snapshot.tree_oid, width):
            raise PinInventoryError("snapshot tree OID is invalid")
        if snapshot.commit_oid is not None and not _is_oid(snapshot.commit_oid, width):
            raise PinInventoryError("snapshot commit OID is invalid")
        if type(snapshot.blobs) is not tuple:
            raise PinInventoryError("snapshot blobs must be an immutable tuple")
        blobs: dict[str, GitBlobSnapshot] = {}
        for blob in snapshot.blobs:
            if type(blob) is not GitBlobSnapshot:
                raise PinInventoryError("snapshot blob is invalid")
            if not _is_scannable_path(blob.path):
                raise PinInventoryError("snapshot blob path is invalid or unscannable")
            if blob.path in blobs:
                raise PinInventoryError("snapshot blobs are duplicated")
            if type(blob.mode) is not int or blob.mode not in (0o100644, 0o100755):
                raise PinInventoryError("snapshot contains a non-regular file")
            if type(blob.data) is not bytes or not _is_oid(blob.blob_oid, width) or _git_oid(blob.data, snapshot.object_format) != blob.blob_oid:
                raise PinInventoryError("snapshot blob OID is inconsistent")
            if not _is_oid(blob.sha256, 64) or _sha256(blob.data) != blob.sha256:
                raise PinInventoryError("snapshot blob SHA-256 is inconsistent")
            blobs[blob.path] = blob
        if INVENTORY_PATH in blobs:
            raise PinInventoryError("inventory must be absent from source snapshot")
        missing = sorted(GOVERNED_JSON_PATHS.difference(blobs))
        if missing:
            raise PinInventoryError(f"governed policy is absent from source snapshot: {missing[0]}")
        readme = blobs.get("engines/nautilus/README.md")
        if readme is not None:
            try:
                readme_text = readme.data.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise PinInventoryError("Nautilus README is not strict UTF-8") from exc
            for name in _POLICY_REFERENCE.findall(readme_text):
                path = f"engines/nautilus/{name}"
                if path not in blobs:
                    raise PinInventoryError(f"README-referenced policy is absent: {name}")
                if path not in GOVERNED_JSON_PATHS:
                    raise PinInventoryError(f"README-referenced policy is not scanned: {name}")
        return blobs

    def _classify(self, observations: Iterable[Observation]) -> tuple[Observation, ...]:
        recorded: list[Observation] = []
        for observation in observations:
            decision = self._registry.classify(observation)
            if decision.code == "UNREGISTERED_IDENTITY":
                raise PinInventoryError(f"unregistered governed identity: {observation.family}={observation.value}")
            recorded.append(observation)
        return tuple(recorded)

    def generate(self, snapshot: GitTreeSnapshot) -> PinInventoryDocument:
        blobs = self._validate_snapshot(snapshot)
        all_observations: list[tuple[GitBlobSnapshot, Observation]] = []
        guards: list[tuple[GitBlobSnapshot, object]] = []
        relations: list[tuple[GitBlobSnapshot, GovernedRelation]] = []
        for path, blob in sorted(blobs.items()):
            all_observations.extend((blob, value) for value in self._classify(self._text.extract_path(path)))
            if not self._text_path(path):
                continue
            try:
                text = blob.data.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise PinInventoryError(f"eligible text blob is not strict UTF-8: {path}") from exc
            specialized: tuple[Observation, ...] = ()
            if path in GOVERNED_JSON_PATHS:
                specialized = self._json.extract(path, text)
            if path in _GOVERNED_PYTHON_PATHS:
                result = self._python.extract(path, text)
                specialized += result.observations
                guards.extend((blob, guard) for guard in result.dynamic_guards)
                relations.extend((blob, relation) for relation in result.governed_relations)
            owners: dict[SourceSpan, Observation] = {}
            for observation in specialized:
                previous = owners.get(observation.span)
                if previous is not None and previous != observation:
                    raise PinInventoryError("conflicting specialized observation ownership")
                owners[observation.span] = observation
            generic = self._text.extract_content(path, text)
            if path.endswith(".py"):
                payloads = _python_payload_spans(path, text)
                metadata = _python_mapping_value_spans(path, text)
                generic = tuple(
                    value for value in generic
                    if any(_contains(payload, value.span) for payload in payloads)
                    and not any(_contains(mapping, value.span) for mapping in metadata)
                )
                if path.startswith("tests/"):
                    generic = ()
            elif path.endswith(".md"):
                prose = _markdown_prose_spans(path, text)
                generic = tuple(value for value in generic if any(_contains(line, value.span) for line in prose))
                if path.startswith("docs/"):
                    generic = ()
            elif path.endswith(".json") and path not in GOVERNED_JSON_PATHS:
                generic = ()
            governs_generic = self._governs_generic_content(path)
            retained: list[Observation] = list(owners.values())
            for value in generic:
                owner = owners.get(value.span)
                if owner is None:
                    if governs_generic or self._registry.classify(value).code != "UNREGISTERED_IDENTITY":
                        retained.append(value)
                elif (owner.family, owner.value) != (value.family, value.value):
                    raise PinInventoryError("conflicting specialized and generic observation ownership")
            combined = tuple(retained)
            all_observations.extend((blob, value) for value in self._classify(combined))

        entries = self._entries(all_observations)
        observed = {(entry.family, entry.value) for entry in entries}
        expected = {(identity.family, identity.value) for identity in self._registry.allowed_identities}
        missing = sorted(expected.difference(observed))
        if missing:
            family, value = missing[0]
            raise PinInventoryError(f"required identity is missing: {family}={value}")
        return PinInventoryDocument(
            "nautilus-pin-inventory/v4", "U00R_TRUSTED_HOST_COOPERATIVE_GIT_V1",
            snapshot.tree_oid, snapshot.object_format, entries,
            self._guard_records(guards), self._relation_records(relations),
        )

    def _entries(self, values: Iterable[tuple[GitBlobSnapshot, Observation]]) -> tuple[PinInventoryEntry, ...]:
        groups: dict[tuple[object, ...], list[SourceSpan]] = {}
        for blob, observation in values:
            decision = self._registry.classify(observation)
            assert decision.allowed_identity is not None
            key = (blob.path, blob.blob_oid, blob.sha256, observation.span.carrier.value,
                   observation.family, observation.value, decision.code, observation.syntax)
            groups.setdefault(key, []).append(observation.span)
        entries: list[PinInventoryEntry] = []
        ids: set[str] = set()
        for key, spans in groups.items():
            path, oid, digest, carrier, family, value, role, syntax = key
            identifier = "PIN-" + _sha256(_compact([path, carrier, family, value, syntax]))[:20].upper()
            if identifier in ids:
                raise PinInventoryError("pin inventory ID collision")
            ids.add(identifier)
            entries.append(PinInventoryEntry(identifier, path, oid, digest, carrier, family, value, role, syntax, _spans(spans)))
        return tuple(sorted(entries, key=lambda entry: (entry.path, entry.carrier, entry.family, entry.value, entry.role, entry.syntax, entry.id)))

    def _guard_records(self, values: Iterable[tuple[GitBlobSnapshot, object]]) -> tuple[DynamicGuardRecord, ...]:
        groups: dict[tuple[object, ...], list[SourceSpan]] = {}
        for blob, guard in values:
            from .model import DynamicGovernedCheck
            if type(guard) is not DynamicGovernedCheck:
                raise PinInventoryError("dynamic guard type is invalid")
            key = (blob.path, blob.blob_oid, blob.sha256, guard.left_root, guard.left_field,
                   guard.operator, guard.right_root, guard.right_field, guard.syntax_fingerprint)
            groups.setdefault(key, []).append(guard.span)
        records: list[DynamicGuardRecord] = []
        ids: set[str] = set()
        for key, spans in groups.items():
            path, oid, digest, left_root, left_field, operator, right_root, right_field, fingerprint = key
            identifier = "GUARD-" + _sha256(_compact([path, left_root, left_field, operator, right_root, right_field]))[:20].upper()
            if identifier in ids:
                raise PinInventoryError("dynamic guard ID collision")
            ids.add(identifier)
            records.append(DynamicGuardRecord(identifier, path, oid, digest, left_root, left_field, operator, right_root, right_field, fingerprint, _spans(spans)))
        return tuple(sorted(records, key=lambda record: (record.path, record.left_root, record.left_field, record.operator, record.right_root, record.right_field, record.syntax_fingerprint, record.id)))

    def _relation_records(self, values: Iterable[tuple[GitBlobSnapshot, GovernedRelation]]) -> tuple[GovernedRelationRecord, ...]:
        groups: dict[tuple[object, ...], list[SourceSpan]] = {}
        for blob, relation in values:
            key = (blob.path, blob.blob_oid, blob.sha256, relation.left_root, relation.left_document_kind,
                   relation.left_field, relation.left_family, relation.operator, relation.right_root,
                   relation.right_document_kind, relation.right_field, relation.right_family, relation.relation_kind, relation.binding_fingerprint,
                   relation.syntax_fingerprint)
            groups.setdefault(key, []).append(relation.span)
        records: list[GovernedRelationRecord] = []
        ids: set[str] = set()
        for key, spans in groups.items():
            (path, oid, digest, left_root, left_document_kind, left_field, left_family, operator, right_root,
             right_document_kind, right_field, right_family, relation_kind, binding, syntax) = key
            identifier = "REL-" + _sha256(_compact([path, left_root, left_document_kind, left_field, left_family, operator, right_root, right_document_kind, right_field, right_family, relation_kind, binding, syntax]))[:20].upper()
            if identifier in ids:
                raise PinInventoryError("governed relation ID collision")
            ids.add(identifier)
            records.append(GovernedRelationRecord(identifier, path, oid, digest, left_root, left_document_kind, left_field, left_family, operator, right_root, right_document_kind, right_field, right_family, relation_kind, binding, syntax, _spans(spans)))
        return tuple(sorted(records, key=lambda record: (record.path, record.left_root, record.left_document_kind, record.left_field, record.left_family, record.operator, record.right_root, record.right_document_kind, record.right_field, record.right_family, record.relation_kind, record.binding_fingerprint, record.syntax_fingerprint, record.id)))

    def serialize(self, document: PinInventoryDocument) -> bytes:
        if type(document) is not PinInventoryDocument:
            raise ValueError("inventory document must be a PinInventoryDocument")
        return _canonical({
            "schema": document.schema, "threat_model": document.threat_model,
            "source_tree_oid": document.source_tree_oid, "object_format": document.object_format,
            "entries": [
                {"id": entry.id, "path": entry.path, "source_blob_oid": entry.source_blob_oid,
                 "source_blob_sha256": entry.source_blob_sha256, "carrier": entry.carrier,
                 "family": entry.family, "value": entry.value, "role": entry.role,
                 "syntax": entry.syntax, "spans": [_span_json(span) for span in entry.spans]}
                for entry in document.entries
            ],
            "dynamic_guards": [
                {"id": record.id, "path": record.path, "source_blob_oid": record.source_blob_oid,
                 "source_blob_sha256": record.source_blob_sha256, "left_root": record.left_root,
                 "left_field": record.left_field, "operator": record.operator, "right_root": record.right_root,
                 "right_field": record.right_field, "syntax_fingerprint": record.syntax_fingerprint,
                 "spans": [_span_json(span) for span in record.spans]}
                for record in document.dynamic_guards
            ],
            "governed_relations": [
                {"id": record.id, "path": record.path, "source_blob_oid": record.source_blob_oid,
                 "source_blob_sha256": record.source_blob_sha256, "left_root": record.left_root,
                 "left_document_kind": record.left_document_kind, "left_field": record.left_field, "left_family": record.left_family, "operator": record.operator,
                 "right_root": record.right_root, "right_document_kind": record.right_document_kind, "right_field": record.right_field, "right_family": record.right_family,
                 "relation_kind": record.relation_kind, "binding_fingerprint": record.binding_fingerprint,
                 "syntax_fingerprint": record.syntax_fingerprint, "spans": [_span_json(span) for span in record.spans]}
                for record in document.governed_relations
            ],
        }, indent=2)

    def verify(self, snapshot: GitTreeSnapshot, inventory_bytes: bytes) -> None:
        if type(inventory_bytes) is not bytes:
            raise ValueError("inventory bytes must be bytes")
        try:
            parsed = json.loads(inventory_bytes.decode("utf-8", errors="strict"), object_pairs_hook=self._no_duplicates)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise PinInventoryError("inventory schema is invalid") from exc
        if not isinstance(parsed, dict):
            raise PinInventoryError("inventory schema is invalid")
        expected = self.serialize(self.generate(snapshot))
        if inventory_bytes != expected:
            raise PinInventoryError("inventory bytes are stale or noncanonical")

    @staticmethod
    def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate JSON key")
            output[key] = value
        return output
