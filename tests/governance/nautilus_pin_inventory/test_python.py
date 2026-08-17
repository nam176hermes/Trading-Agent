"""RED Python syntax and semantic-consumer controls."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import fixture_root, generate_baseline, run_subject
from scripts.nautilus_pin_inventory.model import Observation, SourceSpan
from scripts.nautilus_pin_inventory.registry import DEFAULT_REGISTRY
from scripts.nautilus_pin_inventory.python_extractor import PythonExtractionError, PythonExtractor


def _extract(source: str):
    return PythonExtractor(DEFAULT_REGISTRY).extract("nautilus_consumer.py", source)


def test_python_literals_keep_their_physical_decoded_origins() -> None:
    """Break caught: decoded literal text is cited at an AST-wide or collapsed span."""
    source = (
        'A = "paper-" "compatibility"\n'
        'B = r"zero-order"\n'
        'C = "execution\\x2dsimulation"\n'
        'D = f"paper-compatibility"\n'
        'E = """runtime-closure-v13-paper-compatibility\n'
        'runtime-closure-v13-paper-compatibility"""\n'
    )

    observations = _extract(source)
    def span(value: str, start: int = 0) -> tuple[int, int, int, int]:
        offset = source.index(value, start)
        return (source.count("\n", 0, offset) + 1, offset - source.rfind("\n", 0, offset), source.count("\n", 0, offset + len(value)) + 1, offset + len(value) - (source.rfind("\n", 0, offset + len(value)) + 1) + 1)

    expected = {
        ("profile", "paper-compatibility", 1, 6, 1, 28),
        ("profile", "zero-order", *span("zero-order")),
        ("profile", "execution-simulation", *span("execution\\x2dsimulation")),
        ("profile", "paper-compatibility", *span("paper-compatibility", source.index('D ='))),
        ("generation", "runtime-closure-v13-paper-compatibility", *span("runtime-closure-v13-paper-compatibility", source.index('E ='))),
        ("generation", "runtime-closure-v13-paper-compatibility", *span("runtime-closure-v13-paper-compatibility", source.index('E =') + 1)),
    }
    actual = {
        (
            item.family,
            item.value,
            item.span.start_line,
            item.span.start_column,
            item.span.end_line,
            item.span.end_column,
        )
        for item in observations
    }
    assert expected <= actual


def test_python_governed_comparison_accepts_only_closed_literal_grammar() -> None:
    """Break caught: supported policy comparisons disappear or dynamic syntax becomes an inventory pin."""
    source = (
        'EXPECTED = 6\n'
        'FIELD = policy["schema_version"]\n'
        'if FIELD == EXPECTED: pass\n'
        'if "paper-compatibility" == policy["profile"]: pass\n'
        'if policy["profile"] in ("zero-order", "paper-compatibility"): pass\n'
    )

    assert _extract(source) == (
        Observation("closure_schema", "6", SourceSpan.content("nautilus_consumer.py", 1, 12, 1, 13), "python"),
        Observation("profile", "paper-compatibility", SourceSpan.content("nautilus_consumer.py", 4, 5, 4, 24), "python"),
        Observation("profile", "zero-order", SourceSpan.content("nautilus_consumer.py", 5, 27, 5, 37), "python"),
        Observation("profile", "paper-compatibility", SourceSpan.content("nautilus_consumer.py", 5, 41, 5, 60), "python"),
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ('if policy["profile"] != "zero-order": pass\n', {"zero-order"}),
        ('if "paper-compatibility" != policy["profile"]: pass\n', {"paper-compatibility"}),
        ('if policy["profile"] in ["zero-order", "paper-compatibility"]: pass\n', {"zero-order", "paper-compatibility"}),
        ('if policy["profile"] in {"zero-order", "paper-compatibility"}: pass\n', {"zero-order", "paper-compatibility"}),
    ),
)
def test_python_closed_grammar_accepts_reverse_inequality_and_all_literal_membership_containers(source: str, expected: set[str]) -> None:
    """Break caught: a permitted reverse comparison or literal container is rejected as dynamic syntax."""
    observations = _extract(source)
    assert {item.value for item in observations} >= expected


