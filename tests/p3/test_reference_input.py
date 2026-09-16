"""Synthetic calculation inputs; publication and custody authority are separate."""
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN, localcontext
from types import SimpleNamespace

import pytest

from packages.alpha_lifecycle import executable_reference as reference
from packages.alpha_lifecycle.baselines import BaselineId, BaselineResultV1
from packages.alpha_lifecycle.contracts.execution import HoldoutManifest, InstrumentSpec
from packages.alpha_lifecycle.data_view import _buffer_open
from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from scripts.generate_p3_specs import _candidate_specs, POLICY_SOURCE
from tests.p3.test_dataset import _bar
from tests.p3.test_replica_execution import _seal


@pytest.fixture(scope='module')
def reference_seed(tmp_path_factory):
    root=tmp_path_factory.mktemp('reference-inputs')
    store=LocalArtifactStore(root)
    opaque=store.put_bytes(b'{}',media_type='application/json')
    source=dict(commit_sha='a'*40,tree_sha='b'*40,closure_schema_version='v1',
        closure_policy_sha256='c'*64,closure_sha256='d'*64)
    context_start=date(2024,11,4)
    def bar_ref(day):
        value=_bar(day).model_dump(mode='json',exclude={'digest'})
        # Oscillation includes loss/re-entry and monthly/weekly baseline decisions.
        close=100+(day.toordinal()%40)-20
        value.update(open=str(close+1),close=str(close),high=str(close+2),low=str(close-2))
        return _seal(store,**value)
    context_refs=[]
    for i in range(2800):
        day=date(2018,1,1)+timedelta(days=i)
        if day<context_start:
            digest=hashlib.sha256(day.isoformat().encode()).hexdigest()
            # Deliberately absent: only 300 prior closes plus the scored anchor are permitted.
            context_refs.append(ArtifactRefV1(content_sha256=digest,size_bytes=1,
                media_type='application/json',locator=digest+'.blob'))
        else:
            context_refs.append(bar_ref(day))
    holdout_refs=tuple(bar_ref(date(2025,9,1)+timedelta(days=i)) for i in range(365))
    buffer_ref=store.put_bytes(canonical_json_bytes(_buffer_open(_bar(date(2026,9,1)))),media_type='application/json')
    def dataset(segment,start,end,refs):
        return _seal(store,schema_version='p3-dataset-evidence-v1',snapshot_ref=opaque,
            query_digest='1'*64,segment=segment,date_range=dict(start=start,end=end),
            usable_rows=len(refs),row_refs=refs,
            ordered_rows_digest=hashlib.sha256(canonical_json_bytes([r.content_sha256 for r in refs])).hexdigest(),
            vintage_class='RETROSPECTIVE_CURRENT_ARCHIVE',observed_cutoff='2026-09-05T12:00:02Z',
            limitations=['retrospective.current_archive'])
    context=dataset('RESEARCH','2018-01-01','2025-08-31',context_refs)
    holdout=dataset('HOLDOUT','2025-09-01','2026-08-31',holdout_refs)
    buffer=dataset('BUFFER','2026-09-01','2026-09-01',(buffer_ref,))
    environment=_seal(store,schema_version='p3-environment-identity-v1',python_version='3.11',
        root_lock_digest='a'*64,native_manifest_digest='b'*64,sandbox_policy_digest='c'*64,
        platform='linux-x86_64',decimal_precision=50)
    costs=dict(fee_bps=10,spread_bps=5,slippage_bps=5,funding_bps=0,borrow_bps=0)
    threshold=_seal(store,schema_version='p3-regime-threshold-v1',policy_digest='c'*64,
        training_dataset_ref=context,training_range=dict(start='2018-01-01',end='2021-08-31'),
        sample_count=1276,ordered_volatility_digest='1'*64,threshold='0')
    inputs=_seal(store,schema_version='p3-input-set-v1',source=source,epoch_id='synthetic',
        policy_digest='c'*64,family_digest='e'*64,dataset_evidence_ref=context,
        fold_manifest_ref=opaque,pit_proof_ref=opaque,environment_ref=environment,
        cost_model=costs,
        regime_threshold_ref=threshold,integration_receipt_ref=opaque)
    result=BaselineResultV1(baseline_id=BaselineId.BUY_AND_HOLD,baseline_version='1.0.0',
        dataset_snapshot_sha256=opaque.content_sha256,
        cost_model_sha256=hashlib.sha256(canonical_json_bytes(costs)).hexdigest(),
        metrics_sha256='b'*64,total_return=Decimal(0))
    baseline=_seal(store,schema_version='p3-baseline-selection-v1',pack_ref=opaque,
        selected_id=result.baseline_id.value,selected_result=result,selection_policy_digest='c'*64,
        baseline_replay_proof_ref=opaque)
    registration=_seal(store,schema_version='p3-registration-proof-v1',input_set_ref=inputs,
        baseline_selection_ref=baseline,candidate_head_refs=(opaque,)*4,publication_ref=opaque)
    review=_seal(store,schema_version='p3-family-review-v1',input_set_ref=inputs,
        candidate_report_refs=(opaque,)*4,trial_outcome_refs=(opaque,),review_ref=opaque,complete_disclosure=True)
    candidate=store.put_bytes(canonical_json_bytes(_candidate_specs(json.loads(POLICY_SOURCE.read_bytes()))[0]),media_type='application/json')
    primary=_seal(store,schema_version='p3-primary-selection-v1',family_review_ref=review,
        selection_policy_digest='c'*64,primary_alpha_id='a0.donchian-20-10-close-confirm',
        primary_version='1.0.0',primary_candidate_head_ref=opaque,outcome='SELECTED')
    manifest_ref=_seal(store,schema_version='p3-holdout-manifest-v1',source=source,
        primary_selection_ref=primary,candidate_spec_ref=candidate,research_registration_ref=registration,
        holdout_dataset_ref=holdout,context_dataset_ref=context,buffer_ref=buffer,
        policy_digest='c'*64,selected_baseline=result.baseline_id.value,environment_ref=environment)
    assumptions=_seal(store,schema_version='p3-research-instrument-v1',source=source,policy_digest='c'*64,
        instrument='BTCUSDT.BINANCE',price_increment='0.01',size_increment='0.00001',quote_quantum='0.01',
        minimum_notional='10',classification='APPROVED_RESEARCH_ASSUMPTION_NOT_CURRENT_OR_HISTORICAL_VENUE_FILTER')
    spec_ref=_seal(store,schema_version='p3-instrument-spec-v1',instrument='BTCUSDT.BINANCE',
        security_master_ref=assumptions,price_increment='0.01',size_increment='0.00001',quote_quantum='0.01',
        minimum_notional='10',base_currency='BTC',quote_currency='USDT',
        historical_rule_claim='SOURCE_BOUND_SIMULATION_NOT_HISTORICAL_EXCHANGE_RULES')
    return root,manifest_ref,spec_ref,tuple(context_refs),holdout_refs,buffer_ref


