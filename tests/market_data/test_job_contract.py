"""RED/green contract proof for the provider-free P10 snapshot request."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from packages.job_contracts import (
    ActorIdentity,
    ActorType,
    EnqueueJobRequest,
    JobType,
    SnapshotPayload,
    parse_payload,
    payload_fingerprint,
)


def _request(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "scope": "default",
        "requested_as_of": None,
        "market_data": {
            "provider": "deterministic-provider-free-fixture-v1",
            "instrument": "crypto_spot:FIXTURE:BTC",
            "timeframe": "1m",
            "interval_seconds": 60,
            "requested_at": "2026-08-04T12:00:00Z",
            "provider_retry_limit": 1,
        },
    }
    value.update(overrides)
    return value


def test_market_data_snapshot_request_has_one_closed_provider_fixture_vocabulary() -> None:
    payload = parse_payload(JobType.SNAPSHOT, _request())

    assert isinstance(payload, SnapshotPayload)
    assert payload.market_data is not None
    assert payload.market_data.provider == "deterministic-provider-free-fixture-v1"
    assert payload.market_data.instrument == "crypto_spot:FIXTURE:BTC"
    assert payload.market_data.timeframe == "1m"
    assert payload.market_data.interval_seconds == 60
    assert payload.market_data.requested_at == datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    assert payload.market_data.provider_retry_limit == 1


def test_market_data_snapshot_request_schema_requires_canonical_utc_z_time() -> None:
    schema = SnapshotPayload.model_json_schema()
    requested_at = schema["$defs"]["MarketDataSnapshotRequest"]["properties"]["requested_at"]

    assert requested_at["pattern"] == r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"


def test_market_data_snapshot_request_fingerprint_is_deterministic() -> None:
    left = parse_payload(JobType.SNAPSHOT, _request())
    right = parse_payload(
        JobType.SNAPSHOT,
        {
            "market_data": {
                "provider_retry_limit": 1,
                "requested_at": "2026-08-04T12:00:00Z",
                "interval_seconds": 60,
                "timeframe": "1m",
                "instrument": "crypto_spot:FIXTURE:BTC",
                "provider": "deterministic-provider-free-fixture-v1",
            },
            "requested_as_of": None,
            "scope": "default",
        },
    )

    assert payload_fingerprint(left) == payload_fingerprint(right)


def test_job_authority_preserves_market_data_intent_without_scheduler_identity() -> None:
    request = EnqueueJobRequest.model_validate(
        {
            "job_type": "SNAPSHOT",
            "payload": _request(),
            "idempotency_key": "market-data:btc:2026-08-04T12:00:00Z",
            "actor": {"actor_type": "OPERATOR", "actor_id": "market-data-test"},
        }
    )

    assert request.actor == ActorIdentity(
        actor_type=ActorType.OPERATOR,
        actor_id="market-data-test",
    )
    assert isinstance(request.payload, SnapshotPayload)
    assert request.payload.market_data is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "public-feed"),
        ("instrument", "crypto_spot:FIXTURE:ETH"),
        ("timeframe", "5m"),
        ("interval_seconds", 300),
        ("interval_seconds", True),
        ("requested_at", "2026-08-04T12:00:00+01:00"),
        ("requested_at", datetime(2026, 8, 4, 12, 0)),
        ("provider_retry_limit", 2),
        ("order", "BTC"),
    ],
)
def test_market_data_snapshot_request_rejects_noncanonical_or_privileged_fields(
    field: str,
    value: object,
) -> None:
    request = _request()
    assert isinstance(request["market_data"], dict)
    request["market_data"][field] = value

    with pytest.raises(ValueError):
        parse_payload(JobType.SNAPSHOT, request)


def test_legacy_snapshot_payload_remains_exactly_valid() -> None:
    payload = parse_payload(
        JobType.SNAPSHOT,
        {"scope": "default", "requested_as_of": None},
    )

    assert payload == SnapshotPayload(scope="default", requested_as_of=None)
    assert payload.market_data is None


def test_market_data_snapshot_request_rejects_non_utc_datetime_instances() -> None:
    request = _request()
    assert isinstance(request["market_data"], dict)
    request["market_data"]["requested_at"] = datetime(
        2026, 8, 4, 12, 0, tzinfo=timezone(timedelta(hours=1))
    )

    with pytest.raises(ValueError):
        parse_payload(JobType.SNAPSHOT, request)
