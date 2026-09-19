"""Produce LOCAL research proposals using the existing sealer and full backup reader."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import UTC, datetime
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
from packages.alpha_lifecycle.contracts.data import DatasetEvidence
from packages.alpha_lifecycle.local_qualification import _host_identity
from packages.alpha_lifecycle.local_research import LocalResearchProof
from packages.alpha_lifecycle.pit_evidence import P3ResearchRevisionInventory
from packages.alpha_lifecycle.replica_store import _read
from packages.alpha_lifecycle.research_custody import (
    P3LocalResearchBatchCommitment, _seal, backup_revision_inventory_local,
)
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1, PITQueryV1
from packages.engine_contracts.serialization import canonical_json_bytes
from scripts.seal_p3_dataset import _committed_source, main as seal_dataset


def produce_research_proposal(inventory_ref, *, source, policy_digest, query,
                              dataset_ref, store, backup, identity):
    """No approval is installed; emit facts only after complete destination readback."""
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(identity))
    query = PITQueryV1.model_validate(query)
    receipt, inventory_copy = backup_revision_inventory_local(inventory_ref, source=source,
        policy_digest=policy_digest, query=query, dataset_ref=dataset_ref,
        store=store, backup=backup, identity=identity)
    inventory = _read(store, inventory_ref, P3ResearchRevisionInventory)
    dataset = _read(store, dataset_ref, DatasetEvidence)

    def retain(value):
        return store.put_bytes(canonical_json_bytes(value), media_type='application/json')

    backup_ref = retain(receipt)
    commitment = _seal(dict(schema_version='p3-local-research-batch-commitment-v1',
        source=source, authority=receipt.authority, identity=identity,
        policy_digest=policy_digest, segment='RESEARCH', query=query,
        inventory_content_sha256=inventory_ref.content_sha256, inventory_size_bytes=inventory_ref.size_bytes,
        inventory_entry_count=len(inventory.entries), dataset_content_sha256=dataset_ref.content_sha256,
        dataset_digest=dataset.digest, snapshot_content_sha256=dataset.snapshot_ref.content_sha256,
        batch_ordinal=1, predecessor_commitment_content_sha256=None,
        frozen_at=query.cutoff.isoformat().replace('+00:00','Z'),
        completed_at=query.cutoff.isoformat().replace('+00:00','Z'),
        issued_at=datetime.now(UTC).isoformat().replace('+00:00','Z'),
        backup_receipt_ref=backup_ref, status='PASS'), P3LocalResearchBatchCommitment)
    commitment_ref = retain(commitment)
    proof = _seal(dict(schema_version='p3-local-research-proof-v1', identity=identity,
        source_store_root=str(store._root), backup_store_root=str(backup._root),
        commitment_ref=commitment_ref, backup_receipt_ref=backup_ref,
        backup_inventory_ref=inventory_copy, verified_at=datetime.now(UTC).isoformat().replace('+00:00','Z'),
        status='READBACK_VERIFIED', authority=receipt.authority), LocalResearchProof)
    return dict(schema_version='p3-local-research-proposal-v1', source=source, identity=identity,
        dataset_ref=dataset_ref, revision_inventory_ref=inventory_ref,
        backup_receipt_ref=backup_ref, commitment_ref=commitment_ref, proof_ref=retain(proof),
        qualification='PROPOSAL_ONLY')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'identity', 'store', 'backup-store', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--acquisition-refs', action='store_true')
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.resolve().is_relative_to(ROOT):
        raise ValueError('proposal output must be new and outside source')
    source = _committed_source()
    identity = LocalRunIdentity.model_validate_json(args.identity.read_bytes())
    if _host_identity() != (identity.host_id, identity.boot_id):
        raise ValueError('research producer host or boot differs')
    store, backup = LocalArtifactStore(args.store), LocalArtifactStore(args.backup_store)
    payload = json.loads(args.input.read_bytes())
    query = PITQueryV1.model_validate_json(canonical_json_bytes(payload['query']))
    if args.acquisition_refs:
        output = StringIO()
        with redirect_stdout(output):
            seal_dataset(args.input, args.store, acquisition_refs=True)
        sealed = json.loads(output.getvalue())
        payload.update(revision_inventory_ref=sealed['revision_inventory_ref'],
            dataset_ref=store.put_bytes(canonical_json_bytes(sealed['dataset']), media_type='application/json'))
    policy = hashlib.sha256(canonical_json_bytes(json.loads(
        (ROOT/'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes()))).hexdigest()
    result = produce_research_proposal(ArtifactRefV1.model_validate(payload['revision_inventory_ref']),
        source=source, policy_digest=policy, query=query,
        dataset_ref=ArtifactRefV1.model_validate(payload['dataset_ref']),
        store=store, backup=backup, identity=identity)
    if _committed_source() != source:
        raise ValueError('source changed during research production')
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(canonical_json_bytes(result)+b'\n')
        output.flush()
        os.fsync(output.fileno())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
