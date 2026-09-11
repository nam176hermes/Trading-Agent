"""Portable replica execution proves source behavior, never runtime authority."""
from pathlib import Path
import sys
import pytest

from packages.alpha_lifecycle.contracts.execution import BaselineManifest,InputSet
from packages.alpha_lifecycle.contracts.results import BaselinePack,BaselineSelection,ReplayProof
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
from tests.p3.test_replica_execution import baseline_inputs


def _cli_authorization(store, intent_ref, source):
    """Synthetic review/authority for CLI source tests, never protected authority."""
    from datetime import UTC, datetime, timedelta
    from uuid import UUID
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from tests.p3.test_replica_execution import _seal
    intent = P3OperationInput.model_validate_json(store.read_bytes(intent_ref))
    now = datetime.now(UTC)
    def utc(value):
        return value.isoformat(timespec='microseconds').replace('+00:00','Z')
    safe = dict(broker=False, live=False, network=False, production=False)
    review_ref = _seal(store, schema_version='p3-review-approval-v1', source=source,
        subject_digests=[intent.digest], operator_identity='synthetic-operator', reviewer_identity='synthetic-reviewer',
        review_execution_id='synthetic-execution', verdict='APPROVED', issued_at=utc(now-timedelta(minutes=1)),
        expires_at=utc(now+timedelta(hours=1)), evidence_ref=store.put_bytes(b'{}', media_type='application/json'), authority=safe)
    return _seal(store, schema_version='p3-run-authorization-v1', input_set_ref=intent.input_set_ref,
        review_ref=review_ref, operation=intent.operation, allowed_alpha_ids=intent.allowed_alpha_ids,
        issued_at=utc(now), expires_at=utc(now+timedelta(minutes=30)), nonce=str(UUID(int=2)), issuer_workflow='p3-authority.yml',
        issuer_run_id=1, issuer_attempt=1, authority=safe)


@pytest.mark.parametrize('fault',['fold_policy','threshold_policy','threshold_dataset','fold_snapshot','pit_dataset','pit_folds','pit_vintage','fold_mode','dataset_segment','noncanonical_folds','training_before_dataset','training_overlaps_oos','pit_limitations','missing_revision','missing_no_future'])
def test_baseline_rejects_misbound_input_graph_before_any_replica(tmp_path,monkeypatch,fault):
    import json
    from packages.alpha_lifecycle import baseline_campaign as module
    from tests.p3.test_replica_execution import _seal
    store, manifest_ref = baseline_inputs(tmp_path/'inputs')
    def read(ref):
        return json.loads(store.read_bytes(ref))
    def seal(value):
        value.pop('digest',None)
        return _seal(store,**value)
    manifest = read(manifest_ref)
    inputs = read(BaselineManifest.model_validate(manifest).input_set_ref)
    typed = InputSet.model_validate(inputs)
    folds,threshold,pit,dataset = (read(ref) for ref in (typed.fold_manifest_ref,typed.regime_threshold_ref,typed.pit_proof_ref,typed.dataset_evidence_ref))
    other = store.put_bytes(b'{"other":true}',media_type='application/json').model_dump(mode='json')
    if fault == 'fold_policy':
        folds['static_policy_digest'] = '9'*64
    elif fault == 'threshold_policy':
        threshold['policy_digest'] = '9'*64
    elif fault == 'threshold_dataset':
        threshold['training_dataset_ref'] = other
    elif fault == 'training_before_dataset':
        threshold['training_range']['start'] = '2019-12-31'
    elif fault == 'training_overlaps_oos':
        threshold['training_range']['end'] = folds['folds'][0]['decision_start']
    elif fault == 'pit_limitations':
        pit['limitations'] = ['synthetic-limitation']
    elif fault.startswith('missing_'):
        field = 'revision_proof_ref' if fault == 'missing_revision' else 'no_future_suite_ref'
        pit[field] = dict(content_sha256='e'*64,size_bytes=1,media_type='application/json',locator='e'*64+'.blob')
    elif fault == 'fold_snapshot':
        folds['folds'][0]['snapshot_ref'] = other
        folds['folds'][0] = read(seal(folds['folds'][0]))
    elif fault == 'pit_dataset':
        pit['dataset_ref'] = other
    elif fault == 'pit_folds':
        pit['fold_manifest_ref'] = other
    elif fault == 'pit_vintage':
        pit.update(vintage_class='HISTORICAL_VINTAGE_VERIFIED',historical_vintage_verified=True)
    elif fault == 'fold_mode':
        folds['mode'] = 'HOLDOUT'
        folds['folds'] = [folds['folds'][0]]
        folds['folds'][0]['fold_id'] = 'H1'
        folds['folds'][0] = read(seal(folds['folds'][0]))
    elif fault == 'dataset_segment':
        dataset.update(segment='HOLDOUT',usable_rows=365,row_refs=dataset['row_refs'][:365])
        inputs['dataset_evidence_ref'] = seal(dataset).model_dump(mode='json')
        folds['dataset_evidence_ref'] = inputs['dataset_evidence_ref']
        threshold['training_dataset_ref'] = inputs['dataset_evidence_ref']
        pit['dataset_ref'] = inputs['dataset_evidence_ref']
    folds_ref = seal(folds)
    if fault == 'noncanonical_folds':
        folds_ref = store.put_bytes(json.dumps(read(folds_ref),indent=2).encode(),media_type='application/json')
    inputs['fold_manifest_ref'] = folds_ref.model_dump(mode='json')
    if fault != 'pit_folds':
        pit['fold_manifest_ref'] = inputs['fold_manifest_ref']
    inputs['regime_threshold_ref'] = seal(threshold).model_dump(mode='json')
    inputs['pit_proof_ref'] = seal(pit).model_dump(mode='json')
    manifest['input_set_ref'] = seal(inputs).model_dump(mode='json')
    manifest_ref = seal(manifest)
    def no_child(*args,**kwargs):
        raise AssertionError('a misbound input graph reached replica execution')
    monkeypatch.setattr(module,'run_replays',no_child)
    with pytest.raises(ValueError,match=None if fault.startswith('missing_') else 'research input|canonical'):
        module.execute_baseline_manifest(manifest_ref,store,logical_trial_id='synthetic',output_root=tmp_path/'unused')


