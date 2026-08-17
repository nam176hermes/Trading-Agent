"""RED JSON parser controls."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import fixture_root, generate_baseline, run_subject
from scripts.nautilus_pin_inventory.json_extractor import JsonExtractionError, JsonExtractor
from scripts.nautilus_pin_inventory.model import Observation, SourceSpan
from scripts.nautilus_pin_inventory.registry import DEFAULT_REGISTRY


def _extract(source: str):
    return JsonExtractor(DEFAULT_REGISTRY).extract("engines/nautilus/engine-build-policy.json", source)


def test_json_extractor_keeps_each_repeated_scalar_span() -> None:
    """Break caught: line matching collapses repeated array leaves or cites the JSON key."""
    source = '{"profiles":["paper-compatibility","paper-compatibility"],"profile_manifest_schema_version":6}'
    first = source.index("paper-compatibility")
    second = source.index("paper-compatibility", first + 1)
    schema = source.rindex("6")

    assert _extract(source) == (
        Observation("profile", "paper-compatibility", SourceSpan.content("engines/nautilus/engine-build-policy.json", 1, first + 1, 1, first + len("paper-compatibility") + 1), "json"),
        Observation("profile", "paper-compatibility", SourceSpan.content("engines/nautilus/engine-build-policy.json", 1, second + 1, 1, second + len("paper-compatibility") + 1), "json"),
        Observation("closure_schema", "6", SourceSpan.content("engines/nautilus/engine-build-policy.json", 1, schema + 1, 1, schema + 2), "json"),
    )


def test_json_extractor_uses_the_physical_extent_of_a_string_escape() -> None:
    """Break caught: a JSON escape is cited using decoded columns rather than source columns."""
    source = '{"profile":"paper\\u002dcompatibility"}'
    start = source.index("paper\\u002dcompatibility")
    assert _extract(source) == (
        Observation(
            "profile",
            "paper-compatibility",
            SourceSpan.content("engines/nautilus/engine-build-policy.json", 1, start + 1, 1, start + len("paper\\u002dcompatibility") + 1),
            "json",
        ),
    )


@pytest.mark.parametrize(
    "source",
    (
        '{"profile":"zero-order","profile":"zero-order"}',
        '{"profile":"zero-order","profile":"paper-compatibility"}',
        '{"outer":{"profile":"zero-order","profile":"zero-order"}}',
        '{"outer":{"profile":"zero-order","profile":"paper-compatibility"}}',
        '{"profile_manifest_schema_version":6.0}',
        '{"profile_manifest_schema_version":6e0}',
        '{"profile_manifest_schema_version":NaN}',
        '{"profile_manifest_schema_version":Infinity}',
    ),
)
def test_json_extractor_rejects_duplicate_and_noncanonical_governed_values(source: str) -> None:
    """Break caught: permissive JSON parsing flattens duplicates or fractional governed integers."""
    with pytest.raises(JsonExtractionError, match="invalid JSON inventory source"):
        _extract(source)


def test_json_real_policy_authority_map_is_exact_and_complete() -> None:
    """Break caught: generic keys create false authority while actual rollback pins are omitted."""
    root = Path(__file__).resolve().parents[3]
    engine_path = "engines/nautilus/engine-build-policy.json"
    closure_path = "engines/nautilus/runtime-closure-policy.json"
    engine = JsonExtractor(DEFAULT_REGISTRY).extract(engine_path, (root / engine_path).read_text(encoding="utf-8"))
    closure = JsonExtractor(DEFAULT_REGISTRY).extract(closure_path, (root / closure_path).read_text(encoding="utf-8"))
    engine_pairs = {(item.family, item.value) for item in engine}
    closure_pairs = {(item.family, item.value) for item in closure}
    assert {
        ("engine_version", "1.227.0"), ("engine_version", "v1.227.0"),
        ("tag_object", "0ccb5b55879c072a6e07fc7cbe5297c53c378107"),
        ("upstream_commit", "280ae1762df51a492a4ce71506a40b5c8706def5"),
        ("rust", "1.95.0"), ("cython", "3.2.4"),
    } <= engine_pairs
    assert not any(item.family == "closure_schema" and item.value == "1" for item in engine)
    assert {
        ("engine_version", "1.227.0"),
        ("upstream_commit", "280ae1762df51a492a4ce71506a40b5c8706def5"),
        ("closure_schema", "6"),
        ("selected_source", "1683f1324826b78a715f017a7749fe3d1f7b37f4"),
    } <= closure_pairs
    assert not any(item.family == "closure_schema" and item.value == "1" for item in closure)


@pytest.mark.parametrize("source", ('{"profile":1}', '{"profile":1.2300}', '{"profile":1e3}', '{"profile":true}'))
def test_json_string_families_reject_nonstring_source_lexemes(source: str) -> None:
    """Break caught: numeric lexemes are decoded and rewritten into pin evidence."""
    with pytest.raises(JsonExtractionError, match="invalid JSON inventory source"):
        _extract(source)


@pytest.mark.parametrize(
    "source",
    (
        '{"profile":"\\ud83d\\ude00","\\ud83d\\ude00":"first","😀":"second"}',
        '{"profile":"\\ud83d"}',
        '{"profile":"\\ude00"}',
        '{"profile_manifest_schema_version":-0}',
    ),
)
def test_json_unicode_and_canonical_integer_identity_fail_closed(source: str) -> None:
    """Break caught: surrogate identity and a rewritten integer lexeme create false evidence."""
    with pytest.raises(JsonExtractionError, match="invalid JSON inventory source"):
        _extract(source)


def test_json_valid_surrogate_pair_is_one_scalar_and_exact_span() -> None:
    """Break caught: JSON escape decoding leaves a pair as two surrogate code units."""
    source = '{"profile":"\\ud83d\\ude00"}'
    item = _extract(source)[0]
    assert item.value == "😀"
    assert (item.span.start_column, item.span.end_column) == (source.index("\\ud83d") + 1, source.index("\\ud83d") + len("\\ud83d\\ude00") + 1)


@pytest.mark.parametrize("source", ('{"profile":[' * 300 + '"zero-order"' + ']}' * 300, '{"profile_manifest_schema_version":' + '9' * 5001 + '}'))
def test_json_resource_limits_are_stable_errors(source: str) -> None:
    """Break caught: crafted depth/digit inputs escape as recursion or conversion errors."""
    with pytest.raises(JsonExtractionError, match="invalid JSON inventory source"):
        _extract(source)


def test_json_empty_governed_string_is_an_extractor_error() -> None:
    """Break caught: model span validation leaks instead of the stable extractor error."""
    with pytest.raises(JsonExtractionError, match="invalid JSON inventory source"):
        _extract('{"profile":""}')


def test_duplicate_json_object_member_is_rejected_before_flattening(tmp_path, subject) -> None:
    """Break caught: duplicate governed JSON keys collapse to one inventory citation."""
    root, inventory, surface = fixture_root(
        tmp_path,
        '{"engine_version":"1.227.0","upstream_commit":"280ae1762df51a492a4ce71506a40b5c8706def5","profile_manifest_schema_version":6}',
        name="engines/nautilus/engine-build-policy.json",
    )
    generate_baseline(subject, root, inventory)
    surface.write_text(
        '{"engine_version":"1.227.0","engine_version":"1.227.0","upstream_commit":"280ae1762df51a492a4ce71506a40b5c8706def5","profile_manifest_schema_version":6}',
        encoding="utf-8",
    )
    result = run_subject(subject, root, inventory)
    assert result.returncode == 2, f"duplicate member was not rejected: {result.stdout}\n{result.stderr}"