@pytest.fixture
def reference_input(reference_seed,tmp_path):
    root,manifest_ref,spec_ref,context,holdout,buffer=reference_seed
    output=tmp_path/'output';output.mkdir(mode=0o700)
    store=ReplicaArtifactStore(root,output)
    manifest=HoldoutManifest.model_validate_json(store.read_bytes(manifest_ref))
    spec=InstrumentSpec.model_validate_json(store.read_bytes(spec_ref))
    reads=[]
    writes=[]
    def read(ref):
        reads.append(ref)
        return store.read_bytes(ref)
    def write(raw,*,media_type):
        writes.append(raw)
        return store.put_bytes(raw,media_type=media_type)
    reader=SimpleNamespace(read_bytes=read,put_bytes=write)
    return SimpleNamespace(manifest=manifest,spec=spec,reader=reader,store=store,
        reads=reads,writes=writes,context=context,holdout=holdout,buffer=buffer)


def _changed(value,**changes):
    value=value.model_dump(mode='json',exclude={'digest'}) if hasattr(value,'model_dump') else dict(value)
    value.pop('digest',None)
    value.update(changes)
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return value


def test_reference_reads_only_tail_context_and_buffer_open(reference_input):
    x=reference_input
    result=reference.run_executable_reference(x.manifest,x.spec,x.reader)
    assert set(r.content_sha256 for r in x.reads).isdisjoint(r.content_sha256 for r in x.context[:-301])
    assert all(ref in x.reads for ref in (*x.context[-301:],*x.holdout,x.buffer))
    assert not any(ref.media_type=='application/vnd.apache.parquet' for ref in x.reads)
    assert result.ending_position=='0'
    trace=json.loads(x.store.read_bytes(result.fill_trace_ref))
    assert trace[0]['source_artifact_ref']==x.context[-1].model_dump(mode='json')
    assert trace[-1]['source_artifact_ref']==x.holdout[-1].model_dump(mode='json')
    assert trace[-1]['source_day']=='2026-08-31'
    targets=json.loads(x.store.read_bytes(result.transition_trace_ref))['targets']
    assert len(targets)==366 and targets[-1]==0
    assert result.instrument_spec_ref==x.store.put_bytes(canonical_json_bytes(x.spec),media_type='application/json')


