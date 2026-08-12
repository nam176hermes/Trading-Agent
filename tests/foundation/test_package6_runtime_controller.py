from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import inspect
import json
import os
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from tests.foundation._package6_staging_fixture import Package6StagingLease
from services.paper_runtime import controller as controller_module
from services.paper_runtime.controller import (
    EvidenceBundle,
    EvidenceIncomplete,
    Package6Controller,
    RuntimeStartFailure,
    SourceDrift,
    SpawnEnvironmentEvidence,
    StopEvidence,
    TrackedProcessIdentity,
    TranscriptMetadata,
    issue_runtime_child_authorities,
)
from services.paper_runtime.custodian_client import (
    CustodianAttestation,
    CustodianClient,
    CustodianProtocolError,
    CustodianTimeout,
    NativeBundleReceipt,
    NativeOperationStatus,
    NativeTranscriptChunk,
    OperationState,
    TranscriptStream,
)


OPERATION_ID = bytes.fromhex("00112233445566778899aabbccddeeff")
RECOVERY_TOKEN = bytes.fromhex("ffeeddccbbaa99887766554433221100")
REQUEST_DIGEST = bytes.fromhex("11" * 32)
EXECUTABLE_DIGEST = bytes.fromhex("22" * 32)
PUBLICATION_DIGEST = bytes.fromhex("33" * 32)


@dataclass(frozen=True, slots=True)
class _Operation:
    operation_id: str
    action: str
    component: str
    argv: tuple[str, ...]
    cwd: Path
    bind_host: str | None
    port: int | None
    executable_sha256: str | None


def _native_status(
    *,
    operation_id: bytes = OPERATION_ID,
    state: OperationState = OperationState.RESULT_RETAINED,
    authority_retained: bool = True,
    bundle_committed: bool = False,
    acknowledged: bool = False,
    publication_sha256: bytes = bytes(32),
    exit_status: int = 0,
) -> NativeOperationStatus:
    return NativeOperationStatus(
        operation_id=operation_id,
        recovery_token=RECOVERY_TOKEN,
        state=state,
        resume_state=state,
        authority_retained=authority_retained,
        bundle_committed=bundle_committed,
        stdout_truncated=False,
        stderr_truncated=False,
        acknowledged=acknowledged,
        exit_status=exit_status,
        request_sha256=REQUEST_DIGEST,
        executable_sha256=EXECUTABLE_DIGEST,
        publication_sha256=publication_sha256,
    )


def _attestation(**overrides: object) -> CustodianAttestation:
    values: dict[str, object] = {
        "helper_binary_sha256": "a" * 64,
        "native_source_set_sha256": "b" * 64,
        "protocol_version": 1,
        "protocol_features": (),
        "endpoint_authority": "PREOPENED_UNIX_SEQPACKET_DESCRIPTOR",
        "peer_pid": os.getpid(),
        "peer_uid": os.geteuid(),
        "peer_gid": os.getegid(),
        "candidate_commit": "c" * 40,
        "candidate_tree": "d" * 40,
        "stage_sha256": "e" * 64,
        "fixture_sha256": "f" * 64,
        "mode": "PAPER",
        "live_execution_approved": False,
        "live_trading_approved": False,
    }
    values.update(overrides)
    return CustodianAttestation(**values)


