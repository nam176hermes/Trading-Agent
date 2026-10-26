"""Consumer-boundary tests with declared synthetic metadata; no H1 or runtime authority."""
from datetime import UTC, datetime
from types import SimpleNamespace
import hashlib

import pytest

from packages.alpha_lifecycle.replica_store import _read
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.alpha_lifecycle.contracts.data import DatasetEvidence
from packages.alpha_lifecycle.contracts.execution import InputSet, HoldoutManifest, InstrumentSpec
from packages.alpha_lifecycle.contracts.authority import RunAuthorization, FamilyReview, PrimarySelection
from packages.alpha_lifecycle.contracts.lifecycle import RegistrationProof
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from tests.p3.test_reference_input import reference_seed, _changed  # noqa: F401
from tests.p3.test_holdout_admission import metadata_request  # noqa: F401
from tests.p3.test_baseline_operation import _cli_authorization
from tests.p3.test_replica_execution import _seal


def changed(store, ref, model, **values):
    return store.put_bytes(canonical_json_bytes(_changed(_read(store, ref, model), **values)),
        media_type='application/json')


def closed(label):
    digest = hashlib.sha256(label.encode()).hexdigest()
    return ArtifactRefV1(content_sha256=digest, size_bytes=1, media_type='application/json', locator=digest+'.blob')


def synthetic_dataset(store, ref, **values):
    return changed(store, ref, DatasetEvidence, vintage_class='SYNTHETIC_REHEARSAL',
        limitations=['synthetic.rehearsal_only'], **values)


@pytest.mark.parametrize('fault', [None, 'market_h', 'market_b', 'relabelled_context',
    'commitment_h', 'commitment_b', 'generation_h', 'missing_context'])
def test_holdout_synthetic_metadata_branch_never_reads_sealed_rows(metadata_request, monkeypatch, fault):
    from packages.alpha_lifecycle import authority, holdout
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    store, original, _, manifest = metadata_request
    body = original.body.model_dump(mode='json')
    forbidden = set()
    for field in ('holdout_dataset_ref', 'buffer_ref', 'context_dataset_ref'):
        dataset = _read(store, getattr(original.body, field), DatasetEvidence)
        if field != 'context_dataset_ref':
            forbidden.update(ref.content_sha256 for ref in dataset.row_refs)
        snapshot = closed(field+'.snapshot')
        forbidden.add(snapshot.content_sha256)
        market = ((fault == 'market_h' and field == 'holdout_dataset_ref')
            or (fault == 'market_b' and field == 'buffer_ref')
            or (fault == 'relabelled_context' and field == 'context_dataset_ref'))
        ref = changed(store, getattr(original.body, field), DatasetEvidence, snapshot_ref=snapshot)
        body[field] = ref if market else synthetic_dataset(store, ref)
    generation = closed('generation')
    inputs_ref = changed(store, original.input_set_ref, InputSet, synthetic_generation_ref=generation,
        dataset_evidence_ref=body['context_dataset_ref'])
    inputs = _read(store, inputs_ref, InputSet)
    h_ref = changed(store, inputs_ref, InputSet, dataset_evidence_ref=body['holdout_dataset_ref'],
        synthetic_generation_ref=closed('wrong generation') if fault == 'generation_h' else generation)
    body['holdout_input_set_ref'] = h_ref
    cipher, attestation = closed('cipher'), closed('attestation')
    forbidden.update((cipher.content_sha256, attestation.content_sha256))
    body['custody_record_ref'] = _seal(store, schema_version='p3-custody-record-v1',
        holdout_commitment='a'*64, ciphertext_ref=cipher, plaintext_bundle_digest='b'*64,
        custodian_identity='unit.custodian', research_identity='unit.research',
        custodian_attestation_ref=attestation, access_policy_digest='c'*64,
        classification='HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND')
    commitment = _seal(store, schema_version='p3-synthetic-commitment-v1', purpose='SYNTHETIC_REHEARSAL_ONLY',
        source=inputs.source, policy_digest=inputs.policy_digest, generation_ref=generation,
        research_dataset_ref=body['context_dataset_ref'],
        holdout_dataset_ref=closed('wrong H') if fault == 'commitment_h' else body['holdout_dataset_ref'],
        buffer_dataset_ref=closed('wrong B') if fault == 'commitment_b' else body['buffer_ref'],
        backup_receipt_ref=closed('backup'), issued_at=datetime.now(UTC).isoformat().replace('+00:00','Z'),
        authority=dict(broker=False, live=False, network=False, production=False))
    ref = _seal(store, **{**original.model_dump(mode='json', exclude={'digest'}),
        'input_set_ref': inputs_ref, 'body': body})
    intent = _read(store, ref, P3OperationInput)
    authorization = _read(store, _cli_authorization(store, ref, manifest.source), RunAuthorization)
    contexts = []
    def current_context(*args, **kwargs):
        contexts.append(True)
        # Protected context itself has separate actual staging/profile tests.
        return None if fault == 'missing_context' else SimpleNamespace(commitment_ref=commitment)
    monkeypatch.setattr(authority, 'require_synthetic_operation_context', current_context)
    class DownstreamIntegrationReached(Exception):
        pass
    def read(ref):
        assert ref.content_sha256 not in forbidden, 'synthetic admission opened sealed rows or snapshots'
        if ref == inputs.integration_receipt_ref:
            # Stop after synthetic + InputSet/role checks, before unrelated
            # integration/family qualification. This is not a qualification PASS.
            raise DownstreamIntegrationReached
        return store.read_bytes(ref)
    if fault:
        match = ('current same-run' if fault == 'missing_context' else
                 'InputSets' if fault == 'generation_h' else 'reviewed generation')
        with pytest.raises(ValueError, match=match):
            holdout.validate_holdout_operation_input(intent, authorization, expected_source=manifest.source,
                store=SimpleNamespace(read_bytes=read), now=datetime.now(UTC))
    else:
        with pytest.raises(DownstreamIntegrationReached):
            holdout.validate_holdout_operation_input(intent, authorization, expected_source=manifest.source,
                store=SimpleNamespace(read_bytes=read), now=datetime.now(UTC))
    assert contexts == [True]


