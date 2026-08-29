from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/run_p1_nautilus_vertical_slice.py"
MARKET = ROOT / "tests/fixtures/p1_nautilus/e2e/btcusdt-1m.jsonl"

EXPECTED_FIXTURES = {
    "engine_configuration_sha256": "38fa348e0422607052851028ed84b2478740d930ce09832dc5e42cbb86b78f60",
    "instrument_catalog_sha256": "22a6c061b06d0eef539509a5cfa4a1128843a80b1f48eb473a9b65126f74d822",
    "market_data_sha256": "d390750a1d51b6f333efc7092cd99f2c6752ca6ab51daeaa800171ea92005c9c",
    "strategy_configuration_sha256": "c4002efb2f0f2b14c94699db59ef8c5733602e41c3bfe60999670fb7c0671470",
}


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "python", str(SCRIPT), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_absent_external_authority_is_canonical_deferred_without_job_mutation() -> None:
    result = _run()

    assert result.returncode == 0
    assert result.stderr == ""
    receipt = json.loads(result.stdout)
    assert receipt == {
        "authority_limits": {
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        },
        "evidence": {},
        "external_authority": {"native": "ABSENT", "postgres": "ABSENT"},
        "fixture_authority": {
            "account_id": "p1-btcusdt-fixture-account",
            **EXPECTED_FIXTURES,
            "liquidity_side": "TAKER",
            "opening_source": "p1-engine-configuration",
            "opening_source_revision": EXPECTED_FIXTURES[
                "engine_configuration_sha256"
            ],
            "other_money": "0",
            "reconciliation_source": "VENUE",
            "starting_cash": "1000000",
            "starting_currency": "USDT",
            "strategy_id": "p1-target-strategy-v1",
            "window": {
                "end": "2026-08-05T12:01:00Z",
                "start": "2026-08-05T12:00:00Z",
            },
        },
        "job_mutated": False,
        "reason": "EXTERNAL_AUTHORITY_ABSENT",
        "schema": "trading-agent-p1-nautilus-vertical-slice/v1",
        "status": "DEFERRED",
    }


def test_partial_external_authority_is_blocked_and_does_not_leak_paths() -> None:
    secret_path = "/tmp/private-p1-closure"
    result = _run("--p1-closure-root", secret_path)

    assert result.returncode == 2
    assert result.stderr == ""
    assert secret_path not in result.stdout
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason"] == "EXTERNAL_AUTHORITY_PARTIAL_OR_INVALID"
    assert receipt["job_mutated"] is False


def test_e2e_market_fixture_is_exact_canonical_two_row_authority() -> None:
    expected = (
        b'{"ask":"100","bid":"99","close":"100","event_time":"2026-08-05T12:00:00Z",'
        b'"high":"101","low":"98","open":"99","quote_time":"2026-08-05T12:00:00Z",'
        b'"sequence":1,"volume":"2"}\n'
        b'{"ask":"102","bid":"101","close":"102","event_time":"2026-08-05T12:01:00Z",'
        b'"high":"103","low":"100","open":"101","quote_time":"2026-08-05T12:01:00Z",'
        b'"sequence":2,"volume":"3"}\n'
    )

    assert MARKET.read_bytes() == expected


def test_network_credentials_are_not_an_operator_interface() -> None:
    marker = "must-not-appear"
    result = _run("--exchange-api-key", marker)

    assert result.returncode == 2
    assert marker not in result.stdout
    assert marker not in result.stderr


def test_invalid_authority_values_are_redacted() -> None:
    marker = "/tmp/private-invalid-value"
    result = _run("--postgres-scope", marker)

    assert result.returncode == 2
    assert marker not in result.stdout
    assert marker not in result.stderr


def test_execute_cannot_bypass_unpromoted_database_runtime_pin() -> None:
    result = _run("--execute")

    assert result.returncode == 2
    receipt = json.loads(result.stdout)
    assert receipt["reason"] == "EXTERNAL_AUTHORITY_PARTIAL_OR_INVALID"
    assert receipt["job_mutated"] is False


def test_transport_authority_rejects_checkout_and_symlink_paths(
    tmp_path: Path,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    assert hasattr(vertical, "_validate_transport_root")
    outside = tmp_path / "transport"
    outside.mkdir(mode=0o700)
    vertical._validate_transport_root(outside)

    link = tmp_path / "transport-link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(vertical.VerticalSliceError):
        vertical._validate_transport_root(link)
    with pytest.raises(vertical.VerticalSliceError):
        vertical._validate_transport_root(ROOT / "tests")
