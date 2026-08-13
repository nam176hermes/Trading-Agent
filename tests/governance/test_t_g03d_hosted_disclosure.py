from __future__ import annotations

from collections import Counter
import re
import json
import shlex
import tempfile
import subprocess
import os
import hashlib
from pathlib import Path

import pytest

from scripts import t_g03_capability_topology as topology
import scripts.check_test_governance as test_governance
from scripts.check_test_governance import GovernanceError, audit_topology_root_records


ROOT = Path(__file__).resolve().parents[2]


def _make_targets(source: str) -> dict[str, tuple[str, ...]]:
    return {
        match.group(1): tuple(match.group(2).split())
        for match in re.finditer(
            r"^([A-Za-z0-9_.-]+)[ \t]*:([^=\n]*)$", source, re.MULTILINE
        )
    }


def _reachable(targets: dict[str, tuple[str, ...]], root: str) -> set[str]:
    found: set[str] = set()
    pending = [root]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        pending.extend(targets.get(current, ()))
    return found


def _logical_make_recipe_argvs(source: str) -> list[tuple[str, tuple[str, ...]]]:
    """Extract bounded shell command positions from Make recipes.

    This is deliberately not a general shell parser.  It recognizes the
    control positions used by Make recipes and leaves any unrecognised command
    form as an argv headed by that form, so the guarded-launch contract can
    reject an ambiguous use of a protected entrypoint instead of overlooking it.
    """
    target: str | None = None
    pending_target: str | None = None
    pending: list[str] = []
    commands: list[tuple[str, tuple[str, ...]]] = []
    target_pattern = re.compile(r"^([^\s:=#][^:=#]*?)[ \t]*:(?![=])(.*)$")
    assignment = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
    separators = frozenset({";", ";;", "&&", "||", "|", "&"})
    command_prefixes = frozenset({
        "then", "do", "else", "elif", "!", "{", "(", "if", "while", "until",
    })
    env_options_with_value = frozenset({
        "-C", "-S", "-u", "--chdir", "--split-string", "--unset",
    })

    def continues_shell_line(value: str) -> bool:
        return (len(value) - len(value.rstrip("\\"))) % 2 == 1

    def record(command_target: str, fragments: list[str]) -> None:
        lexer = shlex.shlex(" ".join(fragments), posix=True, punctuation_chars=";|&")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = tuple(lexer)

        def record_shell_command(words: tuple[str, ...]) -> None:
            start = 0
            while start < len(words):
                if words[start] in command_prefixes or assignment.fullmatch(words[start]):
                    start += 1
                    continue
                if words[start] == "case":
                    try:
                        arm_end = next(
                            index
                            for index in range(start + 1, len(words))
                            if words[index] == ")" or words[index].endswith(")")
                        )
                        start = arm_end + 1
                    except StopIteration:
                        commands.append((command_target, words))
                        return
                    continue
                if words[start] == ")":
                    start += 1
                    continue
                if words[start] == "command":
                    start += 1
                    if start < len(words) and words[start] == "--":
                        start += 1
                    while start < len(words) and words[start].startswith("-"):
                        start += 1
                    continue
                if words[start] != "env":
                    break
                start += 1
                while start < len(words):
                    token = words[start]
                    if token == "--":
                        start += 1
                        break
                    if assignment.fullmatch(token) or token.startswith("-"):
                        start += 1
                        if token in env_options_with_value and start < len(words):
                            start += 1
                        continue
                    break
            if start < len(words):
                commands.append((command_target, words[start:]))

        statement: list[str] = []
        for token in (*tokens, ";"):
            if token in separators:
                if statement:
                    record_shell_command(tuple(statement))
                    statement = []
            else:
                statement.append(token)

    def append_recipe_fragment(command_target: str, fragment: str) -> None:
        nonlocal pending_target, pending
        normalized = fragment.lstrip().rstrip()
        if not pending:
            normalized = re.sub(r"^[@+\-]+", "", normalized)
        continues = continues_shell_line(normalized)
        pending_target = command_target if pending_target is None else pending_target
        assert pending_target == command_target
        pending.append(normalized[:-1].rstrip() if continues else normalized)
        if not continues:
            record(command_target, pending)
            pending = []
            pending_target = None

    for line in source.splitlines():
        match = target_pattern.match(line)
        if match is not None:
            assert not pending
            target = match.group(1).strip()
            remainder = match.group(2)
            if ";" in remainder:
                _, _, inline_recipe = remainder.partition(";")
                append_recipe_fragment(target, inline_recipe)
            elif continues_shell_line(line.rstrip()):
                target = None
            continue
        if not line.startswith("\t"):
            assert not pending
            target = None
            continue
        if target is None:
            continue
        append_recipe_fragment(target, line[1:])
    assert not pending
    return commands


def _make_module_commands(source: str, module: str) -> list[tuple[str, tuple[str, ...]]]:
    """Return every Make logical command that launches one canonical Python module."""
    prefix = ("uv", "run", "python", "-m", module)
    return [
        (target, argv[len(prefix):])
        for target, argv in _logical_make_recipe_argvs(source)
        if argv[: len(prefix)] == prefix
    ]


def _make_direct_file_commands(source: str, script: str) -> list[tuple[str, tuple[str, ...]]]:
    """Return every Make logical command that directly executes one guarded script file."""
    prefix = ("uv", "run", "python")
    options_with_value = frozenset({"-W", "-X", "--check-hash-based-pycs"})

    def executes_script(argv: tuple[str, ...]) -> bool:
        if argv[:2] != prefix[:2]:
            return False
        index = 2
        if index < len(argv) and argv[index] == "--":
            index += 1
        if index >= len(argv) or argv[index] != "python":
            return False
        index += 1
        while index < len(argv):
            token = argv[index]
            if token in {"-m", "-c"}:
                return False
            if token == "--":
                index += 1
                break
            if token.startswith("-"):
                index += 1
                if token in options_with_value and index < len(argv):
                    index += 1
                continue
            break
        return index < len(argv) and argv[index] == script

    return [
        (target, argv)
        for target, argv in _logical_make_recipe_argvs(source)
        if executes_script(argv)
    ]