@pytest.mark.parametrize('entrypoint',['helper','cli','cli_without_authorization'])
def test_baseline_operation_returns_selection_after_three_real_child_runs(tmp_path,monkeypatch,capsys,entrypoint):
    from packages.alpha_lifecycle.baseline_campaign import execute_baseline_manifest
    from packages.alpha_lifecycle import sandbox
    store, manifest_ref = baseline_inputs(tmp_path/'inputs')
    manifest = BaselineManifest.model_validate_json(store.read_bytes(manifest_ref))
    inputs = InputSet.model_validate_json(store.read_bytes(manifest.input_set_ref))
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(sandbox,'require_official_sandbox',lambda value:Path('/usr/bin/bwrap'))
    executor = BubblewrapExecutor(store=store,store_root=tmp_path/'inputs',release_root=root,
        python=Path(sys.executable),source=inputs.source,environment_ref=inputs.environment_ref,
        sandbox_policy_digest='c'*64)
    calls = []
    def argv(request,result,output,seccomp_fd):
        calls.append(output.name)
        return (sys.executable,'-I','-B',str(root/'scripts/run_p3_evaluation_child.py'),
                str(request),str(tmp_path/'inputs'),str(result))
    monkeypatch.setattr(executor,'_argv',argv)
    if entrypoint == 'helper':
        selection = execute_baseline_manifest(manifest_ref,executor,logical_trial_id='synthetic-baselines',output_root=tmp_path/'runs')
    else:
        from scripts import run_p3_alpha_campaign as command
        from tests.p3.test_replica_execution import _seal
        intent_ref = _seal(store,schema_version='p3-operation-input-v1',
            workflow_operation='p3-baselines-v1',operation='BASELINES',
            input_set_ref=manifest.input_set_ref,allowed_alpha_ids=[],
            body={'baseline_manifest_ref':manifest_ref})
        def child_executor(**kwargs):
            executor._store = kwargs['store']
            return executor
        monkeypatch.setattr(command,'BubblewrapExecutor',child_executor)
        for name,value in [('manifest',intent_ref),('source',inputs.source),('environment',inputs.environment_ref)]:
            (tmp_path/name).write_bytes(canonical_json_bytes(value))
        monkeypatch.setattr(sys,'argv',[str(command.__file__),
            '--manifest-ref',str(tmp_path/'manifest'),'--source',str(tmp_path/'source'),
            '--environment-ref',str(tmp_path/'environment'),'--store',str(tmp_path/'inputs'),
            '--release',str(root),'--python',sys.executable,'--sandbox-policy-digest','c'*64,
            '--logical-trial-id','p3-baselines-v1','--output',str(tmp_path/'runs')])
        if entrypoint == 'cli_without_authorization':
            with pytest.raises(ValueError, match='authorization'):
                command.main()
            assert calls == []
            return
        authorization_ref = _cli_authorization(store, intent_ref, inputs.source)
        (tmp_path/'authorization').write_bytes(canonical_json_bytes(authorization_ref))
        monkeypatch.setattr(sys,'argv',[*sys.argv,'--job-id','job_baseline',
            '--authorization-ref',str(tmp_path/'authorization')])
        import hashlib
        before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path/'inputs').iterdir()}
        command.main()
        assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path/'inputs').iterdir()} == before
        from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore
        store = ReplicaArtifactStore(tmp_path/'inputs',tmp_path/'runs'/'artifacts')
        selection = BaselineSelection.model_validate_json(capsys.readouterr().out)
        raw_selection=canonical_json_bytes(selection)
        assert (tmp_path/'runs'/'artifacts'/(hashlib.sha256(raw_selection).hexdigest()+'.blob')).read_bytes() == raw_selection
    proof = ReplayProof.model_validate_json(store.read_bytes(selection.baseline_replay_proof_ref))
    pack = BaselinePack.model_validate_json(store.read_bytes(selection.pack_ref))
    assert calls == ['r1','r2','r3']
    assert proof.result_digest == selection.pack_ref.content_sha256
    assert pack.input_set_ref == manifest.input_set_ref
    assert selection.selected_result.total_return == max(item.aggregate_metrics.total_return for item in pack.baseline_results)
    assert selection.selection_policy_digest == inputs.policy_digest


