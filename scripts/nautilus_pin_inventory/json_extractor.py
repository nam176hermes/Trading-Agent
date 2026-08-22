"""Strict bounded JSON parser with duplicate rejection and exact scalar spans."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from .model import Observation, SourceSpan
from .registry import Registry


_MAX_INPUT = 1_000_000
_MAX_DEPTH = 128
_MAX_INTEGER_DIGITS = 1_024
_PATH_FAMILIES = {
    ("engines/nautilus/engine-build-policy.json", ("engine_version",)): "engine_version",
    ("engines/nautilus/engine-build-policy.json", ("profile",)): "profile",
    ("engines/nautilus/engine-build-policy.json", ("profiles", "[]")): "profile",
    ("engines/nautilus/engine-build-policy.json", ("profile_manifest_schema_version",)): "closure_schema",
    ("engines/nautilus/engine-build-policy.json", ("upstream_tag",)): "engine_version",
    ("engines/nautilus/engine-build-policy.json", ("upstream_tag_object",)): "tag_object",
    ("engines/nautilus/engine-build-policy.json", ("upstream_commit",)): "upstream_commit",
    ("engines/nautilus/engine-build-policy.json", ("source_sha256",)): "rollback_sha256",
    ("engines/nautilus/engine-build-policy.json", ("cargo_lock_sha256",)): "rollback_sha256",
    ("engines/nautilus/engine-build-policy.json", ("pyproject_sha256",)): "rollback_sha256",
    ("engines/nautilus/engine-build-policy.json", ("required_rust_version",)): "rust",
    ("engines/nautilus/engine-build-policy.json", ("required_build_wheels", "cython")): "cython",
    ("engines/nautilus/runtime-closure-policy.json", ("engine_version",)): "engine_version",
    ("engines/nautilus/runtime-closure-policy.json", ("engine_upstream_commit",)): "upstream_commit",
    ("engines/nautilus/runtime-closure-policy.json", ("profile",)): "profile",
    ("engines/nautilus/runtime-closure-policy.json", ("profile_manifest_schema_version",)): "closure_schema",
    ("engines/nautilus/runtime-closure-policy.json", ("result_validator_id",)): "validator",
    ("engines/nautilus/runtime-closure-policy.json", ("semantic_profile",)): "semantic_profile",
    ("engines/nautilus/runtime-closure-policy.json", ("source_commit",)): "selected_source",
    ("engines/nautilus/runtime-closure-policy.json", ("artifact_manifest_sha256",)): "rollback_sha256",
    ("engines/nautilus/runtime-closure-policy.json", ("base_runtime_manifest_sha256",)): "rollback_sha256",
    ("engines/nautilus/runtime-closure-policy.json", ("native_entry_guard", "binary_sha256")): "rollback_sha256",
    ("engines/nautilus/runtime-closure-policy.json", ("native_entry_guard", "source_sha256")): "selected_source",
}

# Public scanning authority for every immediate Nautilus README policy. The
# llvm/wheel policies deliberately have no identity-field mapping, but remain
# strict-JSON scan routes rather than inert files.
GOVERNED_JSON_PATHS = frozenset({
    "engines/nautilus/engine-build-policy.json",
    "engines/nautilus/runtime-closure-policy.json",
    "engines/nautilus/llvm-toolchain-policy.json",
    "engines/nautilus/wheel-cache-policy.json",
})


class JsonExtractionError(ValueError):
    """The JSON source cannot be safely flattened into inventory observations."""


def _invalid() -> JsonExtractionError:
    return JsonExtractionError("invalid JSON inventory source")


@dataclass(frozen=True)
class _Scalar:
    value: object
    start: int
    end: int
    kind: str


class _Parser:
    def __init__(self, text: str) -> None:
        if len(text) > _MAX_INPUT:
            raise _invalid()
        self.text = text
        self.index = 0

    def parse(self) -> object:
        value = self.value(0)
        self.white()
        if self.index != len(self.text):
            raise _invalid()
        return value

    def white(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in " \t\r\n":
            self.index += 1

    def value(self, depth: int) -> object:
        if depth > _MAX_DEPTH:
            raise _invalid()
        self.white()
        if self.index == len(self.text):
            raise _invalid()
        marker = self.text[self.index]
        if marker == "{": return self.object(depth + 1)
        if marker == "[": return self.array(depth + 1)
        if marker == '"': return self.string()
        if marker == "-" or marker.isdigit(): return self.number()
        for literal, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(literal, self.index):
                start = self.index; self.index += len(literal)
                return _Scalar(value, start, self.index, "other")
        raise _invalid()

    def object(self, depth: int) -> dict[str, object]:
        self.index += 1; self.white(); values: dict[str, object] = {}
        if self.index < len(self.text) and self.text[self.index] == "}":
            self.index += 1; return values
        while True:
            self.white(); key = self.string()
            if key.value in values: raise _invalid()
            self.white()
            if self.index == len(self.text) or self.text[self.index] != ":": raise _invalid()
            self.index += 1; values[str(key.value)] = self.value(depth); self.white()
            if self.index < len(self.text) and self.text[self.index] == "}":
                self.index += 1; return values
            if self.index == len(self.text) or self.text[self.index] != ",": raise _invalid()
            self.index += 1

    def array(self, depth: int) -> list[object]:
        self.index += 1; self.white(); values: list[object] = []
        if self.index < len(self.text) and self.text[self.index] == "]":
            self.index += 1; return values
        while True:
            values.append(self.value(depth)); self.white()
            if self.index < len(self.text) and self.text[self.index] == "]":
                self.index += 1; return values
            if self.index == len(self.text) or self.text[self.index] != ",": raise _invalid()
            self.index += 1

    def string(self) -> _Scalar:
        start = self.index
        if self.index == len(self.text) or self.text[self.index] != '"': raise _invalid()
        self.index += 1; pieces: list[str] = []
        while self.index < len(self.text):
            character = self.text[self.index]
            if character == '"':
                self.index += 1; return _Scalar("".join(pieces), start, self.index, "string")
            if ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF: raise _invalid()
            if character != "\\":
                pieces.append(character); self.index += 1; continue
            if self.index + 1 >= len(self.text): raise _invalid()
            marker = self.text[self.index + 1]
            simple = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
            if marker in simple:
                pieces.append(simple[marker]); self.index += 2; continue
            if marker != "u" or self.index + 6 > len(self.text): raise _invalid()
            raw = self.text[self.index + 2 : self.index + 6]
            if not re.fullmatch(r"[0-9a-fA-F]{4}", raw): raise _invalid()
            codepoint = int(raw, 16)
            if 0xD800 <= codepoint <= 0xDBFF:
                if self.text[self.index + 6 : self.index + 8] != "\\u": raise _invalid()
                low_raw = self.text[self.index + 8 : self.index + 12]
                if not re.fullmatch(r"[0-9a-fA-F]{4}", low_raw): raise _invalid()
                low = int(low_raw, 16)
                if not 0xDC00 <= low <= 0xDFFF: raise _invalid()
                pieces.append(chr(0x10000 + ((codepoint - 0xD800) << 10) + low - 0xDC00)); self.index += 12; continue
            if 0xDC00 <= codepoint <= 0xDFFF: raise _invalid()
            pieces.append(chr(codepoint)); self.index += 6
        raise _invalid()

    def number(self) -> _Scalar:
        start = self.index
        match = re.match(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", self.text[self.index :])
        if match is None: raise _invalid()
        token = match.group(0); self.index += len(token)
        if "." in token or "e" in token.casefold() or token == "-0": return _Scalar(token, start, self.index, "noncanonical_number")
        if len(token.lstrip("-")) > _MAX_INTEGER_DIGITS: raise _invalid()
        try: return _Scalar(int(token), start, self.index, "integer")
        except ValueError: raise _invalid() from None


class JsonExtractor:
    """Flatten governed JSON leaves only after one strict recursive parse."""
    __slots__ = ("_registry",)

    def __init__(self, registry: Registry) -> None:
        if type(registry) is not Registry: raise ValueError("extractor registry must be a Registry")
        self._registry = registry

    def extract(self, path: str, text: str) -> tuple[Observation, ...]:
        if type(path) is not str or type(text) is not str: raise ValueError("JSON extraction path and text must be strings")
        value = _Parser(text).parse()
        line_breaks = tuple(index for index, character in enumerate(text) if character == "\n")
        observations: list[Observation] = []
        self._flatten(path, value, (), observations, line_breaks)
        return tuple(sorted(set(observations), key=self._sort_key))

    def _flatten(self, path: str, value: object, parts: tuple[str, ...], observations: list[Observation], line_breaks: tuple[int, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items(): self._flatten(path, child, parts + (key,), observations, line_breaks)
            return
        if isinstance(value, list):
            for child in value: self._flatten(path, child, parts + ("[]",), observations, line_breaks)
            return
        family = _PATH_FAMILIES.get((path, parts))
        if family is None: return
        if not isinstance(value, _Scalar): raise _invalid()
        if family == "closure_schema" and value.kind != "integer": raise _invalid()
        if family != "closure_schema" and value.kind != "string": raise _invalid()
        if value.kind == "string" and value.value == "": raise _invalid()
        start = value.start + (1 if value.kind == "string" else 0)
        end = value.end - (1 if value.kind == "string" else 0)
        start_line, start_column = self._position(line_breaks, start)
        end_line, end_column = self._position(line_breaks, end)
        observations.append(Observation(family, str(value.value), SourceSpan.content(path, start_line, start_column, end_line, end_column), "json"))

    @staticmethod
    def _position(line_breaks: tuple[int, ...], offset: int) -> tuple[int, int]:
        prior = bisect.bisect_left(line_breaks, offset)
        line_start = line_breaks[prior - 1] + 1 if prior else 0
        return prior + 1, offset - line_start + 1

    @staticmethod
    def _sort_key(observation: Observation) -> tuple[object, ...]:
        span = observation.span
        return (span.path, span.start_line, span.start_column, span.end_line, span.end_column, observation.family, observation.value)