def _make_noncanonical_guarded_commands(source: str) -> list[tuple[str, tuple[str, ...]]]:
    """Return guarded entrypoint references that are not exact module heads.

    Display commands remain data.  Every other unrecognised command that
    carries an exact guarded module or file token is intentionally treated as
    an unsafe future Make change: this narrow contract cannot prove such shell
    grammar invokes the required package entrypoint.  Opaque evaluators and
    shell expansion in an executable/module position are likewise unsafe:
    this bounded extractor deliberately does not evaluate them.
    """
    guarded = frozenset({
        "scripts.t_g03_capability_topology",
        "scripts.check_test_governance",
        "scripts/t_g03_capability_topology.py",
        "scripts/check_test_governance.py",
    })
    canonical = frozenset({
        ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology"),
        ("uv", "run", "python", "-m", "scripts.check_test_governance"),
    })
    display_commands = frozenset({"echo", "printf"})
    make_assignment = re.compile(
        r"^([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?:::=|::=|:=|\?=|\+=|=)[ \t]*(.*)$"
    )
    make_variable = re.compile(r"^\$\(([^(){}\s]+)\)$|^\$\{([^(){}\s]+)\}$")
    make_values = {
        match.group(1): match.group(2)
        for line in source.splitlines()
        if (match := make_assignment.match(line)) is not None
    }

    def is_shell_indirection(word: str) -> bool:
        """Recognize shell evaluation syntax without interpreting it.

        Make-level values such as ``$(TEST_EVIDENCE_DIR)`` are deliberately
        not shell indirection.  The doubled dollar forms are emitted to the
        shell by Make and can choose an executable or module at runtime.
        """
        return "$$" in word or "`" in word

    def is_make_indirection(word: str) -> bool:
        """Recognize raw Make expansion without resolving a variable value."""
        return "$(" in word or "${" in word

    def resolved_make_recipe_head(argv: tuple[str, ...]) -> tuple[str, ...] | None:
        """Resolve one bounded ordinary Make recipe-head variable.

        ``$(MAKE)`` is the one approved recursive-Make form.  Any other
        recipe-head variable must be fully resolvable from a simple Make
        assignment; otherwise the launcher contract cannot prove it does not
        materialize a guarded program.  This deliberately does not interpret
        Make functions, modifiers, or recursive assignment graphs.
        """
        if not argv:
            return ()
        match = make_variable.fullmatch(argv[0])
        if match is None:
            return argv
        name = next(value for value in match.groups() if value is not None)
        if name == "MAKE":
            return ()
        value = make_values.get(name)
        if value is None or is_make_indirection(value) or "$$" in value or "`" in value:
            return None
        try:
            return tuple(shlex.split(value, posix=True)) + argv[1:]
        except ValueError:
            return None

    def is_opaque_evaluator(argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        if argv[0] in {"eval", ".", "source", "exec", "xargs"}:
            return True
        if argv[0] in {"sh", "bash", "dash", "ksh", "zsh"} and "-c" in argv[1:]:
            return True
        if "-exec" not in argv:
            return False
        payload = argv[argv.index("-exec") + 1:]
        shell_names = frozenset({"sh", "bash", "dash", "ksh", "zsh"})
        return (
            any(token in guarded for token in payload)
            or any(token in {"uv", "python"} for token in payload)
            or (any(token in shell_names for token in payload) and "-c" in payload)
        )

    def has_make_expanded_guarded_launch(argv: tuple[str, ...]) -> bool:
        """Reject unresolved Make variables in the protected launcher path.

        Normal Make path interpolation remains allowed after an already exact
        module command.  A T-G03-named variable is never provable from this
        source-only parser, even when it is the command word itself.
        """
        if any(is_make_indirection(word) and "T_G03" in word for word in argv):
            return True
        if argv[:2] != ("uv", "run") or len(argv) < 3:
            return False
        if argv[2] != "python":
            return is_make_indirection(argv[2])
        if len(argv) < 4:
            return False
        program_index = 4 if argv[3] == "-m" else 3
        return is_make_indirection(argv[program_index])

    def has_make_recipe_head_guarded_launch(argv: tuple[str, ...]) -> bool:
        """Reject ordinary Make command macros that hide a guarded launcher."""
        if not argv or make_variable.fullmatch(argv[0]) is None:
            return False
        resolved = resolved_make_recipe_head(argv)
        if resolved is None:
            return True
        if not resolved:
            return False
        return (
            is_opaque_evaluator(resolved)
            or has_indirect_guarded_launch(resolved)
            or has_make_expanded_guarded_launch(resolved)
            or any(token in guarded for token in resolved)
        )

    def has_indirect_guarded_launch(argv: tuple[str, ...]) -> bool:
        """Reject dynamic words where the protected launcher selects code."""
        if argv and is_shell_indirection(argv[0]):
            return True
        if argv[:2] != ("uv", "run") or len(argv) < 3:
            return False
        if is_shell_indirection(argv[2]):
            return True
        if argv[2] != "python" or len(argv) < 4:
            return False
        program_index = 4 if argv[3] == "-m" else 3
        return program_index < len(argv) and is_shell_indirection(argv[program_index])

    return [
        (target, argv)
        for target, argv in _logical_make_recipe_argvs(source)
        if argv
        and argv[0] not in display_commands
        and (
            is_opaque_evaluator(argv)
            or has_indirect_guarded_launch(argv)
            or has_make_expanded_guarded_launch(argv)
            or has_make_recipe_head_guarded_launch(argv)
            or (
                any(token in guarded for token in argv)
                and argv[:5] not in canonical
            )
        )
    ]


def _make_opaque_guarded_evaluations(source: str) -> list[str]:
    """Return Make eval forms that hide a guarded entrypoint from recipes."""
    guarded_markers = (
        "scripts.t_g03_capability_topology",
        "scripts.check_test_governance",
        "scripts/t_g03_capability_topology.py",
        "scripts/check_test_governance.py",
        "T_G03",
    )
    return [
        line
        for line in source.splitlines()
        if ("$(eval" in line or "${eval" in line)
        and (
            any(marker in line for marker in guarded_markers)
            or re.search(r"\$[({]eval\s+[^)}]*\$[({]", line) is not None
        )
    ]


def test_hosted_portable_route_uses_only_exact_topology_root_lanes() -> None:
    """Break caught: Foundation reintroduces generic root pytest below ci-portable."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    targets = _make_targets(makefile)

    assert targets["ci-portable-private"] == ()
    assert targets["test-all-portable-topology-private"] == (
        "audit-portable",
        "check-d0-closure",
        "check-contracts",
        "check-secrets",
        "test-backend",
        "test-dashboard",
        "typecheck-dashboard",
        "lint-dashboard",
        "ci-portable-topology",
    )

    portable_private = re.search(
        r"^ci-portable-private:\n((?:\t.*\n)+)", makefile, re.MULTILINE
    )
    assert portable_private is not None
    recipe = portable_private.group(1)
    assert recipe.splitlines() == [
        "\t$(MAKE) prepare-root-test-install",
        "\t$(MAKE) test-all-portable-topology-private check-test-governance-topology check-critical-coverage build-dashboard audit-python-source audit-dependencies",
    ]

    route = _reachable(targets, "test-all-portable-topology-private")
    assert not route & {
        "test",
        "test-portable-embedded-proof",
        "test-all-portable-private",
        "check-test-skips",
    }
    assert "ci-portable-topology" in route
    assert "check-test-governance-topology" in recipe

    topology_target = re.search(
        r"^check-test-governance-topology:\n((?:\t.*\n)+)", makefile, re.MULTILINE
    )
    assert topology_target is not None
    recipe = topology_target.group(1)
    assert "uv run python -m scripts.check_test_governance" in recipe
    assert "--topology-audit" in recipe
    assert '$(TEST_EVIDENCE_DIR)/test-governance-topology' in recipe
    assert '$(TEST_EVIDENCE_DIR)' in recipe
    assert 'tests/fixtures/t-g03a-hosted-failure-inventory.tsv' in recipe
    assert '"$$FOUNDATION_CONTEXT_PATH"' in recipe
    assert "--foundation-run-id" not in recipe
    assert "--foundation-head-sha" not in recipe
    topology_recipe = re.search(
        r"^ci-portable-topology:\n((?:\t.*\n)+)", makefile, re.MULTILINE,
    )
    assert topology_recipe is not None
    sequence = topology_recipe.group(1)
    assert sequence.index("$(MAKE) test-portable-root-remainder") < sequence.index("run-lane --lane portable-source")
    remainder_target = re.search(
        r"^test-portable-root-remainder:\n((?:\t.*\n)+)", makefile, re.MULTILINE,
    )
    assert remainder_target is not None
    remainder_sequence = remainder_target.group(1)
    assert remainder_sequence.index("collect-baseline") < remainder_sequence.index("prepare-remainder")
    assert remainder_sequence.index("prepare-remainder") < remainder_sequence.index("run-remainder")
    portable_source_target = re.search(
        r"^test-portable-source:\n((?:\t.*\n)+)", makefile, re.MULTILINE,
    )
    assert portable_source_target is not None
    portable_source_sequence = portable_source_target.group(1)
    assert "native/package6_custodian" in portable_source_sequence
    assert "PACKAGE6_FD_CUSTODY_EXTENSION_PATH" in portable_source_sequence
    assert "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256" in portable_source_sequence
    assert portable_source_sequence.index("collect-baseline") < portable_source_sequence.index("run-lane --lane portable-source")


class MakeContractError(AssertionError):
    """The hermetic Make observer cannot prove the guarded launcher contract."""


_EXACT_LOCK_SHELL = "RUNTIME_RELEASE_LOCK_SHA256 := $(shell sha256sum uv.lock | cut -d' ' -f1)"
_GUARDED_WORDS = frozenset({
    "scripts.t_g03_capability_topology", "scripts.check_test_governance",
    "scripts/t_g03_capability_topology.py", "scripts/check_test_governance.py",
})
# Each entry binds target, logical recipe span, argv, and command-local span.
_APPROVED_RECURSIVE_MAKE_OCCURRENCES = (
    ("check-test-skips", (83, 100), ("-C", "native/package6_custodian", "BUILD_DIR=$$build_dir", "build"), (170, 177)),
    ("test", (121, 137), ("-C", "native/package6_custodian", "BUILD_DIR=$$build_dir", "build"), (164, 171)),
    ("build-package6-custodian", (187, 187), ("-C", "native/package6_custodian", "build"), (0, 7)),
    ("test-package6-custodian-native", (190, 195), ("-C", "native/package6_custodian", "BUILD_DIR=$$build_dir", "test"), (171, 178)),
    ("test-all", (282, 291), ("prepare-root-test-install",), (385, 392)),
    ("test-all", (282, 291), ("test-all-private",), (484, 491)),
    ("ci", (294, 304), ("ci-private",), (359, 366)),
    ("ci-private", (307, 307), ("prepare-root-test-install",), (0, 7)),
    ("ci-private", (308, 308), ("test-all-private", "check-test-skips", "check-critical-coverage", "build-dashboard", "audit-python-source", "audit-dependencies"), (0, 7)),
    ("ci-portable", (311, 323), ("ci-portable-private",), (681, 688)),
    ("ci-portable-private", (326, 326), ("prepare-root-test-install",), (0, 7)),
    ("ci-portable-private", (327, 327), ("test-all-portable-topology-private", "check-test-governance-topology", "check-critical-coverage", "build-dashboard", "audit-python-source", "audit-dependencies"), (0, 7)),
    ("test-portable-source", (331, 347), ("-C", "native/package6_custodian", "BUILD_DIR=$$build_dir", "build"), (287, 294)),
    ("ci-portable-topology", (367, 389), ("-C", "native/package6_custodian", "BUILD_DIR=$$build_dir", "build"), (289, 296)),
    ("ci-portable-topology", (367, 389), ("test-portable-root-remainder",), (949, 956)),
)


def _make_logical_lines(source: str) -> list[tuple[int, int, str]]:
    """Construct complete GNU Make logical lines using trailing-slash parity."""
    result: list[tuple[int, int, str]] = []
    start, fragments = 1, []
    for number, line in enumerate(source.splitlines(), 1):
        trailing = len(line) - len(line.rstrip("\\"))
        if trailing % 2:
            fragments.extend((line[:-1], " "))
            continue
        fragments.append(line)
        result.append((start, number, "".join(fragments)))
        start, fragments = number + 1, []
    if fragments:
        raise MakeContractError(f"unterminated continuation at line {start}")
    return result


def _conditional_quarantine(source: str) -> None:
    """Fail before projection/process creation for every conditional directive."""
    errors: list[str] = []
    stack: list[bool] = []
    define_depth = 0
    directive = re.compile(r"^[ \t]*(ifeq|ifneq|ifdef|ifndef|else|endif)\b(.*)$")
    assignment = re.compile(r"^[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*(?:::=|::=|:=|\?=|\+=|=)")
    rule = re.compile(r"^[^\t #][^:=#]*:(?![=])")
    for first, last, line in _make_logical_lines(source):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or line.startswith("\t"):
            continue
        if define_depth:
            if re.fullmatch(r"endef(?:\s+#.*)?", stripped):
                define_depth -= 1
            continue
        if re.match(r"define(?:\s|$)", stripped):
            define_depth += 1
            continue
        if assignment.match(line) or rule.match(line):
            continue
        match = directive.match(line)
        if match is None:
            if re.match(r"^[ \t]*(?:if\w*|else|endif)\b", line):
                errors.append(f"ambiguous conditional spelling at {first}:{last}")
            continue
        word, tail = match.groups()
        if first != last:
            errors.append(f"continued conditional directive at {first}:{last}")
        if word in {"ifeq", "ifneq", "ifdef", "ifndef"}:
            if not tail.strip():
                errors.append(f"malformed {word} at {first}:{last}")
            stack.append(False)
        elif word == "else":
            if not stack:
                errors.append(f"unmatched else at {first}:{last}")
            elif stack[-1]:
                errors.append(f"duplicate else at {first}:{last}")
            else:
                stack[-1] = True
            if tail.strip() and re.fullmatch(r"\s*(?:ifeq|ifneq|ifdef|ifndef)\b.+", tail) is None:
                errors.append(f"malformed else at {first}:{last}")
        else:
            if tail.strip():
                errors.append(f"unexpected endif material at {first}:{last}")
            if not stack:
                errors.append(f"unmatched endif at {first}:{last}")
            else:
                stack.pop()
        errors.append(f"conditional directive {word} at {first}:{last}")
    if define_depth:
        errors.append("unterminated define body")
    if stack:
        errors.append("missing endif")
    if errors:
        raise MakeContractError("; ".join(errors))


def _recursive_make_projection_spans(source: str) -> list[tuple[int, int]]:
    """Return only verified literal recursive-Make command-word source spans."""
    target: str | None = None
    pending_target: str | None = None
    pending_text: list[str] = []
    pending_offsets: list[int | None] = []
    pending_span: tuple[int, int] | None = None
    recipes: list[tuple[str, str, list[int | None], tuple[int, int]]] = []
    target_pattern = re.compile(r"^([^\s:=#][^:=#]*?)[ \t]*:(?![=])(.*)$")
    assignment = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")

    def continued(value: str) -> bool:
        return (len(value) - len(value.rstrip("\\"))) % 2 == 1

    def append_recipe(target_name: str, text: str, start: int, line_number: int) -> None:
        nonlocal pending_target, pending_text, pending_offsets, pending_span
        normalized = text.lstrip().rstrip()
        left = len(text) - len(text.lstrip())
        offset = start + left
        continues = continued(normalized)
        content = normalized[:-1].rstrip() if continues else normalized
        pending_target = target_name if pending_target is None else pending_target
        if pending_target != target_name:
            raise MakeContractError("mixed Make recipe continuation")
        if pending_span is None:
            pending_span = (line_number, line_number)
        else:
            pending_span = (pending_span[0], line_number)
        pending_text.append(content)
        pending_offsets.extend(range(offset, offset + len(content)))
        if continues:
            pending_text.append(" ")
            pending_offsets.append(None)
            return
        assert pending_span is not None
        recipes.append((target_name, "".join(pending_text), pending_offsets, pending_span))
        pending_target, pending_text, pending_offsets, pending_span = None, [], [], None

    offset = 0
    for line_number, raw in enumerate(source.splitlines(keepends=True), 1):
        line = raw.rstrip("\r\n")
        match = target_pattern.match(line)
        if match is not None:
            if pending_text:
                raise MakeContractError("unterminated Make recipe continuation")
            target = match.group(1).strip()
            remainder = match.group(2)
            if ";" in remainder:
                prefix, _separator, inline = remainder.partition(";")
                append_recipe(target, inline, offset + match.start(2) + len(prefix) + 1, line_number)
            elif continued(line.rstrip()):
                target = None
        elif line.startswith("\t") and target is not None:
            append_recipe(target, line[1:], offset + 1, line_number)
        elif pending_text:
            raise MakeContractError("unterminated Make recipe continuation")
        else:
            target = None
        offset += len(raw)
    if pending_text:
        raise MakeContractError("unterminated Make recipe continuation")

    def tokens(text: str) -> list[tuple[str, int, int, bool]]:
        """Lex the bounded command words needed to verify a root command word."""
        result: list[tuple[str, int, int, bool]] = []
        cursor = 0
        punctuation = ";|&"
        while cursor < len(text):
            if text[cursor].isspace():
                cursor += 1
                continue
            if text[cursor] in punctuation:
                start = cursor
                marker = text[cursor]
                cursor += 1
                if cursor < len(text) and text[cursor] == marker and marker in ";|&":
                    cursor += 1
                result.append((text[start:cursor], start, cursor, False))
                continue
            start, value, quoted = cursor, [], False
            while cursor < len(text) and not text[cursor].isspace() and text[cursor] not in punctuation:
                char = text[cursor]
                if char in "'\"":
                    quoted = True
                    quote = char
                    cursor += 1
                    while cursor < len(text) and text[cursor] != quote:
                        value.append(text[cursor])
                        cursor += 1
                    if cursor == len(text):
                        raise MakeContractError("unterminated shell quote near recursive Make")
                    cursor += 1
                elif char == "\\":
                    quoted = True
                    cursor += 1
                    if cursor == len(text):
                        raise MakeContractError("unterminated shell escape near recursive Make")
                    value.append(text[cursor])
                    cursor += 1
                else:
                    value.append(char)
                    cursor += 1
            result.append(("".join(value), start, cursor, quoted))
        return result

    discovered_occurrences: list[tuple[str, tuple[int, int], tuple[str, ...], tuple[int, int]]] = []
    spans: list[tuple[int, int]] = []
    for target_name, recipe, offsets, recipe_span in recipes:
        current: list[tuple[str, int, int, bool]] = []
        for word, start, end, quoted in [*tokens(recipe), (";", len(recipe), len(recipe), False)]:
            if word in {";", "&&", "||", "|", "&"}:
                command = 0
                while command < len(current) and assignment.fullmatch(current[command][0]):
                    command += 1
                if command < len(current) and current[command][0] == "$(MAKE)" and not current[command][3]:
                    argv = tuple(token for token, _start, _end, _quoted in current[command + 1:])
                    make_start, make_end = current[command][1:3]
                    if offsets[make_start] is None or offsets[make_end - 1] is None:
                        raise MakeContractError("recursive Make word crosses a source rewrite boundary")
                    source_span = (offsets[make_start], offsets[make_end - 1] + 1)
                    command_span = (make_start, make_end)
                    discovered_occurrences.append((target_name, recipe_span, argv, command_span))
                    spans.append(source_span)
                current = []
                continue
            current.append((word, start, end, quoted))

    if Counter(discovered_occurrences) != Counter(_APPROVED_RECURSIVE_MAKE_OCCURRENCES):
        raise MakeContractError("unapproved recursive Make form")
    occurrences = [(match.start(), match.end()) for match in re.finditer(r"\$\(MAKE\)", source)]
    if occurrences != spans:
        raise MakeContractError("unapproved recursive Make form")
    return spans


def _prelaunch_quarantine(source: str) -> None:
    _conditional_quarantine(source)
    logical = _make_logical_lines(source)
    shells = [line for _first, _last, line in logical if "$(shell" in line]
    if shells != [_EXACT_LOCK_SHELL]:
        raise MakeContractError("unexpected $(shell ...) evaluator")
    if any(token in source for token in ("$(eval", "${eval", "$(file", "${file", "$(guile", "${guile", "$(load", "${load")):
        raise MakeContractError("unsafe Make evaluator")
    if re.search(r"\$\$\{?MAKE\}?", source):
        raise MakeContractError("shell-level MAKE indirection")
    if re.search(r"uv\s+run\s+python(?:\s+-m)?\s+['\"]?\$\$", source):
        raise MakeContractError("shell-variable guarded program position")
    if any(
        any(marker in line for marker in _GUARDED_WORDS)
        and any(token in line for token in ("`", "eval ", " -exec ", "xargs ", "bash -c", "sh -c"))
        for line in source.splitlines()
    ):
        raise MakeContractError("opaque shell evaluator near guarded launcher")
    if any(re.match(r"^\t[@-]*\+", line) for line in source.splitlines()):
        raise MakeContractError("recipe + execution escape")
    if any("%" in line and re.match(r"^[^\t #][^:=#]*:(?![=])", line) for line in source.splitlines()):
        raise MakeContractError("unenumerable generated Make target")
    for _first, _last, line in logical:
        if re.match(r"\s*(?:-|s)?include\b", line):
            raise MakeContractError("included Makefile is not observable")
    _recursive_make_projection_spans(source)


def _database_concrete_targets(database: str) -> list[str]:
    """Enumerate concrete root targets from GNU Make's own ``# Files`` table."""
    targets: list[str] = []
    in_files = False
    for line in database.splitlines():
        if line == "# Files":
            in_files = True
            continue
        if in_files and line == "# Finished Make data base":
            break
        if not in_files or line.startswith("#") or line.startswith("\t"):
            continue
        match = re.fullmatch(r"([^\s:#][^:#]*):(?:\s.*)?", line)
        if match is None:
            continue
        target = match.group(1)
        if "%" in target:
            raise MakeContractError(f"unenumerable generated Make target: {target}")
        if target not in {".DEFAULT", ".SUFFIXES"}:
            targets.append(target)
    if not targets:
        raise MakeContractError("GNU Make database yielded no concrete targets")
    return list(dict.fromkeys(targets))


def _assert_t_g03_make_launch_contract(makefile: str) -> None:
    """Observe GNU Make expansion only, with a copied recursive-Make-safe file."""
    _prelaunch_quarantine(makefile)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        sandbox = Path(raw)
        source_digest = hashlib.sha256(makefile.encode()).hexdigest()
        projection = sandbox / "Makefile"
        replacements = _recursive_make_projection_spans(makefile)
        projected_parts: list[str] = []
        cursor = 0
        for start, end in replacements:
            projected_parts.extend((makefile[cursor:start], "make-t-g03-noop"))
            cursor = end
        projected_parts.append(makefile[cursor:])
        projected = "".join(projected_parts)
        assert hashlib.sha256(makefile.encode()).hexdigest() == source_digest
        assert all(
            makefile[start:end] == "$(MAKE)"
            and not any(word in makefile[start:end] for word in _GUARDED_WORDS)
            for start, end in replacements
        )
        projection.write_text(projected, encoding="utf-8")
        env = {
            "PATH": "/usr/bin:/bin", "HOME": str(sandbox / "home"),
            "TEST_EVIDENCE_DIR": str(sandbox / "evidence"),
            "FOUNDATION_CONTEXT_PATH": str(sandbox / "foundation-context.json"),
            "GITHUB_RUN_ID": "99999999999", "RUNNER_TEMP": str(sandbox / "runner-temp"),
        }
        tripwire = sandbox / "shell-tripwire"
        tripwire.write_text("#!/bin/sh\nprintf '%s\\n' recipe-execution-forbidden >&2\nexit 97\n", encoding="utf-8")
        tripwire.chmod(0o700)
        make_variables = ["RUNTIME_RELEASE_LOCK_SHA256=" + "0" * 64, f"SHELL={tripwire}"]
        source_spans = {
            physical: (first, last)
            for first, last, _logical in _make_logical_lines(makefile)
            for physical in range(first, last + 1)
        }
        transform_map = [
            {"kind": "recursive-make-noop", "source_span": source_spans[makefile[:start].count("\n") + 1], "source": "$(MAKE)", "projection": "make-t-g03-noop"}
            for start, _end in replacements
        ]
        if any("scripts." in str(entry["source"]) for entry in transform_map):
            raise MakeContractError("transform may not remove guarded words")
        version = subprocess.run(["make", "--version"], env=env, text=True, capture_output=True, check=False)
        if version.returncode or not version.stdout.startswith("GNU Make"):
            raise MakeContractError("GNU Make is required")
        database = subprocess.run(
            ["make", "--no-builtin-rules", "--no-builtin-variables", "--always-make", "--print-data-base", "--question", "--file", str(projection), *make_variables],
            cwd=sandbox, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if database.returncode not in {0, 1}:
            raise MakeContractError(f"Make database failed: {database.stdout}")
        targets = _database_concrete_targets(database.stdout)
        observed: list[tuple[str, tuple[str, ...]]] = []
        seen_spans: set[tuple[str, int, tuple[str, ...]]] = set()
        trace = re.compile(r":(\d+): target '([^']+)'")
        for requested in targets:
            dry = subprocess.run(
                ["make", "--no-builtin-rules", "--no-builtin-variables", "--always-make", "--dry-run", "--trace", "--file", str(projection), requested, *make_variables],
                cwd=sandbox, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            if dry.returncode:
                raise MakeContractError(f"Make dry-run failed for {requested}: {dry.stdout}")
            current: tuple[str, int, tuple[int, int]] | None = None
            pending: str | None = None
            for line in dry.stdout.splitlines():
                if pending is not None:
                    pending += " " + line.strip()
                    if (len(pending.rstrip()) - len(pending.rstrip().rstrip("\\"))) % 2:
                        pending = pending.rstrip()[:-1]
                        continue
                    line, pending = pending, None
                match = trace.search(line)
                if match:
                    projected_line = int(match.group(1))
                    current = (match.group(2), projected_line, source_spans.get(projected_line, (projected_line, projected_line)))
                    continue
                if current is None:
                    continue
                if (len(line.rstrip()) - len(line.rstrip().rstrip("\\"))) % 2:
                    pending = line.rstrip()[:-1]
                    continue
                if "uv run" not in line:
                    continue
                try:
                    lexer = shlex.shlex(line, posix=True, punctuation_chars=";|&")
                    lexer.whitespace_split, lexer.commenters = True, ""
                    tokens = list(lexer)
                except ValueError as exc:
                    raise MakeContractError(f"unparseable emitted shell command: {line!r}: {exc}") from exc
                statement: list[str] = []
                for token in [*tokens, ";"]:
                    if token not in {";", ";;", "&&", "||", "|", "&"}:
                        statement.append(token)
                        continue
                    index = 0
                    while index < len(statement) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", statement[index]):
                        index += 1
                    argv = tuple(statement[index:])
                    if argv and argv[0] not in {"echo", "printf"} and "echo" not in argv and any(word in _GUARDED_WORDS for word in argv):
                        argv = tuple(re.sub(r"\$(?:\{(FOUNDATION_CONTEXT_PATH)\}|(FOUNDATION_CONTEXT_PATH))", env["FOUNDATION_CONTEXT_PATH"], word) for word in argv)
                        span = (*current, argv)
                        if span not in seen_spans:
                            observed.append((current[0], argv))
                            seen_spans.add(span)
                    statement = []
        evidence, context = env["TEST_EVIDENCE_DIR"], env["FOUNDATION_CONTEXT_PATH"]
        topology = ("--evidence-root", evidence, "--foundation-context-path", context)
        expected = [
            ("check-test-skips", ("uv", "run", "python", "-m", "scripts.check_test_governance", "--report-dir", evidence + "/test-governance")), ("check-test-governance-topology", ("uv", "run", "python", "-m", "scripts.check_test_governance", "--topology-audit", "--report-dir", evidence + "/test-governance-topology", "--topology-evidence-root", evidence, "--inventory", "tests/fixtures/t-g03a-hosted-failure-inventory.tsv", "--foundation-context-path", context)),
            ("test-portable-source", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "reserve", *topology)), ("test-portable-source", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "collect-baseline", *topology)), ("test-portable-source", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "run-lane", "--lane", "portable-source", *topology)),
            ("test-native-capabilities", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "reserve", *topology)), ("test-native-capabilities", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "run-lane", "--lane", "native-capabilities", *topology)),
            ("test-external-authorities", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "reserve", *topology)), ("test-external-authorities", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "run-lane", "--lane", "external-authorities", *topology)),
            ("test-portable-root-remainder", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "collect-baseline", *topology)), ("test-portable-root-remainder", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "prepare-remainder", *topology)), ("test-portable-root-remainder", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "run-remainder", *topology)),
            ("ci-portable-topology", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "reserve", *topology)), ("ci-portable-topology", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "run-lane", "--lane", "portable-source", *topology)), ("ci-portable-topology", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "run-lane", "--lane", "native-capabilities", *topology)), ("ci-portable-topology", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "run-lane", "--lane", "external-authorities", *topology)), ("ci-portable-topology", ("uv", "run", "python", "-m", "scripts.t_g03_capability_topology", "aggregate", *topology)),
        ]
        target_order = {target: index for index, target in enumerate(dict.fromkeys(target for target, _argv in expected))}
        observed.sort(key=lambda entry: target_order.get(entry[0], len(target_order)))
        if observed != expected:
            raise MakeContractError(f"noncanonical GNU Make expansion: {observed!r}")