@pytest.mark.parametrize(
    "source",
    (
        'if 6 <= policy["schema_version"] < 9: pass\n',
        'EXPECTED = 6\nEXPECTED = dynamic()\nif policy["schema_version"] == EXPECTED: pass\n',
        'FIELD = policy["schema_version"]\nif policy["schema_version"] == FIELD: pass\n',
        'def check():\n    EXPECTED = 6\n    if policy["schema_version"] == EXPECTED: pass\n',
        'EXPECTED = 6\ndel EXPECTED\nif policy["schema_version"] == EXPECTED: pass\n',
        'if policy["schema_version"] == f"{6}": pass\n',
        'LEFT = RIGHT = 6\nif policy["schema_version"] == LEFT: pass\n',
        'FIELD = policy["schema_version"]\nFIELD = policy["profile"]\nif FIELD == 6: pass\n',
        'def check():\n    FIELD = policy["schema_version"]\n    if FIELD == 6: pass\n',
        'key = "schema_version"\nif policy[key] == 6: pass\n',
        'if policy["schema_version"] > 6: pass\n',
        'if 6 in policy["schema_version"]: pass\n',
        'if policy["profile"] == "paper-\\xZZcompatibility": pass\n',
        'if policy["profile"] == "unterminated\n',
    ),
)
def test_python_governed_syntax_fails_closed_with_a_stable_error(source: str) -> None:
    """Break caught: ungoverned aliases, chains, or dynamic literals silently bypass semantic extraction."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        _extract(source)


def test_python_unicode_escape_uses_the_physical_escape_extent() -> None:
    """Break caught: Unicode-decoded pins use a decoded width instead of their source bytes."""
    source = 'if policy["profile"] == "\\u007aero-order": pass\n'
    start = source.index("\\u007aero-order")
    expected = Observation(
        "profile",
        "zero-order",
        SourceSpan.content("nautilus_consumer.py", 1, start + 1, 1, start + len("\\u007aero-order") + 1),
        "python",
    )
    assert expected in _extract(source)


@pytest.mark.parametrize(
    "relative",
    (
        "scripts/materialize_nautilus_runtime_closure.py",
        "services/job_worker/nautilus_closure.py",
    ),
)
def test_python_real_governed_consumers_reject_existing_nonclosed_governed_predicates(relative: str) -> None:
    """Break caught: real paths silently relax a governed dynamic RHS."""
    root = Path(__file__).resolve().parents[3]
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(relative, (root / relative).read_text(encoding="utf-8"))


@pytest.mark.parametrize("relative", ("scripts/prepare_nautilus_llvm_toolchain.py", "scripts/materialize_nautilus_runtime_closure.py", "services/job_worker/nautilus_closure.py"))
def test_python_ordinary_static_policy_fields_are_ignored_on_every_path(relative: str) -> None:
    """Break caught: ignoring ordinary fields depends on a path allowlist."""
    assert PythonExtractor(DEFAULT_REGISTRY).extract(relative, 'if policy["ordinary_metadata"] != source: pass\n') == ()


def test_python_real_root_parameter_invalidates_the_closed_comparison_globally() -> None:
    """Break caught: a protected-root binding leaves an exact alias with stale authority."""
    root = Path(__file__).resolve().parents[3]
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(
            "scripts/prepare_nautilus_llvm_toolchain.py",
            (root / "scripts/prepare_nautilus_llvm_toolchain.py").read_text(encoding="utf-8"),
        )


@pytest.mark.parametrize(
    "source",
    (
        'EXPECTED = 6\nEXPECTED += 1\nif policy["schema_version"] == EXPECTED: pass\n',
        'EXPECTED = 6\nif (EXPECTED := 7): pass\nif policy["schema_version"] == EXPECTED: pass\n',
        'EXPECTED = 6\nfor EXPECTED in values: pass\nif policy["schema_version"] == EXPECTED: pass\n',
        'EXPECTED = 6\nwith context() as EXPECTED: pass\nif policy["schema_version"] == EXPECTED: pass\n',
        'EXPECTED = 6\ntry: pass\nexcept Exception as EXPECTED: pass\nif policy["schema_version"] == EXPECTED: pass\n',
        'EXPECTED = 6\nimport math as EXPECTED\nif policy["schema_version"] == EXPECTED: pass\n',
        'EXPECTED = 6\ndef EXPECTED(): pass\nif policy["schema_version"] == EXPECTED: pass\n',
        'p = policy\nif p["schema_version"] == 6: pass\n',
        'if policy.get("schema_version", dynamic()) == 6: pass\n',
        'if policy.get("schema_version", default=6) == 6: pass\n',
    ),
)
def test_python_every_alternate_binding_or_nonclosed_get_fails_closed(source: str) -> None:
    """Break caught: a lexical Store can retain stale governed evidence."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        _extract(source)


