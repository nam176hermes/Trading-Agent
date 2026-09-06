from __future__ import annotations

import ast
from pathlib import Path


def test_evaluation_child_has_no_network_database_publication_or_replay_imports() -> None:
    paths = (
        Path("packages/alpha_lifecycle/evaluation.py"),
        Path("scripts/run_p3_evaluation_child.py"),
    )
    forbidden = {"urllib", "httpx", "requests", "socket", "psycopg", "sqlalchemy", "subprocess"}
    for path in paths:
        tree = ast.parse(path.read_text())
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
        }
        assert not (imported & forbidden), path
        assert "publication" not in path.read_text().lower()