def test_t_g03_make_launches_use_the_complete_canonical_module_contract() -> None:
    """Break caught: a T-G03 Make launch bypasses the package module boundary."""
    _assert_t_g03_make_launch_contract((ROOT / "Makefile").read_text(encoding="utf-8"))


def test_t_g03_make_contract_rejects_a_nonrecursive_make_display_before_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: projection rewrites a display argument as recursive Make."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + (
        "\nfuture-display: ; @echo $(MAKE) ci\n"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Make must not start for display data"),
    )

    with pytest.raises(MakeContractError, match="recursive Make"):
        _assert_t_g03_make_launch_contract(source)


def test_t_g03_make_contract_rejects_a_new_recursive_make_form_before_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a new root recursive Make route reaches the observer."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + (
        "\nfuture-recursive-review: ; $(MAKE) ci\n"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Make must not start for a new recursive form"),
    )

    with pytest.raises(MakeContractError, match="recursive Make"):
        _assert_t_g03_make_launch_contract(source)


@pytest.mark.parametrize(
    "replacement",
    (
        "\t$(MAKE) prepare-root-test-install; $(MAKE) prepare-root-test-install",
        "\t:",
    ),
)
def test_t_g03_make_contract_requires_each_approved_recursive_make_occurrence_exactly_once(
    replacement: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: an approved recursive Make command is duplicated or removed."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    approved = "ci-private:\n\t$(MAKE) prepare-root-test-install\n"
    assert source.count(approved) == 1
    source = source.replace(approved, f"ci-private:\n{replacement}\n")
    invoked = False

    def forbidden(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        pytest.fail("Make must not start for recursive Make occurrence drift")

    monkeypatch.setattr(subprocess, "run", forbidden)

    with pytest.raises(MakeContractError, match="recursive Make"):
        _assert_t_g03_make_launch_contract(source)
    assert not invoked


def test_t_g03_make_contract_rejects_call_expansion_that_materializes_a_direct_file() -> None:
    """Break caught: GNU Make functions materialize a direct-file launcher."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + (
        "\nRUN = uv run python scripts/t_g03_capability_topology.py reserve\n"
        "future-call-expansion:\n"
        "\t$(call RUN)\n"
    )

    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract(source)


def test_t_g03_make_contract_rejects_a_database_generated_direct_file_target() -> None:
    """Break caught: a variable-expanded target escapes source-only enumeration."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + (
        "\nHIDDEN_T_G03_TARGET = future-generated-t-g03-direct\n"
        "$(HIDDEN_T_G03_TARGET):\n"
        "\tuv run python scripts/t_g03_capability_topology.py reserve\n"
    )

    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract(source)


def test_t_g03_make_contract_fails_closed_for_a_guarded_pattern_rule() -> None:
    """Break caught: an uninstantiated pattern recipe hides a direct-file launch."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + (
        "\nfuture-%: ; uv run python scripts/t_g03_capability_topology.py reserve\n"
    )
    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract(source)


@pytest.mark.parametrize("expansion", ("$(call RUN)", "$(value RUN)", "$(strip $(RUN))", "$(addprefix ,$(RUN))", "$(RUN)"))
def test_t_g03_make_contract_rejects_gnu_make_function_or_append_bypasses(expansion: str) -> None:
    """Break caught: GNU Make expansion, rather than a source parser, creates a direct file."""
    assignment = "RUN = uv run python scripts/t_g03_capability_topology.py reserve"
    if expansion == "$(RUN)":
        assignment = "RUN = uv run python scripts/t_g03_capability_topology.py\nRUN += reserve"
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + f"\n{assignment}\nfuture-expansion:\n\t{expansion}\n"
    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract(source)


@pytest.mark.parametrize("directive", ("ifeq (0,1)", "ifneq (0,0)", "ifdef NEVER", "ifndef ALWAYS", "else", "endif", "else ifeq (0,1)", "else ifneq (0,0)", "else ifdef NEVER", "else ifndef ALWAYS"))
def test_t_g03_conditional_quarantine_rejects_every_root_conditional_before_make(directive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Break caught: an inactive conditional hides an unobserved guarded route."""
    invoked = False
    def forbidden(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("Make must not start after conditional quarantine")
    monkeypatch.setattr(subprocess, "run", forbidden)
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + f"\n{directive}\n\tuv run python scripts/t_g03_capability_topology.py reserve\n"
    with pytest.raises(MakeContractError, match="conditional|unmatched"):
        _assert_t_g03_make_launch_contract(source)
    assert not invoked


def test_t_g03_conditional_quarantine_accepts_display_and_define_values() -> None:
    """Break caught: inert comments, recipes, define bodies, and continued values are directives."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + "\n" + "\n".join((
        "# ifeq (0,1)", "define SAFE_TEXT", "ifeq literal define data", "endef",
        "SAFE_DISPLAY = literal " + "\\", "ifeq display data", "# continued comment " + "\\", "ifeq comment data",
        "future-safe-display:", "\t@echo ifeq literal recipe data", "",
    ))
    _assert_t_g03_make_launch_contract(source)


@pytest.mark.parametrize("body", (
    "ifeq (0,1)\nelse\nelse\nendif", "ifeq (0,1)\nelse", "else\nendif",
    "ifeq", "ifeq (0,1) \\", "ifeq (0,1)\nifdef NEVER\nendif\nendif",
    "ifeq (0,1)\ndefine HIDDEN\nuv run python scripts/t_g03_capability_topology.py reserve\nendef\nendif",
))
def test_t_g03_conditional_quarantine_structural_matrix_starts_no_make(body: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Break caught: structural/inactive conditional routes reach the projection."""
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: pytest.fail("Make started"))
    with pytest.raises(MakeContractError):
        _assert_t_g03_make_launch_contract((ROOT / "Makefile").read_text(encoding="utf-8") + "\n" + body + "\n")


@pytest.mark.parametrize("suffix", (
    "\ndefine RUN\nuv run python scripts/t_g03_capability_topology.py reserve\nendef\nfuture-define:\n\t$(call RUN)\n",
    "\nfuture-target-specific: RUN = uv run python scripts/t_g03_capability_topology.py reserve\nfuture-target-specific:\n\t$(RUN)\n",
    "\nfuture-parent: RUN = uv run python scripts/t_g03_capability_topology.py reserve\nfuture-parent: future-prereq\nfuture-prereq:\n\t$(RUN)\n",
    "\nfuture-extra:\n\tuv run python -m scripts.t_g03_capability_topology reserve\n",
))
def test_t_g03_make_observer_rejects_define_variable_and_exact_inventory_drift(suffix: str) -> None:
    """Break caught: Make expansion or an extra site drifts the exact inventory."""
    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract((ROOT / "Makefile").read_text(encoding="utf-8") + suffix)


def test_t_g03_make_observer_rejects_missing_canonical_module_site() -> None:
    """Break caught: a canonical topology launch is removed from its real recipe."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    removed = (
        "\t\tuv run python -m scripts.t_g03_capability_topology run-lane "
        "--lane native-capabilities --evidence-root \"$(TEST_EVIDENCE_DIR)\" "
        "--foundation-context-path \"$$FOUNDATION_CONTEXT_PATH\"\n"
    )
    assert source.count(removed) == 1

    with pytest.raises(MakeContractError, match="noncanonical GNU Make expansion"):
        _assert_t_g03_make_launch_contract(source.replace(removed, "\t\t:\n"))


def test_t_g03_make_observer_rejects_reordered_canonical_module_sites() -> None:
    """Break caught: canonical topology launches run in an order different from the contract."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    ordered = (
        "\t\tuv run python -m scripts.t_g03_capability_topology collect-baseline "
        "--evidence-root \"$(TEST_EVIDENCE_DIR)\" --foundation-context-path "
        "\"$$FOUNDATION_CONTEXT_PATH\"; \\\n"
        "\t\tuv run python -m scripts.t_g03_capability_topology prepare-remainder "
        "--evidence-root \"$(TEST_EVIDENCE_DIR)\" --foundation-context-path "
        "\"$$FOUNDATION_CONTEXT_PATH\"; \\\n"
    )
    reordered = (
        "\t\tuv run python -m scripts.t_g03_capability_topology prepare-remainder "
        "--evidence-root \"$(TEST_EVIDENCE_DIR)\" --foundation-context-path "
        "\"$$FOUNDATION_CONTEXT_PATH\"; \\\n"
        "\t\tuv run python -m scripts.t_g03_capability_topology collect-baseline "
        "--evidence-root \"$(TEST_EVIDENCE_DIR)\" --foundation-context-path "
        "\"$$FOUNDATION_CONTEXT_PATH\"; \\\n"
    )
    assert source.count(ordered) == 1

    with pytest.raises(MakeContractError, match="noncanonical GNU Make expansion"):
        _assert_t_g03_make_launch_contract(source.replace(ordered, reordered))