class _ClientScript:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.status_value = _native_status(
            state=OperationState.RUNNING,
            authority_retained=True,
            exit_status=-(2**31),
        )
        self.stop_value = _native_status()
        self.run_once_value = _native_status()
        self.recover_value: tuple[NativeOperationStatus, ...] = (
            self.stop_value,
        )
        self.transcripts = {
            TranscriptStream.STDOUT: b"stdout evidence",
            TranscriptStream.STDERR: b"stderr evidence",
        }

    def bind(self, attestation: CustodianAttestation | None = None) -> CustodianClient:
        client = CustodianClient.__new__(CustodianClient)
        client._attestation = attestation or _attestation()
        for name in (
            "start",
            "status",
            "stop",
            "run_once",
            "read_transcript",
            "publish_bundle",
            "acknowledge",
            "recover",
        ):
            setattr(client, name, getattr(self, name))
        return client

    def start(self, request: object) -> NativeOperationStatus:
        self.calls.append(("start", (request,)))
        return self.status_value

    def status(
        self, operation_id: bytes, recovery_token: bytes
    ) -> NativeOperationStatus:
        self.calls.append(("status", (operation_id, recovery_token)))
        return self.status_value

    def stop(
        self, operation_id: bytes, recovery_token: bytes
    ) -> NativeOperationStatus:
        self.calls.append(("stop", (operation_id, recovery_token)))
        return self.stop_value

    def run_once(self, request: object) -> NativeOperationStatus:
        self.calls.append(("run_once", (request,)))
        return self.run_once_value

    def read_transcript(
        self,
        operation_id: bytes,
        recovery_token: bytes,
        stream: TranscriptStream,
        *,
        offset: int,
        length: int,
    ) -> NativeTranscriptChunk:
        self.calls.append(
            (
                "read_transcript",
                (operation_id, recovery_token, stream, offset, length),
            )
        )
        content = self.transcripts[stream]
        retained = content[offset : offset + length]
        return NativeTranscriptChunk(
            operation_id=operation_id,
            stream=stream,
            offset=offset,
            data=retained,
            observed_size=len(content),
            retained_size=len(content),
            sha256=hashlib.sha256(content).digest(),
            eof=offset + len(retained) == len(content),
            truncated=False,
        )

    def publish_bundle(
        self,
        operation_id: bytes,
        recovery_token: bytes,
        publication_id: bytes,
    ) -> NativeBundleReceipt:
        self.calls.append(
            (
                "publish_bundle",
                (operation_id, recovery_token, publication_id),
            )
        )
        published = _native_status(
            bundle_committed=True,
            publication_sha256=PUBLICATION_DIGEST,
        )
        return NativeBundleReceipt(
            operation=published,
            manifest_sha256=PUBLICATION_DIGEST,
        )

    def acknowledge(
        self,
        operation_id: bytes,
        recovery_token: bytes,
        publication_digest: bytes,
    ) -> NativeOperationStatus:
        self.calls.append(
            (
                "acknowledge",
                (operation_id, recovery_token, publication_digest),
            )
        )
        return _native_status(
            state=OperationState.ACKNOWLEDGED,
            authority_retained=False,
            bundle_committed=True,
            acknowledged=True,
            publication_sha256=PUBLICATION_DIGEST,
        )

    def recover(self) -> tuple[NativeOperationStatus, ...]:
        self.calls.append(("recover", ()))
        return self.recover_value


def _capability(tmp_path: Path) -> SimpleNamespace:
    source_root = tmp_path / "source"
    source_root.mkdir()
    bound = source_root / "bound.py"
    bound.write_bytes(b"PAPER = True\n")
    bound.chmod(0o644)
    stage = tmp_path / "stage/application"
    executable = stage / ".venv/bin/python3.11"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"\x7fELF" + bytes(124))
    operations = (
        _Operation(
            "job-api.start",
            "START",
            "JOB_API",
            (
                str(executable),
                "-I",
                "-m",
                "apps.job_api.main",
            ),
            stage,
            "127.0.0.1",
            8401,
            EXECUTABLE_DIGEST.hex(),
        ),
        _Operation(
            "job-api.stop",
            "STOP",
            "JOB_API",
            (),
            stage,
            "127.0.0.1",
            8401,
            None,
        ),
    )
    return SimpleNamespace(
        approval_sha256="9" * 64,
        source_commit="c" * 40,
        source_tree="d" * 40,
        source_root=source_root,
        source_bindings=(
            ("bound.py", hashlib.sha256(bound.read_bytes()).hexdigest()),
        ),
        operations=MappingProxyType(
            {operation.operation_id: operation for operation in operations}
        ),
        max_output_bytes=65536,
        startup_timeout_seconds=1,
        cleanup_timeout_seconds=1,
        evidence_root=tmp_path / "evidence",
        fixture_sha256="f" * 64,
        fixture=SimpleNamespace(path=tmp_path / "fixture.json"),
        staging_material=SimpleNamespace(
            authority_path=tmp_path / "staging-authority.json",
            activation_path=tmp_path / "staging-activation.json",
        ),
        authority_digests=MappingProxyType({"stage": "e" * 64}),
        custodian=SimpleNamespace(
            helper_binary_sha256="a" * 64,
            native_source_set_sha256="b" * 64,
            protocol_version=1,
            protocol_features=(),
            endpoint_authority="PREOPENED_UNIX_SEQPACKET_DESCRIPTOR",
            operations=(
                "START",
                "STOP",
                "STATUS",
                "RECOVER",
                "RUN_ONCE",
                "READ_TRANSCRIPT",
                "PUBLISH_BUNDLE",
                "ACK",
            ),
            candidate_commit="c" * 40,
            candidate_tree="d" * 40,
            stage_sha256="e" * 64,
            fixture_sha256="f" * 64,
            mode="PAPER",
            live_execution_approved=False,
            live_trading_approved=False,
        ),
    )


_JOB_API_CREDENTIAL_NAMES = (
    "database-host",
    "database-port",
    "database-name",
    "database-password",
    "job-api-principal-type",
    "job-api-principal-id",
    "job-api-token",
)
_WORKER_CREDENTIAL_NAMES = (
    "database-host",
    "database-port",
    "database-name",
    "database-password",
)


