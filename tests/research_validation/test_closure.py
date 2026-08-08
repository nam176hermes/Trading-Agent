from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from packages.engine_contracts import (
    ArtifactReference,
    CURRENT_SCHEMA_VERSION,
    EngineCommandEnvelope,
    EngineEvent,
    EngineEventEnvelope,
    EventAttribute,
    EventFamily,
    RunBacktest,
    canonical_json_bytes,
    payload_digest,
)
from packages.nautilus_backtest import NautilusBacktestError, validate_isolated_backtest_result
from packages.research_validation import (
    ResearchClosureError,
    ResearchEvidenceArtifactError,
    ResearchEvidenceArtifactReference,
    canonical_evidence_artifact_bytes,
    close_ws04_research,
    load_verified_evidence,
)
from tests.research_validation.test_models import NOW, _digest, evidence
from tests.research_validation.test_models import _campaign_evidence, _scenario_comparison
import packages.research_validation as research
from packages.research_validation import closure as closure_module


@pytest.fixture
def secure_tmp_path() -> Path:
    path = Path(tempfile.mkdtemp(prefix="research-validation-", dir="/tmp"))
    try:
        yield path
    finally:
        for directory, _children, files in os.walk(path):
            Path(directory).chmod(0o700)
            for name in files:
                candidate = Path(directory) / name
                if not candidate.is_symlink():
                    candidate.chmod(0o600)
        shutil.rmtree(path)


def _request() -> EngineCommandEnvelope:
    references = (
        ArtifactReference(
            artifact_id=UUID("11111111-1111-4111-8111-111111111111"),
            sha256=_digest("e"),
            media_type="application/json",
        ),
        ArtifactReference(
            artifact_id=UUID("22222222-2222-4222-8222-222222222222"),
            sha256=_digest("f"),
            media_type="application/json",
        ),
        ArtifactReference(
            artifact_id=UUID("33333333-3333-4333-8333-333333333333"),
            sha256=_digest("1"),
            media_type="application/json",
        ),
        ArtifactReference(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            sha256=_digest("d"),
            media_type="application/jsonl",
        ),
    )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        start_time=NOW,
        end_time=NOW + timedelta(minutes=30),
    )
    return EngineCommandEnvelope(
        message_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        correlation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        causation_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        engine_run_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        stream_sequence=1,
        event_time=NOW,
        initialization_time=NOW,
        schema_version=CURRENT_SCHEMA_VERSION,
        producer_identity="worker-authority-1",
        source_commit="0" * 40,
        config_digest=payload_digest(
            {
                "engine_configuration": command.engine_configuration,
                "instrument_catalog": command.instrument_catalog,
                "strategy_configuration": command.strategy_configuration,
            }
        ),
        payload_digest=payload_digest(command),
        payload=command,
    )


def _event(request: EngineCommandEnvelope) -> EngineEventEnvelope:
    assert isinstance(request.payload, RunBacktest)
    inputs = hashlib.sha256(
        canonical_json_bytes(
            {
                "engine_configuration": request.payload.engine_configuration.sha256,
                "instrument_catalog": request.payload.instrument_catalog.sha256,
                "strategy_configuration": request.payload.strategy_configuration.sha256,
                "market_data": request.payload.market_data.sha256,
            }
        )
    ).hexdigest()
    payload = EngineEvent(
        event_type="NautilusBacktestCompleted",
        family=EventFamily.ENGINE_LIFECYCLE,
        attributes=(
            EventAttribute(name="input_artifacts_sha256", value=inputs),
            EventAttribute(name="iterations", value=2),
            EventAttribute(name="total_events", value=2),
            EventAttribute(name="total_orders", value=0),
            EventAttribute(name="total_positions", value=0),
        ),
    )
    return EngineEventEnvelope(
        message_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
        engine_run_id=request.engine_run_id,
        stream_sequence=request.stream_sequence + 1,
        event_time=request.event_time,
        initialization_time=request.initialization_time,
        schema_version=request.schema_version,
        producer_identity=request.producer_identity,
        source_commit=request.source_commit,
        config_digest=request.config_digest,
        payload_digest=payload_digest(payload),
        payload=payload,
    )


def _closure_evidence(request: EngineCommandEnvelope, event: EngineEventEnvelope):
    result = validate_isolated_backtest_result(request, event)
    base = evidence()
    provenance = base.provenance.model_copy(
        update={
            "backtest_input_artifacts_sha256": result.input_artifacts_sha256,
            "backtest_result_sha256": result.result_sha256,
            "backtest_event_sha256": hashlib.sha256(canonical_json_bytes(event)).hexdigest(),
            "source_commit": request.source_commit,
        }
    )
    comparisons = tuple(
        item.model_copy(
            update={
                "input_artifacts_sha256": result.input_artifacts_sha256,
                "result_sha256": result.result_sha256,
                "event_sha256": hashlib.sha256(canonical_json_bytes(event)).hexdigest(),
            }
        )
        for item in base.comparisons
    )
    return evidence(provenance=provenance, comparisons=comparisons)


