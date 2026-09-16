"""Private native request/result seam over synthetic released views."""
import hashlib

import pytest

from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView,build_holdout_calculation_view
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_reference_input import reference_seed  # noqa: F401


@pytest.fixture
def native_inputs(reference_seed):
    root,manifest,spec,*_=reference_seed
    store=LocalArtifactStore(root)
    view=HoldoutCalculationView(build_holdout_calculation_view(manifest,spec,store),manifest,spec)
    return store,view,manifest,spec


@pytest.mark.parametrize('role',['PRIMARY','SELECTED_BASELINE'])
def test_native_request_is_derived_from_exact_view_and_role(native_inputs,role):
    from packages.alpha_lifecycle.native_request import prepare_native_request,validate_native_request
    store,view,manifest,spec=native_inputs
    before=set(store._root.iterdir())
    request=prepare_native_request(manifest,spec,view,role=role)
    assert request.role==role and len(request.steps)==366
    assert request.steps[0].source_day.isoformat()=='2025-08-31'
    assert request.steps[-1].source_day.isoformat()=='2026-08-31'
    assert request.steps[-1].target==0
    assert validate_native_request(canonical_json_bytes(request),manifest,spec,view,role=role)==request
    assert set(store._root.iterdir())==before  # No plaintext-derived request persisted to CAS.
    for field,value in [('role','SELECTED_BASELINE' if role=='PRIMARY' else 'PRIMARY'),
        ('source',{**request.source.model_dump(mode='json'),'commit_sha':'9'*40}),
        ('environment_ref',spec)]:
        changed=request.model_dump(mode='json',exclude={'digest'});changed[field]=value
        changed['digest']=hashlib.sha256(canonical_json_bytes(changed)).hexdigest()
        with pytest.raises(ValueError):validate_native_request(canonical_json_bytes(changed),manifest,spec,view,role=role)
    with pytest.raises(ValueError):validate_native_request(canonical_json_bytes(request)+b'\n',manifest,spec,view,role=role)
    with pytest.raises(ValueError):prepare_native_request(manifest,spec,store,role=role)


@pytest.mark.host_coupled
@pytest.mark.skipif(not __import__('os').environ.get('P3_NATIVE_TEST_PYTHON'),reason='pinned offline native Python required')
@pytest.mark.parametrize('role',['PRIMARY','SELECTED_BASELINE'])
def test_full_native_request_runs_both_roles_three_times(native_inputs,role,tmp_path):
    import json,os,subprocess
    from pathlib import Path
    from decimal import Decimal
    from packages.alpha_lifecycle.native_request import prepare_native_request,native_step_bytes,native_result_from_output
    from packages.alpha_lifecycle.executable_reference import run_executable_reference,run_selected_baseline_reference
    from packages.alpha_lifecycle.contracts.execution import HoldoutManifest,InstrumentSpec
    from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore,_read
    source,view,manifest_ref,spec_ref=native_inputs
    output=tmp_path/'results';output.mkdir(mode=0o700)
    store=ReplicaArtifactStore(source._root,output)
    manifest=_read(view,manifest_ref,HoldoutManifest);spec=_read(view,spec_ref,InstrumentSpec)
    reference=(run_executable_reference if role=='PRIMARY' else run_selected_baseline_reference)(manifest,spec,store)
    expected=json.loads(store.read_bytes(reference.fill_trace_ref))
    request=prepare_native_request(manifest_ref,spec_ref,view,role=role)
    script=Path(__file__).resolve().parents[2]/'engines/nautilus/runtime_v1/p3_next_open.py'
    observed=[]
    for _ in range(3):
        process=subprocess.run([os.environ['P3_NATIVE_TEST_PYTHON'],'-I','-B',str(script)],
            input=native_step_bytes(request),capture_output=True,timeout=30,check=True,env={})
        native=native_result_from_output(request,process.stdout,view,store)
        observed.append(native)
        assert native.transition_trace_ref==reference.transition_trace_ref
        assert native.manifest_ref==reference.manifest_ref
        actual=json.loads(store.read_bytes(native.fill_trace_ref))
        assert len(actual)==len(expected)
        for left,right in zip(actual,expected,strict=True):
            for key in ('sequence','event_time_ns','init_time_ns','kind','side','price','quantity',
                'position_after','source_day','source_artifact_ref'):
                assert left[key]==right[key],(key,left,right)
            assert abs(Decimal(left['fee_quote'] or '0')-Decimal(right['fee_quote'] or '0'))<=Decimal('.01')
            assert abs(Decimal(left['cash_after'])-Decimal(right['cash_after']))<=Decimal('.02')
        assert abs(Decimal(native.ending_cash)-Decimal(reference.ending_cash))<=Decimal('.02')
        assert native.ending_position=='0'
    assert observed[0]==observed[1]==observed[2]
    with pytest.raises(ValueError):native_result_from_output(request,process.stdout+b'\n',view,store)
    with pytest.raises(ValueError):native_result_from_output(request,b'[]\n',view,store)
