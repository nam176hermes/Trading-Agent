#!/usr/bin/python3.12
"""Stdlib-only sealed probe for every U01 direct API surface."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
import sysconfig
import zipfile


_SCHEMA = "trading-agent-nautilus-v1231-api-probe/v1"
_RESULT_SURFACE = "API-BACKTEST-RESULT"
_STRATEGY_SURFACE = "API-STRATEGY"
_STRATEGY_LOCAL_MEMBERS = {
    "entry_filled_quantity",
    "rejected",
    "semantic_events",
}


class ApiProbeError(ValueError):
    """The sealed API contract or observed candidate API is incomplete."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _objects(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ApiProbeError(f"{label} must be a list of objects")
    return value


def build_probe_manifest(
    contract: dict[str, object],
    *,
    symbols: dict[str, object],
    result: object,
    strategy_type: type,
    engine_version: str,
    lifecycle: dict[str, bool],
) -> dict[str, object]:
    """Validate and project the complete generated contract into one record."""

    surfaces = _objects(contract.get("api_surfaces"), "api_surfaces")
    invocations = _objects(contract.get("local_invocations"), "local_invocations")
    surface_ids = [item.get("id") for item in surfaces]
    if (
        not surface_ids
        or any(not isinstance(item, str) or not item for item in surface_ids)
        or len(surface_ids) != len(set(surface_ids))
        or set(symbols) != set(surface_ids)
    ):
        raise ApiProbeError("surface identity or mapping is incomplete")

    cases: list[dict[str, object]] = []
    for surface in surfaces:
        surface_id = surface.get("id")
        module = surface.get("import_module")
        symbol_name = surface.get("import_symbol")
        members = surface.get("required_members")
        if (
            not isinstance(surface_id, str)
            or not isinstance(module, str)
            or not isinstance(symbol_name, str)
            or not isinstance(members, list)
            or any(not isinstance(member, str) or not member for member in members)
        ):
            raise ApiProbeError("surface shape is invalid")
        symbol = symbols[surface_id]
        case = "IMPORTED_SYMBOL"
        for member in members:
            target = symbol
            if surface_id == _RESULT_SURFACE:
                target = result
                case = "RESULT_INSTANCE"
            elif surface_id == _STRATEGY_SURFACE and member in _STRATEGY_LOCAL_MEMBERS:
                target = strategy_type
                case = "STRATEGY_SUBCLASS"
            if not hasattr(target, member):
                raise ApiProbeError(f"required member is absent: {surface_id}.{member}")
        cases.append(
            {
                "case": case,
                "id": surface_id,
                "members": members,
                "module": module,
                "symbol": symbol_name,
            }
        )

    invocation_ids: list[str] = []
    known_surfaces = set(surface_ids)
    for invocation in invocations:
        invocation_id = invocation.get("id")
        mapped = invocation.get("surface_ids")
        if (
            not isinstance(invocation_id, str)
            or not invocation_id
            or not isinstance(mapped, list)
            or not mapped
            or any(not isinstance(item, str) or item not in known_surfaces for item in mapped)
        ):
            raise ApiProbeError("local invocation surface mapping is invalid")
        invocation_ids.append(invocation_id)
    if len(invocation_ids) != len(set(invocation_ids)):
        raise ApiProbeError("local invocation identity is duplicated")
    if engine_version != "1.231.0":
        raise ApiProbeError("candidate engine version is not exact")
    expected_lifecycle = {
        "dispose_called": True,
        "reset_called": True,
        "reset_retained_instrument": True,
        "reset_retained_strategy": True,
    }
    if lifecycle != expected_lifecycle:
        raise ApiProbeError("engine lifecycle observation is incomplete")

    return {
        "api_surface_count": len(cases),
        "engine_version": engine_version,
        "lifecycle": lifecycle,
        "local_invocation_count": len(invocation_ids),
        "local_invocation_ids": sorted(invocation_ids),
        "schema": _SCHEMA,
        "status": "PASS",
        "surface_cases": cases,
        "surface_ids_sha256": hashlib.sha256(
            _canonical(sorted(surface_ids))
        ).hexdigest(),
    }


