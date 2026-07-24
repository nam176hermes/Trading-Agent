"""
kalshi_collector.py — Kalshi Prediction Market Macro Signals

Fetches market-implied probabilities for macro events from Kalshi's regulated
US prediction market and converts them into regime modifiers for macro.py.

Gracefully degrades when KALSHI_API_KEY_ID is not set or API is unreachable.
Output: reports/kalshi_macro_<timestamp>.json
"""
import json
import logging
import os
from datetime import datetime, timezone

from runtime_paths import configured_env_file, reports_dir

log = logging.getLogger('kalshi_collector')

REPORTS_DIR = reports_dir()
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

MACRO_SERIES = {
    'fed_hike':  'KXFED',
    'fed_cut':   'KXFEDCUT',
    'recession': 'KXREC',
    'cpi':       'KXCPI',
}

KALSHI_REGIME_WEIGHTS = {
    'fed_hike':  -0.15,
    'fed_cut':   +0.12,
    'recession': -0.20,
    'cpi':       -0.08,
}

def _get_api_key():
    key_id = os.getenv('KALSHI_API_KEY_ID')
    key_path = os.getenv('KALSHI_PRIVATE_KEY_PATH')
    if not key_id:
        env_file = configured_env_file()
        if env_file is not None:
            from dotenv import load_dotenv
            load_dotenv(env_file)
            key_id = os.getenv('KALSHI_API_KEY_ID')
            key_path = os.getenv('KALSHI_PRIVATE_KEY_PATH')
    return key_id, key_path

def _fetch_market_probs(client):
    probs = {}
    for series_key, ticker in MACRO_SERIES.items():
        try:
            markets = client.get_markets(series_ticker=ticker, status='open', limit=10)
            if not markets:
                continue
            market = markets[0]
            yes_ask = getattr(market, 'yes_ask', None)
            if yes_ask is not None:
                probs[series_key] = float(yes_ask) / 100.0
                log.info('[kalshi] %s (%s): %.1f%%', series_key, ticker, probs[series_key] * 100)
        except Exception as e:
            log.debug('[kalshi] Failed to fetch %s: %s', ticker, e)
    return probs

def _regime_delta(probs):
    delta = 0.0
    reasons = []
    for key, weight in KALSHI_REGIME_WEIGHTS.items():
        prob = probs.get(key)
        if prob is None:
            continue
        contribution = weight * prob
        delta += contribution
        pct = prob * 100
        if abs(contribution) >= 0.03:
            direction = 'bearish' if contribution < 0 else 'bullish'
            reasons.append(f'Kalshi {key.replace("_", " ")} prob={pct:.0f}% ({direction})')
    return round(delta, 4), reasons

def collect():
    key_id, key_path = _get_api_key()
    if not key_id:
        log.debug('[kalshi] KALSHI_API_KEY_ID not set — skipping. Sign up at https://kalshi.com')
        return None
    try:
        from pykalshi.client import KalshiClient
        client = KalshiClient(api_key_id=key_id, private_key_path=key_path)
    except Exception as e:
        log.warning('[kalshi] Client init failed: %s', e)
        return None
    try:
        probs = _fetch_market_probs(client)
    except Exception as e:
        log.warning('[kalshi] Market fetch failed: %s', e)
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass
    if not probs:
        return None
    delta, reasons = _regime_delta(probs)
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    report = {
        'source': 'kalshi',
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'probabilities': {k: round(v, 4) for k, v in probs.items()},
        'regime_score_delta': delta,
        'regime_reasons': reasons,
    }
    out_path = REPORTS_DIR / f'kalshi_macro_{ts}.json'
    out_path.write_text(json.dumps(report, indent=2))
    old = sorted(REPORTS_DIR.glob('kalshi_macro_*.json'))
    for f in old[:-5]:
        f.unlink(missing_ok=True)
    log.info('[kalshi] regime_delta=%.3f reasons=%s', delta, reasons)
    return report

def load_latest():
    files = sorted(REPORTS_DIR.glob('kalshi_macro_*.json'))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except Exception:
        return None

if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    result = collect()
    if result:
        print(json.dumps(result, indent=2))
    else:
        print('[kalshi] No data — set KALSHI_API_KEY_ID in protected runtime configuration')
