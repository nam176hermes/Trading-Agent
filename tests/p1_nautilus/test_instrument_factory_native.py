from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
CATALOG = ROOT / "tests/fixtures/p1_nautilus/contracts/instrument-catalog.json"


def test_exact_g1_native_instrument_when_host_authority_is_supplied() -> None:
    python = os.environ.get("P1_NAUTILUS_PYTHON")
    site_packages = os.environ.get("P1_NAUTILUS_SITE_PACKAGES")
    if python is None and site_packages is None:
        # Portable source qualification cannot claim native authority.
        assert "nautilus_trader" not in subprocess.run(
            ["uv", "run", "python", "-c", "import sys;print(' '.join(sys.path))"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return
    assert python is not None and site_packages is not None
    script = r'''
import json
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from runtime_v1.instrument_factory import InstrumentFactoryError, build_instrument
values = json.loads(open(sys.argv[3], encoding="utf-8").read())
catalog = tuple(sorted(values.items()))
instrument = build_instrument(catalog)
rejections = []
for label, updates in (
    ("wrong_price_precision", {"price_precision": 3}),
    ("zero_tick", {"tick_size": "0"}),
    ("negative_step", {"step_size": "-0.000001"}),
    ("unsupported_product", {"product_type": "equity"}),
    ("unknown_currency", {"base_currency": "ETH"}),
    ("over_precision", {"price_precision": 17}),
    ("outside_native_range", {"tick_size": "9223372037"}),
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
    "rejections": rejections,
    "settlement_currency": str(instrument.get_settlement_currency()),
    "size_increment": str(instrument.size_increment),
    "size_precision": instrument.size_precision,
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
        "rejections": [
            "wrong_price_precision",
            "zero_tick",
            "negative_step",
            "unsupported_product",
            "unknown_currency",
            "over_precision",
            "outside_native_range",
            "generated_identity",
            "unexpected_maximum",
            "duplicate_catalog_key",
        ],
        "settlement_currency": "USDT",
        "size_increment": "0.000001",
        "size_precision": 6,
    }