@pytest.mark.parametrize('fault',['missing_environment','noncanonical_environment','sandbox_policy'])
def test_cli_rejects_environment_before_executor_construction(tmp_path,monkeypatch,fault):
    import json
    from scripts import run_p3_alpha_campaign as command
    from tests.p3.test_publication import _changed
    from tests.p3.test_replica_execution import _seal
    store,manifest_ref = baseline_inputs(tmp_path/'inputs')
    manifest = BaselineManifest.model_validate_json(store.read_bytes(manifest_ref))
    inputs = InputSet.model_validate_json(store.read_bytes(manifest.input_set_ref))
    if fault == 'missing_environment':
        (tmp_path/'inputs'/inputs.environment_ref.locator).unlink()
    elif fault == 'noncanonical_environment':
        raw = json.dumps(json.loads(store.read_bytes(inputs.environment_ref)),indent=2).encode()
        environment_ref = store.put_bytes(raw,media_type='application/json')
        inputs = _changed(inputs,environment_ref=environment_ref.model_dump(mode='json'))
        input_ref = store.put_bytes(canonical_json_bytes(inputs),media_type='application/json')
        manifest = _changed(manifest,input_set_ref=input_ref.model_dump(mode='json'))
        manifest_ref = store.put_bytes(canonical_json_bytes(manifest),media_type='application/json')
    intent_ref = _seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-baselines-v1',
        operation='BASELINES',input_set_ref=manifest.input_set_ref,allowed_alpha_ids=[],body={'baseline_manifest_ref':manifest_ref})
    for name,value in [('manifest',intent_ref),('source',inputs.source),('environment',inputs.environment_ref)]:
        (tmp_path/name).write_bytes(canonical_json_bytes(value))
    def forbidden(**kwargs):
        raise AssertionError('invalid environment reached executor construction')
    monkeypatch.setattr(command,'BubblewrapExecutor',forbidden)
    monkeypatch.setattr(sys,'argv',[str(command.__file__),'--manifest-ref',str(tmp_path/'manifest'),
        '--source',str(tmp_path/'source'),'--environment-ref',str(tmp_path/'environment'),
        '--store',str(tmp_path/'inputs'),'--release',str(Path(__file__).resolve().parents[2]),
        '--python',sys.executable,'--sandbox-policy-digest',('d' if fault == 'sandbox_policy' else 'c')*64,
        '--logical-trial-id','p3-baselines-v1','--output',str(tmp_path/'runs')])
    with pytest.raises((ValueError,OSError)):
        command.main()
    assert not (tmp_path/'runs').exists()
