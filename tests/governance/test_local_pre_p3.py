"""Local prerequisite proofs preserve exact executed inventory and issuer."""
import importlib.util
import json

import pytest

from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts import canonical_json_bytes
from tests.p3.test_pit_suite import suite_inputs
from tests.p3.test_local_qualification import local  # noqa: F401


@pytest.mark.parametrize('fault', [None, 'skip', 'missing', 'extra', 'duplicate', 'setup', 'failed_exit', 'issuer'])
def test_local_suite_readback_requires_complete_calls_and_exact_identity(tmp_path, fault):
    assert importlib.util.find_spec('packages.local_pre_p3'), 'local prerequisite readback is missing'
    from packages.local_pre_p3 import build_local_test_evidence, validate_local_test_evidence
    _, _, source, _, _ = suite_inputs(tmp_path)
    root = tmp_path/'proofs'; root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(dict(
        issuer_kind='LOCAL', issuer_identity='operator-a',
        run_id='00000000-0000-4000-8000-000000000002', attempt=1,
        host_id='a'*64, boot_id='00000000-0000-4000-8000-000000000001')))
    nodes = ['tests/alpha_lifecycle/test_alpha_registry.py::test_example']
    collection = dict(schema_version=1, component='root', collection_only=True,
        pytest_exit_status=0, summary={'collected':1}, tests=[dict(test_node_id=n,
        component='root', outcome='collected', reason='', phase='collection') for n in nodes])
    report = {**collection, 'collection_only':False, 'summary':{'passed':1},
        'tests':[{**row,'outcome':'passed','phase':'call'} for row in collection['tests']]}
    if fault == 'skip': report['tests'][0]['outcome']='skipped'
    elif fault == 'missing': report['tests']=[]
    elif fault == 'extra': report['tests'][0]['test_node_id']='tests/other.py::test_other'
    elif fault == 'duplicate': report['tests']*=2
    elif fault == 'setup': report['tests'][0]['phase']='setup'
    elif fault == 'failed_exit': report['pytest_exit_status']=1
    collection_ref = store.put_bytes(canonical_json_bytes(collection),media_type='application/json')
    report_ref = store.put_bytes((json.dumps(report,indent=2,sort_keys=True)+'\n').encode(),media_type='application/json')
    def check():
        evidence=build_local_test_evidence(gate='ALPHA_REGISTRY_FOUNDATION', source=source,
            identity=identity, collection_ref=collection_ref, report_ref=report_ref,
            started_at='2026-09-18T11:00:00Z',completed_at='2026-09-18T12:00:00Z',store=store)
        ref=store.put_bytes(canonical_json_bytes(evidence),media_type='application/json')
        expected=identity.model_copy(update={'attempt':2}) if fault=='issuer' else identity
        return validate_local_test_evidence(ref,gate='ALPHA_REGISTRY_FOUNDATION',
            source=source,identity=expected,store=store)
    if fault:
        with pytest.raises(ValueError): check()
    else:
        assert check().executed_node_ids == tuple(nodes)


def test_local_producer_observes_real_alpha_suite_without_github(tmp_path, monkeypatch):
    from scripts import qualify_pre_p3 as producer
    assert hasattr(producer, 'execute_local_suite'), 'local suite producer is missing'
    from packages.local_pre_p3 import validate_local_test_evidence
    store, _, source, _, _ = suite_inputs(tmp_path)
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(dict(
        issuer_kind='LOCAL', issuer_identity='operator-a',
        run_id='00000000-0000-4000-8000-000000000002', attempt=1,
        host_id='a'*64, boot_id='00000000-0000-4000-8000-000000000001')))
    monkeypatch.setattr(producer, '_source_v2', lambda: source.model_dump(mode='json'))
    for name in tuple(__import__('os').environ):
        if name.startswith('GITHUB_'): monkeypatch.delenv(name)
    proof = producer.execute_local_suite('ALPHA_REGISTRY_FOUNDATION', identity, store)
    assert len(proof.executed_node_ids) > 1
    ref=store.put_bytes(canonical_json_bytes(proof),media_type='application/json')
    assert validate_local_test_evidence(ref,gate=proof.gate,source=source,identity=identity,store=store)==proof