def _closed_json(path: Path) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ApiProbeError("probe input contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiProbeError("probe input is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise ApiProbeError("probe input must be an object")
    return value


def _require_stdlib_entry() -> None:
    if (
        sys.version_info[:2] != (3, 12)
        or sys.flags.isolated != 1
        or sys.flags.no_site != 1
        or sys.flags.ignore_environment != 1
        or sys.flags.no_user_site != 1
        or sys.flags.safe_path is not True
    ):
        raise ApiProbeError("probe requires direct isolated CPython 3.12")
    allowed = {
        Path(sysconfig.get_path("stdlib")).resolve(),
        Path(sysconfig.get_path("platstdlib")).resolve(),
    }
    if shared := sysconfig.get_config_var("DESTSHARED"):
        allowed.add(Path(shared).resolve())
    allowed.add(
        Path(sysconfig.get_path("stdlib")).resolve().parent
        / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    )
    if not sys.path or any(Path(item).resolve() not in allowed for item in sys.path):
        raise ApiProbeError("probe started with an ambient import path")


def _extract_wheels(wheel_directory: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    wheels = tuple(sorted(wheel_directory.glob("*.whl"), key=lambda item: item.name))
    if not wheels:
        raise ApiProbeError("sealed wheel inventory is empty")
    for index, wheel in enumerate(wheels):
        destination = Path("/tmp") / f"wheel-{index}"
        destination.mkdir(mode=0o700)
        try:
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    relative = Path(member.filename)
                    if (
                        relative.is_absolute()
                        or ".." in relative.parts
                        or stat.S_ISLNK(member.external_attr >> 16)
                    ):
                        raise ApiProbeError("sealed wheel contains an unsafe member")
                archive.extractall(destination)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ApiProbeError("sealed wheel is unreadable") from exc
        roots.append(destination.resolve(strict=True))
    sys.path.extend(str(root) for root in roots)
    return tuple(roots)


def _load_strategy(path: Path) -> type:
    specification = importlib.util.spec_from_file_location(
        "_p1_u05_target_portfolio_strategy", path
    )
    if specification is None or specification.loader is None:
        raise ApiProbeError("target strategy specification is unavailable")
    module = importlib.util.module_from_spec(specification)
    try:
        specification.loader.exec_module(module)
    except Exception as exc:
        raise ApiProbeError("target strategy cannot be loaded") from exc
    strategy = getattr(module, "TargetPortfolioStrategy", None)
    if not isinstance(strategy, type):
        raise ApiProbeError("target strategy class is unavailable")
    return strategy


def _observe_candidate(
    contract: dict[str, object], roots: tuple[Path, ...], strategy_path: Path
) -> dict[str, object]:
    surfaces = _objects(contract.get("api_surfaces"), "api_surfaces")
    symbols: dict[str, object] = {}
    for surface in surfaces:
        module_name = surface.get("import_module")
        symbol_name = surface.get("import_symbol")
        surface_id = surface.get("id")
        if not all(isinstance(value, str) for value in (module_name, symbol_name, surface_id)):
            raise ApiProbeError("surface import shape is invalid")
        module = importlib.import_module(module_name)
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str) or not any(
            Path(origin).resolve(strict=True).is_relative_to(root) for root in roots
        ):
            raise ApiProbeError("surface import escaped the sealed wheels")
        try:
            symbols[surface_id] = getattr(module, symbol_name)
        except AttributeError as exc:
            raise ApiProbeError(f"required symbol is absent: {surface_id}") from exc

    from nautilus_trader import __version__
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.common.config import LoggingConfig
    from nautilus_trader.config import BacktestEngineConfig
    from nautilus_trader.model.currencies import USDT
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Money
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from nautilus_trader.trading.strategy import Strategy

    engine = BacktestEngine(
        BacktestEngineConfig(
            load_state=False,
            logging=LoggingConfig(bypass_logging=True),
            run_analysis=False,
            save_state=False,
        )
    )
    disposed = False
    try:
        instrument = TestInstrumentProvider.btcusdt_binance()
        engine.add_venue(
            venue=Venue("BINANCE"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(1_000_000, USDT)],
        )
        engine.add_instrument(instrument)
        engine.add_strategy(Strategy())
        result = engine.get_result()
        engine.reset()
        lifecycle = {
            "dispose_called": True,
            "reset_called": True,
            "reset_retained_instrument": engine.cache.instrument(instrument.id) is not None,
            "reset_retained_strategy": len(engine.trader.strategies()) == 1,
        }
    finally:
        engine.dispose()
        disposed = True
    lifecycle["dispose_called"] = disposed
    return build_probe_manifest(
        contract,
        symbols=symbols,
        result=result,
        strategy_type=_load_strategy(strategy_path),
        engine_version=__version__,
        lifecycle=lifecycle,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--strategy", required=True, type=Path)
    parser.add_argument("--wheel-directory", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        _require_stdlib_entry()
        document = _observe_candidate(
            _closed_json(arguments.contract),
            _extract_wheels(arguments.wheel_directory),
            arguments.strategy,
        )
    except ApiProbeError as exc:
        print(f"API probe failed: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(_canonical(document) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
