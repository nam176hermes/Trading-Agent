"""Synthetic released views; no SQL consumption or custody qualification."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.alpha_lifecycle.contracts.execution import HoldoutManifest, InstrumentSpec
from packages.alpha_lifecycle.evaluation import evaluate_holdout
from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_reference_input import reference_seed  # noqa: F401


def test_view_contains_only_calculation_reads_and_supports_the_existing_evaluator(reference_seed,tmp_path):
    from packages.alpha_lifecycle.holdout_view import build_holdout_calculation_view,HoldoutCalculationView
    root,manifest_ref,spec_ref,context,holdout,buffer=reference_seed
    original=LocalArtifactStore(root)
    reads=[]
    def read(ref):
        reads.append(ref)
        return original.read_bytes(ref)
    raw=build_holdout_calculation_view(manifest_ref,spec_ref,SimpleNamespace(read_bytes=read))
    inventory=json.loads(raw)
    assert set(inventory)=={ref.locator for ref in reads}
    assert len(inventory)==681
    assert all(ref.locator in inventory for ref in (*context[-301:],*holdout,buffer))
    assert all(ref.locator not in inventory for ref in context[:-301])
    assert all(json.loads(value).get('schema_version') not in
        {'p3-custody-record-v1','p3-independent-review-v1'} for value in inventory.values())
    assert set(json.loads(inventory[buffer.locator]))=={
        'schema_version','date','open','opened_at','observed_at','source_evidence_ref','instrument','digest'}
    view=HoldoutCalculationView(raw,manifest_ref,spec_ref)
    with pytest.raises(AttributeError):
        view.raw = b'unvalidated replacement'
    with pytest.raises(ValueError,match='immutable'):
        view.put_bytes(b'anything',media_type='application/json')
    output=tmp_path/'output';output.mkdir(mode=0o700)
    store=ReplicaArtifactStore(view,output)
    manifest=HoldoutManifest.model_validate_json(view.read_bytes(manifest_ref))
    spec=InstrumentSpec.model_validate_json(view.read_bytes(spec_ref))
    actual=evaluate_holdout(manifest,spec,store)
    expected_root=tmp_path/'expected';expected_root.mkdir(mode=0o700)
    assert actual==evaluate_holdout(manifest,spec,ReplicaArtifactStore(root,expected_root))
    assert {ref.locator for ref in (*context,*holdout,buffer)}.isdisjoint(path.name for path in output.iterdir())


@pytest.mark.parametrize('fault',['extra','missing','tamper','noncanonical','type','oversize'])
def test_view_rejects_unexpected_or_unverifiable_transport(reference_seed,fault):
    from packages.alpha_lifecycle.holdout_view import build_holdout_calculation_view,HoldoutCalculationView,MAX_VIEW_BYTES
    root,manifest_ref,spec_ref,_,holdout,_=reference_seed
    raw=build_holdout_calculation_view(manifest_ref,spec_ref,LocalArtifactStore(root))
    inventory=json.loads(raw)
    if fault=='extra':
        value=canonical_json_bytes({'unneeded':'custody-secret'}).decode()
        inventory[hashlib.sha256(value.encode()).hexdigest()+'.blob']=value
    elif fault=='missing': del inventory[holdout[0].locator]
    elif fault=='tamper': inventory[holdout[0].locator]+=' '
    elif fault=='type': inventory[holdout[0].locator]=True
    raw=canonical_json_bytes(inventory)
    if fault=='noncanonical': raw+=b'\n'
    if fault=='oversize': raw=b' '*(MAX_VIEW_BYTES+1)
    with pytest.raises(ValueError):
        HoldoutCalculationView(raw,manifest_ref,spec_ref)


def test_holdout_child_never_mounts_the_source_cas(reference_seed,tmp_path,monkeypatch):
    from packages.alpha_lifecycle.holdout_view import build_holdout_calculation_view,HoldoutCalculationView
    root,manifest_ref,spec_ref,*_=reference_seed
    manifest=HoldoutManifest.model_validate_json(LocalArtifactStore(root).read_bytes(manifest_ref))
    retained=tmp_path/'retained';retained.mkdir(mode=0o700)
    store=ReplicaArtifactStore(root,retained)
    view=HoldoutCalculationView(build_holdout_calculation_view(manifest_ref,spec_ref,store),manifest_ref,spec_ref)
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox',lambda path:path)
    executor=BubblewrapExecutor(store=store,store_root=root,release_root=Path('/p3/release'),
        python=Path('/p3/python/bin/python3.11'),source=manifest.source,
        environment_ref=manifest.environment_ref,sandbox_policy_digest='c'*64,instrument_spec_ref=spec_ref,
        holdout_view=view, before_spawn=lambda: None)
    descriptors=[]
    def probe(argv,**kwargs):
        assert not any(argv[i:i+3]==('--ro-bind',str(root),str(root)) for i in range(len(argv)-2))
        assert str(root) not in argv
        descriptors.extend(kwargs['pass_fds'])
        assert len(descriptors)==2
        index=argv.index('--ro-bind-data')
        descriptor=int(argv[index+1])
        assert descriptor in descriptors
        assert argv[index-2:index]==('--perms','600')
        assert os.fstat(descriptor).st_mode & 0o777==0o600
        assert fcntl.fcntl(descriptor,getattr(fcntl, 'F_GET_SEALS', 1034))==15
        raw=os.pread(descriptor,os.fstat(descriptor).st_size,0)
        assert len(json.loads(raw))==681
        assert argv[argv.index(str(executor._python))+5]==argv[index+2]
        raise RuntimeError('curated launch observed')
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.subprocess.run',probe)
    with pytest.raises(RuntimeError,match='curated launch observed'):
        executor.execute(manifest_ref,replicate='R1',logical_trial_id='synthetic',output_dir=tmp_path)
    for descriptor in descriptors:
        with pytest.raises(OSError): os.fstat(descriptor)


def test_holdout_child_reads_only_the_released_private_view(reference_seed, tmp_path):
    from packages.alpha_lifecycle.holdout_view import build_holdout_calculation_view
    from packages.alpha_lifecycle.contracts.results import HoldoutEvaluationResult
    from scripts.run_p3_evaluation_child import main

    root, manifest_ref, spec_ref, *_ = reference_seed
    private = tmp_path / 'released'
    private.mkdir(mode=0o700)
    view = private / 'view.json'
    view.write_bytes(build_holdout_calculation_view(manifest_ref, spec_ref, LocalArtifactStore(root)))
    view.chmod(0o600)
    request = tmp_path / 'manifest.json'
    request.write_bytes(canonical_json_bytes(manifest_ref))
    output = tmp_path / 'result.json'
    main(request, view, output, instrument_spec_ref_json=canonical_json_bytes(spec_ref).decode())
    result = HoldoutEvaluationResult.model_validate_json(output.read_bytes())
    assert result.manifest_ref == manifest_ref
    assert tuple(f.fold_id for f in result.primary_base.fold_results) == ('H1',)
    assert canonical_json_bytes(result) == output.read_bytes()
    _, _, _, context, holdout, buffer = reference_seed
    assert {ref.locator for ref in (*context, *holdout, buffer)}.isdisjoint(
        p.name for p in (tmp_path / 'artifacts').iterdir())


def test_holdout_requires_fresh_fence_before_each_child(reference_seed, tmp_path, monkeypatch):
    from packages.alpha_lifecycle.holdout_view import build_holdout_calculation_view, HoldoutCalculationView
    from packages.alpha_lifecycle.sandbox import SandboxHeld

    root, manifest_ref, spec_ref, *_ = reference_seed
    store = LocalArtifactStore(root)
    manifest = HoldoutManifest.model_validate_json(store.read_bytes(manifest_ref))
    view = HoldoutCalculationView(build_holdout_calculation_view(manifest_ref, spec_ref, store), manifest_ref, spec_ref)
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox', lambda path: path)
    arguments = dict(store=store, store_root=root, release_root=Path('/p3/release'),
        python=Path('/p3/python/bin/python3.11'), source=manifest.source,
        environment_ref=manifest.environment_ref, sandbox_policy_digest='c' * 64,
        instrument_spec_ref=spec_ref, holdout_view=view)
    with pytest.raises(ValueError, match='fence'):
        BubblewrapExecutor(**arguments)
    descriptors = []
    def revoked():
        raise RuntimeError('synthetic current claim revoked')
    executor = BubblewrapExecutor(**arguments, before_spawn=revoked)
    original = executor._argv
    def argv(*args):
        descriptors.extend(args[-2:])
        return original(*args)
    monkeypatch.setattr(executor, '_argv', argv)
    def forbidden(*args, **kwargs):
        pytest.fail('revoked holdout must never spawn')
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.subprocess.run', forbidden)
    for replica in ('R1', 'R2'):
        with pytest.raises(SandboxHeld, match='fence'):
            executor.execute(manifest_ref, replicate=replica, logical_trial_id='synthetic', output_dir=tmp_path)
    assert len(descriptors) == 4
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize('extra', [None, 'holdout', 'unrelated'])
def test_parent_recomputes_holdout_without_plaintext_in_research_cas(reference_seed, tmp_path, monkeypatch, extra):
    """Exercise calculation/readback; the process launcher is synthetic."""
    from packages.alpha_lifecycle.holdout_view import build_holdout_calculation_view, HoldoutCalculationView
    import shutil
    root, manifest_ref, spec_ref, _, holdout, buffer = reference_seed
    root = Path(shutil.copytree(root, tmp_path / 'retained'))
    store = LocalArtifactStore(root)
    view = HoldoutCalculationView(build_holdout_calculation_view(manifest_ref, spec_ref, store), manifest_ref, spec_ref)
    manifest = HoldoutManifest.model_validate_json(view.read_bytes(manifest_ref))
    spec = InstrumentSpec.model_validate_json(view.read_bytes(spec_ref))
    for ref in (*holdout, buffer):
        (root / ref.locator).unlink()  # Synthetic fixture only; custody is the sole raw reader.
    before = set(root.iterdir())
    output = tmp_path / 'replica'; output.mkdir(mode=0o700)
    artifacts = output / 'artifacts'; artifacts.mkdir(mode=0o700)
    expected = evaluate_holdout(manifest, spec, ReplicaArtifactStore(view, artifacts))
    if extra is not None:
        LocalArtifactStore(artifacts).put_bytes(view.read_bytes(holdout[0]) if extra == 'holdout'
            else b'{"unrequested":"output"}', media_type='application/json')
    (output / 'result.json').write_bytes(canonical_json_bytes(expected))
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox', lambda path: path)
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.subprocess.run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    executor = BubblewrapExecutor(store=store, store_root=root, release_root=Path('/p3/release'),
        python=Path('/p3/python/bin/python3.11'), source=manifest.source,
        environment_ref=manifest.environment_ref, sandbox_policy_digest='c' * 64,
        instrument_spec_ref=spec_ref, holdout_view=view, before_spawn=lambda: None)
    if extra is not None:
        from packages.alpha_lifecycle.sandbox import SandboxHeld
        with pytest.raises(SandboxHeld, match='output inventory'):
            executor.execute(manifest_ref, replicate='R1', logical_trial_id='synthetic', output_dir=output)
        assert set(root.iterdir()) == before
        return
    receipt = executor.execute(manifest_ref, replicate='R1', logical_trial_id='synthetic', output_dir=output)
    assert store.read_bytes(receipt.result_ref) == canonical_json_bytes(expected)
    assert before < set(root.iterdir())
    assert all(not (root / ref.locator).exists() for ref in (*holdout, buffer))
