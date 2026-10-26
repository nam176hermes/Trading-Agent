"""Synthetic protected-reader injection tests; no profile or host is provisioned."""
import hashlib
from pathlib import Path

import pytest

from packages.engine_contracts import canonical_json_bytes
from tests.p3.test_spawn_capability import synthetic_provider
from packages.alpha_lifecycle.authority import require_synthetic_operation_context as _real_context


def test_unprotected_profile_cannot_issue_a_closure(tmp_path):
    from services.job_worker.p3_host_profile import read_official_profile
    path=tmp_path/'profile.json';path.write_bytes(b'{}\n');path.chmod(0o444)
    with pytest.raises(ValueError):
        read_official_profile(path,hashlib.sha256(b'{}\n').hexdigest(),
            job_id='job_'+'1'*32,payload=None,admission=True)


@pytest.fixture
def profile_inputs(synthetic_provider,monkeypatch):
    from services.job_worker import p3_host_profile as module
    from services.job_worker import p3_spawn
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    provider,job,closure=synthetic_provider
    from packages.alpha_lifecycle import authority
    # Closure transport fixtures have no dataset/PIT graph. Admission is tested
    # separately below with the real context function restored.
    monkeypatch.setattr(authority, 'require_synthetic_operation_context', lambda *a, **kw: None)
    store=provider._store
    intent=P3OperationInput.model_validate_json(store.read_bytes(job.payload.manifest_ref))
    inputs=InputSet.model_validate_json(store.read_bytes(intent.input_set_ref))
    auth=RunAuthorization.model_validate_json(store.read_bytes(job.payload.authorization_ref))
    inventory=store.put_bytes(canonical_json_bytes(dict(schema_version='p3-host-files-v1',files=[
        dict(source=str(m.source),target=str(m.target),size=m.size,mode=m.mode,sha256=m.sha256)
        for m in closure.mounts])),media_type='application/json')
    document=dict(schema_version='p3-official-host-profile-v1',job_id=job.job_id,
        payload=job.payload.model_dump(mode='json'),environment_ref=closure.environment_ref.model_dump(mode='json'),
        store_root=str(provider._store_root),output_root=str(provider._output_root),python_root=str(closure.python_root),
        worker_credentials_directory='/run/credentials/p3-worker',
        sandbox_executable=str(closure.sandbox.executable),sandbox_sha256=closure.sandbox.executable_sha256,
        sandbox_version=closure.sandbox.version,
        inventory_ref=inventory.model_dump(mode='json'),closure_sha256=closure.closure_sha256,
        integration_receipt_ref=inputs.integration_receipt_ref.model_dump(mode='json'),
        qualification_job_detail_ref=store.put_bytes(b'{}',media_type='application/json').model_dump(mode='json'),
        postgres_binary_sha256='b'*64)
    from tests.p3.github_lane import GithubLane
    # The run context is the controller's protected API attestation, never the environment.
    context=GithubLane(monkeypatch,source=closure.source,run_id=auth.issuer_run_id,
        attempt=auth.issuer_attempt,operation=job.payload.logical_trial_id)
    def digest():
        return hashlib.sha256(canonical_json_bytes(document)+b'\n').hexdigest()
    monkeypatch.setattr(module,'read_protected_canonical_json_current',lambda path:(document,digest()))
    monkeypatch.setattr(module,'_release_files',p3_spawn._release_files)
    monkeypatch.setattr(module,'inspect_python_runtime',p3_spawn.inspect_python_runtime)
    monkeypatch.setattr(module,'canonical_source_identity',lambda root:closure.source.model_dump(mode='json'))
    monkeypatch.setattr(__import__('packages.project_status',fromlist=['derive_project_status']),'derive_project_status',
        lambda root:pytest.fail('GITHUB host profile must not re-derive networked status'))
    observations=[]
    def qualify(ref,**kwargs):
        observations.append((ref,kwargs))
    monkeypatch.setattr(module,'validate_integration_job_result',qualify)
    return module,provider,job,closure,document,context,digest,observations