def _authority(
    tmp_path: Path, supplied, *, artifact: bytes | None = None
) -> ResearchEvidenceArtifactReference:
    root = tmp_path / "sealed-evidence"
    root.mkdir(mode=0o700, parents=True)
    value = canonical_evidence_artifact_bytes(supplied) if artifact is None else artifact
    source = root / "ws04-evidence.json"
    source.write_bytes(value)
    source.chmod(0o400)
    root.chmod(0o500)
    return ResearchEvidenceArtifactReference(
        root=root,
        filename=source.name,
        sha256=hashlib.sha256(value).hexdigest(),
    )


def test_ws04_closure_is_available() -> None:
    assert close_ws04_research is not None


def test_ws04_closure_is_stable_for_exact_04a_to_04d_evidence(secure_tmp_path: Path) -> None:
    request = _request()
    event = _event(request)
    supplied = _closure_evidence(request, event)

    reference = _authority(secure_tmp_path, supplied)
    first = close_ws04_research(supplied.provenance.dataset, request, event, reference)
    second = close_ws04_research(supplied.provenance.dataset, request, event, reference)

    assert first.closure_sha256 == second.closure_sha256
    assert first.backtest_result_sha256 == supplied.provenance.backtest_result_sha256
    assert first.research_evidence_sha256 != first.research_report_sha256
    assert first.research_evidence_artifact_sha256 == reference.sha256
    assert first.research_analysis_output_sha256 == supplied.analysis_output_sha256
    assert first.market_data_sha256 != first.canonical_rows_sha256


def test_ws04_closure_rejects_tampered_engine_event(secure_tmp_path: Path) -> None:
    request = _request()
    event = _event(request).model_copy(update={"stream_sequence": 99})
    supplied = _closure_evidence(request, _event(request))

    with pytest.raises(NautilusBacktestError, match="bound"):
        close_ws04_research(
            supplied.provenance.dataset,
            request,
            event,
            _authority(secure_tmp_path, supplied),
        )


def test_ws04_closure_rejects_artifact_or_gate_drift(secure_tmp_path: Path) -> None:
    request = _request()
    event = _event(request)
    supplied = _closure_evidence(request, event)
    drifted_provenance = supplied.provenance.model_copy(
        update={"strategy_configuration_sha256": _digest("9")}
    )

    with pytest.raises(ResearchClosureError, match="artifact binding"):
        close_ws04_research(supplied.provenance.dataset, request, event, _authority(
            secure_tmp_path, supplied.model_copy(update={"provenance": drifted_provenance})
        ))

    wrong_commit = supplied.provenance.model_copy(update={"source_commit": "1" * 40})
    source_drift = evidence(
        provenance=wrong_commit,
        point_in_time=supplied.point_in_time,
        comparisons=supplied.comparisons,
    )
    with pytest.raises(ResearchClosureError, match="result provenance"):
        close_ws04_research(
            supplied.provenance.dataset,
            request,
            event,
            _authority(secure_tmp_path / "commit", source_drift),
        )

    future_known = supplied.point_in_time[0].model_copy(
        update={"known_at": NOW + timedelta(minutes=3), "decision_at": NOW + timedelta(minutes=2)}
    )
    failed_gates = evidence(
        point_in_time=(future_known,),
        provenance=supplied.provenance,
        comparisons=supplied.comparisons,
    )
    with pytest.raises(ResearchClosureError, match="gates did not pass"):
        close_ws04_research(
            supplied.provenance.dataset,
            request,
            event,
            _authority(secure_tmp_path / "failed", failed_gates),
        )


def test_ws04_closure_rejects_noncanonical_or_mismatched_evidence_artifact(
    secure_tmp_path: Path,
) -> None:
    request = _request()
    event = _event(request)
    supplied = _closure_evidence(request, event)
    artifact = canonical_evidence_artifact_bytes(supplied)

    with pytest.raises(ResearchEvidenceArtifactError, match="canonical"):
        close_ws04_research(
            supplied.provenance.dataset,
            request,
            event,
            _authority(secure_tmp_path, supplied, artifact=artifact + b" "),
        )

    with pytest.raises(TypeError, match="ResearchEvidenceArtifactReference"):
        close_ws04_research(supplied.provenance.dataset, request, event, supplied)

    reference = _authority(secure_tmp_path / "digest", supplied)
    wrong_digest = ResearchEvidenceArtifactReference(
        root=reference.root, filename=reference.filename, sha256="0" * 64
    )
    with pytest.raises(ResearchEvidenceArtifactError, match="digest drifted"):
        close_ws04_research(supplied.provenance.dataset, request, event, wrong_digest)

    trace = json.loads(artifact)
    trace["analysis_output_sha256"] = "0" * 64
    bad_trace = canonical_json_bytes(trace)
    with pytest.raises(ResearchEvidenceArtifactError, match="invalid"):
        close_ws04_research(
            supplied.provenance.dataset,
            request,
            event,
            _authority(secure_tmp_path / "trace", supplied, artifact=bad_trace),
        )


