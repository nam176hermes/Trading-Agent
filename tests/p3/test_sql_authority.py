import hashlib
import importlib.util
import json
import pytest
from pathlib import Path
from uuid import UUID

from packages.alpha_lifecycle.contracts.lifecycle import PublicationRequest
from packages.alpha_lifecycle.registry import AlphaLifecycleStatus, AlphaRecordV1, QualificationDecision
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_sql import DomainAppendEntry, PublicationProposal, PublicationTransport
from services.job_worker.results import validate_p3_result_bytes


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic/versions/0020_p3_alpha_campaign_authority.py"


def test_migration_routes_plpgsql_percent_tokens_through_alembic_execute(
    monkeypatch,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "p3_alpha_campaign_migration", MIGRATION
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class Recorder:
        statement = ""

        def execute(self, statement: str) -> None:
            self.statement = statement

    recorder = Recorder()
    monkeypatch.setattr(migration, "op", recorder)
    migration.upgrade()

    assert "%ROWTYPE" in recorder.statement


def test_migration_closes_p3_publication_behind_narrow_capabilities() -> None:
    source = MIGRATION.read_text()
    assert 'revision = "0020_p3_alpha_campaign_authority"' in source
    assert 'down_revision = "0019_p2_security_master"' in source
    for name in (
        "api_enqueue_alpha_campaign",
        "worker_claim_alpha_campaign",
        "worker_commit_alpha_campaign",
        "read_alpha_commit",
        "api_cancel_alpha_campaign",
    ):
        assert f"CREATE FUNCTION job_plane.{name}" in source
        assert f"REVOKE ALL PRIVILEGES ON FUNCTION\n          job_plane.{name}" in source
    for table in (
        "p3_alpha_heads",
        "p3_alpha_job_commits",
        "p3_alpha_projection",
        "p3_campaign_authorizations",
    ):
        assert f"CREATE TABLE public.{table}" in source
    commit = source.split("AS $worker_commit_alpha_campaign$", 1)[1].split(
        "$worker_commit_alpha_campaign$;", 1
    )[0]
    assert commit.index("FROM public.jobs") < commit.index("FROM public.job_attempts")
    assert commit.count("FOR UPDATE") >= 3
    assert "clock_timestamp()" in commit
    assert "ORDER BY" in commit and "alpha_id" in commit and "alpha_version" in commit
    assert "public.append_domain_event" in commit
    assert "P3 illegal registry transition" in commit
    assert "P3 registry predecessor or identity rejected" in commit
    assert "P3 registration batch must contain four IDEA/CANDIDATE pairs" in commit
    assert "P3 outbox payload rejected" in commit
    assert "INSERT INTO public.p3_alpha_job_commits" in commit
    assert "UPDATE public.jobs" in commit
    assert "UPDATE public.job_attempts" in commit
    assert "SET search_path = pg_catalog" in source.split(
        "AS $worker_commit_alpha_campaign$", 1
    )[0].rsplit("CREATE FUNCTION", 1)[1]
    for privilege in ("INSERT", "UPDATE", "DELETE"):
        assert f"GRANT {privilege} ON" not in "\n".join(
            line for line in source.splitlines() if "trading_job_worker" in line
        )


def _ref(character: str) -> ArtifactRefV1:
    digest = character * 64
    return ArtifactRefV1(
        content_sha256=digest,
        size_bytes=1,
        media_type="application/json",
        locator=f"{digest}.blob",
    )


def _request() -> PublicationRequest:
    payload = {
        "schema_version": "p3-publication-request-v1",
        "idempotency_key": "register-a0",
        "semantic_request_digest": "a" * 64,
        "stage": "REGISTER",
        "evidence_ref": _ref("b"),
        "expected_heads": ({"alpha_id": "a0.test", "version": "1.0.0", "sequence": 0, "event_digest": None},),
        "proposed_event_refs": (_ref("c"),),
        "job_id": "job-1",
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return PublicationRequest.model_validate(payload)


def _entry() -> DomainAppendEntry:
    event_id = UUID("11111111-1111-5111-8111-111111111111")
    stream_id = UUID("22222222-2222-5222-8222-222222222222")
    record = AlphaRecordV1(
        alpha_id="a0.test", version="1.0.0", source_sha="a" * 40,
        implementation_identity="packages.alpha_lifecycle.candidates:run_candidate",
        dataset_snapshot_sha256="b" * 64, feature_set=("close",),
        parameter_set_sha256="c" * 64,
        training_start_at="2020-01-01T00:00:00Z",
        training_end_at="2021-01-01T00:00:00Z",
        validation_start_at="2021-01-02T00:00:00Z",
        validation_end_at="2022-01-01T00:00:00Z",
        oos_start_at="2022-01-02T00:00:00Z",
        oos_end_at="2023-01-01T00:00:00Z",
        universe=("BTCUSDT.BINANCE",), cost_model_sha256="d" * 64,
        baseline_id="B0_CASH", baseline_version="1.0.0",
        metrics_sha256=None, robustness_sha256=None,
        qualification_decision=QualificationDecision.NOT_EVALUATED,
        qualification_reason="preregistered", artifact_digests=("e" * 64,),
        lineage=("p3-btc-d1-e1",), superseded_version=None,
        lifecycle_status=AlphaLifecycleStatus.IDEA,
    )
    registry_event = {
        "predecessor_sha256": None,
        "record": record,
        "schema_version": "alpha-registry-event-v1",
        "sequence": 1,
    }
    registry_event_text = canonical_json_bytes(registry_event).decode()
    event = {
        "causation_id": "33333333-3333-5333-8333-333333333333",
        "correlation_id": "44444444-4444-5444-8444-444444444444",
        "effective_at": "2026-09-05T00:00:00Z",
        "event_id": str(event_id),
        "event_type": "AlphaRegistryTransitionRecordedV1",
        "expires_at": "2026-09-06T00:00:00Z",
        "ingested_at": "2026-09-05T00:00:00Z",
        "observed_at": "2026-09-05T00:00:00Z",
        "payload": {
            "alpha_id": "a0.test",
            "alpha_version": "1.0.0",
            "epoch_id": "p3-btc-d1-e1",
            "evidence_sha256": "d" * 64,
            "predecessor_sha256": None,
            "registry_event_sha256": hashlib.sha256(registry_event_text.encode()).hexdigest(),
            "registry_event_text": registry_event_text,
            "registry_sequence": 1,
            "schema_version": "alpha-registry-transition-recorded-v1",
        },
        "produced_at": "2026-09-05T00:00:00Z",
        "schema_version": "event-envelope-v1",
        "sequence": 1,
        "source": "p3-alpha-lifecycle",
        "stream_id": str(stream_id),
        "trace_id": "55555555-5555-5555-8555-555555555555",
    }
    return DomainAppendEntry(
        event_id=event_id,
        stream_id=stream_id,
        sequence=1,
        event_type="AlphaRegistryTransitionRecordedV1",
        canonical_event_text=canonical_json_bytes(event).decode(),
        topic="p3.alpha-registry",
        outbox_payload_text=canonical_json_bytes({"event_id": str(event_id)}).decode(),
    )


def test_private_transport_is_closed_and_size_bounded() -> None:
    entry = _entry()
    payload = json.loads(entry.canonical_event_text)['payload']
    request_value = _request().model_dump(mode='json', exclude={'digest'})
    request_value.update(evidence_ref=_ref('d').model_dump(mode='json'), proposed_event_refs=[dict(
        content_sha256=payload['registry_event_sha256'],
        size_bytes=len(payload['registry_event_text'].encode()), media_type='application/json',
        locator=payload['registry_event_sha256']+'.blob')])
    request_value['digest'] = hashlib.sha256(canonical_json_bytes(request_value)).hexdigest()
    request = PublicationRequest.model_validate_json(canonical_json_bytes(request_value))
    transport = PublicationTransport(
        job_id=request.job_id,
        attempt_id="attempt-1",
        worker_id="worker-1",
        lease_token="abcdefghijklmnop",
        request=request,
        entries=(entry,),
    )
    encoded = transport.canonical_bytes()
    assert len(encoded) <= 1_048_576
    assert PublicationTransport.from_canonical_bytes(encoded) == transport


@pytest.mark.parametrize('fault', ['order', 'size', 'media', 'evidence', 'count'])
def test_publication_transport_binds_ordered_artifacts(tmp_path, fault):
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from services.job_worker.p3_operation_fixture import publication_entries, _sealed
    store = LocalArtifactStore(tmp_path)
    evidence = store.put_bytes(b'{}', media_type='application/json')
    entries, refs, heads = publication_entries(store, evidence, ('a0.test',))
    request = _request().model_dump(mode='json')
    request.update(evidence_ref=evidence.model_dump(mode='json'), expected_heads=heads,
                   proposed_event_refs=[r.model_dump(mode='json') for r in refs])
    if fault == 'order':
        request['proposed_event_refs'].reverse()
    elif fault == 'size':
        request['proposed_event_refs'][0]['size_bytes'] += 1
    elif fault == 'media':
        request['proposed_event_refs'][0]['media_type'] = 'text/plain'
    elif fault == 'evidence':
        request['evidence_ref'] = _ref('f').model_dump(mode='json')
    else:
        request['proposed_event_refs'].pop()
    request = PublicationRequest.model_validate_json(_sealed(request))
    with pytest.raises(ValueError, match='publication artifact'):
        PublicationTransport(job_id=request.job_id, attempt_id='attempt-1',
            worker_id='worker-1', lease_token='abcdefghijklmnop', request=request, entries=entries)


def test_unprivileged_publication_proposal_is_validated_before_worker_commit() -> None:
    proposal = PublicationProposal(request=_request(), entries=(_entry(),))
    parsed = validate_p3_result_bytes(
        "p3-publication-register-v1", canonical_json_bytes(proposal)
    )
    assert parsed == proposal


def test_sql_enqueue_attempt_cap_matches_the_fixed_p3_command() -> None:
    import re
    from types import SimpleNamespace

    from services.job_worker.command_registry import p3_command_spec

    source = MIGRATION.read_text()
    enqueue = source.split('AS $api_enqueue_alpha_campaign$', 1)[1].split(
        '$api_enqueue_alpha_campaign$;', 1
    )[0]
    insert = enqueue.split('INSERT INTO public.jobs(', 1)[1].split(
        'ON CONFLICT', 1
    )[0]
    assert insert.split(') VALUES', 1)[0].strip().endswith('priority,max_attempts')
    attempts = re.search(r"'OPERATOR',p_actor_id,p_priority,(\d+)\s*\)", insert)
    assert attempts is not None
    spec = p3_command_spec(SimpleNamespace(
        operation='OOS', logical_trial_id='p3-oos-a0-v1'
    ))
    assert int(attempts.group(1)) == spec.max_attempts == 1


def test_migration_scopes_schema_creation_to_owner_transfer_only() -> None:
    source = MIGRATION.read_text()
    grant = 'GRANT USAGE,CREATE ON SCHEMA public,job_plane TO trading_p3_owner;'
    revoke = 'REVOKE CREATE ON SCHEMA public,job_plane FROM trading_p3_owner;'
    assert grant in source
    assert source.index(grant) < source.index('ALTER TABLE public.p3_alpha_heads OWNER TO')
    assert source.index(revoke) > source.index('ALTER FUNCTION job_plane.worker_start_alpha_campaign')


def test_migration_retains_alembic_search_path_and_binds_authorized_input() -> None:
    source = MIGRATION.read_text()
    preflight = source.split('$p3_preflight$')[1]
    assert "set_config('search_path'" not in preflight
    assert "IF NOT (CASE" in source and "ELSE false END) THEN" in source
    assert "a.input_set_digest = v_payload #>> '{manifest_ref,content_sha256}'" in source
    assert "a.input_set_digest=v_job.payload#>>'{manifest_ref,content_sha256}'" in source
    assert source.index('REVOKE ALL PRIVILEGES ON TABLE public.p3_alpha_heads') < source.index('ALTER TABLE public.p3_alpha_heads OWNER TO')
    assert "CREATE POLICY p3_owner_jobs" in source
    assert "USING (job_type='ALPHA_CAMPAIGN') WITH CHECK (job_type='ALPHA_CAMPAIGN')" in source


def test_sql_source_check_requires_explicit_manual_selection(monkeypatch) -> None:
    import pytest
    from scripts import check_p3_sql_source

    calls = []
    monkeypatch.setattr(check_p3_sql_source, 'run_sql_source_check', lambda: calls.append(True))
    with pytest.raises(SystemExit) as refused:
        check_p3_sql_source.main([])
    assert refused.value.code == 2 and calls == []
    check_p3_sql_source.main(['--run-disposable-sql'])
    assert calls == [True]
