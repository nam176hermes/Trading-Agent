"""Immutable evidence records for Nautilus pin inventory extraction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


def _require_string(value: object, name: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _require_int(value: object, name: str) -> None:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")


class Carrier(StrEnum):
    CONTENT = "CONTENT"
    PATH = "PATH"


@dataclass(frozen=True)
class SourceSpan:
    path: str
    carrier: Carrier
    start_line: int
    start_column: int
    end_line: int
    end_column: int

    def __post_init__(self) -> None:
        _require_string(self.path, "span path")
        if type(self.carrier) is not Carrier:
            raise ValueError("span carrier must be a Carrier")
        for name, value in (
            ("start line", self.start_line),
            ("start column", self.start_column),
            ("end line", self.end_line),
            ("end column", self.end_column),
        ):
            _require_int(value, name)
        if self.carrier is Carrier.CONTENT:
            if self.start_line < 1 or self.end_line < 1:
                raise ValueError("content spans require positive line numbers")
            if self.start_column < 1 or self.end_column < 1:
                raise ValueError("content spans require positive column numbers")
        else:
            if self.start_line != 0 or self.end_line != 0:
                raise ValueError("path spans require line zero")
            if self.start_column < 0 or self.end_column < 0:
                raise ValueError("path span columns must be non-negative")
            if self.end_column > len(self.path):
                raise ValueError("path span columns must be within the path")
        if (self.end_line, self.end_column) <= (self.start_line, self.start_column):
            raise ValueError("span must have a non-empty forward range")

    @classmethod
    def content(
        cls, path: str, start_line: int, start_column: int, end_line: int, end_column: int
    ) -> SourceSpan:
        return cls(path, Carrier.CONTENT, start_line, start_column, end_line, end_column)

    @classmethod
    def path_span(cls, path: str, start_column: int, end_column: int) -> SourceSpan:
        return cls(path, Carrier.PATH, 0, start_column, 0, end_column)


@dataclass(frozen=True)
class Observation:
    family: str
    value: str
    span: SourceSpan
    syntax: str

    def __post_init__(self) -> None:
        _require_string(self.family, "observation family")
        _require_string(self.value, "observation value")
        if type(self.span) is not SourceSpan:
            raise ValueError("observation span must be a SourceSpan")
        _require_string(self.syntax, "observation syntax")


@dataclass(frozen=True)
class DynamicGovernedCheck:
    """One proved direct same-family governed comparison."""

    path: str
    left_root: str
    left_field: str
    operator: Literal["==", "!="]
    right_root: str
    right_field: str
    syntax_fingerprint: str
    span: SourceSpan

    def __post_init__(self) -> None:
        for name, value in (
            ("dynamic guard path", self.path),
            ("dynamic guard left root", self.left_root),
            ("dynamic guard left field", self.left_field),
            ("dynamic guard right root", self.right_root),
            ("dynamic guard right field", self.right_field),
            ("dynamic guard syntax fingerprint", self.syntax_fingerprint),
        ):
            _require_string(value, name)
        if self.operator not in ("==", "!="):
            raise ValueError("dynamic guard operator is invalid")
        if type(self.span) is not SourceSpan or self.span.path != self.path:
            raise ValueError("dynamic guard span must bind the claimed path")


@dataclass(frozen=True)
class GovernedRelation:
    """A proved cross-family consistency relation; never a literal pin."""

    path: str
    left_root: str
    left_field: str
    left_family: str
    operator: Literal["==", "!="]
    right_root: str
    right_field: str
    right_family: str
    relation_kind: Literal["cross_family_consistency_guard"]
    binding_fingerprint: str
    syntax_fingerprint: str
    span: SourceSpan

    def __post_init__(self) -> None:
        for name, value in (
            ("relation path", self.path),
            ("relation left root", self.left_root),
            ("relation left field", self.left_field),
            ("relation left family", self.left_family),
            ("relation right root", self.right_root),
            ("relation right field", self.right_field),
            ("relation right family", self.right_family),
            ("relation binding fingerprint", self.binding_fingerprint),
            ("relation syntax fingerprint", self.syntax_fingerprint),
        ):
            _require_string(value, name)
        if self.operator not in ("==", "!="):
            raise ValueError("relation operator is invalid")
        if self.relation_kind != "cross_family_consistency_guard":
            raise ValueError("relation kind is invalid")
        if type(self.span) is not SourceSpan or self.span.path != self.path:
            raise ValueError("relation span must bind the claimed path")


@dataclass(frozen=True)
class PythonExtractionResult:
    """Immutable Python extraction channels with no legacy tuple compatibility."""

    observations: tuple[Observation, ...]
    dynamic_guards: tuple[DynamicGovernedCheck, ...]
    governed_relations: tuple[GovernedRelation, ...]

    def __post_init__(self) -> None:
        if type(self.observations) is not tuple or any(type(value) is not Observation for value in self.observations):
            raise ValueError("Python observations must be an Observation tuple")
        if type(self.dynamic_guards) is not tuple or any(type(value) is not DynamicGovernedCheck for value in self.dynamic_guards):
            raise ValueError("Python dynamic guards must be a DynamicGovernedCheck tuple")
        if type(self.governed_relations) is not tuple or any(type(value) is not GovernedRelation for value in self.governed_relations):
            raise ValueError("Python governed relations must be a GovernedRelation tuple")


IdentityRole = Literal["ROLLBACK", "CANDIDATE_CONTEXT"]


@dataclass(frozen=True)
class AllowedIdentity:
    family: str
    value: str
    role: IdentityRole

    def __post_init__(self) -> None:
        _require_string(self.family, "allowed identity family")
        _require_string(self.value, "allowed identity value")
        if type(self.role) is not str:
            raise ValueError("allowed identity role must be a string")
        if self.role not in ("ROLLBACK", "CANDIDATE_CONTEXT"):
            raise ValueError("allowed identity role is invalid")


@dataclass(frozen=True)
class InventoryDiagnostic:
    """A registration decision attached to the immutable observed token."""

    code: Literal["ROLLBACK", "CANDIDATE_CONTEXT", "UNREGISTERED_IDENTITY"]
    observation: Observation
    allowed_identity: AllowedIdentity | None = None

    def __post_init__(self) -> None:
        if type(self.code) is not str:
            raise ValueError("inventory diagnostic code must be a string")
        if self.code not in ("ROLLBACK", "CANDIDATE_CONTEXT", "UNREGISTERED_IDENTITY"):
            raise ValueError("inventory diagnostic code is invalid")
        if type(self.observation) is not Observation:
            raise ValueError("inventory diagnostic observation must be an Observation")
        if self.allowed_identity is not None and type(self.allowed_identity) is not AllowedIdentity:
            raise ValueError("inventory diagnostic allowed identity must be an AllowedIdentity")
        if self.code == "UNREGISTERED_IDENTITY" and self.allowed_identity is not None:
            raise ValueError("unregistered observations cannot have an allowed identity")
        if self.code != "UNREGISTERED_IDENTITY":
            if self.allowed_identity is None:
                raise ValueError("registered observations require an allowed identity")
            if self.allowed_identity.role != self.code:
                raise ValueError("registered diagnostic role must match the allowed identity")
            if (
                self.allowed_identity.family != self.observation.family
                or self.allowed_identity.value != self.observation.value
            ):
                raise ValueError("registered diagnostic identity must match the observation")
