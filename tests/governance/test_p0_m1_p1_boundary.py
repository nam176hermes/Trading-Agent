from __future__ import annotations

from pathlib import Path
import re
import shlex

import pytest

from scripts import check_p0_ci_closure as closure


ROOT = Path(__file__).resolve().parents[2]
_TARGET = re.compile(r"[A-Za-z0-9_.-]+\Z")


def _unresolved() -> None:
    raise closure.ClosureError("P0_M1_P1_MAKE_RECURSION_UNRESOLVED")


def _wrapped_recursive_targets(recipe: str, graph: dict[str, set[str]]) -> set[str]:
    """Resolve every Make call in a shell wrapper or fail closed."""
    matches = re.findall(r"\$\(MAKE\)[ \t]+([^;]+)", recipe)
    if len(matches) != recipe.count("$(MAKE)"):
        _unresolved()

    root_targets: set[str] = set()
    for arguments in matches:
        try:
            words = shlex.split(arguments.strip())
        except ValueError:
            _unresolved()
        if not words:
            _unresolved()
        if words[0] == "-C":
            if (
                len(words) < 3
                or not re.fullmatch(r"[A-Za-z0-9_./-]+", words[1])
                or words[1].startswith("/")
                or ".." in Path(words[1]).parts
            ):
                _unresolved()
            external_words = words[2:]
            while external_words and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*=[^$()]*(?:\$\$[A-Za-z_][A-Za-z0-9_]*)?",
                external_words[0],
            ):
                external_words = external_words[1:]
            if not external_words or any(
                _TARGET.fullmatch(word) is None for word in external_words
            ):
                _unresolved()
            continue
        if any(_TARGET.fullmatch(word) is None or word not in graph for word in words):
            _unresolved()
        root_targets.update(words)
    return root_targets


def _strict_portable_make_graph(raw: bytes) -> dict[str, set[str]]:
    """Layer fail-closed recursive-Make resolution over the reviewed parser."""
    graph = closure._make_graph(raw)
    recipes: dict[str, list[str]] = {}
    current_targets: tuple[str, ...] = ()
    seen_targets: set[str] = set()
    lines = raw.decode("utf-8").splitlines()
    for is_recipe, line in closure._make_logical_lines(lines):
        if is_recipe:
            for target in current_targets:
                recipes.setdefault(target, []).append(line.lstrip(" \t"))
            continue
        statement = closure._make_statement(line, seen_targets)
        current_targets = () if statement is None else statement[0]

    reachable: set[str] = set()
    pending = ["ci", "ci-portable"]
    while pending:
        target = pending.pop()
        if target in reachable:
            continue
        reachable.add(target)
        for recipe in recipes.get(target, ()):
            if "$(MAKE)" not in recipe:
                continue
            called = closure._recursive_make_targets(recipe)
            if not called:
                called = _wrapped_recursive_targets(recipe, graph)
            graph[target].update(called)
        pending.extend(graph.get(target, ()))
    return graph


def test_make_graph_rejects_variable_indirected_recursive_target() -> None:
    """A variable-indirected recursive target must not disappear from the graph."""
    makefile = b"""\
HOST_TARGET := ci-host-authority
.PHONY: ci ci-portable ci-host-authority
ci: ci-portable
\t$(MAKE) $(HOST_TARGET)
ci-portable:
\t@:
ci-host-authority:
\t@:
"""

    with pytest.raises(
        closure.ClosureError,
        match="^P0_M1_P1_MAKE_RECURSION_UNRESOLVED$",
    ):
        _strict_portable_make_graph(makefile)


def test_portable_make_graph_is_literal_and_cannot_reach_host_authority() -> None:
    """The checked-in portable route must be fully resolved without host authority."""
    graph = _strict_portable_make_graph((ROOT / "Makefile").read_bytes())

    assert graph["ci"] == {"ci-portable"}
    assert not closure._reachable(graph, "ci", "ci-host-authority")
    assert not closure._reachable(graph, "ci-portable", "ci-host-authority")
