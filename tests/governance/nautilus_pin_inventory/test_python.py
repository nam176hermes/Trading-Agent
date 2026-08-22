"""RED Python syntax and semantic-consumer controls."""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest

from conftest import fixture_root, generate_baseline, run_subject
from scripts.nautilus_pin_inventory.model import (
    DynamicGovernedCheck,
    GovernedRelation,
    Observation,
    SourceSpan,
)
from scripts.nautilus_pin_inventory.registry import DEFAULT_REGISTRY
from scripts.nautilus_pin_inventory.python_extractor import PythonExtractionError, PythonExtractor


def _extract(source: str):
    return PythonExtractor(DEFAULT_REGISTRY).extract("nautilus_consumer.py", source).observations


def test_python_real_runtime_policy_captures_proven_dynamic_guards_and_relation() -> None:
    """Break caught: exact runtime policy comparisons cannot be audited as evidence."""
    root = Path(__file__).resolve().parents[3]
    path = "scripts/materialize_nautilus_runtime_closure.py"
    result = PythonExtractor(DEFAULT_REGISTRY).extract(path, (root / path).read_text(encoding="utf-8"))

    assert any(
        isinstance(guard, DynamicGovernedCheck)
        and (guard.left_root, guard.left_field, guard.operator, guard.right_root, guard.right_field)
        == ("policy", "result_validator_id", "!=", "specification", "result_validator_id")
        for guard in result.dynamic_guards
    )
    assert any(
        isinstance(relation, GovernedRelation)
        and (relation.left_root, relation.left_document_kind, relation.left_field, relation.left_family,
             relation.operator, relation.right_root, relation.right_document_kind, relation.right_field,
             relation.right_family, relation.relation_kind)
        == ("policy", "nautilus_runtime_closure_policy", "source_commit", "selected_source", "!=",
            "policy", "nautilus_runtime_closure_policy", "engine_upstream_commit", "upstream_commit",
            "cross_family_consistency_guard")
        for relation in result.governed_relations
    )


def test_python_real_closure_manifest_captures_conditional_expected_identity_and_relation() -> None:
    """Break caught: conditionally bound expected identity is treated as an ungoverned name."""
    root = Path(__file__).resolve().parents[3]
    path = "services/job_worker/nautilus_closure.py"
    result = PythonExtractor(DEFAULT_REGISTRY).extract(path, (root / path).read_text(encoding="utf-8"))

    assert any(
        (guard.left_root, guard.left_field, guard.operator, guard.right_root, guard.right_field)
        == ("closure_manifest", "result_validator_id", "!=", "expected_identity", "result_validator_id")
        for guard in result.dynamic_guards
    )
    assert any(
        (relation.left_root, relation.left_document_kind, relation.left_field, relation.left_family,
         relation.operator, relation.right_root, relation.right_document_kind, relation.right_field,
         relation.right_family)
        == ("closure_manifest", "nautilus_closure_manifest", "source_commit", "selected_source", "!=",
            "closure_manifest", "nautilus_closure_manifest", "engine_upstream_commit", "upstream_commit")
        for relation in result.governed_relations
    )


def _runtime_policy_source() -> str:
    root = Path(__file__).resolve().parents[3]
    return (root / "scripts/materialize_nautilus_runtime_closure.py").read_text(encoding="utf-8")


def _closure_manifest_source() -> str:
    root = Path(__file__).resolve().parents[3]
    return (root / "services/job_worker/nautilus_closure.py").read_text(encoding="utf-8")


def _with_root_statement(source: str, insertion: str | None, root: str, statement: str) -> str:
    if insertion is None:
        return source + "\n" + statement + "\n"
    return source.replace(insertion, f"{insertion}\n{textwrap.indent(statement, '    ')}", 1)


def _assert_authority_rejects(path: str, source: str) -> None:
    """Keep authority regressions parse-valid: the extractor, not syntax, rejects them."""
    ast.parse(source, filename=path)
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, source)


@pytest.mark.parametrize(
    "replacement",
    (
        "specification = replacement",
        "del specification",
        "specification[\"replacement\"] = replacement",
        "import collections as specification",
        "[specification for specification in values]",
        "lambda specification: specification",
    ),
)
def test_python_specification_requires_its_exact_proved_origin(replacement: str) -> None:
    """Break caught: a spelling-compatible specification binding survives an unproved module event."""
    source = _runtime_policy_source().replace(
        "specification = _PROFILE_SPECS.get(str(profile))",
        "specification = _PROFILE_SPECS.get(str(profile))\n    " + replacement,
        1,
    )

    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract("scripts/materialize_nautilus_runtime_closure.py", source)