def _child_authorities(
    tmp_path: Path,
    capability: SimpleNamespace,
) -> object:
    tmp_path.mkdir(parents=True, exist_ok=True)
    directories = []
    for component, names in (
        ("job-api", _JOB_API_CREDENTIAL_NAMES),
        ("worker", _WORKER_CREDENTIAL_NAMES),
    ):
        directory = tmp_path / f"{component}-credentials"
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
        for name in names:
            path = directory / name
            path.write_text(f"{component}-{name}", encoding="utf-8")
            path.chmod(0o600)
        directories.append(directory)
    return issue_runtime_child_authorities(
        capability,
        job_api_credentials=directories[0],
        worker_credentials=directories[1],
    )


@dataclass(frozen=True, slots=True)
class SealedRuntimeFixture:
    bundle: Path
    identities: dict[str, TrackedProcessIdentity]
    disposable_root: Path


def _sealed_runtime_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    lease: Package6StagingLease,
) -> SealedRuntimeFixture:
    """Build a source-owned evidence fixture using native custody records."""

    from scripts.validate_package6_runtime_approval import (
        validate_package6_runtime_approval,
    )
    from services.paper_runtime.evidence import (
        child_environment_key_sets,
        issue_postgres_cleanup_evidence,
        write_runtime_evidence_bundle,
    )
    from services.paper_runtime.integration import RuntimeChainEvidence
    from tests.foundation.test_package6_runtime_approval import (
        _context,
        _rebind_dynamic_authorities,
        _record,
    )

    postgres_approval_bytes = b'{"approved":"synthetic-package6-test"}'
    postgres_sha256 = hashlib.sha256(postgres_approval_bytes).hexdigest()
    document = _record(tmp_path, lease=lease)
    document["postgres_authority"]["approval_sha256"] = postgres_sha256
    _rebind_dynamic_authorities(document, tmp_path, lease=lease)
    approval_bytes = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    context = _context(tmp_path, lease=lease)._replace(
        disposable_postgres_approval_sha256=postgres_sha256
    )
    capability = validate_package6_runtime_approval(
        document,
        context,
        approval_bytes=approval_bytes,
    )
    capability.source_root.chmod(0o755)
    authorities = _child_authorities(
        tmp_path / "credentials",
        capability,
    )
    capability.evidence_root.mkdir(mode=0o700)
    capability.disposable_root.mkdir(mode=0o700)
    key_sets = child_environment_key_sets()
    operation_ids = {
        "job_api": bytes.fromhex("11" * 16),
        "worker": bytes.fromhex("22" * 16),
    }
    tokens = {
        "job_api": bytes.fromhex("33" * 16),
        "worker": bytes.fromhex("44" * 16),
    }
    identities = {
        component: TrackedProcessIdentity(
            operation_id=(
                "job-api.start" if component == "job_api" else "worker.start"
            ),
            component=(
                "JOB_API" if component == "job_api" else "WORKER"
            ),
            native_operation_id=operation_ids[component].hex(),
            recovery_token=tokens[component].hex(),
            state="RUNNING",
            environment=SpawnEnvironmentEvidence(
                component=(
                    "JOB_API" if component == "job_api" else "WORKER"
                ),
                operation_id=(
                    "job-api.start"
                    if component == "job_api"
                    else "worker.start"
                ),
                keys=tuple(key_sets[component]),
            ),
        )
        for component in ("job_api", "worker")
    }
    transcript = TranscriptMetadata(
        sha256=hashlib.sha256(b"bounded native transcript").hexdigest(),
        size=len(b"bounded native transcript"),
        observed_size=len(b"bounded native transcript"),
        truncated=False,
        eof=True,
    )
    stops = {
        component: StopEvidence(
            operation_id=(
                "job-api.stop" if component == "job_api" else "worker.stop"
            ),
            component=(
                "JOB_API" if component == "job_api" else "WORKER"
            ),
            native_operation_id=operation_ids[component].hex(),
            recovery_token=tokens[component].hex(),
            state="RESULT_RETAINED",
            exit_code=0,
            cleanup_proven=True,
            stdout=transcript,
            stderr=transcript,
        )
        for component in ("job_api", "worker")
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
    states = ("QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED")
    events = [
        {
            "sequence": index,
            "from_state": None if index == 1 else states[index - 2],
            "to_state": state,
            "reason_code": None,
            "attempt_id": "attempt-1" if index > 1 else None,
            "metadata": (
                {}
                if index < 4
                else {
                    "lineage": {
                        "command": {
                            "sha256": capability.authority_digests[
                                "command"
                            ]
                        },
                        "safety": {
                            "final": {
                                "sha256": capability.authority_digests[
                                    "safety"
                                ]
                            }
                        },
                    }
                }
            ),
        }
        for index, state in enumerate(states, start=1)
    ]
    chain = RuntimeChainEvidence(
        processes={
            component: asdict(identity)
            for component, identity in identities.items()
        },
        readiness={
            "operation_id": "job-api.start",
            "native_operation_id": operation_ids["job_api"].hex(),
            "attempts": 1,
            "status": "READY",
        },
        first_request={
            "status": 201,
            "body": {"data": {"job": dict(job), "outcome": "ENQUEUED"}},
        },
        duplicate_request={
            "status": 200,
            "body": {
                "data": {"job": dict(job), "outcome": "DEDUPLICATED"}
            },
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
                        "market_data_provenance": (
                            "DETERMINISTIC_PROVIDER_FREE_V1"
                        ),
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
        worker_stop=asdict(stops["worker"]),
        job_api_stop=asdict(stops["job_api"]),
        native_publications={
            component: {
                "operation_id": operation_ids[component].hex(),
                "manifest_sha256": (
                    "7" * 64 if component == "job_api" else "8" * 64
                ),
            }
            for component in ("job_api", "worker")
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
    return SealedRuntimeFixture(
        bundle=bundle,
        identities=identities,
        disposable_root=capability.disposable_root,
    )


@pytest.fixture
def issued_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    capability = _capability(tmp_path)
    monkeypatch.setattr(
        controller_module,
        "is_issued_capability",
        lambda value: value is capability,
    )
    monkeypatch.setattr(
        controller_module,
        "_native_operation_id",
        lambda _capability, _component: OPERATION_ID,
    )
    return capability


def test_runtime_without_attested_native_client_fails_before_any_start(
    issued_capability: SimpleNamespace,
) -> None:
    with pytest.raises(TypeError, match="attested native custodian"):
        Package6Controller(issued_capability)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("helper_binary_sha256", "0" * 64),
        ("native_source_set_sha256", "0" * 64),
        ("protocol_version", 2),
        ("protocol_features", ("UNKNOWN",)),
        ("endpoint_authority", "FILESYSTEM_SOCKET_PATH"),
        ("candidate_commit", "0" * 40),
        ("candidate_tree", "0" * 40),
        ("stage_sha256", "0" * 64),
        ("fixture_sha256", "0" * 64),
        ("mode", "LIVE"),
        ("live_execution_approved", True),
        ("live_trading_approved", True),
        ("peer_uid", os.geteuid() + 1),
        ("peer_gid", os.getegid() + 1),
    ),
)
def test_controller_rejects_every_attestation_mismatch_before_request(
    issued_capability: SimpleNamespace,
    field: str,
    value: object,
) -> None:
    script = _ClientScript()
    attestation = _attestation()
    object.__setattr__(attestation, field, value)
    client = script.bind(attestation)

    with pytest.raises(TypeError, match="attested native custodian"):
        Package6Controller(issued_capability, custodian_client=client)

    assert script.calls == []


def test_start_delegates_exact_relative_executable_and_paper_environment(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )

    identity = controller.start("job-api.start")

    assert identity.operation_id == "job-api.start"
    assert identity.component == "JOB_API"
    assert identity.native_operation_id == OPERATION_ID.hex()
    assert identity.recovery_token == RECOVERY_TOKEN.hex()
    assert [name for name, _arguments in script.calls] == ["start"]
    request = script.calls[0][1][0]
    assert request.operation_id == OPERATION_ID
    assert request.executable == ".venv/bin/python3.11"
    assert request.executable_sha256 == EXECUTABLE_DIGEST
    assert request.argv[1:] == ("-I", "-m", "apps.job_api.main")
    assert "TRADING_MODE=paper" in request.environment
    assert "LIVE_EXECUTION_ENABLED=false" in request.environment
    assert "LIVE_TRADING_APPROVED=false" in request.environment


def test_source_drift_fails_before_native_start(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )
    (issued_capability.source_root / "bound.py").write_bytes(b"DRIFT\n")

    with pytest.raises(SourceDrift, match="source binding"):
        controller.start("job-api.start")

    assert script.calls == []


@pytest.mark.parametrize(
    "mutation",
    ("content", "replace", "extra", "missing", "mode"),
)
def test_child_file_drift_fails_before_spawn(
    issued_capability: SimpleNamespace,
    tmp_path: Path,
    mutation: str,
) -> None:
    authorities = _child_authorities(tmp_path, issued_capability)
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
        child_authorities=authorities,
    )
    credential = authorities.job_api_credentials / "database-host"
    original = credential.read_bytes()
    if mutation == "content":
        credential.write_bytes(b"in-place-drift")
        credential.chmod(0o600)
    elif mutation == "replace":
        replacement = credential.with_name("replacement")
        replacement.write_bytes(original)
        replacement.chmod(0o600)
        replacement.replace(credential)
    elif mutation == "extra":
        extra = credential.with_name("unexpected-credential")
        extra.write_bytes(b"unexpected")
        extra.chmod(0o600)
    elif mutation == "missing":
        credential.unlink()
    else:
        credential.chmod(0o644)

    with pytest.raises(SourceDrift, match="credential"):
        controller.start("job-api.start")

    assert script.calls == []


def test_credential_directory_path_replacement_preserves_bound_generation(
    issued_capability: SimpleNamespace,
    tmp_path: Path,
) -> None:
    authorities = _child_authorities(tmp_path, issued_capability)
    original = authorities.job_api_credentials
    displaced = original.with_name("job-api-credentials-displaced")
    original.rename(displaced)
    original.mkdir(mode=0o700)
    for name in _JOB_API_CREDENTIAL_NAMES:
        replacement = original / name
        replacement.write_text(f"replacement-{name}", encoding="utf-8")
        replacement.chmod(0o600)
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
        child_authorities=authorities,
    )

    controller.start("job-api.start")

    request = script.calls[0][1][0]
    assert request.credential_directory_fd is not None
    assert (
        os.fstat(request.credential_directory_fd).st_ino
        == displaced.stat().st_ino
    )
    assert "CREDENTIALS_DIRECTORY=/proc/self/fd/5" in request.environment
    assert request.credential_manifest.startswith(b"P6CM1")
    assert b"job-api-database-password" not in request.credential_manifest
    assert b"replacement-database-password" not in request.credential_manifest


