from __future__ import annotations

from decimal import Decimal

import pytest

from packages.alpha_lifecycle.capacity import CapacityError, participation_samples
from packages.alpha_lifecycle.robustness import delayed_weights, regime_excess
from packages.alpha_lifecycle.trials import deterministic_trial_keys


def test_delayed_scenario_shifts_precomputed_targets() -> None:
    assert delayed_weights((1, 1, 0, 1)) == (0, 1, 1, 0)


def test_capacity_uses_only_nonzero_transitions_and_rejects_bad_volume() -> None:
    assert participation_samples(
        (Decimal("1"), Decimal("0"), Decimal("0.5")),
        (Decimal("1000000"), Decimal("1"), Decimal("500000")),
    ) == (Decimal("0.1"), Decimal("0.1"))
    with pytest.raises(CapacityError, match="E_VOLUME"):
        participation_samples((Decimal("1"),), (Decimal("0"),))


def test_regime_excess_compounds_same_labeled_samples() -> None:
    labels = ("BULL_LOW_VOL", "BEAR_LOW_VOL", "HIGH_VOL")
    result = regime_excess(
        (Decimal("0.1"), Decimal("-0.1"), Decimal("0.2")),
        (Decimal("0"), Decimal("0"), Decimal("0.1")),
        labels,
    )
    assert result["HIGH_VOL"] == Decimal("0.1")


def test_trial_keys_cover_only_seven_preregistered_paths() -> None:
    keys = deterministic_trial_keys("p3-btc-d1-e1", "a0.donchian-20-10-close-confirm")
    assert len(keys) == len(set(keys)) == 7
    assert all("attempt" not in key for key in keys)


def _trace_inputs(tmp_path):
    import hashlib
    from datetime import date,timedelta
    from packages.alpha_lifecycle.contracts.data import DailyBar
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from packages.engine_contracts.serialization import canonical_json_bytes
    from tests.p3.test_dataset import _bar
    tmp_path.chmod(0o700)
    store=LocalArtifactStore(tmp_path)
    bars=[]
    refs=[]
    for i in range(4):
        payload=_bar(date(2025,8,28)+timedelta(days=i)).model_dump(mode='json',exclude={'digest'})
        payload['quote_volume']=str(1000000*2**i)
        payload['digest']=hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        bars.append(DailyBar.model_validate(payload))
        refs.append(store.put_bytes(canonical_json_bytes(bars[-1]),media_type='application/json'))
    return store,tuple(bars),tuple(refs)


@pytest.mark.parametrize('raw,expected',[
    ((0,0,1,1),(0,0,0,0)),
    ((1,1,0,0),(0,1,1,1)),
])
def test_scenario_suppresses_terminal_signal_after_delaying(tmp_path,monkeypatch,raw,expected):
    import json
    from packages.alpha_lifecycle import evaluation
    from packages.alpha_lifecycle.contracts.data import Fold,FoldManifest
    from packages.alpha_lifecycle.contracts.policy import CandidateSpec
    from packages.alpha_lifecycle.contracts.results import PerformanceTrace
    from packages.alpha_lifecycle.metrics import CostModelV1
    from scripts.generate_p3_specs import _candidate_specs,POLICY_SOURCE
    from tests.p3.test_replica_execution import _seal
    store,bars,refs=_trace_inputs(tmp_path)
    fold=_seal(store,schema_version='p3-fold-v1',fold_id='H1',
        return_end_range=dict(start='2025-08-29',end='2025-08-31'),
        context_start='2025-08-28',decision_start='2025-08-28',decision_end='2025-08-30',
        return_count=3,decision_row_refs=refs[:-1],return_row_refs=refs[1:],snapshot_ref=refs[0])
    manifest=_seal(store,schema_version='p3-fold-manifest-v1',mode='HOLDOUT',
        folds=[Fold.model_validate_json(store.read_bytes(fold))],static_policy_digest='c'*64,dataset_evidence_ref=refs[0])
    calls=[]
    def targets(*args,**kwargs):
        calls.append('candidate')
        return raw
    monkeypatch.setattr(evaluation,'run_candidate',targets)
    # Fixed upstream signal/regime samples isolate the scenario's post-delay accounting seam.
    monkeypatch.setattr(evaluation,'regime_labels',lambda *args:('BEAR_LOW_VOL',)*4)
    result=evaluation._scenario(scenario='DELAYED',perturbation_id=None,
        spec=CandidateSpec.model_validate(_candidate_specs(json.loads(POLICY_SOURCE.read_bytes()))[0]),
        bars=bars,folds=FoldManifest.model_validate_json(store.read_bytes(manifest)),threshold=Decimal('.02'),
        costs=CostModelV1(fee_bps=10,spread_bps=5,slippage_bps=5,funding_bps=0,borrow_bps=0),
        delayed=True,store=store)
    trace=PerformanceTrace.model_validate_json(store.read_bytes(result.fold_results[0].trace_ref))
    assert calls==['candidate']
    assert tuple(sample.next_weight for sample in trace.samples)==expected
    assert Decimal(trace.samples[-1].turnover)==0
    assert (Decimal(trace.samples[-1].exit_cost)>0)==bool(expected[-1])
    if not any(expected): assert result.aggregate_metrics.total_return==0


