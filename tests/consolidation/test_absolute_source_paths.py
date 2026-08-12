from __future__ import annotations

import ast
import subprocess
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
COMPONENT_ROOTS = ("apps/dashboard/", "legacy/research-backend/")
SOURCE_SUFFIXES = {
    ".cjs",
    ".js",
    ".json",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
SOURCE_NAMES = {"AGENTS.md", "Makefile", "README.md"}
HISTORICAL_PREFIXES = (
    ".superpowers/",
    "apps/dashboard/audit/",
    "docs/implementation/",
    "legacy/research-backend/audit/",
)
HISTORICAL_FILES = {
    "docs/superpowers/plans/2026-07-13-canonical-source-consolidation.md",
}
FORBIDDEN_SOURCE_PATHS = (
    b".hermes",
    b"/home/thenam176/" + b".hermes",
    b"~/" + b".hermes",
    b".local/share/" + b"codex-worktrees",
    b"/home/thenam176/projects/" + b"trading-dashboard",
    b"/home/thenam176/projects/" + b"trading-agent-migration",
)


def _contains_legacy_source_path(content: bytes) -> bool:
    return any(marker in content for marker in FORBIDDEN_SOURCE_PATHS)


def _tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        raw.decode("utf-8")
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def _is_current_component_source(relative: str) -> bool:
    if not relative.startswith(COMPONENT_ROOTS):
        return False
    if relative in HISTORICAL_FILES or relative.startswith(HISTORICAL_PREFIXES):
        return False
    path = PurePosixPath(relative)
    return path.suffix in SOURCE_SUFFIXES or path.name in SOURCE_NAMES


class _TempfileUserHomeDirectoryVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._tempfile_modules: set[str] = set()
        self._tempfile_constructors: dict[str, str] = {}
        self._pathlib_modules: set[str] = set()
        self._path_constructors: set[str] = set()
        self.lines: list[int] = []

    def visit_Import(self, node: ast.Import) -> None:
        for imported in node.names:
            if imported.name == "tempfile":
                self._tempfile_modules.add(imported.asname or "tempfile")
            if imported.name == "pathlib":
                self._pathlib_modules.add(imported.asname or "pathlib")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "tempfile":
            for imported in node.names:
                if imported.name in {"mkdtemp", "TemporaryDirectory"}:
                    self._tempfile_constructors[imported.asname or imported.name] = (
                        imported.name
                    )
        if node.module == "pathlib":
            for imported in node.names:
                if imported.name == "Path":
                    self._path_constructors.add(imported.asname or "Path")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_selected_tempfile_constructor(node.func):
            directory = self._directory_argument(node)
            if directory is not None and self._is_literal_user_home(directory):
                self.lines.append(node.lineno)
        self.generic_visit(node)

    def _is_selected_tempfile_constructor(self, function: ast.expr) -> bool:
        if isinstance(function, ast.Name):
            return function.id in self._tempfile_constructors
        return (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id in self._tempfile_modules
            and function.attr in {"mkdtemp", "TemporaryDirectory"}
        )

    @staticmethod
    def _directory_argument(node: ast.Call) -> ast.expr | None:
        for keyword in node.keywords:
            if keyword.arg == "dir":
                return keyword.value
            if keyword.arg is None and isinstance(keyword.value, ast.Dict):
                for key, value in zip(keyword.value.keys, keyword.value.values):
                    if isinstance(key, ast.Constant) and key.value == "dir":
                        return value
        return node.args[2] if len(node.args) > 2 else None

    def _is_literal_user_home(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value.startswith("/home/")
        if not isinstance(node, ast.Call) or not node.args:
            return False
        if not self._is_resolved_path_constructor(node.func):
            return False
        first_argument = node.args[0]
        return (
            isinstance(first_argument, ast.Constant)
            and isinstance(first_argument.value, str)
            and first_argument.value.startswith("/home/")
        )

    def _is_resolved_path_constructor(self, function: ast.expr) -> bool:
        if isinstance(function, ast.Name):
            return function.id in self._path_constructors
        return (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id in self._pathlib_modules
            and function.attr == "Path"
        )


def _literal_user_home_tempfile_directory_lines(source: str) -> list[int]:
    visitor = _TempfileUserHomeDirectoryVisitor()
    visitor.visit(ast.parse(source))
    return visitor.lines


def _tracked_python_test_paths() -> list[str]:
    return [
        relative
        for relative in _tracked_paths()
        if relative.endswith(".py")
        and (
            relative.startswith("tests/")
            or relative.startswith("legacy/research-backend/tests/")
        )
    ]


def _literal_user_home_tempfile_directories() -> list[str]:
    violations: list[str] = []
    for relative in _tracked_python_test_paths():
        lines = _literal_user_home_tempfile_directory_lines(
            (ROOT / relative).read_text(encoding="utf-8")
        )
        violations.extend(f"{relative}:{line}" for line in lines)
    return violations


def test_tracked_component_sources_do_not_name_legacy_source_checkouts() -> None:
    failures: list[str] = []
    for relative in _tracked_paths():
        if not _is_current_component_source(relative):
            continue
        content = (ROOT / relative).read_bytes()
        if _contains_legacy_source_path(content):
            failures.append(relative)

    assert failures == [], "legacy source checkout paths:\n" + "\n".join(failures)


def test_scan_rejects_split_hermes_constructions_and_json_config() -> None:
    adversarial_sources = (
        b"path.join(home, '.hermes', 'crypto-research')",
        b"Path.home() / '.hermes' / 'crypto-research'",
        b'{"runtime_root":".hermes/crypto-research"}',
    )

    assert all(_contains_legacy_source_path(source) for source in adversarial_sources)


def test_tempfile_user_home_directory_scanner_resolves_selected_bindings() -> None:
    source = """\
import pathlib
import pathlib as pl
import tempfile
import tempfile as tf
from pathlib import Path
from pathlib import Path as P
from tempfile import TemporaryDirectory as TempDir
from tempfile import mkdtemp

tempfile.mkdtemp(dir=\"/home/direct-module\")
mkdtemp(dir=\"/home/direct-import\")
tf.TemporaryDirectory(dir=\"/home/module-alias\")
TempDir(dir=\"/home/direct-import-alias\")
tf.mkdtemp(\"prefix\", None, \"/home/third-positional\")
TempDir(\"suffix\", \"prefix\", \"/home/third-positional-tempdir\")
tf.TemporaryDirectory(**{\"dir\": \"/home/static-keyword\"})
tf.mkdtemp(dir=Path(\"/home/direct-path\"))
tf.mkdtemp(dir=pathlib.Path(\"/home/qualified-path\"))
tf.mkdtemp(dir=pl.Path(\"/home/module-alias-path\"))
tf.mkdtemp(dir=P(\"/home/direct-import-alias-path\"))
"""

    assert _literal_user_home_tempfile_directory_lines(source) == list(range(10, 21))


def test_tempfile_user_home_directory_scanner_ignores_nonselected_values() -> None:
    source = """\
import tempfile

temporary_root = \"/home/dynamic\"
tempfile.mkdtemp(dir=\"/tmp\")
tempfile.TemporaryDirectory(dir=temporary_root + \"/child\")
policy_runtime_identity = \"/home/thenam176/.cache/trading-agent\"
other_constructor(dir=\"/home/not-a-tempfile-directory\")
"""

    assert _literal_user_home_tempfile_directory_lines(source) == []


def test_tracked_python_test_fixtures_do_not_set_literal_user_home_temp_roots() -> None:
    assert _literal_user_home_tempfile_directories() == []
