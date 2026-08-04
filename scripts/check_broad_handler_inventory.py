from __future__ import annotations

import argparse
import ast
from pathlib import Path
import subprocess
import sys


START_MARKER = "<!-- P9_BROAD_HANDLER_INVENTORY_START -->"
END_MARKER = "<!-- P9_BROAD_HANDLER_INVENTORY_END -->"
START_BOUNDARY = START_MARKER + "\n```text\n"
END_BOUNDARY = "\n```\n" + END_MARKER
DEFAULT_INVENTORY = Path("docs/implementation/foundation-exception-inventory.md")
Row = tuple[str, str, str, str, str]


class InventoryError(RuntimeError):
    pass


def _is_broad_exception_type(exception_type: ast.expr | None) -> bool:
    if exception_type is None:
        return True
    if isinstance(exception_type, ast.Name):
        return exception_type.id in {"Exception", "BaseException"}
    if (
        isinstance(exception_type, ast.Attribute)
        and isinstance(exception_type.value, ast.Name)
        and exception_type.value.id == "builtins"
    ):
        return exception_type.attr in {"Exception", "BaseException"}
    if isinstance(exception_type, ast.Tuple):
        return any(_is_broad_exception_type(element) for element in exception_type.elts)
    return False


def _exception_form(node: ast.expr | None) -> str:
    if node is None:
        return "bare"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Tuple):
        return "(" + ",".join(_exception_form(item) for item in node.elts) + ")"
    return ast.unparse(node)


def _classification(path: str) -> str:
    parts = path.split("/")
    if "tests" in parts:
        return "TESTS"
    if parts[0] == "scripts":
        return "TOOLING_MIGRATION"
    if path == "legacy/research-backend/execute_live.py":
        return "INTENTIONAL_CONTAINMENT"
    return "PRODUCTION_CRITICAL"


def _disposition(handler: ast.ExceptHandler) -> str:
    marker_names = {
        ast.Raise: "RAISE",
        ast.Return: "RETURN",
        ast.Pass: "PASS",
        ast.Continue: "CONTINUE",
    }
    markers: list[tuple[int, int, str]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda, ast.ExceptHandler)):
            return
        for node_type, marker in marker_names.items():
            if isinstance(node, node_type):
                markers.append((node.lineno, node.col_offset, marker))
                break
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in handler.body:
        visit(statement)
    return min(markers)[2] if markers else "OTHER"


def _tracked_python_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "*.py"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise InventoryError("could not list tracked Python files")
    return sorted(path for path in result.stdout.splitlines() if path)


def inventory_rows(root: Path) -> list[Row]:
    rows: list[Row] = []
    for relative_path in _tracked_python_files(root):
        path = root / relative_path
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError) as error:
            raise InventoryError(f"could not parse tracked Python file: {relative_path}") from error
        for handler in ast.walk(tree):
            if isinstance(handler, ast.ExceptHandler) and _is_broad_exception_type(handler.type):
                rows.append((
                    relative_path,
                    str(handler.lineno),
                    _exception_form(handler.type),
                    _classification(relative_path),
                    _disposition(handler),
                ))
    return sorted(rows, key=lambda row: (row[0], int(row[1]), row[2:]))


def _inventory_bounds(text: str) -> tuple[int, int]:
    if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise InventoryError("inventory markers must occur exactly once")
    try:
        start = text.index(START_BOUNDARY) + len(START_BOUNDARY)
        end = text.index(END_BOUNDARY, start)
    except ValueError as error:
        raise InventoryError("inventory markers are malformed") from error
    if end < start:
        raise InventoryError("inventory markers are malformed")
    return start, end


def read_documented_rows(inventory_path: Path) -> list[Row]:
    try:
        text = inventory_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise InventoryError("could not read inventory document") from error
    start, end = _inventory_bounds(text)
    rows = [tuple(line.split("|")) for line in text[start:end].splitlines() if line]
    if any(len(row) != 5 for row in rows):
        raise InventoryError("inventory rows must have exactly five fields")
    typed_rows = [tuple(row) for row in rows]
    if len(typed_rows) != len(set(typed_rows)):
        raise InventoryError("inventory rows must be unique")
    return typed_rows  # type: ignore[return-value]


def check_inventory(root: Path, inventory_path: Path) -> None:
    documented = read_documented_rows(inventory_path)
    observed = inventory_rows(root)
    if documented != observed:
        raise InventoryError("inventory drift")


def write_inventory(root: Path, inventory_path: Path) -> None:
    try:
        text = inventory_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise InventoryError("could not read inventory document") from error
    start, end = _inventory_bounds(text)
    replacement = "\n".join("|".join(row) for row in inventory_rows(root))
    updated = text[:start] + replacement + text[end:]
    if updated != text:
        try:
            inventory_path.write_text(updated, encoding="utf-8")
        except OSError as error:
            raise InventoryError("could not write inventory document") from error


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="check the broad-handler inventory")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--check", action="store_true")
    operation.add_argument("--write", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--inventory", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    root = arguments.root.resolve()
    inventory = (arguments.inventory or root / DEFAULT_INVENTORY).resolve()
    try:
        inventory.relative_to(root)
    except ValueError:
        print("inventory path must be below root", file=sys.stderr)
        return 1
    try:
        if arguments.check:
            check_inventory(root, inventory)
        else:
            write_inventory(root, inventory)
    except InventoryError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
