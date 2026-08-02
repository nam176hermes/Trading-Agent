"""Approved Package 6 Job API, worker, and durable readback orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg
from psycopg import Connection
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from typing import Mapping

from scripts.validate_package6_runtime_approval import (
    ValidatedPackage6Capability,
    is_issued_capability,
)
from services.job_store.config import read_systemd_credential

from .controller import (
    IncompleteCleanupProof,
    Package6Controller,
    ProcessStatus,
    ReadinessEvidence,
    RuntimeCleanupFailure,
    RuntimeChildAuthorities,
    RuntimeRecoveryState,
    RuntimeStartFailure,
    StopEvidence,
)
from .custodian_client import CustodianClient


@dataclass(frozen=True, slots=True)
class RuntimeChainEvidence:
    processes: dict[str, object]
    readiness: dict[str, object]
    first_request: dict[str, object]
    duplicate_request: dict[str, object]
    api_list: dict[str, object]
    api_detail: dict[str, object]
    database: dict[str, object]
    dashboard_status: dict[str, object]
    worker_stop: dict[str, object]
    job_api_stop: dict[str, object]
    native_publications: dict[str, object]


@dataclass(frozen=True, slots=True)
class RuntimeCleanupSuccess:
    """A component-labeled successful cleanup from an exceptional chain."""

    component: str
    evidence: StopEvidence


@dataclass(frozen=True, slots=True)
class RuntimeRecoverySnapshotFailure:
    """A separately ordered metadata failure that never spends STOP authority."""

    component: str
    operation_id: str
    cleanup_attempts_consumed: int
    error: BaseException


@dataclass(frozen=True, slots=True)
class _RuntimeCleanupResult:
    """Internal successful cleanup together with ordered historical failures."""

    evidence: StopEvidence
    failures: tuple[RuntimeCleanupFailure, ...]

    def __getattr__(self, name: str) -> object:
        """Preserve read compatibility for direct callers of the retry helper."""

        return getattr(self.evidence, name)


@dataclass(frozen=True, slots=True)
class RuntimeRecoveryOwner:
    """Outer-custody owner for controllers with unproven tracked cleanup."""

    controller: Package6Controller
    records: tuple[RuntimeRecoveryState, ...]


class RuntimeChainFailure(RuntimeError):
    """Public-safe aggregate with cleanup evidence and optional recovery custody."""

    PUBLIC_MESSAGE = "paper runtime chain failed safely"

    def __init__(
        self,
        primary_error: BaseException | None,
        cleanup_failures: list[RuntimeCleanupFailure],
        cleanup_successes: list[RuntimeCleanupSuccess] | None = None,
        recovery_owner: RuntimeRecoveryOwner | None = None,
        recovery_snapshot_failures: (
            list[RuntimeRecoverySnapshotFailure] | None
        ) = None,
    ) -> None:
        super().__init__(self.PUBLIC_MESSAGE)
        self.primary_error = primary_error
        self.cleanup_failures = tuple(cleanup_failures)
        self.cleanup_successes = tuple(cleanup_successes or ())
        self.recovery_owner = recovery_owner
        self.recovery_snapshot_failures = tuple(
            recovery_snapshot_failures or ()
        )


class RuntimeCleanupExhausted(RuntimeError):
    """Public-safe exhausted component budget with ordered attempt failures."""

    PUBLIC_MESSAGE = "paper runtime cleanup failed safely"

    def __init__(
        self,
        failures: tuple[RuntimeCleanupFailure, ...],
    ) -> None:
        super().__init__(self.PUBLIC_MESSAGE)
        self.failures = failures


def _request_json(
    method: str,
    url: str,
    *,
    token: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    raw = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if payload is not None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=raw, headers=headers, method=method)
    try:
        with urlopen(request, timeout=2) as response:  # noqa: S310 - approved loopback
            body = response.read(64 * 1024)
            status = response.status
    except HTTPError as error:
        body = error.read(64 * 1024)
        status = error.code
    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise RuntimeError("Job API returned a non-object envelope")
    return status, decoded


def _credential(directory: Path, name: str) -> str:
    return read_systemd_credential({"CREDENTIALS_DIRECTORY": str(directory)}, name)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise RuntimeError(f"{label} is not an object")
    return value


def _mapping_list(value: object, label: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise RuntimeError(f"{label} is not a list")
    return [_mapping(item, f"{label} item") for item in value]


def _worker_exit_error(status: ProcessStatus) -> RuntimeError:
    return RuntimeError(
        "worker exited before terminal job state: "
        f"exit_code={status.exit_code}"
    )


def _database_evidence(
    capability: ValidatedPackage6Capability,
    authorities: RuntimeChildAuthorities,
    job_id: str,
) -> dict[str, object]:
    conninfo = make_conninfo(
        host=_credential(authorities.worker_credentials, "database-host"),
        port=_credential(authorities.worker_credentials, "database-port"),
        dbname=_credential(authorities.worker_credentials, "database-name"),
        user="trading_job_worker",
        password=_credential(authorities.worker_credentials, "database-password"),
        options="-c statement_timeout=5000",
    )
    connection: Connection[dict[str, object]]
    with Connection[dict[str, object]].connect(
        conninfo, row_factory=dict_row
    ) as connection:
        job = connection.execute(
            """
            SELECT job_id, state, attempt_count, reason_code, result_hash,
                   lease_owner, lease_expires_at, cancel_requested_at
            FROM jobs WHERE job_id = %s
            """,
            (job_id,),
        ).fetchone()
        events = connection.execute(
            """
            SELECT sequence, from_state, to_state, reason_code, attempt_id,
                   metadata
            FROM job_events WHERE job_id = %s ORDER BY sequence
            """,
            (job_id,),
        ).fetchall()
        attempts = connection.execute(
            """
            SELECT attempt_id, outcome, claimed_at, started_at, finished_at,
                   exit_code, heartbeat_at, lease_expires_at,
                   termination_reason
            FROM job_attempts WHERE job_id = %s ORDER BY attempt_number
            """,
            (job_id,),
        ).fetchall()
        artifacts = connection.execute(
            """
            SELECT artifact_id, attempt_id, artifact_type, relative_ref,
                   validator_id, sha256, size_bytes, media_type, truncated,
                   validation_metadata
            FROM job_artifacts WHERE job_id = %s ORDER BY created_at, artifact_id
            """,
            (job_id,),
        ).fetchall()
        queue_row = connection.execute(
            "SELECT count(*) AS count FROM jobs WHERE state = 'QUEUED'"
        ).fetchone()
        idempotent_row = connection.execute(
            "SELECT count(*) AS count FROM jobs WHERE idempotency_key = %s",
            (capability.request.idempotency_key,),
        ).fetchone()
        heartbeats = connection.execute(
            """
            SELECT worker_id, status, current_job_id, current_attempt_id,
                   heartbeat_at FROM worker_heartbeats
            WHERE current_job_id = %s OR current_job_id IS NULL
            ORDER BY heartbeat_at
            """,
            (job_id,),
        ).fetchall()
    if job is None:
        raise RuntimeError("durable job row is missing")
    if queue_row is None or type(queue_row.get("count")) is not int:
        raise RuntimeError("durable queue count is invalid")
    if idempotent_row is None or type(idempotent_row.get("count")) is not int:
        raise RuntimeError("durable idempotent count is invalid")
    queue_depth = queue_row["count"]
    idempotent_count = idempotent_row["count"]
    return {
        "job": dict(job),
        "events": [dict(row) for row in events],
        "attempts": [dict(row) for row in attempts],
        "artifacts": [dict(row) for row in artifacts],
        "worker_heartbeats": [dict(row) for row in heartbeats],
        "queue_depth": queue_depth,
        "idempotent_job_count": idempotent_count,
        "postgres_approval_sha256": capability.postgres.approval_sha256,
    }


def _stop_with_retry(
    controller: Package6Controller,
    component: str,
    operation_id: str,
    *,
    prior_failures: tuple[RuntimeCleanupFailure, ...] = (),
) -> _RuntimeCleanupResult:
    """Spend one two-attempt component budget.

    Every failed or incomplete attempt remains part of the result even when a
    later attempt proves cleanup. The caller can therefore expose the complete
    bounded history without assigning recovery ownership to a cleaned process.
    """

    failures = list(prior_failures)
    attempts_consumed = max(
        (failure.attempt for failure in failures),
        default=0,
    )
    for attempt in range(attempts_consumed + 1, 3):
        try:
            evidence = controller.stop(operation_id)
        except BaseException as error:
            failures.append(
                RuntimeCleanupFailure(
                    component,
                    operation_id,
                    attempt,
                    error,
                )
            )
            recover = getattr(controller, "recover_completed_stop", None)
            recovered = None
            if callable(recover):
                try:
                    recovered = recover(operation_id)
                except BaseException as recovery_error:
                    failures.append(
                        RuntimeCleanupFailure(
                            component,
                            operation_id,
                            attempt,
                            recovery_error,
                        )
                    )
                    recovered = None
            if isinstance(recovered, StopEvidence):
                return _RuntimeCleanupResult(recovered, tuple(failures))
            continue
        if evidence.cleanup_proven:
            return _RuntimeCleanupResult(evidence, tuple(failures))
        failures.append(
            RuntimeCleanupFailure(
                component,
                operation_id,
                attempt,
                IncompleteCleanupProof(evidence),
            )
        )
    exhausted = RuntimeCleanupExhausted(tuple(failures))
    if failures:
        raise exhausted from failures[-1].error
    raise exhausted


def run_approved_runtime_chain(
    capability: ValidatedPackage6Capability,
    child_authorities: RuntimeChildAuthorities,
    *,
    custodian_client: CustodianClient,
) -> RuntimeChainEvidence:
    """Run the one approved SNAPSHOT and always clean up in reverse order."""

    if not is_issued_capability(capability):
        raise TypeError("validated Package 6 capability is required")
    controller = Package6Controller(
        capability,
        custodian_client=custodian_client,
        child_authorities=child_authorities,
    )
    api_started = False
    worker_started = False
    worker_stop: StopEvidence | None = None
    api_stop: StopEvidence | None = None
    primary_error: BaseException | None = None
    cleanup_failures: list[RuntimeCleanupFailure] = []
    cleanup_successes: list[RuntimeCleanupSuccess] = []
    recovery_records: list[RuntimeRecoveryState] = []
    recovery_snapshot_failures: list[RuntimeRecoverySnapshotFailure] = []
    recovery_owner_required = False
    consumed_stop_budgets: set[str] = set()
    native_publications: dict[str, object] = {}
    start_cleanup_failures: dict[
        str, tuple[RuntimeCleanupFailure, ...]
    ] = {}
    result: RuntimeChainEvidence | None = None

    def retain_recovery_state(
        component: str,
        operation_id: str,
        cleanup_attempts_consumed: int,
    ) -> None:
        nonlocal recovery_owner_required
        recovery_owner_required = True
        try:
            record = controller.snapshot_recovery_state(
                component,
                operation_id,
                cleanup_attempts_consumed,
            )
        except BaseException as error:
            recovery_snapshot_failures.append(
                RuntimeRecoverySnapshotFailure(
                    component,
                    operation_id,
                    cleanup_attempts_consumed,
                    error,
                )
            )
        else:
            recovery_records.append(record)

    def acknowledge_cleanup_proof(
        component: str,
        operation_id: str,
        evidence: StopEvidence,
        stop_attempt: int,
    ) -> bool:
        """Publish natively, then retry only idempotent publication/ACK."""

        for _ in range(2):
            try:
                receipt = controller.publish_evidence(
                    operation_id, evidence
                )
                native_publications[component] = {
                    "operation_id": receipt.operation.operation_id.hex(),
                    "manifest_sha256": receipt.manifest_sha256.hex(),
                }
                controller.acknowledge_stop(operation_id, evidence)
            except BaseException as error:
                cleanup_failures.append(
                    RuntimeCleanupFailure(
                        component,
                        operation_id,
                        stop_attempt,
                        error,
                    )
                )
            else:
                return True
        return False

    def consume_stop_budget(
        component: str,
        operation_id: str,
    ) -> StopEvidence | None:
        consumed_stop_budgets.add(component)
        prior_failures = start_cleanup_failures.get(component, ())
        try:
            cleanup = _stop_with_retry(
                controller,
                component,
                operation_id,
                prior_failures=prior_failures,
            )
            if isinstance(cleanup, StopEvidence):
                cleanup = _RuntimeCleanupResult(cleanup, ())
            cleanup_failures.extend(cleanup.failures)
            acknowledged = acknowledge_cleanup_proof(
                component,
                operation_id,
                cleanup.evidence,
                max(
                    (failure.attempt for failure in cleanup.failures),
                    default=1,
                ),
            )
            cleanup_successes.append(
                RuntimeCleanupSuccess(component, cleanup.evidence)
            )
            if not acknowledged:
                retain_recovery_state(
                    component,
                    operation_id,
                    max(
                        (
                            failure.attempt
                            for failure in cleanup.failures
                        ),
                        default=1,
                    ),
                )
                return None
            return cleanup.evidence
        except RuntimeCleanupExhausted as error:
            cleanup_failures.extend(error.failures)
            retain_recovery_state(
                component,
                operation_id,
                max(
                    (failure.attempt for failure in error.failures),
                    default=0,
                ),
            )
            return None
        except BaseException as error:
            handoff_failures = list(prior_failures)
            stop_attempt = min(
                RuntimeStartFailure.MAX_CLEANUP_ATTEMPTS,
                max(
                    (failure.attempt for failure in handoff_failures),
                    default=0,
                )
                + 1,
            )
            handoff_failures.append(
                RuntimeCleanupFailure(
                    component,
                    operation_id,
                    stop_attempt,
                    error,
                )
            )
            recover = getattr(controller, "recover_completed_stop", None)
            recovered = None
            if callable(recover):
                for _ in range(2):
                    try:
                        recovered = recover(operation_id)
                    except BaseException as recovery_error:
                        handoff_failures.append(
                            RuntimeCleanupFailure(
                                component,
                                operation_id,
                                stop_attempt,
                                recovery_error,
                            )
                        )
                    else:
                        break
            if isinstance(recovered, StopEvidence):
                cleanup_failures.extend(handoff_failures)
                acknowledged = acknowledge_cleanup_proof(
                    component,
                    operation_id,
                    recovered,
                    stop_attempt,
                )
                cleanup_successes.append(
                    RuntimeCleanupSuccess(component, recovered)
                )
                if not acknowledged:
                    retain_recovery_state(
                        component, operation_id, stop_attempt
                    )
                    return None
                return recovered
            cleanup_failures.extend(handoff_failures)
            retain_recovery_state(
                component,
                operation_id,
                stop_attempt,
            )
            return None

    try:
        try:
            api_identity = controller.start("job-api.start")
        except RuntimeStartFailure as error:
            if error.cleanup_success is not None:
                cleanup_successes.append(
                    RuntimeCleanupSuccess("job_api", error.cleanup_success)
                )
                acknowledged = acknowledge_cleanup_proof(
                    "job_api",
                    "job-api.stop",
                    error.cleanup_success,
                    max(
                        (
                            failure.attempt
                            for failure in error.cleanup_failures
                        ),
                        default=1,
                    ),
                )
                if not acknowledged:
                    consumed_stop_budgets.add("job_api")
                    api_started = True
                    retain_recovery_state(
                        "job_api",
                        "job-api.stop",
                        max(
                            (
                                failure.attempt
                                for failure in error.cleanup_failures
                            ),
                            default=1,
                        ),
                    )
            if error.owns_recovery_state:
                api_started = True
                start_cleanup_failures["job_api"] = error.cleanup_failures
            raise
        api_started = True
        readiness: ReadinessEvidence = controller.wait_ready("job-api.start")
        token = _credential(child_authorities.job_api_credentials, "job-api-token")
        base = f"http://{capability.listener.host}:{capability.listener.port}"
        request_body = {
            "job_type": capability.request.job_type,
            "payload": {"scope": "default", "requested_as_of": None},
            "idempotency_key": capability.request.idempotency_key,
            "priority": 0,
        }
        first_status, first = _request_json(
            "POST", f"{base}/v1/jobs", token=token, payload=request_body
        )
        second_status, second = _request_json(
            "POST", f"{base}/v1/jobs", token=token, payload=request_body
        )
        if first_status != 201 or second_status != 200:
            raise RuntimeError("idempotent Job API request statuses are invalid")
        first_data = _mapping(first.get("data", {}), "first response data")
        second_data = _mapping(second.get("data", {}), "second response data")
        if first_data.get("outcome") != "ENQUEUED" or second_data.get(
            "outcome"
        ) != "DEDUPLICATED":
            raise RuntimeError("idempotent Job API outcomes are invalid")
        first_job = _mapping(first_data.get("job", {}), "first response job")
        second_job = _mapping(second_data.get("job", {}), "second response job")
        job_id = first_job.get("job_id")
        if not isinstance(job_id, str) or second_job.get("job_id") != job_id:
            raise RuntimeError("duplicate request did not resolve to one durable job")
        expected_actor = {
            "actor_type": "OPERATOR",
            "actor_id": "foundation-validation",
        }
        if (
            capability.request.actor != "FOUNDATION_VALIDATION"
            or first_job.get("actor") != expected_actor
            or second_job.get("actor") != expected_actor
        ):
            raise RuntimeError("request actor does not match approved fixture authority")
        try:
            worker_identity = controller.start("worker.start")
        except RuntimeStartFailure as error:
            if error.cleanup_success is not None:
                cleanup_successes.append(
                    RuntimeCleanupSuccess("worker", error.cleanup_success)
                )
                acknowledged = acknowledge_cleanup_proof(
                    "worker",
                    "worker.stop",
                    error.cleanup_success,
                    max(
                        (
                            failure.attempt
                            for failure in error.cleanup_failures
                        ),
                        default=1,
                    ),
                )
                if not acknowledged:
                    consumed_stop_budgets.add("worker")
                    worker_started = True
                    retain_recovery_state(
                        "worker",
                        "worker.stop",
                        max(
                            (
                                failure.attempt
                                for failure in error.cleanup_failures
                            ),
                            default=1,
                        ),
                    )
            if error.owns_recovery_state:
                worker_started = True
                start_cleanup_failures["worker"] = error.cleanup_failures
            raise
        worker_started = True
        deadline = time.monotonic() + capability.operation_timeout_seconds
        detail: dict[str, object] = {}
        while time.monotonic() < deadline:
            worker_status = controller.status("worker.start")
            if worker_status.exit_code is not None:
                raise _worker_exit_error(worker_status)
            status, detail = _request_json(
                "GET", f"{base}/v1/jobs/{job_id}", token=token
            )
            detail_data = _mapping(detail.get("data", {}), "detail response data")
            detail_job = _mapping(detail_data.get("job", {}), "detail response job")
            state = detail_job.get("state")
            if status == 200 and state in {
                "SUCCEEDED",
                "FAILED",
                "BLOCKED",
                "CANCELLED",
            }:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("bounded worker did not reach a terminal state")
        list_status, listed = _request_json("GET", f"{base}/v1/jobs", token=token)
        if list_status != 200:
            raise RuntimeError("Job API list readback failed")
        database = _database_evidence(capability, child_authorities, job_id)
        durable_job = _mapping(database["job"], "durable job")
        detail_data = _mapping(detail["data"], "detail response data")
        api_job = _mapping(detail_data["job"], "detail response job")
        if (
            database["idempotent_job_count"] != capability.request.expected_job_count
            or durable_job["state"] != api_job["state"]
            or durable_job["result_hash"] != api_job["result_hash"]
            or durable_job["lease_owner"] is not None
            or durable_job["lease_expires_at"] is not None
            or durable_job["cancel_requested_at"] is not None
        ):
            raise RuntimeError("API and durable readback do not match")
        listed_data = _mapping(listed.get("data", {}), "list response data")
        listed_jobs = _mapping_list(listed_data.get("items", []), "listed jobs")
        if len([item for item in listed_jobs if item.get("job_id") == job_id]) != 1:
            raise RuntimeError("Job API list readback does not contain the durable job")
        dashboard_status = {
            key: api_job[key]
            for key in ("job_id", "state", "attempt_count", "reason_code", "result_hash")
        }
        worker_stop = consume_stop_budget("worker", "worker.stop")
        if worker_stop is not None:
            worker_started = False
        api_stop = consume_stop_budget("job_api", "job-api.stop")
        if api_stop is not None:
            api_started = False
        if worker_stop is not None and api_stop is not None:
            result = RuntimeChainEvidence(
                processes={
                    "job_api": asdict(api_identity),
                    "worker": asdict(worker_identity),
                },
                readiness=asdict(readiness),
                first_request={"status": first_status, "body": first},
                duplicate_request={"status": second_status, "body": second},
                api_list=listed,
                api_detail=detail,
                database=database,
                dashboard_status=dashboard_status,
                worker_stop=asdict(worker_stop),
                job_api_stop=asdict(api_stop),
                native_publications=dict(native_publications),
            )
    except BaseException as error:
        primary_error = error
    finally:
        if worker_started and "worker" not in consumed_stop_budgets:
            try:
                worker_cleanup = consume_stop_budget(
                    "worker", "worker.stop"
                )
            except BaseException as error:
                cleanup_failures.append(
                    RuntimeCleanupFailure(
                        "worker",
                        "worker.stop",
                        len(start_cleanup_failures.get("worker", ())) + 1,
                        error,
                    )
                )
                retain_recovery_state(
                    "worker",
                    "worker.stop",
                    min(
                        RuntimeStartFailure.MAX_CLEANUP_ATTEMPTS,
                        len(start_cleanup_failures.get("worker", ())) + 1,
                    ),
                )
            else:
                if worker_cleanup is not None:
                    worker_started = False
        if api_started and "job_api" not in consumed_stop_budgets:
            try:
                api_cleanup = consume_stop_budget(
                    "job_api", "job-api.stop"
                )
            except BaseException as error:
                cleanup_failures.append(
                    RuntimeCleanupFailure(
                        "job_api",
                        "job-api.stop",
                        len(start_cleanup_failures.get("job_api", ())) + 1,
                        error,
                    )
                )
                retain_recovery_state(
                    "job_api",
                    "job-api.stop",
                    min(
                        RuntimeStartFailure.MAX_CLEANUP_ATTEMPTS,
                        len(start_cleanup_failures.get("job_api", ())) + 1,
                    ),
                )
            else:
                if api_cleanup is not None:
                    api_started = False
    if primary_error is not None or cleanup_failures:
        recovery_records.sort(
            key=lambda record: (
                0 if record.component == "worker" else 1,
                record.operation_id,
            )
        )
        recovery_owner = (
            RuntimeRecoveryOwner(controller, tuple(recovery_records))
            if recovery_owner_required
            else None
        )
        failure = RuntimeChainFailure(
            primary_error,
            cleanup_failures,
            cleanup_successes,
            recovery_owner,
            recovery_snapshot_failures,
        )
        if primary_error is not None:
            raise failure from primary_error
        raise failure
    if result is None:
        raise RuntimeChainFailure(None, [])
    return result


__all__ = [
    "RuntimeChainEvidence",
    "RuntimeChainFailure",
    "RuntimeCleanupSuccess",
    "RuntimeCleanupExhausted",
    "RuntimeCleanupFailure",
    "RuntimeRecoveryOwner",
    "RuntimeRecoverySnapshotFailure",
    "run_approved_runtime_chain",
]
