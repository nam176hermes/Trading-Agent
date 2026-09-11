#!/usr/bin/env python3
"""Execute fixed P3 operation inputs; publication remains a SQL-owned commit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.baseline_campaign import ArtifactStore, execute_baseline_manifest, ReadbackStore, _read
from packages.alpha_lifecycle.contracts.execution import BaselineManifest, EvaluationManifest, InputSet, EnvironmentIdentity
from packages.alpha_lifecycle.operation_input import P3OperationInput, BaselinesInput, RegisterFamilyInput, CandidateOOSInput, SelectPrimaryInput
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-ref", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--environment-ref", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--sandbox-policy-digest", required=True)
    parser.add_argument("--logical-trial-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sandbox", type=Path, default=Path("/usr/bin/bwrap"))
    parser.add_argument("--runtime-mounts-ref", type=Path)
    parser.add_argument("--job-id")
    parser.add_argument("--authorization-ref", type=Path)
    args = parser.parse_args()
    store: ArtifactStore = LocalArtifactStore(args.store)
    intent_raw = store.read_bytes(ArtifactRefV1.model_validate_json(args.manifest_ref.read_bytes()))
    intent = P3OperationInput.model_validate_json(intent_raw)
    if canonical_json_bytes(intent) != intent_raw or intent.workflow_operation != args.logical_trial_id:
        raise ValueError('operation intent differs from the fixed workflow operation')
    source = SourceIdentity.model_validate_json(args.source.read_bytes())
    environment = ArtifactRefV1.model_validate_json(args.environment_ref.read_bytes())
    inputs = _read(store,intent.input_set_ref,InputSet)
    environment_identity = _read(store,environment,EnvironmentIdentity)
    if (inputs.source != source
        or inputs.environment_ref != environment
        or environment_identity.sandbox_policy_digest != args.sandbox_policy_digest):
        raise ValueError('operation manifest, InputSet or execution identity differs')
    from packages.alpha_lifecycle.authority import stage_alpha_campaign_payload
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    if not args.job_id or args.authorization_ref is None:
        raise ValueError('operation requires job attribution and retained authorization')
    authorization = _read(store,ArtifactRefV1.model_validate_json(args.authorization_ref.read_bytes()),RunAuthorization)
    stage_alpha_campaign_payload(ReadbackStore(store,store),authorization,source,intent.workflow_operation,
        intent_raw,operation_input=intent)
    args.output.mkdir(mode=0o700,exist_ok=True)
    private_store = args.output/'artifacts'
    private_store.mkdir(mode=0o700,exist_ok=False)
    store = ReplicaArtifactStore(args.store,private_store)
    if isinstance(intent.body,RegisterFamilyInput):
        from services.job_worker.p3_publication_producer import prepare_family_registration
        proposal = prepare_family_registration(intent,job_id=args.job_id,
            observed_at=authorization.issued_at,expires_at=authorization.expires_at,store=store)
        sys.stdout.buffer.write(canonical_json_bytes(proposal) + b"\n")
        return
    if isinstance(intent.body,SelectPrimaryInput):
        from packages.alpha_lifecycle.contracts.authority import FamilyReview
        from packages.alpha_lifecycle.primary_selection import select_primary
        family=_read(store,intent.body.family_review_ref,FamilyReview)
        if family.input_set_ref!=intent.input_set_ref:
            raise ValueError('family review belongs to another InputSet')
        result=select_primary(family,store)
        raw=canonical_json_bytes(result)
        store.put_bytes(raw,media_type='application/json')
        sys.stdout.buffer.write(raw+b"\n")
        return
    if not isinstance(intent.body,(BaselinesInput,CandidateOOSInput)):
        raise RuntimeError('HELD E_OPERATION: official operation executor is not implemented')
    manifest_ref=(intent.body.baseline_manifest_ref if isinstance(intent.body,BaselinesInput)
        else intent.body.evaluation_manifest_ref)
    manifest = _read(store,manifest_ref,BaselineManifest if isinstance(intent.body,BaselinesInput) else EvaluationManifest)
    if manifest.input_set_ref != intent.input_set_ref:
        raise ValueError('operation manifest belongs to another InputSet')
    if isinstance(manifest,EvaluationManifest):
        from packages.alpha_lifecycle.publication import validate_evaluation_registration
        from packages.alpha_lifecycle.lifecycle import read_registry_event
        validate_evaluation_registration(manifest,store=ReadbackStore(store,store))
        if intent.allowed_alpha_ids!=(read_registry_event(store,manifest.candidate_head_ref).record.alpha_id,):
            raise ValueError('OOS operation belongs to another candidate')
    runtime_mounts = ()
    if args.runtime_mounts_ref is not None:
        with args.runtime_mounts_ref.open('rb') as stream:
            raw = stream.read(1048577)
        values = json.loads(raw)
        if (len(raw) > 1048576 or not isinstance(values,list) or len(values) > 8192
            or any(not isinstance(value,str) for value in values)
            or canonical_json_bytes(values) != raw):
            raise ValueError('runtime mounts must be a bounded canonical path list')
        runtime_mounts = tuple(Path(value) for value in values)
    executor = BubblewrapExecutor(
        store=store, store_root=args.store, release_root=args.release, python=args.python,
        source=source, environment_ref=environment,
        sandbox_policy_digest=args.sandbox_policy_digest, bwrap=args.sandbox, runtime_mounts=runtime_mounts,
    )
    if isinstance(intent.body,CandidateOOSInput):
        from packages.alpha_lifecycle.replay import run_replays
        from packages.alpha_lifecycle.contracts.results import EvaluationResult,ReplayReceipt
        from services.job_worker.p3_publication_producer import prepare_candidate_oos
        proof=run_replays(manifest_ref,executor,logical_trial_id=args.logical_trial_id,output_root=args.output)
        receipt=_read(executor,proof.receipt_refs[0],ReplayReceipt)
        evaluation=_read(executor,receipt.result_ref,EvaluationResult)
        result=prepare_candidate_oos(intent,evaluation,proof,job_id=args.job_id,
            observed_at=authorization.issued_at,expires_at=authorization.expires_at,store=executor)
    else:
        result = execute_baseline_manifest(
            manifest_ref, executor,
            logical_trial_id=args.logical_trial_id, output_root=args.output,
        )
    raw_result=canonical_json_bytes(result)
    store.put_bytes(raw_result,media_type="application/json")
    sys.stdout.buffer.write(raw_result + b"\n")


if __name__ == "__main__":
    main()
