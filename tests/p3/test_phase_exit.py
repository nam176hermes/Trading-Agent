"""Frozen economic boundaries; these unit inputs grant no phase-exit authority."""
from decimal import Decimal, localcontext, ROUND_DOWN
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.alpha_lifecycle.phase_exit import _economic_checks
from tests.p3.test_reference_input import reference_seed  # noqa: F401


def _inputs():
    def scenario(value, drawdown='.25'):
        return SimpleNamespace(aggregate_metrics=SimpleNamespace(total_return=Decimal(value), max_drawdown=Decimal(drawdown)))
    evaluation = SimpleNamespace(primary_base=scenario('.12'), baseline_base=scenario('.10'),
        primary_double_cost=scenario('.000000000001'), primary_delayed=scenario('.000000000001'),
        capacity=SimpleNamespace(median='.02', peak='.10'))
    return evaluation, SimpleNamespace(net_return='.12', max_drawdown='.25'), SimpleNamespace(net_return='.10')


def test_all_nine_numeric_checks_match_frozen_policy_and_ignore_ambient_decimal_context():
    policy = json.loads((Path(__file__).resolve().parents[2]/'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes())['holdout']
    assert {key:policy[key] for key in ('total_return_gt', 'baseline_excess_gte', 'double_cost_return_gt',
        'delayed_return_gt', 'max_drawdown_lte', 'capacity_median_lte', 'capacity_peak_lte',
        'native_return_gt', 'native_baseline_excess_gte', 'native_max_drawdown_lte')} == {
        'total_return_gt':'0', 'baseline_excess_gte':'0.02', 'double_cost_return_gt':'0',
        'delayed_return_gt':'0', 'max_drawdown_lte':'0.25', 'capacity_median_lte':'0.02',
        'capacity_peak_lte':'0.10', 'native_return_gt':'0', 'native_baseline_excess_gte':'0.02',
        'native_max_drawdown_lte':'0.25'}
    expected = _economic_checks(*_inputs())
    assert len(expected) == 9 and all(check.passed for check in expected)
    with localcontext(prec=2, rounding=ROUND_DOWN):
        assert _economic_checks(*_inputs()) == expected


@pytest.mark.parametrize('fault,code', [('return','RETURN'), ('excess','EXCESS'),
    ('double','DOUBLE_COST'), ('delay','DELAY'), ('drawdown','MAX_DRAWDOWN'),
    ('median','CAPACITY'), ('peak','CAPACITY'), ('native_return','NATIVE_RETURN'),
    ('native_excess','NATIVE_EXCESS'), ('native_drawdown','NATIVE_DRAWDOWN')])
def test_economic_failures_cannot_be_rounded_up_to_pass(fault, code):
    evaluation, primary, baseline = _inputs()
    if fault == 'return': evaluation.primary_base.aggregate_metrics.total_return = Decimal(0)
    elif fault == 'excess': evaluation.primary_base.aggregate_metrics.total_return = Decimal('.119999999999')
    elif fault == 'double': evaluation.primary_double_cost.aggregate_metrics.total_return = Decimal(0)
    elif fault == 'delay': evaluation.primary_delayed.aggregate_metrics.total_return = Decimal(0)
    elif fault == 'drawdown': evaluation.primary_base.aggregate_metrics.max_drawdown = Decimal('.250000000001')
    elif fault == 'median': evaluation.capacity.median = '.020000000001'
    elif fault == 'peak': evaluation.capacity.peak = '.100000000001'
    elif fault == 'native_return': primary.net_return = '0'
    elif fault == 'native_excess': primary.net_return = '.119999999999'
    else: primary.max_drawdown = '.250000000001'
    with localcontext(prec=2, rounding=ROUND_DOWN):
        checks = {check.check_id:check.passed for check in _economic_checks(evaluation, primary, baseline)}
    assert not checks[code]


@pytest.fixture
def exit_graph(reference_seed, monkeypatch, tmp_path):
    """Owner selection/qualification and native receipts are declared unit inputs.

    Holdout arithmetic, replay readback, executable traces, both comparisons and
    exit recomputation are real. This fixture cannot qualify an official run.
    """
    import hashlib
    import shutil
    from packages.alpha_lifecycle import primary_selection, baseline_campaign
    from packages.alpha_lifecycle.contracts.authority import PrimarySelection, FamilyReview
    from packages.alpha_lifecycle.contracts.execution import HoldoutManifest, InstrumentSpec, InputSet, EnvironmentIdentity
    from packages.alpha_lifecycle.contracts.lifecycle import RegistrationProof
    from packages.alpha_lifecycle.contracts.results import BaselineSelection
    from packages.alpha_lifecycle.evaluation import evaluate_holdout
    from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView, build_holdout_calculation_view
    from packages.alpha_lifecycle.parity import build_parity_pair
    from packages.alpha_lifecycle.registry import AlphaLifecycleStatus, QualificationDecision
    from packages.alpha_lifecycle.replica_store import _read
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from packages.engine_contracts.serialization import canonical_json_bytes
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from tests.p3.test_lifecycle import _record
    from tests.p3.test_parity_pair import _change, _pair_inputs, _put
    root, manifest_ref, spec_ref, *_ = reference_seed
    root = Path(shutil.copytree(root, tmp_path / 'retained'))
    store = LocalArtifactStore(root)
    def seal(**value):
        return _put(store, {**value, 'digest':hashlib.sha256(canonical_json_bytes(value)).hexdigest()})
    manifest = _read(store, manifest_ref, HoldoutManifest)
    selection = _read(store, manifest.primary_selection_ref, PrimarySelection)
    family = _read(store, selection.family_review_ref, FamilyReview)
    opaque = _put(store, {})
    bundle = seal(schema_version='p3-qualification-bundle-v1', evaluation_ref=opaque, replay_proof_ref=opaque,
        pit_ref=opaque, legacy_evidence_ref=opaque, legacy_result_ref=opaque,
        criteria=[dict(criterion_id=f'C{i:02}', passed=True, failure_code='E_UNIT') for i in range(1, 17)],
        pipeline_verdict='PASS', alpha_verdict='PASS')
    closure = seal(schema_version='p3-campaign-closure-report-v1', stage='RESEARCH_DECISION',
        prepublication_ref=opaque, publication_ref=opaque, qualification_ref=bundle, exit_result_ref=None,
        projection_digest=None, projection_status='PENDING')
    family_ref = _change(store, selection.family_review_ref, candidate_report_refs=(closure,)*4)
    record = _record(AlphaLifecycleStatus.OOS_PASS).model_copy(update=dict(qualification_decision=QualificationDecision.PASS,
        metrics_sha256='1'*64, robustness_sha256=bundle.content_sha256))
    head_ref = _put(store, dict(schema_version='alpha-registry-event-v1', sequence=4, predecessor_sha256='2'*64, record=record))
    selection_ref = _change(store, manifest.primary_selection_ref, family_review_ref=family_ref, primary_candidate_head_ref=head_ref)
    selection = _read(store, selection_ref, PrimarySelection)
    manifest_ref = _change(store, manifest_ref, primary_selection_ref=selection_ref)
    manifest = _read(store, manifest_ref, HoldoutManifest)
    monkeypatch.setattr(primary_selection, 'select_primary', lambda *args:selection)
    registration = _read(store, manifest.research_registration_ref, RegistrationProof)
    baseline = _read(store, registration.baseline_selection_ref, BaselineSelection)
    monkeypatch.setattr(baseline_campaign, 'validate_baseline_selection', lambda *args:baseline)
    inputs = _read(store, family.input_set_ref, InputSet)
    holdout_inputs = _change(store, family.input_set_ref, dataset_evidence_ref=manifest.holdout_dataset_ref)
    request = seal(schema_version='p3-holdout-request-v1', source=manifest.source,
        primary_selection_ref=selection_ref, policy_digest=manifest.policy_digest, holdout_input_set_ref=holdout_inputs,
        custody_record_ref=opaque, review_ref=opaque, issued_at='2026-09-02T00:00:00Z', expires_at='2026-09-03T00:00:00Z',
        logical_trial_id='p3-holdout-primary-v1', authority=dict(broker=False, live=False, network=False, production=False))
    view = HoldoutCalculationView(build_holdout_calculation_view(manifest_ref, spec_ref, store), manifest_ref, spec_ref)
    _, arguments = _pair_inputs(store, view, manifest_ref, spec_ref)
    pair = _put(store, build_parity_pair(store=store, **arguments))
    evaluation = evaluate_holdout(manifest, _read(store, spec_ref, InstrumentSpec), store)
    evaluation_ref = _put(store, evaluation)
    environment = _read(store, manifest.environment_ref, EnvironmentIdentity)
    receipts = tuple(seal(schema_version='p3-replay-receipt-v1', logical_trial_id='p3-holdout-primary-v1',
        replicate=f'R{i}', manifest_digest=manifest_ref.content_sha256, result_ref=evaluation_ref,
        source=manifest.source, environment_ref=manifest.environment_ref, sandbox_policy_digest=environment.sandbox_policy_digest,
        started_at='2026-09-02T00:00:00Z', completed_at='2026-09-02T00:00:01Z', process_exit=0,
        network_denied=True, output_inventory_digest='3'*64) for i in range(1, 4))
    proof = seal(schema_version='p3-replay-proof-v1', manifest_digest=manifest_ref.content_sha256,
        result_digest=evaluation_ref.content_sha256, receipt_refs=receipts)
    intent_ref = seal(schema_version='p3-operation-input-v1', workflow_operation='p3-phase-exit-v1', operation='PHASE_EXIT',
        input_set_ref=family.input_set_ref, allowed_alpha_ids=(selection.primary_alpha_id,), body=dict(
        primary_selection_ref=selection_ref, primary_qualification_ref=bundle, baseline_selection_ref=registration.baseline_selection_ref,
        holdout_request_ref=request, holdout_evaluation_ref=evaluation_ref, holdout_replay_ref=proof,
        executable_ref=arguments['primary_reference_ref'], baseline_executable_ref=arguments['baseline_reference_ref'],
        parity_ref=pair, current_primary_head_ref=head_ref))
    # The raw holdout is available only through the one released view.
    from packages.alpha_lifecycle.contracts.data import DatasetEvidence
    dataset = _read(store, manifest.holdout_dataset_ref, DatasetEvidence)
    for ref in (*dataset.row_refs, reference_seed[-1]):
        (root / ref.locator).unlink()
    return store, _read(store, intent_ref, P3OperationInput), inputs.source, view


def test_parent_recomputes_all_thirteen_checks_and_keeps_failed_primary(exit_graph):
    from packages.alpha_lifecycle.phase_exit import evaluate_phase_exit
    store, intent, source, view = exit_graph
    before = set(store._root.iterdir())
    result = evaluate_phase_exit(intent, expected_source=source, store=store, holdout_view=view)
    assert len(result.checks) == 13
    assert result.verdict == 'FAIL'  # Oscillating prices fail; no fallback selection.
    assert result.primary_selection_ref == intent.body.primary_selection_ref
    assert all(check.passed for check in result.checks if check.check_id in {'PARITY','REPLAY','PRIMARY','IDENTITY'})
    assert set(store._root.iterdir()) == before
    from datetime import UTC, datetime
    from services.job_worker.p3_publication_producer import prepare_phase_exit
    from packages.alpha_lifecycle.lifecycle import read_registry_event
    from packages.alpha_lifecycle.replica_store import _read, ReadbackStore
    from packages.alpha_lifecycle.contracts.lifecycle import PrePublicationEvidence
    arguments = dict(expected_source=source, job_id='p3-unit-exit', observed_at=datetime(2026,9,2,tzinfo=UTC),
        expires_at=datetime(2026,9,3,tzinfo=UTC))
    proposal = prepare_phase_exit(intent, store=store, holdout_view=view, **arguments)
    assert proposal.request.stage == 'EXIT_DECISION'
    assert len(proposal.entries) == len(proposal.request.proposed_event_refs) == len(proposal.request.expected_heads) == 1
    event = read_registry_event(store, proposal.request.proposed_event_refs[0])
    assert event.record.lifecycle_status.value == 'REJECTED'
    assert event.predecessor_sha256 == intent.body.current_primary_head_ref.content_sha256
    evidence = _read(store, proposal.request.evidence_ref, PrePublicationEvidence)
    assert evidence.qualification_bundle_ref == intent.body.primary_qualification_ref
    assert prepare_phase_exit(intent, store=ReadbackStore(store, store), holdout_view=view, **arguments) == proposal


@pytest.mark.parametrize('fault', ['missing', 'different_manifest'])
def test_phase_exit_requires_its_exact_released_view(exit_graph, reference_seed, fault):
    from packages.alpha_lifecycle.phase_exit import evaluate_phase_exit
    from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView, build_holdout_calculation_view
    from packages.data_catalog.artifact_store import LocalArtifactStore
    store, intent, source, _ = exit_graph
    wrong_view = None
    if fault == 'different_manifest':
        root, manifest, spec, *_ = reference_seed
        wrong_view = HoldoutCalculationView(build_holdout_calculation_view(manifest, spec,
            LocalArtifactStore(root)), manifest, spec)
    before = set(store._root.iterdir())
    with pytest.raises(ValueError):
        evaluate_phase_exit(intent, expected_source=source, store=store, holdout_view=wrong_view)
    assert set(store._root.iterdir()) == before


@pytest.mark.parametrize('fault', ['source', 'head', 'baseline_role', 'replay_trial', 'summary'])
def test_parent_rejects_unbound_exit_evidence_without_writing(exit_graph, fault):
    from packages.alpha_lifecycle.phase_exit import evaluate_phase_exit
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.replica_store import _read
    from packages.alpha_lifecycle.contracts.results import ReplayProof
    from tests.p3.test_parity_pair import _change, _put
    store, intent, source, view = exit_graph
    body = intent.body.model_dump(mode='json')
    if fault == 'source': source = source.model_copy(update={'commit_sha':'9'*40})
    elif fault == 'head': body['current_primary_head_ref'] = intent.body.baseline_selection_ref
    elif fault == 'baseline_role': body['baseline_executable_ref'] = intent.body.executable_ref
    elif fault == 'summary': body['executable_ref'] = _change(store, intent.body.executable_ref, net_return='100')
    else:
        proof = _read(store, intent.body.holdout_replay_ref, ReplayProof)
        receipts = tuple(_change(store, ref, logical_trial_id='other-experiment') for ref in proof.receipt_refs)
        body['holdout_replay_ref'] = _change(store, intent.body.holdout_replay_ref, receipt_refs=receipts)
    altered = _read(store, _change(store, _put(store, intent), body=body), P3OperationInput)
    before = set(store._root.iterdir())
    with pytest.raises(ValueError): evaluate_phase_exit(altered, expected_source=source, store=store, holdout_view=view)
    assert set(store._root.iterdir()) == before


def test_native_mismatch_is_held_and_cannot_publish(exit_graph):
    import hashlib
    from datetime import UTC, datetime
    from packages.alpha_lifecycle.phase_exit import evaluate_phase_exit
    from packages.alpha_lifecycle.parity import ParityPair, build_parity_pair, compare_executable_results
    from packages.alpha_lifecycle.contracts.results import ParityResult, ExecutableResult
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.replica_store import _read
    from packages.engine_contracts.serialization import canonical_json_bytes
    from services.job_worker.p3_publication_producer import prepare_phase_exit
    from tests.p3.test_parity_pair import _change, _put
    store, intent, source, view = exit_graph
    pair = _read(store, intent.body.parity_ref, ParityPair)
    parity = _read(store, pair.primary_parity_ref, ParityResult)
    native = _read(store, parity.native_result_refs[0], ExecutableResult)
    rows = json.loads(store.read_bytes(native.fill_trace_ref))
    signal = next(row for row in rows if row['kind'] == 'SIGNAL')
    signal.pop('digest'); signal['price'] = '1'
    signal['digest'] = hashlib.sha256(canonical_json_bytes(signal)).hexdigest()
    native_ref = _change(store, parity.native_result_refs[0], fill_trace_ref=_put(store, rows))
    receipts = tuple(_change(store, ref, result_ref=native_ref) for ref in parity.native_receipt_refs)
    failed = compare_executable_results(parity.reference_ref, (native_ref,)*3, receipts, store, quote_quantum=Decimal('.01'))
    assert failed.verdict == 'FAIL'
    fields = {key:getattr(pair, key) for key in type(pair).model_fields if key.endswith('_ref')}
    fields['primary_parity_ref'] = _put(store, failed)
    pair_ref = _put(store, build_parity_pair(store=store, **fields))
    body = intent.body.model_dump(mode='json'); body['parity_ref'] = pair_ref
    intent = _read(store, _change(store, _put(store, intent), body=body), P3OperationInput)
    before = set(store._root.iterdir())
    result = evaluate_phase_exit(intent, expected_source=source, store=store, holdout_view=view)
    assert result.verdict == 'HELD'
    assert not next(check for check in result.checks if check.check_id == 'PARITY').passed
    with pytest.raises(ValueError, match='HELD'):
        prepare_phase_exit(intent, expected_source=source, job_id='p3-unit-exit',
            observed_at=datetime(2026,9,2,tzinfo=UTC), expires_at=datetime(2026,9,3,tzinfo=UTC), store=store, holdout_view=view)
    assert set(store._root.iterdir()) == before


@pytest.fixture
def committed_exit(exit_graph):
    """Synthetic SQL observation of the real retained FAIL publication; no host authority."""
    import hashlib
    from datetime import UTC,datetime
    from types import SimpleNamespace
    from packages.engine_contracts.serialization import canonical_json_bytes
    from packages.alpha_lifecycle.contracts.authority import LocalRunAuthorization,ReviewApproval
    from packages.alpha_lifecycle.contracts.lifecycle import JobCommitResult
    from packages.alpha_lifecycle.lifecycle import read_registry_event
    from services.job_worker.p3_publication_producer import prepare_phase_exit
    from packages.job_contracts import AlphaCampaignPayload,AlphaCampaignOperation
    store,intent,source,view=exit_graph
    def put(value):return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    def sealed(model,**value):
        value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        return model.model_validate_json(canonical_json_bytes(value))
    proposal=prepare_phase_exit(intent,expected_source=source,job_id='job_'+'a'*32,
        observed_at=datetime(2026,9,2,tzinfo=UTC),expires_at=datetime(2026,9,3,tzinfo=UTC),store=store,holdout_view=view)
    review=sealed(ReviewApproval,schema_version='p3-review-approval-v1',source=source,
        subject_digests=[intent.digest],operator_identity='synthetic-operator',reviewer_identity='synthetic-reviewer',
        review_execution_id='synthetic-review',verdict='APPROVED',issued_at='2026-09-02T00:00:00Z',
        expires_at='2026-09-03T00:00:00Z',evidence_ref=put({'synthetic':'review'}),
        authority=dict(broker=False,live=False,network=False,production=False))
    auth=sealed(LocalRunAuthorization,schema_version='p3-local-run-authorization-v1',input_set_ref=intent.input_set_ref,
        review_ref=put(review),operation='PHASE_EXIT',allowed_alpha_ids=intent.allowed_alpha_ids,
        issued_at='2026-09-02T00:00:00Z',expires_at='2026-09-03T00:00:00Z',nonce='00000000-0000-4000-8000-000000000002',
        identity=dict(issuer_kind='LOCAL',issuer_identity='synthetic',run_id='00000000-0000-4000-8000-000000000003',
            attempt=1,host_id='a'*64,boot_id='00000000-0000-4000-8000-000000000001'),
        authority=dict(broker=False,live=False,network=False,production=False))
    payload=AlphaCampaignPayload(schema_version='p3-alpha-campaign-payload-v1',operation=AlphaCampaignOperation.PHASE_EXIT,
        manifest_ref=put(intent),authorization_ref=put(auth),expected_source=source,logical_trial_id='p3-phase-exit-v1')
    request=proposal.request
    commit=sealed(JobCommitResult,schema_version='p3-job-commit-result-v1',job_id=request.job_id,
        idempotency_key=request.idempotency_key,semantic_request_digest=request.semantic_request_digest,
        prepublication_ref=request.evidence_ref,ledger_event_ids=[str(entry.event_id) for entry in proposal.entries],
        registry_event_refs=request.proposed_event_refs,alpha_outcome='FAIL')
    event=read_registry_event(store,request.proposed_event_refs[0])
    inventory=put([dict(path='artifacts/'+ref.locator,artifact_ref=ref) for ref in (put(proposal),request.evidence_ref,*request.proposed_event_refs)])
    observation=dict(payload=payload,publication_request=request,commit_result=commit,
        committed_at='2026-09-02T01:00:00+00:00',attempt_id='attempt_'+'b'*32,output_inventory_ref=inventory,
        registry_head=dict(alpha_id=event.record.alpha_id,version=event.record.version,sequence=event.sequence,event_digest=event.event_sha256))
    raw=canonical_json_bytes(observation)
    repository=SimpleNamespace(read_phase_exit=lambda job_id:raw)
    return store,repository,request.job_id,json.loads(raw),auth


@pytest.mark.parametrize('fault',[None,'uncommitted','attempt','head','source','missing','changed'])
def test_committed_phase_export_restarts_without_view_and_preserves_fail(committed_exit,monkeypatch,fault):
    from services.job_worker.p3_phase_readback import export_phase_exit,validate_phase_exit_receipt
    from packages.engine_contracts.serialization import canonical_json_bytes
    from packages.alpha_lifecycle.contracts.lifecycle import PrePublicationEvidence
    from packages.alpha_lifecycle.replica_store import _read
    store,repository,job_id,observation,_=committed_exit
    # Any recomputation or reacquisition after SQL commit is a regression.
    def forbidden(*a,**k):raise AssertionError('post-commit export must not calculate or disclose')
    monkeypatch.setattr('packages.alpha_lifecycle.phase_exit.evaluate_phase_exit',forbidden)
    if fault=='uncommitted':repository.read_phase_exit=lambda _:None
    elif fault=='attempt':observation['attempt_id']='another-attempt'
    elif fault=='head':observation['registry_head']['event_digest']='0'*64
    elif fault=='source':observation['payload']['expected_source']['commit_sha']='0'*40
    elif fault=='missing':
        evidence=_read(store,__import__('packages.data_contracts',fromlist=['ArtifactRefV1']).ArtifactRefV1.model_validate(observation['publication_request']['evidence_ref']),PrePublicationEvidence)
        (store._root/evidence.exit_result_ref.locator).unlink()
    if fault not in ('uncommitted','missing'):
        count=[]
        def read(_):
            count.append(1)
            return canonical_json_bytes(observation)+(b' ' if fault=='changed' and len(count)>1 else b'')
        repository.read_phase_exit=read
    if fault:
        with pytest.raises((ValueError,TypeError,OSError)):export_phase_exit(job_id,repository=repository,store=store)
    else:
        first=export_phase_exit(job_id,repository=repository,store=store)
        assert export_phase_exit(job_id,repository=repository,store=store)==first
        receipt=validate_phase_exit_receipt(first,repository=repository,store=store)
        assert receipt.outcome=='FAIL' and receipt.identity.issuer_kind=='LOCAL'
        assert receipt.attempt_id==observation['attempt_id'] and receipt.registry_head_ref is not None


@pytest.mark.parametrize('outcome',['HELD','NONE_QUALIFIED'])
def test_terminal_nonpublication_keeps_outcome_without_registry_or_disclosure(committed_exit,monkeypatch,outcome):
    import hashlib
    from packages.engine_contracts.serialization import canonical_json_bytes
    from packages.alpha_lifecycle.replica_store import _read
    from packages.alpha_lifecycle.operation_input import P3OperationInput,FAMILY_IDS
    from packages.alpha_lifecycle.contracts.authority import PrimarySelection,FamilyReview,LocalRunAuthorization,ReviewApproval
    from packages.job_contracts import AlphaCampaignPayload,payload_fingerprint
    from apps.job_api.contracts import JobDetailEnvelope
    from services.job_worker.p3_phase_readback import terminal_phase_observation,validate_phase_exit_receipt
    store,_,job_id,observation,auth=committed_exit
    def put(value):return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    def seal(model,**value):
        value.pop('digest',None);value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        return model.model_validate_json(canonical_json_bytes(value))
    payload=AlphaCampaignPayload.model_validate_json(canonical_json_bytes(observation['payload']))
    intent=_read(store,payload.manifest_ref,P3OperationInput)
    result=None
    if outcome=='NONE_QUALIFIED':
        previous=_read(store,intent.body.primary_selection_ref,PrimarySelection)
        result=seal(PrimarySelection,**{**previous.model_dump(mode='json'),'outcome':'NONE_QUALIFIED',
            'primary_alpha_id':None,'primary_version':None,'primary_candidate_head_ref':None})
        intent=seal(P3OperationInput,schema_version='p3-operation-input-v1',operation='OOS',
            workflow_operation='p3-select-primary-v1',input_set_ref=intent.input_set_ref,allowed_alpha_ids=FAMILY_IDS,
            body=dict(family_review_ref=result.family_review_ref))
        review=_read(store,auth.review_ref,ReviewApproval)
        review=seal(ReviewApproval,**{**review.model_dump(mode='json'),'subject_digests':[intent.digest]})
        auth=seal(LocalRunAuthorization,**{**auth.model_dump(mode='json'),'operation':'OOS',
            'allowed_alpha_ids':list(FAMILY_IDS),'review_ref':put(review)})
        payload=AlphaCampaignPayload.model_validate_json(canonical_json_bytes(dict(schema_version='p3-alpha-campaign-payload-v1',
            operation='OOS',manifest_ref=put(intent),authorization_ref=put(auth),expected_source=payload.expected_source,
            logical_trial_id='p3-select-primary-v1')))
        family=_read(store,result.family_review_ref,FamilyReview)
        def select(actual,reader):
            assert actual==family and reader is store
            return result
        monkeypatch.setattr('packages.alpha_lifecycle.primary_selection.select_primary',select)
    ref=put(result) if result else None
    attempt=observation['attempt_id'];finished='2026-09-02T01:00:00Z'
    detail=dict(job=dict(job_id=job_id,job_type='ALPHA_CAMPAIGN',state='SUCCEEDED' if result else 'BLOCKED',
        payload=payload,payload_fingerprint=payload_fingerprint(payload),actor=dict(actor_type='OPERATOR',actor_id='synthetic-operator'),
        priority=0,requested_at='2026-09-02T00:01:00Z',updated_at=finished,attempt_count=1,
        reason_code='RESULT_VALIDATED' if result else 'P3_AUTHORITY_HELD',result_hash=None if ref is None else ref.content_sha256),
        attempts=[dict(attempt_id=attempt,attempt_number=1,worker_id='synthetic-worker',claimed_at='2026-09-02T00:02:00Z',
            started_at='2026-09-02T00:03:00Z',finished_at=finished,exit_code=None,termination_reason=None,artifact_count=0 if ref is None else 1)],
        artifacts=[] if ref is None else [dict(artifact_id='artifact_result',attempt_id=attempt,artifact_type='result',
            validator_id='p3-primary-selection-v1',sha256=ref.content_sha256,size_bytes=ref.size_bytes,created_at=finished)],events=[])
    envelope=JobDetailEnvelope.model_validate_json(canonical_json_bytes(dict(schema_version='1.0.0',trace_id='synthetic',
        generated_at='2026-09-02T01:01:00Z',data=detail)))
    receipt=terminal_phase_observation(put(envelope),store=store)
    assert receipt.outcome==outcome and receipt.publication_ref is None and receipt.registry_head_ref is None
    assert validate_phase_exit_receipt(put(receipt),repository=None,store=store)==receipt
    changed=envelope.model_copy(update={'data':envelope.data.model_copy(update={
        'job':envelope.data.job.model_copy(update={'attempt_count':2})})})
    with pytest.raises(ValueError):terminal_phase_observation(put(changed),store=store)


@pytest.mark.parametrize('fault',[None,'revoked','wrong_source','wrong_host','expired'])
def test_committed_phase_export_authenticates_exact_retained_evidence(committed_exit,monkeypatch,fault):
    """Compose real export/auth/readback; inject only clock, host and protected custody."""
    from datetime import UTC,datetime
    from packages.alpha_lifecycle import local_qualification as authority
    from packages.alpha_lifecycle.contracts.authority import QualificationBinding
    from packages.engine_contracts.serialization import canonical_json_bytes
    from packages.runtime_release import config
    from services.job_worker.p3_phase_readback import export_phase_exit,validate_phase_exit_receipt
    from tests.p3.test_local_qualification import sealed,SAFE
    store,repository,job_id,_,auth=committed_exit
    ref=export_phase_exit(job_id,repository=repository,store=store)
    phase=validate_phase_exit_receipt(ref,repository=repository,store=store)
    binding=QualificationBinding(gate='P3_PHASE_EXIT',source=phase.source,policy_digest=phase.policy_digest,
        input_ref=phase.input_ref,environment_ref=phase.environment_ref)
    def put(value):return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    receipt=sealed(dict(schema_version='local-phase-qualification-receipt-v1',identity=auth.identity,binding=binding,
        started_at='2026-09-02T01:00:00Z',completed_at='2026-09-02T01:10:00Z',
        expires_at='2026-09-02T03:00:00Z',evidence_refs=[ref],status='PASS',authority=SAFE))
    approval=sealed(dict(schema_version='local-qualification-approval-v1',issuer_identity=auth.identity.issuer_identity,
        receipt_ref=put(receipt),issued_at='2026-09-02T01:15:00Z',expires_at='2026-09-02T03:00:00Z',
        revoked=fault=='revoked',authority=SAFE))
    def protected(path):
        assert path==Path('/etc/trading-agent/local-qualification/synthetic/P3_PHASE_EXIT.json')
        return canonical_json_bytes(approval)+b'\n',(1,2)
    monkeypatch.setattr(config,'_read_protected_file',protected)
    monkeypatch.setattr(authority,'_host_identity',lambda:('f'*64 if fault=='wrong_host' else auth.identity.host_id,auth.identity.boot_id))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,9,2,3 if fault=='expired' else 2,tzinfo=UTC)
    monkeypatch.setattr(authority,'datetime',Clock)
    if fault=='wrong_source':binding=binding.model_copy(update={'source':binding.source.model_copy(update={'commit_sha':'f'*40})})
    if fault:
        with pytest.raises(ValueError):authority.validate_local_qualification(auth.identity,binding,store=store)
    else:
        qualified=authority.validate_local_qualification(auth.identity,binding,store=store)
        observed=validate_phase_exit_receipt(qualified.evidence_refs[0],repository=repository,store=store)
        assert observed==phase and observed.outcome=='FAIL' and qualified.status=='PASS'
        assert export_phase_exit(job_id,repository=repository,store=store)==ref


def _holdout_result(store, intent):
    from packages.alpha_lifecycle.contracts.results import HoldoutEvaluationResult
    from packages.alpha_lifecycle.holdout import HoldoutOperationResult
    from packages.alpha_lifecycle.replica_store import _read
    from packages.engine_contracts.serialization import canonical_json_bytes
    import hashlib
    body = intent.body
    value = dict(schema_version='p3-holdout-operation-result-v1', holdout_request_ref=body.holdout_request_ref,
        holdout_manifest_ref=_read(store, body.holdout_evaluation_ref, HoldoutEvaluationResult).manifest_ref,
        holdout_evaluation_ref=body.holdout_evaluation_ref, holdout_replay_ref=body.holdout_replay_ref,
        executable_ref=body.executable_ref, baseline_executable_ref=body.baseline_executable_ref)
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    result = HoldoutOperationResult.model_validate_json(canonical_json_bytes(value))
    return store.put_bytes(canonical_json_bytes(result), media_type='application/json'), result


def test_parity_and_exit_bodies_derive_from_committed_holdout_and_session_notes(exit_graph):
    from pydantic import ValidationError
    from packages.alpha_lifecycle.local_campaign import parity_body, phase_exit_body, propose_operation
    from packages.alpha_lifecycle.native_request import prepare_native_commitment
    from packages.alpha_lifecycle.operation_input import NativeParityInput
    from packages.engine_contracts.serialization import canonical_json_bytes
    from scripts.p3_local_controller import derive_body
    store, intent, source, view = exit_graph
    from packages.alpha_lifecycle.parity import ParityPair
    from packages.alpha_lifecycle.replica_store import _read
    held_ref, held = _holdout_result(store, intent)
    spec_ref = _read(store, intent.body.parity_ref, ParityPair).instrument_spec_ref
    commitment = prepare_native_commitment(held.holdout_manifest_ref, spec_ref, view)
    commitment_ref = store.put_bytes(canonical_json_bytes(commitment), media_type='application/json')
    parity = parity_body(held_ref, commitment_ref, store=store)
    assert parity == dict(holdout_manifest_ref=held.holdout_manifest_ref, primary_reference_ref=held.executable_ref,
        baseline_reference_ref=held.baseline_executable_ref, instrument_spec_ref=spec_ref,
        native_request_ref=commitment_ref)
    with pytest.raises(ValidationError) as missing_id:
        propose_operation('p3-native-parity-v1', intent.input_set_ref, parity, store=store)
    assert missing_id.value.errors()[0]['loc'] == ('allowed_alpha_ids', 0)
    assert missing_id.value.errors()[0]['input'] is None
    request = derive_body(dict(workflow_operation='p3-native-parity-v1', input_set_ref=intent.input_set_ref,
        refs=dict(holdout_result_ref=held_ref, native_commitment_ref=commitment_ref)), source=source, store=store)
    parity_intent = propose_operation(request['workflow_operation'], request['input_set_ref'], request['body'],
        store=store, primary_alpha_id=request.get('primary_alpha_id'))
    assert parity_intent.allowed_alpha_ids == intent.allowed_alpha_ids
    assert parity_intent.body == NativeParityInput.model_validate(parity)
    body = phase_exit_body(intent.body.primary_selection_ref, held_ref, intent.body.parity_ref, store=store)
    from packages.alpha_lifecycle.operation_input import PhaseExitInput
    assert PhaseExitInput.model_validate(body) == intent.body
    exit_intent = propose_operation('p3-phase-exit-v1', intent.input_set_ref, body, store=store,
                                    primary_alpha_id=intent.allowed_alpha_ids[0])
    assert exit_intent == intent


@pytest.mark.parametrize('reference', ['manifest', 'selection'])
@pytest.mark.parametrize('fault', ['missing', 'wrong_type'])
def test_parity_body_derivation_rejects_unavailable_or_wrong_typed_primary_chain(exit_graph, reference, fault):
    from pydantic import ValidationError
    from packages.alpha_lifecycle.native_request import prepare_native_commitment
    from packages.alpha_lifecycle.parity import ParityPair
    from packages.alpha_lifecycle.replica_store import _read
    from packages.data_catalog.artifact_store import ArtifactIntegrityError
    from packages.engine_contracts.serialization import canonical_json_bytes
    from scripts.p3_local_controller import derive_body
    from tests.p3.test_parity_pair import _change
    store, intent, source, view = exit_graph
    held_ref, held = _holdout_result(store, intent)
    spec_ref = _read(store, intent.body.parity_ref, ParityPair).instrument_spec_ref
    commitment_ref = store.put_bytes(canonical_json_bytes(
        prepare_native_commitment(held.holdout_manifest_ref, spec_ref, view)), media_type='application/json')
    bad_ref = (held.holdout_manifest_ref.model_copy(update={'content_sha256':'0'*64, 'locator':'0'*64+'.blob'})
        if fault == 'missing' else store.put_bytes(canonical_json_bytes({}), media_type='application/json'))
    manifest_ref = (bad_ref if reference == 'manifest' else
        _change(store, held.holdout_manifest_ref, primary_selection_ref=bad_ref))
    held_ref = _change(store, held_ref, holdout_manifest_ref=manifest_ref)
    commitment_ref = _change(store, commitment_ref, manifest_ref=manifest_ref)
    with pytest.raises(ArtifactIntegrityError if fault == 'missing' else ValidationError):
        derive_body(dict(workflow_operation='p3-native-parity-v1', input_set_ref=intent.input_set_ref,
            refs=dict(holdout_result_ref=held_ref, native_commitment_ref=commitment_ref)), source=source, store=store)


def test_parity_body_refuses_a_commitment_for_another_manifest(exit_graph):
    from packages.alpha_lifecycle.local_campaign import parity_body
    from packages.engine_contracts.serialization import canonical_json_bytes
    store, intent, _, _ = exit_graph
    held_ref, _ = _holdout_result(store, intent)
    with pytest.raises(ValueError):
        parity_body(held_ref, store.put_bytes(canonical_json_bytes({}), media_type='application/json'), store=store)


def test_session_parity_pair_note_reads_back_with_native_parent_proof(exit_graph, tmp_path):
    import hashlib
    import os
    from packages.alpha_lifecycle.parity import ParityPair, SessionParityPair
    from packages.alpha_lifecycle.replica_store import _read
    from packages.engine_contracts.serialization import canonical_json_bytes
    from services.job_worker.p3_session import publish_session_ref, read_session_ref
    store, intent, source, _ = exit_graph
    value = _read(store, intent.body.parity_ref, ParityPair).model_dump(mode='json', exclude={'digest'})
    value['native_parent_proof_ref'] = intent.body.holdout_replay_ref.model_dump(mode='json')
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    pair = SessionParityPair.model_validate(value)
    ref = store.put_bytes(canonical_json_bytes(pair), media_type='application/json')
    publish_session_ref(tmp_path, 'job_parity', 'PARITY_PAIR', ref)
    assert read_session_ref(tmp_path, 'job_parity', 'PARITY_PAIR', worker_uid=os.geteuid(),
                            store=store, expected_source=source) == ref


def test_phase_exit_derives_from_plain_pair_and_retained_session_pair(exit_graph, tmp_path):
    import os
    from packages.alpha_lifecycle.local_campaign import phase_exit_body
    from packages.alpha_lifecycle.operation_input import PhaseExitInput
    from packages.alpha_lifecycle.parity import SessionParityPair
    from packages.alpha_lifecycle.replica_store import _read
    from scripts.p3_local_controller import derive_body
    from services.job_worker.p3_session import publish_session_ref, read_session_ref
    from tests.p3.test_parity_pair import _change
    store, intent, source, _ = exit_graph
    held_ref, _ = _holdout_result(store, intent)
    plain_body = phase_exit_body(intent.body.primary_selection_ref, held_ref, intent.body.parity_ref, store=store)
    assert PhaseExitInput.model_validate(plain_body) == intent.body
    # Declared unit placeholder, as in the session-note test above; no native proof authority.
    pair_ref = _change(store, intent.body.parity_ref, native_parent_proof_ref=intent.body.holdout_replay_ref)
    publish_session_ref(tmp_path, 'job_parity', 'PARITY_PAIR', pair_ref)
    observed_ref = read_session_ref(tmp_path, 'job_parity', 'PARITY_PAIR', worker_uid=os.geteuid(),
                                   store=store, expected_source=source)
    assert observed_ref == pair_ref
    request = derive_body(dict(workflow_operation='p3-phase-exit-v1', input_set_ref=intent.input_set_ref,
        refs=dict(primary_selection_ref=intent.body.primary_selection_ref, holdout_result_ref=held_ref,
                  parity_pair_ref=observed_ref)), source=source, store=store)
    assert request['primary_alpha_id'] == intent.allowed_alpha_ids[0]
    assert PhaseExitInput.model_validate(request['body']) == intent.body.model_copy(update={'parity_ref': pair_ref})
    assert _read(store, request['body']['parity_ref'], SessionParityPair).native_parent_proof_ref == intent.body.holdout_replay_ref


@pytest.mark.parametrize('fault', ['digest', 'unknown_field', 'parent_type', 'manifest', 'primary', 'baseline', 'noncanonical'])
def test_phase_exit_rejects_invalid_session_pair(exit_graph, fault):
    from pydantic import ValidationError
    from packages.alpha_lifecycle.local_campaign import phase_exit_body
    from packages.engine_contracts.serialization import canonical_json_bytes
    from tests.p3.test_parity_pair import _change
    store, intent, _, _ = exit_graph
    held_ref, held = _holdout_result(store, intent)
    changes = dict(native_parent_proof_ref=intent.body.holdout_replay_ref)
    if fault == 'unknown_field':
        changes['unexpected'] = 'untrusted'
    elif fault == 'parent_type':
        changes['native_parent_proof_ref'] = None
    elif fault in {'manifest', 'primary', 'baseline'}:
        field = {'manifest': 'manifest_ref', 'primary': 'primary_reference_ref', 'baseline': 'baseline_reference_ref'}[fault]
        changes[field] = held.holdout_replay_ref
    pair_ref = _change(store, intent.body.parity_ref, **changes)
    if fault in {'digest', 'noncanonical'}:
        value = json.loads(store.read_bytes(pair_ref))
        if fault == 'digest':
            value['digest'] = '0' * 64
        raw = canonical_json_bytes(value) if fault == 'digest' else json.dumps(value).encode()
        pair_ref = store.put_bytes(raw, media_type='application/json')
    with pytest.raises(ValidationError if fault in {'digest', 'unknown_field', 'parent_type'} else ValueError):
        phase_exit_body(intent.body.primary_selection_ref, held_ref, pair_ref, store=store)


def _bridge_lane(run_id=41, attempt=2, *, mode='GITHUB', campaign='OFFICIAL', input_ref=None, source=None):
    from types import SimpleNamespace
    from packages.alpha_lifecycle.contracts.authority import GithubRunIdentity
    identity=GithubRunIdentity(issuer_kind='GITHUB',issuer_identity='p3-authority',repository='nam176hermes/Trading-Agent',
        workflow_ref='p3-authority.yml',run_id=run_id,attempt=attempt)
    return (SimpleNamespace(mode=mode,identity=identity),
            SimpleNamespace(campaign=campaign,input_ref=input_ref,source=source))


def test_producer_v2_receipt_is_not_a_v1_receipt(committed_exit):
    """RED for S4: the real exporter output is v2; consumers accept only v1."""
    from services.job_worker.p3_phase_readback import export_phase_exit
    from packages.p3_provenance import PhaseExitReceipt
    store,repository,job_id,_,_=committed_exit
    ref=export_phase_exit(job_id,repository=repository,store=store)
    with pytest.raises(ValueError):
        PhaseExitReceipt.model_validate_json(store.read_bytes(ref))


@pytest.mark.parametrize('fault',['fail_outcome','fake_run','rehearsal'])
def test_v1_bridge_refuses_real_fail_fake_runs_and_rehearsals(committed_exit,fault):
    from services.job_worker.p3_phase_readback import export_phase_exit,phase_exit_v1
    store,repository,job_id,_,_=committed_exit
    ref=export_phase_exit(job_id,repository=repository,store=store)
    run,research=_bridge_lane(mode='FAKE' if fault=='fake_run' else 'GITHUB',
        campaign='REHEARSAL' if fault=='rehearsal' else 'OFFICIAL')
    with pytest.raises(ValueError):
        phase_exit_v1(ref,repository=repository,store=store,run=run,research=research)


@pytest.mark.parametrize('fault',[None,'other_run','local_identity','inputs','sql_changed','rejected_head',
                                   'fake_run','rehearsal','synthetic'])
def test_v1_bridge_maps_an_authenticated_github_pass(committed_exit,monkeypatch,fault):
    """Mapping proof only: SQL-authenticated readback is substituted with a PASS outcome."""
    import hashlib
    from types import SimpleNamespace
    from services.job_worker import p3_phase_readback as module
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.replica_store import _read
    from packages.engine_contracts.serialization import canonical_json_bytes
    from packages.p3_provenance import PhaseExitReceipt
    store,repository,job_id,observation,_=committed_exit
    ref=module.export_phase_exit(job_id,repository=repository,store=store)
    real=module.validate_phase_exit_receipt(ref,repository=repository,store=store)
    run,research=_bridge_lane(input_ref=real.input_ref,source=real.source)
    identity=run.identity if fault!='local_identity' else real.identity
    passed=real.model_copy(update=dict(outcome='PASS',identity=identity))
    if fault=='synthetic':
        from tests.p3.test_synthetic_provenance import sealed
        inputs=_read(store,real.input_ref,InputSet)
        synthetic=sealed(InputSet,**(inputs.model_dump(mode='json',exclude={'digest'}) |
            dict(synthetic_generation_ref=real.environment_ref)))
        synthetic_ref=store.put_bytes(canonical_json_bytes(synthetic),media_type='application/json')
        passed=passed.model_copy(update=dict(input_ref=synthetic_ref))
        research.input_ref=synthetic_ref
    monkeypatch.setattr(module,'validate_phase_exit_receipt',lambda *a,**k:passed)
    status='REJECTED' if fault=='rejected_head' else 'QUALIFIED'
    monkeypatch.setattr(module,'read_registry_event',lambda store,ref:SimpleNamespace(record=SimpleNamespace(
        lifecycle_status=SimpleNamespace(value=status),qualification_decision=SimpleNamespace(value='PASS'))))
    if fault=='other_run':
        run,_=_bridge_lane(run_id=42)
    if fault=='fake_run':
        run.mode='FAKE'      # PR77-04: refused although the substituted outcome is PASS
    if fault=='rehearsal':
        research.campaign='REHEARSAL'
    if fault=='inputs':
        research.input_ref=real.environment_ref
    if fault=='sql_changed':
        original=repository.read_phase_exit
        repository.read_phase_exit=lambda job:original(job)+b' '
    if fault:
        with pytest.raises(ValueError):
            module.phase_exit_v1(ref,repository=repository,store=store,run=run,research=research)
        return
    v1=module.phase_exit_v1(ref,repository=repository,store=store,run=run,research=research)
    assert type(v1) is PhaseExitReceipt and v1.status=='PASS'
    intent=_read(store,module.AlphaCampaignPayload.model_validate_json(canonical_json_bytes(observation['payload'])).manifest_ref,P3OperationInput)
    assert v1.epoch_id==_read(store,real.input_ref,InputSet).epoch_id
    assert v1.primary_selection_ref==intent.body.primary_selection_ref
    assert v1.exit_result_ref==real.result_ref and v1.closure_report_ref==real.closure_report_ref
    assert v1.qualified_registry_head_ref==real.registry_head_ref
    assert v1.bundle_inventory_ref.model_dump(mode='json')==observation['output_inventory_ref']
    assert (v1.qualification_workflow,v1.qualification_run_id,v1.qualification_attempt)==('p3-authority.yml',41,2)
    assert v1.source==real.source
    body={k:v for k,v in json.loads(canonical_json_bytes(v1)).items() if k!='digest'}
    assert v1.digest==hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def test_exporter_v1_reads_the_protected_run_and_inputs_itself(monkeypatch):
    """The v1 path takes no caller-supplied input binding (F-01)."""
    from types import SimpleNamespace
    from packages.alpha_lifecycle import github_research, github_run
    from scripts import export_p3_phase_exit as exporter
    run=SimpleNamespace(name='run')
    receipt=SimpleNamespace(source='source',input_ref='input')
    calls=[]
    monkeypatch.setattr(github_run,'read_github_run',lambda r,a,admission:calls.append(('run',r,a,admission)) or (run,'d'))
    monkeypatch.setattr(exporter,'validate_phase_exit_receipt',lambda ref,**k:receipt)
    monkeypatch.setattr(github_research,'read_github_research_inputs',
        lambda value,source,input_set_ref:calls.append(('inputs',value,source,input_set_ref)) or 'research')
    monkeypatch.setattr(exporter,'phase_exit_v1',lambda ref,**k:calls.append(('v1',k['run'],k['research'])) or {'v1':True})
    raw=exporter.github_v1('ref',repository=None,store=None,run_id=41,attempt=2)
    assert raw==b'{"v1":true}\n'
    assert calls==[('run',41,2,False),('inputs',run,'source','input'),('v1',run,'research')]