def test_evidence_authority_rejects_unsealed_source(secure_tmp_path: Path) -> None:
    supplied = evidence()
    root = secure_tmp_path / "unsealed"
    root.mkdir(mode=0o700)
    source = root / "ws04-evidence.json"
    source.write_bytes(canonical_evidence_artifact_bytes(supplied))
    source.chmod(0o600)
    root.chmod(0o500)
    reference = ResearchEvidenceArtifactReference(
        root=root,
        filename=source.name,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )

    with pytest.raises(ResearchEvidenceArtifactError, match="not sealed"):
        load_verified_evidence(reference)

    sealed = _authority(secure_tmp_path / "sealed", supplied)
    alias = secure_tmp_path / "alias"
    os.symlink(sealed.root, alias)
    with pytest.raises(ResearchEvidenceArtifactError, match="contains a symlink"):
        load_verified_evidence(
            ResearchEvidenceArtifactReference(
                root=alias, filename=sealed.filename, sha256=sealed.sha256
            )
        )


def _campaign_evidence_value():
    return _campaign_evidence(
        tuple(
            _scenario_comparison(scenario_id, index)
            for index, scenario_id in enumerate(research.PHASE4_SCENARIO_IDS)
        )
    )


def test_campaign_closure_returns_eight_scenario_proofs_and_one_stable_digest() -> None:
    evidence_v2 = _campaign_evidence_value()

    first = closure_module._close_ws04_research_campaign_evidence(evidence_v2)
    second = closure_module._close_ws04_research_campaign_evidence(evidence_v2)

    assert first.schema_version == "ws04-campaign-closure-v2"
    assert first.closure_sha256 == second.closure_sha256
    assert len(first.scenarios) == 8
    assert tuple(item.scenario_id for item in first.scenarios) == research.PHASE4_SCENARIO_IDS
    assert all(item.reference_result_sha256 == item.nautilus_result_sha256 for item in first.scenarios)
    assert all(item.legacy_selected is False for item in first.scenarios)


def test_campaign_closure_contract_requires_repository_ordered_eight_scenarios() -> None:
    value = closure_module._close_ws04_research_campaign_evidence(
        _campaign_evidence_value()
    )
    fields = value.model_dump(exclude={"closure_sha256"})

    with pytest.raises(ValueError, match="at least 8|eight-scenario"):
        research.Ws04CampaignClosureV2(
            **(fields | {"scenarios": value.scenarios[:-1]})
        )
    with pytest.raises(ValueError, match="ordered"):
        research.Ws04CampaignClosureV2(
            **(fields | {"scenarios": tuple(reversed(value.scenarios))})
        )


def test_campaign_closure_rejects_parity_drift_or_paper_binding_drift() -> None:
    value = _campaign_evidence_value()
    drifted = value.comparisons[0].model_copy(
        update={"nautilus_event_sha256": _digest("0")}
    )
    with pytest.raises(research.ResearchClosureError, match="campaign.*gates"):
        closure_module._close_ws04_research_campaign_evidence(
            _campaign_evidence((drifted, *value.comparisons[1:]))
        )

    paper = research.PaperCompatibilityResultV1.create(
        candidate_closure_sha256=value.paper_result.candidate_closure_sha256,
        candidate_manifest_sha256=value.paper_result.candidate_manifest_sha256,
        engine_configuration_sha256=value.paper_result.engine_configuration_sha256,
        instrument_catalog_sha256=value.paper_result.instrument_catalog_sha256,
        strategy_configuration_sha256=value.paper_result.strategy_configuration_sha256,
        strategy_source_sha256=value.paper_result.strategy_source_sha256,
        scenario_campaign_sha256=_digest("0"),
        parity_record_sha256=value.paper_result.parity_record_sha256,
        launcher_result_sha256=value.paper_result.launcher_result_sha256,
    )
    with pytest.raises(research.ResearchClosureError, match="campaign.*gates"):
        closure_module._close_ws04_research_campaign_evidence(
            _campaign_evidence(value.comparisons, paper_result=paper)
        )


def test_v1_closure_semantics_remain_exact_after_campaign_api_is_added(
    secure_tmp_path: Path,
) -> None:
    request = _request()
    event = _event(request)
    supplied = _closure_evidence(request, event)
    before = close_ws04_research(
        supplied.provenance.dataset,
        request,
        event,
        _authority(secure_tmp_path, supplied),
    )

    after = close_ws04_research(
        supplied.provenance.dataset,
        request,
        event,
        _authority(secure_tmp_path / "again", supplied),
    )

    assert before == after
    assert before.schema_version == "ws04-closure-v1"


def test_public_campaign_closer_rejects_a_caller_constructed_evidence_object() -> None:
    with pytest.raises(TypeError):
        research.close_ws04_research_campaign(_campaign_evidence_value())
