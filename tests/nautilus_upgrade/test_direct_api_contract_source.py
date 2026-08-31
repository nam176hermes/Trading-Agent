from __future__ import annotations

import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/direct-api-contract.json"
)
SOURCE_PATHS = (
    "engines/nautilus/launcher/nautilus_backtest.py",
    "engines/nautilus/launcher/nautilus_paper_compat.py",
    "engines/nautilus/launcher/target_portfolio_strategy.py",
)
EXPECTED_IMPORT_COUNT = 42
EXPECTED_INVOCATION_COUNT = 153
EXPECTED_INVOCATIONS_SHA256 = (
    "fca451e8a434486022e4b62e787aaf53cf9ad1d3ac85700df6c470521f68cf52"
)
EXPECTED_RELEASE_BULLET_COUNT = 992
EXPECTED_RELEASE_MANIFEST_SHA256 = (
    "2165c54983a1950e3054b0a164ff640a84e56c2ba187b8a0cf0de36dedbeda9a"
)
EXPECTED_RELEASE_COUNTS = {
    "1.228.0": 216,
    "1.229.0": 227,
    "1.230.0": 48,
    "1.231.0": 501,
}
EXPECTED_SOURCE_BINDING_COUNT = 72
EXPECTED_SOURCE_BINDINGS_SHA256 = (
    "49b3f5ad6c369dfc888f56ed6f50dd88f32bca13848aa4f53d80dadba608ff6a"
)
RELEASES_BLOB = "e3e774c9c3506e15036383a1994906e7775942fa"
EXACT_CACHE_ENV = "P1_U01_UPSTREAM_GIT_DIR"
ROOT_RUNTIME_DIRS = ("apps", "packages", "services", "scripts")
INVENTORY_PATH = (
    ROOT / "docs/implementation/p1-real-nautilus/upgrade/pin-inventory.json"
)
ROLLBACK_COMMIT = "280ae1762df51a492a4ce71506a40b5c8706def5"
CANDIDATE_COMMIT = "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
REQUIRED_PIN_VALUES = {
    "PIN-705B6185D7861B184F80": ROLLBACK_COMMIT,
    "PIN-426107DED6B955FE5AA6": ROLLBACK_COMMIT,
    "PIN-7D1CD22BD68E9DFEE446": CANDIDATE_COMMIT,
    "PIN-FF46916D969407BE2268": ROLLBACK_COMMIT,
}


def _canonical_digest(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _tracked_python_paths() -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--", "*.py"],
        check=True,
        capture_output=True,
    )
    return sorted(
        path.decode("utf-8")
        for path in result.stdout.split(b"\0")
        if path
    )