def test_reference_separates_300_unscored_context_closes_from_the_anchor(reference_input):
    x=reference_input
    reference.run_executable_reference(x.manifest,x.spec,x.reader)
    context_reads=[ref for ref in x.reads if ref in x.context]
    assert context_reads==list(x.context[-301:])
    assert len(context_reads[:-1])==300


@pytest.mark.parametrize('fault',[
    'source','policy','environment','baseline','context_role','holdout_role','buffer_role',
    'context_range','holdout_range','buffer_range','ordered_digest','row_duplicate',
    'row_day_boundary','buffer_daily_bar','primary','registration','noncanonical','ref_hash',
    'price_increment','size_increment','quote_quantum','minimum_notional',
    'assumption_source','assumption_policy','assumption_classification','assumption_values',
])
def test_reference_rejects_invalid_inputs_before_outputs(reference_input,fault):
    x=reference_input
    manifest=x.manifest
    spec=x.spec
    def change_ref(ref,**changes):
        return x.store.put_bytes(canonical_json_bytes(_changed(json.loads(x.store.read_bytes(ref)),**changes)),media_type='application/json')
    changes={}
    if fault=='source': changes['source']={**manifest.source.model_dump(mode='json'),'commit_sha':'f'*40}
    elif fault=='policy': changes['policy_digest']='f'*64
    elif fault=='environment': changes['environment_ref']=manifest.primary_selection_ref
    elif fault=='baseline': changes['selected_baseline']='B0_CASH'
    elif fault.endswith('_role'):
        name={'context_role':'context_dataset_ref','holdout_role':'holdout_dataset_ref','buffer_role':'buffer_ref'}[fault]
        changes[name]=manifest.holdout_dataset_ref if fault!='holdout_role' else manifest.context_dataset_ref
    elif fault.endswith('_range'):
        name={'context_range':'context_dataset_ref','holdout_range':'holdout_dataset_ref','buffer_range':'buffer_ref'}[fault]
        old=json.loads(x.store.read_bytes(getattr(manifest,name)))['date_range']
        changes[name]=change_ref(getattr(manifest,name),date_range={**old,'end':'2026-09-02'})
    elif fault in {'ordered_digest','row_duplicate','row_day_boundary','buffer_daily_bar'}:
        ref=manifest.buffer_ref if fault=='buffer_daily_bar' else manifest.holdout_dataset_ref
        document=json.loads(x.store.read_bytes(ref))
        if fault=='ordered_digest': document['ordered_rows_digest']='f'*64
        elif fault=='row_duplicate': document['row_refs'][10]=document['row_refs'][9]
        elif fault=='row_day_boundary':
            document['row_refs'][0]=change_ref(x.holdout[0],opened_at='2025-09-01T01:00:00Z',
                closed_at_exclusive='2025-09-02T01:00:00Z').model_dump(mode='json')
        else:
            document['row_refs']=[x.store.put_bytes(canonical_json_bytes(_bar(date(2026,9,1))),media_type='application/json').model_dump(mode='json')]
        if fault!='ordered_digest':
            document['ordered_rows_digest']=hashlib.sha256(canonical_json_bytes([r['content_sha256'] for r in document['row_refs']])).hexdigest()
        changes['buffer_ref' if fault=='buffer_daily_bar' else 'holdout_dataset_ref']=x.store.put_bytes(canonical_json_bytes(_changed(document)),media_type='application/json')
    elif fault=='primary': changes['primary_selection_ref']=change_ref(manifest.primary_selection_ref,primary_alpha_id='a1.dual-sma-50-200')
    elif fault=='registration': changes['research_registration_ref']=change_ref(manifest.research_registration_ref,input_set_ref=manifest.primary_selection_ref)
    elif fault=='noncanonical':
        document=json.loads(x.store.read_bytes(manifest.buffer_ref))
        changes['buffer_ref']=x.store.put_bytes(json.dumps(document,indent=2).encode(),media_type='application/json')
    elif fault=='ref_hash':
        original=x.reader.read_bytes
        x.reader.read_bytes=lambda ref: original(manifest.holdout_dataset_ref if ref==manifest.buffer_ref else ref)
    elif fault.startswith('assumption_'):
        field,value={
            'assumption_source':('source',{**manifest.source.model_dump(mode='json'),'commit_sha':'f'*40}),
            'assumption_policy':('policy_digest','f'*64),
            'assumption_classification':('classification','CURRENT_VENUE_RULES'),
            'assumption_values':('minimum_notional','0'),
        }[fault]
        spec=InstrumentSpec.model_validate(_changed(spec,security_master_ref=change_ref(spec.security_master_ref,**{field:value})))
    else: spec=InstrumentSpec.model_validate(_changed(spec,**{fault:'1'}))
    manifest=HoldoutManifest.model_validate(_changed(manifest,**changes))
    with pytest.raises(ValueError):
        reference.run_executable_reference(manifest,spec,x.reader)
    assert not x.writes


