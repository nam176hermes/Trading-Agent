"""Contract tests for typed Nautilus pin observations and registration."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import importlib
import importlib.util
import re

import pytest


# These literals are intentionally independent from the Task 1 acceptance
# oracle.  A production registry must register this complete approved set.
ROLLBACK_IDENTITIES = {
    "engine_version": ("1.227.0", "v1.227.0"),
    "upstream_commit": ("280ae1762df51a492a4ce71506a40b5c8706def5",),
    "tag_object": ("0ccb5b55879c072a6e07fc7cbe5297c53c378107",),
    "rust": ("1.95.0",),
    "cython": ("3.2.4",),
    "setuptools": ("82", "82.0.1"),
    "closure_schema": ("6",),
    "rollback_sha256": (
        "a00d3ab0c5b2ba1e4a4ac4c9af70f5b3fe30717d9b42a328e51696e3894a45e2",
        "083652294183947a352d1443ed0245311bf7ee5a716b66ccc21e814be25851ed",
        "f707cbe27b183ba598c31f1b3b6ec67e36f36e878c4228d3fef80741efb81b28",
        "105579383ea3c5e44104bbe162ab78380f7abb5654e15ac3b600beee54ed93d2",
        "ff2e7753974c7b163bd890f9913dbfbb630f80195708ab67d537d72939e0c56b",
        "0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b",
        "7d3cc69b340536ee6c0e74f4c6954c8a6ed19121df1836a1fab0aad4e43c4f79",
        "69cb87568361ccd6324550fb3823956c64e073b4cf09e674d7eb0883f844c044",
        "18c9ba4af073ae953e0115f577423348b6d454c158da59cbcbd3c9e34a22856f",
        "14d4fd990dccfdbb8b6dfe964a04ae9e80fefb30914cf433de1bc503b8ad03fa",
        "b143564cf3ad63b4ca01afb9a27e7496c9b1c6ff1f3c46cf10b6c4a047545d20",
        "746df241937f6e791f30d66f2b70d50c88c451d6e6575fd903a46ea63e6c3ae2",
        "151b1570623253295ae36ea4b0933ad1f051fa56277ac9d1f54edcedc2c60c9a",
        "c78158a9539332fec665b019236c7d61e530cd2a343c5f6a9f60cde55d297d18",
        "78af5dc64867adbe81b8b825230aabbac2d25b289971ad301dc3998f09f5abe3",
        "ab04b77042fb351a541764054e2bac7259097c749f6ff930c3fc68ef631d592c",
        "2b17f496472473b746e9ac2cf96971b8999e7c94f796580b17c32310372f61a3",
    ),
    "generation": (
        "nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c",
        "runtime-closure-v12-r12-simulation",
        "runtime-closure-v13-paper-compatibility",
    ),
    "profile": ("zero-order", "execution-simulation", "paper-compatibility"),
    "semantic_profile": (
        "nautilus-execution-simulation-v1",
        "nautilus-execution-simulation-v2",
        "nautilus-paper-compatibility-v1",
    ),
    "validator": (
        "nautilus-backtest-result-v1",
        "nautilus-backtest-simulation-result-v1",
        "nautilus-paper-compatibility-result-v1",
    ),
    "selected_source": (
        "1683f1324826b78a715f017a7749fe3d1f7b37f4",
        "a25053355abcfece9b7d5c524f4a3d3c06ce727aec8224012ef9b683240fd880",
    ),
}

CANDIDATE_CONTEXT_IDENTITIES = {
    "engine_version": ("1.231.0", "v1.231.0"),
    "upstream_commit": ("27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",),
    "tag_object": ("d3e1685e979925d7b0ffacd1b3f442547686e18f",),
    "sdist_sha256": ("142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f",),
    "wheel_sha256": ("8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216",),
}

FAMILY_CONTEXTS = {
    "engine_version": ("Nautilus engine version", "1.227.0"),
    "upstream_commit": ("Nautilus upstream_commit", "280ae1762df51a492a4ce71506a40b5c8706def5"),
    "tag_object": ("Nautilus tag_object", "0ccb5b55879c072a6e07fc7cbe5297c53c378107"),
    "rust": ("Nautilus rust", "1.95.0"),
    "cython": ("Nautilus cython", "3.2.4"),
    "setuptools": ("Nautilus setuptools", "82.0.1"),
    "closure_schema": ("Nautilus closure schema", "6"),
    "rollback_sha256": ("Nautilus rollback_sha256", ROLLBACK_IDENTITIES["rollback_sha256"][0]),
    "generation": ("Nautilus generation", "nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c"),
    "profile": ("Nautilus profile", "paper-compatibility"),
    "semantic_profile": ("Nautilus semantic_profile", "nautilus-paper-compatibility-v1"),
    "validator": ("Nautilus validator", "nautilus-paper-compatibility-result-v1"),
    "selected_source": ("Nautilus selected_source", "a25053355abcfece9b7d5c524f4a3d3c06ce727aec8224012ef9b683240fd880"),
    "sdist_sha256": ("Nautilus sdist_sha256", CANDIDATE_CONTEXT_IDENTITIES["sdist_sha256"][0]),
    "wheel_sha256": ("Nautilus wheel_sha256", CANDIDATE_CONTEXT_IDENTITIES["wheel_sha256"][0]),
}


def _registry_api():
    """Load the task-owned API after proving the registry module exists."""
    try:
        spec = importlib.util.find_spec("scripts.nautilus_pin_inventory.registry")
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, "Task 2 registry module is not implemented"
    model = importlib.import_module("scripts.nautilus_pin_inventory.model")
    registry = importlib.import_module("scripts.nautilus_pin_inventory.registry")
    return model, registry


def _observation(model, family: str, value: str):
    return model.Observation(
        family=family,
        value=value,
        span=model.SourceSpan.content("notes.md", 1, 1, 1, len(value) + 1),
        syntax="text",
    )


@pytest.mark.parametrize(
    ("role", "identities"),
    (("ROLLBACK", ROLLBACK_IDENTITIES), ("CANDIDATE_CONTEXT", CANDIDATE_CONTEXT_IDENTITIES)),
)
def test_registry_assigns_every_approved_identity_its_distinct_role(role: str, identities: dict[str, tuple[str, ...]]) -> None:
    """Break caught: an approved identity is missing or given the other role."""
    model, registry = _registry_api()

    for family, values in identities.items():
        for value in values:
            decision = registry.DEFAULT_REGISTRY.classify(_observation(model, family, value))
            assert decision.code == role


def test_family_detection_keeps_the_complete_unregistered_version_token() -> None:
    """Break caught: allowed-value matching truncates a version suffix before registration."""
    model, registry = _registry_api()

    observations = registry.DEFAULT_REGISTRY.detect(
        "engine_version",
        "Nautilus engine version: 1.227.0-beta\n",
        path="notes.md",
        carrier=model.Carrier.CONTENT,
        syntax="text",
    )

    assert observations == (
        model.Observation(
            family="engine_version",
            value="1.227.0-beta",
            span=model.SourceSpan.content("notes.md", 1, 26, 1, 38),
            syntax="text",
        ),
    )
    assert registry.DEFAULT_REGISTRY.classify(observations[0]).code == "UNREGISTERED_IDENTITY"


def test_family_detection_keeps_the_complete_path_token_and_path_span() -> None:
    """Break caught: path detection drops a candidate prefix or uses content coordinates."""
    model, registry = _registry_api()
    path = "nautilus/v1.231.0/README.md"

    observations = registry.DEFAULT_REGISTRY.detect(
        "engine_version",
        path,
        path=path,
        carrier=model.Carrier.PATH,
        syntax="path",
    )

    start = path.index("v1.231.0")
    assert observations == (
        model.Observation(
            family="engine_version",
            value="v1.231.0",
            span=model.SourceSpan(path, model.Carrier.PATH, 0, start, 0, start + len("v1.231.0")),
            syntax="path",
        ),
    )
    assert registry.DEFAULT_REGISTRY.classify(observations[0]).code == "CANDIDATE_CONTEXT"


@pytest.mark.parametrize(
    ("family", "value"),
    (
        ("upstream_commit", "280ae1762df51a492a4ce71506a40b5c8706def5future"),
        ("generation", "nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c-next"),
        ("profile", "paper-compatibility-next"),
    ),
)
def test_every_family_grammar_preserves_suffixes_before_registration(family: str, value: str) -> None:
    """Break caught: one family matcher recognizes only its currently allowed literals."""
    model, registry = _registry_api()
    source = f"Nautilus {family}: {value}\n"

    observations = registry.DEFAULT_REGISTRY.detect(
        family, source, path="notes.md", carrier=model.Carrier.CONTENT, syntax="text"
    )

    assert len(observations) == 1
    assert observations[0].value == value
    assert registry.DEFAULT_REGISTRY.classify(observations[0]).code == "UNREGISTERED_IDENTITY"


@pytest.mark.parametrize("suffix", ("future", "_future", ".future", "-future", "+future"))
@pytest.mark.parametrize(("family", "context"), tuple(FAMILY_CONTEXTS.items()))
def test_every_family_extracts_attached_token_forms_before_registration(
    family: str, context: tuple[str, str], suffix: str
) -> None:
    """Break caught: a family authorizes an allowed prefix of an attached token."""
    model, registry = _registry_api()
    label, registered = context
    complete = registered + suffix

    observations = registry.DEFAULT_REGISTRY.detect(
        family,
        f"{label}: {complete}\n",
        path="notes.md",
        carrier=model.Carrier.CONTENT,
        syntax="text",
    )

    assert len(observations) == 1
    assert observations[0].value == complete
    assert registry.DEFAULT_REGISTRY.classify(observations[0]).code == "UNREGISTERED_IDENTITY"


@pytest.mark.parametrize("suffix", ("é", "١"))
@pytest.mark.parametrize(("family", "context"), tuple(FAMILY_CONTEXTS.items()))
def test_every_family_consumes_unicode_alphanumeric_continuations_before_registration(
    family: str, context: tuple[str, str], suffix: str
) -> None:
    """Break caught: ASCII token boundaries authorize a registered prefix before a Unicode continuation."""
    model, registry = _registry_api()
    label, registered = context
    complete = registered + suffix

    observations = registry.DEFAULT_REGISTRY.detect(
        family,
        f"{label}: {complete}\n",
        path="notes.md",
        carrier=model.Carrier.CONTENT,
        syntax="text",
    )

    assert len(observations) == 1
    assert observations[0].value == complete
    assert registry.DEFAULT_REGISTRY.classify(observations[0]).code == "UNREGISTERED_IDENTITY"


@pytest.mark.parametrize("suffix", ("é", "١"))
def test_path_family_consumes_unicode_alphanumeric_continuations_before_registration(suffix: str) -> None:
    """Break caught: a path token truncates before a Unicode continuation and keeps candidate context."""
    model, registry = _registry_api()
    path = f"nautilus/v1.231.0{suffix}/README.md"

    observations = registry.DEFAULT_REGISTRY.detect(
        "engine_version", path, path=path, carrier=model.Carrier.PATH, syntax="path"
    )

    assert len(observations) == 1
    assert observations[0].value == f"v1.231.0{suffix}"
    assert registry.DEFAULT_REGISTRY.classify(observations[0]).code == "UNREGISTERED_IDENTITY"


@pytest.mark.parametrize("suffix", ("\u0301", "\u200d"))
@pytest.mark.parametrize(("family", "context"), tuple(FAMILY_CONTEXTS.items()))
def test_every_family_rejects_unsupported_unicode_continuations_without_authorizing_a_prefix(
    family: str, context: tuple[str, str], suffix: str
) -> None:
    """Break caught: a combining or format code point ends a token but preserves its allowed prefix."""
    model, registry = _registry_api()
    label, registered = context

    observations = registry.DEFAULT_REGISTRY.detect(
        family,
        f"{label}: {registered}{suffix}\n",
        path="notes.md",
        carrier=model.Carrier.CONTENT,
        syntax="text",
    )

    assert observations == ()


@pytest.mark.parametrize("suffix", ("\u0301", "\u200d"))
def test_path_family_rejects_unsupported_unicode_continuations_without_authorizing_a_prefix(suffix: str) -> None:
    """Break caught: an unsupported path continuation leaves a candidate version registered."""
    model, registry = _registry_api()
    path = f"nautilus/v1.231.0{suffix}/README.md"

    observations = registry.DEFAULT_REGISTRY.detect(
        "engine_version", path, path=path, carrier=model.Carrier.PATH, syntax="path"
    )

    assert observations == ()


def test_path_family_requires_a_trusted_delimiter_before_the_token() -> None:
    """Break caught: path discovery starts an allowed version inside an ungoverned path component."""
    model, registry = _registry_api()
    path = "nautilus/notv1.231.0/README.md"

    observations = registry.DEFAULT_REGISTRY.detect(
        "engine_version", path, path=path, carrier=model.Carrier.PATH, syntax="path"
    )

    assert observations == ()


@pytest.mark.parametrize(
    "factory",
    (
        lambda model: model.SourceSpan("notes.md", model.Carrier.CONTENT, 0, 1, 0, 2),
        lambda model: model.SourceSpan("notes.md", model.Carrier.CONTENT, 1, 0, 1, 2),
        lambda model: model.SourceSpan("notes.md", model.Carrier.CONTENT, 1, 3, 1, 3),
        lambda model: model.SourceSpan("notes.md", model.Carrier.PATH, 1, 0, 1, 2),
        lambda model: model.SourceSpan("notes.md", model.Carrier.PATH, 0, 3, 0, 3),
    ),
)
def test_source_span_rejects_coordinates_for_the_other_carrier_or_empty_ranges(factory) -> None:
    """Break caught: an extractor can publish ambiguous or impossible source coordinates."""
    model, _ = _registry_api()

    with pytest.raises(ValueError):
        factory(model)


def test_models_are_immutable_and_registry_rejects_conflicting_registration() -> None:
    """Break caught: a later extractor can mutate evidence or make a role decision ambiguous."""
    model, registry = _registry_api()
    observation = _observation(model, "engine_version", "1.227.0")

    with pytest.raises(FrozenInstanceError):
        observation.value = "1.231.0"  # type: ignore[misc]
    with pytest.raises(ValueError, match="conflicting"):
        registry.Registry(
            family_specs=registry.DEFAULT_FAMILY_SPECS,
            allowed_identities=(
                model.AllowedIdentity("engine_version", "1.227.0", "ROLLBACK"),
                model.AllowedIdentity("engine_version", "1.227.0", "CANDIDATE_CONTEXT"),
            ),
        )


def test_registry_retains_no_mutable_or_reassignable_classification_state() -> None:
    """Break caught: callers can change registration decisions after registry construction."""
    model, registry_module = _registry_api()
    registry = registry_module.Registry(
        family_specs=registry_module.DEFAULT_FAMILY_SPECS,
        allowed_identities=(model.AllowedIdentity("engine_version", "1.227.0", "ROLLBACK"),),
    )
    unknown = _observation(model, "engine_version", "9.9.9")

    assert registry.classify(unknown).code == "UNREGISTERED_IDENTITY"


def test_registry_rejects_initializer_replay_without_changing_classification() -> None:
    """Break caught: replaying public initialization replaces sealed registration authority."""
    model, registry_module = _registry_api()
    registry = registry_module.Registry(
        family_specs=registry_module.DEFAULT_FAMILY_SPECS,
        allowed_identities=(model.AllowedIdentity("engine_version", "1.227.0", "ROLLBACK"),),
    )
    unknown = _observation(model, "engine_version", "9.9.9")

    assert registry.classify(unknown).code == "UNREGISTERED_IDENTITY"
    with pytest.raises(ValueError, match="already initialized"):
        registry.__init__(
            family_specs=registry_module.DEFAULT_FAMILY_SPECS,
            allowed_identities=(model.AllowedIdentity("engine_version", "9.9.9", "ROLLBACK"),),
        )
    assert registry.classify(unknown).code == "UNREGISTERED_IDENTITY"
    with pytest.raises(AttributeError):
        registry._allowed = {}  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        registry._family_specs = {}  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        del registry._sealed  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        del registry._allowed  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        del registry._family_specs  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        registry._allowed[("engine_version", "9.9.9")] = model.AllowedIdentity("engine_version", "9.9.9", "ROLLBACK")  # type: ignore[index,attr-defined]
    with pytest.raises(TypeError):
        registry._family_specs["other"] = registry_module.DEFAULT_FAMILY_SPECS[0]  # type: ignore[index,attr-defined]
    assert registry.classify(unknown).code == "UNREGISTERED_IDENTITY"


def test_registry_exposes_immutable_family_specs_for_an_injected_registry() -> None:
    """Break caught: Task 3 cannot enumerate a custom registry's exact immutable family definitions."""
    model, registry_module = _registry_api()
    spec = registry_module.FamilySpec("custom", re.compile(r"(?P<value>custom)"))
    registry = registry_module.Registry(
        family_specs=(spec,),
        allowed_identities=(model.AllowedIdentity("custom", "custom", "ROLLBACK"),),
    )

    assert registry.family_specs == (spec,)
    with pytest.raises(AttributeError):
        registry.family_specs += (spec,)
    assert registry.family_specs == (spec,)