@pytest.mark.parametrize(
    ("suffix", "target"),
    (
        (
            "\nfuture-prefix.t-g03-direct-file:\n"
            "\t@-+uv run python scripts/t_g03_capability_topology.py reserve",
            "future-prefix.t-g03-direct-file",
        ),
        (
            "\nfuture-inline.t-g03-direct-file: ; uv run python "
            "scripts/t_g03_capability_topology.py reserve",
            "future-inline.t-g03-direct-file",
        ),
        (
            "\nfuture-continuation.t-g03-direct-file:\n"
            "\tuv run python \\\n"
            "\t\tscripts/t_g03_capability_topology.py reserve",
            "future-continuation.t-g03-direct-file",
        ),
        (
            "\nfuture-even-boundary.t-g03-direct-file:\n"
            "\tprintf '%s\\n' prior " "\\\\" "\n"
            "\tuv run python scripts/t_g03_capability_topology.py reserve",
            "future-even-boundary.t-g03-direct-file",
        ),
        (
            "\nfuture-python-option.t-g03-direct-file:\n"
            "\tuv run python -B scripts/t_g03_capability_topology.py reserve",
            "future-python-option.t-g03-direct-file",
        ),
        (
            "\nfuture-env.t-g03-direct-file:\n"
            "\tenv -i T_G03_TEST=1 uv run python scripts/t_g03_capability_topology.py reserve",
            "future-env.t-g03-direct-file",
        ),
        (
            "\nfuture-brace.t-g03-direct-file:\n"
            "\t{ uv run python scripts/t_g03_capability_topology.py reserve; }",
            "future-brace.t-g03-direct-file",
        ),
        (
            "\nfuture-command-wrapper.t-g03-direct-file:\n"
            "\tcommand uv run python scripts/t_g03_capability_topology.py reserve",
            "future-command-wrapper.t-g03-direct-file",
        ),
        (
            "\nfuture-uv-delimiter.t-g03-direct-file:\n"
            "\tuv run -- python scripts/t_g03_capability_topology.py reserve",
            "future-uv-delimiter.t-g03-direct-file",
        ),
        (
            "\nfuture-if-condition.t-g03-direct-file:\n"
            "\tif uv run python scripts/t_g03_capability_topology.py reserve; then :; fi",
            "future-if-condition.t-g03-direct-file",
        ),
        (
            "\nfuture-while-condition.t-g03-direct-file:\n"
            "\twhile uv run python scripts/t_g03_capability_topology.py reserve; do :; done",
            "future-while-condition.t-g03-direct-file",
        ),
        (
            "\nfuture-case-arm.t-g03-direct-file:\n"
            "\tcase x in x) uv run python scripts/t_g03_capability_topology.py reserve ;; esac",
            "future-case-arm.t-g03-direct-file",
        ),
    ),
    ids=(
        "recipe-prefix", "inline-recipe", "odd-continuation", "even-boundary",
        "python-option", "env-wrapper", "brace-group", "command-wrapper",
        "uv-delimiter", "if-condition", "while-condition", "case-arm",
    ),
)
def test_t_g03_make_contract_rejects_all_direct_file_recipe_shapes(
    suffix: str, target: str,
) -> None:
    """Break caught: a direct executable shape bypasses the canonical-module contract."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + suffix

    detected = _make_direct_file_commands(source, "scripts/t_g03_capability_topology.py")
    assert [observed_target for observed_target, _argv in detected] == [target]
    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract(source)


@pytest.mark.parametrize(
    "suffix",
    (
        (
            "\nfuture-python-option-module.t-g03-noncanonical:\n"
            "\tuv run python -B -m scripts.t_g03_capability_topology reserve"
        ),
        (
            "\nfuture-uv-delimiter-module.t-g03-noncanonical:\n"
            "\tuv run -- python -m scripts.t_g03_capability_topology reserve"
        ),
    ),
    ids=("python-option-before-module", "uv-delimiter-before-module"),
)
def test_t_g03_make_contract_rejects_noncanonical_module_entrypoints(
    suffix: str,
) -> None:
    """Break caught: a guarded tool is launched without its exact approved argv head."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + suffix

    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract(source)