def test_reference_full_result_is_independent_of_caller_decimal_context(reference_input):
    x=reference_input
    with localcontext(prec=50,rounding=ROUND_HALF_EVEN):
        expected=reference.run_executable_reference(x.manifest,x.spec,x.reader)
    with localcontext(prec=12,rounding=ROUND_CEILING) as context:
        assert reference.run_executable_reference(x.manifest,x.spec,x.reader)==expected
        assert (context.prec,context.rounding)==(12,ROUND_CEILING)


@pytest.mark.parametrize('baseline',tuple(BaselineId))
def test_selected_baseline_reference_uses_same_opens_and_flat_reset(reference_input,baseline):
    x=reference_input
    registration=json.loads(x.store.read_bytes(x.manifest.research_registration_ref))
    selection=json.loads(x.store.read_bytes(ArtifactRefV1.model_validate(registration['baseline_selection_ref'])))
    result=selection['selected_result'];result.pop('result_sha256')
    result['baseline_id']=baseline.value
    selection=_changed(selection,selected_id=baseline.value,
        selected_result=BaselineResultV1.model_validate_json(json.dumps(result)).model_dump(mode='json'))
    registration=_changed(registration,baseline_selection_ref=x.store.put_bytes(canonical_json_bytes(selection),media_type='application/json'))
    manifest=HoldoutManifest.model_validate(_changed(x.manifest,selected_baseline=baseline.value,
        research_registration_ref=x.store.put_bytes(canonical_json_bytes(registration),media_type='application/json')))
    actual=reference.run_selected_baseline_reference(manifest,x.spec,x.reader)
    targets=json.loads(x.store.read_bytes(actual.transition_trace_ref))['targets']
    assert len(targets)==366 and targets[-1]==0
    if baseline is BaselineId.CASH: assert set(targets)=={0} and actual.ending_cash=='100000'
    elif baseline in {BaselineId.BUY_AND_HOLD,BaselineId.EQUAL_WEIGHT}: assert targets==[1]*365+[0]
    else:
        from packages.alpha_lifecycle.baselines import baseline_weights_with_reset
        from packages.alpha_lifecycle.contracts.data import DailyBar
        from packages.alpha_lifecycle.data_view import to_daily_close
        rows=tuple(to_daily_close(DailyBar.model_validate_json(x.store.read_bytes(ref))) for ref in (*x.context[-301:],*x.holdout))
        expected=baseline_weights_with_reset(baseline,rows,score_start=rows[300].closed_at)
        assert targets==[int(v) for v in expected[:-1]]+[0]
        assert targets[0]==0  # 2025-08-31 is neither a month change nor Monday.
    trace=json.loads(x.store.read_bytes(actual.fill_trace_ref))
    assert trace[0]['cash_after']=='100000' and trace[0]['position_after']=='0'
    assert actual.ending_position=='0'