@pytest.mark.parametrize('fault', [None, 'market_h', 'market_b', 'no_purpose'])
def test_executable_calculation_refuses_mixed_or_purposeless_segments(reference_seed, fault):
    from packages.alpha_lifecycle.executable_reference import _execution_inputs
    root, manifest_ref, spec_ref, *_ = reference_seed
    store = LocalArtifactStore(root)
    manifest = _read(store, manifest_ref, HoldoutManifest)
    refs = {}
    for field in ('context_dataset_ref', 'holdout_dataset_ref', 'buffer_ref'):
        ref = getattr(manifest, field)
        market = (fault == 'market_h' and field == 'holdout_dataset_ref'
                  or fault == 'market_b' and field == 'buffer_ref')
        refs[field] = ref if market else synthetic_dataset(store, ref)
    registration = _read(store, manifest.research_registration_ref, RegistrationProof)
    purpose = {} if fault == 'no_purpose' else dict(synthetic_generation_ref=closed('generation'))
    inputs_ref = changed(store, registration.input_set_ref, InputSet,
        dataset_evidence_ref=refs['context_dataset_ref'], **purpose)
    registration_ref = changed(store, manifest.research_registration_ref, RegistrationProof, input_set_ref=inputs_ref)
    primary = _read(store, manifest.primary_selection_ref, PrimarySelection)
    family_ref = changed(store, primary.family_review_ref, FamilyReview, input_set_ref=inputs_ref)
    primary_ref = changed(store, manifest.primary_selection_ref, PrimarySelection, family_review_ref=family_ref)
    current = HoldoutManifest.model_validate(_changed(manifest, **refs,
        research_registration_ref=registration_ref, primary_selection_ref=primary_ref))
    spec = _read(store, spec_ref, InstrumentSpec)
    if fault:
        with pytest.raises(ValueError, match='hash-bound InputSet purpose' if fault == 'no_purpose' else 'mix market'):
            _execution_inputs(current, spec, store)
    else:
        assert _execution_inputs(current, spec, store)[1].vintage_class == 'SYNTHETIC_REHEARSAL'


def test_official_and_local_graphs_reject_synthetic_before_producer_traversal(reference_seed):
    from packages.alpha_lifecycle.github_research import validate_github_research_graph
    from packages.alpha_lifecycle.local_research import validate_local_research_graph
    root, manifest_ref, *_ = reference_seed
    store = LocalArtifactStore(root)
    manifest = _read(store, manifest_ref, HoldoutManifest)
    registration = _read(store, manifest.research_registration_ref, RegistrationProof)
    ref = changed(store, registration.input_set_ref, InputSet, synthetic_generation_ref=closed('generation'))
    inputs = _read(store, ref, InputSet)
    reads = []
    def read(target):
        assert target == ref, 'synthetic purpose reached protected producer graph'
        reads.append(target)
        return store.read_bytes(target)
    reader = SimpleNamespace(read_bytes=read)
    with pytest.raises(ValueError, match='OFFICIAL research cannot admit synthetic'):
        validate_github_research_graph(SimpleNamespace(input_set_ref=ref), None, reader)
    with pytest.raises(ValueError, match='LOCAL research cannot admit synthetic'):
        validate_local_research_graph(None, inputs, None, reader)
    assert reads == [ref]
