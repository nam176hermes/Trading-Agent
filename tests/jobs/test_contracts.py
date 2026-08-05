import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.job_contracts import (
    APPROVED_ASSET_SYMBOLS,
    BacktestPayload,
    ActorIdentity,
    ActorType,
    ArtifactMetadata,
    DebatePayload,
    EnqueueJobBody,
    EnqueueJobRequest,
    EventMetadata,
    AttemptMetadata,
    JobDetail,
    JobMetadata,
    JobState,
    JobType,
    PayloadTooLarge,
    ReplayPayload,
    SnapshotPayload,
    canonical_payload_json,
    parse_payload,
    payload_fingerprint,
)
from packages.job_contracts.asset_registry import (
    CANONICAL_ASSET_REGISTRY,
    PHASE1_CANONICAL_EQUITY_SYMBOLS,
    PHASE1_CRYPTO_SYMBOLS,
)


def _ajv_schema_results(
    schema: dict[str, object], samples: list[dict[str, object]]
) -> list[dict[str, bool]]:
    dashboard_root = Path(__file__).resolve().parents[2] / "apps" / "dashboard"
    script = """
const fs = require('fs');
const Ajv2020 = require(process.argv[1] + '/node_modules/@redocly/ajv/dist/2020').default;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const validate = new Ajv2020({allErrors: true, strict: false}).compile(input.schema);
const results = input.samples.map(({expected, value}) => ({expected, actual: validate(value)}));
process.stdout.write(JSON.stringify(results));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(dashboard_root)],
        input=json.dumps({"schema": schema, "samples": samples}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _engine_backtest_wire_payload() -> dict[str, object]:
    return {
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
    }


def _job_payload_wire_forms() -> tuple[tuple[JobType, dict[str, object]], ...]:
    return (
        (
            JobType.SNAPSHOT,
            {"scope": "default", "requested_as_of": None},
        ),
        (JobType.DEBATE, {"asset": "BTC", "horizon": "1d"}),
        (JobType.REPLAY, {"session_id": "session-1"}),
        (
            JobType.BACKTEST,
            {
                "asset": "AAPL",
                "strategy_id": "legacy-binary-report-v1",
                "date_from": None,
                "date_to": None,
            },
        ),
        (JobType.BACKTEST, _engine_backtest_wire_payload()),
    )


def _job_metadata_wire(
    job_type: JobType, payload: dict[str, object]
) -> dict[str, object]:
    _, job, *_ = _api_metadata_models()
    value = job.model_dump(mode="json")
    value["job_type"] = job_type.value
    value["payload"] = payload
    return value


def _job_metadata_schema_samples() -> list[dict[str, object]]:
    return [
        {
            "expected": job_type is payload_job_type,
            "value": _job_metadata_wire(job_type, payload),
        }
        for job_type in JobType
        for payload_job_type, payload in _job_payload_wire_forms()
    ]


def test_payload_fingerprint_is_order_independent():
    left = parse_payload(
        JobType.SNAPSHOT,
        {"scope": "default", "requested_as_of": None},
    )
    right = parse_payload(
        JobType.SNAPSHOT,
        {"requested_as_of": None, "scope": "default"},
    )
    assert payload_fingerprint(left) == payload_fingerprint(right)


def test_canonical_payload_json_is_deterministic_and_compact():
    payload = parse_payload(
        JobType.SNAPSHOT,
        {"requested_as_of": None, "scope": "default"},
    )
    canonical = canonical_payload_json(payload)

    assert canonical == '{"requested_as_of":null,"scope":"default"}'
    assert payload_fingerprint(payload) == hashlib.sha256(canonical.encode()).hexdigest()
    assert json.loads(canonical) == payload.model_dump(mode="json")


@pytest.mark.parametrize(
    "field",
    ["executable", "module", "command", "argv", "shell", "cwd", "environment", "output_path", "timeout"],
)
def test_command_fields_are_forbidden(field):
    with pytest.raises(ValidationError):
        parse_payload(
            JobType.SNAPSHOT,
            {"scope": "default", "requested_as_of": None, field: "x"},
        )


def test_unknown_job_type_is_rejected():
    with pytest.raises(ValueError, match="unknown job type"):
        parse_payload("SHELL", {})


def test_canonical_input_larger_than_8_kib_is_rejected_before_model_validation():
    with pytest.raises(PayloadTooLarge, match="8192"):
        parse_payload(
            JobType.SNAPSHOT,
            {
                "scope": "default",
                "requested_as_of": None,
                "padding": "x" * 8192,
            },
        )


@pytest.mark.parametrize(
    "asset",
    ["UNKNOWN", "BTC;rm", "BTC\nETH", "--BTC", "../BTC", "BTC/../../etc", "\u00a0BTC"],
)
def test_unknown_or_unsafe_assets_are_rejected(asset):
    with pytest.raises(ValidationError):
        parse_payload(JobType.DEBATE, {"asset": asset, "horizon": "1d"})


@pytest.mark.parametrize("raw", ["btc", " BTC ", "NvDa"])
def test_asset_symbols_are_canonicalized_stably(raw):
    expected = raw.strip().upper()
    payload = parse_payload(JobType.DEBATE, {"asset": raw, "horizon": "1d"})
    assert payload.asset == expected


def test_contract_registry_has_the_exact_17_canonical_phase1_symbols():
    assert tuple(CANONICAL_ASSET_REGISTRY) == (
        "BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC",
        "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
    )
    assert APPROVED_ASSET_SYMBOLS == frozenset(CANONICAL_ASSET_REGISTRY)
    assert tuple(CANONICAL_ASSET_REGISTRY) == (
        PHASE1_CRYPTO_SYMBOLS + PHASE1_CANONICAL_EQUITY_SYMBOLS
    )
    assert "SPY" not in APPROVED_ASSET_SYMBOLS
    assert "QQQ" not in APPROVED_ASSET_SYMBOLS


@pytest.mark.parametrize("session_id", ["../session", "/tmp/session", "session/name", "--session", "a\nb", "a;whoami", "a b"])
def test_replay_rejects_paths_traversal_flags_and_metacharacters(session_id):
    with pytest.raises(ValidationError):
        parse_payload(JobType.REPLAY, {"session_id": session_id})


def test_replay_accepts_only_a_bounded_opaque_session_id():
    payload = parse_payload(JobType.REPLAY, {"session_id": "Session_2026-07-12"})
    assert payload.session_id == "Session_2026-07-12"
    with pytest.raises(ValidationError):
        parse_payload(JobType.REPLAY, {"session_id": "a" * 129})


def test_unsupported_debate_horizon_is_rejected():
    with pytest.raises(ValidationError):
        parse_payload(JobType.DEBATE, {"asset": "BTC", "horizon": "7d"})


@pytest.mark.parametrize("field", ["date_from", "date_to"])
def test_backtest_date_ranges_are_rejected(field):
    value = {
        "asset": "AAPL",
        "strategy_id": "legacy-binary-report-v1",
        "date_from": None,
        "date_to": None,
    }
    value[field] = "2026-01-01"
    with pytest.raises(ValidationError):
        parse_payload(JobType.BACKTEST, value)


def test_backtest_payload_accepts_a_distinct_strict_engine_authority_form():
    payload = parse_payload(
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

    assert type(payload).__name__ == "EngineBacktestPayload"
    assert set(payload.model_dump(mode="json")) == {"engine_backtest"}


def test_payload_models_are_strict_frozen_and_selected_by_type():
    cases = [
        (JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None}, SnapshotPayload),
        (JobType.DEBATE, {"asset": "ETH", "horizon": "1d"}, DebatePayload),
        (JobType.REPLAY, {"session_id": "session-1"}, ReplayPayload),
        (
            JobType.BACKTEST,
            {
                "asset": "MSFT",
                "strategy_id": "legacy-binary-report-v1",
                "date_from": None,
                "date_to": None,
            },
            BacktestPayload,
        ),
    ]
    for job_type, value, expected_type in cases:
        model = parse_payload(job_type, value)
        assert type(model) is expected_type
        with pytest.raises(ValidationError):
            model.__setattr__(next(iter(type(model).model_fields)), "changed")


def _api_metadata_models():
    now = datetime(2026, 7, 12, tzinfo=UTC)
    actor = ActorIdentity(actor_type=ActorType.OPERATOR, actor_id="operator-1")
    payload = SnapshotPayload(scope="default", requested_as_of=None)
    job = JobMetadata(
        job_id="job-1",
        job_type=JobType.SNAPSHOT,
        state=JobState.QUEUED,
        payload=payload,
        payload_fingerprint="a" * 64,
        actor=actor,
        priority=0,
        requested_at=now,
        updated_at=now,
        attempt_count=0,
        reason_code="ENQUEUED",
        result_hash=None,
    )
    attempt = AttemptMetadata(
        attempt_id="attempt-1",
        attempt_number=1,
        worker_id="worker-1",
        claimed_at=now,
        started_at=None,
        finished_at=None,
        exit_code=None,
        termination_reason=None,
        artifact_count=0,
    )
    event = EventMetadata(
        event_id="event-1",
        sequence=1,
        from_state=None,
        to_state=JobState.QUEUED,
        reason_code="ENQUEUED",
        actor=actor,
        trace_id="trace-1",
        created_at=now,
    )
    artifact = ArtifactMetadata(
        artifact_id="artifact-1",
        attempt_id="attempt-1",
        artifact_type="MARKET_REPORT",
        validator_id="market-report-v1",
        sha256="c" * 64,
        size_bytes=100,
        created_at=now,
    )
    return actor, job, attempt, event, artifact


def test_enqueue_request_parses_the_payload_selected_by_job_type():
    actor, *_ = _api_metadata_models()
    request = EnqueueJobRequest(
        job_type=JobType.DEBATE,
        payload={"asset": "btc", "horizon": "1d"},
        idempotency_key="operator-20260712-1",
        actor=actor,
        priority=10,
    )
    assert isinstance(request.payload, DebatePayload)
    assert request.payload.asset == "BTC"


@pytest.mark.parametrize(
    "idempotency_key",
    (
        "schedule:snapshot:2026-07-16T12:00Z",
        "schedule:operator-owned",
    ),
)
def test_public_enqueue_body_reserves_the_scheduler_namespace(
    idempotency_key: str,
) -> None:
    with pytest.raises(ValidationError):
        EnqueueJobBody.model_validate(
            {
                "job_type": "SNAPSHOT",
                "payload": {"scope": "default", "requested_as_of": None},
                "idempotency_key": idempotency_key,
                "priority": 0,
            }
        )


def test_internal_scheduler_request_accepts_only_its_exact_identity() -> None:
    valid = {
        "job_type": "SNAPSHOT",
        "payload": {"scope": "default", "requested_as_of": None},
        "idempotency_key": "schedule:snapshot:2026-07-16T12:00Z",
        "actor": {"actor_type": "SCHEDULER", "actor_id": "scheduler-one"},
        "priority": 0,
    }
    request = EnqueueJobRequest.model_validate(valid)
    assert request.actor.actor_type is ActorType.SCHEDULER

    invalid = (
        {**valid, "idempotency_key": "schedule:snapshot:not-a-slot"},
        {**valid, "idempotency_key": "manual:snapshot:2026-07-16T12:00Z"},
        {**valid, "actor": {"actor_type": "OPERATOR", "actor_id": "operator-one"}},
        {**valid, "job_type": "DEBATE", "payload": {"asset": "BTC", "horizon": "1d"}},
        {**valid, "priority": 1},
    )
    for value in invalid:
        with pytest.raises(ValidationError):
            EnqueueJobRequest.model_validate(value)


@pytest.mark.parametrize(
    ("expected", "idempotency_key"),
    (
        (True, "schedule:snapshot:2026-07-16T12:00Z"),
        (True, "schedule:snapshot:2026-04-30T12:00Z"),
        (True, "schedule:snapshot:0001-01-01T12:00Z"),
        (True, "schedule:snapshot:9999-12-31T12:00Z"),
        (True, "schedule:snapshot:2000-02-29T12:00Z"),
        (False, "schedule:snapshot:2026-02-31T12:00Z"),
        (False, "schedule:snapshot:0000-01-01T12:00Z"),
        (False, "schedule:snapshot:1900-02-29T12:00Z"),
    ),
)
def test_internal_scheduler_runtime_enforces_exact_gregorian_calendar(
    expected: bool,
    idempotency_key: str,
) -> None:
    value = {
        "job_type": "SNAPSHOT",
        "payload": {"scope": "default", "requested_as_of": None},
        "idempotency_key": idempotency_key,
        "actor": {"actor_type": "SCHEDULER", "actor_id": "scheduler-one"},
        "priority": 0,
    }

    if expected:
        assert EnqueueJobRequest.model_validate(value).idempotency_key == idempotency_key
    else:
        with pytest.raises(ValidationError):
            EnqueueJobRequest.model_validate(value)


def test_public_enqueue_json_schema_reserves_scheduler_namespace() -> None:
    common = {
        "job_type": "SNAPSHOT",
        "payload": {"scope": "default", "requested_as_of": None},
        "priority": 0,
    }
    cases = (
        (
            True,
            {
                **common,
                "idempotency_key": (
                    "dashboard:snapshot:0123456789abcdef0123456789abcdef"
                ),
            },
        ),
        (
            False,
            {
                **common,
                "idempotency_key": "schedule:snapshot:2026-07-16T12:00Z",
            },
        ),
        (False, {**common, "idempotency_key": "schedule:operator-owned"}),
    )
    samples = [
        {"expected": expected, "value": value} for expected, value in cases
    ]

    assert _ajv_schema_results(EnqueueJobBody.model_json_schema(), samples) == [
        {"expected": expected, "actual": expected} for expected, _ in cases
    ]


def test_enqueue_request_json_schema_enforces_scheduler_identity_coupling() -> None:
    snapshot = {"scope": "default", "requested_as_of": None}
    scheduled = {
        "job_type": "SNAPSHOT",
        "payload": snapshot,
        "idempotency_key": "schedule:snapshot:2026-07-16T12:00Z",
        "actor": {"actor_type": "SCHEDULER", "actor_id": "scheduler-one"},
        "priority": 0,
    }
    cases = (
        (True, scheduled),
        (
            True,
            {
                **scheduled,
                "idempotency_key": "schedule:snapshot:2026-04-30T12:00Z",
            },
        ),
        (
            True,
            {
                **scheduled,
                "idempotency_key": "schedule:snapshot:0001-01-01T12:00Z",
            },
        ),
        (
            True,
            {
                **scheduled,
                "idempotency_key": "schedule:snapshot:9999-12-31T12:00Z",
            },
        ),
        (
            True,
            {
                **scheduled,
                "idempotency_key": "schedule:snapshot:2000-02-29T12:00Z",
            },
        ),
        (
            False,
            {
                **scheduled,
                "idempotency_key": "schedule:snapshot:2026-02-31T12:00Z",
            },
        ),
        (
            False,
            {
                **scheduled,
                "idempotency_key": "schedule:snapshot:0000-01-01T12:00Z",
            },
        ),
        (
            False,
            {
                **scheduled,
                "idempotency_key": "schedule:snapshot:1900-02-29T12:00Z",
            },
        ),
        (
            False,
            {
                **scheduled,
                "idempotency_key": "schedule:snapshot:2026-07-16T12:00:00Z",
            },
        ),
        (
            False,
            {
                **scheduled,
                "idempotency_key": "schedule:snapshot:2026-07-16T12:60Z",
            },
        ),
        (
            False,
            {
                **scheduled,
                "idempotency_key": "schedule:debate:2026-07-16T12:00Z",
            },
        ),
        (
            False,
            {
                **scheduled,
                "actor": {
                    "actor_type": "OPERATOR",
                    "actor_id": "operator-one",
                },
            },
        ),
        (
            False,
            {
                **scheduled,
                "job_type": "DEBATE",
                "payload": {"asset": "BTC", "horizon": "1d"},
            },
        ),
        (False, {**scheduled, "priority": 1}),
        (
            False,
            {
                **scheduled,
                "idempotency_key": "manual:snapshot:one",
            },
        ),
        (
            True,
            {
                **scheduled,
                "idempotency_key": "manual:snapshot:one",
                "actor": {"actor_type": "OPERATOR", "actor_id": "operator-one"},
            },
        ),
    )
    samples = [
        {"expected": expected, "value": value} for expected, value in cases
    ]

    assert _ajv_schema_results(EnqueueJobRequest.model_json_schema(), samples) == [
        {"expected": expected, "actual": expected} for expected, _ in cases
    ]


def test_enqueue_request_json_schema_enforces_exact_job_payload_pairs_and_assets():
    """The published schema must reject combinations the runtime rejects."""

    schema = EnqueueJobRequest.model_json_schema()
    cases = [
        (
            True,
            {
                "job_type": "SNAPSHOT",
                "payload": {"scope": "default", "requested_as_of": None},
            },
        ),
        (
            True,
            {"job_type": "DEBATE", "payload": {"asset": " btc ", "horizon": "1d"}},
        ),
        (
            True,
            {"job_type": "REPLAY", "payload": {"session_id": "session-1"}},
        ),
        (
            True,
            {
                "job_type": "BACKTEST",
                "payload": {
                    "asset": "AAPL",
                    "strategy_id": "legacy-binary-report-v1",
                    "date_from": None,
                    "date_to": None,
                },
            },
        ),
        (
            True,
            {
                "job_type": "BACKTEST",
                "payload": _engine_backtest_wire_payload(),
            },
        ),
        (
            False,
            {"job_type": "SNAPSHOT", "payload": {"asset": "BTC", "horizon": "1d"}},
        ),
        (
            False,
            {"job_type": "DEBATE", "payload": {"scope": "default", "requested_as_of": None}},
        ),
        (
            False,
            {"job_type": "DEBATE", "payload": {"asset": "UNKNOWN", "horizon": "1d"}},
        ),
        (
            False,
            {"job_type": "BACKTEST", "payload": {"asset": "BTC;rm", "strategy_id": "legacy-binary-report-v1", "date_from": None, "date_to": None}},
        ),
        (
            False,
            {
                "job_type": "BACKTEST",
                "payload": {
                    **_engine_backtest_wire_payload(),
                    "asset": "BTC",
                },
            },
        ),
    ]
    actor_and_identity = {
        "idempotency_key": "contract-test",
        "actor": {"actor_type": "OPERATOR", "actor_id": "operator-1"},
        "priority": 0,
    }
    samples = [
        {"expected": expected, "value": {**value, **actor_and_identity}}
        for expected, value in cases
    ]
    results = _ajv_schema_results(schema, samples)
    assert results == [
        {"expected": expected, "actual": expected} for expected, _ in cases
    ]


def test_job_metadata_and_detail_runtime_enforce_every_job_payload_pair() -> None:
    for sample in _job_metadata_schema_samples():
        detail = {
            "job": sample["value"],
            "attempts": [],
            "events": [],
            "artifacts": [],
        }
        if sample["expected"]:
            JobMetadata.model_validate(sample["value"])
            JobDetail.model_validate(detail)
        else:
            with pytest.raises(ValidationError):
                JobMetadata.model_validate(sample["value"])
            with pytest.raises(ValidationError):
                JobDetail.model_validate(detail)


def test_job_metadata_and_detail_json_schemas_enforce_every_job_payload_pair() -> None:
    metadata_samples = _job_metadata_schema_samples()
    detail_samples = [
        {
            "expected": sample["expected"],
            "value": {
                "job": sample["value"],
                "attempts": [],
                "events": [],
                "artifacts": [],
            },
        }
        for sample in metadata_samples
    ]

    for schema, samples in (
        (JobMetadata.model_json_schema(mode="validation"), metadata_samples),
        (JobMetadata.model_json_schema(mode="serialization"), metadata_samples),
        (JobDetail.model_json_schema(mode="validation"), detail_samples),
        (JobDetail.model_json_schema(mode="serialization"), detail_samples),
    ):
        assert _ajv_schema_results(schema, samples) == [
            {"expected": sample["expected"], "actual": sample["expected"]}
            for sample in samples
        ]


def test_generated_openapi_responses_enforce_every_job_payload_pair() -> None:
    openapi = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "generated/job-api/openapi/openapi.json"
        ).read_text(encoding="utf-8")
    )
    schemas = openapi["components"]["schemas"]
    metadata_samples = _job_metadata_schema_samples()
    detail_samples = [
        {
            "expected": sample["expected"],
            "value": {
                "job": sample["value"],
                "attempts": [],
                "events": [],
                "artifacts": [],
            },
        }
        for sample in metadata_samples
    ]

    for component, samples in (
        ("JobMetadata", metadata_samples),
        ("JobDetail", detail_samples),
    ):
        root_schema = {
            "components": {"schemas": schemas},
            "$ref": f"#/components/schemas/{component}",
        }
        assert _ajv_schema_results(root_schema, samples) == [
            {"expected": sample["expected"], "actual": sample["expected"]}
            for sample in samples
        ]


@pytest.mark.parametrize(
    ("job_type", "payload"),
    [
        (job_type, payload)
        for job_type in JobType
        for payload in (
            SnapshotPayload(scope="default", requested_as_of=None),
            DebatePayload(asset="BTC", horizon="1d"),
            ReplayPayload(session_id="session-1"),
            BacktestPayload(
                asset="AAPL",
                strategy_id="legacy-binary-report-v1",
                date_from=None,
                date_to=None,
            ),
        )
        if {
            JobType.SNAPSHOT: SnapshotPayload,
            JobType.DEBATE: DebatePayload,
            JobType.REPLAY: ReplayPayload,
            JobType.BACKTEST: BacktestPayload,
        }[job_type]
        is not type(payload)
    ],
)
def test_job_metadata_rejects_every_job_type_payload_mismatch(job_type, payload):
    _, job, *_ = _api_metadata_models()
    value = job.model_dump()
    value["job_type"] = job_type
    value["payload"] = payload.model_dump()

    with pytest.raises(ValidationError):
        JobMetadata.model_validate(value)


@pytest.mark.parametrize(
    "forbidden",
    [
        "raw_output",
        "stdout",
        "stderr",
        "token",
        "lease_token",
        "environment",
        "request_headers",
        "command_fingerprint",
    ],
)
def test_api_dtos_forbid_sensitive_or_unbounded_fields(forbidden):
    _, job, attempt, event, artifact = _api_metadata_models()
    for model in (job, attempt, event, artifact):
        value = model.model_dump()
        value[forbidden] = "secret"
        with pytest.raises(ValidationError):
            type(model).model_validate(value)


def test_job_detail_contains_only_sanitized_metadata_models():
    _, job, attempt, event, artifact = _api_metadata_models()
    detail = JobDetail(
        job=job,
        attempts=[attempt],
        events=[event],
        artifacts=[artifact],
    )
    assert detail.job.job_id == "job-1"
    assert detail.attempts == (attempt,)
    assert detail.events == (event,)
    assert detail.artifacts == (artifact,)
