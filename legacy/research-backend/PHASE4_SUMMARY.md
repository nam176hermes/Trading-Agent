# Phase 4: Data Vendor Fallback Chain — Implementation Complete

## What Was Built

### 1. NEW: `data_vendors.py` (167 lines)
Vendor abstraction layer with automatic fallback routing.

**Components:**
- `RateLimitError` exception class for 429 responses
- `VENDOR_MAP`: Fallback chain configuration
  - `get_price`: CoinGecko → CryptoCompare → Coinpaprika
  - `get_volume`: CoinGecko → Binance → CryptoCompare
  - `get_technicals`: Binance → CoinGecko
- `route_to_vendor()`: Main router with automatic fallback
- Individual vendor implementations:
  - `_coingecko_get_price()`: Primary price source
  - `_binance_get_price()`: Binance ticker price
  - `_cryptocompare_get_price()`: CryptoCompare free tier
  - `_coinpaprika_get_price()`: Coinpaprika free tier
  - `_binance_get_technicals()`: OHLCV candles

**Features:**
- ✓ Falls through on 429 rate limits and timeouts
- ✓ Does NOT fall through on 404s (data not found)
- ✓ Logs every vendor attempt for audit trail
- ✓ Free tier only (no auth for CryptoCompare/Coinpaprika)

### 2. MODIFIED: `data_collector.py`
Updated to use vendor routing while preserving all existing functions.

**Changes:**
- ✓ Added import: `from data_vendors import route_to_vendor, RateLimitError`
- ✓ Updated `collect_all()`: Uses vendor routing instead of direct calls
- ✓ Updated `polling_loop()`: Uses vendor routing per symbol
- ✓ Preserved all existing functions as wrappers/vendor implementations:
  - `fetch_coingecko_prices()`
  - `fetch_binance_ohlcv()`
  - `fetch_binance_price_fallback()`
  - `null_asset()`
  - `_load_cached_report()`
  - `log_raw()`

**Fallback Chain (TACN-CN):**
1. Vendor routing (3+ vendors with automatic fallback)
2. Cached report fallback (3rd tier degraded mode)
3. Null asset with logged reason (no fabrication)

## Verification

```bash
# Test imports
python3 -c "from data_vendors import route_to_vendor, RateLimitError; print('✓ OK')"
python3 -c "from data_collector import collect_all, fetch_coingecko_prices; print('✓ OK')"

# Check vendor configuration
python3 -c "import data_vendors; print(data_vendors.VENDOR_MAP.keys())"
# dict_keys(['get_price', 'get_volume', 'get_technicals'])

# Verify existing functions preserved
python3 -c "from data_collector import fetch_coingecko_prices; print('✓ OK')"
```

## Architecture

```
data_collector.py (orchestrator)
    ├── route_to_vendor() → data_vendors.py
    │   ├── CoinGecko (primary)
    │   ├── CryptoCompare (fallback)
    │   ├── Coinpaprika (fallback)
    │   └── Binance (technicals)
    ├── _load_cached_report() → cached JSON
    └── null_asset() → final fallback

Existing functions preserved:
├── fetch_coingecko_prices() ← direct CoinGecko (still works)
├── fetch_binance_ohlcv() ← direct Binance (still works)
└── fetch_binance_price_fallback() ← Binance ticker (still works)
```

## Next Steps

Phase 5 would typically add:
- Circuit breaker pattern (temporarily disable failing vendors)
- Metrics collection (vendor success rates, latency)
- Vendor health monitoring
- Configurable fallback weights

## Files Changed

- **NEW:** `data_vendors.py` (167 lines)
- **MODIFIED:** `data_collector.py` (imports + routing in `collect_all()` and `polling_loop()`)
- **UNCHANGED:** All existing function signatures and behavior