@pytest.mark.parametrize('fault', [None,'missing','duplicate','source','issuer','policy','p2_chain','p1_chain','pre_p3_chain'])
def test_local_prerequisite_chain_never_promotes_partial_or_mixed_evidence(tmp_path, fault):
    from packages import local_pre_p3 as module
    assert hasattr(module,'validate_local_prerequisite_chain'), 'local prerequisite chain is missing'
    from packages.alpha_lifecycle.contracts.authority import QualificationReceipt
    from tests.p3.test_local_qualification import sealed
    store, _, source, _, _ = suite_inputs(tmp_path)
    ref=store.put_bytes(b'{}',media_type='application/json').model_dump(mode='json')
    identity=dict(issuer_kind='LOCAL',issuer_identity='operator-a',
        run_id='00000000-0000-4000-8000-000000000002',attempt=1,
        host_id='a'*64,boot_id='00000000-0000-4000-8000-000000000001')
    receipts=[]
    refs={}
    for gate in module.LOCAL_PRE_P3_GATES:
        dependencies = ('P2_SOURCE_COMPLETE','P2_RUNTIME_QUALIFIED') if gate=='P2_QUALIFIED' else (
            tuple(refs) if gate=='PRE_P3_READY' else ())
        evidence=[refs[dependency] for dependency in dependencies] or [ref,{
            **ref,'content_sha256':'1'*64,'locator':'1'*64+'.blob'}]
        value=sealed(dict(schema_version='local-phase-qualification-receipt-v1',identity=identity,
            binding=dict(gate=gate,source=source.model_dump(mode='json'),policy_digest='b'*64,
                input_ref=ref,environment_ref=ref),started_at='2026-09-18T11:00:00Z',
            completed_at='2026-09-18T12:00:00Z',expires_at='2026-09-18T13:00:00Z',
            evidence_refs=evidence,status='PASS',authority=dict(broker=False,live=False,network=False,production=False)))
        proof=QualificationReceipt.model_validate_json(canonical_json_bytes(value))
        receipts.append(proof)
        refs[gate]=store.put_bytes(canonical_json_bytes(proof),media_type='application/json').model_dump(mode='json')
    if fault in ('source','issuer','policy'):
        value=receipts[0].model_dump(mode='json')
        if fault=='source': value['binding']['source']['commit_sha']='9'*40
        elif fault=='policy': value['binding']['policy_digest']='9'*64
        else: value['identity']['attempt']=2
        receipts[0]=QualificationReceipt.model_validate_json(canonical_json_bytes(sealed(value)))
    elif fault=='missing': receipts.pop(0)
    elif fault=='duplicate': receipts.append(receipts[0])
    elif fault in ('p2_chain','p1_chain','pre_p3_chain'):
        gate={'p2_chain':'P2_QUALIFIED','p1_chain':'P1_LTS_READY','pre_p3_chain':'PRE_P3_READY'}[fault]
        index=next(i for i,r in enumerate(receipts) if r.binding.gate==gate)
        value=receipts[index].model_dump(mode='json'); value['evidence_refs']=[ref]
        receipts[index]=QualificationReceipt.model_validate_json(canonical_json_bytes(sealed(value)))
    if fault:
        with pytest.raises(ValueError): module.validate_local_prerequisite_chain(receipts)
    else:
        assert set(module.validate_local_prerequisite_chain(receipts))==set(module.LOCAL_PRE_P3_GATES)


def test_local_readiness_rejects_dirty_source_before_reading_authority(tmp_path, monkeypatch):
    from packages import local_pre_p3 as module
    assert hasattr(module, 'read_local_pre_p3'), 'protected local prerequisite admission is missing'
    from tests.hwc.test_hwc_receipt import repository
    root=repository(tmp_path)
    (root/'source.py').write_text('CHANGED = True\n')
    def forbidden(*args):
        pytest.fail('dirty source must fail before authority is read')
    monkeypatch.setattr(module, 'read_protected_canonical_json_current', forbidden)
    with pytest.raises(ValueError, match='clean'):
        module.read_local_pre_p3(root)


