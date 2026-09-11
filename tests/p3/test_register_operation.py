"""Registration stages exactly the approved whole family after baseline replay."""
from datetime import UTC, datetime, timedelta
import hashlib
import json

import pytest

from packages.alpha_lifecycle.operation_input import P3OperationInput, FAMILY_IDS
from packages.engine_contracts.serialization import canonical_json_bytes
from scripts.generate_p3_specs import _candidate_specs, POLICY_SOURCE
from tests.p3.test_registration_proof import registration_chain


@pytest.mark.parametrize('fault',[None,'duplicate_spec','record_source','records_order','missing_baseline'])
def test_register_operation_binds_sealed_family_before_proposing(retained_baseline,fault):
    from services.job_worker.p3_publication_producer import prepare_family_registration
    store,evidence,expected,_ = registration_chain(retained_baseline,
        record_fault='source_sha' if fault == 'record_source' else None,
        dangling_selection=fault=='missing_baseline')
    specs = [store.put_bytes(canonical_json_bytes(s),media_type='application/json')
        for s in _candidate_specs(json.loads(POLICY_SOURCE.read_bytes()))]
    if fault == 'duplicate_spec':
        specs[-1] = specs[0]
    records = evidence.candidate_record_refs
    if fault == 'records_order':
        records = tuple(reversed(records))
    value = dict(schema_version='p3-operation-input-v1',workflow_operation='p3-register-family-v1',
        operation='REGISTER_FAMILY',input_set_ref=evidence.input_set_ref,allowed_alpha_ids=FAMILY_IDS,
        body=dict(baseline_selection_ref=evidence.baseline_selection_ref,candidate_spec_refs=specs,candidate_record_refs=records))
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    intent = P3OperationInput.model_validate_json(canonical_json_bytes(value))
    now = datetime(2026,9,10,tzinfo=UTC)
    args = dict(job_id='job_registration',observed_at=now,expires_at=now+timedelta(hours=1),store=store)
    if fault is None:
        assert prepare_family_registration(intent,**args) == expected
    else:
        with pytest.raises((ValueError,OSError)):
            prepare_family_registration(intent,**args)


@pytest.mark.parametrize('fault', [None, 'wrong_authorization_scope'])
def test_register_cli_returns_proposal_without_replica_or_database_access(retained_baseline, tmp_path, monkeypatch, capsys, fault):
    import sys
    from pathlib import Path
    from uuid import UUID
    from scripts import run_p3_alpha_campaign as command
    from packages.alpha_lifecycle.baseline_campaign import _read
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from services.job_store.p3_sql import PublicationProposal
    from tests.p3.test_replica_execution import _seal

    store, evidence, _, _ = registration_chain(retained_baseline)
    inputs = _read(store, evidence.input_set_ref, InputSet)
    specs = tuple(store.put_bytes(canonical_json_bytes(s), media_type='application/json')
        for s in _candidate_specs(json.loads(POLICY_SOURCE.read_bytes())))
    intent_ref = _seal(store, schema_version='p3-operation-input-v1', workflow_operation='p3-register-family-v1',
        operation='REGISTER_FAMILY', input_set_ref=evidence.input_set_ref, allowed_alpha_ids=FAMILY_IDS,
        body=dict(baseline_selection_ref=evidence.baseline_selection_ref, candidate_spec_refs=specs,
            candidate_record_refs=evidence.candidate_record_refs))
    intent = _read(store, intent_ref, P3OperationInput)
    now = datetime.now(UTC)
    def utc(value):
        return value.isoformat(timespec='microseconds').replace('+00:00','Z')
    safe = dict(broker=False, live=False, network=False, production=False)
    review_ref = _seal(store, schema_version='p3-review-approval-v1', source=inputs.source,
        subject_digests=[intent.digest], operator_identity='synthetic-operator', reviewer_identity='synthetic-reviewer',
        review_execution_id='synthetic-execution', verdict='APPROVED', issued_at=utc(now-timedelta(minutes=1)),
        expires_at=utc(now+timedelta(hours=1)), evidence_ref=store.put_bytes(b'{}', media_type='application/json'), authority=safe)
    authorization_ref = _seal(store, schema_version='p3-run-authorization-v1', input_set_ref=evidence.input_set_ref,
        review_ref=review_ref, operation='OOS' if fault else 'REGISTER_FAMILY', allowed_alpha_ids=FAMILY_IDS,
        issued_at=utc(now), expires_at=utc(now+timedelta(minutes=30)), nonce=str(UUID(int=1)), issuer_workflow='p3-authority.yml',
        issuer_run_id=1, issuer_attempt=1, authority=safe)
    for name, value in [('manifest', intent_ref), ('source', inputs.source),
        ('environment', inputs.environment_ref), ('authorization', authorization_ref)]:
        (tmp_path/name).write_bytes(canonical_json_bytes(value))
    def no_replica(**kwargs):
        raise AssertionError('registration attempted to construct a numerical replica')
    monkeypatch.setattr(command, 'BubblewrapExecutor', no_replica)
    before={path.name:path.read_bytes() for path in store._root.iterdir()}
    monkeypatch.setattr(sys, 'argv', [str(command.__file__), '--manifest-ref', str(tmp_path/'manifest'),
        '--source', str(tmp_path/'source'), '--environment-ref', str(tmp_path/'environment'),
        '--store', str(store._root), '--release', str(Path(command.ROOT)), '--python', sys.executable,
        '--sandbox-policy-digest', 'c'*64, '--logical-trial-id', 'p3-register-family-v1',
        '--output', str(tmp_path/'runs'), '--job-id', 'job_registration', '--authorization-ref', str(tmp_path/'authorization')])
    if fault:
        from packages.alpha_lifecycle.authority import AuthorityHeld
        with pytest.raises((ValueError, AuthorityHeld)):
            command.main()
    else:
        command.main()
        proposal = PublicationProposal.model_validate_json(capsys.readouterr().out)
        assert proposal.request.job_id == 'job_registration'
        assert proposal.request.stage == 'REGISTER'
        assert len(proposal.entries) == 8
    assert {path.name:path.read_bytes() for path in store._root.iterdir()} == before
    if fault:
        assert not (tmp_path/'runs').exists()
    else:
        from packages.data_catalog.artifact_store import LocalArtifactStore
        private=LocalArtifactStore(tmp_path/'runs'/'artifacts')
        assert private.read_bytes(proposal.request.evidence_ref)
        assert all(private.read_bytes(ref) for ref in proposal.request.proposed_event_refs)
