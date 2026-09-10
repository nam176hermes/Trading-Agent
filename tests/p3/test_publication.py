import hashlib
from datetime import UTC, datetime
from uuid import UUID

import pytest

from packages.alpha_lifecycle.contracts.lifecycle import PrePublicationEvidence, PublicationRequest
from packages.alpha_lifecycle.publication import build_closure_report, build_publication_receipt
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_publication_repository import JobCommitResult


def _ref(character: str) -> ArtifactRefV1:
    digest = character * 64
    return ArtifactRefV1(content_sha256=digest,size_bytes=1,media_type="application/json",locator=f"{digest}.blob")


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _chain(tmp_path):
    root = tmp_path / "cas"
    root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    evidence_payload = {
        "schema_version":"p3-pre-publication-evidence-v1","stage":"REGISTER",
        "input_set_ref":_ref("1"),"baseline_selection_ref":_ref("2"),
        "qualification_bundle_ref":None,"exit_result_ref":None,
        "candidate_record_refs":(_ref("3"),),
    }
    evidence_payload["digest"] = _digest(evidence_payload)
    evidence = PrePublicationEvidence.model_validate(evidence_payload)
    evidence_ref = store.put_bytes(canonical_json_bytes(evidence),media_type="application/json")
    request_payload = {
        "schema_version":"p3-publication-request-v1","idempotency_key":"register-a0",
        "semantic_request_digest":"a"*64,"stage":"REGISTER","evidence_ref":evidence_ref,
        "expected_heads":({"alpha_id":"a0.test","version":"1.0.0","sequence":0,"event_digest":None},),
        "proposed_event_refs":(_ref("c"),),"job_id":"job-1",
    }
    request_payload["digest"] = _digest(request_payload)
    request = PublicationRequest.model_validate(request_payload)
    commit_payload = {
        "schema_version":"p3-job-commit-result-v1","job_id":"job-1",
        "idempotency_key":"register-a0","semantic_request_digest":"a"*64,
        "prepublication_ref":evidence_ref,
        "ledger_event_ids":(UUID("11111111-1111-5111-8111-111111111111"),),
        "registry_event_refs":(_ref("c"),),"alpha_outcome":"NOT_EVALUATED",
    }
    commit_payload["digest"] = _digest({
        **commit_payload,
        "ledger_event_ids": tuple(str(value) for value in commit_payload["ledger_event_ids"]),
    })
    commit = JobCommitResult.model_validate(commit_payload)
    request_ref = store.put_bytes(canonical_json_bytes(request),media_type="application/json")
    receipt = build_publication_receipt(request_ref,commit,committed_at=datetime(2026,9,5,tzinfo=UTC),store=store)
    closure = build_closure_report(request,commit.prepublication_ref,receipt,store=store)
    return store, request, commit, receipt, closure


def test_receipt_and_closure_are_downstream_of_commit(tmp_path) -> None:
    store, request, commit, receipt, closure = _chain(tmp_path)
    assert JobCommitResult.model_validate_json(store.read_bytes(receipt.commit_result_ref)) == commit
    assert closure.prepublication_ref == commit.prepublication_ref
    assert closure.projection_status == "PENDING"


def _changed(value, **updates):
    body = value.model_dump(mode='json', exclude={'digest'})
    body.update(updates)
    body['digest'] = _digest(body)
    return type(value).model_validate_json(canonical_json_bytes(body))


def test_receipt_cannot_attach_a_commit_to_another_request(tmp_path):
    store, request, commit, receipt, _ = _chain(tmp_path)
    wrong = _changed(request, job_id='another-job')
    ref = store.put_bytes(canonical_json_bytes(wrong), media_type='application/json')
    with pytest.raises(ValueError, match='commit result'):
        build_publication_receipt(ref, commit, committed_at=receipt.committed_at, store=store)


@pytest.mark.parametrize('edge', ['evidence', 'request', 'ledger', 'events', 'commit'])
def test_closure_cannot_combine_unrelated_publication_artifacts(tmp_path, edge):
    store, request, commit, receipt, _ = _chain(tmp_path)
    evidence_ref = request.evidence_ref
    if edge == 'evidence':
        evidence = PrePublicationEvidence.model_validate_json(store.read_bytes(evidence_ref))
        evidence_ref = store.put_bytes(canonical_json_bytes(_changed(
            evidence, input_set_ref=_ref('8').model_dump(mode='json'))), media_type='application/json')
    elif edge == 'request':
        request = _changed(request, job_id='another-job')
    elif edge == 'ledger':
        receipt = _changed(receipt, ledger_event_ids=['99999999-9999-5999-8999-999999999999'])
    elif edge == 'events':
        receipt = _changed(receipt, registry_event_refs=[_ref('8').model_dump(mode='json')])
    else:
        wrong = _changed(commit, job_id='another-job')
        ref = store.put_bytes(canonical_json_bytes(wrong), media_type='application/json')
        receipt = _changed(receipt, commit_result_ref=ref.model_dump(mode='json'))
    with pytest.raises(ValueError, match='publication|commit result'):
        build_closure_report(request, evidence_ref, receipt, store=store)