def test_t_g03_make_contract_fails_closed_for_an_unsupported_function_body() -> None:
    """Break caught: unsupported shell grammar hides a guarded direct launch."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + (
        "\nfuture-function.t-g03-direct-file:\n"
        "\tfunction future_t_g03() { uv run python scripts/t_g03_capability_topology.py reserve; }; future_t_g03"
    )

    assert [target for target, _argv in _make_noncanonical_guarded_commands(source)] == [
        "future-function.t-g03-direct-file",
    ]
    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract(source)


@pytest.mark.parametrize(
    ("suffix", "target"),
    (
        (
            "\nfuture-variable-direct.t-g03-noncanonical:\n"
            "\tscript=scripts/t_g03_capability_topology.py; "
            "uv run python \"$$script\" reserve",
            "future-variable-direct.t-g03-noncanonical",
        ),
        (
            "\nfuture-variable-module.t-g03-noncanonical:\n"
            "\tmodule=scripts.t_g03_capability_topology; "
            "uv run python -m \"$${module}\" reserve",
            "future-variable-module.t-g03-noncanonical",
        ),
        (
            "\nfuture-command-substitution.t-g03-noncanonical:\n"
            "\tuv run python \"$$(printf scripts/t_g03_capability_topology.py)\" reserve",
            "future-command-substitution.t-g03-noncanonical",
        ),
        (
            "\nfuture-backticks.t-g03-noncanonical:\n"
            "\tuv run python \"`printf scripts/t_g03_capability_topology.py`\" reserve",
            "future-backticks.t-g03-noncanonical",
        ),
        (
            "\nfuture-eval.t-g03-noncanonical:\n"
            "\teval \"uv run python scripts/t_g03_capability_topology.py reserve\"",
            "future-eval.t-g03-noncanonical",
        ),
        (
            "\nfuture-shell-c.t-g03-noncanonical:\n"
            "\tbash -c \"uv run python -m scripts.t_g03_capability_topology reserve\"",
            "future-shell-c.t-g03-noncanonical",
        ),
    ),
    ids=(
        "shell-variable-direct-file",
        "shell-variable-module",
        "command-substitution",
        "backticks",
        "eval",
        "shell-c",
    ),
)
def test_t_g03_make_contract_rejects_opaque_entrypoint_evaluation(
    suffix: str, target: str,
) -> None:
    """Break caught: an evaluator or dynamic executable hides a guarded launch."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + suffix

    assert [observed_target for observed_target, _argv in _make_noncanonical_guarded_commands(source)] == [target]
    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract(source)


