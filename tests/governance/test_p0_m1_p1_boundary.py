from __future__ import annotations

from pathlib import Path
import re
import shlex

import pytest

from scripts import check_p0_ci_closure as closure


ROOT = Path(__file__).resolve().parents[2]
_TARGET = re.compile(r"[A-Za-z0-9_.-]+\Z")
_ALTERNATE_MAKE = re.compile(
    r"(?:^|[;&|][ \t]*|[ \t])(?:/[A-Za-z0-9_./-]+/)?g?make(?=[ \t])"
)
_ASSIGNMENT = re.compile(
    r"(?P<name>[A-Za-z_.][A-Za-z0-9_.-]*)\s*"
    r"(?:::=|:=|\?=|\+=|=)\s*(?P<value>.*)\Z"
)
_SIMPLE_VARIABLE_REFERENCE = re.compile(
    r"(?<!\$)\$(?!\$)(?:"
    r"\((?P<paren>[A-Za-z_.][A-Za-z0-9_.-]*)\)"
    r"|\{(?P<brace>[A-Za-z_.][A-Za-z0-9_.-]*)\}"
    r"|(?P<short>[A-Za-z_.])"
    r")"
)
_MAKE_EXPANSION_START = re.compile(r"(?<!\$)\$(?!\$)[({]")
_LITERAL_COMMAND = re.compile(
    r"[A-Za-z0-9_./+-]+(?:[ \t]+[A-Za-z0-9_./+, :=@%-]+)*\Z"
)
_MAKE_EXECUTABLE = re.compile(r"(?:g?make)(?:\.exe)?\Z", re.IGNORECASE)
_SHELL_ESCAPED_MAKE_REFERENCE = re.compile(
    r"\$\$(?:MAKE|\(MAKE\)|\{MAKE\})"
)


def _unresolved() -> None:
    raise closure.ClosureError("P0_M1_P1_MAKE_RECURSION_UNRESOLVED")


def _variable_references(value: str) -> set[str]:
    return {
        match.group("paren") or match.group("brace") or match.group("short")
        for match in _SIMPLE_VARIABLE_REFERENCE.finditer(value)
    }


def _has_unsupported_make_expansion(value: str) -> bool:
    simple_starts = {
        match.start() for match in _SIMPLE_VARIABLE_REFERENCE.finditer(value)
    }
    return any(
        match.start() not in simple_starts
        for match in _MAKE_EXPANSION_START.finditer(value)
    )


def _assignment_value_is_ambiguous(value: str) -> bool:
    """Accept only a fixed literal command or one simple variable reference."""
    source = value.strip()
    references = list(_SIMPLE_VARIABLE_REFERENCE.finditer(source))
    if (
        not source
        or "$$" in source
        or _has_unsupported_make_expansion(source)
    ):
        return True
    if references:
        return not (
            len(references) == 1
            and references[0].span() == (0, len(source))
        )
    return "$" in source or _LITERAL_COMMAND.fullmatch(source) is None


def _value_names_make_executable(value: str) -> bool:
    try:
        words = shlex.split(value.strip())
    except ValueError:
        return False
    return any(
        _MAKE_EXECUTABLE.fullmatch(Path(word).name) is not None for word in words
    )


def _unsafe_command_aliases(assignments: dict[str, list[str]]) -> set[str]:
    """Find aliases that are Make-derived or outside the bounded value grammar."""
    dependencies: dict[str, set[str]] = {}
    unsafe = {"MAKE"}
    for name, values in assignments.items():
        dependencies[name] = set().union(
            *(_variable_references(value) for value in values)
        )
        if (
            len(values) != 1
            or any(_assignment_value_is_ambiguous(value) for value in values)
            or any(_value_names_make_executable(value) for value in values)
        ):
            unsafe.add(name)

    changed = True
    while changed:
        changed = False
        for name, referenced_names in dependencies.items():
            if name not in unsafe and referenced_names & unsafe:
                unsafe.add(name)
                changed = True
    return unsafe - {"MAKE"}


