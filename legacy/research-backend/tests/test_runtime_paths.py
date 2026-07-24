from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import runtime_paths


RUNTIME_VARIABLES = (
    "TRADING_DATA_ROOT",
    "TRADING_REPORTS_DIR",
    "TRADING_SIGNAL_OUTPUT_DIR",
    "TRADING_MODE_FILE",
    "TRADING_KILL_SWITCH_PATH",
    "TRADING_ENV_FILE",
)
RUNTIME_DIRECTORY_PARTS = {
    ".dexter",
    "backtest_results",
    "data",
    "decisions",
    "logs",
    "memory",
    "models",
    "reports",
    "runtime",
    "signals",
    "weekly_reports",
}
RUNTIME_FILENAMES = {
    "STRATEGY.md",
    "decisions.jsonl",
    "decisions_scored.jsonl",
    "live_prices.json",
    "strategy.json",
    "trading.db",
}


def _constant_runtime_path(node: ast.AST) -> bool:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    parts = tuple(part for part in node.value.replace("\\", "/").split("/") if part)
    return bool(
        RUNTIME_DIRECTORY_PARTS.intersection(parts)
        or (parts and parts[-1] in RUNTIME_FILENAMES)
        or (parts and parts[-1].endswith((".db", ".sqlite", ".sqlite3")))
    )


def _contains_source_anchor(node: ast.AST, aliases: set[str]) -> bool:
    return any(
        isinstance(child, ast.Name)
        and (child.id == "__file__" or child.id in aliases)
        for child in ast.walk(node)
    )


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    current: ast.AST = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _path_api_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    path_constructors = {"Path", "_Path", "pathlib.Path"}
    path_joins = {"os.path.join", "posixpath.join", "ntpath.join"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "pathlib":
                    path_constructors.add(f"{local}.Path")
                if alias.name in {"os.path", "posixpath", "ntpath"}:
                    path_joins.add(f"{local}.join")
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local = alias.asname or alias.name
                if node.module == "pathlib" and alias.name == "Path":
                    path_constructors.add(local)
                if node.module == "os" and alias.name == "path":
                    path_joins.add(f"{local}.join")
                if node.module in {"posixpath", "ntpath"} and alias.name == "join":
                    path_joins.add(local)
    return path_constructors, path_joins


def _source_relative_runtime_lines(source: str) -> list[int]:
    tree = ast.parse(source)
    path_constructors, path_joins = _path_api_names(tree)
    source_aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None or not _contains_source_anchor(value, source_aliases):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in source_aliases:
                    source_aliases.add(target.id)
                    changed = True

    failures: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in path_constructors and node.args and _constant_runtime_path(node.args[0]):
                failures.add(node.lineno)
            if name in path_joins:
                has_runtime_part = any(_constant_runtime_path(arg) for arg in node.args)
                if has_runtime_part and (
                    _contains_source_anchor(node, source_aliases)
                    or (node.args and _constant_runtime_path(node.args[0]))
                ):
                    failures.add(node.lineno)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if _contains_source_anchor(node, source_aliases) and any(
                _constant_runtime_path(child) for child in ast.walk(node)
            ):
                failures.add(node.lineno)

    return sorted(failures)


def _clear_runtime_environment(monkeypatch) -> None:
    for name in RUNTIME_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_runtime_paths_default_below_external_user_data_root(monkeypatch, tmp_path) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))

    root = tmp_path / ".local" / "share" / "trading-agent"
    assert runtime_paths.data_root() == root
    assert runtime_paths.reports_dir() == root / "reports"
    assert runtime_paths.signal_output_dir() == root / "signals"
    assert runtime_paths.mode_file() == root / ".mode"
    assert runtime_paths.kill_switch_file() == root / ".kill_switch"
    assert runtime_paths.configured_env_file() is None


def test_runtime_paths_honor_only_explicit_overrides(monkeypatch, tmp_path) -> None:
    _clear_runtime_environment(monkeypatch)
    configured = {
        "TRADING_DATA_ROOT": tmp_path / "data",
        "TRADING_REPORTS_DIR": tmp_path / "reports",
        "TRADING_SIGNAL_OUTPUT_DIR": tmp_path / "signals",
        "TRADING_MODE_FILE": tmp_path / "authority" / "mode",
        "TRADING_KILL_SWITCH_PATH": tmp_path / "authority" / "kill",
        "TRADING_ENV_FILE": tmp_path / "protected" / "runtime.env",
    }
    for name, value in configured.items():
        monkeypatch.setenv(name, str(value))

    assert runtime_paths.data_root() == configured["TRADING_DATA_ROOT"]
    assert runtime_paths.reports_dir() == configured["TRADING_REPORTS_DIR"]
    assert runtime_paths.signal_output_dir() == configured["TRADING_SIGNAL_OUTPUT_DIR"]
    assert runtime_paths.mode_file() == configured["TRADING_MODE_FILE"]
    assert runtime_paths.kill_switch_file() == configured["TRADING_KILL_SWITCH_PATH"]
    assert runtime_paths.configured_env_file() == configured["TRADING_ENV_FILE"]


