import json
import re
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from control_api.app import create_app
from control_api.config import Settings


def build_data_root(root: Path) -> None:
    (root / "reports").mkdir(parents=True)
    (root / "memory").mkdir()
    asset = {
        "symbol": "BTC",
        "current_price": 100.0,
        "price_change_24h_pct": 1.0,
        "price_change_7d_pct": 2.0,
        "rsi_14": 50.0,
        "rsi_signal": "neutral",
        "macd_signal": "neutral",
        "price_vs_sma200": "above",
        "volume_trend": "1.0x",
        "suggestion": "BUY",
        "confidence": "high",
        "signal_conflict": False,
        "reasoning": "fixture",
        "atr_14": 1.0,
        "atr_pct": 1.0,
        "stop_method": "atr",
        "stop_note": "fixture",
        "alerts": [],
        "risk_assessment": {
            "position_size_pct": 0.0,
            "stop_loss_pct": 0.0,
            "risk_level": "LOW",
            "rationale": "fixture",
        },
    }
    (root / "reports" / "report_fixture.json").write_text(
        json.dumps({"timestamp": "2026-06-25T04:54:37Z", "assets": [asset]}), encoding="utf-8"
    )
    decision = {
        "ticker": "BTC",
        "suggestion": "BUY",
        "confidence": 0.5,
        "stored_at": "2026-06-25T04:54:37Z",
        "date": "2026-06-25",
        "price_at_decision": 100.0,
        "signals": {},
        "report_snippet": "fixture",
        "reflected": False,
    }
    (root / "memory" / "decisions.jsonl").write_text(json.dumps(decision) + "\n", encoding="utf-8")
    connection = sqlite3.connect(root / "memory" / "trading.db")
    connection.executescript(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY);"
        "CREATE TABLE trades (id INTEGER PRIMARY KEY);"
    )
    connection.commit()
    connection.close()
    (root / ".mode").write_text("paper\n", encoding="utf-8")
    (root / "live_prices.json").write_text(
        json.dumps({"_health": {"last_health_check": "2026-07-11T12:00:00Z"}}), encoding="utf-8"
    )


def client_for(root: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                data_root=root,
                stale_after_seconds=1800,
                git_commit="test-commit",
                build_time="test-build",
                deployment_id="test-deployment",
            ),
            env={"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"},
        )
    )


def assert_envelope(response) -> dict:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    payload = response.json()
    assert payload["schema_version"] == "2.0.0"
    assert payload["trace_id"].startswith("trace_")
    assert payload["generated_at"]
    return payload


def test_required_get_endpoints_return_versioned_envelopes(tmp_path) -> None:
    build_data_root(tmp_path)
    client = client_for(tmp_path)

    paths = [
        "/health/live",
        "/health/ready",
        "/v1/meta",
        "/v1/system/status",
        "/v1/market/latest",
        "/v1/signals",
        "/v1/decisions?page=1&page_size=50",
        "/v1/capabilities",
        "/v1/costs",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert_envelope(response)

    decisions = client.get("/v1/decisions?page=1&page_size=50").json()["data"]
    detail = client.get(f"/v1/decisions/{decisions['items'][0]['decision_id']}")
    assert detail.status_code == 200
    assert_envelope(detail)


def test_market_stale_and_capability_unknown_are_typed(tmp_path) -> None:
    build_data_root(tmp_path)
    client = client_for(tmp_path)

    market = client.get("/v1/market/latest").json()
    capabilities = client.get("/v1/capabilities").json()

    assert market["freshness"]["status"] == "STALE"
    assert market["data"]["report"]["assets"][0]["suggestion"] == "BUY"
    assert capabilities["data"]["verified"] == 0
    assert all(item["status"] == "UNKNOWN" for item in capabilities["data"]["items"])


def test_decision_query_validation_and_not_found_use_error_contract(tmp_path) -> None:
    build_data_root(tmp_path)
    client = client_for(tmp_path)

    invalid = client.get("/v1/decisions?page=0&page_size=500")
    missing = client.get("/v1/decisions/decision_missing")

    assert invalid.status_code == 422
    assert assert_envelope(invalid)["error"]["code"] == "INVALID_QUERY"
    assert missing.status_code == 404
    assert assert_envelope(missing)["error"]["code"] == "DECISION_NOT_FOUND"


def test_control_api_rejects_mutations(tmp_path) -> None:
    build_data_root(tmp_path)
    client = client_for(tmp_path)

    assert client.post("/v1/market/latest").status_code == 405
    assert client.put("/v1/system/status").status_code == 405


def test_ready_does_not_require_fresh_research(tmp_path) -> None:
    build_data_root(tmp_path)

    response = client_for(tmp_path).get("/health/ready")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "READY"


def test_trace_id_accepts_only_canonical_ascii_values(tmp_path) -> None:
    from control_api.middleware import _trace_id

    build_data_root(tmp_path)
    client = client_for(tmp_path)
    valid = "trace_release.2026-07:control"

    accepted = client.get("/health/live", headers={"X-Trace-Id": valid})

    assert accepted.headers["x-trace-id"] == valid
    assert accepted.json()["trace_id"] == valid

    for invalid in ("trace_", "trace_has space", "trace_" + "a" * 123):
        response = client.get("/health/live", headers={"X-Trace-Id": invalid})
        trace_id = response.headers["x-trace-id"]
        assert trace_id == response.json()["trace_id"]
        assert trace_id != invalid
        assert re.fullmatch(r"trace_[A-Za-z0-9][A-Za-z0-9._:-]{0,121}", trace_id)

    for invalid in ("trace_\u00e9", "trace_\x1f"):
        assert _trace_id(invalid) != invalid


def test_decision_query_rejects_an_oversized_page_window(tmp_path) -> None:
    build_data_root(tmp_path)

    response = client_for(tmp_path).get("/v1/decisions?page=101&page_size=200")

    assert response.status_code == 422
    assert assert_envelope(response)["error"]["code"] == "INVALID_QUERY"
