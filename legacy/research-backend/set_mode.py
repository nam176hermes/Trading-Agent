#!/usr/bin/env python3
"""
Trading Agent Mode Switcher — toggle between paper and live trading.

Usage:
  python set_mode.py              # Show current mode
  python set_mode.py paper        # Switch to paper trading
  python set_mode.py live         # Switch to live trading
  python set_mode.py dryrun       # Switch to dryrun mode

The mode is stored in .mode file in the crypto-research directory.
The trading agent reads this file at startup and on each cycle.
"""

import sys

from runtime_paths import mode_file

VALID_MODES = {"paper", "live", "dryrun"}


def get_mode() -> str:
    """Read current mode, default to paper."""
    target = mode_file()
    if target.exists():
        mode = target.read_text().strip()
        if mode in VALID_MODES:
            return mode
    return "paper"


def set_mode(mode: str) -> None:
    """Write mode to .mode file."""
    if mode not in VALID_MODES:
        print(f"❌ Invalid mode: {mode}. Valid: {', '.join(sorted(VALID_MODES))}")
        sys.exit(1)
    target = mode_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(mode + "\n")
    print(f"✅ Mode set to: {mode}")
    _show_warning(mode)


def _show_warning(mode: str) -> None:
    """Show appropriate warning for the selected mode."""
    if mode == "live":
        print()
        print("⚠️  LIVE MODE — trades WILL execute with real money.")
        print("   Exchange credentials must be supplied through protected runtime configuration.")
        print("   Daily loss limit: 3% of capital. Drawdown halt: 20%.")
        print("   Run 'python set_mode.py paper' to return to paper trading.")
    elif mode == "dryrun":
        print()
        print("🔬 DRYRUN MODE — full pipeline, orders created but NOT submitted.")
    elif mode == "paper":
        print()
        print("📝 PAPER MODE — simulated trades, no real money at risk.")


def show_status() -> None:
    """Display current mode and system state."""
    mode = get_mode()
    emoji = {"paper": "📝", "live": "🟢", "dryrun": "🔬"}.get(mode, "❓")
    label = {"paper": "Paper Trading", "live": "Live Trading", "dryrun": "Dry Run"}
    print(f"{emoji} Current mode: {label.get(mode, mode)} ({mode})")

    # Check if agent is running
    import subprocess
    try:
        result = subprocess.run(
            ["pgrep", "-f", "trading_agent.py"],
            capture_output=True, text=True, timeout=3
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            print(f"🟢 Agent running (PID: {', '.join(pids)})")
        else:
            print("🔴 Agent NOT running")
    except Exception:
        pass

    # Show recent trades
    try:
        from db.repository import get_db
        db = get_db()
        cur = db.execute(
            "SELECT COUNT(*) FROM trades WHERE created_at > datetime('now', '-24 hours')"
        )
        recent = cur.fetchone()[0]
        cur = db.execute(
            "SELECT COUNT(*) FROM signals WHERE created_at > datetime('now', '-24 hours')"
            " AND confidence >= 0.65"
        )
        high_conf = cur.fetchone()[0]
        print(f"📊 Last 24h: {recent} trades | {high_conf} high-confidence signals")
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
        if mode in ("status", "show", "check"):
            show_status()
        else:
            set_mode(mode)
    else:
        show_status()