def test_profile_reconstructs_bound_closure_and_rechecks_receipt(profile_inputs):
    module,provider,job,closure,document,context,digest,observations=profile_inputs
    profile,actual=module.read_official_profile(Path('/synthetic/profile.json'),digest(),
        job_id=job.job_id,payload=job.payload,admission=True)
    assert actual==closure
    assert profile.store_root==str(provider._store_root)
    assert profile.worker_credentials_directory==document['worker_credentials_directory']
    assert len(observations)==1
    assert observations[0][1]['source']==closure.source
    assert observations[0][1]['job_detail_ref'].model_dump(mode='json')==document['qualification_job_detail_ref']


@pytest.mark.parametrize('value',[None,'relative','/run/../credentials'])
def test_profile_requires_explicit_canonical_worker_credentials(profile_inputs,value):
    module,_,job,_,document,context,digest,_=profile_inputs
    if value is None:
        document.pop('worker_credentials_directory')
    else:
        document['worker_credentials_directory']=value
    with pytest.raises(ValueError):
        module.read_official_profile(Path('/synthetic/profile.json'),digest(),
            job_id=job.job_id,payload=job.payload,admission=True)


@pytest.mark.parametrize('fault',['digest','job','payload','source','context','gates','receipt','inventory','file'])
def test_profile_rejects_changed_binding_or_evidence(profile_inputs,monkeypatch,fault):
    module,provider,job,closure,document,context,digest,_=profile_inputs
    expected=digest()
    if fault=='digest':
        expected='0'*64
    elif fault=='job':
        document['job_id']='job_'+'9'*32
    elif fault=='payload':
        document['payload']['authorization_ref']=document['qualification_job_detail_ref']
    elif fault=='source':
        monkeypatch.setattr(module,'canonical_source_identity',lambda root:dict(
            closure.source.model_dump(mode='json'),commit_sha='9'*40))
    elif fault=='context':
        context.attest(job.payload.logical_trial_id,observed_at='2020-01-01T00:00:00Z')
    elif fault=='gates':
        context.revoke_source_ready()  # D-S7-02: GITHUB reads the controller's record, never re-derives
    elif fault=='receipt':
        monkeypatch.setattr(module,'validate_integration_job_result',lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('bad retained proof')))
    elif fault=='inventory':
        document['inventory_ref']=document['qualification_job_detail_ref']
    else:
        mount=closure.mounts[0];mount.source.chmod(0o600);mount.source.write_bytes(b'changed')
    if fault!='digest':
        expected=digest()  # Even a coherently root-pinned document must obey bindings.
    reasons=dict(digest='digest changed',job='another workflow job',payload='another workflow job',
        source='source is not current',context='current workflow',gates='source-ready',
        receipt='bad retained proof',inventory='schema_version|files',file='file identity differs')
    with pytest.raises(ValueError,match=reasons[fault]):
        module.read_official_profile(Path('/synthetic/profile.json'),expected,
            job_id=job.job_id,payload=job.payload,admission=True)


def test_holdout_profile_requires_a_custodian_endpoint(profile_inputs):
    from services.job_worker.p3_host_profile import OfficialHostProfile
    _,_,_,_,document,*_=profile_inputs
    document['payload']['operation']='HOLDOUT'
    document['payload']['logical_trial_id']='p3-holdout-primary-v1'
    with pytest.raises(ValueError,match='custodian'):
        OfficialHostProfile.model_validate_json(canonical_json_bytes(document))
    document['custodian_endpoint']=dict(schema_version='p3-custodian-endpoint-v1',
        socket_path='/run/p3/custodian.sock',custodian_uid=17001,research_uid=17002,
        custodian_identity='custodian',research_identity='research')
    assert OfficialHostProfile.model_validate_json(canonical_json_bytes(document)).custodian_endpoint.research_uid==17002
    document['payload']['operation']='BASELINES'
    document['payload']['logical_trial_id']='p3-baselines-v1'
    with pytest.raises(ValueError):OfficialHostProfile.model_validate_json(canonical_json_bytes(document))


