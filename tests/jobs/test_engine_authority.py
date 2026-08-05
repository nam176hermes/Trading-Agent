from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from packages.engine_contracts import ArtifactReference, RunBacktest
from packages.job_contracts import (
    BacktestPayload,
    EnqueueJobRequest,
    EngineBacktestInput,
    EngineBacktestPayload,
    JobType,
    parse_payload,
)
from services.job_store.worker_repository import ClaimedJob
from services.job_worker.engine_authority import (
    BacktestEngineAuthorityFactory,
)


NOW = datetime(2026, 8, 5, 12, 30, 15, 123456, tzinfo=UTC)
CODE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
JOB_ID = "job_0123456789abcdef0123456789abcdef"
ATTEMPT_ID = "attempt_fedcba9876543210fedcba9876543210"


def _engine_payload() -> EngineBacktestPayload:
    parsed = parse_payload(
        JobType.BACKTEST,
        {
            "engine_backtest": {
                "engine_configuration": {
                    "artifact_id": "11111111-1111-4111-8111-111111111111",
                    "sha256": "1" * 64,
                    "media_type": "application/json",
                },
                "instrument_catalog": {
                    "artifact_id": "22222222-2222-4222-8222-222222222222",
                    "sha256": "2" * 64,
                    "media_type": "application/json",
                },
                "strategy_configuration": {
                    "artifact_id": "33333333-3333-4333-8333-333333333333",
                    "sha256": "3" * 64,
                    "media_type": "application/json",
                },
                "market_data": {
                    "artifact_id": "44444444-4444-4444-8444-444444444444",
                    "sha256": "4" * 64,
                    "media_type": "application/jsonl",
                },
                "start_time": "2026-07-01T00:00:00Z",
                "end_time": "2026-08-01T00:00:00Z",
            }
        },
    )
    assert isinstance(parsed, EngineBacktestPayload)
    return parsed


def _claim(**changes: object) -> ClaimedJob:
    claim = ClaimedJob(
        job_id=JOB_ID,
        job_type=JobType.BACKTEST,
        payload=_engine_payload(),
        attempt_id=ATTEMPT_ID,
        attempt_number=1,
        worker_id="worker-authority-1",
        lease_token="lease-token_0123456789abcdefghijklmnopqrstuvwxyz",
        lease_expires_at=NOW + timedelta(seconds=30),
        max_attempts=3,
    )
    return replace(claim, **changes)


def _factory(
    *, code_commit: str = CODE_COMMIT, clock=lambda: NOW
) -> BacktestEngineAuthorityFactory:
    return BacktestEngineAuthorityFactory(code_commit=code_commit, clock=clock)


def test_backtest_payload_parsing_keeps_legacy_and_engine_authority_forms_distinct() -> None:
    legacy = parse_payload(
        JobType.BACKTEST,
        {
            "asset": "btc",
            "strategy_id": "legacy-binary-report-v1",
            "date_from": None,
            "date_to": None,
        },
    )
    engine = _engine_payload()

    assert type(legacy) is BacktestPayload
    assert legacy.model_dump(mode="json") == {
        "asset": "BTC",
        "strategy_id": "legacy-binary-report-v1",
        "date_from": None,
        "date_to": None,
    }
    assert type(engine) is EngineBacktestPayload
    assert type(engine.engine_backtest) is EngineBacktestInput
    assert set(engine.model_dump(mode="json")) == {"engine_backtest"}


def test_enqueue_api_preserves_an_already_validated_engine_backtest_payload() -> None:
    payload = _engine_payload()

    request = EnqueueJobRequest(
        job_type=JobType.BACKTEST,
        payload=payload,
        idempotency_key="engine-backtest-authority-1",
        actor={"actor_type": "OPERATOR", "actor_id": "operator-1"},
    )

    assert type(request.payload) is EngineBacktestPayload
    assert request.payload == payload


@pytest.mark.parametrize(
    ("path", "value"),
    (
        ((), {"provider": "nautilus"}),
        (("engine_backtest",), {"provider": "nautilus"}),
        (
            ("engine_backtest", "market_data"),
            {"provider": "nautilus"},
        ),
    ),
)
def test_engine_backtest_input_rejects_every_provider_specific_extra(
    path: tuple[str, ...], value: dict[str, str]
) -> None:
    payload = _engine_payload().model_dump(mode="json")
    target: dict[str, object] = payload
    for component in path:
        target = target[component]  # type: ignore[assignment]
    target.update(value)

    with pytest.raises(ValidationError):
        parse_payload(JobType.BACKTEST, payload)