def test_python_governed_endpoint_requires_exact_scope_and_path() -> None:
    """Break caught: a matching root binding becomes authority outside its reviewed endpoint."""
    source = _runtime_policy_source().replace("def _validate_policy_bytes(", "def replacement(", 1)

    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract("scripts/materialize_nautilus_runtime_closure.py", source)
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract("scripts/wrong.py", _runtime_policy_source())


@pytest.mark.parametrize(
    ("path", "source", "rogue"),
    (
        (
            "scripts/materialize_nautilus_runtime_closure.py",
            _runtime_policy_source,
            'def rogue(policy):\n    return policy["source_commit"] == policy["engine_upstream_commit"]\n',
        ),
        (
            "services/job_worker/nautilus_closure.py",
            lambda: (Path(__file__).resolve().parents[3] / "services/job_worker/nautilus_closure.py").read_text(encoding="utf-8"),
            'def rogue(closure_manifest):\n    return closure_manifest["source_commit"] == closure_manifest["engine_upstream_commit"]\n',
        ),
    ),
)
def test_python_governed_access_outside_its_exact_endpoint_fails_closed(path: str, source, rogue: str) -> None:
    """Break caught: another approved endpoint suppresses a rogue or nested-sibling comparison."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, source() + "\n" + rogue)


def test_python_one_proved_and_one_unproved_endpoint_fails_closed() -> None:
    """Break caught: an approved endpoint silently drops an unproved governed peer."""
    source = _runtime_policy_source().replace(
        'policy["source_commit"] == policy["engine_upstream_commit"]',
        'policy["source_commit"] == manifest["engine_upstream_commit"]',
        1,
    )
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract("scripts/materialize_nautilus_runtime_closure.py", source)


def test_python_one_sided_governed_access_fails_closed() -> None:
    """Break caught: an approved root compared with an unproved dynamic value is silently ignored."""
    source = _runtime_policy_source().replace(
        'policy["source_commit"] == policy["engine_upstream_commit"]',
        'policy["source_commit"] == candidate',
        1,
    )
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract("scripts/materialize_nautilus_runtime_closure.py", source)


@pytest.mark.parametrize(
    ("path", "source", "root"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "policy"),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "specification"),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "_PROFILE_SPECS"),
        ("services/job_worker/nautilus_closure.py", lambda: (Path(__file__).resolve().parents[3] / "services/job_worker/nautilus_closure.py").read_text(encoding="utf-8"), "closure_manifest"),
        ("services/job_worker/nautilus_closure.py", lambda: (Path(__file__).resolve().parents[3] / "services/job_worker/nautilus_closure.py").read_text(encoding="utf-8"), "expected_identity"),
        ("services/job_worker/nautilus_closure.py", lambda: (Path(__file__).resolve().parents[3] / "services/job_worker/nautilus_closure.py").read_text(encoding="utf-8"), "_PROFILES"),
    ),
)
def test_python_governed_roots_and_origins_reject_whole_module_rebinding(path: str, source, root: str) -> None:
    """Break caught: a root or origin mapping is rebound outside its approved endpoint."""
    text = source() + f"\nif ({root} := replacement):\n    pass\n"
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, text)


@pytest.mark.parametrize(
    ("path", "source", "insertion", "root"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'profile = policy.get("profile")', "policy"),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'specification = _PROFILE_SPECS.get(str(profile))', "specification"),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, None, "_PROFILE_SPECS"),
        ("services/job_worker/nautilus_closure.py", lambda: (Path(__file__).resolve().parents[3] / "services/job_worker/nautilus_closure.py").read_text(encoding="utf-8"), 'schema_version = closure_manifest.get("schema_version")', "closure_manifest"),
        ("services/job_worker/nautilus_closure.py", lambda: (Path(__file__).resolve().parents[3] / "services/job_worker/nautilus_closure.py").read_text(encoding="utf-8"), "expected_identity = _PROFILES[profile]", "expected_identity"),
        ("services/job_worker/nautilus_closure.py", lambda: (Path(__file__).resolve().parents[3] / "services/job_worker/nautilus_closure.py").read_text(encoding="utf-8"), None, "_PROFILES"),
    ),
)
def test_python_governed_roots_and_origins_reject_receiver_update(path: str, source, insertion: str | None, root: str) -> None:
    """Break caught: a root or origin mapping may be mutated while retaining its old provenance."""
    text = source()
    if insertion is None:
        text += f"\n{root}.update({{}})\n"
    else:
        text = text.replace(insertion, f"{insertion}\n    {root}.update({{}})", 1)
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, text)


def test_python_governed_receiver_escape_fails_closed() -> None:
    """Break caught: an unproved call can mutate a proved endpoint root by reference."""
    source = _runtime_policy_source().replace(
        'profile = policy.get("profile")',
        'profile = policy.get("profile")\n    untrusted(policy)',
        1,
    )
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract("scripts/materialize_nautilus_runtime_closure.py", source)


def test_python_endpoint_must_be_a_direct_module_child() -> None:
    """Break caught: a nested same-name, same-line function reuses an approved endpoint fingerprint."""
    source = _runtime_policy_source()
    start = source.index("def _validate_policy_bytes(")
    end = source.index("\ndef _load_policy(", start)
    assert source[:start].endswith("\n\n")
    nested = "def wrapper():\n" + textwrap.indent(source[start:end], "    ")
    source = source[: start - 1] + nested + source[end:]
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract("scripts/materialize_nautilus_runtime_closure.py", source)


@pytest.mark.parametrize(
    ("path", "source", "replacement"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'def rogue(manifest):\n    return manifest["source_commit"] == manifest["engine_upstream_commit"]\n'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'def rogue(policy):\n    return policy["source_commit"] == policy["engine_upstream_commit"]\n'),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'def rogue(policy, manifest):\n    return policy["source_commit"] == manifest["engine_upstream_commit"]\n'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'def rogue(closure_manifest, policy):\n    return closure_manifest["source_commit"] == policy["engine_upstream_commit"]\n'),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'def rogue(manifest):\n    return manifest["source_commit"] == candidate\n'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'def rogue(policy):\n    return policy["engine_upstream_commit"] == candidate\n'),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'def rogue(policy):\n    return policy.get("source_commit") == policy.get("engine_upstream_commit")\n'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'def rogue(closure_manifest):\n    return closure_manifest.get("source_commit") == closure_manifest.get("engine_upstream_commit")\n'),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'def rogue(policy):\n    return identity(policy)["source_commit"] == identity(policy)["engine_upstream_commit"]\n'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'def rogue(closure_manifest):\n    return identity(closure_manifest)["source_commit"] == identity(closure_manifest)["engine_upstream_commit"]\n'),
    ),
)
def test_python_unproved_governed_root_shapes_never_suppress(path: str, source, replacement: str) -> None:
    """Break caught: root spelling alone hides a rogue direct, mixed, one-sided, or get comparison."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, source() + "\n" + replacement)