@pytest.mark.parametrize('fault',[None,'mixed_lane','wrong_identity','qualification_identity','revoked',
                                 'unknown_lane'])
def test_local_profile_preserves_runtime_checks_and_rejects_mixed_issuers(profile_inputs,monkeypatch,fault):
    from dataclasses import replace
    from types import SimpleNamespace
    from packages.alpha_lifecycle.contracts.authority import RUN_AUTHORIZATION
    from tests.p3.test_local_qualification import sealed
    module,provider,job,closure,document,_,digest,observations=profile_inputs
    value=__import__('json').loads(provider._store.read_bytes(job.payload.authorization_ref))
    for key in ('issuer_workflow','issuer_run_id','issuer_attempt'):value.pop(key)
    identity=dict(issuer_kind='LOCAL',issuer_identity='operator-a',run_id='00000000-0000-4000-8000-000000000002',
        attempt=1,host_id='a'*64,boot_id='00000000-0000-4000-8000-000000000001')
    auth=RUN_AUTHORIZATION.validate_json(canonical_json_bytes(sealed({**value,
        'schema_version':'p3-local-run-authorization-v1','identity':identity})))
    auth_ref=provider._store.put_bytes(canonical_json_bytes(auth),media_type='application/json')
    job=replace(job,payload=job.payload.model_copy(update={'authorization_ref':auth_ref}))
    document.update(schema_version='p3-local-official-host-profile-v1',identity=identity,payload=job.payload.model_dump(mode='json'))
    if fault=='mixed_lane':document.pop('identity');document['schema_version']='p3-official-host-profile-v1'
    elif fault=='unknown_lane':document['schema_version']='p3-local-official-host-profile-v2'
    elif fault=='wrong_identity':document['identity']={**identity,'attempt':2}
    checked=[]
    def gates(root,expected=None): checked.append(('gates',expected))
    def issuer(authorization,source,**kwargs):
        assert source==closure.source and authorization==auth and kwargs['admission'] is True
        # The real LOCAL issuer authenticates prerequisites before returning.
        gates(kwargs['root'],authorization.identity)
        checked.append(('issuer',authorization.identity))
        if fault=='revoked':raise module.AuthorityHeld('revoked')
    monkeypatch.setattr(module,'require_p3_source_ready',gates)
    monkeypatch.setattr(module,'validate_operation_issuer',issuer)
    monkeypatch.setattr(module,'validate_integration_job_result',lambda *a,**k:SimpleNamespace(
        identity=auth.identity.model_copy(update={'attempt':2}) if fault=='qualification_identity' else auth.identity))
    if fault:
        with pytest.raises(ValueError):module.read_official_profile(Path('/synthetic/profile'),digest(),job_id=job.job_id,payload=job.payload,admission=True)
    else:
        profile,actual=module.read_official_profile(Path('/synthetic/profile'),digest(),job_id=job.job_id,payload=job.payload,admission=True)
        assert actual==replace(closure,bound_payload_fingerprint=__import__('packages.job_contracts',fromlist=['payload_fingerprint']).payload_fingerprint(job.payload)) and profile.identity==auth.identity
        assert checked==[('gates',auth.identity),('issuer',auth.identity)]


