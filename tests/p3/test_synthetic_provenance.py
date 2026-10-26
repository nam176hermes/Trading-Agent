"""Synthetic provenance is structural evidence, never market or run authority."""
import hashlib
import json
from pathlib import Path

import pytest

from packages.alpha_lifecycle.contracts.data import DatasetEvidence, PITProof
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_dataset import _ref


def sealed(model, **body):
    return model.model_validate_json(canonical_json_bytes({
        **body, 'digest': hashlib.sha256(canonical_json_bytes(body)).hexdigest(),
    }))


def dataset(**changes):
    body = dict(schema_version='p3-dataset-evidence-v1', snapshot_ref=_ref('a' * 64),
        query_digest='b' * 64, segment='BUFFER',
        date_range=dict(start='2026-09-01', end='2026-09-01'), usable_rows=1,
        row_refs=(_ref('c' * 64),), ordered_rows_digest='d' * 64,
        vintage_class='SYNTHETIC_REHEARSAL', observed_cutoff='2026-10-26T00:00:00Z',
        limitations=('synthetic.rehearsal_only',))
    return sealed(DatasetEvidence, **(body | changes))


def pit(**changes):
    body = dict(schema_version='p3-p-i-t-proof-v1', dataset_ref=_ref('a' * 64),
        fold_manifest_ref=_ref('b' * 64), vintage_class='SYNTHETIC_REHEARSAL',
        historical_vintage_verified=False, revision_proof_ref=_ref('c' * 64),
        no_future_suite_ref=_ref('d' * 64), limitations=('synthetic.rehearsal_only',))
    return sealed(PITProof, **(body | changes))


def test_synthetic_vintage_is_explicit_and_cannot_claim_market_history():
    assert dataset().vintage_class == pit().vintage_class == 'SYNTHETIC_REHEARSAL'
    with pytest.raises(ValueError):
        pit(historical_vintage_verified=True)
    for invalid in ((), ('retrospective.current_archive',)):
        with pytest.raises(ValueError):
            dataset(limitations=invalid)
        with pytest.raises(ValueError):
            pit(limitations=invalid)


def test_official_vintage_cannot_absorb_synthetic_limitation():
    with pytest.raises(ValueError):
        dataset(vintage_class='RETROSPECTIVE_CURRENT_ARCHIVE')
    with pytest.raises(ValueError):
        pit(vintage_class='RETROSPECTIVE_CURRENT_ARCHIVE')


def test_committed_seed9_matches_retained_generator_without_tuning():
    from packages.alpha_lifecycle.synthetic_prices import daily_rows
    rows = daily_rows()
    assert len(rows) == 3166
    assert hashlib.sha256(canonical_json_bytes(rows)).hexdigest() == (
        '54b4f688654789cec4668e3644f4b94a408534d5e86101883be9a27793bf23f7')
    assert rows == daily_rows()