@pytest.mark.parametrize(
    ("path", "source", "original", "replacement"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'policy["source_commit"] == policy["engine_upstream_commit"]', 'identity(policy["source_commit"]) == identity(policy["engine_upstream_commit"])'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'closure_manifest["source_commit"]\n                == closure_manifest["engine_upstream_commit"]', 'identity(closure_manifest["source_commit"])\n                == identity(closure_manifest["engine_upstream_commit"])'),
    ),
)
def test_python_wrapped_approved_relation_fails_closed(path: str, source, original: str, replacement: str) -> None:
    """Break caught: wrapping the approved relation removes its evidence without rejection."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, source().replace(original, replacement, 1))


@pytest.mark.parametrize(
    ("path", "source", "original", "replacement"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'policy["source_commit"] == policy["engine_upstream_commit"]', 'identity(policy)["source_commit"] == identity(policy)["engine_upstream_commit"]'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'closure_manifest["source_commit"]\n                == closure_manifest["engine_upstream_commit"]', 'identity(closure_manifest)["source_commit"]\n                == identity(closure_manifest)["engine_upstream_commit"]'),
    ),
)
def test_python_call_before_subscript_governed_relation_fails_closed(path: str, source, original: str, replacement: str) -> None:
    """Break caught: a call may not conceal a governed receiver before its field access."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, source().replace(original, replacement, 1))


