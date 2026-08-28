from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Iterator
from zipfile import ZipFile


ROOT = Path(__file__).parents[2]
CATALOG = ROOT / "tests/fixtures/p1_nautilus/contracts/instrument-catalog.json"
G1_CLOSURE_SHA256 = "24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255"
G1_WHEEL_SHA256 = "ecc461d0f634c25db17e0fb79136c3bf0d513edd323d4f9adaaf84346e68b2fb"
G1_WHEEL_SIZE = 183_626_605


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


@contextmanager
def exact_g1_runtime() -> Iterator[tuple[str, str] | None]:
    python = os.environ.get("P1_NAUTILUS_PYTHON")
    closure_manifest = os.environ.get("P1_NAUTILUS_CLOSURE_MANIFEST")
    legacy_site = os.environ.get("P1_NAUTILUS_SITE_PACKAGES")
    if python is None and closure_manifest is None and legacy_site is None:
        yield None
        return
    assert python is not None and closure_manifest is not None
    assert legacy_site is None
    manifest_path = Path(closure_manifest).resolve()
    assert _digest(manifest_path) == G1_CLOSURE_SHA256
    document = json.loads(manifest_path.read_bytes())
    root = manifest_path.parent
    python_path = (root / "files/usr/bin/python3.12").resolve()
    assert Path(python).resolve() == python_path
    assert _digest(python_path) == document["python"]["executable_sha256"]
    assert document["engine"] == {
        "name": "nautilus_trader",
        "upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
        "upstream_tag": "v1.231.0",
        "version": "1.231.0",
    }
    records = document["runtime_wheels"]
    wheel_root = root / "files/engine/wheels"
    wheels = {path.name: path for path in wheel_root.glob("*.whl")}
    runtime_names = {record["filename"] for record in records}
    engine_names = {
        name for name in wheels if name.startswith("nautilus_trader-1.231.0-")
    }
    assert len(engine_names) == 1
    assert set(wheels) == runtime_names | engine_names
    for record in records:
        path = wheels[record["filename"]]
        assert path.stat().st_size == record["size"]
        assert _digest(path) == record["sha256"]
    engine_wheel = wheels[engine_names.pop()]
    assert engine_wheel.stat().st_size == G1_WHEEL_SIZE
    assert _digest(engine_wheel) == G1_WHEEL_SHA256
    with tempfile.TemporaryDirectory(prefix="p1-g1-site-", dir="/tmp") as directory:
        for path in wheels.values():
            with ZipFile(path) as archive:
                archive.extractall(directory)
        yield str(python_path), directory


def test_exact_g1_native_instrument_when_host_authority_is_supplied() -> None:
    with exact_g1_runtime() as runtime:
        if runtime is None:
            # Portable source qualification cannot claim native authority.
            assert "nautilus_trader" not in subprocess.run(
                ["uv", "run", "python", "-c", "import sys;print(' '.join(sys.path))"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            return
        python, site_packages = runtime
        script = r'''
import json
from pathlib import Path
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from runtime_v1.instrument_factory import InstrumentFactoryError, build_instrument
assert nautilus_trader.__version__ == "1.231.0"
assert Path(nautilus_trader.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())
values = json.loads(open(sys.argv[3], encoding="utf-8").read())
catalog = tuple(sorted(values.items()))
instrument = build_instrument(catalog)
rebuilt = build_instrument(catalog)
engine = BacktestEngine(BacktestEngineConfig(load_state=False, logging=LoggingConfig(bypass_logging=True), run_analysis=False, save_state=False))
try:
    engine.add_venue(Venue("BINANCE"), OmsType.NETTING, AccountType.CASH, [instrument.min_notional])
    engine.add_instrument(instrument)
    engine.reset()
    engine.add_instrument(rebuilt)
    registration_ids = [str(item.id) for item in engine.cache.instruments()]
finally:
    engine.dispose()
rejections = []
for label, updates in (
    ("wrong_price_precision", {"price_precision": 3}),
    ("zero_tick", {"tick_size": "0"}),
    ("negative_step", {"step_size": "-0.000001"}),
    ("unsupported_product", {"product_type": "equity"}),
    ("unknown_currency", {"base_currency": "ETH"}),
    ("over_precision", {"price_precision": 17}),
    ("outside_price_range", {"tick_size": "17014118346047"}),
    ("outside_quantity_range", {"step_size": "34028236692094"}),
    ("generated_identity", {"instrument_id": "BTCUSDT-1.BINANCE"}),
    ("unexpected_maximum", {"max_quantity": "1"}),
):
    mutated = dict(values)
    mutated.update(updates)
    try:
        build_instrument(tuple(sorted(mutated.items())))
    except InstrumentFactoryError:
        rejections.append(label)
    else:
        raise AssertionError(f"accepted {label}")
try:
    build_instrument(catalog + (("symbol", "BTCUSDT"),))
except InstrumentFactoryError:
    rejections.append("duplicate_catalog_key")
else:
    raise AssertionError("accepted duplicate catalog key")
print(json.dumps({
    "asset_class": instrument.asset_class.name,
    "base_currency": str(instrument.base_currency),
    "base_precision": instrument.base_currency.precision,
    "id": str(instrument.id),
    "instrument_class": instrument.instrument_class.name,
    "min_notional": str(instrument.min_notional.as_decimal()),
    "min_quantity": str(instrument.min_quantity),
    "price_increment": str(instrument.price_increment),
    "price_precision": instrument.price_precision,
    "quote_currency": str(instrument.quote_currency),
    "quote_precision": instrument.quote_currency.precision,
    "raw_symbol": str(instrument.raw_symbol),
    "registration_ids": registration_ids,
    "rejections": rejections,
    "settlement_currency": str(instrument.get_settlement_currency()),
    "size_increment": str(instrument.size_increment),
    "size_precision": instrument.size_precision,
    "venue": str(instrument.id.venue),
}, separators=(",", ":"), sort_keys=True))
'''
        completed = subprocess.run(
            [
                python,
                "-I",
                "-S",
                "-c",
                script,
                str(ROOT / "engines/nautilus"),
                site_packages,
                str(CATALOG),
            ],
            cwd="/",
            check=False,
            capture_output=True,
            text=True,
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "asset_class": "CRYPTOCURRENCY",
        "base_currency": "BTC",
        "base_precision": 8,
        "id": "BTCUSDT.BINANCE",
        "instrument_class": "SPOT",
        "min_notional": "10",
        "min_quantity": "0.000001",
        "price_increment": "0.01",
        "price_precision": 2,
        "quote_currency": "USDT",
        "quote_precision": 8,
        "raw_symbol": "BTCUSDT",
        "registration_ids": ["BTCUSDT.BINANCE"],
        "rejections": [
            "wrong_price_precision",
            "zero_tick",
            "negative_step",
            "unsupported_product",
            "unknown_currency",
            "over_precision",
            "outside_price_range",
            "outside_quantity_range",
            "generated_identity",
            "unexpected_maximum",
            "duplicate_catalog_key",
        ],
        "settlement_currency": "USDT",
        "size_increment": "0.000001",
        "size_precision": 6,
        "venue": "BINANCE",
    }