def test_empty_env_file_override_does_not_enable_env_discovery(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_ENV_FILE", "   ")

    assert runtime_paths.configured_env_file() is None


def test_pytest_collection_uses_an_isolated_session_runtime_root() -> None:
    configured = Path(os.environ["TRADING_DATA_ROOT"])

    assert configured.name.startswith("trading-agent-pytest-")
    assert runtime_paths.data_root() == configured


def test_standalone_integration_diagnostic_is_excluded_from_pytest() -> None:
    import conftest

    assert "test_integration.py" in conftest.collect_ignore


def test_backend_has_no_implicit_dotenv_discovery() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    for source_path in sorted(backend_root.rglob("*.py")):
        relative = source_path.relative_to(backend_root)
        if "tests" in relative.parts or any(part.startswith(".") for part in relative.parts):
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value == ".env" or node.value.endswith("/.env"):
                    failures.append(relative.as_posix())
                    break
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in {"load_dotenv", "_load_dotenv"} and not node.args:
                    failures.append(relative.as_posix())
                    break

    assert failures == [], "implicit env discovery:\n" + "\n".join(failures)


def test_backend_has_no_source_relative_top_level_runtime_paths() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    for source_path in sorted(backend_root.rglob("*.py")):
        relative = source_path.relative_to(backend_root)
        if "tests" in relative.parts or any(part.startswith(".") for part in relative.parts):
            continue
        source = source_path.read_text(encoding="utf-8")
        lines = _source_relative_runtime_lines(source)
        failures.extend(f"{relative.as_posix()}:{line}" for line in lines)

    assert failures == [], "source-relative runtime paths:\n" + "\n".join(failures)


@pytest.mark.parametrize(
    "source",
    (
        "DB = os.path.join(os.path.dirname(__file__), 'memory', 'trading.db')",
        "SCRATCHPAD = Path('.dexter/scratchpad')",
        "RESULTS = Path('backtest_results')",
        "STRATEGY = Path('strategy.json')",
        "ROOT = Path(__file__).parent\nDB = ROOT / 'runtime.sqlite3'",
        "import pathlib\nRESULTS = pathlib.Path('backtest_results')",
        "from os import path as osp\nROOT = Path(__file__).parent\nDB = osp.join(ROOT, 'trading.db')",
    ),
)
def test_runtime_path_scan_rejects_adversarial_path_syntaxes(source: str) -> None:
    assert _source_relative_runtime_lines(source)


def test_runtime_path_scan_ignores_non_path_domain_strings() -> None:
    source = "event = {'type': 'data', 'schema': 'signals', 'strategy': 'strategy.json'}"

    assert _source_relative_runtime_lines(source) == []


def test_residual_runtime_modules_use_the_isolated_data_root(tmp_path) -> None:
    root = tmp_path / "missing" / "nested" / "runtime"
    environment = os.environ.copy()
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "TRADING_DATA_ROOT": str(root),
    })
    code = """\
import json
import backtest_analyzer
import scratchpad
import strategy_optimizer
from db import repository

connection = repository.get_db()
connection.close()
print(json.dumps({
    "backtest": str(backtest_analyzer.RESULTS_DIR),
    "database": repository.DB_PATH,
    "scratchpad": str(scratchpad.SCRATCHPAD_DIR),
    "strategy_results": str(strategy_optimizer.RESULTS_DIR),
    "strategy": str(strategy_optimizer.strategy_path()),
    "strategy_markdown": str(strategy_optimizer.strategy_markdown_path()),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
    )
    paths = json.loads(result.stdout)

    assert paths == {
        "backtest": str(root / "backtest_results"),
        "database": str(root / "memory" / "trading.db"),
        "scratchpad": str(root / ".dexter" / "scratchpad"),
        "strategy_results": str(root / "backtest_results"),
        "strategy": str(root / "strategy.json"),
        "strategy_markdown": str(root / "STRATEGY.md"),
    }
    assert (root / "memory" / "trading.db").is_file()
    assert (root / ".dexter" / "scratchpad").is_dir()


def test_encrypted_secret_paths_are_external_or_explicit(monkeypatch, tmp_path) -> None:
    from exchange import secrets

    monkeypatch.setenv("TRADING_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("TRADING_KEYS_FILE", raising=False)
    monkeypatch.delenv("TRADING_MASTER_KEY", raising=False)
    monkeypatch.delenv("TRADING_MASTER_KEY_FILE", raising=False)

    assert secrets.keys_file() == tmp_path / "data" / ".keys.enc"
    with pytest.raises(RuntimeError, match="TRADING_MASTER_KEY"):
        secrets._get_master_key()

    keys = tmp_path / "protected" / "keys.enc"
    master = tmp_path / "protected" / "master-key"
    master.parent.mkdir()
    master.write_text("fixture-master", encoding="utf-8")
    monkeypatch.setenv("TRADING_KEYS_FILE", str(keys))
    monkeypatch.setenv("TRADING_MASTER_KEY_FILE", str(master))

    assert secrets.keys_file() == keys
    assert secrets._get_master_key() == "fixture-master"


def test_non_strict_runtime_directories_support_a_fresh_external_root(tmp_path) -> None:
    root = tmp_path / "missing" / "nested" / "runtime"
    environment = os.environ.copy()
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "TRADING_DATA_ROOT": str(root),
    })
    for name in (
        "TRADING_ENV_FILE",
        "TRADING_KEYS_FILE",
        "TRADING_MASTER_KEY",
        "TRADING_MASTER_KEY_FILE",
    ):
        environment.pop(name, None)

    subprocess.run(
        [sys.executable, "-c", "import main, kalshi_collector, sentiment_filter"],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert (root / "logs").is_dir()
    assert (root / "reports").is_dir()