@pytest.mark.parametrize(
    ("path", "source", "insertion", "root"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'profile = policy.get("profile")', "policy"),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'specification = _PROFILE_SPECS.get(str(profile))', "specification"),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, None, "_PROFILE_SPECS"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'schema_version = closure_manifest.get("schema_version")', "closure_manifest"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, "expected_identity = _PROFILES[profile]", "expected_identity"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, None, "_PROFILES"),
    ),
)
@pytest.mark.parametrize("escape", ("untrusted((ROOT,))", "untrusted(*[ROOT])", "untrusted([ROOT])", 'untrusted({"root": ROOT})'))
def test_python_governed_container_escapes_fail_closed(path: str, source, insertion: str | None, root: str, escape: str) -> None:
    """Break caught: a tuple, starred list, list, or dict launders a mutable governed receiver."""
    text = _with_root_statement(source(), insertion, root, escape.replace("ROOT", root))
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, text)


@pytest.mark.parametrize(
    ("path", "source", "insertion", "root"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'profile = policy.get("profile")', "policy"),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'specification = _PROFILE_SPECS.get(str(profile))', "specification"),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, None, "_PROFILE_SPECS"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'schema_version = closure_manifest.get("schema_version")', "closure_manifest"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, "expected_identity = _PROFILES[profile]", "expected_identity"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, None, "_PROFILES"),
    ),
)
def test_python_governed_mutator_aliases_fail_closed(path: str, source, insertion: str | None, root: str) -> None:
    """Break caught: binding a mutator first bypasses direct receiver checks."""
    text = _with_root_statement(source(), insertion, root, f"mutate = {root}.update\n    mutate({{}})")
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, text)


def test_python_origin_mapping_nested_receiver_mutation_fails_closed() -> None:
    """Break caught: mutating a value reached from the proved origin map preserves stale provenance."""
    source = _runtime_policy_source() + '\n_PROFILE_SPECS.get("zero-order").update({})\n'
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract("scripts/materialize_nautilus_runtime_closure.py", source)


@pytest.mark.parametrize("replacement", ("set = untrusted", "_closure_digest = untrusted"))
def test_python_reviewed_safe_callee_rebinding_fails_closed(replacement: str) -> None:
    """Break caught: a reviewed receiver call cannot survive rebinding its callee."""
    source = _closure_manifest_source().replace(
        'schema_version = closure_manifest.get("schema_version")',
        f'schema_version = closure_manifest.get("schema_version")\n    {replacement}',
        1,
    )
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract("services/job_worker/nautilus_closure.py", source)


@pytest.mark.parametrize(
    ("path", "source", "changed"),
    (
        (
            "scripts/materialize_nautilus_runtime_closure.py",
            _runtime_policy_source,
            lambda text: text.replace(
                "source_reader: Callable[[Path, str], bytes],",
                "source_reader: Callable[[Path, str], bytes], _PROFILE_SPECS=untrusted,",
                1,
            ),
        ),
        (
            "services/job_worker/nautilus_closure.py",
            _closure_manifest_source,
            lambda text: text.replace("expected_profile: str,", "expected_profile: str, set=untrusted,", 1),
        ),
        (
            "services/job_worker/nautilus_closure.py",
            _closure_manifest_source,
            lambda text: text.replace("expected_profile: str,", "expected_profile: str, _blocked=untrusted,", 1),
        ),
        (
            "scripts/materialize_nautilus_runtime_closure.py",
            _runtime_policy_source,
            lambda text: text.replace(
                "specification = _PROFILE_SPECS.get(str(profile))",
                'specification = _PROFILE_SPECS.get(str(profile))\n    profile_spec = _PROFILE_SPECS["zero-order"]\n    profile_spec.update({})',
                1,
            ),
        ),
        (
            "services/job_worker/nautilus_closure.py",
            _closure_manifest_source,
            lambda text: text.replace("schema_version == 1 and set(closure_manifest)", "schema_version == 1 or  set(closure_manifest)", 1),
        ),
    ),
)
def test_python_structural_endpoint_authority_rejects_fix3_neighbors(path: str, source, changed) -> None:
    """Break caught: a local authority rewrite retains a stale governed endpoint."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, changed(source()))


@pytest.mark.parametrize(
    ("path", "source", "insertion", "root", "escape"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, 'profile = policy.get("profile")', "policy", 'box = {"root": ROOT}\n    untrusted(**box)'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'schema_version = closure_manifest.get("schema_version")', "closure_manifest", 'box = {"root": ROOT}\n    untrusted(**box)'),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, None, "_PROFILE_SPECS", 'box = {"root": ROOT}\n    untrusted(**box)'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, None, "_PROFILES", 'box = {"root": ROOT}\n    untrusted(**box)'),
    ),
)
def test_python_governed_container_alias_escapes_fail_closed(path: str, source, insertion: str | None, root: str, escape: str) -> None:
    """Break caught: a container alias cannot launder a governed receiver across statements."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, _with_root_statement(source(), insertion, root, escape.replace("ROOT", root)))