def test_stage_transition_dispatches_by_enumeration_not_by_exclusion():
    """An unrecognised profile schema must be rejected, never routed to the GITHUB model.

    The stage-transition loop in p3_official.run_session used to choose LOCAL only on
    an exact string match and fall through to OfficialHostProfile for everything else, so a
    new lane or version reached the wrong model before its Literal rejected it. Both the launcher
    seam and read_official_profile now resolve the lane through the same discriminated
    union, which enumerates the accepted schemas and rejects the rest.
    """
    import inspect
    from services.job_worker import p3_official
    from services.job_worker.p3_host_profile import (
        OFFICIAL_PROFILE, LocalOfficialHostProfile, OfficialHostProfile)
    accepted={'p3-official-host-profile-v1','p3-local-official-host-profile-v1'}
    assert {OfficialHostProfile.model_fields['schema_version'].annotation.__args__[0],
            LocalOfficialHostProfile.model_fields['schema_version'].annotation.__args__[0]}==accepted
    for unknown in ('p3-local-official-host-profile-v2','p3-official-host-profile-v2',
                    'p3-local-runtime-image-v1',''):
        with pytest.raises(ValueError):
            OFFICIAL_PROFILE.validate_json(canonical_json_bytes({'schema_version':unknown}))
    source=inspect.getsource(p3_official.run_session)
    assert 'OFFICIAL_PROFILE.validate_json' in source
    assert 'else OfficialHostProfile' not in source


# ---------------------------------------------------------------------------
# R03: LOCAL v2 transport lane
# ---------------------------------------------------------------------------

_V2_IDENTITY = dict(issuer_kind='LOCAL', issuer_identity='operator-a',
    run_id='00000000-0000-4000-8000-000000000002', attempt=1, host_id='a'*64,
    boot_id='00000000-0000-4000-8000-000000000001')


def _ref(seed, size=32):
    return dict(content_sha256=seed*64, size_bytes=size, media_type='application/json',
                locator=seed*64+'.blob')


def _image_descriptor(**overrides):
    value = dict(schema_version='p3-local-runtime-image-v1', image_sha256='b'*64,
        image_size_bytes=182763520, entry_count=7562, logical_bytes=678815144,
        closure_fingerprint='c'*64, inventory_ref=_ref('3'), toolchain_manifest_sha256='d'*64,
        worker_uid=980, worker_gid=980, release_root='release', python_root='python',
        external_libraries=[dict(target='/lib/x86_64-linux-gnu/libc.so.6', mode=0o444,
            size_bytes=2101224, sha256='e'*64, source_dev=2096, source_ino=17465)])
    value.update(overrides)
    return value


def _campaign_payload():
    # OOS rather than HOLDOUT: a HOLDOUT profile additionally requires a custodian
    # endpoint, which is not what these lane and version tests are about.
    return dict(schema_version='p3-alpha-campaign-payload-v1', operation='OOS',
        manifest_ref=_ref('8'), authorization_ref=_ref('9'),
        expected_source=dict(commit_sha='1'*40, tree_sha='2'*40,
            closure_schema_version='trading-agent-source-closure-v1',
            closure_policy_sha256='3'*64, closure_sha256='4'*64),
        logical_trial_id='p3-oos-a0-v1')


def _host_profile_document(schema_version, **overrides):
    value = dict(schema_version=schema_version, job_id='job_'+'1'*32,
        payload=_campaign_payload(), environment_ref=_ref('1'), store_root='/srv/store', output_root='/srv/out',
        python_root='/srv/py', worker_credentials_directory='/run/creds',
        sandbox_executable='/usr/bin/bwrap', sandbox_sha256='2'*64,
        sandbox_version='bubblewrap 0.9.0', inventory_ref=_ref('3'), closure_sha256='4'*64,
        integration_receipt_ref=_ref('5'), qualification_job_detail_ref=_ref('6'),
        postgres_binary_sha256='7'*64, custodian_endpoint=None)
    value.update(overrides)
    return value


