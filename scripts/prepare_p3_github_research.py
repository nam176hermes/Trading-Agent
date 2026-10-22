"""GITHUB research producer for operation `p3-research-inputs-v1` (runs as `ta-p3-producer`).

The root controller authenticates the run and creates the custody roots. This process does the
following, inside that run:

1. reads the protected run attestation and the reviewed pre-run request pinned by it;
2. acquires the 2,800 research days from the public archive (the committed M7 monthly exception
   for 2018-02-08 only);
3. seals the dataset, backs it up with readback and commits the FIRST batch with this run's identity;
4. executes the PIT suite for this source and run;
5. assembles and validates the InputSet.

It prints a proposal and writes nothing outside its two custody roots. Root re-reads every named
object before anything leaves the host. argv: --run-id N --attempt N
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import UTC, date, datetime, timedelta
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import pwd
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.github_research import (  # noqa: E402
    RESEARCH_OPERATION, read_github_research_batch_request, research_roots,
)
from packages.alpha_lifecycle.github_run import GithubRunHeld, read_github_run, read_github_source_ready  # noqa: E402
from packages.data_catalog.artifact_store import LocalArtifactStore  # noqa: E402
from packages.data_contracts import ArtifactRefV1, PITQueryMode, PITQueryV1  # noqa: E402
from packages.engine_contracts.serialization import canonical_json_bytes  # noqa: E402
from scripts.p3_github_research import PRODUCER_USER  # noqa: E402

# The sealer materializes before the cutoff and the backup verifies after it (LOCAL sequencing).
CUTOFF_MARGIN = timedelta(minutes=5)
MONTHLY_EXCEPTION = date(2018, 2, 8)


def _utc(moment: datetime) -> str:
    return moment.isoformat().replace('+00:00', 'Z')


def acquire(request, store, transport_factory):
    """Daily up to 2018-02-07, the committed monthly exception for 2018-02-08, daily from 2018-02-09."""
    from packages.alpha_lifecycle.acquisition import acquire_day_receipt, acquire_monthly_exception
    start, end = date.fromisoformat(request.start), date.fromisoformat(request.end)
    exceptions = {date.fromisoformat(day) for day in request.monthly_exceptions}
    if exceptions != {MONTHLY_EXCEPTION}:
        raise ValueError('only the committed 2018-02-08 monthly exception is accepted')
    daily, monthly = transport_factory(monthly_exception=False), transport_factory(monthly_exception=True)
    references, current = [], start
    while current <= end:
        receipt = (acquire_monthly_exception(current, monthly, store) if current in exceptions
                   else acquire_day_receipt(current, daily, store))
        references.append(json.loads(receipt.artifact_ref.model_dump_json()))
        current += timedelta(days=1)
    return references


def produce(run, request, roots, *, source, transport_factory, now=lambda: datetime.now(UTC),
            sleep=time.sleep, seal=None, pit_suite=None):
    """The producer body; returns the proposal document. Each step is a committed function."""
    from packages.alpha_lifecycle.baseline_campaign import validate_research_inputs
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.alpha_lifecycle.contracts.data import DatasetEvidence
    from packages.alpha_lifecycle.local_campaign import assemble_input_set
    from packages.alpha_lifecycle.pit_evidence import P3ResearchRevisionInventory
    from packages.alpha_lifecycle.replica_store import _read
    from packages.alpha_lifecycle.research_custody import (
        P3ResearchBatchCommitmentV2, _seal, backup_revision_inventory,
    )
    from packages.alpha_lifecycle.sandbox_policy import SANDBOX_PROFILE_V2_SHA256
    from packages.local_pre_p3 import build_environment_identity
    from scripts.qualify_pre_p3 import _execute_pit_suite
    from scripts.seal_p3_dataset import main as seal_dataset
    run_id, attempt = run.identity.run_id, run.identity.attempt
    store, backup = LocalArtifactStore(roots['work']), LocalArtifactStore(roots['backup'])
    store.read_bytes(request.integration_receipt_ref)  # staged by root from the fixture CAS
    references = acquire(request, store, transport_factory)
    frozen = now().replace(microsecond=0)
    cutoff = frozen + CUTOFF_MARGIN
    query = PITQueryV1(mode=PITQueryMode.SYSTEM_OBSERVED, valid_at=datetime(2025, 9, 1, tzinfo=UTC), cutoff=cutoff)
    with tempfile.TemporaryDirectory(prefix='p3-research-') as private:
        request_path = Path(private)/'seal-input.json'
        request_path.write_bytes(canonical_json_bytes(dict(query=query, acquisition_refs=references)))
        output = StringIO()
        with redirect_stdout(output):
            (seal or seal_dataset)(request_path, roots['work'], acquisition_refs=True)
    sealed = json.loads(output.getvalue())
    inventory_ref = ArtifactRefV1.model_validate(sealed['revision_inventory_ref'])
    dataset = DatasetEvidence.model_validate_json(canonical_json_bytes(sealed['dataset']))
    dataset_ref = store.put_bytes(canonical_json_bytes(dataset), media_type='application/json')
    while now() <= cutoff:  # backup and commitment follow the declared cutoff
        sleep(1)
    policy = hashlib.sha256(canonical_json_bytes(json.loads(
        (ROOT/'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes()))).hexdigest()
    receipt, backup_inventory = backup_revision_inventory(inventory_ref, source=source, policy_digest=policy,
        query=query, dataset_ref=dataset_ref, store=store, backup=backup, producer_run_id=run_id,
        producer_attempt=attempt, version=2)
    backup_ref = store.put_bytes(canonical_json_bytes(receipt), media_type='application/json')
    inventory = _read(store, inventory_ref, P3ResearchRevisionInventory)
    commitment = _seal(dict(schema_version='p3-research-batch-commitment-v2', source=source,
        authority=receipt.authority, producer_repository='nam176hermes/Trading-Agent',
        producer_workflow_ref='nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main',
        producer_run_id=run_id, producer_attempt=attempt, policy_digest=policy, segment='RESEARCH', query=query,
        inventory_content_sha256=inventory_ref.content_sha256, inventory_size_bytes=inventory_ref.size_bytes,
        inventory_entry_count=len(inventory.entries), dataset_content_sha256=dataset_ref.content_sha256,
        dataset_digest=dataset.digest, snapshot_content_sha256=dataset.snapshot_ref.content_sha256,
        batch_ordinal=1, predecessor_commitment_content_sha256=None, frozen_at=_utc(frozen),
        completed_at=_utc(cutoff), issued_at=_utc(now().replace(microsecond=0)),
        backup_receipt_ref=backup_ref, status='PASS'), P3ResearchBatchCommitmentV2)
    commitment_ref = store.put_bytes(canonical_json_bytes(commitment), media_type='application/json')
    qualification = dict(producer='scripts/qualify_pre_p3.py', run_id=str(run_id), run_attempt=str(attempt))
    output = StringIO()
    with redirect_stdout(output):
        suite = (pit_suite or _execute_pit_suite)(source.model_dump(mode='json'), qualification, store)
    suite_ref = store.put_bytes(canonical_json_bytes(suite), media_type='application/json')
    environment = build_environment_identity(ROOT, sandbox_policy_digest=SANDBOX_PROFILE_V2_SHA256)
    environment_ref = store.put_bytes(canonical_json_bytes(environment), media_type='application/json')
    input_set_ref = assemble_input_set(source=SourceIdentity.model_validate(source), dataset_ref=dataset_ref,
        commitment_ref=commitment_ref, pit_suite_ref=suite_ref, environment_ref=environment_ref,
        integration_receipt_ref=request.integration_receipt_ref, store=store)
    validate_research_inputs(input_set_ref, store)
    inputs = json.loads(store.read_bytes(input_set_ref))
    value = dict(schema_version='p3-github-research-proposal-v1', identity=run.identity.model_dump(mode='json'),
                 head_sha=run.head_sha, input_set_ref=input_set_ref.model_dump(mode='json'),
                 environment_ref=environment_ref.model_dump(mode='json'), pit_proof_ref=inputs['pit_proof_ref'],
                 commitment_ref=commitment_ref.model_dump(mode='json'), backup_receipt_ref=backup_ref.model_dump(mode='json'),
                 backup_inventory_ref=backup_inventory.model_dump(mode='json'), completed_at=_utc(now()),
                 authority=dict(broker=False, live=False, network=False, production=False))
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return value


def main(argv=None, *, transport_factory=None) -> int:
    from scripts.acquire_p3_binance_daily import PublicArchiveTransport
    from scripts.seal_p3_dataset import _committed_source
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--attempt', type=int, required=True)
    args = parser.parse_args(argv)
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != PRODUCER_USER:
        raise PermissionError(f'the research producer runs only as {PRODUCER_USER}')
    source = SourceIdentity.model_validate(_committed_source())
    run, _ = read_github_run(args.run_id, args.attempt, admission=True)
    if run.mode != 'GITHUB' or run.operation != RESEARCH_OPERATION or run.head_sha != source.commit_sha:
        raise GithubRunHeld('the research producer runs only in its genuine p3-research-inputs-v1 run')
    read_github_source_ready(run.identity, ROOT)
    request = read_github_research_batch_request(run, source=source)
    roots = research_roots(args.run_id, args.attempt)
    if any(roots['work'].iterdir()) and set(path.name for path in roots['work'].iterdir()) != {
            request.integration_receipt_ref.locator}:
        raise GithubRunHeld('research custody is not fresh for this run')
    proposal = produce(run, request, roots, source=source, transport_factory=transport_factory or PublicArchiveTransport)
    if SourceIdentity.model_validate(_committed_source()) != source:
        raise ValueError('source changed during research production')
    read_github_run(args.run_id, args.attempt, admission=False)  # still attested at the end
    sys.stdout.write(canonical_json_bytes(proposal).decode() + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