def _source_text(
    relative_path: str,
    source_overrides: dict[str, str] | None = None,
) -> str:
    if source_overrides is not None and relative_path in source_overrides:
        return source_overrides[relative_path]
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _nautilus_imports(
    source_overrides: dict[str, str] | None = None,
    *,
    relative_paths: tuple[str, ...] | None = None,
) -> list[dict[str, object]]:
    imports: list[dict[str, object]] = []
    paths = _tracked_python_paths() if relative_paths is None else relative_paths
    for relative_path in paths:
        tree = ast.parse(
            _source_text(relative_path, source_overrides),
            filename=relative_path,
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (
                node.module or ""
            ).startswith("nautilus_trader"):
                imports.extend(
                    {
                        "alias": alias.asname,
                        "kind": "ImportFrom",
                        "line": node.lineno,
                        "module": node.module,
                        "path": relative_path,
                        "symbol": alias.name,
                    }
                    for alias in node.names
                )
            elif isinstance(node, ast.Import):
                imports.extend(
                    {
                        "alias": alias.asname,
                        "kind": "Import",
                        "line": node.lineno,
                        "module": alias.name,
                        "path": relative_path,
                        "symbol": None,
                    }
                    for alias in node.names
                    if alias.name.startswith("nautilus_trader")
                )
    return sorted(
        imports,
        key=lambda item: (
            item["path"],
            item["line"],
            item["module"],
            item["symbol"] or "",
        ),
    )


def _defined_symbols(relative_path: str) -> set[str]:
    tree = ast.parse(
        (ROOT / relative_path).read_text(encoding="utf-8"),
        filename=relative_path,
    )
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


BACKTEST = "engines/nautilus/launcher/nautilus_backtest.py"
PAPER = "engines/nautilus/launcher/nautilus_paper_compat.py"
STRATEGY = "engines/nautilus/launcher/target_portfolio_strategy.py"
_RootMap = dict[str, tuple[tuple[str, ...], frozenset[str] | None]]
INVOCATION_ROOTS: dict[tuple[str, str], _RootMap] = {
    (BACKTEST, "_add_native_simulation_venue"): {
        "engine": (("API-BACKTEST-ENGINE",), None),
        "money_type": (("API-MONEY",), None),
        "fill_model_type": (("API-FILL-MODEL",), None),
    },
    (BACKTEST, "_build_simulation_market_data"): {
        "quantity_type": (("API-QUANTITY",), None),
        "bar_type_class": (("API-BAR",), None),
        "price_type": (("API-PRICE",), None),
        "quote_tick_type": (("API-QUOTE-TICK",), None),
    },
    (BACKTEST, "_engine_decimal"): {
        "value": (("API-MONEY", "API-PRICE", "API-QUANTITY"), None),
    },
    (BACKTEST, "_account_balance_count"): {
        "account": (("API-ACCOUNT",), None),
        "balance": (("API-ACCOUNT-BALANCE",), None),
    },
    (BACKTEST, "_run_nautilus_simulation_fixture_loaded"): {
        "BacktestEngine": (("API-BACKTEST-ENGINE",), None),
        "BacktestEngineConfig": (("API-BACKTEST-ENGINE-CONFIG",), None),
        "LoggingConfig": (("API-LOGGING-CONFIG",), None),
        "ScenarioFeeModel": (("API-FEE-MODEL",), None),
        "TestInstrumentProvider": (("API-TEST-INSTRUMENT-PROVIDER",), None),
        "instrument": (("API-INSTRUMENT",), None),
        "Venue": (("API-VENUE",), None),
        "OmsType": (("API-OMS-TYPE",), None),
        "AccountType": (("API-ACCOUNT-TYPE",), None),
        "BarType": (("API-BAR-TYPE",), None),
        "TargetPortfolioStrategy": (("API-STRATEGY",), None),
        "TargetPortfolioStrategyConfig": (("API-STRATEGY-CONFIG",), None),
        "engine": (("API-BACKTEST-ENGINE",), None),
        "engine.cache": (("API-CACHE",), None),
        "engine_result": (("API-BACKTEST-RESULT",), None),
        "strategy": (("API-STRATEGY",), None),
        "position": (("API-POSITION",), None),
        "bars[-1]": (("API-BAR",), None),
        "account": (("API-ACCOUNT",), None),
        "order": (("API-ORDER",), None),
    },
    (
        BACKTEST,
        "_run_nautilus_simulation_fixture_loaded.ScenarioFeeModel.get_commission",
    ): {
        "Money": (("API-MONEY",), None),
        "instrument": (("API-INSTRUMENT",), None),
    },
    (BACKTEST, "_run_nautilus_loaded"): {
        "instrument": (("API-INSTRUMENT",), None),
        "engine": (("API-BACKTEST-ENGINE",), None),
        "result": (("API-BACKTEST-RESULT",), None),
    },
    (PAPER, "initialize_and_dispose_paper_strategy"): {
        "configuration_type": (("API-STRATEGY-CONFIG",), None),
        "strategy_type": (("API-STRATEGY",), None),
        "strategy": (("API-STRATEGY",), None),
    },
    (STRATEGY, "_fixed_point_text"): {
        "value": (("API-PRICE", "API-QUANTITY"), None),
    },
    (STRATEGY, "TargetPortfolioStrategy.on_start"): {
        "self": (
            ("API-STRATEGY",),
            frozenset({"cache", "config", "subscribe_bars"}),
        ),
        "self.cache": (("API-CACHE",), None),
    },
    (STRATEGY, "TargetPortfolioStrategy.on_bar"): {
        "self": (
            ("API-STRATEGY",),
            frozenset({"config", "order_factory", "submit_order"}),
        ),
        "self.order_factory": (("API-ORDER-FACTORY",), None),
    },
    (STRATEGY, "TargetPortfolioStrategy.on_order_filled"): {
        "self": (
            ("API-STRATEGY",),
            frozenset({"config", "order_factory", "submit_order"}),
        ),
        "self.order_factory": (("API-ORDER-FACTORY",), None),
        "event": (("API-ORDER-FILLED",), None),
    },
}

MANUAL_INVOCATIONS = (
    (
        STRATEGY,
        "TargetPortfolioStrategy.__init__",
        53,
        ("API-STRATEGY",),
        "__init__",
        "super().__init__",
        "CALL",
    ),
    (
        STRATEGY,
        "TargetPortfolioStrategy.on_start",
        64,
        ("API-STRATEGY",),
        "on_start",
        "def on_start",
        "OVERRIDE",
    ),
    (
        STRATEGY,
        "TargetPortfolioStrategy.on_bar",
        77,
        ("API-STRATEGY",),
        "on_bar",
        "def on_bar",
        "OVERRIDE",
    ),
    (
        STRATEGY,
        "TargetPortfolioStrategy.on_order_filled",
        126,
        ("API-STRATEGY",),
        "on_order_filled",
        "def on_order_filled",
        "OVERRIDE",
    ),
    (
        STRATEGY,
        "TargetPortfolioStrategy.on_order_rejected",
        202,
        ("API-STRATEGY",),
        "on_order_rejected",
        "def on_order_rejected",
        "OVERRIDE",
    ),
    (
        BACKTEST,
        "_run_nautilus_simulation_fixture_loaded.ScenarioFeeModel",
        2183,
        ("API-FEE-MODEL",),
        "<subclass>",
        "ScenarioFeeModel(FeeModel)",
        "SUBCLASS",
    ),
    (
        BACKTEST,
        "_run_nautilus_simulation_fixture_loaded.ScenarioFeeModel.get_commission",
        2189,
        ("API-FEE-MODEL",),
        "get_commission",
        "def get_commission",
        "OVERRIDE",
    ),
)


def _scope_nodes(tree: ast.Module) -> dict[str, ast.AST]:
    result: dict[str, ast.AST] = {}

    def visit(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                qualified = f"{prefix}.{node.name}" if prefix else node.name
                result[qualified] = node
                visit(node.body, qualified)

    visit(tree.body)
    return result


def _owned_nodes(scope: ast.AST) -> list[ast.AST]:
    result: list[ast.AST] = []
    stack = list(getattr(scope, "body", []))
    while stack:
        node = stack.pop()
        result.append(node)
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return result


def _invocation_record(
    path: str,
    scope: str,
    line: int,
    surface_ids: tuple[str, ...],
    member: str,
    expression: str,
    kind: str,
) -> dict[str, object]:
    identity = f"{path}:{scope}:{line}:{expression}:{','.join(surface_ids)}"
    return {
        "expression": expression,
        "id": f"INV-{hashlib.sha256(identity.encode()).hexdigest()[:16].upper()}",
        "kind": kind,
        "line": line,
        "member": member,
        "path": path,
        "scope": scope,
        "surface_ids": sorted(surface_ids),
    }


def _scope_metadata(
    tree: ast.Module,
) -> tuple[dict[ast.AST, ast.AST], dict[ast.AST, str]]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    names: dict[ast.AST, str] = {tree: "<module>"}
    for name, node in _scope_nodes(tree).items():
        names[node] = name
    return parents, names


def _enclosing_scope(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    names: dict[ast.AST, str],
    *,
    include_self: bool = False,
) -> str:
    current = node if include_self else parents.get(node)
    while current is not None:
        if current in names:
            return names[current]
        current = parents.get(current)
    return "<module>"


def _attribute_parts(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return list(reversed(parts))


def _attribute_root_name(node: ast.AST) -> ast.Name | None:
    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current if isinstance(current, ast.Name) else None


def _is_annotation_load(
    node: ast.Name,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.arg) and parent.annotation is current:
            return True
        if (
            isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
            and parent.returns is current
        ):
            return True
        if isinstance(parent, ast.AnnAssign) and parent.annotation is current:
            return True
        current = parent
    return False


def _has_assignment_ancestor(
    node: ast.Name,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            return True
        if isinstance(
            parent,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            return False
        current = parent
    return False


def _is_call_argument_load(
    node: ast.Name,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.Call):
            return parent.func is not current
        if isinstance(
            parent,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            return False
        current = parent
    return False


def _subclass_expression(node: ast.ClassDef) -> str:
    bases = ",".join(ast.unparse(base) for base in node.bases)
    return f"{node.name}({bases})"


def _discover_import_alias_invocations(
    surfaces: list[dict[str, object]],
    source_overrides: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    surface_ids_by_import: dict[tuple[str, str], tuple[str, ...]] = {}
    qualified_surfaces: dict[str, tuple[str, ...]] = {}
    for surface in surfaces:
        module = str(surface["import_module"])
        symbol = str(surface["import_symbol"])
        key = (module, symbol)
        surface_ids_by_import[key] = tuple(
            sorted((*surface_ids_by_import.get(key, ()), str(surface["id"])))
        )
        qualified_surfaces[f"{module}.{symbol}"] = surface_ids_by_import[key]

    rows: list[dict[str, object]] = []
    import_paths = sorted(
        {
            item["path"]
            for item in _nautilus_imports(
                source_overrides, relative_paths=SOURCE_PATHS
            )
        }
    )
    for path_value in import_paths:
        path = str(path_value)
        tree = ast.parse(
            _source_text(path, source_overrides),
            filename=path,
        )
        parents, scope_names = _scope_metadata(tree)
        call_functions = {
            id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        classified_alias_loads: set[int] = set()
        bindings: list[dict[str, object]] = []
        for node in ast.walk(tree):
            owner = _enclosing_scope(node, parents, scope_names)
            if isinstance(node, ast.ImportFrom) and (
                node.module or ""
            ).startswith("nautilus_trader"):
                for alias in node.names:
                    surface_ids = surface_ids_by_import.get(
                        (node.module or "", alias.name), ()
                    )
                    assert surface_ids, (
                        f"unresolved Nautilus import surface: {path}:"
                        f"{node.lineno}:{node.module}.{alias.name}"
                    )
                    bindings.append(
                        {
                            "alias": alias.asname or alias.name,
                            "kind": "ImportFrom",
                            "module": node.module,
                            "owner": owner,
                            "surface_ids": surface_ids,
                        }
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if not alias.name.startswith("nautilus_trader"):
                        continue
                    bindings.append(
                        {
                            "alias": alias.asname or alias.name.split(".")[0],
                            "asname": alias.asname,
                            "kind": "Import",
                            "module": alias.name,
                            "owner": owner,
                        }
                    )

        def binding_visible(binding: dict[str, object], scope: str) -> bool:
            owner = str(binding["owner"])
            return owner == "<module>" or scope == owner or scope.startswith(owner + ".")

        for node in ast.walk(tree):
            record_scope = _enclosing_scope(
                node,
                parents,
                scope_names,
                include_self=isinstance(node, ast.ClassDef),
            )
            for binding in bindings:
                if not binding_visible(binding, record_scope):
                    continue
                alias_name = str(binding["alias"])
                if binding["kind"] == "ImportFrom":
                    surface_ids = tuple(binding["surface_ids"])
                    if (
                        isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == alias_name
                    ):
                        classified_alias_loads.add(id(node.value))
                        rows.append(
                            _invocation_record(
                                path,
                                record_scope,
                                node.lineno,
                                surface_ids,
                                node.attr,
                                ast.unparse(node),
                                "CALL" if id(node) in call_functions else "READ",
                            )
                        )
                    elif (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == alias_name
                    ):
                        classified_alias_loads.add(id(node.func))
                        rows.append(
                            _invocation_record(
                                path,
                                record_scope,
                                node.lineno,
                                surface_ids,
                                "__init__",
                                node.func.id,
                                "CONSTRUCT",
                            )
                        )
                    elif isinstance(node, ast.ClassDef) and any(
                        isinstance(base, ast.Name) and base.id == alias_name
                        for base in node.bases
                    ):
                        classified_alias_loads.update(
                            id(base)
                            for base in node.bases
                            if isinstance(base, ast.Name)
                            and base.id == alias_name
                        )
                        rows.append(
                            _invocation_record(
                                path,
                                record_scope,
                                node.lineno,
                                surface_ids,
                                "<subclass>",
                                _subclass_expression(node),
                                "SUBCLASS",
                            )
                        )
                    continue

                candidate: ast.AST | None = None
                is_subclass = False
                if isinstance(node, ast.Attribute):
                    candidate = node
                elif isinstance(node, ast.ClassDef):
                    candidates = [
                        base
                        for base in node.bases
                        if isinstance(base, ast.Attribute)
                    ]
                    candidate = candidates[0] if len(candidates) == 1 else None
                    is_subclass = candidate is not None
                if candidate is None:
                    continue
                parts = _attribute_parts(candidate)
                if parts is None or parts[0] != alias_name:
                    continue
                module = str(binding["module"])
                qualified = ".".join(parts) if binding.get("asname") is None else ".".join(
                    [module, *parts[1:]]
                )
                for surface_name, surface_ids in qualified_surfaces.items():
                    if qualified == surface_name and (
                        is_subclass or id(candidate) in call_functions
                    ):
                        root_name = _attribute_root_name(candidate)
                        assert root_name is not None
                        classified_alias_loads.add(id(root_name))
                        rows.append(
                            _invocation_record(
                                path,
                                record_scope,
                                node.lineno,
                                surface_ids,
                                "<subclass>" if is_subclass else "__init__",
                                (
                                    _subclass_expression(node)
                                    if is_subclass
                                    else ast.unparse(candidate)
                                ),
                                "SUBCLASS" if is_subclass else "CONSTRUCT",
                            )
                        )
                    elif qualified.startswith(surface_name + ".") and qualified.count(
                        "."
                    ) == surface_name.count(".") + 1:
                        root_name = _attribute_root_name(candidate)
                        assert root_name is not None
                        classified_alias_loads.add(id(root_name))
                        rows.append(
                            _invocation_record(
                                path,
                                record_scope,
                                node.lineno,
                                surface_ids,
                                qualified.rsplit(".", 1)[1],
                                ast.unparse(candidate),
                                "CALL" if id(candidate) in call_functions else "READ",
                            )
                        )
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Name)
                or not isinstance(node.ctx, ast.Load)
                or id(node) in classified_alias_loads
                or _is_annotation_load(node, parents)
            ):
                continue
            record_scope = _enclosing_scope(node, parents, scope_names)
            for binding in bindings:
                if (
                    node.id != binding["alias"]
                    or not binding_visible(binding, record_scope)
                ):
                    continue
                if binding["kind"] == "ImportFrom":
                    surface_ids = tuple(binding["surface_ids"])
                else:
                    module = str(binding["module"])
                    surface_ids = tuple(
                        sorted(
                            {
                                surface_id
                                for qualified, ids in qualified_surfaces.items()
                                if qualified.startswith(module + ".")
                                for surface_id in ids
                            }
                        )
                    )
                is_classified_argument = (
                    not _has_assignment_ancestor(node, parents)
                    and _is_call_argument_load(node, parents)
                )
                rows.append(
                    _invocation_record(
                        path,
                        record_scope,
                        node.lineno,
                        surface_ids,
                        "<alias-value>" if is_classified_argument else "<alias-read>",
                        node.id,
                        (
                            "ALIAS_ARGUMENT"
                            if is_classified_argument
                            else "UNCLASSIFIED_ALIAS_READ"
                        ),
                    )
                )
    unique = {item["id"]: item for item in rows}
    return sorted(unique.values(), key=lambda item: item["id"])


def _discover_local_invocations(
    surfaces: list[dict[str, object]],
    source_overrides: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    parsed = {
        path: ast.parse(
            _source_text(path, source_overrides), filename=path
        )
        for path in {key[0] for key in INVOCATION_ROOTS}
    }
    scopes = {path: _scope_nodes(tree) for path, tree in parsed.items()}
    rows = _discover_import_alias_invocations(surfaces, source_overrides)
    for (path, scope_name), prefixes in INVOCATION_ROOTS.items():
        nodes = _owned_nodes(scopes[path][scope_name])
        call_attributes = {
            id(node.func)
            for node in nodes
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        for node in nodes:
            if isinstance(node, ast.Attribute):
                expression = ast.unparse(node)
                for prefix, (surface_ids, allowed) in prefixes.items():
                    is_direct_member = (
                        expression.startswith(prefix + ".")
                        and expression.count(".") == prefix.count(".") + 1
                    )
                    if not is_direct_member:
                        continue
                    if (
                        allowed is not None
                        and node.attr not in allowed
                        and not (
                            prefix == "self" and not node.attr.startswith("_")
                        )
                    ):
                        continue
                    rows.append(
                        _invocation_record(
                            path,
                            scope_name,
                            node.lineno,
                            surface_ids,
                            node.attr,
                            expression,
                            "CALL" if id(node) in call_attributes else "READ",
                        )
                    )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in prefixes
            ):
                surface_ids, allowed = prefixes[node.func.id]
                if allowed is None:
                    rows.append(
                        _invocation_record(
                            path,
                            scope_name,
                            node.lineno,
                            surface_ids,
                            "__init__",
                            node.func.id,
                            "CONSTRUCT",
                        )
                    )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                owner = ast.unparse(node.args[0])
                if owner not in prefixes:
                    continue
                surface_ids, allowed = prefixes[owner]
                member = node.args[1].value
                if allowed is None or member in allowed:
                    rows.append(
                        _invocation_record(
                            path,
                            scope_name,
                            node.lineno,
                            surface_ids,
                            member,
                            ast.unparse(node),
                            "DYNAMIC_MEMBER",
                        )
                    )
    rows.extend(_invocation_record(*item) for item in MANUAL_INVOCATIONS)
    unique = {item["id"]: item for item in rows}
    return sorted(unique.values(), key=lambda item: item["id"])


def _assert_invocation_contract(
    contract: dict[str, object],
    source_overrides: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    discovered = _discover_local_invocations(
        contract["api_surfaces"],  # type: ignore[arg-type]
        source_overrides,
    )
    assert contract["local_invocations"] == discovered
    assert _canonical_digest(discovered) == EXPECTED_INVOCATIONS_SHA256
    return discovered


def _assert_manual_invocations_resolve() -> None:
    parsed = {
        path: ast.parse(
            (ROOT / path).read_text(encoding="utf-8"), filename=path
        )
        for path in {item[0] for item in MANUAL_INVOCATIONS}
    }
    scopes = {path: _scope_nodes(tree) for path, tree in parsed.items()}
    for path, scope_name, line, _, member, expression, kind in MANUAL_INVOCATIONS:
        scope = scopes[path][scope_name]
        if kind == "SUBCLASS":
            assert isinstance(scope, ast.ClassDef)
            assert scope.lineno == line
            assert expression == (
                f"{scope.name}({','.join(ast.unparse(base) for base in scope.bases)})"
            )
        elif kind == "OVERRIDE":
            assert isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
            assert scope.lineno == line
            assert scope.name == member
            assert expression == f"def {scope.name}"
        else:
            matches = [
                node
                for node in ast.walk(scope)
                if isinstance(node, ast.Call)
                and node.lineno == line
                and ast.unparse(node.func) == expression
            ]
            assert len(matches) == 1


def _offline_git_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"LANG", "LC_ALL", "PATH", "TEMP", "TMP", "TMPDIR"}
    }
    env.update(
        {
            "GIT_ASKPASS": "/bin/false",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "SSH_ASKPASS": "/bin/false",
        }
    )
    return env


def _git_cache_run(
    git_dir: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.allow=never",
            f"--git-dir={git_dir}",
            *args,
        ],
        check=check,
        capture_output=True,
        env=_offline_git_env(),
        timeout=10,
    )


def _git_cache_output(git_dir: Path, *args: str) -> bytes:
    return _git_cache_run(git_dir, *args).stdout


def _assert_offline_complete_cache(git_dir: Path) -> None:
    config_result = _git_cache_run(
        git_dir,
        "config",
        "--local",
        "--null",
        "--list",
    )
    config_keys = {
        entry.split(b"\n", maxsplit=1)[0]
        .decode("utf-8")
        .strip()
        .casefold()
        for entry in config_result.stdout.split(b"\0")
        if entry
    }
    include_keys = sorted(
        key
        for key in config_keys
        if key == "include"
        or key.startswith("include.")
        or key == "includeif"
        or key.startswith("includeif.")
    )
    assert not include_keys, (
        "local Git include/includeIf directives are forbidden before object reads: "
        f"config_keys={include_keys}"
    )
    unsafe_keys = sorted(
        key
        for key in config_keys
        if key == "extensions.partialclone"
        or (key.startswith("remote.") and key.endswith(".promisor"))
        or (key.startswith("remote.") and key.endswith(".partialclonefilter"))
    )
    promisor_markers = sorted((git_dir / "objects" / "pack").glob("*.promisor"))
    assert not unsafe_keys and not promisor_markers, (
        "partial-clone/promisor exact-OID cache is forbidden: "
        f"config_keys={unsafe_keys} "
        f"promisor_markers={[path.name for path in promisor_markers]}"
    )


def _release_bullet_source_manifest(raw: str) -> list[dict[str, object]]:
    releases = set(EXPECTED_RELEASE_COUNTS)
    release: str | None = None
    section: str | None = None
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if line.startswith("# NautilusTrader 1."):
            current = line.split()[2]
            if current == "1.227.0":
                break
            release = current if current in releases else None
            section = None
            continue
        if release is None:
            continue
        if line.startswith("### "):
            section = line[4:]
        elif line.startswith("- "):
            assert section is not None
            rows.append(
                {
                    "release": release,
                    "section": section,
                    "source_line": line_number,
                    "text_sha256": hashlib.sha256(
                        line[2:].encode("utf-8")
                    ).hexdigest(),
                }
            )
    return sorted(rows, key=lambda item: (item["release"], item["source_line"]))


def _kernel_state_defaults(raw: str) -> dict[str, object]:
    tree = ast.parse(raw)
    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    kernel = classes["NautilusKernelConfig"]
    defaults: dict[str, object] = {}
    for node in kernel.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in {"load_state", "save_state"}
            and isinstance(node.value, ast.Constant)
        ):
            defaults[node.target.id] = node.value.value
    return defaults


def _root_runtime_nautilus_imports() -> list[str]:
    import_sites: list[str] = []
    for directory in ROOT_RUNTIME_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (
                    node.module or ""
                ).startswith("nautilus_trader"):
                    import_sites.append(str(path.relative_to(ROOT)))
                if isinstance(node, ast.Import) and any(
                    alias.name.startswith("nautilus_trader")
                    for alias in node.names
                ):
                    import_sites.append(str(path.relative_to(ROOT)))
    return import_sites


def test_direct_api_contract_covers_existing_source_imports() -> None:
    assert CONTRACT_PATH.is_file(), "missing generated direct API contract"
    raw = CONTRACT_PATH.read_bytes()
    contract = json.loads(raw)
    assert raw == (
        json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=True)
        + "\n"
    ).encode("ascii")
    assert set(contract) == {
        "api_surfaces",
        "authority",
        "classifications",
        "local_imports",
        "local_invocations",
        "local_sources",
        "release_delta",
        "release_manifest",
        "schema_version",
        "source_binding_receipt",
    }
    assert contract["schema_version"] == "p1-nautilus-direct-api-contract/v2"
    discovered_imports = _nautilus_imports(relative_paths=SOURCE_PATHS)
    assert len(discovered_imports) == EXPECTED_IMPORT_COUNT
    assert contract["local_imports"] == discovered_imports
    assert contract["local_sources"] == sorted(
        {item["path"] for item in discovered_imports}
    )
    assert contract["local_sources"] == list(SOURCE_PATHS)

    classifications = contract["classifications"]
    assert classifications == {
        "impact": [
            "ADAPT",
            "BEHAVIOR_REQUALIFY",
            "BLOCK",
            "NOT_USED",
            "UNCHANGED_API",
        ],
        "local_disposition": ["EXTRACT", "REUSE", "RETIRE", "WRAP"],
        "release_item": [
            "ADAPTER_ONLY",
            "BUILD_ONLY",
            "DIRECT",
            "INDIRECT",
            "NOT_RELEVANT",
            "V2_ONLY",
        ],
        "scenario_destination": ["P1-U05", "P1-U06", "P1-U07"],
        "usage": ["BOTH", "CURRENT", "PLANNED"],
    }

    authority = contract["authority"]
    assert set(authority) == {
        "candidate",
        "rollback",
        "root_python_imports_nautilus",
        "runtime_family",
        "u00r_inventory",
        "upstream_repository",
    }
    assert authority["upstream_repository"] == (
        "https://github.com/nautechsystems/nautilus_trader.git"
    )
    assert authority["candidate"] == {
        "commit": CANDIDATE_COMMIT,
        "status": "CANDIDATE_CONTEXT_ONLY",
        "tag": "v1.231.0",
        "tree_oid": "997ffa7b641bfc0563a52e6190b548003316495d",
        "version": "1.231.0",
    }
    assert authority["rollback"] == {
        "commit": ROLLBACK_COMMIT,
        "status": "ROLLBACK_AUTHORITY",
        "tag": "v1.227.0",
        "tree_oid": "8dbb8eed0c875310ffb8f576cb07d7cd40da1556",
        "version": "1.227.0",
    }
    assert authority["root_python_imports_nautilus"] is False
    assert authority["runtime_family"] == "cython-v1"
    assert _root_runtime_nautilus_imports() == []

    inventory_raw = INVENTORY_PATH.read_bytes()
    inventory = json.loads(inventory_raw)
    inventory_authority = authority["u00r_inventory"]
    assert hashlib.sha256(inventory_raw).hexdigest() == inventory_authority["sha256"]
    assert inventory_authority == {
        "blob_oid": "fe91b838f0795784a5843cbdcf802326be644152",
        "path": "docs/implementation/p1-real-nautilus/upgrade/pin-inventory.json",
        "pin_ids": sorted(REQUIRED_PIN_VALUES),
        "schema": "nautilus-pin-inventory/v4",
        "sha256": "b9960c1153bfb89b10a0f3783d0afa5eb67af3fea2f805d8d37702b41bd4a0f9",
        "source_tree_oid": "2e83bc2e811adf5d51a2a67a448d3efbadae3982",
        "threat_model": "U00R_TRUSTED_HOST_COOPERATIVE_GIT_V1",
    }
    assert inventory["schema"] == inventory_authority["schema"]
    assert inventory["source_tree_oid"] == inventory_authority["source_tree_oid"]
    assert inventory["threat_model"] == inventory_authority["threat_model"]
    pins = {
        entry["id"]: entry["value"]
        for entry in inventory["entries"]
        if entry["id"] in REQUIRED_PIN_VALUES
    }
    assert pins == REQUIRED_PIN_VALUES

    surfaces = contract["api_surfaces"]
    assert isinstance(surfaces, list) and len(surfaces) == 33
    assert [surface["id"] for surface in surfaces] == sorted(
        surface["id"] for surface in surfaces
    )
    assert len({surface["id"] for surface in surfaces}) == len(surfaces)
    allowed_impacts = set(classifications["impact"])
    allowed_usage = set(classifications["usage"])
    allowed_dispositions = set(classifications["local_disposition"])
    for surface in surfaces:
        assert set(surface) == {
            "id",
            "impact",
            "import_module",
            "import_symbol",
            "local_divergence",
            "local_sites",
            "required_members",
            "scenario_destinations",
            "source_evidence",
            "usage",
        }
        assert surface["impact"] in allowed_impacts
        assert surface["usage"] in allowed_usage
        assert surface["local_divergence"]
        assert surface["local_sites"]
        assert all(
            set(site) == {"disposition", "path", "symbols"}
            and isinstance(site["symbols"], list)
            and all(isinstance(symbol, str) and symbol for symbol in site["symbols"])
            and site["disposition"] in allowed_dispositions
            for site in surface["local_sites"]
        )
        assert all(
            (ROOT / site["path"]).is_file() and site["symbols"]
            for site in surface["local_sites"]
        )
        for site in surface["local_sites"]:
            definitions = _defined_symbols(site["path"])
            assert set(site["symbols"]) <= definitions
        commits = {
            source["commit"] for source in surface["source_evidence"]
        }
        assert commits == {ROLLBACK_COMMIT, CANDIDATE_COMMIT}
        assert all(
            set(source) == {"blob_oid", "commit", "path", "symbol"}
            and len(source["blob_oid"]) == 40
            and set(source["blob_oid"]) <= set("0123456789abcdef")
            and source["blob_oid"] != "0" * 40
            for source in surface["source_evidence"]
        )
        if surface["impact"] in {"ADAPT", "BEHAVIOR_REQUALIFY"}:
            assert surface["scenario_destinations"]
        assert all(
            destination.split("::", maxsplit=1)[0]
            in classifications["scenario_destination"]
            for destination in surface["scenario_destinations"]
        )

    config_surface = next(
        surface
        for surface in surfaces
        if surface["id"] == "API-BACKTEST-ENGINE-CONFIG"
    )
    assert config_surface["local_divergence"] == (
        "Both endpoint defaults inherit load_state=False and save_state=False. "
        "Current constructors omit both; U05 extraction must set and probe "
        "both explicitly together with run_analysis=False and logging isolation."
    )
    backtest_scopes = _scope_nodes(
        ast.parse((ROOT / BACKTEST).read_text(encoding="utf-8"))
    )
    for scope_name in (
        "_run_nautilus_loaded",
        "_run_nautilus_simulation_fixture_loaded",
    ):
        config_calls = [
            node
            for node in ast.walk(backtest_scopes[scope_name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "BacktestEngineConfig"
        ]
        assert len(config_calls) == 1
        keywords = {keyword.arg for keyword in config_calls[0].keywords}
        assert "run_analysis" in keywords
        assert "load_state" not in keywords
        assert "save_state" not in keywords

    imports = {
        (surface["import_module"], surface["import_symbol"])
        for surface in surfaces
        if surface["usage"] in {"CURRENT", "BOTH"}
    }
    direct_imports = {
        (item["module"], item["symbol"])
        for item in discovered_imports
        if item["kind"] == "ImportFrom"
    }
    assert direct_imports <= imports
    qualified_imports = {
        (surface["import_module"], surface["import_symbol"])
        for surface in surfaces
    }
    product_imports = _nautilus_imports()
    assert {item["kind"] for item in product_imports} == {"ImportFrom"}
    assert {
        (item["module"], item["symbol"]) for item in product_imports
    } <= qualified_imports

    local_invocations = contract["local_invocations"]
    discovered_invocations = _assert_invocation_contract(contract)
    _assert_manual_invocations_resolve()
    assert len(local_invocations) == EXPECTED_INVOCATION_COUNT
    assert [item["id"] for item in local_invocations] == sorted(
        item["id"] for item in local_invocations
    )
    assert len({item["id"] for item in local_invocations}) == len(
        local_invocations
    )
    surfaces_by_id = {surface["id"]: surface for surface in surfaces}
    assert {
        item["path"] for item in local_invocations
    } == set(contract["local_sources"])
    for item in local_invocations:
        assert set(item) == {
            "expression",
            "id",
            "kind",
            "line",
            "member",
            "path",
            "scope",
            "surface_ids",
        }
        assert item["kind"] in {
            "ALIAS_ARGUMENT",
            "CALL",
            "CONSTRUCT",
            "DYNAMIC_MEMBER",
            "OVERRIDE",
            "READ",
            "SUBCLASS",
        }
        assert item["surface_ids"] == sorted(item["surface_ids"])
        for surface_id in item["surface_ids"]:
            surface = surfaces_by_id[surface_id]
            assert {
                source["commit"] for source in surface["source_evidence"]
            } == {ROLLBACK_COMMIT, CANDIDATE_COMMIT}
            assert surface["scenario_destinations"]
            if item["member"] not in {
                "<alias-value>",
                "<subclass>",
                "__init__",
            }:
                assert item["member"] in surface["required_members"]

    release_delta = contract["release_delta"]
    assert len(release_delta) == 40
    assert [item["id"] for item in release_delta] == sorted(
        item["id"] for item in release_delta
    )
    assert len({item["id"] for item in release_delta}) == len(release_delta)
    assert {item["release"] for item in release_delta} == {
        "1.228.0",
        "1.229.0",
        "1.230.0",
        "1.231.0",
    }
    for item in release_delta:
        assert set(item) == {
            "classification",
            "id",
            "impact",
            "local_disposition",
            "release",
            "scenario_destinations",
            "source_evidence",
            "upstream_change",
        }
        assert item["classification"] in classifications["release_item"]
        assert item["impact"] in allowed_impacts
        assert item["local_disposition"] in allowed_dispositions
        assert item["source_evidence"]
        source_commits = {
            source["commit"] for source in item["source_evidence"]
        }
        assert CANDIDATE_COMMIT in source_commits
        assert source_commits <= {ROLLBACK_COMMIT, CANDIDATE_COMMIT}
        assert all(
            len(source["blob_oid"]) == 40
            and set(source["blob_oid"]) <= set("0123456789abcdef")
            and source["blob_oid"] != "0" * 40
            and set(source) == {"blob_oid", "commit", "path", "symbol"}
            for source in item["source_evidence"]
        )
        if item["impact"] == "BEHAVIOR_REQUALIFY":
            assert item["scenario_destinations"]
        assert all(
            destination.split("::", maxsplit=1)[0]
            in classifications["scenario_destination"]
            for destination in item["scenario_destinations"]
        )

    manifest = contract["release_manifest"]
    assert set(manifest) == {
        "aggregate_sha256",
        "bullet_count",
        "counts_by_release",
        "counts_by_section",
        "entries",
        "parser",
        "release_ranges",
        "source",
    }
    assert manifest["source"] == {
        "blob_oid": RELEASES_BLOB,
        "commit": CANDIDATE_COMMIT,
        "path": "RELEASES.md",
    }
    assert manifest["parser"] == {
        "bullet_prefix": "- ",
        "section_prefix": "### ",
        "version_heading_prefix": "# NautilusTrader ",
    }
    assert manifest["release_ranges"] == {
        "1.228.0": {"end_line": 1118, "heading_line": 882},
        "1.229.0": {"end_line": 881, "heading_line": 635},
        "1.230.0": {"end_line": 634, "heading_line": 569},
        "1.231.0": {"end_line": 568, "heading_line": 1},
    }
    entries = manifest["entries"]
    assert manifest["bullet_count"] == EXPECTED_RELEASE_BULLET_COUNT
    assert len(entries) == EXPECTED_RELEASE_BULLET_COUNT
    assert manifest["counts_by_release"] == EXPECTED_RELEASE_COUNTS
    assert Counter(item["release"] for item in entries) == Counter(
        EXPECTED_RELEASE_COUNTS
    )
    section_counts = {
        f"{release}::{section}": count
        for (release, section), count in sorted(
            Counter(
                (item["release"], item["section"]) for item in entries
            ).items()
        )
    }
    assert manifest["counts_by_section"] == section_counts
    assert manifest["aggregate_sha256"] == EXPECTED_RELEASE_MANIFEST_SHA256
    assert _canonical_digest(entries) == EXPECTED_RELEASE_MANIFEST_SHA256
    assert len({item["id"] for item in entries}) == len(entries)
    assert len(
        {(item["release"], item["source_line"]) for item in entries}
    ) == len(entries)
    release_delta_ids = {item["id"] for item in release_delta}
    allowed_boundaries = {
        "CYTHON_V1_ACCOUNTING",
        "CYTHON_V1_ACCOUNTING_DEPENDENCY",
        "CYTHON_V1_ACCOUNT_RESET",
        "CYTHON_V1_BACKTEST_BEHAVIOR",
        "CYTHON_V1_BACKTEST_LIQUIDATION",
        "CYTHON_V1_CACHE_RESET",
        "CYTHON_V1_CACHE_ORDER_IDENTITY",
        "CYTHON_V1_CACHE_ORDER_INDEX",
        "CYTHON_V1_CALLBACK_ACCOUNTING",
        "CYTHON_V1_CALLBACK_ORDER",
        "CYTHON_V1_CVEC_CLOCK_FFI",
        "CYTHON_V1_DOCUMENTATION_CORRECTION",
        "CYTHON_V1_FX_SESSION_TIME",
        "CYTHON_V1_NETTING_RECONCILIATION",
        "CYTHON_V1_PLANNED_CURRENCY_PAIR",
        "ISOLATED_BUILD_TOOLCHAIN",
        "P1_NETTING_NOT_HEDGING",
        "P1_L1_QUOTE_AND_BAR_NO_ORDER_BOOK_DATA",
        "P1_NO_BROKER_ROUTING",
        "P1_SINGLE_INSTRUMENT_ONLY",
        "P1_SPOT_NO_EXPIRING_OPTIONS",
        "P1_UNUSED_DEFI_POOL_PIPELINE",
        "P1_UNUSED_SURFACE",
        "RUST_PYO3_V2",
        "VENUE_ADAPTER_OR_EXTERNAL_NETWORK",
    }
    for entry in entries:
        assert set(entry) == {
            "boundary",
            "classification",
            "id",
            "impact",
            "local_disposition",
            "release",
            "release_delta_id",
            "scenario_destinations",
            "section",
            "source_line",
            "text_sha256",
        }
        assert entry["boundary"] in allowed_boundaries
        assert entry["classification"] in classifications["release_item"]
        assert entry["impact"] in allowed_impacts
        assert entry["local_disposition"] in allowed_dispositions
        assert len(entry["text_sha256"]) == 64
        assert set(entry["text_sha256"]) <= set("0123456789abcdef")
        if entry["release_delta_id"] is not None:
            assert entry["release_delta_id"] in release_delta_ids
        if entry["classification"] in {"DIRECT", "INDIRECT"}:
            assert entry["scenario_destinations"]
        assert all(
            destination.split("::", maxsplit=1)[0]
            in classifications["scenario_destination"]
            for destination in entry["scenario_destinations"]
        )

    receipt = contract["source_binding_receipt"]
    assert set(receipt) == {
        "algorithm",
        "binding_count",
        "bindings",
        "bindings_sha256",
        "external_cache_policy",
    }
    assert receipt["algorithm"] == "sha256-canonical-json-v1"
    assert receipt["external_cache_policy"] == (
        "DEFERRED_IF_UNSET_FAIL_IF_SUPPLIED_INVALID"
    )
    assert receipt["binding_count"] == EXPECTED_SOURCE_BINDING_COUNT
    assert len(receipt["bindings"]) == EXPECTED_SOURCE_BINDING_COUNT
    assert receipt["bindings_sha256"] == EXPECTED_SOURCE_BINDINGS_SHA256
    assert (
        _canonical_digest(receipt["bindings"])
        == EXPECTED_SOURCE_BINDINGS_SHA256
    )
    assert receipt["bindings"] == sorted(
        receipt["bindings"], key=lambda item: (item["commit"], item["path"])
    )
    assert len(
        {(item["commit"], item["path"]) for item in receipt["bindings"]}
    ) == len(receipt["bindings"])
    for binding in receipt["bindings"]:
        assert set(binding) == {"blob_oid", "commit", "path"}
        assert binding["commit"] in {ROLLBACK_COMMIT, CANDIDATE_COMMIT}
        assert len(binding["blob_oid"]) == 40
        assert set(binding["blob_oid"]) <= set("0123456789abcdef")
        assert binding["blob_oid"] != "0" * 40
    recorded_evidence = [
        source
        for surface in surfaces
        for source in surface["source_evidence"]
    ] + [
        source
        for item in release_delta
        for source in item["source_evidence"]
    ]
    recorded_evidence.append(manifest["source"])
    evidence_bindings: dict[tuple[str, str], dict[str, str]] = {}
    for source in recorded_evidence:
        key = (source["commit"], source["path"])
        normalized = {
            "blob_oid": source["blob_oid"],
            "commit": source["commit"],
            "path": source["path"],
        }
        assert key not in evidence_bindings or evidence_bindings[key] == normalized
        evidence_bindings[key] = normalized
    assert receipt["bindings"] == sorted(
        evidence_bindings.values(),
        key=lambda item: (item["commit"], item["path"]),
    )

    serialized = json.dumps(contract, sort_keys=True)
    assert "UNKNOWN" not in serialized
    assert '"impact": "BLOCK"' not in serialized


def test_source_bindings_against_explicit_exact_oid_cache() -> None:
    contract = json.loads(CONTRACT_PATH.read_bytes())
    configured = os.environ.get(EXACT_CACHE_ENV)
    if configured is None:
        print(
            "P1_U01_SOURCE_BINDINGS=DEFERRED "
            f"reason={EXACT_CACHE_ENV}-not-supplied"
        )
        return

    git_dir = Path(configured)
    assert git_dir.is_dir(), f"supplied exact-OID cache is not a directory: {git_dir}"
    _assert_offline_complete_cache(git_dir)
    assert (
        _git_cache_output(git_dir, "rev-parse", "--is-bare-repository")
        .decode("ascii")
        .strip()
        == "true"
    )
    for commit in (ROLLBACK_COMMIT, CANDIDATE_COMMIT):
        resolved = (
            _git_cache_output(git_dir, "rev-parse", f"{commit}^{{commit}}")
            .decode("ascii")
            .strip()
        )
        assert resolved == commit

    receipt = contract["source_binding_receipt"]
    assert receipt["bindings_sha256"] == EXPECTED_SOURCE_BINDINGS_SHA256
    for binding in receipt["bindings"]:
        resolved_blob = (
            _git_cache_output(
                git_dir,
                "rev-parse",
                f"{binding['commit']}:{binding['path']}",
            )
            .decode("ascii")
            .strip()
        )
        assert resolved_blob == binding["blob_oid"]

    releases_raw = _git_cache_output(
        git_dir, "show", f"{CANDIDATE_COMMIT}:RELEASES.md"
    ).decode("utf-8")
    source_rows = _release_bullet_source_manifest(releases_raw)
    manifest_rows = [
        {
            "release": item["release"],
            "section": item["section"],
            "source_line": item["source_line"],
            "text_sha256": item["text_sha256"],
        }
        for item in contract["release_manifest"]["entries"]
    ]
    assert source_rows == manifest_rows
    assert len(source_rows) == EXPECTED_RELEASE_BULLET_COUNT
    for commit in (ROLLBACK_COMMIT, CANDIDATE_COMMIT):
        config_raw = _git_cache_output(
            git_dir,
            "show",
            f"{commit}:nautilus_trader/system/config.py",
        ).decode("utf-8")
        assert _kernel_state_defaults(config_raw) == {
            "load_state": False,
            "save_state": False,
        }
    clock_sources = {
        commit: _git_cache_output(
            git_dir,
            "show",
            f"{commit}:crates/common/src/ffi/clock.rs",
        ).decode("utf-8")
        for commit in (ROLLBACK_COMMIT, CANDIDATE_COMMIT)
    }
    cvec_sources = {
        commit: _git_cache_output(
            git_dir,
            "show",
            f"{commit}:crates/core/src/ffi/cvec.rs",
        ).decode("utf-8")
        for commit in (ROLLBACK_COMMIT, CANDIDATE_COMMIT)
    }
    assert 'pub extern "C" fn vec_time_event_handlers_drop(v: CVec)' in (
        clock_sources[ROLLBACK_COMMIT]
    )
    assert "let CVec { ptr, len, cap } = v;" in clock_sources[ROLLBACK_COMMIT]
    assert "Vec::from_raw_parts" in clock_sources[ROLLBACK_COMMIT]
    assert 'pub unsafe extern "C" fn vec_time_event_handlers_drop(v: CVec)' in (
        clock_sources[CANDIDATE_COMMIT]
    )
    assert "v.into_vec::<TimeEventHandler_API>()" in clock_sources[CANDIDATE_COMMIT]
    assert "#[derive(Clone, Copy, Debug)]" in cvec_sources[ROLLBACK_COMMIT]
    assert "pub unsafe fn into_vec<T>(self) -> Vec<T>" not in (
        cvec_sources[ROLLBACK_COMMIT]
    )
    assert "#[derive(Debug)]" in cvec_sources[CANDIDATE_COMMIT]
    assert "pub unsafe fn into_vec<T>(self) -> Vec<T>" in (
        cvec_sources[CANDIDATE_COMMIT]
    )
    print(
        "P1_U01_SOURCE_BINDINGS=PASS "
        f"candidate={CANDIDATE_COMMIT} rollback={ROLLBACK_COMMIT} "
        f"bindings={receipt['binding_count']} "
        f"bindings_sha256={receipt['bindings_sha256']} "
        f"release_bullets={len(source_rows)} "
        f"release_manifest_sha256={contract['release_manifest']['aggregate_sha256']}"
    )


def test_portable_aggregate_guards_reject_contract_mutations() -> None:
    contract = json.loads(CONTRACT_PATH.read_bytes())

    mutated_bindings = json.loads(
        json.dumps(contract["source_binding_receipt"]["bindings"])
    )
    mutated_bindings[0]["blob_oid"] = "0" * 40
    assert _canonical_digest(mutated_bindings) != EXPECTED_SOURCE_BINDINGS_SHA256

    mutated_release_entries = json.loads(
        json.dumps(contract["release_manifest"]["entries"])
    )
    mutated_release_entries.pop()
    assert (
        _canonical_digest(mutated_release_entries)
        != EXPECTED_RELEASE_MANIFEST_SHA256
    )

    mutated_invocations = json.loads(json.dumps(contract["local_invocations"]))
    mutated_invocations[0]["line"] += 1
    assert _canonical_digest(mutated_invocations) != EXPECTED_INVOCATIONS_SHA256


def test_complete_invocation_guard_rejects_import_alias_laundering() -> None:
    contract = json.loads(CONTRACT_PATH.read_bytes())
    baseline = _assert_invocation_contract(contract)
    strategy_raw = (ROOT / STRATEGY).read_text(encoding="utf-8")
    mutated = strategy_raw.replace(
        "        self._rejected = True\n",
        "        PriceAlias = Price; "
        'self._rejected = bool(PriceAlias.from_str("1"))\n',
    )
    assert mutated != strategy_raw
    mutated_discovery = _discover_local_invocations(
        contract["api_surfaces"],  # type: ignore[arg-type]
        {STRATEGY: mutated},
    )
    added = [item for item in mutated_discovery if item not in baseline]
    assert len(added) == 1
    assert added[0]["expression"] == "Price"
    assert added[0]["kind"] == "UNCLASSIFIED_ALIAS_READ"
    assert added[0]["member"] == "<alias-read>"
    assert added[0]["scope"] == "TargetPortfolioStrategy.on_order_rejected"

    with pytest.raises(AssertionError):
        _assert_invocation_contract(contract, {STRATEGY: mutated})


def test_release_semantic_representatives_are_frozen() -> None:
    contract = json.loads(CONTRACT_PATH.read_bytes())
    entries = {
        (item["release"], item["source_line"]): item
        for item in contract["release_manifest"]["entries"]
    }
    assert entries[("1.231.0", 117)] == {
        "boundary": "P1_UNUSED_DEFI_POOL_PIPELINE",
        "classification": "NOT_RELEVANT",
        "id": "RELNOTE-12310-0117-180D5506E0F1",
        "impact": "NOT_USED",
        "local_disposition": "RETIRE",
        "release": "1.231.0",
        "release_delta_id": "REL-1231-DEFI-POOL-CURRENCY-PAIR",
        "scenario_destinations": [],
        "section": "Enhancements",
        "source_line": 117,
        "text_sha256": (
            "180d5506e0f13475a0c3ed2e88d871dc02cd18a8af557f374418c7c1b27a5e3a"
        ),
    }
    assert entries[("1.231.0", 177)] == {
        "boundary": "CYTHON_V1_CVEC_CLOCK_FFI",
        "classification": "INDIRECT",
        "id": "RELNOTE-12310-0177-DE1FEC88442A",
        "impact": "BEHAVIOR_REQUALIFY",
        "local_disposition": "WRAP",
        "release": "1.231.0",
        "release_delta_id": "REL-1231-CVEC-FFI-OWNERSHIP",
        "scenario_destinations": [
            "P1-U05::cvec-clock-handler-ordering-and-drop",
            "P1-U07::cvec-clock-handler-parity",
        ],
        "section": "Security",
        "source_line": 177,
        "text_sha256": (
            "de1fec88442ac684852a0869158734b577465ef6ec6feae9ddc1103ef93e8a20"
        ),
    }
    assert entries[("1.231.0", 139)] == {
        "boundary": "P1_L1_QUOTE_AND_BAR_NO_ORDER_BOOK_DATA",
        "classification": "NOT_RELEVANT",
        "id": "RELNOTE-12310-0139-38DBAECBC244",
        "impact": "NOT_USED",
        "local_disposition": "RETIRE",
        "release": "1.231.0",
        "release_delta_id": "REL-1231-L3-ZERO-ORDER-ID",
        "scenario_destinations": [],
        "section": "Breaking Changes",
        "source_line": 139,
        "text_sha256": (
            "38dbaecbc2449ed975c156da9a77fab12efc51f084e70860579c5127a2885430"
        ),
    }
    for line, entry_id, text_sha256 in (
        (
            147,
            "RELNOTE-12310-0147-BF6B1D40C4EF",
            "bf6b1d40c4ef430617df7fcfefca716d49af8322dcdf9089a8d169c26f4aca36",
        ),
        (
            169,
            "RELNOTE-12310-0169-5AAF324EA28A",
            "5aaf324ea28a20b8f605e33a2609d2a1c9c414d5c9f538876059145751cd03d6",
        ),
    ):
        assert entries[("1.231.0", line)] == {
            "boundary": "CYTHON_V1_CVEC_CLOCK_FFI",
            "classification": "INDIRECT",
            "id": entry_id,
            "impact": "BEHAVIOR_REQUALIFY",
            "local_disposition": "WRAP",
            "release": "1.231.0",
            "release_delta_id": "REL-1231-CVEC-FFI-OWNERSHIP",
            "scenario_destinations": [
                "P1-U05::cvec-clock-handler-ordering-and-drop",
                "P1-U07::cvec-clock-handler-parity",
            ],
            "section": "Breaking Changes",
            "source_line": line,
            "text_sha256": text_sha256,
        }
    assert entries[("1.228.0", 928)]["classification"] == "V2_ONLY"
    assert entries[("1.228.0", 928)]["boundary"] == "RUST_PYO3_V2"
    deltas = {item["id"]: item for item in contract["release_delta"]}
    assert deltas["REL-1231-L3-ZERO-ORDER-ID"]["upstream_change"] == (
        "L3_MBO preprocessing now derives a deterministic price-based order ID "
        "when an input BookOrder carries zero; the fixed P1 launcher supplies "
        "L1 QuoteTick and Bar only; no BookOrder/OrderBookDelta/L3 feed or surface."
    )
    assert {
        source["path"]
        for source in deltas["REL-1231-CVEC-FFI-OWNERSHIP"]["source_evidence"]
    } == {
        "RELEASES.md",
        "crates/common/src/ffi/clock.rs",
        "crates/core/src/ffi/cvec.rs",
        "nautilus_trader/backtest/engine.pyx",
        "nautilus_trader/common/component.pyx",
        "nautilus_trader/core/rust/common.pxd",
        "nautilus_trader/core/rust/core.pxd",
    }
    assert {
        source["commit"]
        for source in deltas["REL-1231-CVEC-FFI-OWNERSHIP"]["source_evidence"]
    } == {ROLLBACK_COMMIT, CANDIDATE_COMMIT}
    cvec_bindings = {
        (source["commit"], source["path"]): (
            source["blob_oid"],
            source["symbol"],
        )
        for source in deltas["REL-1231-CVEC-FFI-OWNERSHIP"]["source_evidence"]
    }
    assert {
        (ROLLBACK_COMMIT, "crates/common/src/ffi/clock.rs"): (
            "0b643b92680147ec47a7f029a684a221cf37572c",
            "vec_time_event_handlers_drop/manual Vec::from_raw_parts ownership",
        ),
        (CANDIDATE_COMMIT, "crates/common/src/ffi/clock.rs"): (
            "4e356d141d90e9c08a64985b4d4652bced1d5c44",
            "vec_time_event_handlers_drop/unsafe CVec::into_vec ownership",
        ),
        (ROLLBACK_COMMIT, "crates/core/src/ffi/cvec.rs"): (
            "216c0d8c4977545c3085d0b47e20a2ce1d48745a",
            "CVec Clone/Copy with no consuming into_vec",
        ),
        (CANDIDATE_COMMIT, "crates/core/src/ffi/cvec.rs"): (
            "1294263a21ad38d5abfd0a36c3fb61a1f20e3b0b",
            "CVec Clone/Copy removal and consuming into_vec reconstruction",
        ),
    }.items() <= cvec_bindings.items()


@pytest.mark.parametrize(
    "include_key",
    ("include.path", "includeIf.gitdir:/tmp/p1-u01-never.path"),
)
def test_offline_cache_guard_rejects_includes_before_object_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_key: str,
) -> None:
    git_dir = tmp_path / "cache.git"
    included_config = tmp_path / "included.config"
    subprocess.run(
        ["git", "init", "--bare", str(git_dir)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "config",
            "--file",
            str(included_config),
            "remote.spy.promisor",
            "true",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            f"--git-dir={git_dir}",
            "config",
            include_key,
            str(included_config),
        ],
        check=True,
        capture_output=True,
    )
    before = sorted(
        path.relative_to(git_dir).as_posix()
        for path in (git_dir / "objects").rglob("*")
    )
    calls: list[tuple[str, ...]] = []
    real_run = subprocess.run

    def recording_run(
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        command = tuple(str(part) for part in args[0])  # type: ignore[index]
        calls.append(command)
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", recording_run)
    started = time.monotonic()
    with pytest.raises(AssertionError, match="include"):
        _assert_offline_complete_cache(git_dir)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert calls
    assert all(
        not {"fetch", "show", "rev-parse"}.intersection(command)
        for command in calls
    )
    after = sorted(
        path.relative_to(git_dir).as_posix()
        for path in (git_dir / "objects").rglob("*")
    )
    assert after == before


def test_offline_cache_guard_rejects_promisor_before_object_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_dir = tmp_path / "cache.git"
    subprocess.run(
        ["git", "init", "--bare", str(git_dir)],
        check=True,
        capture_output=True,
    )
    for key, value in (
        ("extensions.partialClone", "origin"),
        ("remote.origin.promisor", "true"),
        ("remote.origin.partialclonefilter", "blob:none"),
        ("remote.origin.url", str(tmp_path / "local-spy-origin.git")),
    ):
        subprocess.run(
            ["git", f"--git-dir={git_dir}", "config", key, value],
            check=True,
            capture_output=True,
        )
    before = sorted(
        path.relative_to(git_dir).as_posix()
        for path in (git_dir / "objects").rglob("*")
    )
    calls: list[tuple[str, ...]] = []
    real_run = subprocess.run

    def recording_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        command = tuple(str(part) for part in args[0])  # type: ignore[index]
        calls.append(command)
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", recording_run)
    started = time.monotonic()
    with pytest.raises(AssertionError, match="partial-clone|promisor"):
        _assert_offline_complete_cache(git_dir)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert calls
    assert all(
        not {"fetch", "show", "rev-parse"}.intersection(command)
        for command in calls
    )
    after = sorted(
        path.relative_to(git_dir).as_posix()
        for path in (git_dir / "objects").rglob("*")
    )
    assert after == before
