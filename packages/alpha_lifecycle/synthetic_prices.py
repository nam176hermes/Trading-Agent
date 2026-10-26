"""Frozen seed-9 rehearsal prices: simulated coordinates, never observed markets.

The historical S7 fixture selected this scenario using a synthetic screen. It has
no economic evidentiary value. Preserve its exact rows; do not tune selection.
No caller may override seed, horizon, volatility, liquidity or price parameters.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import random


ROWS_SHA256 = '54b4f688654789cec4668e3644f4b94a408534d5e86101883be9a27793bf23f7'


def daily_rows() -> tuple[tuple[str, ...], ...]:
    """All 2800 research, 365 holdout and one buffer rows, generated together."""
    rng, state, burst = random.Random(9), 1, 0
    rows = []
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        close, cent, base = Decimal('10000'), Decimal('0.01'), Decimal('1000000')
        for index in range(3166):
            if rng.random() < 0.025:
                state = -state
            if burst == 0 and rng.random() < 0.01:
                burst = 15
            sigma = 0.04 if burst else 0.012
            burst = max(0, burst - 1)
            change = Decimal(f'{state * 0.006 + rng.gauss(0, sigma):.6f}')
            opened = close
            close = (opened * (1 + change)).quantize(cent)
            high = (max(opened, close) * Decimal('1.002')).quantize(cent)
            low = (min(opened, close) * Decimal('0.998')).quantize(cent)
            values = (opened, high, low, close, base, (base * close).quantize(cent))
            rows.append((str(date(2018, 1, 1) + timedelta(days=index)),
                         *(format(value.normalize(), 'f') for value in values)))
    return tuple(rows)
