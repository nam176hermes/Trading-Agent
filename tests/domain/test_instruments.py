from __future__ import annotations

from dataclasses import FrozenInstanceError
import pytest
from packages.domain import (
    InstrumentId,
    ProductType,
)


def test_instrument_normalizes_ascii_whitespace_and_case() -> None:
    instrument = InstrumentId(
        symbol=" \tbtc-usdt\n",
        product_type=ProductType.CRYPTO_SPOT,
        venue=" alpaca ",
    )

    assert instrument.symbol == "BTC-USDT"
    assert instrument.venue == "ALPACA"
    assert instrument.canonical == "crypto_spot:ALPACA:BTC-USDT"
    with pytest.raises(FrozenInstanceError):
        instrument.symbol = "ETH-USDT"  # type: ignore[misc]


@pytest.mark.parametrize("symbol", ["", "BTC USDT", "BTC/USDT", "BTC:USDT", "BTC\u00a0USDT", "A" * 33])
def test_instrument_rejects_unsafe_or_oversized_symbols(symbol: str) -> None:
    with pytest.raises(ValueError, match="symbol"):
        InstrumentId(symbol=symbol, product_type=ProductType.CRYPTO_SPOT, venue="BINANCE")


@pytest.mark.parametrize("venue", ["", "ALPACA!", "ALPACA:US", "ALPACA\u00a0", "V" * 33])
def test_instrument_rejects_unsafe_or_oversized_venues(venue: str) -> None:
    with pytest.raises(ValueError, match="venue"):
        InstrumentId(symbol="AAPL", product_type=ProductType.EQUITY, venue=venue)


def test_instrument_validates_product_type_enum() -> None:
    with pytest.raises(ValueError, match="ProductType"):
        InstrumentId(symbol="AAPL", product_type="equity", venue="ALPACA")  # type: ignore[arg-type]


def test_instrument_canonical_form_is_collision_resistant() -> None:
    crypto = InstrumentId("BTC", ProductType.CRYPTO_SPOT, "ALPACA")
    equity = InstrumentId("BTC", ProductType.EQUITY, "ALPACA")
    alternate_venue = InstrumentId("BTC", ProductType.CRYPTO_SPOT, "COINBASE")

    assert len({crypto.canonical, equity.canonical, alternate_venue.canonical}) == 3