def _wrapped_recursive_targets(
    recipe: str,
    graph: dict[str, set[str]],
    root: Path,
) -> set[str]:
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
            directory = (root / words[1]).resolve()
            if directory == root.resolve():
                if any(word not in graph for word in external_words):
                    _unresolved()
                root_targets.update(external_words)
            continue
        if any(_TARGET.fullmatch(word) is None or word not in graph for word in words):
            _unresolved()
        root_targets.update(words)
    return root_targets


def _strict_portable_make_graph(raw: bytes) -> dict[str, set[str]]:
    """Layer fail-closed recursive-Make resolution over the reviewed parser."""
    graph = closure._make_graph(raw)
    recipes: dict[str, list[str]] = {}
    assignments: dict[str, list[str]] = {}
    current_targets: tuple[str, ...] = ()
    seen_targets: set[str] = set()
    lines = raw.decode("utf-8").splitlines()
    for is_recipe, line in closure._make_logical_lines(lines):
        if is_recipe:
            for target in current_targets:
                recipes.setdefault(target, []).append(line.lstrip(" \t"))
            continue
        assignment = _ASSIGNMENT.fullmatch(line.strip())
        if assignment is not None:
            assignments.setdefault(assignment.group("name"), []).append(
                assignment.group("value")
            )
        statement = closure._make_statement(line, seen_targets)
        current_targets = () if statement is None else statement[0]

    unsafe_aliases = _unsafe_command_aliases(assignments)
    reachable: set[str] = set()
    pending = ["ci", "ci-portable"]
    while pending:
        target = pending.pop()
        if target in reachable:
            continue
        reachable.add(target)
        for recipe in recipes.get(target, ()):
            if (
                _variable_references(recipe) & unsafe_aliases
                or _has_unsupported_make_expansion(recipe)
            ):
                _unresolved()
            if (
                _ALTERNATE_MAKE.search(recipe)
                or "${MAKE}" in recipe
                or _SHELL_ESCAPED_MAKE_REFERENCE.search(recipe)
            ):
                _unresolved()
            if "$(MAKE)" not in recipe:
                continue
            called = closure._recursive_make_targets(recipe)
            if not called:
                called = _wrapped_recursive_targets(recipe, graph, ROOT)
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


def test_make_graph_traverses_same_root_dash_c_target() -> None:
    """A `-C .` recursive target must remain a root-graph edge."""
    makefile = b"""\
.PHONY: ci ci-portable ci-host-authority
ci: ci-portable
\t$(MAKE) -C . ci-host-authority
ci-portable:
\t@:
ci-host-authority:
\t@:
"""

    graph = _strict_portable_make_graph(makefile)

    assert closure._reachable(graph, "ci", "ci-host-authority")


def test_make_graph_rejects_bare_make_executable() -> None:
    """A bare Make executable must not bypass recursive-call resolution."""
    makefile = b"""\
.PHONY: ci ci-portable ci-host-authority
ci: ci-portable
\tmake ci-host-authority
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


def test_make_graph_rejects_make_derived_command_alias() -> None:
    """A variable whose value derives from `$(MAKE)` cannot become a command."""
    makefile = b"""\
MAKE_ALIAS := $(MAKE)
.PHONY: ci ci-portable ci-host-authority
ci: ci-portable
\t$(MAKE_ALIAS) ci-host-authority
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


def test_make_graph_rejects_one_character_make_command_alias() -> None:
    """A one-character reference cannot hide a Make-derived command alias."""
    makefile = b"""\
M := $(MAKE)
.PHONY: ci ci-portable ci-host-authority
ci: ci-portable
\t$M ci-host-authority
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


def test_make_graph_rejects_make_function_derived_command_alias() -> None:
    """An unsupported Make function cannot manufacture a command alias."""
    makefile = b"""\
MAKE_ALIAS := $(value MAKE)
.PHONY: ci ci-portable ci-host-authority
ci: ci-portable
\t$(MAKE_ALIAS) ci-host-authority
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


def test_make_graph_rejects_literal_make_command_alias() -> None:
    """A literal Make executable cannot be invoked through a variable alias."""
    makefile = b"""\
MAKE_ALIAS := /usr/bin/make
.PHONY: ci ci-portable ci-host-authority
ci: ci-portable
\t$(MAKE_ALIAS) ci-host-authority
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