class _StringSpoof:
    def __hash__(self) -> int:
        return hash("1.227.0")

    def __eq__(self, other: object) -> bool:
        return other == "1.227.0"


class _PatternClassSpoof:
    @property
    def __class__(self):
        return type(re.compile(""))

    groupindex = {"value": 1}


@pytest.mark.parametrize("value", (True, ["1.227.0"], _StringSpoof()))
def test_public_models_reject_non_string_or_equality_spoof_identity_values(value: object) -> None:
    """Break caught: non-string values reach exact dictionary registration lookup."""
    model, _ = _registry_api()
    span = model.SourceSpan.content("notes.md", 1, 1, 1, 2)

    with pytest.raises(ValueError):
        model.Observation("engine_version", value, span, "text")
    with pytest.raises(ValueError):
        model.AllowedIdentity("engine_version", value, "ROLLBACK")


@pytest.mark.parametrize(
    "factory",
    (
        lambda model: model.Observation(_StringSpoof(), "1.227.0", model.SourceSpan.content("notes.md", 1, 1, 1, 2), "text"),
        lambda model: model.Observation("engine_version", "1.227.0", model.SourceSpan.content("notes.md", 1, 1, 1, 2), _StringSpoof()),
        lambda model: model.AllowedIdentity(_StringSpoof(), "1.227.0", "ROLLBACK"),
        lambda model: model.AllowedIdentity("engine_version", "1.227.0", _StringSpoof()),
        lambda model: model.InventoryDiagnostic(_StringSpoof(), _observation(model, "engine_version", "1.227.0")),
        lambda model: model.InventoryDiagnostic("ROLLBACK", _StringSpoof(), model.AllowedIdentity("engine_version", "1.227.0", "ROLLBACK")),
    ),
)
def test_public_models_reject_non_string_or_spoofed_relevant_inputs(factory) -> None:
    """Break caught: a model accepts spoofed schema values despite its typed public contract."""
    model, _ = _registry_api()

    with pytest.raises(ValueError):
        factory(model)