def test_python_adjacent_comments_and_backslash_continuation_are_lossless() -> None:
    """Break caught: legal adjacent literals separated by comments or continuations disappear."""
    source = (
        'X = ("paper-"\n     # separator\n     "compatibility")\n'
        'Y = "zero-" \\\n'
        '    "order"\n'
    )
    values = {item.value for item in _extract(source)}
    assert {"paper-compatibility", "zero-order"} <= values


def test_python_dynamic_f_string_never_joins_static_segments_into_a_pin() -> None:
    """Break caught: interpolation is erased and fabricates one physical identity span."""
    assert not _extract('X = f"paper-{dynamic}compatibility"\n')
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        _extract('if policy["profile"] == f"paper-{dynamic}compatibility": pass\n')


@pytest.mark.parametrize("digits", ("172", "72", "2"))
def test_python_octal_decoding_matches_ast_and_keeps_full_origin(digits: str) -> None:
    """Break caught: octal parsing loses a digit and hides a registered literal."""
    source = f'if policy["profile"] == "\\{digits}ero-order": pass\n'
    if digits == "172":
        assert any(item.value == "zero-order" for item in _extract(source))
    else:
        assert _extract(source)


@pytest.mark.parametrize(
    "relative",
    (
        "scripts/prepare_nautilus_llvm_toolchain.py",
        "scripts/materialize_nautilus_runtime_closure.py",
        "services/job_worker/nautilus_closure.py",
    ),
)
@pytest.mark.parametrize("source", ('if policy["schema_version"] > 6: pass\n', 'if policy.get("schema_version", dynamic()) == 6: pass\n'))
def test_python_real_paths_do_not_relax_malformed_governed_access(relative: str, source: str) -> None:
    """Break caught: a path allowlist can make governed syntax disappear."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(relative, source)


@pytest.mark.parametrize(
    "source",
    (
        'p, q = policy, None\nif p["schema_version"] == 6: pass\n',
        'p = q = policy\nif p["schema_version"] == 6: pass\n',
        'def check():\n    p = policy\n    if p["schema_version"] == 6: pass\n',
        'EXPECTED = 6\ndef check(EXPECTED):\n    if policy["schema_version"] == EXPECTED: pass\n',
        'EXPECTED = 6\nmatch value:\n    case EXPECTED:\n        pass\nif policy["schema_version"] == EXPECTED: pass\n',
        'KEY = "schema_version"\nif policy.get(KEY) == 6: pass\n',
        'policy = replacement\nif policy["schema_version"] == 6: pass\n',
    ),
)
def test_python_scope_and_alias_bypasses_fail_closed(source: str) -> None:
    """Break caught: destructuring and nested scopes retain stale authority."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        _extract(source)


def test_python_ast_utf8_byte_coordinates_become_character_spans() -> None:
    """Break caught: AST byte offsets produce impossible spans after non-ASCII source."""
    source = 'é = 0; policy["schema_version"] == 6\n'
    item = _extract(source)[0]
    assert (item.span.start_line, item.span.start_column, item.span.end_line, item.span.end_column) == (1, source.index("6") + 1, 1, source.index("6") + 2)


def test_python_static_f_string_doubled_braces_match_python_value() -> None:
    """Break caught: static f-string escaped braces are not decoded as Python does."""
    source = 'if policy["profile"] == f"{{zero-order}}": pass\n'
    values = {item.value for item in _extract(source)}
    assert "{zero-order}" in values
    assert "{{zero-order}}" not in values