@pytest.mark.parametrize(
    "failure",
    (
        CustodianTimeout("private timeout errno=110"),
        CustodianProtocolError("private protocol path=/authority"),
        KeyboardInterrupt("private interrupt credential"),
        SystemExit("private exit transcript"),
    ),
    ids=("timeout", "disconnect", "keyboard-interrupt", "system-exit"),
)
def test_internal_start_cleanup_recovers_proof_at_caller_assignment_opcode(
    issued_capability: SimpleNamespace,
    failure: BaseException,
) -> None:
    script = _ClientScript()
    client = script.bind()

    def interrupted_start(request: object) -> NativeOperationStatus:
        script.calls.append(("start", (request,)))
        script.recover_value = (script.status_value,)
        raise failure

    client.start = interrupted_start
    controller = Package6Controller(
        issued_capability,
        custodian_client=client,
    )

    with pytest.raises(RuntimeStartFailure) as raised:
        controller.start("job-api.start")

    assert str(raised.value) == RuntimeStartFailure.PUBLIC_MESSAGE
    assert raised.value.__cause__ is failure
    assert raised.value.cleanup_success is not None
    assert raised.value.owns_recovery_state is False
    assert [name for name, _arguments in script.calls] == [
        "start",
        "recover",
        "stop",
        "read_transcript",
        "read_transcript",
        "publish_bundle",
        "acknowledge",
    ]
    rendered = str(raised.value)
    assert "private" not in rendered
    assert "/authority" not in rendered
    assert "errno" not in rendered


