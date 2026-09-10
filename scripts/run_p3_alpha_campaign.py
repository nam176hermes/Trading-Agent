#!/usr/bin/env python3
"""Execute fixed P3 operation inputs; publication remains a SQL-owned commit."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.baseline_campaign import execute_baseline_manifest, _read
from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet, EnvironmentIdentity
from packages.alpha_lifecycle.operation_input import P3OperationInput, BaselinesInput, RegisterFamilyInput
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
from packages.data_catalog.artifact_store import LocalArtifactStore
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
    parser.add_argument("--job-id")
    parser.add_argument("--authorization-ref", type=Path)
    args = parser.parse_args()
    store = LocalArtifactStore(args.store)
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
    stage_alpha_campaign_payload(store,authorization,source,intent.workflow_operation,
        intent_raw,operation_input=intent)
    if isinstance(intent.body,RegisterFamilyInput):
        from services.job_worker.p3_publication_producer import prepare_family_registration
        proposal = prepare_family_registration(intent,job_id=args.job_id,
            observed_at=authorization.issued_at,expires_at=authorization.expires_at,store=store)
        sys.stdout.buffer.write(canonical_json_bytes(proposal) + b"\n")
        return
    if not isinstance(intent.body,BaselinesInput):
        raise RuntimeError('HELD E_OPERATION: official operation executor is not implemented')
    manifest = _read(store,intent.body.baseline_manifest_ref,BaselineManifest)
    if manifest.input_set_ref != intent.input_set_ref:
        raise ValueError('operation manifest belongs to another InputSet')
    executor = BubblewrapExecutor(
        store=store, store_root=args.store, release_root=args.release, python=args.python,
        source=source, environment_ref=environment,
        sandbox_policy_digest=args.sandbox_policy_digest,
    )
    result = execute_baseline_manifest(
        intent.body.baseline_manifest_ref, executor,
        logical_trial_id=args.logical_trial_id, output_root=args.output,
    )
    sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")


if __name__ == "__main__":
    main()
