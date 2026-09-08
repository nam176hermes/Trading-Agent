"""Source tests for the real pinned spawn path; these are not native proof."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import io

import pytest

from packages.engine_contracts import canonical_json_bytes, EventAttribute, payload_digest
from packages.job_contracts import JobType
from packages.nautilus_runtime_contracts.result import _decode_event
from packages.nautilus_runtime_contracts import event_message_id, semantic_digest
from services.job_worker.artifacts import ArtifactWriter
from services.job_worker.p1_engine_spawn import P1EngineSpawnProvider
from tests.jobs.test_worker_lifecycle import claim, outcome, safety_evidence
from tests.nautilus_runtime_contracts.test_result import _batch, _p1_request
from tests.p3.test_job_api import _alpha_request


@pytest.mark.parametrize("fault", [None, "truncated", "tampered", "malformed", "duplicate_json", "lineage", "divergence", "second_spawn"])
def test_native_fixture_uses_three_pinned_spawns_and_distinct_artifacts(tmp_path, monkeypatch, fault):
    from services.job_worker import p3_fixture_native as native

    payload = _alpha_request().payload.model_copy(update={
        "operation": "PARITY", "logical_trial_id": "p3-integration-fixture-v1",
    })
    job = replace(claim(max_attempts=1), job_id="job_" + "1" * 32,
                  attempt_id="attempt_" + "2" * 32, job_type=JobType.ALPHA_CAMPAIGN,
                  payload=payload, lease_expires_at=datetime.now(UTC) + timedelta(minutes=5))
    provider = object.__new__(P1EngineSpawnProvider)
    requests, roots = [], []
    monkeypatch.setattr(provider, "prepare", lambda request: requests.append(request) or request)

    class Runner:
        def __init__(self, writer):
            self.writer = writer
            roots.append(writer.root)

        def run(self, prepare, environment, timeout, heartbeat, **kwargs):
            request = prepare()
            if fault == "second_spawn" and len(requests) == 2:
                raise RuntimeError("second spawn refused")
            assert environment is None and timeout is None
            _, original = _batch()
            decoded = tuple(_decode_event(event) for event in original)
            decoded = tuple(event.model_copy(update={
                "closure_digest": native.P1_REAL_BACKTEST_POLICY.closure_sha256,
            }) if event.event_type in {"RunStarted", "RunCompleted"} else event for event in decoded)
            if fault == "divergence" and len(requests) == 2:
                decoded = tuple(event.model_copy(update={"simulation_time": event.simulation_time + timedelta(seconds=1)}) for event in decoded)
            decoded = (*decoded[:-1], decoded[-1].model_copy(update={
                "semantic_digest": semantic_digest(decoded),
            }))
            events = []
            for event, decoded_event in zip(original, decoded, strict=True):
                fields = decoded_event.model_dump(mode="json")
                attributes = tuple(EventAttribute(name=name, value=(canonical_json_bytes(value).decode()
                    if isinstance(value, list) else value)) for name, value in fields.items()
                    if name != "event_type" and value is not None)
                body = event.payload.model_copy(update={"attributes": attributes})
                events.append(event.model_copy(update={
                    "message_id": event_message_id(request.message_id, decoded_event),
                    "correlation_id": request.correlation_id, "causation_id": request.causation_id,
                    "engine_run_id": request.engine_run_id, "event_time": request.event_time,
                    "initialization_time": request.initialization_time,
                    "producer_identity": request.producer_identity, "source_commit": request.source_commit,
                    "payload": body, "payload_digest": payload_digest(body),
                }))
            if fault == "lineage":
                events[0] = events[0].model_copy(update={"engine_run_id": request.message_id})
            raw = b"".join(canonical_json_bytes(event) + b"\n" for event in events)
            if fault == "malformed":
                raw = b"not json\n"
            if fault == "duplicate_json":
                raw = raw.replace(b'{', b'{"stream_sequence":2,', 1)
            stream = self.writer.capture_stream(job.job_id, job.attempt_id, "stdout", io.BytesIO(raw))
            if fault == "truncated":
                stream = replace(stream, truncated=True)
            if fault == "tampered":
                stream = replace(stream, sha256="0" * 64)
            return replace(outcome(), stdout=stream, result_validator_id="nautilus-p1-event-stream-v1")

    monkeypatch.setattr(native, "ProcessRunner", Runner)
    if fault:
        with pytest.raises((RuntimeError, ValueError)) as caught:
            native.run_native_fixture(
                job, _p1_request().payload, provider, artifact_root=tmp_path / "replicas",
                heartbeat=lambda _: None, preflight=lambda: safety_evidence("4" * 64),
            )
        assert len(requests) == (2 if fault in {"divergence", "second_spawn"} else 1)
        if fault == "second_spawn":
            assert len(caught.value.completed) == 1
            assert caught.value.outcome is caught.value.completed[0].outcome
        return
    results = native.run_native_fixture(
        job, _p1_request().payload, provider, artifact_root=tmp_path / "replicas",
        heartbeat=lambda _: None, preflight=lambda: safety_evidence("4" * 64),
    )
    assert all(result.request == request for result, request in zip(results, requests, strict=True))
    assert len(results) == len(requests) == len(set(roots)) == 3
    assert len({request.engine_run_id for request in requests}) == 3
    assert len({result.result.semantic_sha256 for result in results}) == 1
    assert all(request.source_commit == payload.expected_source.commit_sha for request in requests)


@pytest.mark.parametrize("failure", ["exit", "cleanup", "validator"])
def test_native_fixture_stops_before_next_spawn_on_invalid_outcome(tmp_path, monkeypatch, failure):
    from services.job_worker import p3_fixture_native as native

    payload = _alpha_request().payload.model_copy(update={
        "operation": "PARITY", "logical_trial_id": "p3-integration-fixture-v1",
    })
    job = replace(claim(max_attempts=1), job_type=JobType.ALPHA_CAMPAIGN, payload=payload)
    provider = object.__new__(P1EngineSpawnProvider)
    calls = []

    class Runner:
        def __init__(self, writer):
            pass

        def run(self, *args, **kwargs):
            calls.append(1)
            return replace(outcome(),
                exit_code=1 if failure == "exit" else 0,
                termination_reason="SESSION_CLEANUP_FAILED" if failure == "cleanup" else None,
                result_validator_id="other" if failure == "validator" else "nautilus-p1-event-stream-v1")

    monkeypatch.setattr(native, "ProcessRunner", Runner)
    with pytest.raises(native.NativeFixtureError):
        native.run_native_fixture(job, _p1_request().payload, provider,
            artifact_root=tmp_path / "replicas", heartbeat=lambda _: None,
            preflight=lambda: safety_evidence("4" * 64))
    assert len(calls) == 1
