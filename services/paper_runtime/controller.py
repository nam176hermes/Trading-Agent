"""Paper-only Package 6 adapter for the native C11 custody authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import time
from urllib.error import HTTPError
from urllib.request import urlopen
import weakref

from packages.engine_contracts import (
    CURRENT_SCHEMA_VERSION,
    EngineCommandEnvelope,
    EngineSessionIdentityV1,
    RunBacktest,
    canonical_json_bytes,
)
from packages.nautilus_runtime_contracts.events import P1_EVENT_SCHEMA
from packages.nautilus_runtime_contracts.paper import PAPER_PROTOCOL_SCHEMA
from scripts.validate_package6_runtime_approval import (
    PACKAGE6_JOB_API_ENVIRONMENT_KEYS,
    PACKAGE6_WORKER_ENVIRONMENT_KEYS,
    ValidatedPackage6Capability,
    is_issued_capability,
)
from services.job_worker.p1_engine_spawn import (
    P1EngineClosureAttestation,
    validate_p1_engine_closure_attestation,
)

from .custodian_client import (
    CustodianAttestation,
    CustodianClient,
    NativeBundleReceipt,
    NativeOperationRequest,
    NativeOperationStatus,
    OperationState,
    TranscriptStream,
)
from .nautilus_session import (
    EngineSessionPort,
    NautilusPaperSession,
    NautilusSessionResult,
    _issue_engine_session_port,
)
from .nautilus_process import (
    NautilusPaperProcess,
    is_attested_nautilus_paper_process,
)


_NATIVE_OPERATION_AUTHORITY = (
    "START",
    "STOP",
    "STATUS",
    "RECOVER",
    "RUN_ONCE",
    "READ_TRANSCRIPT",
    "PUBLISH_BUNDLE",
    "ACK",
)
_UNKNOWN_EXIT_STATUS = -(2**31)


def _custodian_authority_sha256(attestation: CustodianAttestation) -> str:
    return hashlib.sha256(canonical_json_bytes(asdict(attestation))).hexdigest()


def issue_engine_session_port(
    capability: ValidatedPackage6Capability,
    *,
    custodian_client: CustodianClient,
    closure_attestation: P1EngineClosureAttestation,
    request: EngineCommandEnvelope,
    process: NautilusPaperProcess,
) -> EngineSessionPort:
    """Issue one closure- and custody-bound interactive P1 child handle."""

    if (
        not is_issued_capability(capability)
        or type(custodian_client) is not CustodianClient
        or not Package6Controller._attestation_matches(
            capability, custodian_client.attestation
        )
        or type(request) is not EngineCommandEnvelope
        or type(request.payload) is not RunBacktest
        or not is_attested_nautilus_paper_process(process)
    ):
        raise TypeError("attested paper custody authority is required")
    closure = validate_p1_engine_closure_attestation(closure_attestation)
    if not process.matches_authority(closure, request):
        raise TypeError("exact P1 paper process authority is required")
    return _issue_engine_session_port(
        identity=EngineSessionIdentityV1(
            runtime_family=closure.runtime_family,
            engine_version=closure.engine_version,
            engine_upstream_commit=closure.engine_upstream_commit,
            closure_digest=closure.closure_sha256,
            request_protocol=CURRENT_SCHEMA_VERSION,
            event_schema=P1_EVENT_SCHEMA,
            paper_schema=PAPER_PROTOCOL_SCHEMA,
        ),
        capability_sha256=capability.approval_sha256,
        custodian_authority_sha256=_custodian_authority_sha256(
            custodian_client.attestation
        ),
        process_authority_sha256=process.child_identity_sha256,
        paper_source_sha256=process.paper_source_sha256,
        session_id=request.engine_run_id,
        owner_id=request.causation_id,
        exchange=process.exchange,
        close_input=process.close_input,
        abort=process.abort,
        is_running=process.is_running,
    )


class SourceDrift(RuntimeError):
    """Candidate identity changed before the native request."""


class EvidenceIncomplete(RuntimeError):
    """Native cleanup or publication evidence is incomplete."""


@dataclass(frozen=True, slots=True, weakref_slot=True)
class RuntimeChildAuthorities:
    """Private credential-directory references used only to build child env."""

    job_api_credentials: Path
    worker_credentials: Path
    capability_sha256: str
    _credential_pins: tuple[tuple[object, ...], ...] = field(repr=False)
    _credential_authorities: tuple[
        _CredentialDirectoryAuthority, ...
    ] = field(repr=False)


_ISSUED_CHILD_AUTHORITIES: weakref.WeakSet[RuntimeChildAuthorities] = (
    weakref.WeakSet()
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
_MAX_CREDENTIAL_BYTES = 4096


def _private_directory(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{label} authority is unavailable") from error
    if (
        resolved != path
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError(f"{label} authority is unsafe")
    return path


def _credential_metadata(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


class _CredentialDirectoryAuthority:
    """One process-local owner for a pathname-independent directory generation."""

    __slots__ = ("descriptor", "manifest", "pins")

    def __init__(
        self,
        descriptor: int,
        pins: tuple[object, ...],
        manifest: bytes,
    ) -> None:
        self.descriptor = descriptor
        self.pins = pins
        self.manifest = manifest

    def __del__(self) -> None:
        descriptor = self.descriptor
        self.descriptor = -1
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _inspect_credential_directory(
    directory_descriptor: int,
    required_names: tuple[str, ...],
) -> tuple[tuple[object, ...], bytes]:
    """Bind exact leaf metadata and digests without retaining credential values."""

    directory_before = os.fstat(directory_descriptor)
    if (
        not stat.S_ISDIR(directory_before.st_mode)
        or directory_before.st_uid != os.geteuid()
        or directory_before.st_gid != os.getegid()
        or stat.S_IMODE(directory_before.st_mode) != 0o700
        or tuple(sorted(os.listdir(directory_descriptor)))
        != tuple(sorted(required_names))
    ):
        raise ValueError
    pins: list[tuple[object, ...]] = []
    entries = bytearray()
    for name in sorted(required_names):
        descriptor = -1
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory_descriptor,
            )
            before = os.fstat(descriptor)
            mode = stat.S_IMODE(before.st_mode)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_gid != os.getegid()
                or mode not in {0o400, 0o600}
                or before.st_nlink != 1
                or not 1 <= before.st_size <= _MAX_CREDENTIAL_BYTES
            ):
                raise ValueError
            digest = hashlib.sha256()
            observed_size = 0
            while observed_size <= _MAX_CREDENTIAL_BYTES:
                chunk = os.read(
                    descriptor,
                    min(4096, _MAX_CREDENTIAL_BYTES + 1 - observed_size),
                )
                if not chunk:
                    break
                digest.update(chunk)
                observed_size += len(chunk)
            after = os.fstat(descriptor)
            path_after = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            metadata = _credential_metadata(before)
            leaf_digest = digest.digest()
            if (
                _credential_metadata(after) != metadata
                or _credential_metadata(path_after) != metadata
                or observed_size != before.st_size
                or observed_size > _MAX_CREDENTIAL_BYTES
            ):
                raise ValueError
            pins.append((name, *metadata, leaf_digest))
            encoded_name = name.encode("ascii", "strict")
            entries.extend(struct.pack(">I", len(encoded_name)))
            entries.extend(encoded_name)
            entries.extend(
                struct.pack(
                    ">QQIIIQQqq",
                    before.st_dev,
                    before.st_ino,
                    before.st_uid,
                    before.st_gid,
                    before.st_mode,
                    before.st_nlink,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
            )
            entries.extend(leaf_digest)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    directory_after = os.fstat(directory_descriptor)
    if (
        _credential_metadata(directory_after)
        != _credential_metadata(directory_before)
        or tuple(sorted(os.listdir(directory_descriptor)))
        != tuple(sorted(required_names))
    ):
        raise ValueError
    manifest = (
        b"P6CM1"
        + struct.pack(
            ">IQQIII",
            len(pins),
            directory_before.st_dev,
            directory_before.st_ino,
            directory_before.st_uid,
            directory_before.st_gid,
            directory_before.st_mode,
        )
        + bytes(entries)
    )
    return (
        (
            directory_before.st_dev,
            directory_before.st_ino,
            tuple(pins),
        ),
        manifest,
    )


def _pin_credential_directory(
    path: Path,
    required_names: tuple[str, ...],
) -> _CredentialDirectoryAuthority:
    """Open and retain one exact credential-directory generation."""

    directory_path = _private_directory(path, "runtime credential directory")
    directory_descriptor = -1
    try:
        directory_descriptor = os.open(
            directory_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        directory_before = os.fstat(directory_descriptor)
        path_before = directory_path.lstat()
        if (
            _credential_metadata(directory_before)
            != _credential_metadata(path_before)
        ):
            raise ValueError
        pins, manifest = _inspect_credential_directory(
            directory_descriptor, required_names
        )
        directory_after = os.fstat(directory_descriptor)
        path_after = directory_path.lstat()
        if (
            _credential_metadata(directory_after)
            != _credential_metadata(directory_before)
            or _credential_metadata(path_after)
            != _credential_metadata(directory_before)
        ):
            raise ValueError
        authority = _CredentialDirectoryAuthority(
            directory_descriptor,
            pins,
            manifest,
        )
        directory_descriptor = -1
        return authority
    except (OSError, ValueError) as error:
        raise ValueError(
            "runtime credential directory policy is invalid"
        ) from error
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def issue_runtime_child_authorities(
    capability: ValidatedPackage6Capability,
    *,
    job_api_credentials: Path,
    worker_credentials: Path,
) -> RuntimeChildAuthorities:
    """Issue one candidate-bound pair of private credential directories."""

    if not is_issued_capability(capability):
        raise TypeError("validated Package 6 capability is required")
    job_api_path = Path(job_api_credentials)
    worker_path = Path(worker_credentials)
    credential_pins = (
        _pin_credential_directory(
            job_api_path,
            _JOB_API_CREDENTIAL_NAMES,
        ),
        _pin_credential_directory(
            worker_path,
            _WORKER_CREDENTIAL_NAMES,
        ),
    )
    value = RuntimeChildAuthorities(
        job_api_credentials=job_api_path,
        worker_credentials=worker_path,
        capability_sha256=capability.approval_sha256,
        _credential_pins=tuple(item.pins for item in credential_pins),
        _credential_authorities=credential_pins,
    )
    _ISSUED_CHILD_AUTHORITIES.add(value)
    return value


@dataclass(frozen=True, slots=True)
class SpawnEnvironmentEvidence:
    component: str
    operation_id: str
    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrackedProcessIdentity:
    """Opaque native operation identity; it is not a PID authority."""

    operation_id: str
    component: str
    native_operation_id: str
    recovery_token: str
    state: str
    environment: SpawnEnvironmentEvidence


@dataclass(frozen=True, slots=True)
class TranscriptMetadata:
    sha256: str
    size: int
    observed_size: int
    truncated: bool
    eof: bool


@dataclass(frozen=True, slots=True)
class ProcessStatus:
    operation_id: str
    component: str
    native_operation_id: str
    state: str
    exit_code: int | None
    authority_retained: bool
    bundle_committed: bool


@dataclass(frozen=True, slots=True)
class StopEvidence:
    operation_id: str
    component: str
    native_operation_id: str
    recovery_token: str
    state: str
    exit_code: int
    cleanup_proven: bool
    stdout: TranscriptMetadata
    stderr: TranscriptMetadata


@dataclass(frozen=True, slots=True)
class ReadinessEvidence:
    operation_id: str
    native_operation_id: str
    attempts: int
    status: str


@dataclass(frozen=True, slots=True)
class RuntimeRecoveryState:
    component: str
    operation_id: str
    cleanup_attempts_consumed: int
    native_operation_id: str
    state: str
    cleanup_proven: bool
    stdout: TranscriptMetadata | None
    stderr: TranscriptMetadata | None


@dataclass(frozen=True, slots=True)
class RuntimeCleanupFailure:
    component: str
    operation_id: str
    attempt: int
    error: BaseException


class IncompleteCleanupProof(RuntimeError):
    PUBLIC_MESSAGE = "runtime cleanup proof is incomplete"

    def __init__(self, evidence: StopEvidence) -> None:
        super().__init__(self.PUBLIC_MESSAGE)
        self.evidence = evidence


class RuntimeStartFailure(RuntimeError):
    PUBLIC_MESSAGE = "paper runtime start failed safely"
    MAX_CLEANUP_ATTEMPTS = 2

    def __init__(
        self,
        *,
        component: str,
        operation_id: str,
        stop_operation_id: str,
        cleanup_failures: tuple[RuntimeCleanupFailure, ...],
        cleanup_attempts_consumed: int,
        owns_recovery_state: bool,
        cleanup_success: StopEvidence | None = None,
    ) -> None:
        super().__init__(self.PUBLIC_MESSAGE)
        self.component = component
        self.operation_id = operation_id
        self.stop_operation_id = stop_operation_id
        self.cleanup_failures = cleanup_failures
        self.cleanup_attempts_consumed = cleanup_attempts_consumed
        self.owns_recovery_state = owns_recovery_state
        self.cleanup_success = cleanup_success
        self.remaining_stop_attempts = (
            max(0, self.MAX_CLEANUP_ATTEMPTS - cleanup_attempts_consumed)
            if owns_recovery_state
            else 0
        )


@dataclass(frozen=True, slots=True)
class ProcessEvidence:
    """Compatibility name for one native-completed operation proof."""

    stop: StopEvidence


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    root: Path
    process: StopEvidence
    publication: NativeBundleReceipt


def _empty_transcript() -> TranscriptMetadata:
    return TranscriptMetadata(
        sha256=hashlib.sha256(b"").hexdigest(),
        size=0,
        observed_size=0,
        truncated=False,
        eof=True,
    )


def _operation_key(capability: object, component: str) -> str:
    starts = [
        operation.operation_id
        for operation in capability.operations.values()
        if operation.component == component and operation.action == "START"
    ]
    if len(starts) != 1:
        raise RuntimeError("component native operation authority is invalid")
    return starts[0]


def _native_operation_id(
    capability: ValidatedPackage6Capability, component: str
) -> bytes:
    start_id = _operation_key(capability, component)
    material = (
        b"PACKAGE6_NATIVE_OPERATION_V1\x00"
        + bytes.fromhex(capability.approval_sha256)
        + start_id.encode("ascii")
    )
    value = hashlib.sha256(material).digest()[:16]
    if value == bytes(16):  # pragma: no cover - cryptographic impossibility
        raise RuntimeError("native operation identity is unavailable")
    return value


def _publication_id(
    capability: ValidatedPackage6Capability, operation_id: bytes
) -> bytes:
    material = (
        b"PACKAGE6_NATIVE_PUBLICATION_V1\x00"
        + operation_id
        + bytes.fromhex(capability.source_commit)
        + bytes.fromhex(capability.source_tree)
        + bytes.fromhex(capability.fixture_sha256)
        + bytes.fromhex(capability.authority_digests["stage"])
    )
    value = hashlib.sha256(material).digest()
    if value == bytes(32):  # pragma: no cover - cryptographic impossibility
        raise RuntimeError("native publication identity is unavailable")
    return value


class Package6Controller:
    """Delegate every Package 6 target lifecycle action to native custody."""

    def __init__(
        self,
        capability: ValidatedPackage6Capability,
        *,
        custodian_client: CustodianClient | None = None,
        child_authorities: RuntimeChildAuthorities | None = None,
        nautilus_session: NautilusPaperSession | None = None,
        monotonic=time.monotonic,
    ) -> None:
        if not is_issued_capability(capability):
            raise TypeError("validated Package 6 capability is required")
        if (
            type(custodian_client) is not CustodianClient
            or not self._attestation_matches(
                capability, custodian_client.attestation
            )
        ):
            raise TypeError("attested native custodian client is required")
        if child_authorities is not None and (
            child_authorities not in _ISSUED_CHILD_AUTHORITIES
            or child_authorities.capability_sha256
            != capability.approval_sha256
        ):
            raise TypeError("issued runtime child authorities are required")
        if nautilus_session is not None and (
            type(nautilus_session) is not NautilusPaperSession
            or not nautilus_session.matches_controller(
                capability.approval_sha256,
                _custodian_authority_sha256(custodian_client.attestation),
            )
        ):
            raise TypeError("custody-bound Nautilus paper session is required")
        self._capability = capability
        self._client = custodian_client
        self._child_authorities = child_authorities
        self._monotonic = monotonic
        self._active: dict[str, NativeOperationStatus] = {}
        self._pending: dict[str, bytes] = {}
        self._completed: dict[str, StopEvidence] = {}
        self._receipts: dict[str, NativeBundleReceipt] = {}
        self._acknowledged: set[str] = set()
        self._nautilus_session = nautilus_session

    def execute_nautilus(
        self,
        raw: bytes,
        *,
        expected_checkpoint_sha256: str,
    ) -> NautilusSessionResult:
        """Dispatch one P1 command through the controller's sole paper session."""

        if self._nautilus_session is None:
            raise RuntimeError("Nautilus paper session authority is unavailable")
        return self._nautilus_session.execute(
            raw, expected_checkpoint_sha256=expected_checkpoint_sha256
        )

    @staticmethod
    def _attestation_matches(
        capability: ValidatedPackage6Capability,
        attestation: CustodianAttestation,
    ) -> bool:
        authority = getattr(capability, "custodian", None)
        if authority is None:
            return False
        expected = {
            "helper_binary_sha256": authority.helper_binary_sha256,
            "native_source_set_sha256": authority.native_source_set_sha256,
            "protocol_version": authority.protocol_version,
            "protocol_features": authority.protocol_features,
            "endpoint_authority": authority.endpoint_authority,
            "candidate_commit": capability.source_commit,
            "candidate_tree": capability.source_tree,
            "stage_sha256": capability.authority_digests["stage"],
            "fixture_sha256": capability.fixture_sha256,
            "mode": authority.mode,
            "live_execution_approved": False,
            "live_trading_approved": False,
        }
        return (
            tuple(authority.operations) == _NATIVE_OPERATION_AUTHORITY
            and all(
                getattr(attestation, name, None) == value
                for name, value in expected.items()
            )
            and attestation.peer_uid == os.geteuid()
            and attestation.peer_gid == os.getegid()
            and attestation.peer_pid > 0
        )

    def _operation(self, operation_id: str, action: str):
        operation = self._capability.operations.get(operation_id)
        if operation is None or operation.action != action:
            raise ValueError(f"exact approved {action} operation is required")
        return operation

    def _validate_bindings(self) -> None:
        root = self._capability.source_root
        for relative, expected in self._capability.source_bindings:
            path = root / relative
            try:
                info = path.lstat()
                raw = path.read_bytes()
                resolved = path.resolve(strict=True)
                resolved.relative_to(root.resolve(strict=True))
            except (OSError, ValueError) as error:
                raise SourceDrift(
                    "source binding is unavailable before native request"
                ) from error
            if (
                resolved != path
                or stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o022
                or hashlib.sha256(raw).hexdigest() != expected
            ):
                raise SourceDrift(
                    "source binding drift detected before native request"
                )

    def _attest_child_credentials(self) -> None:
        child = self._child_authorities
        if child is None:
            return
        try:
            observed = tuple(
                _inspect_credential_directory(
                    authority.descriptor,
                    required_names,
                )
                for authority, required_names in zip(
                    child._credential_authorities,
                    (
                    _JOB_API_CREDENTIAL_NAMES,
                    _WORKER_CREDENTIAL_NAMES,
                    ),
                    strict=True,
                )
            )
        except (OSError, ValueError) as error:
            raise SourceDrift(
                "runtime credential authority is unavailable"
            ) from error
        if tuple(item[0] for item in observed) != child._credential_pins:
            raise SourceDrift(
                "runtime credential authority changed before native request"
            )
        if tuple(item[1] for item in observed) != tuple(
            authority.manifest
            for authority in child._credential_authorities
        ):
            raise SourceDrift(
                "runtime credential authority changed before native request"
            )

    def _environment(self, operation: object) -> tuple[str, ...]:
        values = {
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
            "LIVE_TRADING_ENABLED": "false",
            "PATH": "/usr/bin:/bin",
            "TRADING_PACKAGE6_APPROVAL_SHA256": (
                self._capability.approval_sha256
            ),
            "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH": str(
                self._capability.staging_material.activation_path
            ),
            "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH": str(
                self._capability.staging_material.authority_path
            ),
            "TRADING_PACKAGE6_STAGING_SCOPE": "PACKAGE6_STAGING_V2",
            "TRADING_MODE": "paper",
            "TZ": "UTC",
        }
        if operation.component == "WORKER":
            values["TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH"] = str(
                self._capability.fixture.path
            )
        if self._child_authorities is not None:
            values["CREDENTIALS_DIRECTORY"] = "/proc/self/fd/5"
            expected_keys = (
                PACKAGE6_JOB_API_ENVIRONMENT_KEYS
                if operation.component == "JOB_API"
                else PACKAGE6_WORKER_ENVIRONMENT_KEYS
            )
            if tuple(sorted(values)) != expected_keys:
                raise SourceDrift(
                    "runtime child environment contract changed"
                )
        return tuple(f"{key}={values[key]}" for key in sorted(values))

    def _request(self, operation: object) -> NativeOperationRequest:
        try:
            executable = Path(operation.argv[0]).relative_to(
                operation.cwd
            ).as_posix()
        except (IndexError, ValueError) as error:
            raise SourceDrift(
                "approved executable is outside native source authority"
            ) from error
        credential_authority = None
        if self._child_authorities is not None:
            credential_authority = self._child_authorities._credential_authorities[
                0 if operation.component == "JOB_API" else 1
            ]
        return NativeOperationRequest(
            operation_id=_native_operation_id(
                self._capability, operation.component
            ),
            executable=executable,
            executable_sha256=bytes.fromhex(
                operation.executable_sha256
            ),
            argv=tuple(operation.argv),
            environment=self._environment(operation),
            credential_directory_fd=(
                None
                if credential_authority is None
                else credential_authority.descriptor
            ),
            credential_manifest=(
                b""
                if credential_authority is None
                else credential_authority.manifest
            ),
        )

    def start(self, operation_id: str) -> TrackedProcessIdentity:
        operation = self._operation(operation_id, "START")
        if (
            operation.component in self._active
            or operation.component in self._pending
            or operation.component in self._completed
        ):
            raise RuntimeError(
                "component already has native or retained custody"
            )
        self._validate_bindings()
        request = self._request(operation)
        self._attest_child_credentials()
        self._pending[operation.component] = request.operation_id
        status: NativeOperationStatus | None = None
        try:
            status = self._client.start(request)
            if (
                status.operation_id != request.operation_id
                or status.recovery_token == bytes(16)
                or not status.authority_retained
                or status.state
                not in {
                    OperationState.RUNNING,
                    OperationState.RESULT_RETAINED,
                    OperationState.RECOVERY_REQUIRED,
                }
            ):
                raise EvidenceIncomplete("native START proof is incomplete")
            self._active[operation.component] = status
            self._pending.pop(operation.component, None)
            environment = self._environment(operation)
            return TrackedProcessIdentity(
                operation_id=operation.operation_id,
                component=operation.component,
                native_operation_id=status.operation_id.hex(),
                recovery_token=status.recovery_token.hex(),
                state=status.state.name,
                environment=SpawnEnvironmentEvidence(
                    component=operation.component,
                    operation_id=operation.operation_id,
                    keys=tuple(
                        item.partition("=")[0] for item in environment
                    ),
                ),
            )
        except BaseException as error:
            self._raise_request_failure(
                operation,
                request,
                error,
                observed_status=status,
            )
            raise AssertionError("request failure reconciliation returned")

    def _stop_operation(self, component: str) -> object:
        matches = [
            item
            for item in self._capability.operations.values()
            if item.component == component and item.action == "STOP"
        ]
        if len(matches) != 1:
            raise RuntimeError("component STOP authority is invalid")
        return matches[0]

    def _recover_operation_status(
        self,
        component: str,
        expected_operation_id: bytes,
    ) -> NativeOperationStatus | None:
        matches = [
            status
            for status in self._client.recover()
            if status.operation_id == expected_operation_id
        ]
        if len(matches) > 1:
            raise EvidenceIncomplete("native recovery identity is ambiguous")
        if not matches:
            self._pending.pop(component, None)
            return None
        status = matches[0]
        self._pending.pop(component, None)
        if status.state not in {
            OperationState.ACKNOWLEDGED,
            OperationState.RESULT_RETAINED,
        }:
            self._active[component] = status
        return status

    def _raise_request_failure(
        self,
        operation: object,
        request: NativeOperationRequest,
        original_error: BaseException,
        *,
        observed_status: NativeOperationStatus | None,
    ) -> None:
        """Bound recovery after a native request may have taken effect."""

        stop_operation = self._stop_operation(operation.component)
        failures: list[RuntimeCleanupFailure] = []
        cleanup_success: StopEvidence | None = self._completed.get(
            operation.component
        )
        attempts_consumed = 0
        status = observed_status
        if status is None or (
            status.operation_id != request.operation_id
            or status.recovery_token == bytes(16)
        ):
            try:
                status = self._recover_operation_status(
                    operation.component,
                    request.operation_id,
                )
            except BaseException as error:
                failures.append(
                    RuntimeCleanupFailure(
                        operation.component.lower(),
                        stop_operation.operation_id,
                        0,
                        error,
                    )
                )
                status = None
        else:
            self._pending.pop(operation.component, None)

        if cleanup_success is None and status is not None:
            if status.state is OperationState.ACKNOWLEDGED:
                self._acknowledged.add(operation.component)
                self._active.pop(operation.component, None)
            elif status.state is OperationState.RESULT_RETAINED:
                try:
                    cleanup_success = self._stop_evidence(
                        stop_operation,
                        status,
                    )
                except BaseException as error:
                    failures.append(
                        RuntimeCleanupFailure(
                            operation.component.lower(),
                            stop_operation.operation_id,
                            0,
                            error,
                        )
                    )
                    self._active[operation.component] = status
                else:
                    self._completed[operation.component] = cleanup_success
                    self._active.pop(operation.component, None)
            else:
                self._active[operation.component] = status

        while (
            cleanup_success is None
            and operation.component in self._active
            and attempts_consumed < RuntimeStartFailure.MAX_CLEANUP_ATTEMPTS
        ):
            attempts_consumed += 1
            try:
                cleanup_success = self.stop(stop_operation.operation_id)
            except BaseException as error:
                failures.append(
                    RuntimeCleanupFailure(
                        operation.component.lower(),
                        stop_operation.operation_id,
                        attempts_consumed,
                        error,
                    )
                )
                try:
                    recovered = self.recover_completed_stop(
                        stop_operation.operation_id
                    )
                except BaseException as recovery_error:
                    failures.append(
                        RuntimeCleanupFailure(
                            operation.component.lower(),
                            stop_operation.operation_id,
                            attempts_consumed,
                            recovery_error,
                        )
                    )
                else:
                    if recovered is not None:
                        cleanup_success = recovered

        publication_proven = operation.component in self._acknowledged
        if cleanup_success is not None and not publication_proven:
            for _attempt in range(2):
                try:
                    self.publish_evidence(
                        stop_operation.operation_id,
                        cleanup_success,
                    )
                    self.acknowledge_stop(
                        stop_operation.operation_id,
                        cleanup_success,
                    )
                except BaseException as error:
                    failures.append(
                        RuntimeCleanupFailure(
                            operation.component.lower(),
                            stop_operation.operation_id,
                            max(1, attempts_consumed),
                            error,
                        )
                    )
                else:
                    publication_proven = True
                    break

        owns_recovery_state = (
            operation.component in self._pending
            or operation.component in self._active
            or (
                cleanup_success is not None
                and not publication_proven
            )
        )
        failure = RuntimeStartFailure(
            component=operation.component.lower(),
            operation_id=operation.operation_id,
            stop_operation_id=stop_operation.operation_id,
            cleanup_failures=tuple(failures),
            cleanup_attempts_consumed=attempts_consumed,
            owns_recovery_state=owns_recovery_state,
            cleanup_success=cleanup_success,
        )
        raise failure from original_error

    @staticmethod
    def _exit_code(status: NativeOperationStatus) -> int | None:
        return (
            None
            if status.exit_status == _UNKNOWN_EXIT_STATUS
            else status.exit_status
        )

    @staticmethod
    def _validate_continuity(
        prior: NativeOperationStatus,
        current: NativeOperationStatus,
        *,
        permitted_states: frozenset[OperationState],
        publication_may_appear: bool = False,
    ) -> None:
        publication_matches = (
            current.publication_sha256 == prior.publication_sha256
            or (
                publication_may_appear
                and prior.publication_sha256 == bytes(32)
                and current.publication_sha256 != bytes(32)
            )
        )
        if (
            current.operation_id != prior.operation_id
            or current.recovery_token != prior.recovery_token
            or current.request_sha256 != prior.request_sha256
            or current.executable_sha256 != prior.executable_sha256
            or not publication_matches
            or current.state not in permitted_states
        ):
            raise EvidenceIncomplete(
                "native operation continuity proof is incomplete"
            )

    def status(self, operation_id: str) -> ProcessStatus:
        operation = self._operation(operation_id, "START")
        retained = self._active.get(operation.component)
        if retained is None and operation.component in self._pending:
            retained = self._recover_operation_status(
                operation.component,
                self._pending[operation.component],
            )
        if retained is None:
            raise RuntimeError("status requires native recovery authority")
        status = self._client.status(
            retained.operation_id, retained.recovery_token
        )
        self._validate_continuity(
            retained,
            status,
            permitted_states=frozenset(
                {
                    retained.state,
                    OperationState.RESULT_RETAINED,
                    OperationState.RECOVERY_REQUIRED,
                    OperationState.ACKNOWLEDGED,
                }
            ),
        )
        self._active[operation.component] = status
        return ProcessStatus(
            operation_id=operation.operation_id,
            component=operation.component,
            native_operation_id=status.operation_id.hex(),
            state=status.state.name,
            exit_code=self._exit_code(status),
            authority_retained=status.authority_retained,
            bundle_committed=status.bundle_committed,
        )

    def _transcript_metadata(
        self,
        status: NativeOperationStatus,
        stream: TranscriptStream,
    ) -> TranscriptMetadata:
        offset = 0
        content_digest = hashlib.sha256()
        observed_size: int | None = None
        retained_size: int | None = None
        native_digest: bytes | None = None
        truncated: bool | None = None
        eof = False
        while not eof:
            length = min(
                64 * 1024,
                max(1, self._capability.max_output_bytes - offset),
            )
            chunk = self._client.read_transcript(
                status.operation_id,
                status.recovery_token,
                stream,
                offset=offset,
                length=length,
            )
            facts = (
                chunk.observed_size,
                chunk.retained_size,
                chunk.sha256,
                chunk.truncated,
            )
            if observed_size is None:
                (
                    observed_size,
                    retained_size,
                    native_digest,
                    truncated,
                ) = facts
                if retained_size > self._capability.max_output_bytes:
                    raise EvidenceIncomplete(
                        "native transcript exceeds approved bound"
                    )
            elif facts != (
                observed_size,
                retained_size,
                native_digest,
                truncated,
            ):
                raise EvidenceIncomplete(
                    "native transcript metadata changed during read"
                )
            if not chunk.data and not chunk.eof:
                raise EvidenceIncomplete("native transcript read did not advance")
            content_digest.update(chunk.data)
            offset += len(chunk.data)
            eof = chunk.eof
        if (
            observed_size is None
            or retained_size is None
            or native_digest is None
            or truncated is None
            or offset != retained_size
            or (not truncated and content_digest.digest() != native_digest)
        ):
            raise EvidenceIncomplete("native transcript proof is incomplete")
        return TranscriptMetadata(
            sha256=native_digest.hex(),
            size=retained_size,
            observed_size=observed_size,
            truncated=truncated,
            eof=True,
        )

    def _stop_evidence(
        self,
        operation: object,
        status: NativeOperationStatus,
    ) -> StopEvidence:
        if (
            status.state is not OperationState.RESULT_RETAINED
            or not status.authority_retained
            or self._exit_code(status) is None
        ):
            raise EvidenceIncomplete("native cleanup proof is incomplete")
        stdout = self._transcript_metadata(
            status, TranscriptStream.STDOUT
        )
        stderr = self._transcript_metadata(
            status, TranscriptStream.STDERR
        )
        return StopEvidence(
            operation_id=operation.operation_id,
            component=operation.component,
            native_operation_id=status.operation_id.hex(),
            recovery_token=status.recovery_token.hex(),
            state=status.state.name,
            exit_code=status.exit_status,
            cleanup_proven=True,
            stdout=stdout,
            stderr=stderr,
        )

    def stop(self, operation_id: str) -> StopEvidence:
        operation = self._operation(operation_id, "STOP")
        retained = self._active.get(operation.component)
        if retained is None:
            recovered = self.recover_completed_stop(operation_id)
            if recovered is not None:
                return recovered
            retained = self._active.get(operation.component)
            if retained is None:
                raise RuntimeError("STOP requires native recovery authority")
        status = self._client.stop(
            retained.operation_id, retained.recovery_token
        )
        self._validate_continuity(
            retained,
            status,
            permitted_states=frozenset(
                {
                    OperationState.RESULT_RETAINED,
                    OperationState.RECOVERY_REQUIRED,
                    OperationState.ACKNOWLEDGED,
                }
            ),
        )
        evidence = self._stop_evidence(operation, status)
        self._completed[operation.component] = evidence
        self._active.pop(operation.component, None)
        return evidence

    def recover_completed_stop(
        self, operation_id: str
    ) -> StopEvidence | None:
        operation = self._operation(operation_id, "STOP")
        cached = self._completed.get(operation.component)
        if cached is not None:
            return cached
        expected = _native_operation_id(
            self._capability, operation.component
        )
        status = self._recover_operation_status(
            operation.component,
            expected,
        )
        if status is None:
            return None
        if status.state is OperationState.ACKNOWLEDGED:
            self._acknowledged.add(operation.component)
            return None
        if status.state is not OperationState.RESULT_RETAINED:
            return None
        evidence = self._stop_evidence(operation, status)
        self._completed[operation.component] = evidence
        self._active.pop(operation.component, None)
        return evidence

    def publish_evidence(
        self, operation_id: str, evidence: StopEvidence
    ) -> NativeBundleReceipt:
        operation = self._operation(operation_id, "STOP")
        retained = self._completed.get(operation.component)
        if retained != evidence or not evidence.cleanup_proven:
            raise EvidenceIncomplete(
                "native publication requires exact retained STOP proof"
            )
        prior = self._receipts.get(operation.component)
        if prior is not None:
            return prior
        native_id = bytes.fromhex(evidence.native_operation_id)
        receipt = self._client.publish_bundle(
            native_id,
            bytes.fromhex(evidence.recovery_token),
            _publication_id(self._capability, native_id),
        )
        if (
            receipt.operation.operation_id != native_id
            or receipt.operation.recovery_token
            != bytes.fromhex(evidence.recovery_token)
            or not receipt.operation.bundle_committed
            or receipt.manifest_sha256
            != receipt.operation.publication_sha256
        ):
            raise EvidenceIncomplete(
                "native publication receipt is incomplete"
            )
        self._receipts[operation.component] = receipt
        return receipt

    def acknowledge_stop(
        self, operation_id: str, evidence: StopEvidence
    ) -> None:
        operation = self._operation(operation_id, "STOP")
        if operation.component in self._acknowledged:
            return
        retained = self._completed.get(operation.component)
        receipt = self._receipts.get(operation.component)
        if retained != evidence or receipt is None:
            raise EvidenceIncomplete(
                "native acknowledgement requires publication receipt"
            )
        status = self._client.acknowledge(
            bytes.fromhex(evidence.native_operation_id),
            bytes.fromhex(evidence.recovery_token),
            receipt.manifest_sha256,
        )
        if (
            status.operation_id
            != bytes.fromhex(evidence.native_operation_id)
            or status.recovery_token
            != bytes.fromhex(evidence.recovery_token)
            or status.publication_sha256 != receipt.manifest_sha256
            or status.state is not OperationState.ACKNOWLEDGED
            or not status.acknowledged
            or status.authority_retained
        ):
            raise EvidenceIncomplete(
                "native acknowledgement proof is incomplete"
            )
        self._acknowledged.add(operation.component)

    def snapshot_recovery_state(
        self,
        component: str,
        operation_id: str,
        cleanup_attempts_consumed: int,
    ) -> RuntimeRecoveryState:
        operation = self._operation(operation_id, "STOP")
        if (
            operation.component.lower() != component
            or type(cleanup_attempts_consumed) is not int
            or not 1
            <= cleanup_attempts_consumed
            <= RuntimeStartFailure.MAX_CLEANUP_ATTEMPTS
        ):
            raise RuntimeError("runtime recovery snapshot authority is invalid")
        completed = self._completed.get(operation.component)
        if completed is not None:
            return RuntimeRecoveryState(
                component=component,
                operation_id=operation_id,
                cleanup_attempts_consumed=cleanup_attempts_consumed,
                native_operation_id=completed.native_operation_id,
                state=completed.state,
                cleanup_proven=completed.cleanup_proven,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        active = self._active.get(operation.component)
        pending = self._pending.get(operation.component)
        if active is None and pending is not None:
            return RuntimeRecoveryState(
                component=component,
                operation_id=operation_id,
                cleanup_attempts_consumed=cleanup_attempts_consumed,
                native_operation_id=pending.hex(),
                state=OperationState.RECOVERY_REQUIRED.name,
                cleanup_proven=False,
                stdout=None,
                stderr=None,
            )
        if active is None:
            raise RuntimeError("runtime recovery state is unavailable")
        return RuntimeRecoveryState(
            component=component,
            operation_id=operation_id,
            cleanup_attempts_consumed=cleanup_attempts_consumed,
            native_operation_id=active.operation_id.hex(),
            state=active.state.name,
            cleanup_proven=False,
            stdout=None,
            stderr=None,
        )

    def wait_ready(self, operation_id: str) -> ReadinessEvidence:
        operation = self._operation(operation_id, "START")
        retained = self._active.get(operation.component)
        if retained is None:
            raise RuntimeError("readiness requires native recovery authority")
        if operation.bind_host is None or operation.port is None:
            raise ValueError("approved operation has no readiness endpoint")
        deadline = self._monotonic() + self._capability.startup_timeout_seconds
        attempts = 0
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "approved operation readiness timed out"
                )
            attempts += 1
            status = self.status(operation_id)
            if status.exit_code is not None:
                raise RuntimeError("approved operation exited before readiness")
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "approved operation readiness timed out"
                )
            try:
                with urlopen(  # noqa: S310 - exact approved loopback endpoint
                    (
                        f"http://{operation.bind_host}:{operation.port}"
                        "/health/ready"
                    ),
                    timeout=remaining,
                ) as response:
                    if response.status != 200:
                        raise RuntimeError(
                            "approved readiness response is invalid"
                        )
                    raw = response.read(16 * 1024 + 1)
            except HTTPError:
                raise RuntimeError(
                    "approved readiness response is invalid"
                ) from None
            except OSError:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "approved operation readiness timed out"
                    ) from None
                time.sleep(min(0.02, remaining))
                continue
            if self._monotonic() >= deadline:
                raise TimeoutError(
                    "approved operation readiness timed out"
                )
            try:
                document = json.loads(raw)
            except (UnicodeError, json.JSONDecodeError):
                raise RuntimeError(
                    "approved readiness response is invalid"
                ) from None
            if (
                len(raw) > 16 * 1024
                or not isinstance(document, dict)
                or not isinstance(document.get("data"), dict)
                or document["data"].get("status") != "READY"
            ):
                raise RuntimeError(
                    "approved readiness response is invalid"
                )
            return ReadinessEvidence(
                operation_id=operation_id,
                native_operation_id=retained.operation_id.hex(),
                attempts=attempts,
                status="READY",
            )

    def run_once(self, operation_id: str) -> EvidenceBundle:
        operation = self._operation(operation_id, "START")
        if (
            operation.component in self._active
            or operation.component in self._pending
        ):
            raise RuntimeError("component already has native custody")
        self._validate_bindings()
        request = self._request(operation)
        self._attest_child_credentials()
        self._pending[operation.component] = request.operation_id
        status: NativeOperationStatus | None = None
        try:
            status = self._client.run_once(request)
            self._pending.pop(operation.component, None)
            stop_operation = self._stop_operation(operation.component)
            evidence = self._stop_evidence(stop_operation, status)
            self._completed[operation.component] = evidence
            receipt = self.publish_evidence(
                stop_operation.operation_id, evidence
            )
            self.acknowledge_stop(stop_operation.operation_id, evidence)
            return EvidenceBundle(
                root=(
                    self._capability.evidence_root
                    / evidence.native_operation_id
                ),
                process=evidence,
                publication=receipt,
            )
        except BaseException as error:
            self._raise_request_failure(
                operation,
                request,
                error,
                observed_status=status,
            )
            raise AssertionError("request failure reconciliation returned")


__all__ = [
    "EvidenceBundle",
    "EvidenceIncomplete",
    "IncompleteCleanupProof",
    "Package6Controller",
    "ProcessEvidence",
    "ProcessStatus",
    "ReadinessEvidence",
    "RuntimeChildAuthorities",
    "RuntimeCleanupFailure",
    "RuntimeRecoveryState",
    "RuntimeStartFailure",
    "SourceDrift",
    "SpawnEnvironmentEvidence",
    "StopEvidence",
    "TrackedProcessIdentity",
    "TranscriptMetadata",
    "issue_engine_session_port",
    "issue_runtime_child_authorities",
]
