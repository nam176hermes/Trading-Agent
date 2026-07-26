from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType
from typing import Callable, cast

import pytest

from services.paper_runtime.controller import (
    EvidenceIncomplete,
    Package6Controller,
    RuntimeChildAuthorities,
    SourceDrift,
    SpawnEnvironmentEvidence,
    TrackedProcessIdentity,
    issue_runtime_child_authorities,
    verify_evidence_bundle,
)
from services.paper_runtime.evidence import (
    child_environment_key_sets,
    issue_postgres_cleanup_evidence,
    verify_runtime_evidence_bundle,
    write_runtime_evidence_bundle,
)
from services.paper_runtime.integration import RuntimeChainEvidence
from scripts.validate_package6_runtime_approval import (
    Package6ApprovalContext,
    ValidatedPackage6Capability,
    ValidatedOperation,
    canonical_record_sha256,
    validate_package6_runtime_approval,
)
from tests.foundation.test_package6_runtime_approval import (
    COMMIT,
    PG_APPROVAL,
    TREE,
    _context,
    _rebind_dynamic_authorities,
    _record,
)

def _capability(
    tmp_path: Path, argv: list[str], timeout: int = 5
) -> ValidatedPackage6Capability:
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_path.chmod(0o700)
    argv = [str(Path(argv[0]).resolve()), *argv[1:]]
    document = _record(tmp_path)
    cast(dict[str, object], document["constraints"])[
        "operation_timeout_seconds"
    ] = timeout
    _rebind_dynamic_authorities(document, tmp_path)
    capability = validate_package6_runtime_approval(document, _context(tmp_path))
    operations = {}
    for operation_id, action, operation_argv, digest in (
        (
            "fixture.start",
            "START",
            tuple(argv),
            hashlib.sha256(Path(argv[0]).read_bytes()).hexdigest(),
        ),
        ("fixture.stop", "STOP", (), None),
    ):
        operation = ValidatedOperation()
        for name, value in {
            "operation_id": operation_id,
            "action": action,
            "component": "FIXTURE_TEST_DOUBLE",
            "argv": operation_argv,
            "cwd": tmp_path,
            "bind_host": None,
            "port": None,
            "executable_sha256": digest,
        }.items():
            object.__setattr__(operation, name, value)
        operations[operation_id] = operation
    object.__setattr__(capability, "operations", MappingProxyType(operations))
    object.__setattr__(
        capability, "operation_ids", ("fixture.start", "fixture.stop")
    )
    return capability


def _credential_authorities(
    tmp_path: Path,
    capability: ValidatedPackage6Capability,
    monkeypatch: pytest.MonkeyPatch,
) -> RuntimeChildAuthorities:
    directories: list[Path] = []
    common = {
        "database-host": capability.postgres.bind_host,
        "database-port": str(capability.postgres.port),
        "database-name": capability.postgres.database_name,
        "database-password": "paper-only-password",
    }
    for component in ("job-api", "worker"):
        directory = tmp_path / f"{component}-credentials"
        directory.mkdir(mode=0o700, parents=True)
        values = dict(common)
        if component == "job-api":
            values.update(
                {
                    "job-api-principal-type": "OPERATOR",
                    "job-api-principal-id": "foundation-validation",
                    "job-api-token": "paper-only-token",
                }
            )
        for name, value in values.items():
            path = directory / name
            path.write_text(value, encoding="utf-8")
            path.chmod(0o600)
        directories.append(directory)
    monkeypatch.setattr(
        "services.job_store.config.read_systemd_credential",
        lambda values, name: (
            Path(values["CREDENTIALS_DIRECTORY"]) / name
        ).read_text(encoding="utf-8"),
    )
    return issue_runtime_child_authorities(
        capability,
        job_api_credentials=directories[0],
        worker_credentials=directories[1],
    )


