"""RED text/path controls for complete-token identity extraction."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

from hypothesis import given, settings, strategies as st
import pytest

from tests.governance.nautilus_pin_inventory import required_identities
from scripts.nautilus_pin_inventory.model import AllowedIdentity, Carrier, Observation, SourceSpan
from scripts.nautilus_pin_inventory.registry import DEFAULT_REGISTRY, FamilySpec, Registry
from scripts.nautilus_pin_inventory.text_extractor import TextAndPathExtractor


ALL_SLUG_IDENTITIES = tuple(sorted(
    required_identities.ROLLBACK_IDENTITIES["profile"]
    | required_identities.ROLLBACK_IDENTITIES["semantic_profile"]
    | required_identities.ROLLBACK_IDENTITIES["validator"]
))


def _extractor() -> TextAndPathExtractor:
    return TextAndPathExtractor(DEFAULT_REGISTRY)


FAMILY_MUTATION_CASES = (
    ("engine_version", "Nautilus engine version", "1.227.0"),
    ("upstream_commit", "Nautilus upstream_commit", "280ae1762df51a492a4ce71506a40b5c8706def5"),
    ("tag_object", "Nautilus tag_object", "0ccb5b55879c072a6e07fc7cbe5297c53c378107"),
    ("rust", "Nautilus rust", "1.95.0"),
    ("cython", "Nautilus cython", "3.2.4"),
    ("setuptools", "Nautilus setuptools", "82.0.1"),
    ("closure_schema", "Nautilus closure schema", "6"),
    ("rollback_sha256", "Nautilus rollback_sha256", "a00d3ab0c5b2ba1e4a4ac4c9af70f5b3fe30717d9b42a328e51696e3894a45e2"),
    ("generation", "Nautilus generation", "runtime-closure-v12-r12-simulation"),
    ("profile", "Nautilus profile", "paper-compatibility"),
    ("semantic_profile", "Nautilus semantic_profile", "nautilus-paper-compatibility-v1"),
    ("validator", "Nautilus validator", "nautilus-paper-compatibility-result-v1"),
    ("selected_source", "Nautilus selected_source", "1683f1324826b78a715f017a7749fe3d1f7b37f4"),
    ("sdist_sha256", "Nautilus sdist_sha256", "142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f"),
    ("wheel_sha256", "Nautilus wheel_sha256", "8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216"),
)
TOKEN_MUTATION_SUFFIXES = ("x", "١", ".1", "-next", "+local", "_next", "\u0301", "\u200d")
GRAMMAR_CONSUMED_SUFFIXES = ("x", "١", ".1", "-next", "+local", "_next")
SUPPORTED_WRAPPER_PAIRS = (
    ("`", "`"),
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
    ("_", "_"),
    ("__", "__"),
    ("~~", "~~"),
    ("**", "**"),
    ("(", ")"),
    ("[", "]"),
    ("/", "/"),
    ("—", "—"),
)


@pytest.mark.parametrize(
    ("family", "source", "value"),
    (
        ("engine_version", "Nautilus engine version: v2.3.4-beta\n", "v2.3.4-beta"),
        ("upstream_commit", f"Nautilus upstream_commit: {'a' * 40}-next\n", f"{'a' * 40}-next"),
        ("tag_object", f"Nautilus tag_object: {'b' * 40}.next\n", f"{'b' * 40}.next"),
        ("rust", "Nautilus rust: 1.96.0+future\n", "1.96.0+future"),
        ("cython", "Nautilus cython: 3.3.0-next\n", "3.3.0-next"),
        ("setuptools", "Nautilus setuptools: 83.future\n", "83.future"),
        ("closure_schema", "Nautilus closure schema: 7-next\n", "7-next"),
        ("rollback_sha256", f"Nautilus rollback_sha256: {'c' * 64}-next\n", f"{'c' * 64}-next"),
        ("generation", "Nautilus generation: unregistered-generation-v2\n", "unregistered-generation-v2"),
        ("profile", "Nautilus profile: unregistered-profile-v2\n", "unregistered-profile-v2"),
        ("semantic_profile", "Nautilus semantic_profile: unregistered-semantic-v2\n", "unregistered-semantic-v2"),
        ("validator", "Nautilus validator: unregistered-validator-v2\n", "unregistered-validator-v2"),
        ("selected_source", f"Nautilus selected_source: {'d' * 40}+next\n", f"{'d' * 40}+next"),
        ("sdist_sha256", f"Nautilus sdist_sha256: {'e' * 64}.next\n", f"{'e' * 64}.next"),
        ("wheel_sha256", f"Nautilus wheel_sha256: {'f' * 64}-next\n", f"{'f' * 64}-next"),
    ),
)
def test_content_extractor_emits_complete_anchored_tokens_for_every_family(
    family: str, source: str, value: str
) -> None:
    """Break caught: a family loses an attached token suffix before classification."""
    observations = _extractor().extract_content("notes.md", source)

    assert any(observation.family == family and observation.value == value for observation in observations)


def test_content_extractor_uses_exact_one_based_half_open_coordinates() -> None:
    """Break caught: content coordinates are not the exact source location of the full token."""
    source = "before\nNautilus engine version: 1.227.0-beta\nafter\n"

    observations = _extractor().extract_content("notes.md", source)

    assert Observation(
        family="engine_version",
        value="1.227.0-beta",
        span=SourceSpan.content("notes.md", 2, 26, 2, 38),
        syntax="text",
    ) in observations


@pytest.mark.parametrize(
    ("family", "value"),
    tuple(
        (family, value)
        for identities in (
            required_identities.ROLLBACK_IDENTITIES,
            required_identities.CANDIDATE_CONTEXT_IDENTITIES,
        )
        for family, values in identities.items()
        for value in sorted(values)
    ),
)
def test_content_extractor_finds_every_registered_identity_without_an_anchor(
    family: str, value: str
) -> None:
    """Break caught: a known pin outside an anchored line disappears from the inventory."""
    observations = _extractor().extract_content("docs/changelog.md", f"retained pin `{value}`\n")

    assert any(observation.family == family and observation.value == value for observation in observations)


def test_content_extractor_finds_real_markdown_design_line_with_exact_spans() -> None:
    """Break caught: Git-visible Markdown punctuation hides exact rollback pins."""
    source = (
        "**Current rollback engine:** NautilusTrader `1.227.0` / "
        "`280ae1762df51a492a4ce71506a40b5c8706def5`\n"
    )
    version = "1.227.0"
    commit = "280ae1762df51a492a4ce71506a40b5c8706def5"

    assert _extractor().extract_content("docs/implementation/p1-real-nautilus/design.md", source) == (
        Observation(
            "engine_version",
            version,
            SourceSpan.content("docs/implementation/p1-real-nautilus/design.md", 1, source.index(version) + 1, 1, source.index(version) + len(version) + 1),
            "text",
        ),
        Observation(
            "upstream_commit",
            commit,
            SourceSpan.content("docs/implementation/p1-real-nautilus/design.md", 1, source.index(commit) + 1, 1, source.index(commit) + len(commit) + 1),
            "text",
        ),
    )


@pytest.mark.parametrize(
    "opening, closing",
    (("`", "`"), ('"', '"'), ("“", "”"), ("(", ")"), ("/", "/"), ("[", "]"),
     ("", "."), ("**", "**"), ("~~", "~~"), ("_", "_"), ("__", "__"), ("—", "—")),
)
def test_registered_identity_accepts_common_markdown_boundaries_without_prefix_authorization(
    opening: str, closing: str
) -> None:
    """Break caught: punctuation loses a known pin or accepts an attached mutation as that pin."""
    value = "1.227.0"
    path = "docs/guide.md"
    source = f"retained {opening}{value}{closing}\n"

    assert _extractor().extract_content(path, source) == (
        Observation("engine_version", value, SourceSpan.content(path, 1, source.index(value) + 1, 1, source.index(value) + len(value) + 1), "text"),
    )
    for suffix in ("x", ".1", "\u0301", "\u200d"):
        mutated = f"retained {opening}{value}{suffix}{closing}\n"
        observations = _extractor().extract_content(path, mutated)
        assert not any(item.family == "engine_version" and item.value == value for item in observations)


@pytest.mark.parametrize(
    "source",
    (
        "retained _1.227.0\n",
        "retained 1.227.0_\n",
        "retained ~~1.227.0~\n",
        "retained ~1.227.0~~\n",
    ),
)
def test_ambiguous_markdown_wrapper_requires_a_symmetric_pair(source: str) -> None:
    """Break caught: one attached underscore or tilde is treated as a safe token boundary."""
    observations = _extractor().extract_content("docs/guide.md", source)

    assert not any(item.family == "engine_version" and item.value == "1.227.0" for item in observations)


def test_anchored_terminal_period_emits_only_the_exact_registered_identity() -> None:
    """Break caught: ordinary sentence punctuation creates a dotted unregistered duplicate."""
    path = "docs/guide.md"
    source = "Nautilus engine version: 1.227.0.\n"

    assert _extractor().extract_content(path, source) == (
        Observation("engine_version", "1.227.0", SourceSpan.content(path, 1, 26, 1, 33), "text"),
    )


@pytest.mark.parametrize(
    "opening, closing",
    (("", ""), ("_", "_"), ("__", "__"), ("~~", "~~"), ("`", "`"), ('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")),
)
def test_terminal_period_is_wrapper_aware_for_registered_identities(opening: str, closing: str) -> None:
    """Break caught: a period inside a literal authorizes its prefix or outside markup hides the pin."""
    value = "1.227.0"
    path = "docs/guide.md"
    inside = f"retained {opening}{value}.{closing}\n"
    outside = f"retained {opening}{value}{closing}.\n"

    inside_observations = _extractor().extract_content(path, inside)
    if opening:
        assert not any(item.family == "engine_version" and item.value == value for item in inside_observations)
    else:
        assert inside_observations == (
            Observation("engine_version", value, SourceSpan.content(path, 1, 10, 1, 17), "text"),
        )
    assert _extractor().extract_content(path, outside) == (
        Observation("engine_version", value, SourceSpan.content(path, 1, outside.index(value) + 1, 1, outside.index(value) + len(value) + 1), "text"),
    )


@pytest.mark.parametrize(("opening", "closing"), SUPPORTED_WRAPPER_PAIRS)
def test_every_supported_wrapper_has_one_shared_global_and_contextual_contract(opening: str, closing: str) -> None:
    """Break caught: a declared wrapper is visible to globals but hides anchored grammar or period safety."""
    path = "docs/wrappers.md"
    registered = "1.227.0"
    unknown = "9.9.9-next"

    known_source = f"retained {opening}{registered}{closing}\n"
    assert _extractor().extract_content(path, known_source) == (
        Observation("engine_version", registered, SourceSpan.content(path, 1, known_source.index(registered) + 1, 1, known_source.index(registered) + len(registered) + 1), "text"),
    )

    unknown_source = f"Nautilus engine version: {opening}{unknown}{closing}\n"
    assert _extractor().extract_content(path, unknown_source) == (
        Observation("engine_version", unknown, SourceSpan.content(path, 1, unknown_source.index(unknown) + 1, 1, unknown_source.index(unknown) + len(unknown) + 1), "text"),
    )

    collision_source = f"Nautilus rust: {opening}{registered}{closing}\n"
    assert _extractor().extract_content(path, collision_source) == (
        Observation("rust", registered, SourceSpan.content(path, 1, collision_source.index(registered) + 1, 1, collision_source.index(registered) + len(registered) + 1), "text"),
    )

    inside_period = f"retained {opening}{registered}.{closing}\n"
    assert not any(
        item.family == "engine_version" and item.value == registered
        for item in _extractor().extract_content(path, inside_period)
    )

    contextual_outside_period = f"Nautilus rust: {opening}{registered}{closing}.\n"
    assert _extractor().extract_content(path, contextual_outside_period) == (
        Observation("rust", registered, SourceSpan.content(path, 1, contextual_outside_period.index(registered) + 1, 1, contextual_outside_period.index(registered) + len(registered) + 1), "text"),
    )

    registered_outside_period = f"retained {opening}{registered}{closing}.\n"
    assert _extractor().extract_content(path, registered_outside_period) == (
        Observation("engine_version", registered, SourceSpan.content(path, 1, registered_outside_period.index(registered) + 1, 1, registered_outside_period.index(registered) + len(registered) + 1), "text"),
    )


@pytest.mark.parametrize(
    ("family", "label", "value"),
    (
        ("engine_version", "Nautilus engine version", "1.95.0"),
        ("rust", "Nautilus rust", "1.227.0"),
        ("cython", "Nautilus cython", "1.227.0"),
        ("setuptools", "Nautilus setuptools", "1.227.0"),
    ),
)
def test_contextual_content_family_owns_a_coordinate_over_global_registered_collisions(
    family: str, label: str, value: str
) -> None:
    """Break caught: a globally registered family is added beside a real contextual grammar match."""
    path = "docs/collisions.md"
    source = f"{label}: {value}\n"

    assert _extractor().extract_content(path, source) == (
        Observation(family, value, SourceSpan.content(path, 1, len(label) + 3, 1, len(label) + len(value) + 3), "text"),
    )


def test_contextual_terminal_period_normalizes_before_global_classification() -> None:
    """Break caught: a rust anchor with prose punctuation emits dotted rust plus global engine evidence."""
    path = "docs/collisions.md"
    source = "Nautilus rust: 1.227.0.\n"

    assert _extractor().extract_content(path, source) == (
        Observation("rust", "1.227.0", SourceSpan.content(path, 1, 16, 1, 23), "text"),
    )


@pytest.mark.parametrize(
    ("source", "value"),
    (
        ("Nautilus engine version: `9.9.9-next`\n", "9.9.9-next"),
        ("Nautilus engine version: _9.9.9-next_\n", "9.9.9-next"),
        ("Nautilus engine version: ~~9.9.9-next~~\n", "9.9.9-next"),
        ("Nautilus engine version: 9.9.9-next—\n", "9.9.9-next"),
        ("Nautilus engine version: 9.9.9-next.\n", "9.9.9-next"),
    ),
)
def test_anchored_unknowns_preserve_the_full_token_across_supported_boundaries(source: str, value: str) -> None:
    """Break caught: real family grammar matches disappear behind Markdown or retain prose punctuation."""
    path = "docs/mutation.md"
    start = source.index(value) + 1

    assert _extractor().extract_content(path, source) == (
        Observation("engine_version", value, SourceSpan.content(path, 1, start, 1, start + len(value)), "text"),
    )


@pytest.mark.parametrize("value", ("1.227.0", "1.95.0", "3.2.4", "82.0.1"))
def test_contextual_path_family_owns_a_coordinate_over_global_registered_collisions(value: str) -> None:
    """Break caught: a governed path engine grammar emits rust/cython/setuptools global registrations too."""
    path = f"engines/nautilus/{value}/README.md"
    start = path.index(value)

    assert _extractor().extract_path(path) == (
        Observation("engine_version", value, SourceSpan.path_span(path, start, start + len(value)), "path"),
    )


def test_unknown_content_requires_the_familys_actual_anchor_without_governed_fan_out() -> None:
    """Break caught: governed prose becomes unrelated identity observations."""
    extractor = _extractor()

    assert extractor.extract_content("engines/nautilus/README.md", "ordinary source\n") == ()
    assert extractor.extract_content("engines/nautilus/README.md", "dependency version 9.9.9\n") == ()
    assert extractor.extract_content("apps/dashboard/package-lock.json", "dependency version 9.9.9\n") == ()
    assert extractor.extract_content("apps/dashboard/package-lock.json", "engine version: 9.9.9\n") == (
        Observation("engine_version", "9.9.9", SourceSpan.content("apps/dashboard/package-lock.json", 1, 17, 1, 22), "text"),
    )


def test_path_extractor_keeps_complete_token_and_zero_based_half_open_coordinates() -> None:
    """Break caught: path extraction truncates a suffix or emits content coordinates."""
    path = "engines/nautilus/v9.9.9-next/README.md"

    observations = _extractor().extract_path(path)

    start = path.index("v9.9.9-next")
    assert observations == (
        Observation(
            family="engine_version",
            value="v9.9.9-next",
            span=SourceSpan.path_span(path, start, start + len("v9.9.9-next")),
            syntax="path",
        ),
    )


def test_path_extractor_finds_registered_tokens_but_limits_unknowns_to_governed_paths() -> None:
    """Break caught: exact path pins disappear or arbitrary external path versions become candidates."""
    extractor = _extractor()

    registered = extractor.extract_path("docs/releases/v1.231.0/README.md")
    assert any(
        observation.family == "engine_version"
        and observation.value == "v1.231.0"
        and observation.span.carrier is Carrier.PATH
        for observation in registered
    )
    assert extractor.extract_path("docs/releases/v9.9.9/README.md") == ()


def test_injected_family_specs_use_their_actual_content_and_path_grammars() -> None:
    """Break caught: extraction fabricates a family-name anchor instead of using the injected grammar."""
    registry = Registry(
        family_specs=(
            FamilySpec(
                "release",
                re.compile(r"(?i)\bNautilus release\s*:\s*(?P<value>r[0-9]+)"),
                re.compile(r"releases/(?P<value>r[0-9]+)"),
            ),
        ),
        allowed_identities=(AllowedIdentity("release", "r1", "ROLLBACK"),),
    )
    extractor = TextAndPathExtractor(registry)

    assert extractor.extract_content("docs/release.md", "retained `r1`\n") == (
        Observation("release", "r1", SourceSpan.content("docs/release.md", 1, 11, 1, 13), "text"),
    )
    assert extractor.extract_content("docs/release.md", "Nautilus release: r2\n") == (
        Observation("release", "r2", SourceSpan.content("docs/release.md", 1, 19, 1, 21), "text"),
    )
    assert extractor.extract_content("engines/nautilus/release.md", "retained r2\n") == ()
    assert extractor.extract_path("engines/nautilus/releases/r2/README.md") == (
        Observation("release", "r2", SourceSpan.path_span("engines/nautilus/releases/r2/README.md", 26, 28), "path"),
    )
    assert extractor.extract_path("docs/releases/r2/README.md") == ()


@pytest.mark.parametrize(
    ("source", "value"),
    (
        ("Nautilus release: `r2`\n", "r2"),
        ("Nautilus release: r2.\n", "r2"),
    ),
)
def test_injected_family_grammar_extracts_wrapped_and_prose_terminated_unknowns(source: str, value: str) -> None:
    """Break caught: wrapper normalization bypasses or loses an injected grammar's actual anchor."""
    registry = Registry(
        family_specs=(FamilySpec("release", re.compile(r"Nautilus release:\s*(?P<value>r[0-9]+[._+-]*)")),),
        allowed_identities=(AllowedIdentity("release", "r1", "ROLLBACK"),),
    )
    path = "docs/release.md"
    start = source.index(value) + 1

    assert TextAndPathExtractor(registry).extract_content(path, source) == (
        Observation("release", value, SourceSpan.content(path, 1, start, 1, start + len(value)), "text"),
    )