def test_generation_has_no_market_or_workflow_issuer_and_rejects_tampering(tmp_path):
    from packages.alpha_lifecycle.synthetic_data import generate_synthetic_datasets, validate_synthetic_dataset
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from tests.p3.test_github_run import SOURCE
    root = tmp_path / 'synthetic'
    root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    source = SourceIdentity.model_validate(SOURCE)
    result = generate_synthetic_datasets(store=store, source=source, policy_digest='a' * 64)
    generation = result.generation
    raw = canonical_json_bytes(generation)
    assert b'binance.public-archive' not in raw and b'producer_run_id' not in raw
    assert generation.purpose == 'SYNTHETIC_REHEARSAL_ONLY'
    for segment, ref in result.datasets.items():
        actual = validate_synthetic_dataset(ref, generation_ref=result.generation_ref,
            source=source, policy_digest='a' * 64, segment=segment, store=store)
        assert actual.vintage_class == 'SYNTHETIC_REHEARSAL'
    from packages.alpha_lifecycle.replica_store import _read
    original = _read(store, result.datasets['BUFFER'], DatasetEvidence)
    body = original.model_dump(mode='json', exclude={'digest'})
    body.update(vintage_class='RETROSPECTIVE_CURRENT_ARCHIVE', limitations=['retrospective.current_archive'])
    tampered = sealed(DatasetEvidence, **body)
    ref = store.put_bytes(canonical_json_bytes(tampered), media_type='application/json')
    with pytest.raises(ValueError):
        validate_synthetic_dataset(ref, generation_ref=result.generation_ref,
            source=source, policy_digest='a' * 64, segment='BUFFER', store=store)
    from packages.alpha_lifecycle.synthetic_data import backup_synthetic_generation, validate_synthetic_commitment
    backup_root = tmp_path / 'backup'
    backup_root.mkdir(mode=0o700)
    # Original source objects are immutable; unrelated relabelling cannot alter the commitment.
    commitment_ref = backup_synthetic_generation(result, store=store, backup=LocalArtifactStore(backup_root))
    commitment = validate_synthetic_commitment(commitment_ref, generation_ref=result.generation_ref,
        dataset_ref=result.datasets['RESEARCH'], source=source, policy_digest='a' * 64, store=store)
    assert commitment.holdout_dataset_ref == result.datasets['HOLDOUT']
    from packages.alpha_lifecycle.synthetic_data import read_synthetic_commitment_metadata
    args = dict(generation_ref=result.generation_ref, dataset_ref=result.datasets['RESEARCH'],
        source=source, policy_digest='a' * 64, store=store)
    for changed in (dict(policy_digest='b' * 64), dict(dataset_ref=result.datasets['HOLDOUT']),
                    dict(generation_ref=result.datasets['BUFFER'])):
        with pytest.raises(ValueError):
            read_synthetic_commitment_metadata(commitment_ref, **(args | changed))
    from packages.alpha_lifecycle.synthetic_data import SyntheticBackup, SyntheticCommitment
    original_backup = _read(store, commitment.backup_receipt_ref, SyntheticBackup)
    inventory = json.loads(store.read_bytes(original_backup.inventory_ref))
    inventory.pop(0)
    inventory_ref = store.put_bytes(json.dumps(inventory, indent=2).encode(), media_type='application/json')
    altered_backup = sealed(SyntheticBackup,
        **(original_backup.model_dump(mode='json', exclude={'digest'}) | dict(inventory_ref=inventory_ref)))
    altered_backup_ref = store.put_bytes(canonical_json_bytes(altered_backup), media_type='application/json')
    altered = sealed(SyntheticCommitment,
        **(commitment.model_dump(mode='json', exclude={'digest'}) | dict(backup_receipt_ref=altered_backup_ref)))
    altered_ref = store.put_bytes(canonical_json_bytes(altered), media_type='application/json')
    with pytest.raises(ValueError, match='bounded unique inventory'):
        read_synthetic_commitment_metadata(altered_ref, **args)
    from packages.alpha_lifecycle.research_custody import parse_research_batch_commitment
    with pytest.raises(ValueError):
        parse_research_batch_commitment(store.read_bytes(commitment_ref))
    with pytest.raises(ValueError):
        generate_synthetic_datasets(store=store, source=source, policy_digest='a' * 64)


def test_synthetic_purpose_is_hash_bound_without_changing_official_input_bytes():
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from tests.p3.test_github_run import SOURCE
    policy = json.loads((Path(__file__).resolve().parents[2] /
        'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes())
    body = dict(schema_version='p3-input-set-v1', source=SOURCE, epoch_id='test-epoch',
        policy_digest='a' * 64, family_digest='b' * 64, cost_model=policy['costs']['base'],
        **{field: _ref('c' * 64) for field in ('dataset_evidence_ref', 'fold_manifest_ref', 'pit_proof_ref',
            'environment_ref', 'regime_threshold_ref', 'integration_receipt_ref')})
    official = sealed(InputSet, **body)
    expected = {**body, 'digest': hashlib.sha256(canonical_json_bytes(body)).hexdigest()}
    assert canonical_json_bytes(official) == canonical_json_bytes(expected)
    synthetic = sealed(InputSet, **body, synthetic_generation_ref=_ref('d' * 64))
    assert synthetic.synthetic_generation_ref.content_sha256 == 'd' * 64
    assert synthetic.digest != official.digest


def test_producer_refuses_existing_output_and_source_mutation(tmp_path, monkeypatch):
    from scripts import prepare_p3_synthetic_rehearsal as module
    from tests.p3.test_github_run import SOURCE
    from types import SimpleNamespace
    existing = tmp_path / 'existing'
    existing.mkdir()
    sentinel = existing / 'evidence'
    sentinel.write_bytes(b'preserve')
    with pytest.raises(ValueError, match='new, absolute'):
        module.main(['--output-root', str(existing)])
    assert sentinel.read_bytes() == b'preserve'
    source_reads = iter([SOURCE, SOURCE | dict(commit_sha='e' * 40)])
    monkeypatch.setattr(module, '_committed_source', lambda: next(source_reads))
    monkeypatch.setattr(module, 'generate_synthetic_datasets', lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(module, 'backup_synthetic_generation', lambda *args, **kwargs: None)
    output = tmp_path / 'mutation'
    with pytest.raises(ValueError, match='source changed'):
        module.main(['--output-root', str(output)])
    assert output.is_dir() and not (output / 'proposal.json').exists()