@pytest.mark.parametrize(
    "factory",
    (
        lambda model: model.SourceSpan("abc", model.Carrier.PATH, 0, 2, 0, 4),
        lambda model: model.SourceSpan("abc", model.Carrier.PATH, 0, True, 0, 2),
        lambda model: model.SourceSpan("abc", model.Carrier.PATH, 0, 0, 0, True),
        lambda model: model.SourceSpan(_StringSpoof(), model.Carrier.PATH, 0, 0, 0, 1),
    ),
)
def test_path_spans_require_exact_integer_coordinates_within_the_claimed_path(factory) -> None:
    """Break caught: a PATH occurrence cannot be located inside the path it claims."""
    model, _ = _registry_api()

    with pytest.raises(ValueError):
        factory(model)


def test_path_detection_requires_the_matched_text_to_be_the_claimed_path() -> None:
    """Break caught: path evidence borrows offsets from different attacker-controlled text."""
    model, registry = _registry_api()

    with pytest.raises(ValueError, match="path text"):
        registry.DEFAULT_REGISTRY.detect(
            "engine_version",
            "attacker/v1.231.0/README.md",
            path="trusted/README.md",
            carrier=model.Carrier.PATH,
            syntax="path",
        )


def test_family_spec_rejects_non_string_family_before_compilation_or_lookup() -> None:
    """Break caught: a family key can use equality or hash spoofing in registry maps."""
    _, registry = _registry_api()

    with pytest.raises(ValueError):
        registry.FamilySpec(_StringSpoof(), re.compile(r"(?P<value>value)"))


