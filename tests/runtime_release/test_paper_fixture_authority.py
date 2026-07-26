from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from packages.runtime_release.paper_backend.provider_free_fixture import (
    FixtureAuthorityError,
    load_provider_free_fixture,
)


COMMIT = "41f055b48033714c660f44cc20498b7545366e75"


def _authority(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    fixture = {
        "schema_version": 1,
        "provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
        "as_of": "2026-07-26T12:00:00Z",
        "assets": {
            "BTC": {
                "current_price": 100000,
                "market_cap": 2000000000000,
                "total_volume": 50000000000,
                "price_change_percentage_24h": 1.25,
            }
        },
    }
    fixture_path = tmp_path / "fixture.json"
    raw = json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()
    fixture_path.write_bytes(raw)
    fixture_path.chmod(0o444)
    authority = {
        "schema_version": 1,
        "classification": "PACKAGE6_PROVIDER_FREE_FIXTURE",
        "package6_approval_sha256": "c" * 64,
        "backend_commit": COMMIT,
        "generated_at": "2026-07-26T12:00:00Z",
        "expires_at": "2026-07-26T12:30:00Z",
        "fixture_path": str(fixture_path),
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
    }
    authority_path = tmp_path / "authority.json"
    authority_path.write_text(
        json.dumps(authority, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    authority_path.chmod(0o444)
    return authority_path, authority


def test_exact_fixture_is_deterministic_and_explicitly_provider_free(
    tmp_path: Path,
) -> None:
    authority_path, authority = _authority(tmp_path)

    fixture = load_provider_free_fixture(
        authority_path,
        expected_backend_commit=COMMIT,
        expected_package6_approval_sha256="c" * 64,
        now=datetime(2026, 7, 26, 12, 10, tzinfo=UTC),
        trusted_uid=None,
    )

    assert fixture.provenance == "DETERMINISTIC_PROVIDER_FREE_V1"
    assert fixture.sha256 == authority["fixture_sha256"]
    assert fixture.market["BTC"]["current_price"] == 100000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_price", -1),
        ("market_cap", float("inf")),
        ("total_volume", float("nan")),
        ("price_change_percentage_24h", float("inf")),
    ],
)
def test_fixture_rejects_non_finite_or_negative_market_values(
    tmp_path: Path,
    field: str,
    value: float,
) -> None:
    authority_path, authority = _authority(tmp_path)
    fixture_path = Path(str(authority["fixture_path"]))
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture["assets"]["BTC"][field] = value
    raw = json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()
    fixture_path.chmod(0o600)
    fixture_path.write_bytes(raw)
    fixture_path.chmod(0o444)
    authority["fixture_sha256"] = hashlib.sha256(raw).hexdigest()
    authority_path.chmod(0o600)
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    authority_path.chmod(0o444)

    with pytest.raises(FixtureAuthorityError, match="asset"):
        load_provider_free_fixture(
            authority_path,
            expected_backend_commit=COMMIT,
            expected_package6_approval_sha256="c" * 64,
            now=datetime(2026, 7, 26, 12, 10, tzinfo=UTC),
            trusted_uid=None,
        )


def test_fixture_file_must_share_the_private_authority_directory(
    tmp_path: Path,
) -> None:
    authority_path, authority = _authority(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-fixture.json"
    outside.write_bytes(Path(str(authority["fixture_path"])).read_bytes())
    outside.chmod(0o444)
    authority["fixture_path"] = str(outside)
    authority_path.chmod(0o600)
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    authority_path.chmod(0o444)

    try:
        with pytest.raises(FixtureAuthorityError, match="directory"):
            load_provider_free_fixture(
                authority_path,
                expected_backend_commit=COMMIT,
                expected_package6_approval_sha256="c" * 64,
                now=datetime(2026, 7, 26, 12, 10, tzinfo=UTC),
                trusted_uid=None,
            )
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(extra=True),
        lambda d: d.update(backend_commit="f" * 40),
        lambda d: d.update(expires_at="2026-07-26T13:00:00Z"),
        lambda d: d.update(fixture_sha256="f" * 64),
        lambda d: d.update(classification="COINGECKO_FIXTURE"),
        lambda d: d.update(fixture_path="../fixture.json"),
    ],
)
def test_fixture_authority_fails_closed_on_schema_time_drift_or_path_attack(
    tmp_path: Path, mutation
) -> None:
    authority_path, authority = _authority(tmp_path)
    mutation(authority)
    authority_path.chmod(0o600)
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    authority_path.chmod(0o444)

    with pytest.raises(FixtureAuthorityError):
        load_provider_free_fixture(
            authority_path,
            expected_backend_commit=COMMIT,
            expected_package6_approval_sha256="c" * 64,
            now=datetime(2026, 7, 26, 12, 10, tzinfo=UTC),
            trusted_uid=None,
        )


def test_canonical_entrypoint_prefers_fixture_without_provider_call(
    monkeypatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "legacy/research-backend"))
    monkeypatch.syspath_prepend(
        str(root / "packages/runtime_release/paper_backend")
    )
    from packages.runtime_release.paper_backend import paper_main

    fixture = type(
        "Fixture",
        (),
        {
            "market": {"BTC": {"current_price": 123}},
            "provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
            "sha256": "a" * 64,
        },
    )()
    monkeypatch.setattr(paper_main, "_fixture_market_snapshot", lambda: fixture)
    monkeypatch.setattr(
        paper_main,
        "_public_market_snapshot",
        lambda: pytest.fail("provider must not be called"),
    )

    market, provenance = paper_main._approved_market_snapshot()

    assert market["BTC"]["current_price"] == 123
    assert provenance == {
        "market_data_provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
        "fixture_sha256": "a" * 64,
    }


def test_fixture_report_never_labels_assets_as_provider_backed(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "legacy/research-backend"))
    monkeypatch.syspath_prepend(str(root / "packages/runtime_release/paper_backend"))
    from packages.runtime_release.paper_backend import paper_main

    semantic = paper_main.SnapshotSemanticInputs(
        macro_report={},
        sentiment_report={},
        onchain_report={},
        macro_snapshot={},
        source_fingerprint="a" * 64,
        input_version="test",
    )
    report = paper_main.build_snapshot_report(
        semantic,
        {"BTC": {"current_price": 123}},
        {
            "market_data_provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
            "fixture_sha256": "b" * 64,
        },
    )

    assets = cast(list[dict[str, object]], report["assets"])
    assert assets[0]["source"] == "deterministic_provider_free_fixture"


def test_partial_package6_fixture_selector_fails_closed(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "legacy/research-backend"))
    monkeypatch.syspath_prepend(str(root / "packages/runtime_release/paper_backend"))
    from packages.runtime_release.paper_backend import paper_main

    monkeypatch.setenv("TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH", "/tmp/missing.json")
    monkeypatch.delenv("TRADING_PACKAGE6_APPROVAL_SHA256", raising=False)
    monkeypatch.setattr(
        paper_main,
        "_public_market_snapshot",
        lambda: pytest.fail("provider must be unreachable in a partial Package 6 run"),
    )

    with pytest.raises(FixtureAuthorityError, match="complete spawn-bound"):
        paper_main._approved_market_snapshot()
