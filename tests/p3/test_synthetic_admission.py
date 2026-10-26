"""A synthetic receipt cannot stand in for an official, LOCAL or recorded run."""
import pytest
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from tests.p3.test_github_run import (
    AUTHORITY, HOST, NOW, SOURCE, Store, _admit, _publish_run, _ref, _z, protected, research_inputs, sealed,
)
from packages.engine_contracts.serialization import canonical_json_bytes

__all__ = ['protected']


def synthetic_receipt(store, **changes):
    body = dict(schema_version='p3-github-synthetic-inputs-v1', campaign='REHEARSAL',
        generation_ref=_ref('7' * 64), commitment_ref=_ref('8' * 64),
        provenance='LOCAL_DETERMINISTIC_GENERATOR_NO_WORKFLOW_PRODUCER',
        identity=dict(issuer_kind='GITHUB', issuer_identity='p3-authority',
            repository='nam176hermes/Trading-Agent', workflow_ref='p3-authority.yml', run_id=7, attempt=1))
    return research_inputs(store, **(body | changes))


def test_typed_synthetic_receipt_requires_its_genuine_current_run(protected):
    store = Store()
    receipt = synthetic_receipt(store)
    admitted = _admit(protected, store, receipt)
    assert admitted.generation_ref.content_sha256 == '7' * 64
    for mode in ('FAKE',):
        with pytest.raises(ValueError):
            _admit(protected, store, receipt, mode=mode)
    other = dict(receipt['identity'], run_id=8)
    with pytest.raises(ValueError):
        _admit(protected, store, synthetic_receipt(store, identity=other))


def test_synthetic_receipt_cannot_be_relabelled_official():
    from packages.alpha_lifecycle.github_run import parse_github_research_inputs
    for changes in (dict(campaign='OFFICIAL'), dict(schema_version='p3-github-research-inputs-v2',
                                                  batch_record_sha256='a' * 64)):
        with pytest.raises(ValueError):
            parse_github_research_inputs(synthetic_receipt(Store(), **changes))


@pytest.mark.parametrize('workflow', ['p3-holdout-primary-v1', 'p3-native-parity-v1', 'p3-phase-exit-v1'])
@pytest.mark.parametrize('fault', [None, 'missing', 'expired', 'cross_run', 'fake', 'local', 'legacy_receipt',
                                  'market_input', 'legacy_market_input'])