def test_local_v2_resolves_to_its_own_class_and_carries_an_image():
    """The v2 lane is a distinct concrete type, not a v1 profile with extra fields."""
    from services.job_worker.p3_host_profile import (
        OFFICIAL_PROFILE, LocalOfficialHostProfile, LocalOfficialHostProfileV2)
    document = _host_profile_document('p3-local-official-host-profile-v2',
        identity=_V2_IDENTITY, image=_image_descriptor(),
        custodian_endpoint=None)
    profile = OFFICIAL_PROFILE.validate_json(canonical_json_bytes(document))
    assert isinstance(profile, LocalOfficialHostProfileV2)
    assert not isinstance(profile, LocalOfficialHostProfile)
    assert profile.image.entry_count == 7562


@pytest.mark.parametrize('schema_version,extra,reason', [
    ('p3-local-official-host-profile-v1', {'image': _image_descriptor()}, 'v1 cannot carry an image'),
    ('p3-local-official-host-profile-v2', {}, 'v2 cannot omit its image'),
    ('p3-official-host-profile-v1', {'image': _image_descriptor()}, 'GITHUB cannot carry an image'),
    ('p3-official-host-profile-v2', {}, 'GITHUB v2 cannot omit its image'),
    ('p3-local-official-host-profile-v3', {}, 'unknown version'),
])
def test_cross_lane_and_cross_version_profiles_are_rejected(schema_version, extra, reason):
    """Neither lane may accept the other's shape, and no version may accept another's."""
    from services.job_worker.p3_host_profile import OFFICIAL_PROFILE
    document = _host_profile_document(schema_version, identity=_V2_IDENTITY, **extra)
    with pytest.raises(ValueError):
        OFFICIAL_PROFILE.validate_json(canonical_json_bytes(document))


def test_github_v2_carries_an_image_without_a_local_identity_and_uses_the_v2_policy():
    from packages.alpha_lifecycle.sandbox_policy import SANDBOX_PROFILE_V2_SHA256
    from services.job_worker.p3_host_profile import (
        OFFICIAL_PROFILE, LocalOfficialHostProfileLane, OfficialHostProfileV2, _transport_policy)
    document = _host_profile_document('p3-official-host-profile-v2', image=_image_descriptor())
    document.pop('identity', None)
    profile = OFFICIAL_PROFILE.validate_json(canonical_json_bytes(document))
    assert type(profile) is OfficialHostProfileV2
    assert not isinstance(profile, LocalOfficialHostProfileLane)
    assert _transport_policy(profile) == SANDBOX_PROFILE_V2_SHA256
    with pytest.raises(ValueError):
        OFFICIAL_PROFILE.validate_json(canonical_json_bytes(dict(document, identity=_V2_IDENTITY)))


def test_lane_base_answers_for_both_local_versions_and_never_for_github():
    """A v2 profile must answer 'is this LOCAL?' exactly as a v1 profile does."""
    from services.job_worker.p3_host_profile import (
        LocalOfficialHostProfile, LocalOfficialHostProfileLane, LocalOfficialHostProfileV2,
        OfficialHostProfile)
    assert issubclass(LocalOfficialHostProfile, LocalOfficialHostProfileLane)
    assert issubclass(LocalOfficialHostProfileV2, LocalOfficialHostProfileLane)
    assert not issubclass(OfficialHostProfile, LocalOfficialHostProfileLane)


def test_v2_transport_has_its_own_policy_identity_and_v1_is_pinned():
    """A new transport must produce a new policy digest and leave v1's untouched."""
    from packages.alpha_lifecycle.sandbox_policy import (
        SANDBOX_PROFILE_SHA256, SANDBOX_PROFILE_V2_SHA256)
    assert SANDBOX_PROFILE_SHA256 == '29c4c2c274c4ba2e8adade30ed72ecb39200ecc98a68285949660b036a13c8b8'
    assert SANDBOX_PROFILE_V2_SHA256 != SANDBOX_PROFILE_SHA256
    assert len(SANDBOX_PROFILE_V2_SHA256) == 64


