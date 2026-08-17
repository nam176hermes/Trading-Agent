"""Exact, context-aware text and path observations for Nautilus identities."""

from __future__ import annotations

import unicodedata

from .model import Carrier, Observation, SourceSpan
from .registry import Registry


_PATH_DELIMITER = "/"
_TOKEN_PUNCTUATION = frozenset({"_", ".", "-", "+"})
SUPPORTED_WRAPPER_PAIRS = (
    ("**", "**"),
    ("__", "__"),
    ("~~", "~~"),
    ("`", "`"),
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
    ("_", "_"),
    ("(", ")"),
    ("[", "]"),
    ("/", "/"),
    ("—", "—"),
)


def _require_string(value: object, name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be a string")
    return value


def _is_token_character(character: str) -> bool:
    return character.isalnum() or character in _TOKEN_PUNCTUATION


def _is_content_delimiter(character: str) -> bool:
    """Accept Unicode punctuation/symbol boundaries, never token continuations."""
    if character.isspace():
        return True
    category = unicodedata.category(character)
    if category[0] in {"L", "N", "M", "C"} or character in _TOKEN_PUNCTUATION or character == "~":
        return False
    return category[0] in {"P", "S"}


def _is_trusted_start(text: str, start: int, carrier: Carrier) -> bool:
    if start == 0:
        return True
    if carrier is Carrier.PATH:
        return text[start - 1] == _PATH_DELIMITER
    return _is_content_delimiter(text[start - 1])


def _is_trusted_end(text: str, end: int, carrier: Carrier) -> bool:
    if end == len(text):
        return True
    if carrier is Carrier.PATH:
        return text[end] == _PATH_DELIMITER
    return _is_content_delimiter(text[end])


def _has_wrapper_outer_end(text: str, end: int) -> bool:
    if end == len(text) or _is_content_delimiter(text[end]):
        return True
    return text[end] == "." and (end + 1 == len(text) or _is_content_delimiter(text[end + 1]))


def _is_supported_wrapped_literal(text: str, start: int, end: int) -> bool:
    """Return whether a token is inside one balanced, bounded supported wrapper."""
    for opening, closing in SUPPORTED_WRAPPER_PAIRS:
        opening_start = start - len(opening)
        closing_end = end + len(closing)
        if opening_start < 0 or closing_end > len(text):
            continue
        if text[opening_start:start] != opening or text[end:closing_end] != closing:
            continue
        if (opening_start == 0 or _is_content_delimiter(text[opening_start - 1])) and _has_wrapper_outer_end(text, closing_end):
            return True
    return False


def _is_prose_terminal_period(text: str, start: int, end: int) -> bool:
    return (
        end - start > 1
        and text[end - 1] == "."
        and text[end - 2] != "."
        and _is_trusted_end(text, end, Carrier.CONTENT)
        and not _is_supported_wrapped_literal(text, start, end)
    )


def _normalized_wrapper_view(text: str) -> str:
    """Replace only balanced, bounded wrappers with spaces while retaining offsets."""
    characters = list(text)
    for opening, closing in SUPPORTED_WRAPPER_PAIRS:
        position = 0
        while position < len(text):
            start = text.find(opening, position)
            if start < 0:
                break
            inner_start = start + len(opening)
            end = text.find(closing, inner_start)
            if end < 0:
                break
            inner = text[inner_start:end]
            outer_end = end + len(closing)
            if (
                inner
                and all(_is_token_character(character) for character in inner)
                and (start == 0 or _is_content_delimiter(text[start - 1]))
                and _has_wrapper_outer_end(text, outer_end)
            ):
                characters[start:inner_start] = " " * len(opening)
                characters[end:outer_end] = " " * len(closing)
                position = outer_end
            else:
                position = start + 1
    return "".join(characters)


def _has_complete_contextual_end(text: str, end: int) -> bool:
    if end == len(text):
        return True
    return _is_content_delimiter(text[end])


def _registered_token_spans(text: str, carrier: Carrier) -> tuple[tuple[int, int], ...]:
    """Return whole lexical candidates, recognized wrappers, and prose-period trims."""
    spans: list[tuple[int, int]] = []
    view = _normalized_wrapper_view(text) if carrier is Carrier.CONTENT else text
    position = 0
    while position < len(view):
        if not _is_token_character(view[position]):
            position += 1
            continue
        start = position
        position += 1
        while position < len(view) and _is_token_character(view[position]):
            position += 1
        if _is_trusted_start(view, start, carrier) and _is_trusted_end(view, position, carrier):
            spans.append((start, position))
            if carrier is Carrier.CONTENT and _is_prose_terminal_period(text, start, position):
                spans.append((start, position - 1))
    return tuple(spans)


def _span_for(path: str, carrier: Carrier, text: str, start: int, end: int) -> SourceSpan:
    if carrier is Carrier.PATH:
        return SourceSpan.path_span(path, start, end)
    start_line = text.count("\n", 0, start) + 1
    start_line_offset = text.rfind("\n", 0, start) + 1
    end_line = text.count("\n", 0, end) + 1
    end_line_offset = text.rfind("\n", 0, end) + 1
    return SourceSpan.content(path, start_line, start - start_line_offset + 1, end_line, end - end_line_offset + 1)


def _content_offset(text: str, line: int, column: int) -> int:
    offset = 0
    for _ in range(line - 1):
        offset = text.index("\n", offset) + 1
    return offset + column - 1


def _is_nautilus_governed(path: str) -> bool:
    return any(part.casefold() == "nautilus" for part in path.split(_PATH_DELIMITER))


def _is_valid_git_path(path: str) -> bool:
    if not path or path.startswith(_PATH_DELIMITER) or path.endswith(_PATH_DELIMITER) or "\\" in path:
        return False
    if len(path) >= 2 and path[0].isalpha() and path[1] == ":":
        return False
    parts = path.split(_PATH_DELIMITER)
    return all(part and part not in (".", "..") and part.isascii() for part in parts)


class TextAndPathExtractor:
    """Extract complete family tokens from immutable Git text and path snapshots."""

    __slots__ = ("_family_specs", "_registry")

    def __init__(self, registry: Registry) -> None:
        if type(registry) is not Registry:
            raise ValueError("extractor registry must be a Registry")
        self._registry = registry
        self._family_specs = registry.family_specs

    def extract_content(self, path: str, text: str) -> tuple[Observation, ...]:
        """Extract anchored candidates and globally registered identities from text."""
        path = _require_string(path, "content path")
        text = _require_string(text, "content text")
        if not _is_valid_git_path(path):
            return ()
        anchored = self._contextual_content_observations(path, text)
        return self._combine(
            path=path,
            text=text,
            carrier=Carrier.CONTENT,
            syntax="text",
            anchored=anchored,
        )

    def extract_path(self, path: str) -> tuple[Observation, ...]:
        """Extract path candidates with zero-line, zero-based half-open coordinates."""
        path = _require_string(path, "path")
        if not _is_valid_git_path(path):
            return ()
        governed = _is_nautilus_governed(path)
        detected = tuple(
            observation
            for spec in self._family_specs
            for observation in spec.detect(path, path=path, carrier=Carrier.PATH, syntax="path")
        )
        anchored = tuple(
            observation
            for observation in detected
            if governed or self._registry.classify(observation).code != "UNREGISTERED_IDENTITY"
        )
        return self._combine(
            path=path,
            text=path,
            carrier=Carrier.PATH,
            syntax="path",
            anchored=anchored,
        )

    def _combine(
        self,
        *,
        path: str,
        text: str,
        carrier: Carrier,
        syntax: str,
        anchored: tuple[Observation, ...],
    ) -> tuple[Observation, ...]:
        observations = set(anchored)
        contextual_spans = frozenset(observation.span for observation in anchored)
        for start, end in _registered_token_spans(text, carrier):
            value = text[start:end]
            span = _span_for(path, carrier, text, start, end)
            if span in contextual_spans:
                continue
            for spec in self._family_specs:
                observation = Observation(spec.family, value, span, syntax)
                if self._registry.classify(observation).code != "UNREGISTERED_IDENTITY":
                    observations.add(observation)
        return tuple(sorted(observations, key=self._sort_key))

    def _contextual_content_observations(self, path: str, text: str) -> tuple[Observation, ...]:
        view = _normalized_wrapper_view(text)
        owners: dict[SourceSpan, tuple[tuple[int, int, int], Observation]] = {}
        for family_index, spec in enumerate(self._family_specs):
            for match in spec.content_pattern.finditer(view):
                start = match.start("value")
                end = match.end("value")
                if not _is_trusted_start(view, start, Carrier.CONTENT) or not _has_complete_contextual_end(view, end):
                    continue
                if _is_prose_terminal_period(text, start, end):
                    end -= 1
                observation = Observation(
                    spec.family, text[start:end], _span_for(path, Carrier.CONTENT, text, start, end), "text"
                )
                # Same-span grammars compete by the longest actual anchor; this
                # is the earliest whole match for one value start. Registry
                # order resolves an otherwise identical grammar deterministically.
                ownership_key = (-(start - match.start()), match.start(), family_index)
                incumbent = owners.get(observation.span)
                if incumbent is None or ownership_key < incumbent[0]:
                    owners[observation.span] = (ownership_key, observation)
        return tuple(observation for _, observation in owners.values())

    def _sort_key(self, observation: Observation) -> tuple[str, str, int, int, int, int, str, str, str]:
        span = observation.span
        return (
            span.path,
            span.carrier.value,
            span.start_line,
            span.start_column,
            span.end_line,
            span.end_column,
            observation.family,
            observation.value,
            observation.syntax,
        )
