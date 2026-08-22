"""Fail-closed Python literal and governed-comparison pin extraction."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import tokenize
import unicodedata
from dataclasses import dataclass
from io import StringIO
from typing import Iterable

from .model import (
    DynamicGovernedCheck,
    GovernedRelation,
    Observation,
    PythonExtractionResult,
    SourceSpan,
)
from .registry import Registry


_TOKEN_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.+-")
_OBJECTS = frozenset({"policy", "manifest", "closure_manifest", "closure_policy"})
_FIELDS = {
    "engine_version": "engine_version",
    "engine_upstream_commit": "upstream_commit",
    "source_commit": "upstream_commit",
    "profile": "profile",
    "profile_manifest_schema_version": "closure_schema",
    "result_validator_id": "validator",
    "schema_version": "closure_schema",
    "semantic_profile": "semantic_profile",
}

_DOCUMENT_FIELDS = {
    "nautilus_engine_build_policy": _FIELDS,
    "nautilus_runtime_closure_policy": {**_FIELDS, "source_commit": "selected_source"},
    "nautilus_closure_manifest": {**_FIELDS, "source_commit": "selected_source"},
    "nautilus_base_runtime_manifest": _FIELDS,
}
_CONDITIONAL_ROOTS = frozenset({"specification", "expected_identity"})
_GOVERNED_ROOTS = _OBJECTS | _CONDITIONAL_ROOTS


@dataclass(frozen=True)
class _Endpoint:
    path: str
    qualified_scope: str
    root: str
    binding_kind: str
    document_kind: str


_ENDPOINTS = (
    _Endpoint(
        "scripts/materialize_nautilus_runtime_closure.py",
        "_validate_base_runtime_bytes@566",
        "manifest",
        "base_runtime_manifest_json_object",
        "nautilus_base_runtime_manifest",
    ),
    _Endpoint(
        "scripts/materialize_nautilus_runtime_closure.py",
        "_validate_base_runtime_bytes@566",
        "policy",
        "base_runtime_policy_parameter",
        "nautilus_runtime_closure_policy",
    ),
    _Endpoint(
        "scripts/materialize_nautilus_runtime_closure.py",
        "_validate_policy_bytes@422",
        "policy",
        "runtime_policy_json_object",
        "nautilus_runtime_closure_policy",
    ),
    _Endpoint(
        "scripts/materialize_nautilus_runtime_closure.py",
        "_validate_policy_bytes@422",
        "specification",
        "profile_specification_lookup",
        "nautilus_runtime_closure_policy",
    ),
    _Endpoint(
        "services/job_worker/nautilus_closure.py",
        "attest_nautilus_backtest_closure@515",
        "closure_manifest",
        "closure_manifest_read_json",
        "nautilus_closure_manifest",
    ),
    _Endpoint(
        "services/job_worker/nautilus_closure.py",
        "attest_nautilus_backtest_closure@515",
        "expected_identity",
        "profile_identity_lookup",
        "nautilus_closure_manifest",
    ),
)

_APPROVED_ENDPOINT_HASHES = {
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_base_runtime_bytes", 566): "a078704b0a34fab0d575c9c464deca04f11861e722f71a73311535837d837628",
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_artifact_bytes", 656): "301748498fc1ac4bb52eb73ae828e5470989cc2320ac1673cfa90b9134150e41",
    ("scripts/materialize_nautilus_runtime_closure.py", "_build_native_entry_guard", 741): "cc36293680cc801e8d390307ec10ffe61f7b6531a89f0eaf8ca27f1201c4d0f8",
    ("scripts/materialize_nautilus_runtime_closure.py", "_build_output_manifest", 1049): "b8a00c947ff201b4aa64981cda832f9e1f361ac2c9e31286caba4fe6f8ab8d5b",
    ("scripts/materialize_nautilus_runtime_closure.py", "materialize_runtime_closure", 1084): "ce8af351d43bdf60b541167db75087ad03409edbb93ddff1e0a21391521392d2",
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_policy_bytes", 422): "2c0e2c883286485ac8deb80b77f219901e254c55e8ce47858e3513d97680d266",
    ("scripts/materialize_nautilus_runtime_closure.py", "_load_policy", 501): "1d1ca401e940576c38bcdf42066d584b076743c8c2d10fb4b2763ec9b1aec3b8",
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_base_runtime", 513): "50167e5e33c2d3a9b660e9e89117ca1b5027ba2f2dd74cdcc9cab2d9613a1b5e",
    ("services/job_worker/nautilus_closure.py", "_closure_digest", 343): "94cb3024725f382c9043a65bad8c069c00714a1b8e54ca7a97dbef65bada9ff4",
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure", 515): "53794a8f8e0932b44b6fd4b96d4cc50174a281dfa58c6a86156aeba78361eddb",
}
_APPROVED_MODULE_CHILD_HASHES = {
    ("scripts/materialize_nautilus_runtime_closure.py", "_PROFILE_SPECS", 143): "4d30ed9f0db35441006c9d88a7734f67c2a046aec944ea54780e40a1234c10c3",
    ("scripts/materialize_nautilus_runtime_closure.py", "_json_object", 267): "2f8b56257df665e2328b2bca01e91579574639b8ae38e17767f52576115c11ee",
    ("services/job_worker/nautilus_closure.py", "_PROFILES", 47): "cb3ed07358edc79a3226c42930c1882bdb4bdb9c37a1805e21b0b69a71003293",
    ("services/job_worker/nautilus_closure.py", "_blocked", 151): "5860bb7abb423390463c82b929a24bbe7781e992055d758cb300a9ca601c4873",
    ("services/job_worker/nautilus_closure.py", "_read_json", 216): "fd1d05b14169fde6122e3bb4e7edad9053edb8e20be2c0720191894e6c9b4943",
    ("services/job_worker/nautilus_closure.py", "_closure_digest", 343): "94cb3024725f382c9043a65bad8c069c00714a1b8e54ca7a97dbef65bada9ff4",
}
_APPROVED_ROOT_MAP_READERS = {
    ("scripts/materialize_nautilus_runtime_closure.py", "_REPOSITORY_LAUNCHER_SOURCES", 171): "84148bdb6160537c70a2da068b069c697792b604f752feedbb5745cdfbc1c8fa",
}

class PythonExtractionError(ValueError):
    """A governed Python expression cannot be given an exact, safe citation."""


@dataclass(frozen=True)
class _Origin:
    start: int
    end: int


@dataclass(frozen=True)
class _Literal:
    value: str | int | float
    origins: tuple[_Origin, ...]
    dynamic: bool = False


@dataclass(frozen=True)
class _StringToken:
    start: int
    end: int
    literal: _Literal


@dataclass(frozen=True)
class _Binding:
    kind: str
    value: _Literal | str | None


@dataclass(frozen=True)
class _BindingEvent:
    value: ast.AST | None
    top_level_simple: bool


def _invalid() -> PythonExtractionError:
    return PythonExtractionError("invalid governed Python expression")


def _offsets(text: str) -> tuple[int, ...]:
    starts = [0]
    for index, character in enumerate(text):
        if character == "\n":
            starts.append(index + 1)
    return tuple(starts)


def _offset(line_starts: tuple[int, ...], line: int, column: int) -> int:
    return line_starts[line - 1] + column


def _ast_offset(text: str, line_starts: tuple[int, ...], line: int, byte_column: int) -> int:
    """Convert CPython AST UTF-8 byte columns to character offsets."""
    start = line_starts[line - 1]
    end = text.find("\n", start)
    fragment = text[start : len(text) if end < 0 else end]
    consumed = 0
    for index, character in enumerate(fragment):
        if consumed == byte_column:
            return start + index
        consumed += len(character.encode("utf-8"))
    if consumed == byte_column:
        return start + len(fragment)
    raise _invalid()


def _position(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    line_start = text.rfind("\n", 0, offset) + 1
    return line, offset - line_start + 1


def _decode_escaped(content: str, base: int, *, raw: bool) -> _Literal:
    characters: list[str] = []
    origins: list[_Origin] = []
    index = 0
    while index < len(content):
        if raw or content[index] != "\\":
            characters.append(content[index])
            origins.append(_Origin(base + index, base + index + 1))
            index += 1
            continue
        start = index
        index += 1
        if index == len(content):
            raise _invalid()
        marker = content[index]
        if marker == "\n":
            index += 1
            continue
        if marker == "\r" and index + 1 < len(content) and content[index + 1] == "\n":
            index += 2
            continue
        if marker == "x":
            width = 4
        elif marker == "u":
            width = 6
        elif marker == "U":
            width = 10
        elif marker in "01234567":
            digits = 1
            while digits < 3 and index + digits < len(content) and content[index + digits] in "01234567":
                digits += 1
            width = 1 + digits
        elif marker == "N":
            closing = content.find("}", index + 2)
            if index + 1 >= len(content) or content[index + 1] != "{" or closing < 0:
                raise _invalid()
            width = closing - start + 1
        elif marker in "\\'\"abfnrtv":
            width = 2
        else:
            raise _invalid()
        try:
            if marker == "x":
                decoded = chr(int(content[index + 1 : start + width], 16))
            elif marker == "u" or marker == "U":
                decoded = chr(int(content[index + 1 : start + width], 16))
            elif marker in "01234567":
                decoded = chr(int(content[index : start + width], 8))
            elif marker == "N":
                decoded = unicodedata.lookup(content[index + 2 : start + width - 1])
            else:
                decoded = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}.get(marker, marker)
        except (KeyError, ValueError):
            raise _invalid() from None
        if len(decoded) != 1:
            raise _invalid()
        characters.append(decoded)
        origins.append(_Origin(base + start, base + start + width))
        index = start + width
    return _Literal("".join(characters), tuple(origins))


def _literal_string(token: tokenize.TokenInfo, line_starts: tuple[int, ...]) -> _StringToken:
    source = token.string
    match = re.match(r"(?i)([rubf]*)('''|\"\"\"|'|\")", source)
    if match is None:
        raise _invalid()
    prefix, quote = match.groups()
    if source[-len(quote) :] != quote:
        raise _invalid()
    raw = "r" in prefix.casefold()
    formatted = "f" in prefix.casefold()
    start = _offset(line_starts, token.start[0], token.start[1])
    content_start = start + len(prefix) + len(quote)
    content = source[len(prefix) + len(quote) : -len(quote)]
    if not formatted:
        decoded = _decode_escaped(content, content_start, raw=raw)
        end = _offset(line_starts, token.end[0], token.end[1])
        return _StringToken(start, end, decoded)

    # This bounded scanner intentionally records only literal f-string segments.
    # Any interpolation makes the complete expression unusable as a governed value.
    characters: list[str] = []
    origins: list[_Origin] = []
    dynamic = False
    index = 0
    segment_start = 0
    while index < len(content):
        if content[index] not in "{}":
            index += 1
            continue
        if index + 1 < len(content) and content[index + 1] == content[index]:
            segment = _decode_escaped(content[segment_start:index], content_start + segment_start, raw=raw)
            characters.extend(segment.value)
            origins.extend(segment.origins)
            characters.append(content[index])
            origins.append(_Origin(content_start + index, content_start + index + 2))
            index += 2
            segment_start = index
            continue
        segment = _decode_escaped(content[segment_start:index], content_start + segment_start, raw=raw)
        characters.extend(segment.value)
        origins.extend(segment.origins)
        dynamic = True
        depth = 1
        index += 1
        while index < len(content) and depth:
            if content[index] == "{":
                depth += 1
            elif content[index] == "}":
                depth -= 1
            index += 1
        if depth:
            raise _invalid()
        segment_start = index
    segment = _decode_escaped(content[segment_start:], content_start + segment_start, raw=raw)
    characters.extend(segment.value)
    origins.extend(segment.origins)
    end = _offset(line_starts, token.end[0], token.end[1])
    return _StringToken(start, end, _Literal("".join(characters), tuple(origins), dynamic))


def _string_tokens(text: str) -> tuple[_StringToken, ...]:
    line_starts = _offsets(text)
    try:
        tokens = tokenize.generate_tokens(StringIO(text).readline)
        return tuple(_literal_string(token, line_starts) for token in tokens if token.type == tokenize.STRING)
    except (tokenize.TokenError, IndentationError):
        raise _invalid() from None


def _span(path: str, text: str, origins: Iterable[_Origin]) -> SourceSpan:
    items = tuple(origins)
    if not items:
        raise _invalid()
    start_line, start_column = _position(text, items[0].start)
    end_line, end_column = _position(text, items[-1].end)
    return SourceSpan.content(path, start_line, start_column, end_line, end_column)


def _token_ranges(value: str) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(value):
        if value[start] not in _TOKEN_CHARACTERS:
            start += 1
            continue
        end = start + 1
        while end < len(value) and value[end] in _TOKEN_CHARACTERS:
            end += 1
        ranges.append((start, end))
        start = end
    return tuple(ranges)


def _raw_access(node: ast.AST) -> str | None:
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in _OBJECTS:
        if isinstance(node.slice, ast.Constant) and type(node.slice.value) is str:
            return _FIELDS.get(node.slice.value)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in _OBJECTS
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], ast.Constant)
        and type(node.args[0].value) is str
    ):
        return _FIELDS.get(node.args[0].value)
    return None


def _governed_like(node: ast.AST) -> bool:
    """Recognize every attempted governed access, including dynamic keys we reject."""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in _GOVERNED_ROOTS:
        return not (isinstance(node.slice, ast.Constant) and type(node.slice.value) is str and node.slice.value not in _FIELDS)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and isinstance(node.func.value, ast.Name) and node.func.value.id in _GOVERNED_ROOTS:
        return not (node.args and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is str and node.args[0].value not in _FIELDS)
    return _raw_access(node) is not None


class PythonExtractor:
    """Extract exact string-literal pins and closed governed policy comparisons."""

    __slots__ = ("_registry",)

    def __init__(self, registry: Registry) -> None:
        if type(registry) is not Registry:
            raise ValueError("extractor registry must be a Registry")
        self._registry = registry

    def extract(self, path: str, text: str) -> PythonExtractionResult:
        if type(path) is not str or type(text) is not str:
            raise ValueError("Python extraction path and text must be strings")
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError:
            raise _invalid() from None
        if self._requires_endpoint_authority(path, tree):
            self._proved_endpoint_authority(path, tree)
        tokens = _string_tokens(text)
        bindings, invalid_names, invalid_governed_names = self._bindings(tree, tokens, text)
        observations = set(self._literal_observations(path, text, tokens, tree))
        dynamic_guards: set[DynamicGovernedCheck] = set()
        governed_relations: set[GovernedRelation] = set()
        parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                evidence = self._governed_evidence(path, text, tree, node, parents)
                if evidence is not None:
                    guard, relation = evidence
                    if guard is not None:
                        dynamic_guards.add(guard)
                    if relation is not None:
                        governed_relations.add(relation)
                    continue
                if self._inside_approved_endpoint(path, tree, node):
                    continue
                if path in {candidate.path for candidate in _ENDPOINTS} and not any(
                    _governed_like(candidate) or isinstance(candidate, ast.Name) and candidate.id in _GOVERNED_ROOTS
                    for candidate in ast.walk(node)
                ):
                    continue
                observations.update(self._comparison_observations(path, text, node, tokens, bindings, invalid_names, invalid_governed_names))
        return PythonExtractionResult(
            tuple(sorted(observations, key=self._sort_key)),
            tuple(sorted(dynamic_guards, key=self._dynamic_sort_key)),
            tuple(sorted(governed_relations, key=self._relation_sort_key)),
        )

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")

    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return hashlib.sha256(cls._canonical(value)).hexdigest()

    @staticmethod
    def _module_child_name(node: ast.AST) -> str | None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return node.name
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0] if len(node.targets) == 1 else None
            return target.id if isinstance(target, ast.Name) else None
        return None

    @staticmethod
    def _direct_governed_comparison(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Compare)
            and len(node.ops) == len(node.comparators) == 1
            and isinstance(node.ops[0], (ast.Eq, ast.NotEq))
            and PythonExtractor._direct_access(node.left) is not None
            and PythonExtractor._direct_access(node.comparators[0]) is not None
        )

    @staticmethod
    def _requires_endpoint_authority(path: str, tree: ast.Module) -> bool:
        return path in {endpoint.path for endpoint in _ENDPOINTS} and any(
            _governed_like(node) for node in ast.walk(tree)
        )

    @classmethod
    def _structural_hash(cls, node: ast.AST, *, normalize: bool) -> str:
        value = copy.deepcopy(node)
        if normalize:
            for item in ast.walk(value):
                if cls._direct_governed_comparison(item):
                    item.ops[0] = ast.Eq()
        return cls._fingerprint(ast.dump(value, annotate_fields=True, include_attributes=True))

    @classmethod
    def _proved_endpoint_authority(cls, path: str, tree: ast.Module) -> None:
        expected_endpoints = {
            (name, line): fingerprint
            for (candidate_path, name, line), fingerprint in _APPROVED_ENDPOINT_HASHES.items()
            if candidate_path == path
        }
        if not expected_endpoints:
            return
        expected_children = {
            (name, line): fingerprint
            for (candidate_path, name, line), fingerprint in _APPROVED_MODULE_CHILD_HASHES.items()
            if candidate_path == path
        }
        expected_root_map_readers = {
            (name, line): fingerprint
            for (candidate_path, name, line), fingerprint in _APPROVED_ROOT_MAP_READERS.items()
            if candidate_path == path
        }
        children = {(cls._module_child_name(node), getattr(node, "lineno", 0)): node for node in tree.body}
        if set(children).isdisjoint(expected_endpoints) or any(
            key not in children or cls._structural_hash(children[key], normalize=True) != fingerprint
            for key, fingerprint in expected_endpoints.items()
        ):
            raise _invalid()
        if any(
            key not in children or cls._structural_hash(children[key], normalize=False) != fingerprint
            for key, fingerprint in expected_children.items()
        ):
            raise _invalid()
        if any(
            key not in children or cls._structural_hash(children[key], normalize=False) != fingerprint
            for key, fingerprint in expected_root_map_readers.items()
        ):
            raise _invalid()
        endpoint_roots = {endpoint.root for endpoint in _ENDPOINTS if endpoint.path == path}
        root_maps = {
            name
            for (name, line), node in children.items()
            if (name, line) in expected_children and isinstance(node, (ast.Assign, ast.AnnAssign))
        }
        protected = {name for name, _ in expected_children} | {name for name, _ in expected_endpoints} | {"set"}
        if any(sum(name == child_name for child_name, _ in children) != 1 for name in protected - {"set"}):
            raise _invalid()
        allowed = {
            (name, id(candidate))
            for (name, _), node in children.items()
            if name in protected
            for candidate in ast.walk(node)
            if isinstance(candidate, ast.Name) and candidate.id == name and isinstance(candidate.ctx, ast.Store)
        } | {
            (name, id(node))
            for (name, _), node in children.items()
            if name in protected and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        root_map_scopes = {
            id(node)
            for (name, _), node in children.items()
            if name in root_maps or (name, _) in expected_root_map_readers or (name, _) in expected_endpoints
        }
        sole_callers = {
            "_validate_policy_bytes": "_load_policy",
            "_load_policy": "materialize_runtime_closure",
            "_validate_base_runtime": "materialize_runtime_closure",
            "_validate_base_runtime_bytes": "_validate_base_runtime",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
                raise _invalid()
            if isinstance(node, ast.Import) and any(alias.name == "builtins" for alias in node.names):
                raise _invalid()
            if isinstance(node, (ast.Global, ast.Nonlocal)) and any(name in protected for name in node.names):
                raise _invalid()
            if isinstance(node, ast.arg) and node.arg in protected:
                raise _invalid()
            if isinstance(node, ast.ExceptHandler) and node.name in protected:
                raise _invalid()
            if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name in protected:
                raise _invalid()
            if isinstance(node, ast.MatchMapping) and node.rest in protected:
                raise _invalid()
            if isinstance(node, ast.Name) and node.id in protected and isinstance(node.ctx, (ast.Store, ast.Del)):
                if (node.id, id(node)) not in allowed:
                    raise _invalid()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in protected:
                if (node.name, id(node)) not in allowed:
                    raise _invalid()
            if isinstance(node, (ast.Import, ast.ImportFrom)) and any((alias.asname or alias.name.split(".")[0]) in protected for alias in node.names):
                raise _invalid()
            if isinstance(node, ast.Name) and node.id in root_maps:
                owners = [owner for owner in tree.body if owner is node or node in ast.walk(owner)]
                if len(owners) != 1 or id(owners[0]) not in root_map_scopes:
                    raise _invalid()
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in sole_callers:
                owners = [owner for owner in tree.body if node in ast.walk(owner)]
                if len(owners) != 1 or cls._module_child_name(owners[0]) != sole_callers[node.id]:
                    raise _invalid()
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) and isinstance(node.value, ast.Name) and node.value.id == "__builtins__":
                raise _invalid()
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.attr == "set" and isinstance(node.value, ast.Name) and node.value.id in {"builtins", "__builtins__"}:
                raise _invalid()
            if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name) and node.target.id in endpoint_roots and cls._scope(tree, node) is tree:
                raise _invalid()
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and cls._scope(tree, node) is tree:
                targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                if any(isinstance(target, ast.Name) and target.id in endpoint_roots for target in targets):
                    raise _invalid()
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"append", "clear", "extend", "pop", "remove", "update"}:
                if any(isinstance(item, ast.Name) and item.id in root_maps for item in ast.walk(node.func.value)):
                    raise _invalid()
            if isinstance(node, ast.Call) and any(
                isinstance(item, ast.Name) and item.id in root_maps
                for value in (*node.args, *(keyword.value for keyword in node.keywords))
                for item in ast.walk(value)
            ):
                raise _invalid()
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"globals", "locals", "vars", "setattr", "delattr"}:
                raise _invalid()

    @staticmethod
    def _inside_approved_endpoint(path: str, tree: ast.Module, node: ast.AST) -> bool:
        return any(
            isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef))
            and (path, candidate.name, candidate.lineno) in _APPROVED_ENDPOINT_HASHES
            and candidate.lineno <= node.lineno <= candidate.end_lineno
            for candidate in tree.body
        )

    @staticmethod
    def _scope(tree: ast.Module, node: ast.AST) -> ast.AST:
        candidates = [
            candidate for candidate in ast.walk(tree)
            if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            and candidate.lineno <= node.lineno <= candidate.end_lineno
        ]
        return max(candidates, key=lambda candidate: candidate.lineno) if candidates else tree

    @staticmethod
    def _scope_bindings(scope: ast.AST) -> dict[str, ast.AST]:
        """Return single-assignment direct bindings in one lexical scope."""
        values: dict[str, list[ast.AST]] = {}
        deleted: set[str] = set()
        for item in ast.walk(scope):
            if item is not scope and isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        values.setdefault(target.id, []).append(item.value)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.value is not None:
                values.setdefault(item.target.id, []).append(item.value)
            elif isinstance(item, ast.Name) and isinstance(item.ctx, ast.Del):
                deleted.add(item.id)
        return {name: entries[0] for name, entries in values.items() if len(entries) == 1 and name not in deleted}

    @staticmethod
    def _binding_kind(root: str, value: ast.AST) -> str | None:
        if (
            root == "manifest"
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_json_object"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Name)
            and value.args[0].id == "manifest_raw"
            and len(value.keywords) == 1
            and value.keywords[0].arg == "label"
            and isinstance(value.keywords[0].value, ast.Constant)
            and value.keywords[0].value.value == "base runtime manifest"
        ):
            return "base_runtime_manifest_json_object"
        if (
            root == "policy"
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_json_object"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Name)
            and value.args[0].id == "raw"
            and len(value.keywords) == 1
            and value.keywords[0].arg == "label"
            and isinstance(value.keywords[0].value, ast.Constant)
            and value.keywords[0].value.value == "runtime closure policy"
        ):
            return "runtime_policy_json_object"
        if (
            root == "closure_manifest"
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_read_json"
            and len(value.args) == 2
            and not value.keywords
            and isinstance(value.args[0], ast.BinOp)
            and isinstance(value.args[0].left, ast.Attribute)
            and isinstance(value.args[0].left.value, ast.Name)
            and value.args[0].left.value.id == "config"
            and value.args[0].left.attr == "runtime_root"
            and isinstance(value.args[0].op, ast.Div)
            and isinstance(value.args[0].right, ast.Name)
            and value.args[0].right.id == "_MANIFEST_NAME"
            and isinstance(value.args[1], ast.Constant)
            and value.args[1].value == "closure manifest"
        ):
            return "closure_manifest_read_json"
        if (
            root == "specification"
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "_PROFILE_SPECS"
            and value.func.attr == "get"
            and len(value.args) == 1
            and not value.keywords
            and isinstance(value.args[0], ast.Call)
            and isinstance(value.args[0].func, ast.Name)
            and value.args[0].func.id == "str"
            and len(value.args[0].args) == 1
            and isinstance(value.args[0].args[0], ast.Name)
            and value.args[0].args[0].id == "profile"
            and not value.args[0].keywords
        ):
            return "profile_specification_lookup"
        if root == "expected_identity" and isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name) and value.value.id == "_PROFILES" and isinstance(value.slice, ast.Name) and value.slice.id == "profile":
            return "profile_identity_lookup"
        return None

    @staticmethod
    def _direct_access(node: ast.AST) -> tuple[str, str] | None:
        if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Name) or node.value.id not in _GOVERNED_ROOTS:
            return None
        if not isinstance(node.slice, ast.Constant) or type(node.slice.value) is not str:
            return None
        return node.value.id, node.slice.value

    @staticmethod
    def _qualified_scope(scope: ast.AST) -> str:
        return f"{getattr(scope, 'name', '<module>')}@{getattr(scope, 'lineno', 1)}"

    @staticmethod
    def _base_runtime_policy_chain(tree: ast.Module) -> bool:
        """Prove the sole reviewed policy flow into the base-runtime check."""
        children = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        loader = children.get("_load_policy")
        validator = children.get("_validate_base_runtime")
        publisher = children.get("materialize_runtime_closure")
        if loader is None or validator is None or publisher is None:
            return False
        def call_named(node: ast.AST, name: str, arguments: tuple[str, ...]) -> bool:
            return (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
                and tuple(argument.id for argument in node.args if isinstance(argument, ast.Name)) == arguments
            )
        return (
            any(call_named(node, "_validate_policy_bytes", ("raw",)) for node in ast.walk(loader))
            and any(call_named(node, "_validate_base_runtime_bytes", ("manifest_raw", "policy")) for node in ast.walk(validator))
            and any(call_named(node, "_load_policy", ("policy_path",)) for node in ast.walk(publisher))
            and any(call_named(node, "_validate_base_runtime", ("base_runtime", "policy")) for node in ast.walk(publisher))
        )

    @classmethod
    def _binding_fingerprint(
        cls,
        path: str,
        qualified_scope: str,
        bindings: Iterable[tuple[str, str, str, str]],
    ) -> str:
        return cls._fingerprint([path, qualified_scope, [list(binding) for binding in sorted(bindings)]])

    def _endpoint(
        self,
        path: str,
        tree: ast.Module,
        node: ast.AST,
        access: tuple[str, str],
    ) -> tuple[str, str, str, str, str, str] | None:
        root, field = access
        scope = self._scope(tree, node)
        qualified_scope = self._qualified_scope(scope)
        candidates = tuple(endpoint for endpoint in _ENDPOINTS if endpoint.path == path and endpoint.qualified_scope == qualified_scope and endpoint.root == root)
        if candidates and scope not in tree.body:
            raise _invalid()
        if not candidates:
            return None
        bindings = self._scope_bindings(scope)
        value = bindings.get(root)
        binding_kind = self._binding_kind(root, value) if value is not None else None
        if (
            value is None
            and root == "policy"
            and isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
            and scope.name == "_validate_base_runtime_bytes"
            and any(argument.arg == "policy" for argument in (*scope.args.posonlyargs, *scope.args.args))
            and self._base_runtime_policy_chain(tree)
        ):
            binding_kind = "base_runtime_policy_parameter"
            value = ast.Name(id="policy")
        endpoint = next((item for item in candidates if item.binding_kind == binding_kind), None)
        if endpoint is None or value is None:
            raise _invalid()
        family = _DOCUMENT_FIELDS[endpoint.document_kind].get(field)
        if family is None:
            return None
        return root, endpoint.document_kind, field, family, endpoint.binding_kind, ast.dump(value, annotate_fields=True, include_attributes=False)

    @staticmethod
    def _node_span(path: str, text: str, node: ast.AST) -> SourceSpan:
        starts = _offsets(text)
        start = _ast_offset(text, starts, node.lineno, node.col_offset)
        end = _ast_offset(text, starts, node.end_lineno, node.end_col_offset)
        return _span(path, text, (_Origin(start, end),))

    def _governed_evidence(
        self,
        path: str,
        text: str,
        tree: ast.Module,
        node: ast.Compare,
        parents: dict[int, ast.AST],
    ) -> tuple[DynamicGovernedCheck | None, GovernedRelation | None] | None:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            return None
        operator = "==" if isinstance(node.ops[0], ast.Eq) else "!=" if isinstance(node.ops[0], ast.NotEq) else None
        if operator is None:
            return None
        left_access = self._direct_access(node.left)
        right_access = self._direct_access(node.comparators[0])
        if left_access is None or right_access is None:
            return None
        left = self._endpoint(path, tree, node, left_access)
        right = self._endpoint(path, tree, node, right_access)
        if left is None or right is None:
            return None
        scope = self._scope(tree, node)
        binding_fingerprint = self._binding_fingerprint(
            path,
            self._qualified_scope(scope),
            {(left[0], left[1], left[4], left[5]), (right[0], right[1], right[4], right[5])},
        )
        syntax_fingerprint = self._fingerprint([left[0], left[2], operator, right[0], right[2]])
        span = self._node_span(path, text, node)
        if left[3] == right[3]:
            return (
                DynamicGovernedCheck(path, left[0], left[2], operator, right[0], right[2], syntax_fingerprint, span),
                None,
            )
        return (
            None,
            GovernedRelation(
                path, left[0], left[1], left[2], left[3], "!=",
                right[0], right[1], right[2], right[3], "cross_family_consistency_guard",
                binding_fingerprint, syntax_fingerprint, span,
            ),
        )

    def _bindings(self, tree: ast.Module, tokens: tuple[_StringToken, ...], text: str) -> tuple[dict[str, _Binding], set[str], set[str]]:
        """Build one whole-module binding-event and conservative provenance graph."""
        def names(target: ast.AST | None) -> tuple[str, ...]:
            if isinstance(target, ast.Name):
                return (target.id,)
            if isinstance(target, (ast.Tuple, ast.List)):
                return tuple(name for item in target.elts for name in names(item))
            if isinstance(target, ast.Starred):
                return names(target.value)
            return ()

        events: dict[str, list[_BindingEvent]] = {}

        def record(target: ast.AST | None, value: ast.AST | None, top_level_simple: bool = False) -> None:
            for name in names(target):
                events.setdefault(name, []).append(_BindingEvent(value, top_level_simple))

        def mutation_base(target: ast.AST) -> ast.Name | None:
            current = target
            while isinstance(current, (ast.Attribute, ast.Subscript)):
                current = current.value
            return current if isinstance(current, ast.Name) else None

        top_level = {id(statement) for statement in tree.body}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    record(target, node.value, id(node) in top_level and len(node.targets) == 1 and isinstance(target, ast.Name))
                    base = mutation_base(target)
                    if base is not None and not isinstance(target, ast.Name):
                        record(base, node.value)
            elif isinstance(node, ast.AnnAssign):
                record(node.target, node.value, id(node) in top_level and isinstance(node.target, ast.Name) and node.value is not None)
                base = mutation_base(node.target)
                if base is not None and not isinstance(node.target, ast.Name):
                    record(base, node.value)
            elif isinstance(node, ast.AugAssign):
                record(node.target, node.value)
            elif isinstance(node, ast.NamedExpr):
                record(node.target, node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                record(node.target, node.iter)
            elif isinstance(node, ast.comprehension):
                record(node.target, node.iter)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    record(item.optional_vars, item.context_expr)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                events.setdefault(node.name, []).append(_BindingEvent(None, False))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == "*":
                        for root in _OBJECTS:
                            events.setdefault(root, []).append(_BindingEvent(None, False))
                    else:
                        events.setdefault(alias.asname or alias.name.split(".")[0], []).append(_BindingEvent(None, False))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                events.setdefault(node.name, []).append(_BindingEvent(None, False))
                if isinstance(node, ast.ClassDef):
                    continue
                arguments = node.args
                positional = (*arguments.posonlyargs, *arguments.args)
                defaults = (None,) * (len(positional) - len(arguments.defaults)) + tuple(arguments.defaults)
                for argument, default in zip(positional, defaults):
                    events.setdefault(argument.arg, []).append(_BindingEvent(default, False))
                for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
                    events.setdefault(argument.arg, []).append(_BindingEvent(default, False))
                if arguments.vararg is not None:
                    events.setdefault(arguments.vararg.arg, []).append(_BindingEvent(None, False))
                if arguments.kwarg is not None:
                    events.setdefault(arguments.kwarg.arg, []).append(_BindingEvent(None, False))
            elif isinstance(node, ast.Lambda):
                arguments = node.args
                positional = (*arguments.posonlyargs, *arguments.args)
                defaults = (None,) * (len(positional) - len(arguments.defaults)) + tuple(arguments.defaults)
                for argument, default in zip(positional, defaults):
                    events.setdefault(argument.arg, []).append(_BindingEvent(default, False))
                for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
                    events.setdefault(argument.arg, []).append(_BindingEvent(default, False))
                if arguments.vararg is not None:
                    events.setdefault(arguments.vararg.arg, []).append(_BindingEvent(None, False))
                if arguments.kwarg is not None:
                    events.setdefault(arguments.kwarg.arg, []).append(_BindingEvent(None, False))
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                events.setdefault(node.name, []).append(_BindingEvent(None, False))
            elif isinstance(node, ast.MatchMapping) and node.rest:
                events.setdefault(node.rest, []).append(_BindingEvent(None, False))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del):
                events.setdefault(node.id, []).append(_BindingEvent(None, False))

        # Every member Store/Del is a mutation event even when its enclosing
        # statement has no ordinary Name target (AugAssign, loops, deletes,
        # comprehensions, and future AST target-bearing forms included).
        mutation_bases: set[str] = set()
        for target in ast.walk(tree):
            if isinstance(target, (ast.Attribute, ast.Subscript)) and isinstance(target.ctx, (ast.Store, ast.Del)):
                base = mutation_base(target)
                if base is not None:
                    events.setdefault(base.id, []).append(_BindingEvent(None, False))
                    mutation_bases.add(base.id)

        invalid_roots = set(_OBJECTS).intersection(events)
        tainted: set[str] = set(mutation_bases)

        def expression_tainted(value: ast.AST | None) -> bool:
            if value is None:
                return False
            if _governed_like(value):
                return True
            if isinstance(value, ast.Name):
                return value.id in _OBJECTS or value.id in tainted
            return any(expression_tainted(child) for child in ast.iter_child_nodes(value))

        for _ in range(len(events) + 1):
            before = len(tainted)
            for name, name_events in events.items():
                if any(expression_tainted(event.value) for event in name_events):
                    tainted.add(name)
            if len(tainted) == before:
                break

        bindings: dict[str, _Binding] = {}
        invalid = set(events)
        for name, name_events in events.items():
            if len(name_events) != 1:
                continue
            event = name_events[0]
            if not event.top_level_simple or event.value is None:
                continue
            access = _raw_access(event.value)
            if access is not None:
                receiver = event.value.value if isinstance(event.value, ast.Subscript) else event.value.func.value
                if isinstance(receiver, ast.Name) and receiver.id not in invalid_roots:
                    bindings[name] = _Binding("alias", access)
                    invalid.discard(name)
                continue
            literal = self._literal(event.value, tokens, text)
            if literal is not None and not literal.dynamic:
                bindings[name] = _Binding("constant", literal)
                invalid.discard(name)
        invalid.update(invalid_roots)
        return bindings, invalid, tainted

    def _literal_observations(self, path: str, text: str, tokens: tuple[_StringToken, ...], tree: ast.Module) -> tuple[Observation, ...]:
        observations: set[Observation] = set()
        groups: list[list[_StringToken]] = []
        for token in tokens:
            if groups and text[groups[-1][-1].end : token.start].strip() == "":
                groups[-1].append(token)
            else:
                groups.append([token])
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and type(node.value) is str:
                literal = self._literal(node, tokens, text)
                if literal is not None and len(literal.origins) == len(str(literal.value)):
                    groups.append([_StringToken(0, 0, literal)])
        for group in groups:
            literal = _Literal(
                "".join(str(token.literal.value) for token in group),
                tuple(origin for token in group for origin in token.literal.origins),
                any(token.literal.dynamic for token in group),
            )
            if literal.dynamic:
                continue
            for start, end in _token_ranges(str(literal.value)):
                candidate = str(literal.value)[start:end]
                for spec in self._registry.family_specs:
                    observation = Observation(spec.family, candidate, _span(path, text, literal.origins[start:end]), "python")
                    if self._registry.classify(observation).code != "UNREGISTERED_IDENTITY":
                        observations.add(observation)
        return tuple(observations)

    def _literal(self, node: ast.AST, tokens: tuple[_StringToken, ...], text: str) -> _Literal | None:
        if isinstance(node, ast.Constant) and type(node.value) in (str, int, float):
            if type(node.value) is not str:
                starts = _offsets(text)
                start = _ast_offset(text, starts, node.lineno, node.col_offset)
                end = _ast_offset(text, starts, node.end_lineno, node.end_col_offset)
                return _Literal(node.value, tuple(_Origin(offset, offset + 1) for offset in range(start, end)))
            starts = _offsets(text)
            start = _ast_offset(text, starts, node.lineno, node.col_offset)
            end = _ast_offset(text, starts, node.end_lineno, node.end_col_offset)
            pieces = [token.literal for token in tokens if start <= token.start and token.end <= end]
            if not pieces:
                return None
            return _Literal("".join(str(piece.value) for piece in pieces), tuple(origin for piece in pieces for origin in piece.origins), any(piece.dynamic for piece in pieces))
        if isinstance(node, ast.JoinedStr):
            starts = _offsets(text)
            start = _ast_offset(text, starts, node.lineno, node.col_offset)
            end = _ast_offset(text, starts, node.end_lineno, node.end_col_offset)
            pieces = [token.literal for token in tokens if start <= token.start and token.end <= end]
            if len(pieces) != 1:
                return None
            return pieces[0]
        return None

    def _field(self, node: ast.AST, bindings: dict[str, _Binding], invalid: set[str]) -> str | None:
        direct = _raw_access(node)
        if direct is not None:
            return direct
        if isinstance(node, ast.Name):
            binding = bindings.get(node.id)
            if binding is not None and binding.kind == "alias":
                return str(binding.value)
        return None

    def _value(self, node: ast.AST, tokens: tuple[_StringToken, ...], text: str, bindings: dict[str, _Binding], invalid: set[str]) -> tuple[_Literal, ...] | None:
        if isinstance(node, ast.Name):
            if node.id in invalid:
                raise _invalid()
            binding = bindings.get(node.id)
            if binding is None or binding.kind != "constant":
                return None
            return (binding.value,)  # type: ignore[return-value]
        literal = self._literal(node, tokens, text)
        if literal is not None:
            return (literal,)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            values: list[_Literal] = []
            for element in node.elts:
                item = self._literal(element, tokens, text)
                if item is None or item.dynamic:
                    return None
                values.append(item)
            return tuple(values)
        return None

    def _comparison_observations(self, path: str, text: str, node: ast.Compare, tokens: tuple[_StringToken, ...], bindings: dict[str, _Binding], invalid: set[str], invalid_governed: set[str]) -> tuple[Observation, ...]:
        def carries_provenance(value: ast.AST) -> bool:
            return any(isinstance(item, ast.Name) and (item.id in _OBJECTS or item.id in invalid_governed - _CONDITIONAL_ROOTS) for item in ast.walk(value))

        def attempted_access(value: ast.AST) -> bool:
            if _governed_like(value):
                return True
            if carries_provenance(value) and isinstance(value, (ast.Call, ast.Subscript, ast.Attribute)):
                keys = [item.value for item in ast.walk(value) if isinstance(item, ast.Constant) and type(item.value) is str]
                if any(key in _FIELDS for key in keys):
                    return True
                if isinstance(value, ast.Subscript) and not (isinstance(value.slice, ast.Constant) and type(value.slice.value) is str):
                    return True
                if isinstance(value, ast.Call) and any(not isinstance(argument, ast.Constant) for argument in value.args):
                    return True
            if isinstance(value, ast.Subscript) and not (isinstance(value.value, ast.Name) and value.value.id in _OBJECTS):
                return carries_provenance(value.value)
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "get" and not (isinstance(value.func.value, ast.Name) and value.func.value.id in _OBJECTS):
                return carries_provenance(value.func.value)
            return False

        if not any(
            attempted_access(candidate)
            or (isinstance(candidate, ast.Subscript) and isinstance(candidate.value, ast.Name) and candidate.value.id in invalid_governed - _CONDITIONAL_ROOTS)
            or (isinstance(candidate, ast.Name) and ((candidate.id in bindings and bindings[candidate.id].kind == "alias") or candidate.id in invalid_governed - _CONDITIONAL_ROOTS))
            for candidate in ast.walk(node)
        ):
            return ()
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise _invalid()
        if any(isinstance(candidate, ast.Name) and candidate.id in invalid_governed - _CONDITIONAL_ROOTS and candidate.id not in bindings for candidate in ast.walk(node)):
            raise _invalid()
        if any(isinstance(candidate, ast.Name) and candidate.id in _OBJECTS and candidate.id in invalid for candidate in ast.walk(node)):
            raise _invalid()
        if any(_governed_like(candidate) and _raw_access(candidate) is None for candidate in ast.walk(node)):
            raise _invalid()
        operation = node.ops[0]
        left_field = self._field(node.left, bindings, invalid)
        right_field = self._field(node.comparators[0], bindings, invalid)
        if isinstance(operation, (ast.Eq, ast.NotEq)):
            if left_field is not None and right_field is None:
                field, values = left_field, self._value(node.comparators[0], tokens, text, bindings, invalid)
            elif right_field is not None and left_field is None:
                field, values = right_field, self._value(node.left, tokens, text, bindings, invalid)
            else:
                raise _invalid()
        elif isinstance(operation, ast.In) and left_field is not None and right_field is None:
            field, values = left_field, self._value(node.comparators[0], tokens, text, bindings, invalid)
            if not isinstance(node.comparators[0], (ast.Tuple, ast.List, ast.Set)):
                raise _invalid()
        else:
            raise _invalid()
        if values is None:
            raise _invalid()
        observations: list[Observation] = []
        for literal in values:
            if literal.dynamic or not literal.origins:
                raise _invalid()
            value = str(literal.value)
            observations.append(Observation(field, value, _span(path, text, literal.origins), "python"))
        return tuple(observations)

    @staticmethod
    def _sort_key(observation: Observation) -> tuple[object, ...]:
        span = observation.span
        return (span.path, span.start_line, span.start_column, span.end_line, span.end_column, observation.family, observation.value)

    @staticmethod
    def _dynamic_sort_key(guard: DynamicGovernedCheck) -> tuple[object, ...]:
        span = guard.span
        return (
            guard.path, guard.left_root, guard.left_field, guard.operator,
            guard.right_root, guard.right_field, guard.syntax_fingerprint,
            span.start_line, span.start_column, span.end_line, span.end_column,
        )

    @staticmethod
    def _relation_sort_key(relation: GovernedRelation) -> tuple[object, ...]:
        span = relation.span
        return (
            relation.path, relation.left_root, relation.left_document_kind, relation.left_field, relation.left_family,
            relation.operator, relation.right_root, relation.right_document_kind, relation.right_field, relation.right_family,
            relation.relation_kind, relation.binding_fingerprint, relation.syntax_fingerprint,
            span.start_line, span.start_column, span.end_line, span.end_column,
        )