def test_each_session_stage_rechecks_current_synthetic_context(protected, monkeypatch, workflow, fault):
    """Protected-file boundary only; full generation/backup graph has its own test."""
    from packages.alpha_lifecycle import authority as module
    from packages.alpha_lifecycle import synthetic_data
    from packages.alpha_lifecycle.authority import AuthorityHeld, WORKFLOW_OPERATIONS
    from packages.alpha_lifecycle.contracts.authority import RUN_AUTHORIZATION
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.alpha_lifecycle.github_run import parse_github_research_inputs
    from packages.data_contracts import ArtifactRefV1
    from packages.alpha_lifecycle.operation_input import P3OperationInput, _OPERATION_BODIES
    store = Store()
    policy = json.loads((Path(__file__).resolve().parents[2] /
        'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes())
    body = dict(schema_version='p3-input-set-v1', source=SOURCE, epoch_id='test-epoch',
        policy_digest='1' * 64, family_digest='b' * 64, cost_model=policy['costs']['base'],
        synthetic_generation_ref=_ref('7' * 64),
        **{field: _ref('3' * 64) for field in ('dataset_evidence_ref', 'fold_manifest_ref',
            'environment_ref', 'regime_threshold_ref', 'integration_receipt_ref')})
    body['pit_proof_ref'] = store.put(sealed(dict(schema_version='p3-p-i-t-proof-v1',
        dataset_ref=body['dataset_evidence_ref'], fold_manifest_ref=body['fold_manifest_ref'],
        vintage_class='SYNTHETIC_REHEARSAL', historical_vintage_verified=False,
        revision_proof_ref=_ref('8' * 64), no_future_suite_ref=_ref('9' * 64),
        limitations=['synthetic.rehearsal_only'])))
    if fault in {'market_input', 'legacy_market_input'}:
        body.pop('synthetic_generation_ref')
    input_ref = store.put(sealed(body))
    changes = dict(input_ref=input_ref, environment_ref=body['environment_ref'],
                   integration_receipt_ref=body['integration_receipt_ref'])
    receipt = synthetic_receipt(store, **changes)
    binding = parse_github_research_inputs(receipt).binding_digest
    receipt = synthetic_receipt(store, **changes, subjects=[input_ref['content_sha256'], binding])
    if fault in {'legacy_receipt', 'legacy_market_input'}:
        receipt = research_inputs(store, **changes)
        binding = parse_github_research_inputs(receipt).binding_digest
        receipt = research_inputs(store, **changes, subjects=[input_ref['content_sha256'], binding])
    if fault == 'cross_run':
        receipt = sealed({**receipt, 'identity': {**receipt['identity'], 'run_id': 8}})
    path = Path('/etc/trading-agent-p3/github/research-inputs.json')
    protected[path] = receipt
    protected[path.with_name('research-inputs-review.json')] = store.review
    _publish_run(protected, mode='FAKE' if fault == 'fake' else 'GITHUB',
        operation='p3-holdout-primary-v1',
        research_inputs_sha256=hashlib.sha256(canonical_json_bytes(receipt) + b'\n').hexdigest())
    protected[Path('/run/trading-agent-p3/7-1/session.json')] = dict(
        schema_version='p3-holdout-session-v1', source=SOURCE, workflow_run_id=7, workflow_attempt=1)
    auth = dict(schema_version='p3-run-authorization-v1', issuer_workflow='p3-authority.yml',
        issuer_run_id=7, issuer_attempt=1, input_set_ref=input_ref, review_ref=_ref('5' * 64),
        operation=WORKFLOW_OPERATIONS[workflow], allowed_alpha_ids=['a0.donchian-20-10-close-confirm'],
        issued_at=_z(NOW-timedelta(minutes=1)), expires_at=_z(NOW+timedelta(hours=8)),
        nonce='00000000-0000-4000-8000-000000000001', authority=AUTHORITY)
    if fault == 'local':
        for key in ('issuer_workflow', 'issuer_run_id', 'issuer_attempt'):
            auth.pop(key)
        auth.update(schema_version='p3-local-run-authorization-v1', identity=dict(
            issuer_kind='LOCAL', issuer_identity='local-test', run_id='00000000-0000-4000-8000-000000000007',
            attempt=1, host_id=HOST[0], boot_id=str(HOST[1])))
    authorization = RUN_AUTHORIZATION.validate_json(canonical_json_bytes(sealed(auth)))
    operation, body_type = _OPERATION_BODIES[workflow]
    intent_body = {name: ('1' * 64 if name == 'policy_digest' else _ref('4' * 64))
                   for name in body_type.model_fields}
    intent = P3OperationInput.model_validate_json(canonical_json_bytes(sealed(dict(
        schema_version='p3-operation-input-v1', workflow_operation=workflow, operation=operation,
        input_set_ref=input_ref, allowed_alpha_ids=auth['allowed_alpha_ids'], body=intent_body))))
    checks = []
    monkeypatch.setattr(synthetic_data, 'read_synthetic_commitment_metadata', lambda *a, **kw: checks.append(kw))
    if fault == 'missing':
        protected.pop(path)
    current = NOW + timedelta(hours=7) if fault == 'expired' else NOW
    def check():
        return module.require_synthetic_operation_context(authorization, SourceIdentity.model_validate(SOURCE),
            intent, store, now=current)
    if fault is None:
        assert check().identity.run_id == 7
        assert len(checks) == 1
        # Real staging with a typed intent and exact operation review. Generation
        # reconstruction remains a declared dependency stub in this file test.
        from datetime import datetime
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(NOW.timestamp(), tz=tz)
        monkeypatch.setattr(module, 'datetime', Clock)
        evidence = store.put({'test': 'operation review evidence'})
        review = store.put(sealed(dict(schema_version='p3-review-approval-v1', source=SOURCE,
            subject_digests=[intent.digest], operator_identity='operator', reviewer_identity='reviewer',
            review_execution_id='unit-only', verdict='APPROVED', issued_at=_z(NOW-timedelta(minutes=2)),
            expires_at=_z(NOW+timedelta(hours=9)), evidence_ref=evidence, authority=AUTHORITY)))
        staged_auth = RUN_AUTHORIZATION.validate_json(canonical_json_bytes(sealed({**auth, 'review_ref': review})))
        def put_bytes(raw, *, media_type):
            ref = store.put(json.loads(raw))
            assert store.read_bytes(ArtifactRefV1.model_validate(ref)) == raw
            return ArtifactRefV1.model_validate(ref)
        staging = SimpleNamespace(read_bytes=store.read_bytes, put_bytes=put_bytes)
        def stage():
            return module.stage_alpha_campaign_payload(staging, staged_auth, SourceIdentity.model_validate(SOURCE),
                workflow, canonical_json_bytes(intent), operation_input=intent)
        assert stage().logical_trial_id == workflow
        assert len(checks) == 2
        protected.pop(path)
        with pytest.raises(module.AuthorityHeld, match='REVIEW'):
            stage()
        with pytest.raises(ValueError):
            check()  # A prior positive check is not a cached lease.
        assert len(checks) == 2
    else:
        with pytest.raises(AuthorityHeld if fault == 'fake' else ValueError):
            check()
        assert checks == []
