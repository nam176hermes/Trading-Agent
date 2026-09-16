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
def exit_graph(reference_seed, monkeypatch):
    """Owner selection/qualification and native receipts are declared unit inputs.

    Holdout arithmetic, replay readback, executable traces, both comparisons and
    exit recomputation are real. This fixture cannot qualify an official run.
    """
    import hashlib
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
    return store, _read(store, intent_ref, P3OperationInput), inputs.source


def test_parent_recomputes_all_thirteen_checks_and_keeps_failed_primary(exit_graph):
    from packages.alpha_lifecycle.phase_exit import evaluate_phase_exit
    store, intent, source = exit_graph
    before = set(store._root.iterdir())
    result = evaluate_phase_exit(intent, expected_source=source, store=store)
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
    proposal = prepare_phase_exit(intent, store=store, **arguments)
    assert proposal.request.stage == 'EXIT_DECISION'
    assert len(proposal.entries) == len(proposal.request.proposed_event_refs) == len(proposal.request.expected_heads) == 1
    event = read_registry_event(store, proposal.request.proposed_event_refs[0])
    assert event.record.lifecycle_status.value == 'REJECTED'
    assert event.predecessor_sha256 == intent.body.current_primary_head_ref.content_sha256
    evidence = _read(store, proposal.request.evidence_ref, PrePublicationEvidence)
    assert evidence.qualification_bundle_ref == intent.body.primary_qualification_ref
    assert prepare_phase_exit(intent, store=ReadbackStore(store, store), **arguments) == proposal


@pytest.mark.parametrize('fault', ['source', 'head', 'baseline_role', 'replay_trial', 'summary'])
def test_parent_rejects_unbound_exit_evidence_without_writing(exit_graph, fault):
    from packages.alpha_lifecycle.phase_exit import evaluate_phase_exit
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.replica_store import _read
    from packages.alpha_lifecycle.contracts.results import ReplayProof
    from tests.p3.test_parity_pair import _change, _put
    store, intent, source = exit_graph
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
    with pytest.raises(ValueError): evaluate_phase_exit(altered, expected_source=source, store=store)
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
    store, intent, source = exit_graph
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
    result = evaluate_phase_exit(intent, expected_source=source, store=store)
    assert result.verdict == 'HELD'
    assert not next(check for check in result.checks if check.check_id == 'PARITY').passed
    with pytest.raises(ValueError, match='HELD'):
        prepare_phase_exit(intent, expected_source=source, job_id='p3-unit-exit',
            observed_at=datetime(2026,9,2,tzinfo=UTC), expires_at=datetime(2026,9,3,tzinfo=UTC), store=store)
    assert set(store._root.iterdir()) == before
