"""Fail-closed Python literal and governed-comparison pin extraction."""

from __future__ import annotations

import ast
import re
import tokenize
import unicodedata
from dataclasses import dataclass
from io import StringIO
from typing import Iterable

from .model import Observation, SourceSpan
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
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in _OBJECTS:
        return not (isinstance(node.slice, ast.Constant) and type(node.slice.value) is str and node.slice.value not in _FIELDS)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and isinstance(node.func.value, ast.Name) and node.func.value.id in _OBJECTS:
        return not (node.args and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is str and node.args[0].value not in _FIELDS)
    return _raw_access(node) is not None


class PythonExtractor:
    """Extract exact string-literal pins and closed governed policy comparisons."""

    __slots__ = ("_registry",)

    def __init__(self, registry: Registry) -> None:
        if type(registry) is not Registry:
            raise ValueError("extractor registry must be a Registry")
        self._registry = registry

    def extract(self, path: str, text: str) -> tuple[Observation, ...]:
        if type(path) is not str or type(text) is not str:
            raise ValueError("Python extraction path and text must be strings")
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError:
            raise _invalid() from None
        tokens = _string_tokens(text)
        bindings, invalid_names, invalid_governed_names = self._bindings(tree, tokens, text)
        observations = set(self._literal_observations(path, text, tokens, tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                observations.update(self._comparison_observations(path, text, node, tokens, bindings, invalid_names, invalid_governed_names))
        return tuple(sorted(observations, key=self._sort_key))

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
            return any(isinstance(item, ast.Name) and (item.id in _OBJECTS or item.id in invalid_governed) for item in ast.walk(value))

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
            or (isinstance(candidate, ast.Subscript) and isinstance(candidate.value, ast.Name) and candidate.value.id in invalid_governed)
            or (isinstance(candidate, ast.Name) and ((candidate.id in bindings and bindings[candidate.id].kind == "alias") or candidate.id in invalid_governed))
            for candidate in ast.walk(node)
        ):
            return ()
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise _invalid()
        if any(isinstance(candidate, ast.Name) and candidate.id in invalid_governed and candidate.id not in bindings for candidate in ast.walk(node)):
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