def test_local_gate_producer_runs_real_p2_and_retains_readable_proof(tmp_path, monkeypatch):
    from scripts import qualify_pre_p3 as producer
    from packages import local_pre_p3 as reader
    from packages.alpha_lifecycle import local_qualification as custody
    from packages.alpha_lifecycle.contracts.authority import QualificationBinding
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
    from packages.alpha_lifecycle.sandbox_policy import SANDBOX_PROFILE_SHA256
    from tests.p3.test_local_qualification import sealed
    import hashlib
    assert hasattr(producer, 'produce_local_gate'), 'local semantic qualification producer is missing'
    store, _, source, _, _ = suite_inputs(tmp_path)
    host, boot = custody._host_identity()
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(dict(
        issuer_kind='LOCAL', issuer_identity='operator-a',run_id='00000000-0000-4000-8000-000000000002',
        attempt=1, host_id=host, boot_id=str(boot))))
    environment = sealed(dict(schema_version='p3-environment-identity-v1',python_version='3.11',
        root_lock_digest=hashlib.sha256((producer.ROOT/'uv.lock').read_bytes()).hexdigest(),
        native_manifest_digest=P1_REAL_BACKTEST_POLICY.closure_sha256,
        sandbox_policy_digest=SANDBOX_PROFILE_SHA256,platform='linux-x86_64',decimal_precision=50))
    binding=QualificationBinding(gate='P2_SOURCE_COMPLETE',source=source,
        policy_digest=hashlib.sha256((producer.ROOT/reader.POLICY_PATH).read_bytes()).hexdigest(),
        input_ref=store.put_bytes(b'{}',media_type='application/json'),
        environment_ref=store.put_bytes(canonical_json_bytes(environment),media_type='application/json'))
    monkeypatch.setattr(producer, '_source_v2', lambda:source.model_dump(mode='json'))
    for name in tuple(__import__('os').environ):
        if name.startswith('GITHUB_'): monkeypatch.delenv(name)
    receipt=producer.produce_local_gate(identity,binding,store)
    assert receipt.binding==binding and receipt.identity==identity
    assert len(receipt.evidence_refs)==3
    reader._validate_local_gate_proof(receipt,root=producer.ROOT,store=store)
    with pytest.raises(ValueError,match='host'):
        producer.produce_local_gate(identity.model_copy(update={'host_id':'0'*64}),binding,store)
    with pytest.raises(ValueError,match='policy'):
        producer.produce_local_gate(identity,binding.model_copy(update={'policy_digest':'0'*64}),store)


@pytest.mark.parametrize('fault',[None,'profile_swap','source_drift','revoked_child','environment',
                                 'expired_child','replaced_child','bypassing_writer',
                                 'absent_attestation'])
