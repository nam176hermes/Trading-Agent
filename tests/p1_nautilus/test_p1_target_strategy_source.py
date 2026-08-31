from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path("engines/nautilus/runtime_v1/target_strategy.py")


def _tree() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def test_source_is_one_sealed_strategy_with_frozen_config_and_explicit_states() -> None:
    tree = _tree()
    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }

    assert {
        "StrategyEventCollector",
        "StrategyState",
        "TargetStrategy",
        "TargetStrategyConfig",
    } <= classes.keys()
    assert {
        member.name
        for member in classes["TargetStrategy"].body
        if isinstance(member, ast.FunctionDef)
    } >= {
        "on_bar",
        "on_order_filled",
        "on_order_rejected",
        "on_quote_tick",
        "on_start",
        "on_stop",
    }
    source = SOURCE.read_text(encoding="utf-8")
    for state in (
        "WAITING_FOR_TARGET",
        "ORDER_WORKING",
        "TARGET_REACHED",
        "EXIT_ONLY",
        "COMPLETED",
        "FAILED",
    ):
        assert state in source
    assert "class TargetStrategyConfig(StrategyConfig, frozen=True)" in source


def test_source_has_one_collector_channel_and_no_io_network_or_dynamic_imports() -> None:
    tree = _tree()
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"__import__", "eval", "exec", "open", "print"}
    ]
    collector_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record"
    ]

    assert imported.isdisjoint(
        {"asyncio", "http", "importlib", "json", "os", "pathlib", "requests", "socket", "subprocess", "sys", "urllib"}
    )
    assert calls == []
    assert collector_calls
    assert all(ast.unparse(node.func.value) == "self._collector" for node in collector_calls)


def test_source_subscribes_only_to_configured_quote_and_bar_and_uses_market_orders() -> None:
    tree = _tree()
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert {"subscribe_bars", "subscribe_quote_ticks"} <= calls
    assert {"unsubscribe_bars", "unsubscribe_quote_ticks"} <= calls
    assert "market" in calls
    assert "limit" not in calls
    assert "submit_order" in calls