@pytest.mark.parametrize(("source", "start"), (("r1\n", 1), ("__r1__\n", 3)))
def test_injected_contextual_grammar_requires_a_complete_start_boundary(source: str, start: int) -> None:
    """Break caught: an injected value grammar authorizes r1 as a suffix of xr1."""
    registry = Registry(
        family_specs=(FamilySpec("release", re.compile(r"(?P<value>r[0-9]+)")),),
        allowed_identities=(AllowedIdentity("release", "r1", "ROLLBACK"),),
    )
    extractor = TextAndPathExtractor(registry)

    assert extractor.extract_content("docs/release.md", "xr1\n") == ()
    assert extractor.extract_content("docs/release.md", source) == (
        Observation("release", "r1", SourceSpan.content("docs/release.md", 1, start, 1, start + 2), "text"),
    )


@pytest.mark.parametrize("label", ("Nautilus semantic profile", "Nautilus semantic-profile"))
def test_semantic_profile_contextual_grammar_owns_its_overlapping_profile_span(label: str) -> None:
    """Break caught: profile and semantic_profile both own one contextual value coordinate."""
    path = "docs/profiles.md"
    value = "nautilus-paper-compatibility-v1"
    source = f"{label}: {value}\n"

    assert _extractor().extract_content(path, source) == (
        Observation("semantic_profile", value, SourceSpan.content(path, 1, source.index(value) + 1, 1, source.index(value) + len(value) + 1), "text"),
    )