def test_local_admission_rechecks_protected_dag_after_semantic_readback(local, monkeypatch, fault):
    """Exercise the custody/DAG composition; each gate's semantics has owner tests."""
    import hashlib
    from pathlib import Path
    from packages import local_pre_p3 as reader, pre_p3_provenance as provenance
    from packages.runtime_release import config
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
    from packages.alpha_lifecycle.sandbox_policy import SANDBOX_PROFILE_SHA256
    from tests.p3.test_local_qualification import sealed
    module,store,store_root,identity,binding,_,_,_,_=local
    root=Path(__file__).resolve().parents[2]
    binding['policy_digest']=hashlib.sha256((root/reader.POLICY_PATH).read_bytes()).hexdigest()
    environment=sealed(dict(schema_version='p3-environment-identity-v1',python_version='3.11',
        root_lock_digest=hashlib.sha256((root/'uv.lock').read_bytes()).hexdigest(),
        native_manifest_digest=P1_REAL_BACKTEST_POLICY.closure_sha256,
        sandbox_policy_digest=SANDBOX_PROFILE_SHA256,platform='linux-x86_64',decimal_precision=50))
    if fault=='environment': environment=sealed({**environment,'root_lock_digest':'0'*64})
    def retain(value):
        return store.put_bytes(canonical_json_bytes(value),media_type='application/json').model_dump(mode='json')
    binding['environment_ref']=retain(environment)
    refs={}; bindings=[]; approvals={}; receipts={}; alternate=[]
    for gate in reader.LOCAL_PRE_P3_GATES:
        current={**binding,'gate':gate}; bindings.append(current)
        children=('P2_SOURCE_COMPLETE','P2_RUNTIME_QUALIFIED') if gate=='P2_QUALIFIED' else (
            tuple(refs) if gate=='PRE_P3_READY' else ())
        receipt=sealed(dict(schema_version='local-phase-qualification-receipt-v1',identity=identity,binding=current,
            started_at='2026-09-18T11:00:00Z',completed_at='2026-09-18T11:30:00Z',expires_at='2026-09-18T13:00:00Z',
            evidence_refs=[refs[c] for c in children] or [retain({'test':1}),retain({'native':1})],
            status='PASS',authority=dict(broker=False,live=False,network=False,production=False)))
        receipts[gate]=receipt; refs[gate]=retain(receipt)
        if fault=='replaced_child' and gate=='P1_H_COMPLETE':
            # A second authentic receipt for the same gate and binding: unrevoked, unexpired
            # and structurally valid, so only comparing it against the already admitted
            # receipt can detect the substitution.
            alternate.append(retain(sealed({**receipt,'started_at':'2026-09-18T11:01:00Z'})))
        # An expired prerequisite is authentic, unrevoked and still reports a zero exit in any
        # producer sentinel; only reading the protected approval itself can reject it.
        expires='2026-09-18T11:50:00Z' if fault=='expired_child' and gate=='P1_H_COMPLETE' else '2026-09-18T13:00:00Z'
        approvals[gate]=sealed(dict(schema_version='local-qualification-approval-v1',issuer_identity='operator-a',
            receipt_ref=refs[gate],issued_at='2026-09-18T11:45:00Z',expires_at=expires,
            revoked=False,authority=receipt['authority']))
    profile=dict(schema_version='local-pre-p3-profile-v1',identity=identity,store_root=str(store_root),bindings=bindings)
    checked=[]
    # The store now carries the authority protocol's own records, and admission refuses a
    # store whose approval bytes moved without its generation moving with them.
    from packages.local_authority import PROTOCOL_VERSION
    generation=7
    def approval_bytes(gate):
        return canonical_json_bytes(approvals[gate])+b'\n'
    def attestation():
        members=[[gate,hashlib.sha256(approval_bytes(gate)).hexdigest()]
                 for gate in sorted(reader.LOCAL_PRE_P3_GATES)]
        return dict(schema_version=PROTOCOL_VERSION,issuer='operator-a',generation=generation,
            store_digest=hashlib.sha256(canonical_json_bytes(
                dict(issuer='operator-a',approvals=members))).hexdigest())
    def protected(path,**kwargs):
        if path.parent==store_root and path.suffix=='.blob':
            return path.read_bytes(),(1,2)
        if path.name=='generation.json':
            return canonical_json_bytes(dict(schema_version=PROTOCOL_VERSION,
                generation=generation,issuer='operator-a'))+b'\n',(1,2)
        if path.name=='store-attestation.json':
            if fault=='absent_attestation':
                raise FileNotFoundError(str(path))
            value=attestation()
            if fault=='bypassing_writer':
                # An approval installed without the protocol leaves the attestation
                # describing bytes that are no longer there.
                value=dict(value,store_digest='0'*64)
            return canonical_json_bytes(value)+b'\n',(1,2)
        if path==reader.LOCAL_PRE_P3_PROFILE:
            value={**profile,'store_root':str(store_root.parent)} if checked and fault=='profile_swap' else profile
        else:
            assert path.parent==Path('/etc/trading-agent/local-qualification/operator-a')
            value=approvals[path.stem]
            if len(checked)==10 and path.stem=='P1_H_COMPLETE':
                if fault=='revoked_child':
                    value=sealed({**value,'revoked':True})
                elif fault=='replaced_child':
                    value=sealed({**value,'receipt_ref':alternate[0]})
        return canonical_json_bytes(value)+b'\n',(1,2)
    monkeypatch.setattr(config,'_read_protected_file',protected)
    monkeypatch.setattr(provenance,'canonical_source_identity',lambda *_:binding['source'])
    monkeypatch.setattr(provenance,'_git',lambda *_:' M source.py' if checked and fault=='source_drift' else '')
    monkeypatch.setattr(reader,'_validate_local_gate_proof',lambda receipt,**_:checked.append(receipt.binding.gate))
    expected={'bypassing_writer':'changed outside the authority protocol',
              'absent_attestation':'not under the authority protocol',
              'expired_child':'local qualification approval is not current',
              'replaced_child':'local prerequisite approval changed during admission',
              'revoked_child':'local qualification approval is revoked'}.get(fault)
    if fault:
        with pytest.raises(ValueError) as raised: reader.read_local_pre_p3(root)
        if expected: assert expected in str(raised.value)
    else:
        gates=reader.read_local_pre_p3(root)
        assert tuple(gates)==reader.LOCAL_PRE_P3_GATES
        assert tuple(checked)==reader.LOCAL_PRE_P3_GATES
        assert all(gate.identity.issuer_kind=='LOCAL' for gate in gates.values())


