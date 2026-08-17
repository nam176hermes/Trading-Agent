"""RED Python syntax and semantic-consumer controls."""

from __future__ import annotations

from conftest import fixture_root, generate_baseline, run_subject


def test_chained_governed_comparison_is_rejected(tmp_path, subject) -> None:
    """Break caught: chained schema comparisons are silently skipped, allowing bound drift."""
    root, inventory, _ = fixture_root(tmp_path, 'if 6 <= policy["schema_version"] < 9:\n    pass\n', name="nautilus_consumer.py")
    result = run_subject(subject, root, inventory, "--generate")
    assert result.returncode == 2, result.stderr
    assert "PIN_INVENTORY_INVALID" in result.stderr
    assert "nautilus_consumer.py:1" in result.stderr


def test_reassigned_constant_and_field_alias_are_rejected(tmp_path, subject) -> None:
    """Break caught: a stale module binding is used after a constant or field alias is reassigned."""
    source = 'EXPECTED = 6\nFIELD = policy["schema_version"]\nEXPECTED = dynamic()\nFIELD == EXPECTED\n'
    root, inventory, _ = fixture_root(tmp_path, source, name="nautilus_consumer.py")
    result = run_subject(subject, root, inventory, "--generate")
    assert result.returncode == 2, result.stderr
    assert "PIN_INVENTORY_INVALID" in result.stderr
    assert "nautilus_consumer.py:4" in result.stderr


def test_escaped_f_string_generation_is_not_skipped(tmp_path, subject) -> None:
    """Break caught: escaped f-string constant text bypasses physical literal extraction."""
    root, inventory, surface = fixture_root(tmp_path, 'ENGINE = "1.227.0"\n', name="nautilus_literal.py")
    generate_baseline(subject, root, inventory)
    surface.write_text(
        'ENGINE = "1.227.0"\nVALUE = f"runtime\\x2dclosure-v13-paper-compatibility"\n',
        encoding="utf-8",
    )
    result = run_subject(subject, root, inventory)
    assert result.returncode == 1, result.stderr
    assert "UNKNOWN nautilus_literal.py:2 selected_closure_generation runtime-closure-v13-paper-compatibility" in result.stderr


def test_repeated_multiline_literal_occurrence_changes_the_inventory(tmp_path, subject) -> None:
    """Break caught: a second physical generation occurrence collapses into the first citation."""
    root, inventory, surface = fixture_root(tmp_path, 'ENGINE = "1.227.0"\nVALUE = """runtime\\x2dclosure-v13-paper-compatibility\n"""\n', name="nautilus_literal.py")
    generate_baseline(subject, root, inventory)
    surface.write_text(
        'ENGINE = "1.227.0"\nVALUE = """runtime\\x2dclosure-v13-paper-compatibility\nruntime\\x2dclosure-v13-paper-compatibility\n"""\n',
        encoding="utf-8",
    )
    result = run_subject(subject, root, inventory)
    assert result.returncode == 1, result.stderr
    assert "STALE nautilus_literal.py:2 selected_closure_generation runtime-closure-v13-paper-compatibility locations changed" in result.stderr
