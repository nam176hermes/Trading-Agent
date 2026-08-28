from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]
LOADER = ROOT / "engines/nautilus/runtime_v1/market_data_loader.py"


def test_market_data_loader_keeps_the_fixed_native_and_input_seams() -> None:
    source = LOADER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    native_imports = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("nautilus_trader")
        for alias in node.names
    }

    assert native_imports == {
        ("nautilus_trader.model.data", "Bar"),
        ("nautilus_trader.model.data", "BarType"),
        ("nautilus_trader.model.data", "QuoteTick"),
        ("nautilus_trader.model.instruments", "CurrencyPair"),
        ("nautilus_trader.model.objects", "Price"),
        ("nautilus_trader.model.objects", "Quantity"),
    }
    assert "test_kit" not in source
    assert "importlib" not in source
    assert "inspect" not in source
    assert "open(" not in source
    assert "Path(" not in source
    assert "socket" not in source


def test_market_data_row_grammar_is_closed_and_quote_precedes_bar() -> None:
    tree = ast.parse(LOADER.read_text(encoding="utf-8"))
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"_ROW_KEYS", "_SEMANTIC_DOMAIN"}
    }

    assert assignments["_ROW_KEYS"] == {
        "ask",
        "bid",
        "close",
        "event_time",
        "high",
        "low",
        "open",
        "quote_time",
        "sequence",
        "volume",
    }
    assert assignments["_SEMANTIC_DOMAIN"] == b"nautilus-p1-market-data-semantic-v1\0"