def test_holdout_research_uses_one_h1_and_keeps_the_selected_baseline(reference_input):
    from packages.alpha_lifecycle import evaluation
    from packages.alpha_lifecycle.contracts.data import Fold
    from packages.alpha_lifecycle.contracts.results import PerformanceTrace
    x=reference_input
    result=evaluation.evaluate_holdout(x.manifest,x.spec,x.reader)
    scenarios=(result.primary_base,result.primary_double_cost,result.primary_delayed,result.baseline_base)
    assert tuple(s.scenario for s in scenarios)==('BASE','DOUBLE_COST','DELAYED','BASE')
    assert all(s.perturbation_id is None and len(s.fold_results)==1 for s in scenarios)
    traces=[]
    for scenario in scenarios:
        assert scenario.fold_results[0].fold_id=='H1'
        trace=PerformanceTrace.model_validate_json(x.store.read_bytes(scenario.fold_results[0].trace_ref))
        traces.append(trace)
        fold=Fold.model_validate_json(x.store.read_bytes(trace.fold_ref))
        assert (str(fold.context_start),str(fold.decision_start),str(fold.decision_end))==('2024-11-04','2025-08-31','2026-08-30')
        assert fold.return_count==365 and fold.decision_row_refs==(x.context[-1],*x.holdout[:-1])
        assert fold.return_row_refs==x.holdout and len(trace.samples)==366
        assert trace.samples[0].kind=='ENTRY' and trace.samples[-1].next_weight==trace.samples[-1].applied_weight
        assert set(sample.regime for sample in trace.samples)=={'HIGH_VOL'}
    assert all(t.subject_id==traces[0].subject_id for t in traces[:3])
    assert traces[-1].subject_id=='B1_BUY_AND_HOLD'
    assert all(s.next_weight==1 for s in traces[-1].samples)
    assert Decimal(traces[-1].samples[-1].exit_cost)>0
    # This synthetic A0 never enters; valid failed performance must still be returned.
    assert result.primary_base.aggregate_metrics.total_return==0 and result.capacity.no_trades
    assert not set(x.reads).intersection(x.context[:-301])
    assert not any(ref.media_type=='application/vnd.apache.parquet' for ref in x.reads)


def test_holdout_research_freezes_decimal_context(reference_input):
    from packages.alpha_lifecycle import evaluation
    x=reference_input
    with localcontext(prec=50,rounding=ROUND_HALF_EVEN):
        expected=evaluation.evaluate_holdout(x.manifest,x.spec,x.reader)
    with localcontext(prec=12,rounding=ROUND_CEILING) as context:
        assert evaluation.evaluate_holdout(x.manifest,x.spec,x.reader)==expected
        assert (context.prec,context.rounding)==(12,ROUND_CEILING)