def test_engine_backtest_input_rejects_an_empty_or_reversed_window() -> None:
    for end_time in ("2026-07-01T00:00:00Z", "2026-06-30T23:59:59Z"):
        payload = _engine_payload().model_dump(mode="json")
        payload["engine_backtest"]["end_time"] = end_time
        with pytest.raises(ValidationError, match="end_time must be after start_time"):
            parse_payload(JobType.BACKTEST, payload)


def test_factory_derives_the_closed_command_and_full_attempt_authority() -> None:
    envelope = _factory().from_claim(_claim())

    assert type(envelope.payload) is RunBacktest
    assert envelope.payload == RunBacktest(
        command_type="RunBacktest",
        engine_configuration=ArtifactReference(
            artifact_id=UUID("11111111-1111-4111-8111-111111111111"),
            sha256="1" * 64,
            media_type="application/json",
        ),
        instrument_catalog=ArtifactReference(
            artifact_id=UUID("22222222-2222-4222-8222-222222222222"),
            sha256="2" * 64,
            media_type="application/json",
        ),
        strategy_configuration=ArtifactReference(
            artifact_id=UUID("33333333-3333-4333-8333-333333333333"),
            sha256="3" * 64,
            media_type="application/json",
        ),
        market_data=ArtifactReference(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            sha256="4" * 64,
            media_type="application/jsonl",
        ),
        start_time=datetime(2026, 7, 1, tzinfo=UTC),
        end_time=datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert envelope.message_id == UUID("864e910f-8e0a-54e2-adb4-64eb8f2c3c25")
    assert envelope.correlation_id == UUID("09224f0c-7596-5af1-9e23-3d01644f46c2")
    assert envelope.causation_id == UUID("9149303b-040e-5076-8d42-3a75fd790a62")
    assert envelope.engine_run_id == UUID("a1a533d8-52a3-5633-a6e9-16e77bc84e30")
    assert envelope.stream_sequence == 1
    assert envelope.event_time == NOW
    assert envelope.initialization_time == NOW
    assert envelope.schema_version == "1.0.0"
    assert envelope.producer_identity == "worker-authority-1"
    assert envelope.source_commit == CODE_COMMIT

    canonical_command = json.dumps(
        envelope.payload.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected_digest = hashlib.sha256(canonical_command).hexdigest()
    assert envelope.payload_digest == expected_digest
    canonical_config = json.dumps(
        {
            "engine_configuration": envelope.payload.engine_configuration.model_dump(
                mode="json"
            ),
            "instrument_catalog": envelope.payload.instrument_catalog.model_dump(
                mode="json"
            ),
            "strategy_configuration": (
                envelope.payload.strategy_configuration.model_dump(mode="json")
            ),
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert envelope.config_digest == hashlib.sha256(canonical_config).hexdigest()
    assert envelope.config_digest != envelope.payload_digest
    assert "provider" not in canonical_command.decode("utf-8").lower()
    assert "nautilus" not in canonical_command.decode("utf-8").lower()


def test_factory_ids_vary_by_attempt_while_the_closed_command_stays_identical() -> None:
    first = _factory().from_claim(_claim())
    repeated = _factory().from_claim(_claim())
    retry = _factory().from_claim(
        _claim(
            attempt_id="attempt_00000000000000000000000000000002",
            attempt_number=2,
        )
    )

    assert repeated == first
    assert retry.payload == first.payload
    assert retry.payload_digest == first.payload_digest
    assert retry.config_digest == first.config_digest
    assert retry.message_id != first.message_id
    assert retry.correlation_id != first.correlation_id
    assert retry.causation_id != first.causation_id
    assert retry.engine_run_id != first.engine_run_id


def test_factory_binds_attempt_number_independently_of_attempt_id() -> None:
    first = _factory().from_claim(_claim(attempt_number=1))
    inconsistent = _factory().from_claim(_claim(attempt_number=2))

    assert inconsistent.payload == first.payload
    assert inconsistent.message_id != first.message_id
    assert inconsistent.correlation_id != first.correlation_id
    assert inconsistent.causation_id != first.causation_id
    assert inconsistent.engine_run_id != first.engine_run_id


def test_factory_attributes_the_exact_claimed_worker_in_the_producer() -> None:
    first = _factory().from_claim(_claim(worker_id="worker-authority-1"))
    second = _factory().from_claim(_claim(worker_id="worker-authority-2"))

    assert first.producer_identity == "worker-authority-1"
    assert second.producer_identity == "worker-authority-2"
    assert first != second


def test_factory_producer_remains_safe_for_the_largest_claimed_worker_id() -> None:
    worker_id = "w" * 128

    envelope = _factory().from_claim(_claim(worker_id=worker_id))

    assert envelope.producer_identity == worker_id


def test_config_digest_projects_only_configuration_references() -> None:
    baseline_payload = _engine_payload()
    baseline = _factory().from_claim(_claim(payload=baseline_payload))

    market_wire = baseline_payload.model_dump(mode="json")
    market_wire["engine_backtest"]["market_data"]["sha256"] = "5" * 64
    market_changed = _factory().from_claim(
        _claim(payload=parse_payload(JobType.BACKTEST, market_wire))
    )

    window_wire = baseline_payload.model_dump(mode="json")
    window_wire["engine_backtest"]["start_time"] = "2026-07-02T00:00:00Z"
    window_changed = _factory().from_claim(
        _claim(payload=parse_payload(JobType.BACKTEST, window_wire))
    )

    config_wire = baseline_payload.model_dump(mode="json")
    config_wire["engine_backtest"]["engine_configuration"]["sha256"] = "6" * 64
    config_changed = _factory().from_claim(
        _claim(payload=parse_payload(JobType.BACKTEST, config_wire))
    )

    assert market_changed.config_digest == baseline.config_digest
    assert market_changed.payload_digest != baseline.payload_digest
    assert window_changed.config_digest == baseline.config_digest
    assert window_changed.payload_digest != baseline.payload_digest
    assert config_changed.config_digest != baseline.config_digest
    assert config_changed.payload_digest != baseline.payload_digest


def test_factory_refuses_the_accepted_legacy_backtest_payload() -> None:
    legacy = BacktestPayload(
        asset="BTC",
        strategy_id="legacy-binary-report-v1",
        date_from=None,
        date_to=None,
    )

    with pytest.raises(ValueError, match="engine backtest authority input is required"):
        _factory().from_claim(_claim(payload=legacy))


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"job_type": JobType.REPLAY}, "claimed BACKTEST job is required"),
        ({"job_id": "job-unsafe"}, "claim identity is invalid"),
        ({"attempt_id": "attempt-unsafe"}, "claim identity is invalid"),
        ({"worker_id": "worker/unsafe"}, "claim identity is invalid"),
        ({"lease_token": ""}, "claim fence is invalid"),
        ({"attempt_number": 0}, "claim attempt boundary is invalid"),
        ({"attempt_number": 4}, "claim attempt boundary is invalid"),
        ({"max_attempts": 0}, "claim attempt boundary is invalid"),
        (
            {"lease_expires_at": datetime(2026, 8, 5, 12, 30, 15, tzinfo=UTC)},
            "claim lease is expired",
        ),
        (
            {"lease_expires_at": datetime(2026, 8, 5, 12, 31)},
            "claim lease time is invalid",
        ),
    ),
)
def test_factory_rejects_missing_inconsistent_or_boundary_invalid_claims(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _factory().from_claim(_claim(**changes))


@pytest.mark.parametrize(
    "clock_value",
    (
        datetime(2026, 8, 5, 12, 30, 15),
        datetime(2026, 8, 5, 8, 30, 15, tzinfo=timezone(timedelta(hours=-4))),
    ),
)
def test_factory_requires_a_canonical_utc_worker_clock(clock_value: datetime) -> None:
    with pytest.raises(ValueError, match="worker clock returned an invalid time"):
        _factory(clock=lambda: clock_value).from_claim(_claim())


def test_factory_normalizes_database_and_clock_utc_zoneinfo() -> None:
    zoneinfo_now = NOW.replace(tzinfo=ZoneInfo("UTC"))
    claim = _claim(
        lease_expires_at=(NOW + timedelta(seconds=30)).replace(
            tzinfo=ZoneInfo("UTC")
        )
    )

    envelope = _factory(clock=lambda: zoneinfo_now).from_claim(claim)

    assert envelope.event_time == NOW
    assert envelope.event_time.tzinfo is UTC


@pytest.mark.parametrize("code_commit", ("", "generated", "A" * 40, "a" * 39))
def test_factory_requires_a_canonical_worker_commit(code_commit: str) -> None:
    with pytest.raises(ValueError, match="worker code commit is invalid"):
        _factory(code_commit=code_commit)


def test_factory_rejects_non_claim_objects_including_client_envelopes() -> None:
    envelope = _factory().from_claim(_claim())

    with pytest.raises(TypeError, match="ClaimedJob"):
        _factory().from_claim(envelope)  # type: ignore[arg-type]


def test_factory_rejects_claimed_job_subclasses() -> None:
    class ExtendedClaimedJob(ClaimedJob):
        pass

    base = _claim()
    extended = ExtendedClaimedJob(
        job_id=base.job_id,
        job_type=base.job_type,
        payload=base.payload,
        attempt_id=base.attempt_id,
        attempt_number=base.attempt_number,
        worker_id=base.worker_id,
        lease_token=base.lease_token,
        lease_expires_at=base.lease_expires_at,
        max_attempts=base.max_attempts,
    )

    with pytest.raises(TypeError, match="exact ClaimedJob"):
        _factory().from_claim(extended)
