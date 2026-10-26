"""Prepare synthetic data only; never dispatch, approve, register or open a campaign.

The fixed generator creates all segments before any outcome. A fresh empty root
outside source contains work/backup CAS and a proposal. Existing roots are never
reused. Integration, real PIT execution, independent review and protected run
admission remain separate prerequisites; this command grants none of them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.synthetic_data import backup_synthetic_generation, generate_synthetic_datasets
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from scripts.seal_p3_dataset import _committed_source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.output_root
    if not root.is_absolute() or root.resolve().is_relative_to(ROOT) or root.exists() or root.is_symlink():
        raise ValueError('synthetic output root must be new, absolute and outside source')
    source = SourceIdentity.model_validate(_committed_source())
    policy = hashlib.sha256(canonical_json_bytes(json.loads(
        (ROOT/'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes()))).hexdigest()
    root.mkdir(mode=0o700)
    (root/'work').mkdir(mode=0o700)
    (root/'backup').mkdir(mode=0o700)
    store, backup = LocalArtifactStore(root/'work'), LocalArtifactStore(root/'backup')
    result = generate_synthetic_datasets(store=store, source=source, policy_digest=policy)
    commitment_ref = backup_synthetic_generation(result, store=store, backup=backup)
    if SourceIdentity.model_validate(_committed_source()) != source:
        raise ValueError('synthetic source changed during generation; output is not an accepted proposal')
    proposal = dict(schema_version='p3-synthetic-dataset-proposal-v1', source=source,
        generation_ref=result.generation_ref, commitment_ref=commitment_ref, datasets=result.datasets,
        purpose='SYNTHETIC_REHEARSAL_ONLY', qualification='PROPOSAL_ONLY', authority=result.generation.authority)
    with (root/'proposal.json').open('xb') as stream:
        stream.write(canonical_json_bytes(proposal) + b'\n')
    print(canonical_json_bytes(proposal).decode())


if __name__ == '__main__':
    main()
