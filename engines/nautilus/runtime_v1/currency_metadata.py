"""Pure fixed-currency precision authority for the P1 spot profile."""

from __future__ import annotations

from decimal import Decimal


CURRENCY_METADATA = {
    "BTC": (8, 0, "Bitcoin", 1),
    "USDT": (6, 0, "Tether", 1),
}


def currency_quanta(base: object, quote: object) -> tuple[Decimal, Decimal]:
    if base != "BTC" or quote != "USDT":
        raise ValueError("P1 catalog currency identity is invalid")
    return (
        Decimal(1).scaleb(-CURRENCY_METADATA[base][0]),
        Decimal(1).scaleb(-CURRENCY_METADATA[quote][0]),
    )


__all__ = ["CURRENCY_METADATA", "currency_quanta"]