@pytest.mark.parametrize(
    ("path", "source", "insertion", "root", "escape"),
    (
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'schema_version = closure_manifest.get("schema_version")', "closure_manifest", "untrusted(ROOT if ready else {})"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'schema_version = closure_manifest.get("schema_version")', "closure_manifest", "untrusted(lambda: ROOT)"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'schema_version = closure_manifest.get("schema_version")', "closure_manifest", "untrusted(*(ROOT for _ in (0,)))"),
    ),
)
def test_python_recursive_receiver_escape_forms_fail_closed(path: str, source, insertion: str, root: str, escape: str) -> None:
    """Break caught: arbitrary nested AST children cannot conceal a governed receiver."""
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, _with_root_statement(source(), insertion, root, escape.replace("ROOT", root)))


@pytest.mark.parametrize(
    ("path", "source", "statement"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, '_PROFILE_SPECS["zero-order"].update({})'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, '_PROFILES["zero-order"].update({})'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'closure_manifest.get("argv_prefix", ()).append("evil")'),
    ),
)
def test_python_nested_governed_result_mutation_fails_closed(path: str, source, statement: str) -> None:
    """Break caught: a governed subscript or safe-call result cannot become a mutator receiver."""
    text = _with_root_statement(source(), 'schema_version = closure_manifest.get("schema_version")' if "closure_manifest" in statement else None, "closure_manifest" if "closure_manifest" in statement else "_PROFILE_SPECS", statement)
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract(path, text)


def test_python_reviewed_comparison_must_remain_in_its_enforcing_predicate() -> None:
    """Break caught: a reviewed comparison moved to an unused assignment ceases to enforce validation."""
    source = _closure_manifest_source()
    original = 'closure_manifest["engine_version"] != _EXPECTED_ENGINE_VERSION'
    source = source.replace(original, "False", 1).replace(
        'schema_version = closure_manifest.get("schema_version")',
        f'schema_version = closure_manifest.get("schema_version")\n    ignored = {original}',
        1,
    )
    with pytest.raises(PythonExtractionError, match="invalid governed Python expression"):
        PythonExtractor(DEFAULT_REGISTRY).extract("services/job_worker/nautilus_closure.py", source)


def test_python_generic_path_still_extracts_an_ordinary_candidate_literal() -> None:
    """Break caught: exact admission unnecessarily closes generic-path literal extraction."""
    result = PythonExtractor(DEFAULT_REGISTRY).extract(
        "notes/ordinary.py",
        'ORDINARY_NOTE = "1.231.0"\n',
    )

    assert any(
        observation.family == "engine_version"
        and observation.value == "1.231.0"
        for observation in result.observations
    )


@pytest.mark.parametrize(
    ("path", "source", "statement"),
    (
        (
            "services/job_worker/nautilus_closure.py",
            _closure_manifest_source,
            '__builtins__.update({"set": untrusted})',
        ),
        (
            "services/job_worker/nautilus_closure.py",
            _closure_manifest_source,
            '_blocked.__globals__["_PROFILES"] = untrusted',
        ),
        (
            "services/job_worker/nautilus_closure.py",
            _closure_manifest_source,
            'exec("set = untrusted")',
        ),
        (
            "scripts/materialize_nautilus_runtime_closure.py",
            _runtime_policy_source,
            'getattr(__import__(__name__), "_validate_base_runtime_bytes")('
            'b"{}", untrusted, file_reader=untrusted)',
        ),
        (
            "services/job_worker/nautilus_closure.py",
            _closure_manifest_source,
            'from builtins import __dict__ as builtin_map\n'
            'builtin_map["set"] = untrusted',
        ),
        (
            "scripts/materialize_nautilus_runtime_closure.py",
            _runtime_policy_source,
            'from sys import modules\n'
            'modules[__name__].__dict__["_PROFILE_SPECS"].clear()',
        ),
        (
            "scripts/materialize_nautilus_runtime_closure.py",
            _runtime_policy_source,
            'from sys import modules\n'
            'modules[__name__].__dict__["_validate_policy_bytes"] = object',
        ),
    ),
)
def test_python_exact_governed_module_rejects_any_reflective_source_drift(
    path: str,
    source,
    statement: str,
) -> None:
    """Break caught: a source-level reflective mutation bypasses partial authority checks."""
    _assert_authority_rejects(path, source() + "\n" + statement + "\n")


