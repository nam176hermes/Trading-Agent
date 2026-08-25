"""Explicit family grammars and approved Nautilus pin registrations."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Pattern

from .model import AllowedIdentity, Carrier, InventoryDiagnostic, Observation, SourceSpan


_STRING_PATTERN_TYPE = type(re.compile(""))
_CONTENT_DELIMITERS = frozenset({" ", "\t", "\r", "\n", "\"", "'", ",", ";", ")", "]", "}"})
_PATH_DELIMITERS = frozenset({"/"})


def _require_string_pattern(pattern: object, name: str) -> None:
    if type(pattern) is not _STRING_PATTERN_TYPE or type(pattern.pattern) is not str:
        raise ValueError(f"{name} must be a compiled string regular expression")


def _has_trusted_delimiter(text: str, end: int, carrier: Carrier) -> bool:
    if end == len(text):
        return True
    if carrier is Carrier.PATH:
        return text[end] in _PATH_DELIMITERS
    return text[end] in _CONTENT_DELIMITERS


def _has_trusted_start(text: str, start: int, carrier: Carrier) -> bool:
    return carrier is not Carrier.PATH or start == 0 or text[start - 1] in _PATH_DELIMITERS


@dataclass(frozen=True)
class FamilySpec:
    """Grammar for complete tokens in one governed identity family."""

    family: str
    content_pattern: Pattern[str]
    path_pattern: Pattern[str] | None = None

    def __post_init__(self) -> None:
        if type(self.family) is not str or not self.family:
            raise ValueError("family spec family must not be empty")
        _require_string_pattern(self.content_pattern, "family content pattern")
        if "value" not in self.content_pattern.groupindex:
            raise ValueError("family content pattern must name the value group")
        if self.path_pattern is not None:
            _require_string_pattern(self.path_pattern, "family path pattern")
            if "value" not in self.path_pattern.groupindex:
                raise ValueError("family path pattern must name the value group")

    def detect(self, text: str, *, path: str, carrier: Carrier, syntax: str) -> tuple[Observation, ...]:
        if type(text) is not str or type(path) is not str or type(syntax) is not str:
            raise ValueError("detection text, path, and syntax must be strings")
        if type(carrier) is not Carrier:
            raise ValueError("carrier must be a Carrier")
        if carrier is Carrier.PATH:
            if text != path:
                raise ValueError("path text must match the claimed path")
            pattern = self.path_pattern
        elif carrier is Carrier.CONTENT:
            pattern = self.content_pattern
        else:
            raise ValueError("carrier must be a Carrier")
        if pattern is None:
            return ()
        observations: list[Observation] = []
        for match in pattern.finditer(text):
            start = match.start("value")
            end = match.end("value")
            if not _has_trusted_start(text, start, carrier) or not _has_trusted_delimiter(text, end, carrier):
                continue
            observations.append(
                Observation(
                    family=self.family,
                    value=match.group("value"),
                    span=_span_for_match(path, carrier, text, start, end),
                    syntax=syntax,
                )
            )
        return tuple(observations)


@dataclass(frozen=True)
class IdentityAlias:
    """One exact path observation deriving authority from a canonical identity."""

    family: str
    observed_value: str
    path: str
    canonical_family: str
    canonical_value: str
    span: SourceSpan
    carrier: Carrier = Carrier.PATH
    syntax: str = "path"

    def __post_init__(self) -> None:
        for name, value in (
            ("alias family", self.family),
            ("alias observed value", self.observed_value),
            ("alias path", self.path),
            ("alias canonical family", self.canonical_family),
            ("alias canonical value", self.canonical_value),
            ("alias syntax", self.syntax),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"{name} must be a non-empty string")
        parts = self.path.split("/")
        if (
            not self.path.isascii()
            or self.path.startswith("/")
            or "\\" in self.path
            or (len(self.path) >= 2 and self.path[0].isalpha() and self.path[1] == ":")
            or any(not part or part in (".", "..") for part in parts)
        ):
            raise ValueError("alias path must be an exact repository-relative Git path")
        if type(self.carrier) is not Carrier or self.carrier is not Carrier.PATH:
            raise ValueError("alias carrier must be PATH")
        if self.syntax != "path":
            raise ValueError("alias syntax must be path")
        if type(self.span) is not SourceSpan:
            raise ValueError("alias span must be a SourceSpan")
        if self.span.path != self.path or self.span.carrier is not self.carrier:
            raise ValueError("alias span must bind the exact path carrier")
        if self.path[self.span.start_column:self.span.end_column] != self.observed_value:
            raise ValueError("alias span bytes must equal the observed value")


def _span_for_match(path: str, carrier: Carrier, text: str, start: int, end: int) -> SourceSpan:
    if carrier is Carrier.PATH:
        return SourceSpan.path_span(path, start, end)
    start_line = text.count("\n", 0, start) + 1
    line_start = text.rfind("\n", 0, start) + 1
    end_line = text.count("\n", 0, end) + 1
    end_line_start = text.rfind("\n", 0, end) + 1
    return SourceSpan.content(path, start_line, start - line_start + 1, end_line, end - end_line_start + 1)


class Registry:
    """Immutable family grammar and exact registration lookup."""

    __slots__ = ("_family_specs", "_allowed", "_aliases", "_sealed")

    def __init__(
        self,
        *,
        family_specs: tuple[FamilySpec, ...],
        allowed_identities: tuple[AllowedIdentity, ...],
        aliases: tuple[IdentityAlias, ...] = (),
    ) -> None:
        if getattr(self, "_sealed", False):
            raise ValueError("Registry is already initialized")
        if type(family_specs) is not tuple or type(allowed_identities) is not tuple or type(aliases) is not tuple:
            raise ValueError("registry inputs must be tuples")
        if any(type(spec) is not FamilySpec for spec in family_specs):
            raise ValueError("registry family specifications must be FamilySpec values")
        if any(type(identity) is not AllowedIdentity for identity in allowed_identities):
            raise ValueError("registry allowed identities must be AllowedIdentity values")
        if any(type(alias) is not IdentityAlias for alias in aliases):
            raise ValueError("registry aliases must be IdentityAlias values")
        specs = {spec.family: spec for spec in family_specs}
        if len(specs) != len(family_specs):
            raise ValueError("family specifications must be unique")
        allowed: dict[tuple[str, str], AllowedIdentity] = {}
        for identity in allowed_identities:
            if identity.family not in specs:
                raise ValueError("allowed identity has no family specification")
            key = (identity.family, identity.value)
            prior = allowed.get(key)
            if prior is not None and prior != identity:
                raise ValueError("conflicting registration for identity")
            allowed[key] = identity
        alias_bindings: dict[Observation, tuple[IdentityAlias, AllowedIdentity]] = {}
        alias_contexts: set[tuple[str, str, str]] = set()
        alias_spans: set[tuple[str, Carrier, str, SourceSpan]] = set()
        for alias in sorted(
            aliases,
            key=lambda value: (
                value.path,
                value.span.start_column,
                value.span.end_column,
                value.family,
                value.observed_value,
                value.canonical_family,
                value.canonical_value,
            ),
        ):
            if alias.family not in specs:
                raise ValueError("alias identity has no family specification")
            if alias.family != alias.canonical_family:
                raise ValueError("alias and canonical identity families must match")
            canonical = allowed.get((alias.canonical_family, alias.canonical_value))
            if canonical is None:
                raise ValueError("alias canonical identity is not registered")
            if (alias.family, alias.observed_value) in allowed:
                raise ValueError("alias must not shadow a globally allowed identity")
            observation = Observation(alias.family, alias.observed_value, alias.span, alias.syntax)
            context = (alias.family, alias.observed_value, alias.path)
            span = (alias.path, alias.carrier, alias.syntax, alias.span)
            if observation in alias_bindings or context in alias_contexts or span in alias_spans:
                raise ValueError("duplicate or conflicting alias registration")
            alias_bindings[observation] = (alias, canonical)
            alias_contexts.add(context)
            alias_spans.add(span)
        object.__setattr__(self, "_family_specs", MappingProxyType(specs))
        object.__setattr__(self, "_allowed", MappingProxyType(allowed))
        object.__setattr__(self, "_aliases", MappingProxyType(alias_bindings))
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("Registry is immutable")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Registry is immutable")

    @property
    def family_specs(self) -> tuple[FamilySpec, ...]:
        """The registered family definitions in deterministic registration order."""
        return tuple(self._family_specs.values())

    @property
    def allowed_identities(self) -> tuple[AllowedIdentity, ...]:
        """Read-only exact identity inventory for deterministic completeness checks."""
        return tuple(self._allowed.values())

    @property
    def aliases(self) -> tuple[IdentityAlias, ...]:
        """Exact contextual aliases in deterministic path order."""
        return tuple(alias for alias, _ in self._aliases.values())

    def detect(self, family: str, text: str, *, path: str, carrier: Carrier, syntax: str) -> tuple[Observation, ...]:
        if type(family) is not str:
            raise ValueError("identity family must be a string")
        try:
            spec = self._family_specs[family]
        except KeyError as exc:
            raise ValueError(f"unknown identity family: {family}") from exc
        return spec.detect(text, path=path, carrier=carrier, syntax=syntax)

    def classify(self, observation: Observation) -> InventoryDiagnostic:
        if type(observation) is not Observation:
            raise ValueError("classification input must be an Observation")
        allowed = self._allowed.get((observation.family, observation.value))
        if allowed is None:
            binding = self._aliases.get(observation)
            if binding is None:
                return InventoryDiagnostic("UNREGISTERED_IDENTITY", observation)
            alias, canonical = binding
            allowed = AllowedIdentity(alias.family, alias.observed_value, canonical.role)
        return InventoryDiagnostic(allowed.role, observation, allowed)


def _content(label: str, token: str) -> Pattern[str]:
    return re.compile(rf"(?i)\b{label}\b\s*[:=]\s*[\"']?(?P<value>{token})")


_VERSION = r"v?\d+(?:\.\d+)+(?:[\w._+-])*"
_HEX40 = r"[0-9a-f]{40}[\w._+-]*"
_HEX64 = r"[0-9a-f]{64}[\w._+-]*"
_INTEGER = r"\d+[\w._+-]*"
_TOKEN = r"\w[\w._+-]*"


DEFAULT_FAMILY_SPECS = (
    FamilySpec("engine_version", _content(r"(?:nautilus\s+)?engine[_\s-]*version", _VERSION), re.compile(rf"(?P<value>{_VERSION})")),
    FamilySpec("upstream_commit", _content(r"upstream[_\s-]*commit", _HEX40)),
    FamilySpec("tag_object", _content(r"tag[_\s-]*object", _HEX40)),
    FamilySpec("rust", _content(r"rust", _VERSION)),
    FamilySpec("cython", _content(r"cython", _VERSION)),
    FamilySpec("setuptools", _content(r"setuptools", _INTEGER)),
    FamilySpec("closure_schema", _content(r"(?:closure|manifest|profile_manifest)[_\s-]*schema(?:[_\s-]*version)?", _INTEGER)),
    FamilySpec("rollback_sha256", _content(r"rollback[_\s-]*sha256", _HEX64)),
    FamilySpec("generation", _content(r"generation", _TOKEN)),
    FamilySpec("profile", _content(r"profile", _TOKEN)),
    FamilySpec("semantic_profile", _content(r"semantic[_\s-]*profile", _TOKEN)),
    FamilySpec("validator", _content(r"(?:result[_\s-]*)?validator", _TOKEN)),
    FamilySpec("selected_source", _content(r"selected[_\s-]*source", r"[0-9a-f]{40,64}[\w._+-]*")),
    FamilySpec("sdist_sha256", _content(r"sdist[_\s-]*sha256", _HEX64)),
    FamilySpec("wheel_sha256", _content(r"wheel[_\s-]*sha256", _HEX64)),
)


def _identities(role: str, values: dict[str, tuple[str, ...]]) -> tuple[AllowedIdentity, ...]:
    return tuple(AllowedIdentity(family, value, role) for family, family_values in values.items() for value in family_values)


_ROLLBACK_VALUES = {
    "engine_version": ("1.227.0", "v1.227.0"),
    "upstream_commit": ("280ae1762df51a492a4ce71506a40b5c8706def5",),
    "tag_object": ("0ccb5b55879c072a6e07fc7cbe5297c53c378107",),
    "rust": ("1.95.0",), "cython": ("3.2.4",), "setuptools": ("82", "82.0.1"), "closure_schema": ("6",),
    "rollback_sha256": ("a00d3ab0c5b2ba1e4a4ac4c9af70f5b3fe30717d9b42a328e51696e3894a45e2", "083652294183947a352d1443ed0245311bf7ee5a716b66ccc21e814be25851ed", "f707cbe27b183ba598c31f1b3b6ec67e36f36e878c4228d3fef80741efb81b28", "105579383ea3c5e44104bbe162ab78380f7abb5654e15ac3b600beee54ed93d2", "ff2e7753974c7b163bd890f9913dbfbb630f80195708ab67d537d72939e0c56b", "0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b", "7d3cc69b340536ee6c0e74f4c6954c8a6ed19121df1836a1fab0aad4e43c4f79", "69cb87568361ccd6324550fb3823956c64e073b4cf09e674d7eb0883f844c044", "18c9ba4af073ae953e0115f577423348b6d454c158da59cbcbd3c9e34a22856f", "14d4fd990dccfdbb8b6dfe964a04ae9e80fefb30914cf433de1bc503b8ad03fa", "b143564cf3ad63b4ca01afb9a27e7496c9b1c6ff1f3c46cf10b6c4a047545d20", "746df241937f6e791f30d66f2b70d50c88c451d6e6575fd903a46ea63e6c3ae2", "151b1570623253295ae36ea4b0933ad1f051fa56277ac9d1f54edcedc2c60c9a", "c78158a9539332fec665b019236c7d61e530cd2a343c5f6a9f60cde55d297d18", "78af5dc64867adbe81b8b825230aabbac2d25b289971ad301dc3998f09f5abe3", "ab04b77042fb351a541764054e2bac7259097c749f6ff930c3fc68ef631d592c", "2b17f496472473b746e9ac2cf96971b8999e7c94f796580b17c32310372f61a3"),
    "generation": ("nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c", "runtime-closure-v12-r12-simulation", "runtime-closure-v13-paper-compatibility"),
    "profile": ("zero-order", "execution-simulation", "paper-compatibility"),
    "semantic_profile": ("nautilus-execution-simulation-v1", "nautilus-execution-simulation-v2", "nautilus-paper-compatibility-v1"),
    "validator": ("nautilus-backtest-result-v1", "nautilus-backtest-simulation-result-v1", "nautilus-paper-compatibility-result-v1"),
    "selected_source": ("1683f1324826b78a715f017a7749fe3d1f7b37f4", "a25053355abcfece9b7d5c524f4a3d3c06ce727aec8224012ef9b683240fd880"),
}
_CANDIDATE_VALUES = {
    "engine_version": ("1.231.0", "v1.231.0"), "upstream_commit": ("27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",),
    "tag_object": ("d3e1685e979925d7b0ffacd1b3f442547686e18f",),
    "sdist_sha256": ("142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f",),
    "wheel_sha256": ("8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216",),
}

DEFAULT_REGISTRY = Registry(
    family_specs=DEFAULT_FAMILY_SPECS,
    allowed_identities=_identities("ROLLBACK", _ROLLBACK_VALUES) + _identities("CANDIDATE_CONTEXT", _CANDIDATE_VALUES),
    aliases=tuple(
        IdentityAlias(
            "engine_version",
            value,
            path,
            "engine_version",
            "v1.231.0",
            SourceSpan.path_span(path, start, end),
        )
        for path, value, start, end in (
            ("engines/nautilus/candidates/v1.231/cargo-registry-policy.json", "v1.231", 28, 34),
            ("engines/nautilus/candidates/v1.231/engine-build-policy.json", "v1.231", 28, 34),
            ("engines/nautilus/candidates/v1.231/input-cache-policy.json", "v1.231", 28, 34),
            ("engines/nautilus/candidates/v1.231/toolchain-inputs.json", "v1.231", 28, 34),
            ("engines/nautilus/candidates/v1.231/wheel-cache-policy.json", "v1.231", 28, 34),
            ("engines/nautilus/v1.231-provenance-policy.json", "v1.231-provenance-policy.json", 17, 46),
        )
    ),
)