def test_injected_multiline_grammar_has_hash_seed_independent_ordering() -> None:
    """Break caught: equal partial sort keys allow set iteration to change public observation order."""
    program = """
import json
import re
from scripts.nautilus_pin_inventory.model import AllowedIdentity
from scripts.nautilus_pin_inventory.registry import FamilySpec, Registry
from scripts.nautilus_pin_inventory.text_extractor import TextAndPathExtractor

registry = Registry(
    family_specs=(FamilySpec('release', re.compile(r'(?s)(?P<value>r1(?:\\nr1)?)')),),
    allowed_identities=(AllowedIdentity('release', 'r1', 'ROLLBACK'),),
)
observations = TextAndPathExtractor(registry).extract_content('docs/release.md', 'r1\\nr1\\n')
print(json.dumps([(item.value, item.span.start_line, item.span.start_column, item.span.end_line, item.span.end_column, item.syntax) for item in observations]))
"""
    outputs = []
    for seed in ("1", "2", "17", "101"):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        completed = subprocess.run(
            [sys.executable, "-c", program], cwd=Path(__file__).resolve().parents[3], env=environment,
            text=True, capture_output=True, check=False,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout)

    assert outputs.count(outputs[0]) == len(outputs)


@pytest.mark.parametrize(
    "path",
    (
        "../engines/nautilus/v9.9.9/README.md",
        "./engines/nautilus/v9.9.9/README.md",
        "engines/nautilus/../dashboard/v9.9.9/README.md",
        "engines/./nautilus/v9.9.9/README.md",
        "engines//nautilus/v9.9.9/README.md",
        "/engines/nautilus/v9.9.9/README.md",
        "C:/engines/nautilus/v9.9.9/README.md",
        "c:/engines/nautilus/v9.9.9/README.md",
        "C:engines/nautilus/v9.9.9/README.md",
        "c:engines/nautilus/v9.9.9/README.md",
        "engines\\nautilus\\v9.9.9\\README.md",
        "engines/Ｎautilus/v9.9.9/README.md",
    ),
)
def test_invalid_paths_are_rejected_before_governance_or_extraction(path: str) -> None:
    """Break caught: raw traversal or non-canonical components bypass governed-path boundaries."""
    extractor = _extractor()

    assert extractor.extract_path(path) == ()
    assert extractor.extract_content(path, "engine version: 9.9.9\n") == ()