@pytest.mark.parametrize(
    ("path", "source"),
    (
        (
            "scripts/materialize_nautilus_runtime_closure.py",
            _runtime_policy_source,
        ),
        (
            "services/job_worker/nautilus_closure.py",
            _closure_manifest_source,
        ),
    ),
)
def test_python_exact_governed_module_rejects_ordinary_source_addition(
    path: str,
    source,
) -> None:
    """Break caught: unrelated module additions silently retain stale authority."""
    _assert_authority_rejects(path, source() + '\nORDINARY_NOTE = "unchanged"\n')


def test_python_relation_fingerprints_bind_document_kind_and_raw_operator() -> None:
    """Break caught: relation identity loses endpoint kind or normalizes raw equality to semantic inequality."""
    extractor = PythonExtractor(DEFAULT_REGISTRY)
    source = _runtime_policy_source()
    baseline = extractor.extract("scripts/materialize_nautilus_runtime_closure.py", source).governed_relations
    raw_inequality = extractor.extract(
        "scripts/materialize_nautilus_runtime_closure.py",
        source.replace('policy["source_commit"] == policy["engine_upstream_commit"]', 'policy["source_commit"] != policy["engine_upstream_commit"]', 1),
    ).governed_relations
    baseline_kind = extractor._binding_fingerprint(
        "scripts/materialize_nautilus_runtime_closure.py",
        "_validate_policy_bytes@422",
        (("policy", "nautilus_runtime_closure_policy", "runtime_policy_json_object", "Name(id='policy')"),),
    )
    changed_kind = extractor._binding_fingerprint(
        "scripts/materialize_nautilus_runtime_closure.py",
        "_validate_policy_bytes@422",
        (("policy", "nautilus_engine_build_policy", "runtime_policy_json_object", "Name(id='policy')"),),
    )

    assert baseline[0].syntax_fingerprint != raw_inequality[0].syntax_fingerprint
    assert baseline_kind != changed_kind


def test_python_governed_module_fingerprints_match_reviewed_fix6_sources() -> None:
    """Break caught: a governed source changes without a reviewed whole-module fingerprint."""
    root = Path(__file__).resolve().parents[3]
    extractor = PythonExtractor(DEFAULT_REGISTRY)
    runtime_path = "scripts/materialize_nautilus_runtime_closure.py"
    closure_path = "services/job_worker/nautilus_closure.py"

    runtime = extractor.extract(
        runtime_path,
        (root / runtime_path).read_text(encoding="utf-8"),
    )
    closure = extractor.extract(
        closure_path,
        (root / closure_path).read_text(encoding="utf-8"),
    )

    assert (
        len(runtime.observations),
        len(runtime.dynamic_guards),
        len(runtime.governed_relations),
    ) == (15, 4, 1)
    assert (
        len(closure.observations),
        len(closure.dynamic_guards),
        len(closure.governed_relations),
    ) == (19, 1, 1)


def test_python_governed_module_rejects_neighbor_field_drift() -> None:
    """Break caught: a governed field changes while preserving comparison shape."""
    path = "scripts/materialize_nautilus_runtime_closure.py"
    source = _runtime_policy_source().replace(
        'policy["engine_version"]',
        'policy["semantic_profile"]',
        1,
    )
    _assert_authority_rejects(path, source)


@pytest.mark.parametrize("field", ("engine_name", "python_identity"))
def test_python_governed_module_rejects_unemitted_identity_operator_drift(
    field: str,
) -> None:
    """Break caught: an unsupported identity check inverts under normalization."""
    path = "scripts/materialize_nautilus_runtime_closure.py"
    source = _runtime_policy_source().replace(
        f'manifest["{field}"] != policy["{field}"]',
        f'manifest["{field}"] == policy["{field}"]',
        1,
    )
    _assert_authority_rejects(path, source)