def test_local_cli_keeps_evidence_outside_source_and_never_self_approves(tmp_path,monkeypatch):
    from scripts import qualify_pre_p3 as producer
    assert producer.main(['local-readback','--output',str(producer.ROOT/'unsafe-local.json')])==2
    output=tmp_path/'readback.json'
    assert producer.main(['local-readback','--output',str(output)])==2
    assert not output.exists()


def test_local_plan_cannot_bind_dirty_source_or_issue_an_approval(tmp_path,monkeypatch):
    from scripts import qualify_pre_p3 as module
    from packages.local_pre_p3 import LOCAL_PRE_P3_PROFILE
    original=LOCAL_PRE_P3_PROFILE.exists()
    def dirty():raise module.QualificationError('qualification requires a clean source tree')
    monkeypatch.setattr(module,'_source_v2',dirty)
    output=tmp_path/'proposal.json'
    assert module.main(['local-plan','--issuer','synthetic','--store-root',str(tmp_path),
        '--input-ref',str(tmp_path/'input.json'),'--environment-ref',str(tmp_path/'environment.json'),'--output',str(output)])!=0
    assert not output.exists() and LOCAL_PRE_P3_PROFILE.exists()==original


def test_phase_qualification_proposal_preserves_held_economics(local,tmp_path,monkeypatch):
    from datetime import datetime,UTC,timedelta
    from types import SimpleNamespace
    import hashlib
    from scripts import qualify_pre_p3 as producer
    from packages import local_pre_p3
    from packages.alpha_lifecycle.contracts.authority import QualificationBinding,QualificationReceipt
    from packages.p3_provenance import PhaseExitReceiptV2
    from packages.p3_status import LocalPhaseProfile
    from services.job_worker import p3_phase_readback
    from tests.p3.test_local_qualification import sealed
    from tests.governance.test_local_phase_binding import phase_inputs
    source_root=tmp_path/'source';source_root.mkdir()
    run,ready,expected,store,store_root=phase_inputs(source_root)
    monkeypatch.setattr(producer,'ROOT',source_root)
    phase=PhaseExitReceiptV2.model_validate_json(canonical_json_bytes(sealed(dict(
        schema_version='p3-phase-exit-v2',source=expected.source,identity=run,policy_digest=expected.policy_digest,
        input_ref=expected.input_ref,environment_ref=expected.environment_ref,job_id='job_'+'a'*32,
        attempt_id='attempt_'+'b'*32,authorization_ref=expected.input_ref,terminal_observation_ref=expected.input_ref,
        publication_ref=None,closure_report_ref=None,result_ref=None,registry_head_ref=None,outcome='HELD',
        authority=dict(broker=False,live=False,network=False,production=False)))))
    ref=store.put_bytes(canonical_json_bytes(phase),media_type='application/json')
    profile=LocalPhaseProfile(schema_version='p3-local-phase-profile-v1',identity=run,binding=expected,
        phase_receipt_ref=ref,store_root=str(store_root),worker_credentials_directory='/unused/credentials')
    profile_path=tmp_path/'phase-profile.json';profile_path.write_bytes(canonical_json_bytes(profile))
    ready.expires_at=datetime.now(UTC)+timedelta(hours=1)
    monkeypatch.setattr(local_pre_p3,'read_local_pre_p3',lambda root:{'PRE_P3_READY':ready})
    checked=[]
    def readback(actual,*,repository,store):
        assert actual==ref and repository is None
        checked.append(actual)
        return phase
    monkeypatch.setattr(p3_phase_readback,'validate_phase_exit_receipt',readback)
    output=tmp_path/'phase-proposal.json'
    assert producer.main(['local-propose-phase','--profile',str(profile_path),'--output',str(output)])==0
    proposed=QualificationReceipt.model_validate_json(output.read_bytes())
    assert proposed.evidence_refs==(ref,) and proposed.identity==run and checked==[ref]
    assert PhaseExitReceiptV2.model_validate_json(store.read_bytes(ref)).outcome=='HELD'