@pytest.mark.parametrize("case", FAMILY_MUTATION_CASES)
@settings(max_examples=7, deadline=None, derandomize=True)
@given(st.sampled_from(GRAMMAR_CONSUMED_SUFFIXES))
def test_direct_extractor_never_authorizes_an_attached_registered_identity_prefix(
    case: tuple[str, str, str], suffix: str
) -> None:
    """Break caught: any family returns its old registered identity after an attached mutation."""
    family, label, registered = case
    mutated = registered + suffix
    observations = _extractor().extract_content("docs/mutation.md", f"{label}: `{mutated}`\n")

    family_observations = tuple(item for item in observations if item.family == family)
    assert all(item.value != registered for item in family_observations)
    assert family_observations == (
        Observation(family, mutated, SourceSpan.content("docs/mutation.md", 1, len(label) + 4, 1, len(label) + len(mutated) + 4), "text"),
    )


@pytest.mark.parametrize("case", FAMILY_MUTATION_CASES)
@pytest.mark.parametrize("suffix", ("\u0301", "\u200d"))
def test_every_family_rejects_unicode_mark_and_format_attached_prefixes(
    case: tuple[str, str, str], suffix: str
) -> None:
    """Break caught: an M/C continuation recovers a registered identity outside its grammar."""
    family, label, registered = case
    observations = _extractor().extract_content("docs/mutation.md", f"{label}: `{registered}{suffix}`\n")

    assert not any(item.family == family and item.value == registered for item in observations)