@pytest.mark.parametrize(
    ("path", "source", "rogue"),
    (
        (
            "scripts/materialize_nautilus_runtime_closure.py",
            _runtime_policy_source,
            'def rogue(specification):\n    return specification["result_validator_id"] == specification["result_validator_id"]\n',
        ),
        (
            "services/job_worker/nautilus_closure.py",
            _closure_manifest_source,
            'def rogue(expected_identity):\n    return expected_identity["result_validator_id"] == expected_identity["result_validator_id"]\n',
        ),
    ),
)
def test_python_conditional_governed_root_outside_proved_endpoint_fails_closed(path: str, source, rogue: str) -> None:
    """Break caught: a conditional root spelling outside its proved origin silently vanishes."""
    _assert_authority_rejects(path, source() + "\n" + rogue)


@pytest.mark.parametrize(
    ("path", "source", "statement"),
    (
        (
            "scripts/materialize_nautilus_runtime_closure.py",
            _runtime_policy_source,
            "def _validate_policy_bytes(*args, **kwargs):\n    return {}",
        ),
        (
            "services/job_worker/nautilus_closure.py",
            _closure_manifest_source,
            "attest_nautilus_backtest_closure = untrusted",
        ),
    ),
)
def test_python_later_endpoint_binding_fails_closed(path: str, source, statement: str) -> None:
    """Break caught: a later endpoint definition or assignment replaces reviewed behavior."""
    _assert_authority_rejects(path, source() + "\n" + statement + "\n")


@pytest.mark.parametrize(
    ("path", "source", "statement"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "match untrusted:\n    case _PROFILE_SPECS:\n        pass"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, "match untrusted:\n    case _blocked:\n        pass"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, "from untrusted import *"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'globals()["_blocked"] = untrusted'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'del globals()["_blocked"]'),
    ),
)
def test_python_indirect_module_binding_of_protected_authority_fails_closed(path: str, source, statement: str) -> None:
    """Break caught: indirect module binders replace a proved helper or origin map."""
    _assert_authority_rejects(path, source() + "\n" + statement + "\n")


@pytest.mark.parametrize(
    ("path", "source", "statement"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "import builtins\nbuiltins.set = untrusted"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, '__builtins__["set"] = untrusted'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'setattr(__import__("builtins"), "set", untrusted)'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, 'delattr(__import__("builtins"), "set")'),
    ),
)
def test_python_builtin_set_mutation_fails_closed(path: str, source, statement: str) -> None:
    """Break caught: a reviewed builtin call survives monkeypatching its module authority."""
    _assert_authority_rejects(path, source() + "\n" + statement + "\n")


@pytest.mark.parametrize(
    ("path", "source", "root", "statement"),
    (
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "_PROFILE_SPECS", '_PROFILE_SPECS["zero-order"] = untrusted'),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "_PROFILE_SPECS", 'del _PROFILE_SPECS["zero-order"]'),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "_PROFILE_SPECS", '_PROFILE_SPECS.setdefault("forged", {})'),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "_PROFILE_SPECS", "alias = _PROFILE_SPECS\nalias.clear()"),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "_PROFILE_SPECS", 'alias = _PROFILE_SPECS["zero-order"]\nalias.update({})'),
        ("scripts/materialize_nautilus_runtime_closure.py", _runtime_policy_source, "_PROFILE_SPECS", "mutate = _PROFILE_SPECS.update\nmutate({})"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, "_PROFILES", '_PROFILES["zero-order"] = untrusted'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, "_PROFILES", 'del _PROFILES["zero-order"]'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, "_PROFILES", '_PROFILES.setdefault("forged", {})'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, "_PROFILES", "alias = _PROFILES\nalias.clear()"),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, "_PROFILES", 'alias = _PROFILES["zero-order"]\nalias.update({})'),
        ("services/job_worker/nautilus_closure.py", _closure_manifest_source, "_PROFILES", "mutate = _PROFILES.update\nmutate({})"),
    ),
)
def test_python_independent_origin_map_transition_fails_closed(path: str, source, root: str, statement: str) -> None:
    """Break caught: an origin-map mutation through direct or indirect forms keeps stale provenance."""
    _assert_authority_rejects(path, source() + "\n" + statement + "\n")


