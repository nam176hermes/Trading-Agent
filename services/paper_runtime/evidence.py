"""Immutable final Package 6 evidence bundle and strict verifier."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import time
from uuid import uuid4
import weakref
from typing import Mapping

from scripts.validate_package6_runtime_approval import (
    ValidatedPackage6Capability,
    is_issued_capability,
    package6_authority_digests,
)

from .controller import EvidenceIncomplete, RuntimeChildAuthorities
from .integration import RuntimeChainEvidence


VERDICT = "PENDING_CONTROLLER_RUNTIME_VERIFICATION"
EVIDENCE_DOCUMENTS = (
    "docs/implementation/foundation-paper-runtime-dashboard.md",
    "docs/implementation/foundation-paper-runtime-db.md",
    "docs/implementation/foundation-paper-runtime-final.md",
    "docs/implementation/foundation-paper-runtime-request.md",
    "docs/implementation/foundation-paper-runtime-result.md",
    "docs/implementation/foundation-paper-runtime-rollback.md",
    "docs/implementation/foundation-paper-runtime-worker.md",
)
_SHA256 = frozenset("0123456789abcdef")
_BASE_CHILD_ENVIRONMENT_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        "TRADING_MODE",
        "LIVE_EXECUTION_ENABLED",
        "LIVE_TRADING_APPROVED",
        "LIVE_TRADING_ENABLED",
        "CREDENTIALS_DIRECTORY",
        "TRADING_PACKAGE6_STAGING_SCOPE",
        "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH",
        "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH",
        "TRADING_PACKAGE6_APPROVAL_SHA256",
    }
)


def child_environment_key_sets() -> dict[str, tuple[str, ...]]:
    """Return the code-owned allowlist for both runtime children."""

    return {
        "job_api": tuple(sorted(_BASE_CHILD_ENVIRONMENT_KEYS)),
        "worker": tuple(
            sorted(
                {
                    *_BASE_CHILD_ENVIRONMENT_KEYS,
                    "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH",
                }
            )
        ),
    }


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False, weakref_slot=True)
class PostgresCleanupEvidence:
    approval_sha256: str
    listener_alive: bool
    listener_negative_probes: int
    process_alive: bool
    process_group_alive: bool
    process_pid: int
    process_group: int
    start_ticks: int
    exit_code: int
    pgdata_exists: bool
    cleanup_complete: bool


_ISSUED_CLEANUP: weakref.WeakSet[PostgresCleanupEvidence] = weakref.WeakSet()


def issue_postgres_cleanup_evidence(
    capability: ValidatedPackage6Capability,
    document: dict[str, object],
) -> PostgresCleanupEvidence:
    if not is_issued_capability(capability):
        raise TypeError("validated Package 6 capability is required")
    fields = {
        "approval_sha256",
        "listener_alive",
        "listener_negative_probes",
        "process_alive",
        "process_group_alive",
        "process_pid",
        "process_group",
        "start_ticks",
        "exit_code",
        "pgdata_exists",
        "cleanup_complete",
    }
    if not isinstance(document, dict) or set(document) != fields:
        raise EvidenceIncomplete("PostgreSQL cleanup evidence fields are invalid")
    if (
        document["approval_sha256"] != capability.postgres.approval_sha256
        or document["listener_alive"] is not False
        or type(document["listener_negative_probes"]) is not int
        or document["listener_negative_probes"] < 3
        or document["process_alive"] is not False
        or document["process_group_alive"] is not False
        or type(document["process_pid"]) is not int
        or document["process_pid"] < 2
        or type(document["process_group"]) is not int
        or document["process_group"] < 2
        or type(document["start_ticks"]) is not int
        or document["start_ticks"] < 1
        or document["exit_code"] != 0
        or document["pgdata_exists"] is not False
        or document["cleanup_complete"] is not True
    ):
        raise EvidenceIncomplete("PostgreSQL cleanup is incomplete")
    value = PostgresCleanupEvidence()
    for name in fields:
        object.__setattr__(value, name, document[name])
    _ISSUED_CLEANUP.add(value)
    return value


def request_and_wait_for_postgres_cleanup(
    capability: ValidatedPackage6Capability,
) -> PostgresCleanupEvidence:
    """Handshake with the separately approved PostgreSQL lifecycle controller."""

    if not is_issued_capability(capability):
        raise TypeError("validated Package 6 capability is required")
    root = capability.evidence_root
    request = _canonical(
        {
            "package6_approval_sha256": capability.approval_sha256,
            "postgres_approval_sha256": capability.postgres.approval_sha256,
            "action": "STOP_AND_REMOVE_DISPOSABLE_POSTGRES",
        }
    )
    _write_files(root, {"postgres-cleanup-request.json": request})
    deadline = time.monotonic() + capability.cleanup_timeout_seconds
    while time.monotonic() < deadline:
        try:
            raw = _safe_read(root, "postgres-cleanup-evidence.json")
        except EvidenceIncomplete:
            time.sleep(0.05)
            continue
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as error:
            raise EvidenceIncomplete("PostgreSQL cleanup JSON is invalid") from error
        return issue_postgres_cleanup_evidence(capability, document)
    raise EvidenceIncomplete("PostgreSQL cleanup controller did not complete")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise EvidenceIncomplete(f"{label} is not an object")
    return value


def _mapping_list(value: object, label: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise EvidenceIncomplete(f"{label} is not a list")
    return [_mapping(item, f"{label} item") for item in value]


def _safe_read(
    root: Path,
    relative: str,
    *,
    maximum: int = 2 * 1024 * 1024,
    strict_root: bool = True,
) -> bytes:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != relative:
        raise EvidenceIncomplete("evidence source path is invalid")
    root_fd = os.open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    descriptor = -1
    try:
        root_info = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.geteuid()
            or (
                strict_root
                and stat.S_IMODE(root_info.st_mode) != 0o700
            )
            or (
                not strict_root
                and root_info.st_mode & 0o022
            )
        ):
            raise EvidenceIncomplete("evidence directory policy is invalid")
        descriptor = os.open(
            relative,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=root_fd,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o022
            or info.st_size > maximum
        ):
            raise EvidenceIncomplete("evidence source file policy is invalid")
        raw = os.read(descriptor, maximum + 1)
        if len(raw) != info.st_size:
            raise EvidenceIncomplete("evidence source file changed while reading")
        return raw
    except OSError as error:
        raise EvidenceIncomplete("evidence source file is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_fd)


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    root_fd = os.open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        for name, raw in files.items():
            descriptor = os.open(
                name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | os.O_CLOEXEC,
                0o600,
                dir_fd=root_fd,
            )
            try:
                view = memoryview(raw)
                while view:
                    view = view[os.write(descriptor, view) :]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        os.close(root_fd)


def write_runtime_evidence_bundle(
    capability: ValidatedPackage6Capability,
    child_authorities: RuntimeChildAuthorities,
    chain: RuntimeChainEvidence,
    cleanup: PostgresCleanupEvidence,
    *,
    source_root: Path,
    approval_bytes: bytes,
    postgres_approval_bytes: bytes,
) -> Path:
    if not is_issued_capability(capability) or cleanup not in _ISSUED_CLEANUP:
        raise TypeError("issued runtime and cleanup capabilities are required")
    documents: dict[str, bytes] = {}
    for relative in EVIDENCE_DOCUMENTS:
        raw = _safe_read(source_root, relative, strict_root=False)
        expected = dict(capability.source_bindings).get(relative)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise EvidenceIncomplete("evidence document does not match source binding")
        documents[Path(relative).name] = raw
    detail = _mapping(chain.api_detail["data"], "API detail data")
    job = _mapping(detail["job"], "API detail job")
    events = _mapping_list(chain.database["events"], "database events")
    attempts = _mapping_list(chain.database["attempts"], "database attempts")
    artifacts = _mapping_list(chain.database["artifacts"], "database artifacts")
    sequences = [item["sequence"] for item in events]
    result_artifacts = [
        item for item in artifacts if item["artifact_type"] == "RESULT"
    ]
    if (
        job["state"] != "SUCCEEDED"
        or chain.database["idempotent_job_count"] != 1
        or chain.database["queue_depth"] != 0
        or not events
        or sequences != list(range(1, len(sequences) + 1))
        or len(sequences) != len(set(sequences))
        or not attempts
        or not chain.database["worker_heartbeats"]
        or len(result_artifacts) != 1
        or result_artifacts[0]["truncated"] is not False
        or result_artifacts[0]["sha256"] != job["result_hash"]
        or not result_artifacts[0]["validation_metadata"]
        or _mapping(
            result_artifacts[0]["validation_metadata"],
            "result validation metadata",
        ).get(
            "market_data_provenance"
        )
        != "DETERMINISTIC_PROVIDER_FREE_V1"
        or _mapping(
            result_artifacts[0]["validation_metadata"],
            "result validation metadata",
        ).get("fixture_sha256")
        != capability.fixture.sha256
        or not chain.worker_stop["cleanup_proven"]
        or not chain.job_api_stop["cleanup_proven"]
    ):
        raise EvidenceIncomplete("runtime chain evidence is incomplete or inconsistent")
    interpreter_path = Path(capability.operations["job-api.start"].argv[0])
    interpreter_info = interpreter_path.stat(follow_symlinks=False)
    interpreter_sha256 = hashlib.sha256(interpreter_path.read_bytes()).hexdigest()
    if (
        not stat.S_ISREG(interpreter_info.st_mode)
        or interpreter_sha256
        != capability.operations["job-api.start"].executable_sha256
    ):
        raise EvidenceIncomplete("interpreter identity changed before evidence sealing")
    terminal_metadata = _mapping(
        events[-1].get("metadata", {}), "terminal event metadata"
    )
    terminal_lineage = _mapping(
        terminal_metadata.get("lineage", {}), "terminal lineage"
    )
    command_lineage = _mapping(
        terminal_lineage.get("command", {}), "command lineage"
    )
    safety = _mapping(terminal_lineage.get("safety", {}), "safety lineage")
    safety_lineage = _mapping(safety.get("final", {}), "final safety lineage")
    approved_authorities = dict(
        package6_authority_digests(
            capability.staging_material, capability.fixture_material
        )
    )
    if approved_authorities != dict(capability.authority_digests):
        raise EvidenceIncomplete("observed runtime authority digests do not match approval")
    runtime = {
        "schema_version": 2,
        "verdict": VERDICT,
        "approval": {
            "sha256": capability.approval_sha256,
            "postgres_approval_sha256": capability.postgres.approval_sha256,
        },
        "source": {
            "commit": capability.source_commit,
            "tree": capability.source_tree,
            "bindings": list(capability.source_bindings),
        },
        "authority_digests": approved_authorities,
        "interpreter": {
            "argv0": str(interpreter_path),
            "sha256": interpreter_sha256,
            "device": interpreter_info.st_dev,
            "inode": interpreter_info.st_ino,
            "mode": stat.S_IMODE(interpreter_info.st_mode),
        },
        "operations": {
            key: {
                "argv": list(value.argv),
                "cwd": str(value.cwd),
                "host": value.bind_host,
                "port": value.port,
                "executable_sha256": value.executable_sha256,
            }
            for key, value in capability.operations.items()
        },
        "child_environments": {
            component: _mapping(
                _mapping(process, f"{component} process").get("environment"),
                f"{component} process environment",
            )
            for component, process in _mapping(
                chain.processes, "runtime processes"
            ).items()
        },
        "chain": asdict(chain),
        "postgres_cleanup": asdict(cleanup),
        "document_sha256": {
            name: hashlib.sha256(raw).hexdigest()
            for name, raw in sorted(documents.items())
        },
    }
    files = {
        "approval.json": approval_bytes,
        "postgres-approval.json": postgres_approval_bytes,
        "runtime.json": _canonical(runtime),
        **documents,
    }
    evidence_root = capability.evidence_root
    try:
        root_info = evidence_root.lstat()
        resolved_root = evidence_root.resolve(strict=True)
    except OSError as error:
        raise EvidenceIncomplete("pre-created evidence root is unavailable") from error
    if (
        resolved_root != evidence_root
        or stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) != 0o700
    ):
        raise EvidenceIncomplete("evidence root policy is invalid")
    bundle = evidence_root / f"package6-{uuid4().hex}"
    root_fd = os.open(
        evidence_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        os.mkdir(bundle.name, 0o700, dir_fd=root_fd)
    finally:
        os.close(root_fd)
    entries = [
        {
            "path": name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
        for name, raw in sorted(files.items())
    ]
    files["index.json"] = _canonical(
        {"schema_version": 2, "verdict": VERDICT, "entries": entries}
    )
    _write_files(bundle, files)
    verify_runtime_evidence_bundle(bundle)
    return bundle


def verify_runtime_evidence_bundle(root: Path) -> bool:
    index = json.loads(_safe_read(root, "index.json"))
    if (
        not isinstance(index, dict)
        or set(index) != {"schema_version", "verdict", "entries"}
        or index["schema_version"] != 2
        or index["verdict"] != VERDICT
        or not isinstance(index["entries"], list)
    ):
        raise EvidenceIncomplete("runtime evidence index schema is invalid")
    names = {entry.get("path") for entry in index["entries"] if isinstance(entry, dict)}
    required = {
        "approval.json",
        "postgres-approval.json",
        "runtime.json",
        *(Path(path).name for path in EVIDENCE_DOCUMENTS),
    }
    if names != required or len(names) != len(index["entries"]):
        raise EvidenceIncomplete("runtime evidence manifest is incomplete")
    for entry in index["entries"]:
        if set(entry) != {"path", "sha256", "size_bytes"}:
            raise EvidenceIncomplete("runtime evidence entry fields are invalid")
        raw = _safe_read(root, entry["path"])
        if (
            len(raw) != entry["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != entry["sha256"]
        ):
            raise EvidenceIncomplete("runtime evidence digest or size does not match")
    runtime = json.loads(_safe_read(root, "runtime.json"))
    expected_fields = {
        "schema_version",
        "verdict",
        "approval",
        "source",
        "authority_digests",
        "interpreter",
        "operations",
        "child_environments",
        "chain",
        "postgres_cleanup",
        "document_sha256",
    }
    chain_fields = {
        "processes",
        "readiness",
        "first_request",
        "duplicate_request",
        "api_list",
        "api_detail",
        "database",
        "dashboard_status",
        "worker_stop",
        "job_api_stop",
    }
    database_fields = {
        "job",
        "events",
        "attempts",
        "artifacts",
        "worker_heartbeats",
        "queue_depth",
        "idempotent_job_count",
        "postgres_approval_sha256",
    }
    if (
        not isinstance(runtime, dict)
        or set(runtime) != expected_fields
        or runtime["schema_version"] != 2
        or runtime["verdict"] != VERDICT
        or set(runtime["authority_digests"])
        != {"release", "application", "backend", "command", "semantic", "fixture", "safety"}
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or not set(value) <= _SHA256
            for value in runtime["authority_digests"].values()
        )
        or set(runtime["chain"]) != chain_fields
        or set(runtime["chain"]["database"]) != database_fields
        or set(runtime["interpreter"])
        != {"argv0", "sha256", "device", "inode", "mode"}
        or runtime["operations"]["job-api.start"]["executable_sha256"]
        != runtime["interpreter"]["sha256"]
        or hashlib.sha256(_safe_read(root, "approval.json")).hexdigest()
        != runtime["approval"]["sha256"]
        or hashlib.sha256(_safe_read(root, "postgres-approval.json")).hexdigest()
        != runtime["approval"]["postgres_approval_sha256"]
        or runtime["approval"]["postgres_approval_sha256"]
        != runtime["chain"]["database"]["postgres_approval_sha256"]
        or runtime["postgres_cleanup"]["cleanup_complete"] is not True
        or runtime["postgres_cleanup"]["listener_alive"] is not False
        or runtime["postgres_cleanup"]["process_alive"] is not False
        or runtime["postgres_cleanup"]["process_group_alive"] is not False
        or runtime["postgres_cleanup"]["listener_negative_probes"] < 3
        or runtime["postgres_cleanup"]["start_ticks"] < 1
        or runtime["postgres_cleanup"]["exit_code"] != 0
        or runtime["postgres_cleanup"]["pgdata_exists"] is not False
        or runtime["chain"]["database"]["idempotent_job_count"] != 1
        or runtime["chain"]["database"]["queue_depth"] != 0
        or runtime["chain"]["worker_stop"]["cleanup_proven"] is not True
        or runtime["chain"]["job_api_stop"]["cleanup_proven"] is not True
        or runtime["chain"]["processes"]["job_api"]["pid"]
        != runtime["chain"]["job_api_stop"]["pid"]
        or runtime["chain"]["processes"]["job_api"]["start_ticks"]
        != runtime["chain"]["job_api_stop"]["start_ticks"]
        or runtime["chain"]["processes"]["worker"]["pid"]
        != runtime["chain"]["worker_stop"]["pid"]
        or runtime["chain"]["processes"]["worker"]["start_ticks"]
        != runtime["chain"]["worker_stop"]["start_ticks"]
        or runtime["chain"]["readiness"]["pid"]
        != runtime["chain"]["processes"]["job_api"]["pid"]
        or runtime["chain"]["readiness"]["status"] != "READY"
        or runtime["chain"]["readiness"]["listener_inode"] < 1
    ):
        raise EvidenceIncomplete("runtime evidence authority or cleanup is invalid")
    result_artifacts = [
        artifact
        for artifact in runtime["chain"]["database"]["artifacts"]
        if artifact.get("artifact_type") == "RESULT"
    ]
    events = runtime["chain"]["database"]["events"]
    sequences = [event.get("sequence") for event in events]
    job = runtime["chain"]["database"]["job"]
    detail_job = runtime["chain"]["api_detail"]["data"]["job"]
    first_job = runtime["chain"]["first_request"]["body"]["data"]["job"]
    duplicate_job = runtime["chain"]["duplicate_request"]["body"]["data"]["job"]
    if not events:
        raise EvidenceIncomplete("runtime event history is missing")
    terminal_lineage = events[-1].get("metadata", {}).get("lineage", {})
    command_lineage = terminal_lineage.get("command", {})
    safety_lineage = terminal_lineage.get("safety", {}).get("final", {})
    approval = json.loads(_safe_read(root, "approval.json"))
    expected_environment = child_environment_key_sets()
    observed_environments = runtime["child_environments"]
    process_components = {"job_api": "JOB_API", "worker": "WORKER"}
    environment_valid = (
        isinstance(observed_environments, dict)
        and set(observed_environments) == set(process_components)
    )
    if environment_valid:
        for component, component_name in process_components.items():
            observed = observed_environments[component]
            process = runtime["chain"]["processes"][component]
            environment_valid = (
                isinstance(observed, dict)
                and set(observed)
                == {
                    "component", "operation_id", "pid", "process_group",
                    "start_ticks", "keys",
                }
                and observed["component"] == component_name
                and observed["operation_id"] == f"{component.replace('_', '-')}.start"
                and observed["keys"] == list(expected_environment[component])
                and all(
                    observed[field] == process[field]
                    for field in (
                        "operation_id", "pid", "process_group", "start_ticks"
                    )
                )
                and process.get("environment") == observed
            )
            if not environment_valid:
                break
    if (
        len(result_artifacts) != 1
        or result_artifacts[0].get("truncated") is not False
        or not result_artifacts[0].get("relative_ref")
        or not result_artifacts[0].get("validator_id")
        or not result_artifacts[0].get("validation_metadata")
        or result_artifacts[0]["validation_metadata"].get(
            "market_data_provenance"
        )
        != "DETERMINISTIC_PROVIDER_FREE_V1"
        or result_artifacts[0]["validation_metadata"].get("fixture_sha256")
        != runtime["authority_digests"]["fixture"]
        or result_artifacts[0].get("sha256") != job.get("result_hash")
        or job.get("state") != "SUCCEEDED"
        or detail_job.get("state") != job.get("state")
        or len(
            {
                first_job.get("job_id"),
                duplicate_job.get("job_id"),
                detail_job.get("job_id"),
                job.get("job_id"),
            }
        )
        != 1
        or sequences != list(range(1, len(sequences) + 1))
        or len(sequences) != len(set(sequences))
        or not runtime["chain"]["database"]["attempts"]
        or not runtime["chain"]["database"]["worker_heartbeats"]
        or [event.get("to_state") for event in events]
        != ["QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED"]
        or any(
            attempt.get("claimed_at") is None
            or attempt.get("started_at") is None
            or attempt.get("heartbeat_at") is None
            or attempt.get("finished_at") is None
            or attempt.get("lease_expires_at") is None
            or attempt.get("outcome") != "SUCCEEDED"
            or attempt.get("termination_reason") is not None
            for attempt in runtime["chain"]["database"]["attempts"]
        )
        or job.get("lease_owner") is not None
        or job.get("lease_expires_at") is not None
        or job.get("cancel_requested_at") is not None
        or len(
            [
                item
                for item in runtime["chain"]["api_list"]["data"]["items"]
                if item.get("job_id") == job.get("job_id")
            ]
        )
        != 1
        or runtime["chain"]["dashboard_status"]
        != {
            key: detail_job[key]
            for key in ("job_id", "state", "attempt_count", "reason_code", "result_hash")
        }
        or runtime["authority_digests"] != approval.get("authority_digests")
        or not environment_valid
    ):
        raise EvidenceIncomplete("runtime state, event, or sealed result proof is invalid")
    for name, digest in runtime["document_sha256"].items():
        if hashlib.sha256(_safe_read(root, name)).hexdigest() != digest:
            raise EvidenceIncomplete("evidence document digest does not match")
    return True


__all__ = [
    "EVIDENCE_DOCUMENTS",
    "PostgresCleanupEvidence",
    "child_environment_key_sets",
    "issue_postgres_cleanup_evidence",
    "request_and_wait_for_postgres_cleanup",
    "verify_runtime_evidence_bundle",
    "write_runtime_evidence_bundle",
]