@pytest.mark.parametrize("case", FAMILY_MUTATION_CASES)
@pytest.mark.parametrize("suffix", TOKEN_MUTATION_SUFFIXES)
def test_every_family_rejects_fixed_attached_token_mutations(
    case: tuple[str, str, str], suffix: str
) -> None:
    """Break caught: a fixed punctuation or Unicode suffix leaves a registered family prefix."""
    family, label, registered = case
    observations = _extractor().extract_content("docs/mutation.md", f"{label}: {registered}{suffix}\n")

    assert not any(item.family == family and item.value == registered for item in observations)


@pytest.mark.parametrize("case", FAMILY_MUTATION_CASES)
@pytest.mark.parametrize("suffix", TOKEN_MUTATION_SUFFIXES)
def test_every_family_path_rejects_fixed_attached_token_mutations(
    case: tuple[str, str, str], suffix: str
) -> None:
    """Break caught: an all-family PATH continuation preserves its old registered identity."""
    family, _, registered = case
    observations = _extractor().extract_path(f"docs/pins/{registered}{suffix}/README.md")

    assert not any(item.family == family and item.value == registered for item in observations)


@settings(max_examples=7, deadline=None, derandomize=True)
@given(st.sampled_from(TOKEN_MUTATION_SUFFIXES))
def test_path_grammar_never_authorizes_a_registered_engine_prefix(suffix: str) -> None:
    """Break caught: the default PATH grammar retains its registered candidate after mutation."""
    registered = "v1.231.0"
    observations = _extractor().extract_path(f"engines/nautilus/{registered}{suffix}/README.md")

    assert not any(item.family == "engine_version" and item.value == registered for item in observations)


@pytest.mark.parametrize("case", FAMILY_MUTATION_CASES)
def test_every_family_finds_its_registered_identity_on_the_path_carrier(
    case: tuple[str, str, str]
) -> None:
    """Break caught: a registered path-only identity disappears outside a family-specific path grammar."""
    family, _, registered = case
    path = f"docs/pins/{registered}/README.md"
    start = path.index(registered)

    assert _extractor().extract_path(path) == (
        Observation(family, registered, SourceSpan.path_span(path, start, start + len(registered)), "path"),
    )
