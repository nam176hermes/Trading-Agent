from pathlib import Path
import hashlib
import json

import pytest

from packages.alpha_lifecycle.authority import (
    AuthorityHeld,
    build_alpha_campaign_payload,
    validate_request,
    validate_workflow_operation,
)
from packages.alpha_lifecycle.contracts.authority import RunAuthorization
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.engine_contracts.serialization import canonical_json_bytes


def test_workflow_operation_is_a_closed_enum() -> None:
    assert validate_workflow_operation("p3-baselines-v1") == "BASELINES"
    assert validate_workflow_operation("p3-oos-a3-v1") == "OOS"
    with pytest.raises(AuthorityHeld):
        validate_workflow_operation("p3-oos-a4-v1")


def test_authority_request_rejects_group_readable_file(tmp_path) -> None:
    path = tmp_path/"request.json"; path.write_text("{}")
    path.chmod(0o640)
    with pytest.raises(AuthorityHeld,match="0600"):
        validate_request(path,expected_source=None,operation="BASELINES")


def _request(expires_at: str) -> tuple[dict[str, object], SourceIdentity]:
    source = SourceIdentity(
        commit_sha="a" * 40,
        tree_sha="b" * 40,
        closure_schema_version="pre-p3-source-closure-v1",
        closure_policy_sha256="c" * 64,
        closure_sha256="d" * 64,
    )
    ref = {
        "content_sha256": "e" * 64,
        "size_bytes": 1,
        "media_type": "application/json",
        "locator": f"{'e' * 64}.blob",
    }
    authorization = {
        "schema_version": "p3-run-authorization-v1",
        "input_set_ref": ref,
        "review_ref": ref,
        "operation": "BASELINES",
        "allowed_alpha_ids": [],
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": expires_at,
        "nonce": "00000000-0000-4000-8000-000000000001",
        "issuer_workflow": "p3-authority.yml",
        "issuer_run_id": 1,
        "issuer_attempt": 1,
        "authority": {"broker": False, "live": False, "network": False, "production": False},
    }
    authorization["digest"] = hashlib.sha256(canonical_json_bytes(authorization)).hexdigest()
    return {
        "schema_version": "p3-authority-request-file-v1",
        "execution_source": source.model_dump(mode="json"),
        "authorization": authorization,
    }, source


def test_authority_request_is_source_bound_and_unexpired(tmp_path) -> None:
    request, source = _request("2026-01-01T01:00:00Z")
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    path.chmod(0o600)
    with pytest.raises(AuthorityHeld, match="not current"):
        validate_request(path.resolve(), source, "BASELINES")


def test_protected_request_builds_one_closed_worker_payload() -> None:
    request, source = _request("2026-01-01T01:00:00Z")
    authorization = RunAuthorization.model_validate_json(
        json.dumps(request["authorization"])
    )
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from tests.p3.test_operation_input import intent
    operation_input = P3OperationInput.model_validate_json(intent(input_set_ref=authorization.input_set_ref.model_dump(mode="json")))
    payload = build_alpha_campaign_payload(
        authorization, source, "p3-baselines-v1", operation_input=operation_input,
    )
    assert payload.operation == "BASELINES"
    assert payload.logical_trial_id == "p3-baselines-v1"
    assert payload.manifest_ref.content_sha256 == hashlib.sha256(canonical_json_bytes(operation_input)).hexdigest()
    assert payload.manifest_ref != authorization.input_set_ref
    assert payload.authorization_ref.locator == (
        f"{payload.authorization_ref.content_sha256}.blob"
    )