@pytest.mark.parametrize('overrides,reason', [
    ({'external_libraries': []}, 'no libraries'),
    ({'external_libraries': [dict(target='/b.so', mode=0o444, size_bytes=1, sha256='e'*64,
        source_dev=1, source_ino=1), dict(target='/a.so', mode=0o444, size_bytes=1,
        sha256='f'*64, source_dev=1, source_ino=2)]}, 'unordered targets'),
    ({'external_libraries': [dict(target='/a.so', mode=0o444, size_bytes=1, sha256='e'*64,
        source_dev=1, source_ino=1), dict(target='/a.so', mode=0o444, size_bytes=1,
        sha256='f'*64, source_dev=1, source_ino=2)]}, 'duplicate targets'),
    ({'release_root': 'python'}, 'release and python roots collide'),
    ({'entry_count': 8193}, 'over the accepted file cap'),
    ({'image_size_bytes': 2147483649}, 'over the image artifact cap'),
])
def test_image_descriptor_enforces_transport_bounds(overrides, reason):
    """Transport caps are refused at the descriptor, before any host work begins."""
    from services.job_worker.p3_host_profile import P3RuntimeImageDescriptor
    with pytest.raises(ValueError):
        P3RuntimeImageDescriptor.model_validate_json(
            canonical_json_bytes(_image_descriptor(**overrides)))


def test_image_descriptor_excludes_run_scoped_mount_identity():
    """Image identity is durable; loop, mount and owner identity belong to one run."""
    from services.job_worker.p3_host_profile import P3RuntimeImageDescriptor
    fields = set(P3RuntimeImageDescriptor.model_fields)
    assert not fields & {'backing_dev', 'backing_ino', 'loop_device', 'mount_nonce',
                         'controller_pid', 'mount_generation', 'boot_id'}


@pytest.mark.parametrize('typed', [False, True])
def test_profile_real_staging_rejects_rehearsal_receipt_with_market_input(profile_inputs, monkeypatch, typed):
    from packages.alpha_lifecycle import authority
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.github_run import parse_github_research_inputs
    from tests.p3.github_lane import _sealed
    module, provider, job, _, _, context, digest, _ = profile_inputs
    monkeypatch.setattr(authority, 'require_synthetic_operation_context', _real_context)
    intent = P3OperationInput.model_validate_json(provider._store.read_bytes(job.payload.manifest_ref))
    context.admit_inputs(intent.input_set_ref)
    path = Path('/etc/trading-agent-p3/github/research-inputs.json')
    if typed:
        receipt = dict(context.documents[path])
        receipt.update(schema_version='p3-github-synthetic-inputs-v1',
            generation_ref=receipt['environment_ref'], commitment_ref=receipt['integration_receipt_ref'],
            provenance='LOCAL_DETERMINISTIC_GENERATOR_NO_WORKFLOW_PRODUCER',
            identity=context.documents[context.directory/'github-run.json']['identity'])
        binding = parse_github_research_inputs(_sealed(receipt)).binding_digest
        review_path = path.with_name('research-inputs-review.json')
        review = _sealed({**context.documents[review_path],
            'subject_digests': [intent.input_set_ref.content_sha256, binding]})
        receipt['review_ref'] = provider._store.put_bytes(canonical_json_bytes(review),
            media_type='application/json').model_dump(mode='json')
        context.documents[path] = _sealed(receipt)
        context.documents[review_path] = review
        context.research_inputs_sha256 = hashlib.sha256(canonical_json_bytes(context.documents[path])+b'\n').hexdigest()
        context.attest(job.payload.logical_trial_id)
    with pytest.raises(authority.AuthorityHeld, match='REVIEW') as caught:
        module.read_official_profile(Path('/synthetic/profile.json'), digest(),
            job_id=job.job_id, payload=job.payload, admission=True)
    assert 'genuine rehearsal requires a synthetic InputSet' in str(caught.value.__cause__)


