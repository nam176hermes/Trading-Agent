"""Proposed envelopes have stable identities; SQL remains publication authority."""
from datetime import UTC, datetime, timedelta
import hashlib
import pytest
from uuid import UUID, uuid5

from packages.alpha_lifecycle.lifecycle import plan_transition
from packages.alpha_lifecycle.registry import AlphaLifecycleStatus
from packages.alpha_lifecycle.contracts.lifecycle import ExpectedHead
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_sql import PublicationTransport
from tests.p3.test_lifecycle import _record, _evidence


def test_producer_stages_stable_envelopes_without_worker_claim(tmp_path):
    from services.job_worker.p3_publication_producer import build_publication_proposal
    store = LocalArtifactStore(tmp_path)
    evidence = _evidence()
    events, heads = [], []
    for index in range(4):
        record = _record(AlphaLifecycleStatus.IDEA).model_copy(update={'alpha_id':f'a{index}.producer'})
        idea = plan_transition(record,None,evidence)
        candidate = plan_transition(record.model_copy(update={'lifecycle_status':AlphaLifecycleStatus.CANDIDATE}),idea,evidence)
        events.extend((idea,candidate))
        heads.append(ExpectedHead(alpha_id=record.alpha_id,version='1.0.0',sequence=0,event_digest=None))
    observed = datetime(2026,9,10,tzinfo=UTC)
    args = dict(events=tuple(events),expected_heads=tuple(heads),evidence=evidence,epoch_id='p3-btc-d1-e1',
                job_id='job_registration',observed_at=observed,expires_at=observed+timedelta(hours=1),store=store)
    proposal = build_publication_proposal(**args)
    assert build_publication_proposal(**args) == proposal
    namespace = UUID('5d3f7ad6-7372-5cb7-80a7-68a179f1fdb2')
    for event,entry in zip(events,proposal.entries,strict=True):
        stream = uuid5(namespace,canonical_json_bytes(['p3-btc-d1-e1',event.record.alpha_id,'1.0.0']).decode())
        assert entry.stream_id == stream
        assert entry.event_id == uuid5(stream,canonical_json_bytes([event.sequence,event.event_sha256]).decode())
        assert store.read_bytes(event.artifact)
    assert proposal.request.evidence_ref.content_sha256 == hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
    proposal_raw = canonical_json_bytes(proposal)
    assert store.read_bytes(store.put_bytes(proposal_raw,media_type='application/json')) == proposal_raw
    for field in (b'lease_token',b'attempt_id',b'worker_id',b'publication_ref',b'committed_at'):
        assert field not in proposal_raw
    first = PublicationTransport(job_id='job_registration',attempt_id='attempt-1',worker_id='worker-1',
        lease_token='synthetic-fence-1',request=proposal.request,entries=proposal.entries)
    second = first.model_copy(update=dict(attempt_id='attempt-2',lease_token='synthetic-fence-2'))
    assert first.request == second.request


def test_producer_rejects_researched_without_final_oos_decision(tmp_path):
    from services.job_worker.p3_publication_producer import build_publication_proposal
    from packages.alpha_lifecycle.contracts.lifecycle import PrePublicationEvidence
    from tests.p3.test_lifecycle import _ref
    evidence = _evidence()
    idea = plan_transition(_record(AlphaLifecycleStatus.IDEA),None,evidence)
    candidate = plan_transition(_record(AlphaLifecycleStatus.CANDIDATE),idea,evidence)
    value = evidence.model_dump(mode='json',exclude={'digest'})
    value.update(stage='RESEARCH_DECISION',qualification_bundle_ref=_ref('4').model_dump(mode='json'))
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    evidence = PrePublicationEvidence.model_validate_json(canonical_json_bytes(value))
    researched = plan_transition(_record(AlphaLifecycleStatus.RESEARCHED),candidate,evidence)
    with pytest.raises(ValueError,match='publication batch'):
        build_publication_proposal(events=(researched,),expected_heads=(ExpectedHead(
            alpha_id=candidate.record.alpha_id,version='1.0.0',sequence=2,event_digest=candidate.event_sha256),),
            evidence=evidence,epoch_id='p3-btc-d1-e1',job_id='job_oos',
            observed_at=datetime(2026,9,10,tzinfo=UTC),expires_at=datetime(2026,9,11,tzinfo=UTC),
            store=LocalArtifactStore(tmp_path))


@pytest.mark.parametrize('decision,metrics,valid', [
    ('NOT_EVALUATED', None, False), ('FAIL', 'a'*64, False),
    ('PASS', None, False), ('PASS', 'a'*64, True),
])
def test_oos_pass_requires_passing_evaluation_identity(tmp_path, decision, metrics, valid):
    from services.job_worker.p3_publication_producer import build_publication_proposal
    from packages.alpha_lifecycle.registry import QualificationDecision
    from tests.p3.test_publication import _changed
    from tests.p3.test_lifecycle import _ref
    evidence = _evidence()
    idea = plan_transition(_record(AlphaLifecycleStatus.IDEA),None,evidence)
    candidate = plan_transition(_record(AlphaLifecycleStatus.CANDIDATE),idea,evidence)
    evidence = _changed(evidence,stage='RESEARCH_DECISION',qualification_bundle_ref=_ref('4').model_dump(mode='json'))
    record = _record(AlphaLifecycleStatus.RESEARCHED).model_copy(update={
        'qualification_decision':QualificationDecision(decision),
        'metrics_sha256':metrics,'robustness_sha256':metrics})
    researched = plan_transition(record,candidate,evidence)
    passed = plan_transition(record.model_copy(update={'lifecycle_status':AlphaLifecycleStatus.OOS_PASS}),researched,evidence)
    args = dict(events=(researched,passed),expected_heads=(ExpectedHead(
        alpha_id=candidate.record.alpha_id,version='1.0.0',sequence=2,event_digest=candidate.event_sha256),),
        evidence=evidence,epoch_id='p3-btc-d1-e1',job_id='job_oos',
        observed_at=datetime(2026,9,10,tzinfo=UTC),expires_at=datetime(2026,9,11,tzinfo=UTC),
        store=LocalArtifactStore(tmp_path))
    if valid:
        assert len(build_publication_proposal(**args).entries) == 2
    else:
        with pytest.raises(ValueError,match='qualification'):
            build_publication_proposal(**args)