@pytest.mark.parametrize(
    "factory",
    (
        lambda registry: registry.FamilySpec("family", _PatternClassSpoof()),
        lambda registry: registry.FamilySpec("family", re.compile(rb"(?P<value>value)")),
        lambda registry: registry.FamilySpec("family", re.compile(r"(?P<value>value)"), _PatternClassSpoof()),
        lambda registry: registry.FamilySpec("family", re.compile(r"(?P<value>value)"), re.compile(rb"(?P<value>value)")),
    ),
)
def test_family_spec_rejects_non_exact_or_bytes_patterns(factory) -> None:
    """Break caught: an imposter or bytes regex reaches string extraction."""
    _, registry = _registry_api()

    with pytest.raises(ValueError):
        factory(registry)


@pytest.mark.parametrize(
    "allowed",
    (
        lambda model: model.AllowedIdentity("engine_version", "9.9.9", "ROLLBACK"),
        lambda model: model.AllowedIdentity("other_family", "1.227.0", "ROLLBACK"),
    ),
)
def test_registered_diagnostic_requires_allowed_identity_to_match_observation(allowed) -> None:
    """Break caught: an exported registered decision attaches authority for a different identity."""
    model, _ = _registry_api()

    with pytest.raises(ValueError, match="match"):
        model.InventoryDiagnostic("ROLLBACK", _observation(model, "engine_version", "1.227.0"), allowed(model))