@pytest.mark.parametrize(
    ("suffix", "target"),
    (
        (
            "\nT_G03_SCRIPT = scripts/t_g03_capability_topology.py\n"
            "future-make-direct.t-g03-noncanonical:\n"
            "\tuv run python \"$(T_G03_SCRIPT)\" reserve",
            "future-make-direct.t-g03-noncanonical",
        ),
        (
            "\nT_G03_MODULE = scripts.t_g03_capability_topology\n"
            "future-make-module.t-g03-noncanonical:\n"
            "\tuv run python -m \"${T_G03_MODULE}\" reserve",
            "future-make-module.t-g03-noncanonical",
        ),
        (
            "\nfuture-find-exec.t-g03-noncanonical:\n"
            "\tfind . -exec sh -c \"uv run python scripts/t_g03_capability_topology.py reserve\" \\\\;",
            "future-find-exec.t-g03-noncanonical",
        ),
        (
            "\nfuture-xargs.t-g03-noncanonical:\n"
            "\tprintf '%s\\n' . | xargs sh -c \"uv run python scripts/t_g03_capability_topology.py reserve\"",
            "future-xargs.t-g03-noncanonical",
        ),
        (
            "\n$(eval future-make-eval.t-g03-noncanonical: ; "
            "uv run python scripts/t_g03_capability_topology.py reserve)",
            "future-make-eval.t-g03-noncanonical",
        ),
    ),
    ids=("make-variable-direct-file", "make-variable-module", "find-exec-shell-body", "xargs-shell-body", "make-eval"),
)
def test_t_g03_make_contract_rejects_recursive_or_make_expanded_entrypoints(
    suffix: str, target: str,
) -> None:
    """Break caught: a recursive or Make-expanded command hides a T-G03 launch."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + suffix

    if target == "future-make-eval.t-g03-noncanonical":
        assert _make_opaque_guarded_evaluations(source)
    else:
        assert [observed_target for observed_target, _argv in _make_noncanonical_guarded_commands(source)] == [target]
    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract(source)


@pytest.mark.parametrize(
    "suffix",
    (
        (
            "\nRULE = future-make-eval-variable.t-g03-noncanonical: ; "
            "uv run python scripts/t_g03_capability_topology.py reserve\n"
            "$(eval $(RULE))"
        ),
        (
            "\nRUN = uv run python scripts/t_g03_capability_topology.py reserve\n"
            "future-make-command-variable.t-g03-noncanonical:\n"
            "\t$(RUN)"
        ),
    ),
    ids=("eval-variable-rule", "recipe-command-variable"),
)
def test_t_g03_make_contract_rejects_generic_make_indirection_to_guarded_launcher(
    suffix: str,
) -> None:
    """Break caught: an ordinary Make macro materializes a guarded non-module launch."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + suffix

    with pytest.raises(AssertionError):
        _assert_t_g03_make_launch_contract(source)


def test_t_g03_make_contract_allows_a_statically_resolved_benign_recipe_macro() -> None:
    """Break caught: a safe non-launcher Make command macro becomes overblocked."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + (
        "\nSAFE_DISPLAY = printf 'topology contract intact\\n'\n"
        "future-safe-recipe-macro:\n"
        "\t$(SAFE_DISPLAY)"
    )

    assert not _make_noncanonical_guarded_commands(source)
    _assert_t_g03_make_launch_contract(source)


def test_t_g03_make_contract_keeps_safe_evidence_path_expansions_outside_launcher() -> None:
    """Break caught: safe current evidence-path Make expansion is rejected as executable indirection."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + (
        "\nfuture-safe-evidence-path:\n"
        "\tuv run python -m scripts.t_g03_capability_topology reserve "
        "--evidence-root \"$(TEST_EVIDENCE_DIR)\""
    )

    assert not _make_noncanonical_guarded_commands(source)


