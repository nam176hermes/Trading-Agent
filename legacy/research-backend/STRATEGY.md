# Trading Strategy v5.0-fallback
Generated: 2026-05-16T02:01:47.369011+00:00
Source: statistical_only

## Symbols
- Trade: ['ADA', 'AVAX', 'BTC', 'DOGE', 'ETH', 'LINK', 'SOL']
- Avoid: []
- Min win rate: 45%

## Regime Rules
- Allow: ['trending_up']
- Avoid: ['unclear']

## Direction
- LONG: ✅
- SHORT: ❌

## Position Sizing
- Base: 3.0%
- Max: 10.0%
- Min: 1.0%

## Stops & Targets
- ATR multiplier (long): 2.0x
- Trailing stop: 2.0%
- Min R:R: 2.0:1

## Rationale
### Symbol Selection
Trading symbols with ≥45% win rate: ['ADA', 'AVAX', 'BTC', 'DOGE', 'ETH', 'LINK', 'SOL']. Avoiding: []

### Regime Filter
Only trending_up (highest win rate). Avoid unclear (lowest).

### Direction
LONG only — SHORT signals show negative forward returns across all regimes.

### Confidence
Min 55% confidence for entry based on win rate differential.