def test_post_spawn_identity_setup_failure_retains_recovery_state(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    client = script.bind()
    original = KeyboardInterrupt("private call-to-assignment failure")

    def interrupted_start(request: object) -> NativeOperationStatus:
        script.calls.append(("start", (request,)))
        raise original

    def interrupted_recover() -> tuple[NativeOperationStatus, ...]:
        script.calls.append(("recover", ()))
        raise CustodianProtocolError("private recovery disconnect")

    client.start = interrupted_start
    client.recover = interrupted_recover
    controller = Package6Controller(
        issued_capability,
        custodian_client=client,
    )

    with pytest.raises(RuntimeStartFailure) as raised:
        controller.start("job-api.start")

    assert raised.value.__cause__ is original
    assert raised.value.owns_recovery_state is True
    assert raised.value.cleanup_attempts_consumed == 0
    snapshot = controller.snapshot_recovery_state(
        "job_api", "job-api.stop", 1
    )
    assert snapshot.native_operation_id == OPERATION_ID.hex()
    assert snapshot.state == "RECOVERY_REQUIRED"
    assert [name for name, _arguments in script.calls] == [
        "start",
        "recover",
    ]


def test_run_once_call_to_store_baseexception_has_no_orphan(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    client = script.bind()
    original = SystemExit("private run-once transcript")

    def interrupted_run_once(request: object) -> NativeOperationStatus:
        script.calls.append(("run_once", (request,)))
        script.recover_value = (script.run_once_value,)
        raise original

    client.run_once = interrupted_run_once
    controller = Package6Controller(
        issued_capability,
        custodian_client=client,
    )

    with pytest.raises(RuntimeStartFailure) as raised:
        controller.run_once("job-api.start")

    assert raised.value.__cause__ is original
    assert raised.value.cleanup_success is not None
    assert raised.value.owns_recovery_state is False
    assert [name for name, _arguments in script.calls] == [
        "run_once",
        "recover",
        "read_transcript",
        "read_transcript",
        "publish_bundle",
        "acknowledge",
    ]


def test_status_uses_only_native_operation_and_recovery_authority(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )
    controller.start("job-api.start")

    status = controller.status("job-api.start")

    assert status.state == "RUNNING"
    assert status.exit_code is None
    assert script.calls[-1] == (
        "status",
        (OPERATION_ID, RECOVERY_TOKEN),
    )


def test_status_identity_mismatch_does_not_replace_retained_authority(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )
    controller.start("job-api.start")
    good_status = script.status_value
    script.status_value = replace(
        good_status,
        recovery_token=bytes.fromhex("01" * 16),
    )

    with pytest.raises(EvidenceIncomplete, match="continuity"):
        controller.status("job-api.start")

    script.status_value = good_status
    controller.status("job-api.start")
    assert script.calls[-1] == (
        "status",
        (OPERATION_ID, RECOVERY_TOKEN),
    )


def test_stop_reads_bounded_native_transcript_metadata_and_retains_proof(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )
    controller.start("job-api.start")

    evidence = controller.stop("job-api.stop")
    recovered = controller.recover_completed_stop("job-api.stop")

    assert evidence.cleanup_proven is True
    assert evidence.state == "RESULT_RETAINED"
    assert evidence.stdout == TranscriptMetadata(
        sha256=hashlib.sha256(b"stdout evidence").hexdigest(),
        size=len(b"stdout evidence"),
        observed_size=len(b"stdout evidence"),
        truncated=False,
        eof=True,
    )
    assert evidence.stderr.sha256 == hashlib.sha256(
        b"stderr evidence"
    ).hexdigest()
    assert recovered is evidence
    assert not hasattr(evidence.stdout, "path")
    assert not hasattr(evidence.stdout, "content")
    assert [name for name, _arguments in script.calls].count(
        "read_transcript"
    ) == 2


def test_recover_rebuilds_completed_stop_through_native_recover_and_read(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )

    recovered = controller.recover_completed_stop("job-api.stop")

    assert isinstance(recovered, StopEvidence)
    assert recovered.cleanup_proven is True
    assert [name for name, _arguments in script.calls] == [
        "recover",
        "read_transcript",
        "read_transcript",
    ]


def test_publish_then_ack_is_exact_and_idempotent(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )
    controller.start("job-api.start")
    evidence = controller.stop("job-api.stop")

    receipt = controller.publish_evidence("job-api.stop", evidence)
    controller.acknowledge_stop("job-api.stop", evidence)
    controller.acknowledge_stop("job-api.stop", evidence)

    assert receipt.manifest_sha256 == PUBLICATION_DIGEST
    names = [name for name, _arguments in script.calls]
    assert names[-2:] == ["publish_bundle", "acknowledge"]
    publication_call = script.calls[-2][1]
    assert publication_call[:2] == (OPERATION_ID, RECOVERY_TOKEN)
    assert len(publication_call[2]) == 32
    assert script.calls[-1][1] == (
        OPERATION_ID,
        RECOVERY_TOKEN,
        PUBLICATION_DIGEST,
    )


def test_ack_before_native_publication_fails_closed(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )
    controller.start("job-api.start")
    evidence = controller.stop("job-api.stop")

    with pytest.raises(RuntimeError, match="publication"):
        controller.acknowledge_stop("job-api.stop", evidence)

    assert "acknowledge" not in [
        name for name, _arguments in script.calls
    ]


def test_run_once_uses_native_run_read_publish_ack_without_start_or_stop(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )

    bundle = controller.run_once("job-api.start")

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.process.cleanup_proven is True
    assert bundle.publication.manifest_sha256 == PUBLICATION_DIGEST
    assert [name for name, _arguments in script.calls] == [
        "run_once",
        "read_transcript",
        "read_transcript",
        "publish_bundle",
        "acknowledge",
    ]


def test_transcript_bytes_never_enter_public_dataclasses_or_repr(
    issued_capability: SimpleNamespace,
) -> None:
    script = _ClientScript()
    secret = b"PRIVATE_TRANSCRIPT_TOKEN"
    script.transcripts[TranscriptStream.STDOUT] = secret
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )
    controller.start("job-api.start")

    evidence = controller.stop("job-api.stop")

    assert secret.decode() not in repr(evidence)
    assert evidence.stdout.sha256 == hashlib.sha256(secret).hexdigest()


def test_controller_source_has_no_direct_target_custody_symbols() -> None:
    source = inspect.getsource(controller_module)
    forbidden = (
        "ctypes",
        "subprocess.Popen",
        "os.killpg",
        "os.waitpid",
        "os.getpgid",
        "process_group",
        "pidfd",
        "unlink(",
        "unlinkat",
    )

    assert all(term not in source for term in forbidden)


def test_wait_ready_enforces_global_deadline_around_delayed_response(
    issued_capability: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _ClientScript()
    now = [0.0]
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
        monotonic=lambda: now[0],
    )
    controller.start("job-api.start")

    original_status = script.status

    def delayed_status(
        operation_id: bytes, recovery_token: bytes
    ) -> NativeOperationStatus:
        value = original_status(operation_id, recovery_token)
        now[0] = 2.0
        return value

    controller._client.status = delayed_status
    contacted = False

    def forbidden_probe(_url: str, *, timeout: float) -> object:
        nonlocal contacted
        contacted = True
        raise AssertionError(f"probe exceeded total deadline: {timeout}")

    monkeypatch.setattr(controller_module, "urlopen", forbidden_probe)

    with pytest.raises(TimeoutError, match="readiness"):
        controller.wait_ready("job-api.start")

    assert contacted is False
    assert "JOB_API" in controller._active


def test_wait_ready_detects_native_exit_before_overall_timeout(
    issued_capability: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )
    controller.start("job-api.start")
    script.status_value = _native_status(
        state=OperationState.RESULT_RETAINED,
        authority_retained=True,
        exit_status=23,
    )
    contacted = False

    def forbidden_probe(_url: str, *, timeout: float) -> object:
        nonlocal contacted
        contacted = True
        raise AssertionError(f"exited child was probed: {timeout}")

    monkeypatch.setattr(controller_module, "urlopen", forbidden_probe)

    with pytest.raises(RuntimeError, match="exited before readiness"):
        controller.wait_ready("job-api.start")

    assert contacted is False
    assert "JOB_API" in controller._active


@pytest.mark.parametrize("response_case", ("wrong-status", "malformed"))
def test_wait_ready_wrong_status_or_malformed_response_fails_closed(
    issued_capability: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    response_case: str,
) -> None:
    script = _ClientScript()
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
    )
    controller.start("job-api.start")

    class Response:
        status = 503 if response_case == "wrong-status" else 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 16 * 1024 + 1
            return b"{" if response_case == "malformed" else b"{}"

    monkeypatch.setattr(
        controller_module,
        "urlopen",
        lambda _url, *, timeout: Response(),
    )

    with pytest.raises(RuntimeError, match="readiness response"):
        controller.wait_ready("job-api.start")

    assert "JOB_API" in controller._active