@pytest.mark.parametrize('missing_return',[False,True])
def test_capacity_maps_each_transition_to_its_actual_accounting_day(tmp_path,missing_return):
    from packages.alpha_lifecycle.data_view import to_daily_close
    from packages.alpha_lifecycle.execution_trace import build_research_trace
    from packages.alpha_lifecycle.metrics import CostModelV1
    from packages.alpha_lifecycle.robustness import _capacity
    store,bars,refs=_trace_inputs(tmp_path)
    trace=build_research_trace(tuple(to_daily_close(bar) for bar in bars),
        tuple(map(Decimal,(1,0,1,1))),CostModelV1(fee_bps=10,spread_bps=5,slippage_bps=5,funding_bps=0,borrow_bps=0),
        fold_ref=refs[0],subject_id='synthetic',decision_row_refs=refs[:-1],return_row_refs=refs[1:],
        regimes=('BEAR_LOW_VOL',)*4)
    samples=trace.samples
    if missing_return:
        import hashlib
        from packages.engine_contracts.serialization import canonical_json_bytes
        raw=samples[1].model_dump(mode='json',exclude={'digest'})
        raw['return_row_ref']=None
        raw['digest']=hashlib.sha256(canonical_json_bytes(raw)).hexdigest()
        samples=(samples[0],type(samples[1]).model_validate(raw),*samples[2:])
        with pytest.raises(ValueError,match='effective-day row'):
            _capacity(tuple(('H1',sample) for sample in samples),'c'*64,store)
        return
    evidence=_capacity(tuple(('H1',sample) for sample in samples),'c'*64,store)
    assert tuple(map(Decimal,evidence.event_participations))==tuple(map(Decimal,('.1','.05','.025','.0125')))
    assert evidence.event_keys==('h1.0.transition','h1.1.transition','h1.2.transition','h1.3.exit')
    assert Decimal(evidence.median)==Decimal('.0375') and Decimal(evidence.peak)==Decimal('.1')


def test_p3_trace_rejects_unsuppressed_terminal_target_without_changing_raw_math(tmp_path):
    from packages.alpha_lifecycle.data_view import to_daily_close
    from packages.alpha_lifecycle.execution_trace import build_research_trace,build_trace_math
    from packages.alpha_lifecycle.metrics import CostModelV1
    _,bars,refs=_trace_inputs(tmp_path)
    rows=tuple(to_daily_close(bar) for bar in bars)
    weights=tuple(map(Decimal,(0,0,0,1)))
    costs=CostModelV1(fee_bps=10,spread_bps=5,slippage_bps=5,funding_bps=0,borrow_bps=0)
    assert len(build_trace_math(rows,weights,costs))==4
    with pytest.raises(ValueError,match='terminal'):
        build_research_trace(rows,weights,costs,fold_ref=refs[0],subject_id='synthetic',
            decision_row_refs=refs[:-1],return_row_refs=refs[1:],regimes=('BEAR_LOW_VOL',)*4)
