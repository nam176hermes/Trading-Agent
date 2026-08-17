"""RED text/path controls for complete-token identity extraction."""

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


ALL_SLUG_IDENTITIES = tuple(sorted(
    required_identities.ROLLBACK_IDENTITIES["profile"]
    | required_identities.ROLLBACK_IDENTITIES["semantic_profile"]
    | required_identities.ROLLBACK_IDENTITIES["validator"]
))


@pytest.mark.parametrize("mutated", ("1.227.0.1", "1.227.0-beta", "1.227.0+local"))
def test_engine_suffix_drift_is_not_accepted_as_the_rollback_pin(mutated: str, tmp_path, subject) -> None:
    """Break caught: a finite version matcher accepts a different release as 1.227.0."""
    root, inventory, surface = fixture_root(tmp_path, "Nautilus engine version: 1.227.0\n")
    generate_baseline(subject, root, inventory)
    surface.write_text(f"Nautilus engine version: {mutated}\n", encoding="utf-8")
    assert_mutation_rejected(subject, root, inventory)


@pytest.mark.parametrize(
    ("base", "mutated"),
    (("paper-compatibility", "paper-compatibility-next"),
     ("nautilus-paper-compatibility-v1", "nautilus-paper-compatibility-v1evil"),
     ("nautilus-paper-compatibility-result-v1", "nautilus-paper-compatibility-result-v1evil")),
)
def test_slug_suffix_drift_is_not_accepted_as_the_registered_identity(base: str, mutated: str, tmp_path, subject) -> None:
    """Break caught: profile, semantic, or validator prefixes preserve an old record."""
    root, inventory, surface = fixture_root(tmp_path, f"Nautilus identity: {base}\n")
    generate_baseline(subject, root, inventory)
    surface.write_text(f"Nautilus identity: {mutated}\n", encoding="utf-8")
    assert_mutation_rejected(subject, root, inventory)


@pytest.mark.parametrize("value", ("1.231.0", "v1.231.0"))
def test_path_only_candidate_identity_is_inventory_context(value: str, tmp_path, subject) -> None:
    """Break caught: a candidate version in a governed path is invisible to discovery."""
    path = f"nautilus/{value}/README.md"
    start_column = path.index(value)  # path offsets are 0-based and half-open
    root, inventory, _ = fixture_root(tmp_path, "ordinary source\n", name=path)
    generated = run_subject(subject, root, inventory, "--generate")
    assert generated.returncode == 0, generated.stderr
    document = json.loads(inventory.read_text(encoding="utf-8"))
    assert any(
        entry.get("family") == "engine_version"
        and entry.get("value") == value
        and entry.get("role") == "CANDIDATE_CONTEXT"
        and entry.get("carrier") == "PATH"
        and entry.get("spans") == [{"path": path, "carrier": "PATH", "start_line": 0, "start_column": start_column, "end_line": 0, "end_column": start_column + len(value)}]
        for entry in document["entries"]
    )


@settings(max_examples=3, deadline=None, derandomize=True)
@given(
    st.sampled_from(["1.227.0", "v1.227.0"]),
    st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=12),
)
def test_suffix_never_preserves_the_registered_identity(base: str, suffix: str) -> None:
    """Break caught: arbitrary version suffixes retain the rollback record after mutation."""
    with TemporaryDirectory() as directory:
        root, inventory, surface = fixture_root(Path(directory), f"Nautilus engine version: {base}\n")
        subject = selected_subject()
        generate_baseline(subject, root, inventory)
        surface.write_text(f"Nautilus engine version: {base}{suffix}\n", encoding="utf-8")
        assert_mutation_rejected(subject, root, inventory)


@settings(max_examples=3, deadline=None, derandomize=True)
@given(st.sampled_from(ALL_SLUG_IDENTITIES), st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=12))
def test_slug_suffixes_never_preserve_registered_identity(base: str, suffix: str) -> None:
    """Break caught: arbitrary profile/semantic/validator suffixes retain a prefix match."""
    with TemporaryDirectory() as directory:
        root, inventory, surface = fixture_root(Path(directory), f"Nautilus identity: {base}\n")
        subject = selected_subject()
        generate_baseline(subject, root, inventory)
        surface.write_text(f"Nautilus identity: {base}{suffix}\n", encoding="utf-8")
        assert_mutation_rejected(subject, root, inventory)
