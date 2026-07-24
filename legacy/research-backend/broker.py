"""
broker.py — Alpaca Markets API integration for stock execution.

Paper mode only. Live trading disabled via kill switch.
LIVE_TRADING_ENABLED must be manually set to True — NEVER auto-enable.
"""

import json
import logging
import os
from asset_registry import resolve_asset
from live_execution_policy import LiveExecutionPolicy
from runtime_paths import configured_env_file, mode_file

log = logging.getLogger("broker")

LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
PAPER_TRADING_ENABLED = True   # Paper trading through Alpaca sandbox — safe

# Required environment variables:
#   ALPACA_API_KEY=your_api_key
#   ALPACA_SECRET_KEY=your_secret_key
#   ALPACA_PAPER=true (defaults to paper endpoint)
# Without these keys, broker operations will return None.

ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_URL = "https://api.alpaca.markets"

def get_mode() -> str:
    """Read the trading mode from .mode file. Returns 'paper' or 'real'."""
    target = mode_file()
    if target.exists():
        mode = target.read_text().strip().lower()
        if mode in ("paper", "real"):
            return mode
    return "paper"


def set_mode(mode: str) -> None:
    """Write the trading mode to .mode file."""
    if mode not in ("paper", "real"):
        raise ValueError(f"Invalid mode: {mode}. Must be 'paper' or 'real'.")
    target = mode_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(mode + "\n")


def _load_env():
    env_path = configured_env_file()
    if env_path is not None:
        from dotenv import load_dotenv
        load_dotenv(env_path)


def _get_credentials() -> tuple[str | None, str | None, str | None]:
    _load_env()
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    paper = os.getenv("ALPACA_PAPER", "true").lower() in ("true", "1", "yes")
    return key, secret, "paper" if paper else "live"


def is_configured() -> bool:
    key, secret, _ = _get_credentials()
    return bool(key and secret)


def _auth_headers() -> dict | None:
    key, secret, _ = _get_credentials()
    if not key or not secret:
        return None
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }


def _api_url(endpoint: str) -> tuple[str, dict | None]:
    _, _, env = _get_credentials()
    base = ALPACA_PAPER_URL if env == "paper" else ALPACA_LIVE_URL
    headers = _auth_headers()
    return f"{base}/v2/{endpoint}", headers


def get_account() -> dict | None:
    url, headers = _api_url("account")
    if not headers:
        return None
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error("[broker] get_account failed: %s", e)
        return None


def place_order(symbol: str, qty: float, side: str) -> dict | None:
    """
    Place a market order via Alpaca. Paper trading allowed; live must be explicitly enabled.
    side: "buy" or "sell"
    """
    _, _, env = _get_credentials()

    if env == "live":
        decision = LiveExecutionPolicy().evaluate(
            "live",
            adapter_initialized=True,
            credentials_available=is_configured(),
        )
        if not decision.allowed:
            log.critical("[broker] Real order blocked: reason=%s", decision.reason_code)
            return {
                "status": "blocked",
                "reason": f"LIVE_TRADING_ENABLED=False; {decision.reason_code}",
                "reason_code": decision.reason_code,
            }

    # Block live orders unless explicitly enabled
    if env == "live" and not LIVE_TRADING_ENABLED:
        log.info("[broker] Live trading disabled — order not sent: %s %s %.2f", side, symbol, qty)
        return {"status": "blocked", "reason": "LIVE_TRADING_ENABLED=False"}

    # Paper trading must also be enabled (default: True for sandbox safety)
    if env == "paper" and not PAPER_TRADING_ENABLED:
        log.info("[broker] Paper trading disabled — order not sent: %s %s %.2f", side, symbol, qty)
        return {"status": "blocked", "reason": "PAPER_TRADING_ENABLED=False"}

    url, headers = _api_url("orders")
    if not headers:
        return {"status": "skipped", "reason": "Alpaca credentials not configured"}

    payload = json.dumps({
        "symbol": symbol,
        "qty": str(round(qty, 4)),
        "side": side.lower(),
        "type": "market",
        "time_in_force": "day",
    }).encode()

    try:
        import urllib.request
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            log.info("[broker] Order placed: %s %s %s @ %s", result.get("side"), result.get("symbol"),
                     result.get("qty"), result.get("filled_avg_price", "pending"))
            return result
    except Exception as e:
        log.error("[broker] place_order failed: %s %s %.2f — %s", side, symbol, qty, e)
        return None


def get_positions() -> list[dict]:
    url, headers = _api_url("positions")
    if not headers:
        return []
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error("[broker] get_positions failed: %s", e)
        return []


def close_position(symbol: str) -> dict | None:
    url, headers = _api_url(f"positions/{symbol}")
    if not headers:
        return None
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers, method="DELETE")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error("[broker] close_position %s failed: %s", symbol, e)
        return None


def close_all() -> list[dict]:
    url, headers = _api_url("positions")
    if not headers:
        return []
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers, method="DELETE")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error("[broker] close_all failed: %s", e)
        return []


def execute(symbol: str, qty: float, side: str) -> dict:
    """
    Route: paper_trader always runs, + Alpaca paper if configured.
    Only routes stock symbols to Alpaca (not crypto).
    """
    from paper_trader import execute_signal as paper_execute, load_portfolio

    result = {"paper": "skipped", "alpaca": None}

    # Paper always runs — already handled by paper_trader.execute_signal upstream
    # Broker is the secondary layer for Alpaca paper execution

    route = resolve_asset(symbol)
    if route is None:
        log.error("[broker] Rejecting unknown asset: %s", symbol)
        return {**result, "status": "rejected", "reason": "REJECT_UNKNOWN_ASSET"}
    if route.adapter == "crypto":
        log.info("[broker] %s is crypto — skipping Alpaca (paper trader handles crypto)", symbol)
        return result

    # Alpaca — paper trading (default) or live if explicitly enabled
    if is_configured() and (PAPER_TRADING_ENABLED or LIVE_TRADING_ENABLED):
        result["alpaca"] = place_order(symbol, qty, side)

    return result


def execution_route(symbol: str) -> str | None:
    route = resolve_asset(symbol)
    return route.adapter if route else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(f"Broker configured: {is_configured()}")
    print(f"Live trading enabled: {LIVE_TRADING_ENABLED}")
    if is_configured():
        account = get_account()
        if account:
            print(f"Account status: {account.get('status')}")
            print(f"Buying power: ${account.get('buying_power')}")
        print(f"Positions: {get_positions()}")
