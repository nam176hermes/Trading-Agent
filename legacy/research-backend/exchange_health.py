"""
exchange_health.py — Multi-exchange health monitoring via CCXT.

Provides health checks for Canada-legal crypto exchanges:
  - API availability (fetch_status, fetch_time)
  - Withdrawal status (fetch_currencies → USDT/BTC)
  - Aggregated health summaries

Uses public CCXT endpoints where possible; falls back gracefully
when credentials are unavailable.
"""

import logging
import time
import datetime
from typing import Optional

import ccxt

from exchange.ccxt_bridge import CANADA_LEGAL as CANADA_LEGAL_EXCHANGES, get_exchange

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────

WITHDRAWAL_CHECK_ASSETS = {"USDT", "BTC"}
HIGH_LATENCY_THRESHOLD_MS = 5000.0

# Map human-friendly exchange names to CCXT class names
_EXCHANGE_ID_MAP = {
    "crypto.com": "cryptocom",
}


def _resolve_ccxt_id(exchange_id: str) -> str:
    """Resolve a human-friendly exchange ID to a CCXT class name."""
    key = exchange_id.lower()
    return _EXCHANGE_ID_MAP.get(key, key)


def _create_public_client(exchange_id: str) -> ccxt.Exchange:
    """Create a bare CCXT exchange instance for public API calls (no auth)."""
    ccxt_id = _resolve_ccxt_id(exchange_id)
    exchange_class = getattr(ccxt, ccxt_id)
    return exchange_class({"enableRateLimit": True})


def _get_client(exchange_id: str) -> Optional[ccxt.Exchange]:
    """Try to get an authenticated CCXT client via ccxt_bridge, fall back to public.

    Returns None if neither works.
    """
    # 1. Try authenticated client via ccxt_bridge
    try:
        adapter = get_exchange(exchange_id)
        return adapter._client  # the underlying ccxt.Exchange instance
    except Exception:
        log.debug("[%s] No authenticated adapter — using public endpoints", exchange_id)

    # 2. Fall back to public (unauthenticated) client
    try:
        return _create_public_client(exchange_id)
    except Exception as e:
        log.error("[%s] Failed to create CCXT client: %s", exchange_id, e)
        return None


def _check_withdrawals(currencies: dict, assets: set) -> bool:
    """Return True if all given assets have withdrawals enabled."""
    if not currencies:
        return False

    for asset in assets:
        currency = currencies.get(asset)
        if currency is None:
            # Asset not listed — can't withdraw what doesn't exist
            continue
        # CCXT currency dict exposes: active (bool), withdraw (bool), deposit (bool)
        if not currency.get("withdraw", False) or not currency.get("active", False):
            return False

    return True


# ── Public API ────────────────────────────────────────────────────

def check_exchange_health(exchange_id: str) -> dict:
    """Test exchange health via CCXT endpoints.

    Args:
        exchange_id: Exchange identifier (e.g. 'coinbase', 'kraken').

    Returns:
        {
            'online': bool,
            'latency_ms': float,
            'withdrawals_enabled': bool,
            'errors': list[str],
            'checked_at': str  (ISO 8601)
        }
    """
    errors: list[str] = []
    online = False
    latency_ms = 0.0
    withdrawals_enabled = False
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    client = _get_client(exchange_id)
    if client is None:
        errors.append("Could not create CCXT client (no credentials + public init failed)")
        return {
            "online": False,
            "latency_ms": 0.0,
            "withdrawals_enabled": False,
            "errors": errors,
            "checked_at": checked_at,
        }

    # ── 1. Connectivity: fetch_time (primary) ─────────────────────
    try:
        t_start = time.time()
        server_time = client.fetch_time()
        latency_ms = (time.time() - t_start) * 1000
        if server_time is not None:
            online = True
        else:
            errors.append("fetch_time returned None")
    except Exception as e:
        errors.append(f"fetch_time: {e}")

    # ── 2. Fallback: fetch_status ─────────────────────────────────
    if not online:
        try:
            status = client.fetch_status()
            if isinstance(status, dict) and status.get("status") == "ok":
                online = True
            else:
                errors.append(f"fetch_status: {status}")
        except Exception as e:
            errors.append(f"fetch_status: {e}")

    # ── 3. Withdrawal availability ────────────────────────────────
    try:
        currencies = client.fetch_currencies()
        if currencies:
            withdrawals_enabled = _check_withdrawals(currencies, WITHDRAWAL_CHECK_ASSETS)
        else:
            errors.append("fetch_currencies returned empty")
    except Exception as e:
        errors.append(f"fetch_currencies: {e}")

    return {
        "online": online,
        "latency_ms": round(latency_ms, 2),
        "withdrawals_enabled": withdrawals_enabled,
        "errors": errors,
        "checked_at": checked_at,
    }


def check_all_exchanges() -> dict[str, dict]:
    """Check health of all Canada-legal exchanges.

    Returns:
        {exchange_id: health_dict, ...}
    """
    results = {}

    for exchange_id in sorted(CANADA_LEGAL_EXCHANGES):
        try:
            health = check_exchange_health(exchange_id)
            results[exchange_id] = health

            if not health["online"]:
                log.warning(
                    "[%s] OFFLINE — errors: %s", exchange_id, health["errors"]
                )
            elif not health["withdrawals_enabled"]:
                log.warning(
                    "[%s] ONLINE but withdrawals disabled for %s",
                    exchange_id,
                    ", ".join(sorted(WITHDRAWAL_CHECK_ASSETS)),
                )
            elif health.get("latency_ms", 0) > HIGH_LATENCY_THRESHOLD_MS:
                log.warning(
                    "[%s] ONLINE but high latency: %.0f ms",
                    exchange_id,
                    health["latency_ms"],
                )
        except Exception as e:
            log.error("[%s] Health check crashed: %s", exchange_id, e)
            results[exchange_id] = {
                "online": False,
                "latency_ms": 0.0,
                "withdrawals_enabled": False,
                "errors": [f"Health check exception: {e}"],
                "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }

    return results


def get_exchange_health_summary() -> dict:
    """Aggregated health summary across all exchanges.

    Returns:
        {
            'all_online': bool,
            'failing': list[str],    # Offline exchanges
            'degraded': list[str],   # Online but withdrawals disabled OR latency > 5000 ms
        }
    """
    results = check_all_exchanges()

    failing: list[str] = []
    degraded: list[str] = []

    for exchange_id, health in results.items():
        if not health.get("online", False):
            failing.append(exchange_id)
        elif (
            not health.get("withdrawals_enabled", False)
            or health.get("latency_ms", 0) > HIGH_LATENCY_THRESHOLD_MS
        ):
            degraded.append(exchange_id)

    return {
        "all_online": len(failing) == 0,
        "failing": failing,
        "degraded": degraded,
    }


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

    print("=== Exchange Health Summary ===\n")
    summary = get_exchange_health_summary()
    print(json.dumps(summary, indent=2))

    print("\n=== Detailed Health ===\n")
    detailed = check_all_exchanges()
    for ex_id, health in detailed.items():
        status = "🟢 ONLINE" if health["online"] else "🔴 OFFLINE"
        wd = "✓" if health["withdrawals_enabled"] else "✗"
        print(
            f"{ex_id:12s} {status}  latency={health['latency_ms']:.0f}ms  withdrawals={wd}"
        )
        if health["errors"]:
            for err in health["errors"]:
                print(f"             ⚠  {err}")