@pytest.mark.parametrize(
    "suffix",
    (
        (
            "\nfuture-echo.t-g03-direct-file:\n"
            "\t@echo uv run python scripts/t_g03_capability_topology.py reserve"
        ),
        (
            "\nfuture-brace-display.t-g03-direct-file:\n"
            "\t{ echo uv run python scripts/t_g03_capability_topology.py reserve; }"
        ),
        (
            "\nfuture-even-display.t-g03-direct-file:\n"
            "\tprintf '%s\\n' prior " "\\\\" "\n"
            "\t@echo uv run python scripts/t_g03_capability_topology.py reserve"
        ),
        (
            "\nfuture-case-display.t-g03-direct-file:\n"
            "\tcase x in x) command echo uv run python scripts/t_g03_capability_topology.py reserve ;; esac"
        ),
    ),
    ids=("echo", "brace-echo", "even-boundary-echo", "case-command-echo"),
)
def test_t_g03_make_contract_does_not_treat_display_data_as_an_executable_argv(
    suffix: str,
) -> None:
    """Break caught: display-only direct-file spellings are treated as process launches."""
    source = (ROOT / "Makefile").read_text(encoding="utf-8") + suffix

    assert not _make_direct_file_commands(source, "scripts/t_g03_capability_topology.py")
    _assert_t_g03_make_launch_contract(source)


def _write_topology_evidence(evidence: Path, *, malformed_root_record: bool = False) -> tuple[str, str]:
    run_id = "31641536482"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    inventory = ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv"
    rows = topology.load_inventory(inventory)
    topology.reserve_topology_evidence(evidence, run_id=run_id, head_sha=head_sha)
    (evidence / "t-g03a-hosted-failure-inventory.tsv").write_bytes(inventory.read_bytes())
    topology_root = evidence / "capability-topology"
    ordinary = "tests/ordinary/test_portable.py::test_ordinary"
    candidates = tuple(sorted([*(row.node_id for row in rows), ordinary]))
    candidate_bytes = topology._candidate_file_bytes(candidates)
    collection = {
        "schema_version": 1,
        "component": "root",
        "collection_only": True,
        "pytest_exit_status": 0,
        "tests": [{
            "test_node_id": node, "component": "root", "outcome": "collected", "reason": "", "phase": "collection",
        } for node in candidates],
    }
    collection_bytes = json.dumps(collection, sort_keys=True).encode("utf-8")
    (topology_root / "portable-root-collection.governance.json").write_bytes(collection_bytes)
    baseline: dict[str, object] = {
        "schema_version": topology.BASELINE_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": head_sha,
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "collector_policy": {
            **topology.PORTABLE_ROOT_POLICY,
            "native_custody_extension_identity": "1:2:1000:600:1",
            "native_custody_extension_sha256": "0" * 64,
        },
        "candidate_node_ids": list(candidates),
        "candidate_file_sha256": topology.hashlib.sha256(candidate_bytes).hexdigest(),
        "collection_report_sha256": topology.hashlib.sha256(collection_bytes).hexdigest(),
        "baseline_sha256": "",
    }
    baseline["baseline_sha256"] = topology._baseline_payload_sha256(baseline)
    (topology_root / "portable-root-candidates.txt").write_bytes(candidate_bytes)
    (topology_root / "portable-root-baseline.json").write_bytes(topology.canonical_json_bytes(baseline))
    remainder_bytes = topology._candidate_file_bytes((ordinary,))
    remainder: dict[str, object] = {
        "schema_version": topology.REMAINDER_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": head_sha,
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "baseline_sha256": baseline["baseline_sha256"],
        "remainder_node_ids": [ordinary],
        "remainder_file_sha256": topology.hashlib.sha256(remainder_bytes).hexdigest(),
        "remainder_sha256": "",
    }
    remainder["remainder_sha256"] = topology._remainder_payload_sha256(remainder)
    (topology_root / "portable-root-remainder.txt").write_bytes(remainder_bytes)
    (topology_root / "portable-root-remainder.json").write_bytes(topology.canonical_json_bytes(remainder))
    (topology_root / "portable-root-remainder.governance.json").write_text(
        json.dumps({"schema_version": 1, "component": "root", "pytest_exit_status": 0, "custody_policy": baseline["collector_policy"], "tests": [{
            "test_node_id": ordinary, "component": "root", "outcome": "passed", "reason": "", "phase": "call",
        }]}),
        encoding="utf-8",
    )
    for code in topology.CODE_CLASSIFICATION:
        lane, expected = topology._expected_rows(rows, code)
        state, outcome = {
            "portable-source": ("AVAILABLE", "PASS"),
            "native-capabilities": ("UNAVAILABLE", "DEFERRED"),
            "external-authorities": ("ABSENT", "DEFERRED"),
        }[lane]
        receipt = topology.make_receipt(
            run_id=run_id,
            head_sha=head_sha,
            lane=lane,
            code=code,
            expected=expected,
            collected=expected if outcome == "PASS" else (),
            state=state,
            fact="SOURCE_TEST_EXECUTED" if lane == "portable-source" else (
                "NATIVE_COMPONENT_ABSENT" if lane == "native-capabilities" else "AUTHORITY_ROOT_ABSENT"
            ),
            outcome=outcome,
        )
        (topology_root / f"{code}.json").write_bytes(topology.canonical_json_bytes(receipt))
        if outcome == "PASS":
            observed = list(expected)
            if malformed_root_record:
                observed[-1] = "tests/hidden.py::test_not_selected"
            (topology_root / f"{code}.governance.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "component": "root",
                        "pytest_exit_status": 0,
                        "custody_policy": baseline["collector_policy"],
                        "tests": [
                            {
                                "test_node_id": node,
                                "component": "root",
                                "outcome": "passed",
                                "reason": "",
                                "phase": "call",
                            }
                            for node in observed
                        ],
                    }
                ),
                encoding="utf-8",
            )
    return run_id, head_sha


def test_topology_audit_discloses_deferred_receipts_without_claiming_pass() -> None:
    """Break caught: deferred runtime proofs become PASS or omit exact root evidence."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw)
        run_id, head_sha = _write_topology_evidence(evidence)
        disclosure, root_records = audit_topology_root_records(
            evidence_root=evidence,
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            foundation_run_id=run_id,
            foundation_head_sha=head_sha,
        )

    assert disclosure == {
        "portable_source_status": "PASS",
        "native_capabilities_status": "DEFERRED",
        "external_authorities_status": "DEFERRED",
        "runtime_proof": "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS",
    }
    assert len(root_records) == 33
    assert {record["outcome"] for record in root_records} == {"passed"}


def test_topology_audit_rejects_unsafe_raw_reason_presence_before_acceptance_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: malformed unsafe-reason evidence is ignored while aggregation still claims a result."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw)
        run_id, head_sha = _write_topology_evidence(evidence)
        (evidence / "capability-topology/portable-root-remainder.unsafe-raw-reason-nonacceptance.json").write_bytes(b"foreign")
        monkeypatch.setattr(
            topology, "load_portable_root_baseline",
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unsafe presence must stop before baseline")),
        )
        with pytest.raises(GovernanceError, match="unsafe raw reason nonacceptance is present"):
            audit_topology_root_records(
                evidence_root=evidence,
                inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                foundation_run_id=run_id,
                foundation_head_sha=head_sha,
            )


def test_topology_audit_rejects_a_root_record_that_does_not_match_its_receipt() -> None:
    """Break caught: a partial, extra, or unbound root lane record passes governance."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw)
        run_id, head_sha = _write_topology_evidence(evidence, malformed_root_record=True)
        with pytest.raises(GovernanceError, match="root topology governance"):
            audit_topology_root_records(
                evidence_root=evidence,
                inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                foundation_run_id=run_id,
                foundation_head_sha=head_sha,
            )


def test_topology_audit_never_reaches_the_generic_root_pytest_runner() -> None:
    """Break caught: topology governance calls run_suites and executes pytest tests broadly."""
    source = (ROOT / "scripts/check_test_governance.py").read_text(encoding="utf-8")
    topology_runner = re.search(
        r"^def run_topology_suites\(.+?(?=^def |\Z)", source, re.MULTILINE | re.DOTALL
    )

    assert topology_runner is not None
    body = topology_runner.group(0)
    assert "audit_topology_root_records" in body
    assert "run_suites(" not in body
    assert '"-m", "pytest", "-q", "-p", "scripts.test_governance_pytest", "tests"' not in body

    topology_source = (ROOT / "scripts/t_g03_capability_topology.py").read_text(encoding="utf-8")
    exact_runner = re.search(
        r"^def _run_exact\(.+?(?=^def |\Z)", topology_source, re.MULTILINE | re.DOTALL
    )
    assert exact_runner is not None
    assert "*nodes" in exact_runner.group(0)
    assert ', "tests"' not in exact_runner.group(0)