def test_holdout_scenarios_use_fixed_costs_and_delay_for_one_active_candidate(reference_input):
    from packages.alpha_lifecycle import evaluation
    from packages.alpha_lifecycle.contracts.results import PerformanceTrace
    x=reference_input
    candidate=_candidate_specs(json.loads(POLICY_SOURCE.read_bytes()))[1]
    primary=json.loads(x.store.read_bytes(x.manifest.primary_selection_ref))
    manifest=HoldoutManifest.model_validate(_changed(x.manifest,
        candidate_spec_ref=x.store.put_bytes(canonical_json_bytes(candidate),media_type='application/json'),
        primary_selection_ref=x.store.put_bytes(canonical_json_bytes(_changed(primary,
            primary_alpha_id=candidate['alpha_id'])),media_type='application/json')))
    result=evaluation.evaluate_holdout(manifest,x.spec,x.reader)
    traces=[PerformanceTrace.model_validate_json(x.store.read_bytes(s.fold_results[0].trace_ref))
        for s in (result.primary_base,result.primary_double_cost,result.primary_delayed)]
    base,double,delayed=traces
    targets=tuple(s.next_weight for s in base.samples)
    assert set(targets)=={0,1} and not result.capacity.no_trades
    assert tuple(s.next_weight for s in double.samples)==targets
    assert tuple(s.next_weight for s in delayed.samples)==(0,*targets[:-2],targets[-3])
    assert all(Decimal(b.transition_cost)==2*Decimal(a.transition_cost) and Decimal(b.exit_cost)==2*Decimal(a.exit_cost)
        for a,b in zip(base.samples,double.samples,strict=True))
    assert all(Decimal(s.carry_cost)==0 for s in double.samples)
    assert result.primary_double_cost.aggregate_metrics.total_return<result.primary_base.aggregate_metrics.total_return
    assert all(key.startswith('h1.') for key in result.capacity.event_keys)


def test_holdout_result_rejects_oos_or_mislabelled_scenarios(reference_input):
    from packages.alpha_lifecycle import evaluation
    from packages.alpha_lifecycle.contracts.results import HoldoutEvaluationResult
    x=reference_input
    result=evaluation.evaluate_holdout(x.manifest,x.spec,x.reader)
    for field,fault in (('primary_base','fold'),('baseline_base','fold'),
        ('primary_double_cost','scenario'),('primary_delayed','perturbation')):
        scenario=getattr(result,field)
        if fault=='fold': changes=dict(fold_results=[_changed(scenario.fold_results[0],fold_id='F1')])
        elif fault=='scenario': changes=dict(scenario='BASE')
        else: changes=dict(perturbation_id='p01')
        with pytest.raises(ValueError,match='holdout scenarios'):
            HoldoutEvaluationResult.model_validate_json(canonical_json_bytes(_changed(result,**{field:_changed(scenario,**changes)})))


@pytest.mark.parametrize('fault',['policy','dataset','training','sample_count','negative'])
def test_holdout_rejects_threshold_before_writing(reference_input,fault):
    from packages.alpha_lifecycle import evaluation
    x=reference_input
    def read(ref): return json.loads(x.store.read_bytes(ArtifactRefV1.model_validate(ref)))
    def put(value,**changes): return x.store.put_bytes(canonical_json_bytes(_changed(value,**changes)),media_type='application/json')
    registration=read(x.manifest.research_registration_ref)
    inputs=read(registration['input_set_ref'])
    changes={
        'policy':dict(policy_digest='f'*64),
        'dataset':dict(training_dataset_ref=x.manifest.holdout_dataset_ref),
        'training':dict(training_range=dict(start='2018-01-01',end='2025-08-30')),
        'sample_count':dict(sample_count=1),
        'negative':dict(threshold='-0.01'),
    }[fault]
    inputs_ref=put(inputs,regime_threshold_ref=put(read(inputs['regime_threshold_ref']),**changes))
    primary=read(x.manifest.primary_selection_ref)
    family_ref=put(read(primary['family_review_ref']),input_set_ref=inputs_ref)
    manifest=HoldoutManifest.model_validate(_changed(x.manifest,
        research_registration_ref=put(registration,input_set_ref=inputs_ref),
        primary_selection_ref=put(primary,family_review_ref=family_ref)))
    with pytest.raises(ValueError,match='holdout regime threshold'):
        evaluation.evaluate_holdout(manifest,x.spec,x.reader)
    assert not x.writes
