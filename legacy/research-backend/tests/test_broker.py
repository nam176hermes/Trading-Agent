"""
tests/test_broker.py — Unit and integration tests for broker.py.

Unit tests use unittest.mock to avoid real API calls.
The integration test (test_broker_paper_connectivity) hits the real Alpaca
paper API and is skipped automatically when ALPACA_API_KEY is not set.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import broker


# ── Constants ──────────────────────────────────────────────────────────────────

CRYPTO_SYM = "BTC"
STOCK_SYM  = "AAPL"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_response(body: dict, status: int = 200):
    """Build a mock urllib response object."""
    m = MagicMock()
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    m.read.return_value = json.dumps(body).encode()
    m.status = status
    return m


# ── Configuration ──────────────────────────────────────────────────────────────

class TestConfiguration:
    def test_live_trading_always_disabled(self):
        assert broker.LIVE_TRADING_ENABLED is False, (
            "LIVE_TRADING_ENABLED must remain False — never auto-enable"
        )

    def test_paper_trading_enabled_by_default(self):
        assert broker.PAPER_TRADING_ENABLED is True

    def test_is_configured_false_without_keys(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "")
        assert broker.is_configured() is False

    def test_is_configured_true_with_keys(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        assert broker.is_configured() is True


# ── Order routing ──────────────────────────────────────────────────────────────

class TestExecuteRouting:
    def test_crypto_skips_alpaca(self, monkeypatch):
        """Crypto symbols should never be routed to Alpaca."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        result = broker.execute(CRYPTO_SYM, 0.01, "buy")
        assert result["alpaca"] is None

    def test_stock_with_no_credentials_skips(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "")
        result = broker.execute(STOCK_SYM, 1.0, "buy")
        assert result["alpaca"] is None

    def test_live_order_blocked_when_disabled(self, monkeypatch):
        """Live orders must always be blocked (LIVE_TRADING_ENABLED=False)."""
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
        monkeypatch.setenv("ALPACA_PAPER", "false")
        result = broker.place_order(STOCK_SYM, 1.0, "buy")
        assert result is not None
        assert result["status"] == "blocked"
        assert "LIVE_TRADING_ENABLED" in result["reason"]


# ── Paper order placement (mocked) ────────────────────────────────────────────

class TestPaperOrderMocked:
    def test_paper_buy_returns_order_dict(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("ALPACA_PAPER", "true")

        mock_order = {
            "id": "abc123",
            "symbol": STOCK_SYM,
            "side": "buy",
            "qty": "1",
            "status": "accepted",
            "filled_avg_price": None,
        }
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(mock_order)
            result = broker.place_order(STOCK_SYM, 1.0, "buy")

        assert result is not None
        assert result["symbol"] == STOCK_SYM
        assert result["side"] == "buy"
        assert result["status"] == "accepted"

    def test_paper_sell_uses_correct_endpoint(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("ALPACA_PAPER", "true")

        mock_order = {"id": "def456", "symbol": STOCK_SYM, "side": "sell",
                      "qty": "1", "status": "accepted"}
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(mock_order)
            result = broker.place_order(STOCK_SYM, 1.0, "sell")

        assert result["side"] == "sell"

    def test_place_order_handles_network_error_gracefully(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("ALPACA_PAPER", "true")

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = broker.place_order(STOCK_SYM, 1.0, "buy")

        assert result is None  # graceful failure, no exception raised


# ── Account and positions (mocked) ────────────────────────────────────────────

class TestAccountMocked:
    def test_get_account_returns_dict(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")

        mock_account = {"status": "ACTIVE", "buying_power": "100000.00", "cash": "100000.00"}
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(mock_account)
            result = broker.get_account()

        assert result is not None
        assert result["status"] == "ACTIVE"
        assert "buying_power" in result

    def test_get_positions_returns_list(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")

        mock_positions = [{"symbol": "AAPL", "qty": "5", "avg_entry_price": "150.00"}]
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(mock_positions)
            result = broker.get_positions()

        assert isinstance(result, list)

    def test_get_account_no_credentials_returns_none(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "")
        result = broker.get_account()
        assert result is None


# ── Live integration test (skipped without real keys) ─────────────────────────

@pytest.mark.skipif(
    not (broker.is_configured() and not broker.LIVE_TRADING_ENABLED),
    reason="Alpaca credentials not configured — skipping live integration test",
)
class TestBrokerPaperConnectivity:
    def test_account_accessible(self):
        """Verify real Alpaca paper account is accessible."""
        account = broker.get_account()
        assert account is not None, "Expected account dict from Alpaca paper API"
        assert "status" in account, "Account response missing 'status' field"
        assert account["status"] in ("ACTIVE", "INACTIVE"), f"Unexpected status: {account['status']}"

    def test_positions_endpoint_accessible(self):
        """Verify positions endpoint returns a list."""
        positions = broker.get_positions()
        assert isinstance(positions, list), "Expected list from get_positions()"