@pytest.mark.parametrize(
    "source",
    (
        'if policy["profile"] == manifest["profile"]: pass\n',
        'if policy["profile"] != manifest["profile"]: pass\n',
        'FIELD = policy[KEY]\nif FIELD == 6: pass\n',
        'FIELD = policy.get(KEY)\nif FIELD == 6: pass\n',
        'FIELD = policy.get("schema_version", dynamic())\nif FIELD == 6: pass\n',
        'FIELD, OTHER = policy["schema_version"], None\nif FIELD == 6: pass\n',
        'FIELD = OTHER = policy["schema_version"]\nif FIELD == 6: pass\n',
        'if (FIELD := policy["schema_version"]) == 6: pass\n',
        'p = policy\nq = p\nif q["schema_version"] == 6: pass\n',
        'p = (policy := replacement)\nif p["schema_version"] == 6: pass\n',
        'for p in (policy,):\n    pass\nif p["schema_version"] == 6: pass\n',
        'def check(p=policy):\n    if p["schema_version"] == 6: pass\n',
    ),
)
def test_python_governed_provenance_never_disappears_through_aliases(source: str) -> None:
    """Break caught: an unsupported alias drops a governed condition from inventory."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        _extract(source)


@pytest.mark.parametrize(
    "source",
    (
        'policy = replacement\nFIELD = policy["schema_version"]\nif FIELD == 6: pass\n',
        'FIELD = policy["schema_version"]\ndel policy\nif FIELD == 6: pass\n',
        'def check():\n    policy = replacement\n    if policy["schema_version"] == 6: pass\n',
        'for policy in values:\n    if policy["schema_version"] == 6: pass\n',
        'with context() as policy:\n    if policy["schema_version"] == 6: pass\n',
        'p = policy\nFIELD = p["schema_version"]\nif FIELD == 6: pass\n',
        'p = policy\nq = p\nFIELD = q.get("schema_version")\nif FIELD == 6: pass\n',
        'def check(*, p=policy):\n    FIELD = p["schema_version"]\n    return FIELD == 6\n',
        'FIELD = policy["schema_version"]\nother = FIELD\nif other == 6: pass\n',
        'FIELD = [p for p in (policy,)][0]\nif FIELD["schema_version"] == 6: pass\n',
        'with manager(policy) as p:\n    FIELD = p["schema_version"]\nif FIELD == 6: pass\n',
    ),
)
def test_python_whole_module_binding_event_matrix_fails_closed(source: str) -> None:
    """Break caught: an unenumerated binding event permits stale governed evidence."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        _extract(source)


@pytest.mark.parametrize(
    "source",
    (
        'lambda p=policy: p["schema_version"] == 6\n',
        'lambda *, p=policy: p["schema_version"] == 6\n',
        'FIELD = policy["schema_version"]\nlambda *FIELD: None\nif FIELD == 6: pass\n',
        'FIELD = policy["schema_version"]\nlambda **FIELD: None\nif FIELD == 6: pass\n',
        'match value:\n    case {"x": _, **policy}:\n        pass\nif policy["schema_version"] == 6: pass\n',
        'FIELD = policy["schema_version"]\nmatch value:\n    case {"x": _, **FIELD}:\n        pass\nif FIELD == 6: pass\n',
        '(policy,)[0]["schema_version"] == 6\n',
        '[policy][0].get("schema_version") == 6\n',
        'identity(policy)["schema_version"] == 6\n',
        'policy["ordinary_metadata"]["schema_version"] == 6\n',
        'p = policy["ordinary_metadata"]\nif p["schema_version"] == 6: pass\n',
    ),
)
def test_python_wrapped_protected_root_and_remaining_binders_fail_closed(source: str) -> None:
    """Break caught: a wrapper or omitted binder erases protected-root provenance."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        _extract(source)


def test_python_crlf_string_continuation_is_lossless() -> None:
    """Break caught: backslash-CRLF continuation is rejected despite valid Python semantics."""
    source = 'if policy["profile"] == "paper-\\\r\ncompatibility": pass\r\n'
    item = next(item for item in _extract(source) if item.family == "profile" and item.value == "paper-compatibility")
    assert (item.span.start_line, item.span.end_line) == (1, 2)


@pytest.mark.parametrize(
    "source",
    (
        'from unknown import *\nif policy["schema_version"] == 6: pass\n',
        'policy["replacement"] = replacement\nif policy["schema_version"] == 6: pass\n',
        'policy.attr = replacement\nif policy["schema_version"] == 6: pass\n',
        'holder.p = policy\nif holder.p["schema_version"] == 6: pass\n',
        'holder["p"] = policy\nif holder["p"]["schema_version"] == 6: pass\n',
        'policy.__getitem__("schema_version") == 6\n',
        'operator.getitem(policy, "schema_version") == 6\n',
        'p = policy\np.__getitem__("schema_version") == 6\n',
    ),
)
def test_python_mutation_and_indirect_access_are_governed_attempts(source: str) -> None:
    """Break caught: mutation and alternate access syntax erase protected provenance."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        _extract(source)


@pytest.mark.parametrize(
    "source",
    (
        'policy["replacement"] += value\nif policy["schema_version"] == 6: pass\n',
        'policy.attr += value\nif policy["schema_version"] == 6: pass\n',
        'del policy["replacement"]\nif policy["schema_version"] == 6: pass\n',
        'del policy.attr\nif policy["schema_version"] == 6: pass\n',
        'for policy.attr in values: pass\nif policy["schema_version"] == 6: pass\n',
        '[value for policy.attr in values]\nif policy["schema_version"] == 6: pass\n',
    ),
)
def test_python_every_member_target_event_invalidates_its_base(source: str) -> None:
    """Break caught: non-Assign member targets leave a protected root authoritative."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        _extract(source)


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
