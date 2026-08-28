from __future__ import annotations

import ast
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parents[2]
RUNTIME = ROOT / "engines/nautilus/runtime_v1"
sys.path.insert(0, str(RUNTIME.parent))


def test_main_is_small_composition_with_no_stdout_or_ambient_cli() -> None:
    source = (RUNTIME / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert len(source.splitlines()) < 200
    assert "print(" not in source
    assert "argparse" not in source
    assert "subprocess" not in source
    assert "socket" not in source
    assert "BaseException" not in source
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "require_runtime_entry"
        for node in ast.walk(tree)
    )
    main_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    call_nodes = [
        node
        for node in ast.walk(main_function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    calls = [node.func.id for node in sorted(call_nodes, key=lambda item: item.lineno)]
    assert calls.index("require_engine_version") < calls.index("load_product_lineage")
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "importlib.metadata"
        and [alias.name for alias in node.names] == ["version"]
        for node in ast.walk(tree)
    )
    assert all(
        not (
            isinstance(node, (ast.Import, ast.ImportFrom))
            and (
                any(alias.name == "nautilus_trader" for alias in node.names)
                if isinstance(node, ast.Import)
                else node.module == "nautilus_trader"
            )
        )
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "load_product_lineage"
        for node in ast.walk(tree)
    )


def test_diagnostics_are_bounded_ascii_and_path_free(capfd: pytest.CaptureFixture[str]) -> None:
    from runtime_v1.diagnostics import diagnostic_line, emit_diagnostic

    line = diagnostic_line("E_RUNTIME_NOT_READY")
    assert line == b"P1_RUNTIME:E_RUNTIME_NOT_READY\n"
    assert len(line) <= 128
    assert line.isascii()
    assert b"/" not in line and b"\\" not in line
    emit_diagnostic("E_RUNTIME_NOT_READY")
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == line.decode()
    with pytest.raises(ValueError):
        diagnostic_line("/tmp/secret")
    with pytest.raises(ValueError):
        diagnostic_line([])  # type: ignore[arg-type]