def test_profile_synthetic_context_is_rechecked_after_successful_staging(profile_inputs, monkeypatch):
    from packages.alpha_lifecycle import authority, synthetic_data
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from packages.alpha_lifecycle.github_run import parse_github_research_inputs
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.replica_store import _read
    from tests.p3.test_baseline_operation import _cli_authorization
    from tests.p3.test_reference_input import _changed
    from tests.p3.test_replica_execution import _seal
    from tests.p3.github_lane import _sealed
    module, provider, job, closure, document, context, digest, observations = profile_inputs
    monkeypatch.setattr(authority, 'require_synthetic_operation_context', _real_context)
    store = provider._store
    intent = _read(store, job.payload.manifest_ref, P3OperationInput)
    inputs = _read(store, intent.input_set_ref, InputSet)
    generation = store.put_bytes(b'{"unit":"generation"}', media_type='application/json')
    commitment = store.put_bytes(b'{"unit":"commitment"}', media_type='application/json')
    pit = _seal(store, schema_version='p3-p-i-t-proof-v1', dataset_ref=inputs.dataset_evidence_ref,
        fold_manifest_ref=inputs.fold_manifest_ref, vintage_class='SYNTHETIC_REHEARSAL',
        historical_vintage_verified=False, revision_proof_ref=commitment,
        no_future_suite_ref=generation, limitations=['synthetic.rehearsal_only'])
    input_ref = store.put_bytes(canonical_json_bytes(_changed(inputs,
        synthetic_generation_ref=generation, pit_proof_ref=pit)), media_type='application/json')
    intent_ref = store.put_bytes(canonical_json_bytes(_changed(intent, input_set_ref=input_ref)), media_type='application/json')
    intent = _read(store, intent_ref, P3OperationInput)
    auth = _read(store, _cli_authorization(store, intent_ref, closure.source), RunAuthorization)
    payload = authority.build_alpha_campaign_payload(auth, closure.source, intent.workflow_operation, operation_input=intent)
    document['payload'] = payload.model_dump(mode='json')
    context.admit_inputs(input_ref)
    path = Path('/etc/trading-agent-p3/github/research-inputs.json')
    receipt = {**context.documents[path], 'schema_version':'p3-github-synthetic-inputs-v1',
        'generation_ref':generation, 'commitment_ref':commitment,
        'policy_digest':inputs.policy_digest, 'environment_ref':inputs.environment_ref,
        'integration_receipt_ref':inputs.integration_receipt_ref,
        'provenance':'LOCAL_DETERMINISTIC_GENERATOR_NO_WORKFLOW_PRODUCER',
        'identity':context.documents[context.directory/'github-run.json']['identity']}
    binding = parse_github_research_inputs(_sealed(receipt)).binding_digest
    review_path = path.with_name('research-inputs-review.json')
    review = _sealed({**context.documents[review_path], 'subject_digests':[input_ref.content_sha256, binding]})
    receipt['review_ref'] = store.put_bytes(canonical_json_bytes(review), media_type='application/json')
    context.documents[path], context.documents[review_path] = _sealed(receipt), review
    context.research_inputs_sha256 = hashlib.sha256(canonical_json_bytes(context.documents[path])+b'\n').hexdigest()
    context.attest(intent.workflow_operation)
    checks = []
    # Full generation validation is exercised in test_synthetic_provenance. This
    # test exercises the real protected files -> host profile -> stage fence.
    monkeypatch.setattr(synthetic_data, 'read_synthetic_commitment_metadata', lambda *a, **kw: checks.append(kw))
    module.read_official_profile(Path('/synthetic/profile.json'), digest(), job_id=job.job_id, payload=payload, admission=True)
    assert len(checks) == len(observations) == 1
    context.documents.pop(path)
    with pytest.raises(authority.AuthorityHeld, match='REVIEW'):
        module.read_official_profile(Path('/synthetic/profile.json'), digest(), job_id=job.job_id, payload=payload, admission=False)
    assert len(checks) == len(observations) == 1