def test_fixture_authorization_has_no_official_input_set_dependency(tmp_path,monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    request, source = _request("2026-01-01T01:00:00Z")
    authorization = request["authorization"]
    authorization["schema_version"] = "p3-fixture-authorization-v1"
    authorization["operation"] = "PARITY"
    authorization["fixture_plan_ref"] = authorization.pop("input_set_ref")
    authorization.pop("allowed_alpha_ids")
    now = datetime.now(UTC)
    authorization["issued_at"] = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    authorization["expires_at"] = (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    authorization.pop("digest")
    authorization["digest"] = hashlib.sha256(canonical_json_bytes(authorization)).hexdigest()
    path = tmp_path / "fixture-request.json"
    path.write_text(json.dumps(request)); path.chmod(0o600)
    for key,value in dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='nam176hermes/Trading-Agent',
        GITHUB_REF='refs/heads/main',GITHUB_SHA=source.commit_sha,GITHUB_REF_PROTECTED='true',
        GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_RUN_ID=str(authorization['issuer_run_id']),
        GITHUB_RUN_ATTEMPT=str(authorization['issuer_attempt']),
        GITHUB_WORKFLOW_REF='nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main').items():
        monkeypatch.setenv(key,value)
    validated = validate_request(path.resolve(), source, "PARITY")
    payload = build_alpha_campaign_payload(validated.authorization, source, "p3-integration-fixture-v1")
    assert payload.manifest_ref.model_dump(mode="json") == authorization["fixture_plan_ref"]
    assert not hasattr(validated.authorization, "input_set_ref")
    with pytest.raises(AuthorityHeld):
        build_alpha_campaign_payload(validated.authorization, source, "p3-native-parity-v1")


def test_official_authorization_cannot_bootstrap_integration_fixture() -> None:
    request, source = _request("2026-01-01T01:00:00Z")
    payload = request["authorization"]
    payload["operation"] = "PARITY"
    payload.pop("digest")
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    authorization = RunAuthorization.model_validate_json(json.dumps(payload))
    with pytest.raises(AuthorityHeld):
        build_alpha_campaign_payload(authorization, source, "p3-integration-fixture-v1")


def test_staging_publishes_authorization_and_verifies_manifest_before_enqueue(tmp_path):
    from packages.alpha_lifecycle.authority import stage_alpha_campaign_payload
    from packages.data_catalog.artifact_store import LocalArtifactStore
    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    request, source = _request('2026-01-01T01:00:00Z')
    body = request['authorization']
    manifest = b'{"fixture":"source-test-only"}'
    ref = store.put_bytes(manifest,media_type='application/json')
    body['input_set_ref'] = ref.model_dump(mode='json')
    body['review_ref'] = ref.model_dump(mode='json')
    body.pop('digest')
    body['digest'] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    authorization = RunAuthorization.model_validate_json(canonical_json_bytes(body))
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from tests.p3.test_operation_input import intent
    operation_input = P3OperationInput.model_validate_json(intent(input_set_ref=ref.model_dump(mode="json")))
    from datetime import UTC, datetime, timedelta
    from tests.p3.test_replica_execution import _seal
    now = datetime.now(UTC)
    review_ref = _seal(store, schema_version="p3-review-approval-v1",
        source=source, subject_digests=[operation_input.digest], operator_identity="operator",
        reviewer_identity="reviewer", review_execution_id="synthetic-review",
        verdict="APPROVED", issued_at=(now-timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        expires_at=(now+timedelta(minutes=5)).isoformat().replace("+00:00", "Z"), evidence_ref=ref,
        authority={"broker":False,"live":False,"network":False,"production":False})
    body['review_ref'] = review_ref.model_dump(mode='json')
    body['issued_at'] = now.isoformat().replace("+00:00", "Z")
    body['expires_at'] = (now+timedelta(minutes=4)).isoformat().replace("+00:00", "Z")
    body.pop('digest')
    body['digest'] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    authorization = RunAuthorization.model_validate_json(canonical_json_bytes(body))
    manifest = canonical_json_bytes(operation_input)
    payload = stage_alpha_campaign_payload(store,authorization,source,'p3-baselines-v1',manifest,operation_input=operation_input)
    assert store.read_bytes(payload.authorization_ref) == canonical_json_bytes(authorization)
    assert store.read_bytes(payload.manifest_ref) == manifest
    with pytest.raises(AuthorityHeld,match='manifest'):
        stage_alpha_campaign_payload(store,authorization,source,'p3-baselines-v1',b'wrong',operation_input=operation_input)


@pytest.mark.parametrize('fault',[None,'self_issued','different_nonce','wrong_source','wrong_host','changed_approval'])
def test_local_request_needs_current_protected_request_and_local_readiness(tmp_path,monkeypatch,fault):
    from datetime import UTC,datetime,timedelta
    from types import SimpleNamespace
    from uuid import UUID
    from packages.alpha_lifecycle import authority as module,local_qualification as custody
    from packages import local_pre_p3
    from packages.runtime_release import config
    from tests.p3.test_local_qualification import sealed
    from tests.p3.test_operation_input import intent
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    now=datetime.now(UTC)
    request,source=_request('2026-01-01T01:00:00Z')
    value=request['authorization']
    for key in ('issuer_workflow','issuer_run_id','issuer_attempt'):value.pop(key)
    identity=dict(issuer_kind='LOCAL',issuer_identity='operator-a',run_id='00000000-0000-4000-8000-000000000002',
        attempt=1,host_id='a'*64,boot_id='00000000-0000-4000-8000-000000000001')
    value.update(schema_version='p3-local-run-authorization-v1',identity=identity,
        issued_at=(now-timedelta(minutes=1)).isoformat().replace('+00:00','Z'),
        expires_at=(now+timedelta(minutes=5)).isoformat().replace('+00:00','Z'))
    request['authorization']=sealed(value)
    request['schema_version']='p3-local-authority-request-v1'
    request['operation_input']=P3OperationInput.model_validate_json(intent(input_set_ref=value['input_set_ref'])).model_dump(mode='json')
    path=Path('/etc/trading-agent/local-qualification/operator-a/operations')/(value['nonce']+'.json')
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
    run=LocalRunIdentity.model_validate_json(canonical_json_bytes(identity))
    monkeypatch.setattr(local_pre_p3,'read_local_pre_p3',lambda root:dict(PRE_P3_READY=SimpleNamespace(
        identity=run,binding=SimpleNamespace(source=source,policy_digest='f'*64),
        expires_at=now+timedelta(minutes=10))))
    # T02: the official lane now also admits its InputSet. This test keeps its own
    # scope (protected request plus local readiness); a dedicated test below covers
    # a request that has no research qualification at all.
    from packages.alpha_lifecycle import local_research
    monkeypatch.setattr(local_research,'read_local_research_inputs',
        lambda identity,*,source,policy_digest,input_set_ref:SimpleNamespace(
            expires_at=now+timedelta(minutes=10)))
    monkeypatch.setattr(custody,'_host_identity',lambda:('0'*64 if fault=='wrong_host' else 'a'*64,UUID(identity['boot_id'])))
    calls=[]
    def protected(candidate):
        assert candidate==path
        calls.append(candidate)
        changed={**request,'execution_source':{**request['execution_source'],'commit_sha':'0'*40}}
        return canonical_json_bytes(changed if fault=='changed_approval' and len(calls)>1 else request)+b'\n',(1,2)
    if fault=='self_issued':
        path=tmp_path/'request.json';path.write_bytes(canonical_json_bytes(request));path.chmod(0o600)
    else:monkeypatch.setattr(config,'_read_protected_file',protected)
    if fault=='different_nonce': request['authorization']=sealed({**request['authorization'],'nonce':identity['run_id']})
    if fault=='wrong_source': request['execution_source']={**request['execution_source'],'commit_sha':'0'*40}
    for key in tuple(__import__('os').environ):
        if key.startswith('GITHUB_'):monkeypatch.delenv(key)
    if fault:
        with pytest.raises(AuthorityHeld):validate_request(path,source,'BASELINES')
    else:
        validated=validate_request(path,source,'BASELINES')
        assert validated.authorization.identity==run
        payload=build_alpha_campaign_payload(validated.authorization,source,'p3-baselines-v1',operation_input=validated.operation_input)
        assert payload.expected_source==source


def test_official_local_request_without_research_qualification_is_held(tmp_path,monkeypatch):
    """T02: an official InputSet may not be admitted without P3_RESEARCH_INPUTS."""
    from datetime import UTC,datetime,timedelta
    from types import SimpleNamespace
    from uuid import UUID
    from packages.alpha_lifecycle import local_qualification as custody, local_research
    from packages import local_pre_p3
    from packages.runtime_release import config
    from tests.p3.test_local_qualification import sealed
    from tests.p3.test_operation_input import intent
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
    now=datetime.now(UTC)
    request,source=_request('2026-01-01T01:00:00Z')
    value=request['authorization']
    for key in ('issuer_workflow','issuer_run_id','issuer_attempt'):value.pop(key)
    identity=dict(issuer_kind='LOCAL',issuer_identity='operator-a',
        run_id='00000000-0000-4000-8000-000000000002',attempt=1,host_id='a'*64,
        boot_id='00000000-0000-4000-8000-000000000001')
    value.update(schema_version='p3-local-run-authorization-v1',identity=identity,
        issued_at=(now-timedelta(minutes=1)).isoformat().replace('+00:00','Z'),
        expires_at=(now+timedelta(minutes=5)).isoformat().replace('+00:00','Z'))
    request['authorization']=sealed(value)
    request['schema_version']='p3-local-authority-request-v1'
    request['operation_input']=P3OperationInput.model_validate_json(
        intent(input_set_ref=value['input_set_ref'])).model_dump(mode='json')
    path=Path('/etc/trading-agent/local-qualification/operator-a/operations')/(value['nonce']+'.json')
    run=LocalRunIdentity.model_validate_json(canonical_json_bytes(identity))
    monkeypatch.setattr(local_pre_p3,'read_local_pre_p3',lambda root:dict(PRE_P3_READY=SimpleNamespace(
        identity=run,binding=SimpleNamespace(source=source,policy_digest='f'*64),
        expires_at=now+timedelta(minutes=10))))
    monkeypatch.setattr(custody,'_host_identity',lambda:('a'*64,UUID(identity['boot_id'])))
    def protected(candidate):
        assert candidate==path
        return canonical_json_bytes(request)+b'\n',(1,2)
    monkeypatch.setattr(config,'_read_protected_file',protected)
    for key in tuple(__import__('os').environ):
        if key.startswith('GITHUB_'):monkeypatch.delenv(key)
    def absent(identity,*,source,policy_digest,input_set_ref):
        raise ValueError('no protected research profile for this issuer')
    monkeypatch.setattr(local_research,'read_local_research_inputs',absent)
    with pytest.raises(AuthorityHeld):
        validate_request(path,source,'BASELINES')