def test_topology_runner_merges_sealed_root_with_retained_component_governance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: topology mode drops legacy/dashboard governance after replacing root pytest."""
    with tempfile.TemporaryDirectory(dir="/tmp") as evidence_raw, tempfile.TemporaryDirectory(dir="/tmp") as report_raw:
        evidence = Path(evidence_raw)
        report_dir = Path(report_raw)
        run_id, head_sha = _write_topology_evidence(evidence)
        commands: list[tuple[str, ...]] = []

        def fake_run(command, *, env, **_kwargs):
            commands.append(tuple(command))
            Path(env["TEST_GOVERNANCE_REPORT"]).write_text(
                json.dumps({"tests": [{
                    "test_node_id": "legacy/tests/test_receipt.py::test_retained",
                    "component": "legacy",
                    "outcome": "passed",
                    "reason": "",
                    "phase": "call",
                }]}),
                encoding="utf-8",
            )
            return 0

        def fake_dashboard(directory: Path):
            report = directory / "dashboard-raw.json"
            report.write_text(
                json.dumps({"tests": [{
                    "test_node_id": "apps/dashboard/tests/policy.test.mjs::retained",
                    "component": "dashboard",
                    "outcome": "passed",
                    "reason": "",
                    "phase": "call",
                }]}),
                encoding="utf-8",
            )
            return 0, report

        monkeypatch.setattr(test_governance, "_run", fake_run)
        monkeypatch.setattr(test_governance, "_run_dashboard", fake_dashboard)
        records, exit_codes, disclosure = test_governance.run_topology_suites(
            report_dir,
            topology_evidence_root=evidence,
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            foundation_run_id=run_id,
            foundation_head_sha=head_sha,
        )

    assert commands == [("uv", "run", "--frozen", "--extra", "test", "pytest", "-q", "-p", "scripts.test_governance_pytest")]
    assert exit_codes == {"legacy": 0, "dashboard": 0}
    assert disclosure["runtime_proof"] == "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS"
    assert {str(record["component"]) for record in records} == {"root", "legacy", "dashboard"}


def test_dynamic_baseline_includes_a_new_ordinary_root_node_and_derives_the_exact_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: portable CI freezes the historical 62 IDs and loses a new root test."""
    run_id = "31641536482"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    rows = topology.load_inventory(ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
    candidates = tuple(sorted([*(row.node_id for row in rows), "tests/ordinary/test_new.py::test_new"] ))
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        extension = Path(raw) / "custody.so"
        extension.write_bytes(b"verified custody fixture")
        digest = topology.hashlib.sha256(extension.read_bytes()).hexdigest()
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256", digest)
        topology.reserve_topology_evidence(evidence, run_id=run_id, head_sha=head_sha)
        baseline = topology.collect_portable_root_baseline(
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
            collector=lambda: candidates,
        )
        remainder = topology.prepare_portable_root_remainder(
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
        )

    assert baseline["candidate_node_ids"] == list(candidates)
    assert remainder["remainder_node_ids"] == ["tests/ordinary/test_new.py::test_new"]


def test_remainder_executor_uses_only_the_verified_generated_node_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: the ordinary-root executor broad-runs a directory or replaces its generated list."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha = _write_topology_evidence(evidence)
        extension = Path(raw) / "custody.so"
        extension.write_bytes(b"custody")
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
        monkeypatch.setenv(
            "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
            topology.hashlib.sha256(extension.read_bytes()).hexdigest(),
        )
        selected: list[tuple[str, ...]] = []

        def exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
            selected.append(nodes)
            report.write_text(json.dumps({"schema_version": 1, "component": "root", "pytest_exit_status": 0, "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]), "tests": [{
                "test_node_id": node, "component": "root", "outcome": "passed", "reason": "", "phase": "call",
            } for node in nodes]}), encoding="utf-8")
            return nodes

        # Rebuild the sealed baseline with the real custody identity expected by the executor.
        topology_root = evidence / "capability-topology"
        baseline = json.loads((topology_root / "portable-root-baseline.json").read_text(encoding="utf-8"))
        baseline["collector_policy"] = topology._native_custody_policy()
        baseline["baseline_sha256"] = topology._baseline_payload_sha256(baseline)
        (topology_root / "portable-root-baseline.json").write_bytes(topology.canonical_json_bytes(baseline))
        remainder = json.loads((topology_root / "portable-root-remainder.json").read_text(encoding="utf-8"))
        remainder["baseline_sha256"] = baseline["baseline_sha256"]
        remainder["remainder_sha256"] = topology._remainder_payload_sha256(remainder)
        (topology_root / "portable-root-remainder.json").write_bytes(topology.canonical_json_bytes(remainder))
        (topology_root / "portable-root-remainder.governance.json").unlink()
        executed = topology.execute_portable_root_remainder(
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
            exact_runner=exact,
        )

    assert selected == [("tests/ordinary/test_portable.py::test_ordinary",)]
    assert executed == selected[0]


def test_extension_drift_after_remainder_blocks_the_next_pass_lane_and_closed_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a replaced custody extension permits a later green inventory lane."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        run_id, head_sha = _write_topology_evidence(evidence)
        extension = Path(raw) / "custody.so"
        extension.write_bytes(b"sealed custody")
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
        monkeypatch.setenv(
            "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
            topology.hashlib.sha256(extension.read_bytes()).hexdigest(),
        )
        topology_root = evidence / "capability-topology"
        baseline = json.loads((topology_root / "portable-root-baseline.json").read_text(encoding="utf-8"))
        baseline["collector_policy"] = topology._native_custody_policy()
        baseline["baseline_sha256"] = topology._baseline_payload_sha256(baseline)
        (topology_root / "portable-root-baseline.json").write_bytes(topology.canonical_json_bytes(baseline))
        remainder = json.loads((topology_root / "portable-root-remainder.json").read_text(encoding="utf-8"))
        remainder["baseline_sha256"] = baseline["baseline_sha256"]
        remainder["remainder_sha256"] = topology._remainder_payload_sha256(remainder)
        (topology_root / "portable-root-remainder.json").write_bytes(topology.canonical_json_bytes(remainder))
        for report in [
            topology_root / "portable-root-remainder.governance.json",
            *(
                topology_root / f"{code}.governance.json"
                for code, classification in topology.CODE_CLASSIFICATION.items()
                if classification == "PORTABLE_SOURCE_DEFECT"
            ),
        ]:
            document = json.loads(report.read_text(encoding="utf-8"))
            if document.get("component") == "root" and document.get("pytest_exit_status") == 0:
                document["custody_policy"] = baseline["collector_policy"]
                report.write_text(json.dumps(document), encoding="utf-8")

        remainder_report = topology_root / "portable-root-remainder.governance.json"
        remainder_report.unlink()

        def exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
            report.write_text(json.dumps({
                "schema_version": 1,
                "component": "root",
                "pytest_exit_status": 0,
                "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
                "tests": [{
                    "test_node_id": node,
                    "component": "root",
                    "outcome": "passed",
                    "reason": "",
                    "phase": "call",
                } for node in nodes],
            }), encoding="utf-8")
            return nodes

        topology.execute_portable_root_remainder(
            inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head_sha,
            exact_runner=exact,
        )
        for code, classification in topology.CODE_CLASSIFICATION.items():
            if classification == "PORTABLE_SOURCE_DEFECT":
                (topology_root / f"{code}.json").unlink()
                (topology_root / f"{code}.governance.json").unlink()

        extension.write_bytes(b"replaced custody")
        invoked = False

        def must_not_run(_nodes: tuple[str, ...], _report: Path) -> tuple[str, ...]:
            nonlocal invoked
            invoked = True
            raise AssertionError("custody drift must fail before pytest")

        with pytest.raises(topology.TopologyError, match="custody extension digest drift"):
            topology.run_lane(
                lane="portable-source",
                inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head_sha,
                exact_runner=must_not_run,
            )
        assert not invoked
        assert not any(topology_root.glob("SRC-*.json"))
        with pytest.raises(topology.TopologyError):
            topology.reconcile_portable_root_accounting(
                inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head_sha,
            )


def test_closed_root_accounting_rejects_duplicate_execution_between_remainder_and_inventory() -> None:
    """Break caught: an inventory node is also counted as an ordinary-root execution."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw)
        run_id, head_sha = _write_topology_evidence(evidence)
        topology_root = evidence / "capability-topology"
        remainder = json.loads((topology_root / "portable-root-remainder.json").read_text(encoding="utf-8"))
        duplicate = topology.load_inventory(
            ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
        )[0].node_id
        remainder["remainder_node_ids"] = sorted([*remainder["remainder_node_ids"], duplicate])
        contents = topology._candidate_file_bytes(tuple(remainder["remainder_node_ids"]))
        remainder["remainder_file_sha256"] = topology.hashlib.sha256(contents).hexdigest()
        remainder["remainder_sha256"] = topology._remainder_payload_sha256(remainder)
        (topology_root / "portable-root-remainder.txt").write_bytes(contents)
        (topology_root / "portable-root-remainder.json").write_bytes(topology.canonical_json_bytes(remainder))

        with pytest.raises(topology.TopologyError, match="baseline minus inventory"):
            topology.reconcile_portable_root_accounting(
                inventory=ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head_sha,
            )