def test_python_base_runtime_same_family_guards_are_emitted_with_raw_operator_fingerprints() -> None:
    """Break caught: reviewed base-runtime comparisons are structurally admitted but not inventoried."""
    path = "scripts/materialize_nautilus_runtime_closure.py"
    source = _runtime_policy_source()
    result = PythonExtractor(DEFAULT_REGISTRY).extract(path, source)
    pairs = {
        (guard.left_root, guard.left_field, guard.operator, guard.right_root, guard.right_field): guard.syntax_fingerprint
        for guard in result.dynamic_guards
    }
    assert ("manifest", "engine_version", "!=", "policy", "engine_version") in pairs
    assert ("manifest", "source_commit", "!=", "policy", "engine_upstream_commit") in pairs
    changed = PythonExtractor(DEFAULT_REGISTRY).extract(
        path,
        source.replace('manifest["engine_version"] != policy["engine_version"]', 'manifest["engine_version"] == policy["engine_version"]', 1),
    )
    changed_pairs = {
        (guard.left_root, guard.left_field, guard.operator, guard.right_root, guard.right_field): guard.syntax_fingerprint
        for guard in changed.dynamic_guards
    }
    assert pairs[("manifest", "engine_version", "!=", "policy", "engine_version")] != changed_pairs[("manifest", "engine_version", "==", "policy", "engine_version")]


def test_python_base_runtime_policy_parameter_requires_the_exact_caller_chain() -> None:
    """Break caught: a signature-compatible base check accepts policy flow after its unique caller changes."""
    path = "scripts/materialize_nautilus_runtime_closure.py"
    source = _runtime_policy_source().replace("return manifest, files", "return {}, files", 1)
    _assert_authority_rejects(path, source)


@pytest.mark.parametrize(
    "statement",
    (
        "def rogue():\n    return _validate_base_runtime_bytes(untrusted, {}, file_reader=untrusted)",
        "base_alias = _validate_base_runtime_bytes",
    ),
)
def test_python_base_runtime_policy_chain_rejects_extra_call_or_alias(statement: str) -> None:
    """Break caught: an extra base-runtime callee use forges signature-only policy provenance."""
    path = "scripts/materialize_nautilus_runtime_closure.py"
    _assert_authority_rejects(path, _runtime_policy_source() + "\n" + statement + "\n")


@pytest.mark.parametrize("invalid_kind", ("", "unknown", 1, None))
def test_governed_relation_rejects_unknown_document_kind_and_sorts_by_kind(invalid_kind: object) -> None:
    """Break caught: a relation accepts an invented kind or sorting ignores an identity component."""
    span = SourceSpan.content("policy.py", 1, 1, 1, 2)
    arguments = dict(
        path="policy.py",
        left_root="policy",
        left_document_kind="nautilus_runtime_closure_policy",
        left_field="source_commit",
        left_family="selected_source",
        operator="!=",
        right_root="policy",
        right_document_kind="nautilus_runtime_closure_policy",
        right_field="engine_upstream_commit",
        right_family="upstream_commit",
        relation_kind="cross_family_consistency_guard",
        binding_fingerprint="binding",
        syntax_fingerprint="syntax",
        span=span,
    )
    with pytest.raises(ValueError, match="document kind"):
        GovernedRelation(**{**arguments, "left_document_kind": invalid_kind})

    left = GovernedRelation(**arguments)
    right = GovernedRelation(**{**arguments, "left_document_kind": "nautilus_engine_build_policy"})
    assert [item.left_document_kind for item in sorted((left, right), key=PythonExtractor._relation_sort_key)] == [
        "nautilus_engine_build_policy",
        "nautilus_runtime_closure_policy",
    ]


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


def test_python_ordinary_static_policy_field_is_ignored_on_generic_path() -> None:
    """Break caught: generic ordinary comparisons become pin observations."""
    result = PythonExtractor(DEFAULT_REGISTRY).extract(
        "scripts/prepare_nautilus_llvm_toolchain.py",
        'if policy["ordinary_metadata"] != source: pass\n',
    )
    assert result.observations == ()


@pytest.mark.parametrize(
    "path",
    (
        "scripts/materialize_nautilus_runtime_closure.py",
        "services/job_worker/nautilus_closure.py",
    ),
)
def test_python_governed_module_path_requires_exact_reviewed_module(
    path: str,
) -> None:
    """Break caught: governed paths permit incomplete non-reviewed modules."""
    _assert_authority_rejects(
        path,
        'if policy["ordinary_metadata"] != source: pass\n',
    )


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