def _runtime_capability(tmp_path: Path) -> ValidatedPackage6Capability:
    document = _record(tmp_path)
    capability = validate_package6_runtime_approval(document, _context(tmp_path))
    executable = str(Path(sys.executable).resolve())
    executable_sha256 = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    for operation in capability.operations.values():
        object.__setattr__(operation, "cwd", capability.source_root)
        object.__setattr__(operation, "bind_host", None)
        object.__setattr__(operation, "port", None)
        if operation.action == "START":
            object.__setattr__(
                operation,
                "argv",
                (executable, "-I", "-c", "import time;time.sleep(30)"),
            )
            object.__setattr__(
                operation, "executable_sha256", executable_sha256
            )
    return capability


@dataclass(frozen=True, slots=True)
class SealedRuntimeFixture:
    bundle: Path
    identities: dict[str, TrackedProcessIdentity]


def _sealed_runtime_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SealedRuntimeFixture:
    postgres_approval_bytes = b'{"approved":"synthetic-package6-test"}'
    postgres_sha256 = hashlib.sha256(postgres_approval_bytes).hexdigest()
    document = _record(tmp_path)
    cast(dict[str, object], document["postgres_authority"])[
        "approval_sha256"
    ] = postgres_sha256
    _rebind_dynamic_authorities(document, tmp_path)
    approval_bytes = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    context = _context(tmp_path)._replace(
        disposable_postgres_approval_sha256=postgres_sha256
    )
    capability = validate_package6_runtime_approval(
        document, context, approval_bytes=approval_bytes
    )
    capability.source_root.chmod(0o755)
    authorities = _credential_authorities(
        tmp_path / "credentials", capability, monkeypatch
    )
    evidence_root = capability.evidence_root
    evidence_root.mkdir(mode=0o700)
    job_keys = (
        "CREDENTIALS_DIRECTORY",
        "HOME",
        "LANG",
        "LC_ALL",
        "LIVE_EXECUTION_ENABLED",
        "LIVE_TRADING_APPROVED",
        "LIVE_TRADING_ENABLED",
        "PATH",
        "TRADING_MODE",
        "TRADING_PACKAGE6_APPROVAL_SHA256",
        "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH",
        "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH",
        "TRADING_PACKAGE6_STAGING_SCOPE",
        "TZ",
    )
    worker_keys = tuple(
        sorted((*job_keys, "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH"))
    )
    identities = {
        "job_api": TrackedProcessIdentity(
            operation_id="job-api.start",
            component="JOB_API",
            pid=4101,
            process_group=4101,
            start_ticks=5101,
            environment=SpawnEnvironmentEvidence(
                component="JOB_API",
                operation_id="job-api.start",
                pid=4101,
                process_group=4101,
                start_ticks=5101,
                keys=job_keys,
            ),
        ),
        "worker": TrackedProcessIdentity(
            operation_id="worker.start",
            component="WORKER",
            pid=4102,
            process_group=4102,
            start_ticks=5102,
            environment=SpawnEnvironmentEvidence(
                component="WORKER",
                operation_id="worker.start",
                pid=4102,
                process_group=4102,
                start_ticks=5102,
                keys=worker_keys,
            ),
        ),
    }
    job_id = "00000000-0000-0000-0000-000000000006"
    result_sha256 = "6" * 64
    job = {
        "job_id": job_id,
        "state": "SUCCEEDED",
        "attempt_count": 1,
        "reason_code": None,
        "result_hash": result_sha256,
        "lease_owner": None,
        "lease_expires_at": None,
        "cancel_requested_at": None,
    }
    events = [
        {
            "sequence": sequence,
            "from_state": None if sequence == 1 else states[sequence - 2],
            "to_state": state,
            "reason_code": None,
            "attempt_id": "attempt-1" if sequence > 1 else None,
            "metadata": {} if sequence < 4 else {"lineage": {}},
        }
        for sequence, (state, states) in enumerate(
            (
                ("QUEUED", ("QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED")),
                ("CLAIMED", ("QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED")),
                ("RUNNING", ("QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED")),
                ("SUCCEEDED", ("QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED")),
            ),
            start=1,
        )
    ]
    process_documents: dict[str, object] = {
        component: asdict(identity) for component, identity in identities.items()
    }
    chain = RuntimeChainEvidence(
        processes=process_documents,
        readiness={
            "operation_id": "job-api.start",
            "pid": 4101,
            "start_ticks": 5101,
            "listener_inode": 6101,
            "attempts": 1,
            "status": "READY",
        },
        first_request={
            "status": 201,
            "body": {"data": {"job": dict(job), "outcome": "ENQUEUED"}},
        },
        duplicate_request={
            "status": 200,
            "body": {"data": {"job": dict(job), "outcome": "DEDUPLICATED"}},
        },
        api_list={"data": {"items": [dict(job)]}},
        api_detail={"data": {"job": dict(job)}},
        database={
            "job": dict(job),
            "events": events,
            "attempts": [
                {
                    "attempt_id": "attempt-1",
                    "outcome": "SUCCEEDED",
                    "claimed_at": "2026-07-26T12:00:01Z",
                    "started_at": "2026-07-26T12:00:02Z",
                    "finished_at": "2026-07-26T12:00:03Z",
                    "exit_code": 0,
                    "heartbeat_at": "2026-07-26T12:00:02Z",
                    "lease_expires_at": "2026-07-26T12:00:12Z",
                    "termination_reason": None,
                }
            ],
            "artifacts": [
                {
                    "artifact_id": "artifact-1",
                    "attempt_id": "attempt-1",
                    "artifact_type": "RESULT",
                    "relative_ref": "reports/result.json",
                    "validator_id": "package6-provider-free-v1",
                    "sha256": result_sha256,
                    "size_bytes": 123,
                    "media_type": "application/json",
                    "truncated": False,
                    "validation_metadata": {
                        "market_data_provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
                        "fixture_sha256": capability.fixture.sha256,
                    },
                }
            ],
            "worker_heartbeats": [{"worker_id": "worker-1"}],
            "queue_depth": 0,
            "idempotent_job_count": 1,
            "postgres_approval_sha256": postgres_sha256,
        },
        dashboard_status={
            key: job[key]
            for key in (
                "job_id",
                "state",
                "attempt_count",
                "reason_code",
                "result_hash",
            )
        },
        worker_stop={
            "operation_id": "worker.stop",
            "pid": 4102,
            "process_group": 4102,
            "start_ticks": 5102,
            "listener_negative_probes": 0,
            "exit_code": 0,
            "cleanup_proven": True,
        },
        job_api_stop={
            "operation_id": "job-api.stop",
            "pid": 4101,
            "process_group": 4101,
            "start_ticks": 5101,
            "listener_negative_probes": 3,
            "exit_code": 0,
            "cleanup_proven": True,
        },
    )
    cleanup = issue_postgres_cleanup_evidence(
        capability,
        {
            "approval_sha256": postgres_sha256,
            "listener_alive": False,
            "listener_negative_probes": 3,
            "process_alive": False,
            "process_group_alive": False,
            "process_pid": 4200,
            "process_group": 4200,
            "start_ticks": 5200,
            "exit_code": 0,
            "pgdata_exists": False,
            "cleanup_complete": True,
        },
    )
    bundle = write_runtime_evidence_bundle(
        capability,
        authorities,
        chain,
        cleanup,
        source_root=capability.source_root,
        approval_bytes=approval_bytes,
        postgres_approval_bytes=postgres_approval_bytes,
    )
    return SealedRuntimeFixture(bundle=bundle, identities=identities)


