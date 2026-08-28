from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]
FACTORY = ROOT / "engines/nautilus/runtime_v1/instrument_factory.py"
PROVENANCE = ROOT / "docs/implementation/p1-upstream-provenance.md"


def test_factory_uses_only_accepted_v1231_surfaces_without_test_provider() -> None:
    source = FACTORY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
        if node.module.startswith("nautilus_trader")
    }

    assert imports == {
        ("nautilus_trader.model.identifiers", "InstrumentId"),
        ("nautilus_trader.model.instruments", "CurrencyPair"),
        ("nautilus_trader.model.objects", "Currency"),
        ("nautilus_trader.model.objects", "Money"),
        ("nautilus_trader.model.objects", "Price"),
        ("nautilus_trader.model.objects", "Quantity"),
    }
    assert "test_kit" not in source
    assert "importlib" not in source
    assert "inspect" not in source
    assert "getattr" not in source
    assert "hasattr" not in source


def test_upstream_pattern_is_bound_to_exact_accepted_source() -> None:
    text = PROVENANCE.read_text(encoding="utf-8")

    assert "P1-UPSTREAM-001" in text
    assert "v1.231.0" in text
    assert "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317" in text
    assert "nautilus_trader/model/instruments/currency_pair.pyx" in text
    assert "nautilus_trader/model/objects.pyx" in text
    assert "TestInstrumentProvider" in text
    assert "not used" in text
