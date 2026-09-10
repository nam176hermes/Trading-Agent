"""Fixed P3 authority preflight; this script never grants authority to itself."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.authority import (
    build_alpha_campaign_payload,
    stage_alpha_campaign_payload,
    validate_request,
    validate_workflow_operation,
)
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import AlphaCampaignPayload, EnqueueJobBody
from packages.pre_p3_provenance import canonical_source_identity
from packages.project_status import derive_project_status
from apps.job_api.contracts import (
    JobDeduplicatedEnvelope, JobDetailEnvelope, JobEnqueuedEnvelope,
)


def _write(directory: Path, name: str, value: object) -> None:
    descriptor = os.open(directory / name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC, 0o600)
    try:
        os.write(descriptor, canonical_json_bytes(value) + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


JOB_API = "http://127.0.0.1:8401"
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED"}
MAX_RESPONSE_BYTES = 1_048_576
_TOKEN = re.compile(r"[!-~]{1,4096}", re.ASCII)


def build_enqueue_body(payload: AlphaCampaignPayload, nonce: object) -> EnqueueJobBody:
    return EnqueueJobBody(
        job_type="ALPHA_CAMPAIGN", payload=payload,
        idempotency_key=f"p3:{payload.logical_trial_id}:{nonce}", priority=0,
    )


def _read_token(path: Path) -> str:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (
            not path.is_absolute() or not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}
            or info.st_nlink != 1 or not 1 <= info.st_size <= 4096
        ):
            raise RuntimeError("HELD E_JOB_API_AUTHORITY: token file is unsafe")
        raw = os.read(descriptor, info.st_size + 1)
        token = raw.removesuffix(b"\n").decode("ascii")
        if len(raw) != info.st_size or _TOKEN.fullmatch(token) is None:
            raise RuntimeError("HELD E_JOB_API_AUTHORITY: token is invalid")
        return token
    except OSError as error:
        raise RuntimeError("HELD E_JOB_API_AUTHORITY: token file is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _request_json(method: str, path: str, token: str, body: object | None = None) -> dict[str, object]:
    request = Request(
        JOB_API + path, method=method,
        data=None if body is None else canonical_json_bytes(body),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError("HELD E_JOB_API: loopback Job API request failed") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("HELD E_JOB_API: response exceeded 1 MiB")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("HELD E_JOB_API: response contract is invalid")
    return value


def preflight(request_file: Path, output_dir: Path, workflow_operation: str):
    operation = validate_workflow_operation(workflow_operation)
    source = SourceIdentity.model_validate(canonical_source_identity(ROOT))
    request = validate_request(request_file, source, operation)
    authorization = request.authorization
    payload = build_alpha_campaign_payload(authorization, source, workflow_operation, operation_input=request.operation_input)
    status = derive_project_status(ROOT)
    if not (
        status["gates"]["HWC_SOURCE_READY"] == "PASS"
        and status["gates"]["PRE_P3_READY"] == "PASS"
        and status["p3_alpha_development_allowed"] is True
    ):
        raise RuntimeError("HELD E_SOURCE_READY: protected source gates are not current")
    output_dir.mkdir(mode=0o700,parents=False,exist_ok=False)
    _write(output_dir,"source-identity.json",source)
    _write(output_dir,"input-inventory.json",{
        "authorization_ref":payload.authorization_ref,
        ("fixture_plan_ref" if workflow_operation == "p3-integration-fixture-v1" else "operation_input_ref"):payload.manifest_ref,
        "review_ref":authorization.review_ref,
    })
    _write(output_dir,"preflight.json",{
        "schema_version":"p3-preflight-v1","operation":operation,
        "source":source,"status":"STRUCTURE_VALIDATED","execution_authorized":False,
        "authority":{"broker":False,"live":False,"network":False,"production":False},
    })
    _write(output_dir,"payload.json",payload)
    return request, payload


def dispatch(request_file: Path, token_file: Path, output_dir: Path, workflow_operation: str, *, artifact_root: Path, manifest_file: Path, review_file: Path) -> None:
    request, payload = preflight(request_file, output_dir, workflow_operation)
    authorization = request.authorization
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from services.job_worker.p3_integration import _read_review, _read_authority_bytes, FixturePlan
    store = LocalArtifactStore(artifact_root)
    review = _read_review(review_file,authorization.review_ref)
    store.read_bytes(review.evidence_ref)
    if store.put_bytes(canonical_json_bytes(review),media_type="application/json") != authorization.review_ref:
        raise RuntimeError("HELD E_REVIEW_AUTHORITY: review CAS binding differs")
    manifest = _read_authority_bytes(manifest_file)
    if workflow_operation == "p3-integration-fixture-v1":
        plan = FixturePlan.model_validate_json(manifest)
        if plan.source != payload.expected_source or canonical_json_bytes(plan) != manifest:
            raise RuntimeError("HELD E_MANIFEST: fixture plan differs from source")
    payload = stage_alpha_campaign_payload(store,authorization,payload.expected_source,workflow_operation,manifest, operation_input=request.operation_input)
    token = _read_token(token_file)
    enqueue_raw = _request_json(
        "POST", "/v1/jobs", token,
        build_enqueue_body(payload, authorization.nonce).model_dump(mode="json"),
    )
    try:
        outcome = enqueue_raw["data"]["outcome"]  # type: ignore[index]
        envelope = (
            JobEnqueuedEnvelope if outcome == "ENQUEUED" else JobDeduplicatedEnvelope
        ).model_validate(enqueue_raw)
        job_id = envelope.data.job.job_id
    except Exception as error:
        raise RuntimeError("HELD E_JOB_API: enqueue response contract is invalid") from error
    _write(output_dir, "enqueue-response.json", enqueue_raw)
    deadline = time.monotonic() + 900
    while True:
        detail_raw = _request_json("GET", f"/v1/jobs/{job_id}", token)
        try:
            detail = JobDetailEnvelope.model_validate(detail_raw)
            state = detail.data.job.state.value
        except Exception as error:
            raise RuntimeError("HELD E_JOB_API: detail response contract is invalid") from error
        if state in TERMINAL_STATES:
            _write(output_dir, "job-result.json", detail)
            if state != "SUCCEEDED":
                raise RuntimeError(f"HELD E_P3_JOB: terminal state {state}")
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("HELD E_P3_JOB: bounded wait expired")
        time.sleep(1)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("preflight","prepare-inputs","dispatch"))
    parser.add_argument("--request-file",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--operation",required=True)
    parser.add_argument("--token-file",type=Path)
    parser.add_argument("--artifact-root",type=Path)
    parser.add_argument("--manifest-file",type=Path)
    parser.add_argument("--review-file",type=Path)
    args=parser.parse_args()
    if args.command == "dispatch":
        if any(value is None for value in (args.token_file,args.artifact_root,args.manifest_file,args.review_file)):
            parser.error("dispatch requires token, artifact-root, manifest-file and review-file")
        dispatch(args.request_file,args.token_file,args.output_dir,args.operation,
                 artifact_root=args.artifact_root,manifest_file=args.manifest_file,review_file=args.review_file)
    else:
        preflight(args.request_file,args.output_dir,args.operation)