def _rewrite_runtime_and_index(
    bundle: Path,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    runtime_path = bundle / "runtime.json"
    index_path = bundle / "index.json"
    runtime = cast(
        dict[str, object],
        json.loads(runtime_path.read_text(encoding="utf-8")),
    )
    mutation(runtime)
    raw = json.dumps(
        runtime, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    runtime_mode = runtime_path.stat().st_mode & 0o777
    runtime_path.write_bytes(raw)
    runtime_path.chmod(runtime_mode)
    index = cast(
        dict[str, object],
        json.loads(index_path.read_text(encoding="utf-8")),
    )
    entries = cast(list[dict[str, object]], index["entries"])
    runtime_entry = next(entry for entry in entries if entry["path"] == "runtime.json")
    runtime_entry["sha256"] = hashlib.sha256(raw).hexdigest()
    runtime_entry["size_bytes"] = len(raw)
    index_raw = json.dumps(
        index, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    index_mode = index_path.stat().st_mode & 0o777
    index_path.write_bytes(index_raw)
    index_path.chmod(index_mode)


def _environment_records(
    runtime: dict[str, object], component: str
) -> tuple[dict[str, object], dict[str, object]]:
    top = cast(dict[str, dict[str, object]], runtime["child_environments"])
    chain = cast(dict[str, object], runtime["chain"])
    processes = cast(dict[str, dict[str, object]], chain["processes"])
    process_environment = cast(dict[str, object], processes[component]["environment"])
    return top[component], process_environment


def _mutate_both_environment_records(
    runtime: dict[str, object],
    component: str,
    field: str,
    value: object,
) -> None:
    for environment in _environment_records(runtime, component):
        environment[field] = value


def _add_environment_key(runtime: dict[str, object]) -> None:
    top, process = _environment_records(runtime, "job_api")
    keys = [*cast(list[str], top["keys"]), "UNAPPROVED_PACKAGE6_KEY"]
    top["keys"] = keys
    process["keys"] = list(keys)


def _remove_environment_key(runtime: dict[str, object]) -> None:
    top, process = _environment_records(runtime, "job_api")
    keys = cast(list[str], top["keys"])[1:]
    top["keys"] = keys
    process["keys"] = list(keys)


def _swap_environment_key_sets(runtime: dict[str, object]) -> None:
    job_top, job_process = _environment_records(runtime, "job_api")
    worker_top, worker_process = _environment_records(runtime, "worker")
    job_keys = list(cast(list[str], job_top["keys"]))
    worker_keys = list(cast(list[str], worker_top["keys"]))
    job_top["keys"] = worker_keys
    job_process["keys"] = list(worker_keys)
    worker_top["keys"] = job_keys
    worker_process["keys"] = list(job_keys)


def _diverge_top_level_environment(runtime: dict[str, object]) -> None:
    top, _process = _environment_records(runtime, "job_api")
    top["pid"] = cast(int, top["pid"]) + 1


def _add_credential_looking_environment_field(
    runtime: dict[str, object],
) -> None:
    for environment in _environment_records(runtime, "job_api"):
        environment["authorization_material"] = "[REDACTED]"


@pytest.mark.parametrize(
    ("case", "mutation"),
    (
        ("unapproved-key", _add_environment_key),
        ("missing-approved-key", _remove_environment_key),
        ("swapped-component-key-sets", _swap_environment_key_sets),
        (
            "component",
            lambda runtime: _mutate_both_environment_records(
                runtime, "job_api", "component", "WORKER"
            ),
        ),
        (
            "operation-id",
            lambda runtime: _mutate_both_environment_records(
                runtime, "job_api", "operation_id", "worker.start"
            ),
        ),
        (
            "pid",
            lambda runtime: _mutate_both_environment_records(
                runtime, "job_api", "pid", 9991
            ),
        ),
        (
            "process-group",
            lambda runtime: _mutate_both_environment_records(
                runtime, "job_api", "process_group", 9992
            ),
        ),
        (
            "start-ticks",
            lambda runtime: _mutate_both_environment_records(
                runtime, "job_api", "start_ticks", 9993
            ),
        ),
        ("top-level-divergence", _diverge_top_level_environment),
        ("unexpected-credential-looking-field", _add_credential_looking_environment_field),
    ),
)
def test_sealed_runtime_rejects_semantic_environment_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    fixture = _sealed_runtime_fixture(tmp_path, monkeypatch)
    _rewrite_runtime_and_index(fixture.bundle, mutation)

    with pytest.raises(EvidenceIncomplete) as caught:
        verify_runtime_evidence_bundle(fixture.bundle)
    assert str(caught.value) == (
        "runtime state, event, or sealed result proof is invalid"
    ), f"{case}: semantic environment tamper reached the wrong sanitized rejection"


def test_sealed_child_environments_come_from_tracked_process_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _sealed_runtime_fixture(tmp_path, monkeypatch)
    runtime = cast(
        dict[str, object],
        json.loads((fixture.bundle / "runtime.json").read_text(encoding="utf-8")),
    )
    observed = cast(dict[str, object], runtime["child_environments"])

    assert observed == {
        component: {
            **asdict(identity.environment),
            "keys": list(identity.environment.keys),
        }
        for component, identity in fixture.identities.items()
    }


def test_start_tracks_the_exact_environment_supplied_to_each_popen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _runtime_capability(tmp_path)
    authorities = _credential_authorities(
        tmp_path / "credentials", capability, monkeypatch
    )
    controller = Package6Controller(capability, child_authorities=authorities)
    captured: dict[str, dict[str, str]] = {}
    real_popen = subprocess.Popen
    invoke_popen = cast(Callable[..., subprocess.Popen[bytes]], real_popen)

    def observed_popen(
        args: list[str], **kwargs: object
    ) -> subprocess.Popen[bytes]:
        environment = cast(dict[str, str], kwargs["env"])
        component = (
            "worker"
            if "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH" in environment
            else "job_api"
        )
        captured[component] = dict(environment)
        return invoke_popen(args, **kwargs)

    monkeypatch.setattr(
        "services.paper_runtime.controller.subprocess.Popen", observed_popen
    )

    job_api = controller.start("job-api.start")
    worker = controller.start("worker.start")
    controller.stop("worker.stop")
    controller.stop("job-api.stop")

    assert job_api.environment.keys == tuple(sorted(captured["job_api"]))
    assert worker.environment.keys == tuple(sorted(captured["worker"]))
    assert set(captured["job_api"]) == set(child_environment_key_sets()["job_api"])
    assert set(captured["worker"]) == set(child_environment_key_sets()["worker"])


def test_wait_ready_allows_valid_response_beyond_half_second_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = _runtime_capability(tmp_path)
    operation = capability.operations["job-api.start"]
    object.__setattr__(operation, "bind_host", "127.0.0.1")
    object.__setattr__(operation, "port", 8401)
    clock = iter(float(tick) for tick in range(100))
    controller = Package6Controller(capability, monotonic=lambda: next(clock))
    identity = TrackedProcessIdentity(
        operation_id=operation.operation_id,
        component=operation.component,
        pid=4101,
        process_group=4101,
        start_ticks=5101,
        environment=SpawnEnvironmentEvidence(
            component=operation.component,
            operation_id=operation.operation_id,
            pid=4101,
            process_group=4101,
            start_ticks=5101,
            keys=(),
        ),
    )

    class RunningProcess:
        def poll(self) -> None:
            return None

    class ReadyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 16 * 1024
            return b'{"data":{"status":"READY"}}'

    probe_timeouts: list[float] = []

    def delayed_ready_response(url: str, *, timeout: float):
        assert url == "http://127.0.0.1:8401/health/ready"
        probe_timeouts.append(timeout)
        if timeout <= 0.5:
            raise TimeoutError("valid readiness response needs more than 0.5 seconds")
        return ReadyResponse()

    controller._tracked[operation.component] = (
        cast(subprocess.Popen[bytes], RunningProcess()),
        identity,
    )
    monkeypatch.setattr(
        "services.paper_runtime.controller._start_ticks", lambda pid: 5101
    )
    monkeypatch.setattr(
        controller, "_listener_inode", lambda host, port, pid: 6101
    )
    monkeypatch.setattr(
        "services.paper_runtime.controller.urlopen", delayed_ready_response
    )
    monkeypatch.setattr(
        "services.paper_runtime.controller.time.sleep", lambda seconds: None
    )

    readiness = controller.wait_ready("job-api.start")

    assert readiness.status == "READY"
    assert readiness.listener_inode == 6101
    assert probe_timeouts and probe_timeouts[0] > 0.5


def test_controller_runs_exact_argv_and_writes_stable_hash_index(tmp_path: Path) -> None:
    script = (
        "import json,os;"
        "print(json.dumps({'pid':os.getpid(),'fixture':'provider-free'}))"
    )
    capability = _capability(tmp_path, [sys.executable, "-I", "-c", script])
    attestations: list[str] = []

    bundle = Package6Controller(
        capability,
    ).run_once("fixture.start")

    assert bundle.process.exit_code == 0
    assert bundle.process.shell is False
    assert bundle.process.stdin_closed is True
    assert bundle.process.process_group == bundle.process.pid
    assert bundle.process.cleanup_proven is True
    assert bundle.process.stdout_size <= 65536
    assert verify_evidence_bundle(bundle.root) is True
    first = (bundle.root / "index.json").read_bytes()
    assert json.loads(first)["verdict"] == "PENDING_CONTROLLER_RUNTIME_VERIFICATION"
    assert verify_evidence_bundle(bundle.root) is True
    assert (bundle.root / "index.json").read_bytes() == first


def test_controller_rejects_source_drift_before_spawn(tmp_path: Path) -> None:
    marker = tmp_path / "spawned"
    capability = _capability(
        tmp_path,
        [sys.executable, "-I", "-c", f"open({str(marker)!r},'w').close()"],
    )

    (tmp_path / "source/apps/job_api/app.py").write_text("drift\n", encoding="utf-8")
    with pytest.raises(SourceDrift):
        Package6Controller(capability).run_once("fixture.start")

    assert not marker.exists()


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("import sys;sys.exit(7)", 7),
        ("import time;time.sleep(30)", None),
    ],
)
def test_failed_and_timed_out_children_are_reaped(
    tmp_path: Path, script: str, expected: int | None
) -> None:
    capability = _capability(
        tmp_path, [sys.executable, "-I", "-c", script], timeout=1
    )

    bundle = Package6Controller(capability).run_once("fixture.start")

    assert bundle.process.exit_code == expected
    assert bundle.process.cleanup_proven is True
    assert bundle.process.pid_alive is False
    assert bundle.process.timed_out is (expected is None)


def test_output_is_bounded_and_missing_proof_blocks_completion(tmp_path: Path) -> None:
    capability = _capability(
        tmp_path,
        [sys.executable, "-I", "-c", "print('x'*100000)"],
    )
    bundle = Package6Controller(capability).run_once("fixture.start")

    assert bundle.process.stdout_size == 65536
    process_path = bundle.root / "process.json"
    process = json.loads(process_path.read_text(encoding="utf-8"))
    process.pop("cleanup_proven")
    process_path.chmod(0o600)
    process_path.write_text(
        json.dumps(process, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(EvidenceIncomplete):
        verify_evidence_bundle(bundle.root)


def test_evidence_tamper_is_detected(tmp_path: Path) -> None:
    capability = _capability(
        tmp_path, [sys.executable, "-I", "-c", "print('ok')"]
    )
    bundle = Package6Controller(capability).run_once("fixture.start")
    (bundle.root / "stdout.bin").write_bytes(b"tampered")

    with pytest.raises(EvidenceIncomplete, match="digest"):
        verify_evidence_bundle(bundle.root)


def test_preexisting_approved_listener_blocks_start(tmp_path: Path) -> None:
    capability = _capability(
        tmp_path, [sys.executable, "-I", "-c", "print('must not spawn')"]
    )
    operation = capability.operations["fixture.start"]
    object.__setattr__(operation, "bind_host", "127.0.0.1")
    object.__setattr__(operation, "port", 8401)
    controller = Package6Controller(capability)
    def occupied_listener(host: str, port: int) -> bool:
        return True

    controller._listener_alive = occupied_listener
    with pytest.raises(RuntimeError, match="listener.*already"):
        controller.run_once("fixture.start")


def test_evidence_verifier_rejects_symlinked_index(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "index.json").symlink_to(target)

    with pytest.raises(EvidenceIncomplete, match="index|symlink|policy"):
        verify_evidence_bundle(root)


def test_stop_signals_the_tracked_start_process_without_spawning_again(
    tmp_path: Path, monkeypatch
) -> None:
    capability = _capability(
        tmp_path, [sys.executable, "-I", "-c", "import time;time.sleep(30)"]
    )
    controller = Package6Controller(capability)
    real_popen = __import__("subprocess").Popen
    spawns = []

    def counted_popen(
        args: list[str], **kwargs: object
    ) -> subprocess.Popen[bytes]:
        spawns.append(tuple(args))
        return real_popen(args, **kwargs)

    monkeypatch.setattr("services.paper_runtime.controller.subprocess.Popen", counted_popen)
    identity = controller.start("fixture.start")
    stopped = controller.stop("fixture.stop")

    assert len(spawns) == 1
    assert stopped.pid == identity.pid
    assert stopped.cleanup_proven is True


def test_postgres_cleanup_capability_rejects_any_residual_runtime_state(
    tmp_path: Path,
) -> None:
    capability = _capability(
        tmp_path, [sys.executable, "-I", "-c", "print('unused')"]
    )
    complete = {
        "approval_sha256": capability.postgres.approval_sha256,
        "listener_alive": False,
        "listener_negative_probes": 3,
        "process_alive": False,
        "process_group_alive": False,
        "process_pid": 123,
        "process_group": 123,
        "start_ticks": 456,
        "exit_code": 0,
        "pgdata_exists": False,
        "cleanup_complete": True,
    }
    for field in (
        "listener_alive",
        "process_alive",
        "process_group_alive",
        "pgdata_exists",
    ):
        observed = dict(complete)
        observed[field] = True
        with pytest.raises(EvidenceIncomplete, match="cleanup"):
            issue_postgres_cleanup_evidence(capability, observed)


def test_factory_child_capability_transmits_exact_component_key_sets(
    tmp_path: Path, monkeypatch,
) -> None:
    capability = _capability(
        tmp_path, [sys.executable, "-I", "-c", "print('unused')"]
    )
    authorities = _credential_authorities(tmp_path, capability, monkeypatch)
    controller = Package6Controller(capability, child_authorities=authorities)

    job_api = controller._child_environment("JOB_API")
    worker = controller._child_environment("WORKER")

    shared = {
        "TRADING_PACKAGE6_STAGING_SCOPE": authorities.staging_scope,
        "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH": str(authorities.staging_authority),
        "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH": str(authorities.staging_activation),
        "TRADING_PACKAGE6_APPROVAL_SHA256": authorities.package6_approval_sha256,
    }
    assert {key: job_api[key] for key in shared} == shared
    assert {key: worker[key] for key in shared} == shared
    assert "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH" not in job_api
    assert worker["TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH"] == str(
        authorities.fixture_authority
    )
    assert set(job_api) == {
        "PATH", "HOME", "LANG", "LC_ALL", "TZ", "TRADING_MODE",
        "LIVE_EXECUTION_ENABLED", "LIVE_TRADING_APPROVED",
        "LIVE_TRADING_ENABLED", "CREDENTIALS_DIRECTORY", *shared,
    }
    assert set(worker) == {
        *job_api,
        "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH",
    }


def test_evidence_child_environment_contract_has_four_shared_staging_keys() -> None:
    key_sets = child_environment_key_sets()
    shared_staging = {
        "TRADING_PACKAGE6_STAGING_SCOPE",
        "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH",
        "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH",
        "TRADING_PACKAGE6_APPROVAL_SHA256",
    }

    assert shared_staging <= set(key_sets["job_api"])
    assert set(key_sets["worker"]) == {
        *key_sets["job_api"],
        "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH",
    }


def test_child_capability_is_parent_bound_and_rechecked_before_environment(
    tmp_path: Path, monkeypatch,
) -> None:
    first = _capability(tmp_path / "first", [sys.executable, "-I", "-c", "print(1)"])
    second = _capability(tmp_path / "second", [sys.executable, "-I", "-c", "print(2)"])
    authorities = _credential_authorities(
        tmp_path / "credentials", first, monkeypatch
    )

    with pytest.raises(TypeError, match="child authorities"):
        Package6Controller(second, child_authorities=authorities)

    controller = Package6Controller(first, child_authorities=authorities)
    activation = authorities.staging_activation
    activation.chmod(0o600)
    activation.write_bytes(activation.read_bytes() + b"tamper\n")
    activation.chmod(0o444)
    with pytest.raises(SourceDrift, match="authority"):
        controller._child_environment("JOB_API")


@pytest.mark.parametrize("mutation", ("content", "replace", "missing", "symlink", "mode"))
def test_child_file_drift_fails_before_spawn(
    tmp_path: Path, monkeypatch, mutation: str,
) -> None:
    capability = _capability(
        tmp_path / "candidate", [sys.executable, "-I", "-c", "print(1)"]
    )
    authorities = _credential_authorities(tmp_path / "credentials", capability, monkeypatch)
    controller = Package6Controller(capability, child_authorities=authorities)
    credential = authorities.job_api_credentials / "database-host"
    original = credential.read_text(encoding="utf-8")
    if mutation == "content":
        credential.write_text("localhost", encoding="utf-8")
        credential.chmod(0o600)
    elif mutation == "replace":
        replacement = credential.with_name("replacement")
        replacement.write_text(original, encoding="utf-8")
        replacement.chmod(0o600)
        replacement.replace(credential)
    elif mutation == "missing":
        credential.unlink()
    elif mutation == "symlink":
        credential.unlink()
        credential.symlink_to("/dev/null")
    else:
        credential.chmod(0o644)
    calls = 0

    def forbidden_popen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("Popen must not be called")

    monkeypatch.setattr("services.paper_runtime.controller.subprocess.Popen", forbidden_popen)
    with pytest.raises(SourceDrift, match="authority"):
        controller.run_once("fixture.start")
    assert calls == 0


def test_private_pins_never_render_secret_or_digest(
    tmp_path: Path, monkeypatch,
) -> None:
    capability = _capability(
        tmp_path / "candidate", [sys.executable, "-I", "-c", "print(1)"]
    )
    authorities = _credential_authorities(tmp_path / "credentials", capability, monkeypatch)
    secret = (authorities.job_api_credentials / "job-api-token").read_bytes()
    rendered = repr(authorities)
    assert secret.decode() not in rendered
    assert hashlib.sha256(secret).hexdigest() not in rendered


def test_raw_or_forged_child_authority_is_rejected(tmp_path: Path) -> None:
    capability = _capability(
        tmp_path, [sys.executable, "-I", "-c", "print('unused')"]
    )
    forged = RuntimeChildAuthorities()

    with pytest.raises(TypeError, match="issued runtime child authorities"):
        Package6Controller(capability, child_authorities=forged)