def test_wait_ready_accepts_real_response_beyond_half_second(
    issued_capability: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _ClientScript()
    now = [0.0]
    controller = Package6Controller(
        issued_capability,
        custodian_client=script.bind(),
        monotonic=lambda: now[0],
    )
    controller.start("job-api.start")

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 16 * 1024 + 1
            now[0] = 0.75
            return b'{"data":{"status":"READY"}}'

    observed: list[tuple[str, float]] = []

    def delayed_probe(url: str, *, timeout: float) -> Response:
        observed.append((url, timeout))
        return Response()

    monkeypatch.setattr(controller_module, "urlopen", delayed_probe)

    readiness = controller.wait_ready("job-api.start")

    assert readiness.status == "READY"
    assert observed == [("http://127.0.0.1:8401/health/ready", 1.0)]


def _exercise_exceptional_chain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    worker_failures: int,
    job_api_failures: int,
) -> tuple[object, list[str], BaseException]:
    from services.paper_runtime import integration as integration_module
    from services.paper_runtime.integration import (
        RuntimeChainFailure,
        run_approved_runtime_chain,
    )

    primary = KeyboardInterrupt("private chain primary credential")
    cleanup_order: list[str] = []
    counts = {"worker.stop": 0, "job-api.stop": 0}
    limits = {
        "worker.stop": worker_failures,
        "job-api.stop": job_api_failures,
    }

    def identity(
        operation_id: str,
        component: str,
        native_byte: int,
    ) -> TrackedProcessIdentity:
        native_id = bytes((native_byte,)) * 16
        return TrackedProcessIdentity(
            operation_id=operation_id,
            component=component,
            native_operation_id=native_id.hex(),
            recovery_token=bytes((native_byte + 1,)).hex() * 16,
            state="RUNNING",
            environment=SpawnEnvironmentEvidence(
                component=component,
                operation_id=operation_id,
                keys=(),
            ),
        )

    def stop_evidence(operation_id: str) -> StopEvidence:
        worker = operation_id == "worker.stop"
        native_id = bytes((0x22 if worker else 0x11,)) * 16
        token = bytes((0x23 if worker else 0x12,)) * 16
        empty = TranscriptMetadata(
            sha256=hashlib.sha256(b"").hexdigest(),
            size=0,
            observed_size=0,
            truncated=False,
            eof=True,
        )
        return StopEvidence(
            operation_id=operation_id,
            component="WORKER" if worker else "JOB_API",
            native_operation_id=native_id.hex(),
            recovery_token=token.hex(),
            state="RESULT_RETAINED",
            exit_code=0,
            cleanup_proven=True,
            stdout=empty,
            stderr=empty,
        )

    class Controller:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self, operation_id: str) -> TrackedProcessIdentity:
            if operation_id == "job-api.start":
                return identity(operation_id, "JOB_API", 0x11)
            return identity(operation_id, "WORKER", 0x22)

        def wait_ready(self, operation_id: str) -> object:
            return controller_module.ReadinessEvidence(
                operation_id=operation_id,
                native_operation_id=(bytes((0x11,)) * 16).hex(),
                attempts=1,
                status="READY",
            )

        def status(self, _operation_id: str) -> object:
            raise primary

        def stop(self, operation_id: str) -> StopEvidence:
            cleanup_order.append(operation_id)
            counts[operation_id] += 1
            if counts[operation_id] <= limits[operation_id]:
                raise SystemExit("private cleanup transcript")
            return stop_evidence(operation_id)

        def recover_completed_stop(
            self, _operation_id: str
        ) -> None:
            return None

        def snapshot_recovery_state(
            self,
            component: str,
            operation_id: str,
            cleanup_attempts_consumed: int,
        ) -> object:
            worker = component == "worker"
            return controller_module.RuntimeRecoveryState(
                component=component,
                operation_id=operation_id,
                cleanup_attempts_consumed=cleanup_attempts_consumed,
                native_operation_id=(
                    bytes((0x22 if worker else 0x11,)) * 16
                ).hex(),
                state="RECOVERY_REQUIRED",
                cleanup_proven=False,
                stdout=None,
                stderr=None,
            )

        def publish_evidence(
            self,
            _operation_id: str,
            evidence: StopEvidence,
        ) -> NativeBundleReceipt:
            digest = bytes.fromhex("33" * 32)
            return NativeBundleReceipt(
                operation=_native_status(
                    operation_id=bytes.fromhex(
                        evidence.native_operation_id
                    ),
                    bundle_committed=True,
                    publication_sha256=digest,
                ),
                manifest_sha256=digest,
            )

        def acknowledge_stop(
            self,
            _operation_id: str,
            _evidence: StopEvidence,
        ) -> None:
            return None

    capability = SimpleNamespace(
        listener=SimpleNamespace(host="127.0.0.1", port=8401),
        request=SimpleNamespace(
            job_type="SNAPSHOT",
            idempotency_key="foundation:test",
            actor="FOUNDATION_VALIDATION",
            expected_job_count=1,
        ),
        operation_timeout_seconds=1,
    )
    child_authorities = SimpleNamespace(
        job_api_credentials=Path("/private/not-read")
    )
    first_job = {
        "job_id": "job-1",
        "actor": {
            "actor_type": "OPERATOR",
            "actor_id": "foundation-validation",
        },
    }
    responses = iter(
        (
            (201, {"data": {"outcome": "ENQUEUED", "job": first_job}}),
            (
                200,
                {"data": {"outcome": "DEDUPLICATED", "job": first_job}},
            ),
        )
    )
    monkeypatch.setattr(integration_module, "is_issued_capability", lambda _: True)
    monkeypatch.setattr(integration_module, "Package6Controller", Controller)
    monkeypatch.setattr(
        integration_module,
        "_credential",
        lambda *_args: "not-used-after-primary",
    )
    monkeypatch.setattr(
        integration_module,
        "_request_json",
        lambda *_args, **_kwargs: next(responses),
    )

    with pytest.raises(RuntimeChainFailure) as raised:
        run_approved_runtime_chain(
            capability,
            child_authorities,
            custodian_client=CustodianClient.__new__(CustodianClient),
        )
    return raised.value, cleanup_order, primary


def test_chain_aggregates_baseexceptions_and_completes_reverse_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure, cleanup_order, primary = _exercise_exceptional_chain(
        monkeypatch,
        worker_failures=2,
        job_api_failures=2,
    )

    assert str(failure) == "paper runtime chain failed safely"
    assert failure.primary_error is primary
    assert failure.__cause__ is primary
    assert cleanup_order == [
        "worker.stop",
        "worker.stop",
        "job-api.stop",
        "job-api.stop",
    ]
    assert [record.component for record in failure.recovery_owner.records] == [
        "worker",
        "job_api",
    ]


def test_later_cleanup_success_preserves_prior_failure_without_recovery_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure, cleanup_order, primary = _exercise_exceptional_chain(
        monkeypatch,
        worker_failures=1,
        job_api_failures=0,
    )

    assert failure.primary_error is primary
    assert cleanup_order == [
        "worker.stop",
        "worker.stop",
        "job-api.stop",
    ]
    assert [item.component for item in failure.cleanup_successes] == [
        "worker",
        "job_api",
    ]
    assert failure.cleanup_failures
    assert failure.recovery_owner is None
