"""
alert_manager.py — Signal alert manager for trading dashboard.

Reads the latest report JSON, detects strong signals (confidence > 0.6),
and writes alerts to alerts.jsonl for the frontend dashboard.
"""

import json
import urllib.request
import urllib.parse
import logging
import os
import sys
from datetime import datetime, timezone

from runtime_paths import configured_env_file, data_root, reports_dir

env_file = configured_env_file()
if env_file is not None:
    from dotenv import load_dotenv
    load_dotenv(env_file)
from exchange.secrets import load_secrets_into_env
load_secrets_into_env()

log = logging.getLogger("alert_manager")

REPORTS_DIR = reports_dir()
MEMORY_DIR = data_root() / "memory"
ALERTS_FILE = MEMORY_DIR / "alerts.jsonl"
ALERT_LOG_FILE = MEMORY_DIR / "alert_log.jsonl"

STRONG_ACTIONS = {"BUY", "SELL", "STRONG BUY", "STRONG SELL", "WATCH FOR ENTRY", "WATCH FOR EXIT"}


def get_latest_report() -> dict | None:
    """Read the most recent report JSON file."""
    if not REPORTS_DIR.exists():
        return None

    files = sorted(
        [f for f in os.listdir(REPORTS_DIR) if f.startswith("report_") and f.endswith(".json")],
        reverse=True,
    )
    if not files:
        return None

    with open(REPORTS_DIR / files[0]) as f:
        return json.load(f)


def check_signals(report: dict) -> list[dict]:
    """Extract strong signals from a report."""
    alerts = []
    report_ts = report.get("timestamp", datetime.now(timezone.utc).isoformat())

    for asset in report.get("assets", []):
        symbol = asset.get("symbol", "").upper()
        suggestion = asset.get("suggestion", "").upper().strip()
        confidence_str = asset.get("confidence", "0")
        reasoning = asset.get("reasoning", "")
        price = asset.get("current_price")

        try:
            confidence = float(confidence_str)
        except (ValueError, TypeError):
            confidence = 0.0

        if confidence < 0.5:
            continue

        if suggestion not in STRONG_ACTIONS:
            continue

        alerts.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "asset": symbol,
            "action": suggestion,
            "confidence": round(confidence, 4),
            "price": price,
            "reasoning": reasoning[:300] if reasoning else "",
            "delivered": False,
            "report_ts": report_ts,
        })

    return alerts



def _telegram_post(url: str, payload: bytes, retries: int = 3) -> bool:
    """POST to Telegram with exponential-backoff retry. Returns True on success."""
    import time
    delay = 2.0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=payload, method="POST")
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            if attempt < retries - 1:
                log.warning("[telegram] Attempt %d/%d failed (%s) — retrying in %.0fs",
                            attempt + 1, retries, e, delay)
                time.sleep(delay)
                delay *= 2
            else:
                log.warning("[telegram] All %d attempts failed: %s", retries, e)
    return False


def send_telegram_text(message: str) -> bool:
    """Send a raw text message to Telegram. Returns True on success."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_ALLOWED_USERS", "")
    if not token or not chat_id:
        return False

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
    }).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    return _telegram_post(url, payload)


def send_telegram(alert: dict) -> bool:
    """Send alert to Telegram. Returns True on success."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_ALLOWED_USERS", "")
    if not token or not chat_id:
        return False

    emoji = "🟢" if alert["action"] in ("BUY", "STRONG BUY") else "🔴"
    text = (
        f"{emoji} *{alert['asset']}* — {alert['action']}\n"
        f"Confidence: {alert['confidence']*100:.0f}%\n"
        f"Price: ${alert['price']}\n"
        f"{alert['reasoning'][:200]}"
    )
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    return _telegram_post(url, payload)


ALERT_COOLDOWN_HOURS = 4  # minimum gap before resending same (asset, action) pair


def get_existing_alert_keys() -> set[tuple[str, str, str]]:
    """Get existing alert keys (for exact dedup by report_ts) and recent cooldown set."""
    if not ALERTS_FILE.exists():
        return set()

    keys = set()
    with open(ALERTS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                key = (entry.get("asset", ""), entry.get("action", ""), entry.get("report_ts", ""))
                keys.add(key)
            except (json.JSONDecodeError,):
                continue
    return keys


def _recent_cooldown_keys() -> set[tuple[str, str]]:
    """Return (asset, action) pairs that fired within the last ALERT_COOLDOWN_HOURS."""
    if not ALERTS_FILE.exists():
        return set()

    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ALERT_COOLDOWN_HOURS)).isoformat()
    keys: set[tuple[str, str]] = set()
    with open(ALERTS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("timestamp", "") >= cutoff:
                    keys.add((entry.get("asset", ""), entry.get("action", "")))
            except (json.JSONDecodeError,):
                continue
    return keys


def process_alerts(signals: list[dict]) -> int:
    """
    Process a list of pre-computed signal dicts and deliver alerts.
    Writes to alert_log.jsonl for tracking.
    Returns number of delivered alerts.
    """
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    existing = get_existing_alert_keys()
    cooldown = _recent_cooldown_keys()
    delivered_count = 0

    for alert in signals:
        key = (alert.get("asset", ""), alert.get("action", ""), alert.get("report_ts", ""))
        if key in existing:
            continue
        # Per-pair cooldown: don't re-alert same (asset, action) within 4 hours
        pair = (alert.get("asset", ""), alert.get("action", ""))
        if pair in cooldown:
            log.debug("[alert] Cooldown active for %s %s — skipping", pair[0], pair[1])
            continue

        delivered = send_telegram(alert)
        if delivered:
            alert["delivered"] = True

        with open(ALERTS_FILE, "a") as f:
            f.write(json.dumps(alert) + "\n")

        # Write alert log entry
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "asset": alert.get("asset"),
            "action": alert.get("action"),
            "confidence": alert.get("confidence"),
            "delivered": delivered,
            "message": f"{alert.get('action')} {alert.get('asset')} (conf={alert.get('confidence', 0)*100:.0f}%)",
        }
        with open(ALERT_LOG_FILE, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        if delivered:
            delivered_count += 1

    log.info("[alert] Delivered %d alerts", delivered_count)
    return delivered_count


def run() -> int:
    """Check latest report and emit new alerts. Returns number of new alerts."""
    report = get_latest_report()
    if not report:
        log.info("[alerts] No reports found")
        return 0

    signals = check_signals(report)
    if not signals:
        log.info("[alerts] No strong signals found")
        return 0

    return process_alerts(signals)


def main():
    n = run()
    if n > 0:
        print(f"[alerts] Emitted {n} new alert(s)")
    elif "--verbose" in sys.argv:
        print("[alerts] No new alerts")


if __name__ == "__main__":
    main()
