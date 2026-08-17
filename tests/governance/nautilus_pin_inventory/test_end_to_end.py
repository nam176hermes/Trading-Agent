"""RED acceptance tests for the immutable abaaeb6 command line subject."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings, strategies as st
import pytest

from conftest import (
    assert_mutation_rejected,
    fixture_root,
    generate_baseline,
    required_identities,
    run_subject,
    selected_subject,
)


ALL_GIT_OBJECT_IDENTITIES = tuple(sorted(
    required_identities.ROLLBACK_IDENTITIES["upstream_commit"]
    | required_identities.CANDIDATE_CONTEXT_IDENTITIES["upstream_commit"]
    | required_identities.ROLLBACK_IDENTITIES["tag_object"]
    | required_identities.CANDIDATE_CONTEXT_IDENTITIES["tag_object"]
    | {next(value for value in required_identities.ROLLBACK_IDENTITIES["selected_source"] if len(value) == 40)}
))
ALL_DIGEST_IDENTITIES = tuple(sorted(
    required_identities.ROLLBACK_IDENTITIES["rollback_sha256"]
    | required_identities.CANDIDATE_CONTEXT_IDENTITIES["sdist_sha256"]
    | required_identities.CANDIDATE_CONTEXT_IDENTITIES["wheel_sha256"]
    | {next(value for value in required_identities.ROLLBACK_IDENTITIES["selected_source"] if len(value) == 64)}
))
ALL_ORACLE_IDENTITIES = tuple(
    (role, family, value)
    for role, identities in (
        ("ROLLBACK", required_identities.ROLLBACK_IDENTITIES),
        ("CANDIDATE_CONTEXT", required_identities.CANDIDATE_CONTEXT_IDENTITIES),
    )
    for family, values in sorted(identities.items())
    for value in sorted(values)
)


@pytest.mark.parametrize(
    ("family", "value"),
    tuple((family, value) for family, values in (
        ("engine_version", ("1.231.0", "v1.231.0")),
        ("upstream_commit", ("27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",)),
        ("tag_object", ("d3e1685e979925d7b0ffacd1b3f442547686e18f",)),
        ("sdist_sha256", ("142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f",)),
        ("wheel_sha256", ("8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216",)),
    ) for value in values),
)
def test_every_candidate_context_identity_is_a_generated_content_record(family: str, value: str, tmp_path, subject) -> None:
    """Break caught: an allowed candidate-context identity cannot be generated as a typed source record."""
    source = f"Nautilus {family}: {value}\n"
    start_column = source.index(value) + 1  # content columns are 1-based and half-open
    root, inventory, _ = fixture_root(tmp_path, source)
    generated = run_subject(subject, root, inventory, "--generate")
    assert generated.returncode == 0, generated.stderr
    document = json.loads(inventory.read_text(encoding="utf-8"))
    assert any(
        entry.get("family") == family
        and entry.get("value") == value
        and entry.get("role") == "CANDIDATE_CONTEXT"
        and entry.get("carrier") == "CONTENT"
        and entry.get("spans") == [{"path": "surface.md", "carrier": "CONTENT", "start_line": 1, "start_column": start_column, "end_line": 1, "end_column": start_column + len(value)}]
        for entry in document["entries"]
    )


@settings(max_examples=3, deadline=None, derandomize=True)
@given(st.sampled_from(ALL_GIT_OBJECT_IDENTITIES), st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=12))
def test_every_git_object_identity_suffix_is_not_silently_omitted(base: str, suffix: str) -> None:
    """Break caught: a rollback/candidate commit or tag-object suffix preserves an obsolete observation."""
    with TemporaryDirectory() as directory:
        root, inventory, surface = fixture_root(Path(directory), f"Nautilus provenance value: {base}\n")
        subject = selected_subject()
        generate_baseline(subject, root, inventory)
        surface.write_text(f"Nautilus provenance value: {base}{suffix}\n", encoding="utf-8")
        assert_mutation_rejected(subject, root, inventory)


@settings(max_examples=3, deadline=None, derandomize=True)
@given(st.sampled_from(ALL_DIGEST_IDENTITIES), st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=12))
def test_every_digest_identity_suffix_is_not_silently_omitted(base: str, suffix: str) -> None:
    """Break caught: a rollback/candidate digest suffix preserves an obsolete observation."""
    with TemporaryDirectory() as directory:
        root, inventory, surface = fixture_root(Path(directory), f"Nautilus provenance value: {base}\n")
        subject = selected_subject()
        generate_baseline(subject, root, inventory)
        surface.write_text(f"Nautilus provenance value: {base}{suffix}\n", encoding="utf-8")
        assert_mutation_rejected(subject, root, inventory)


@pytest.mark.parametrize(("role", "family", "value"), ALL_ORACLE_IDENTITIES)
def test_every_oracle_identity_has_an_executable_mutation_control(role: str, family: str, value: str) -> None:
    """Break caught: a required rollback/candidate identity family has no drift control."""
    with TemporaryDirectory() as directory:
        root, inventory, surface = fixture_root(Path(directory), f"Nautilus {family}: {value}\n")
        subject = selected_subject()
        generate_baseline(subject, root, inventory)
        surface.write_text(f"Nautilus {family}: {value}mutation\n", encoding="utf-8")
        assert_mutation_rejected(subject, root, inventory)
