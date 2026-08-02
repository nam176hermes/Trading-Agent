"""Strict, bounded Package 6 native-custodian protocol client.

The client owns only its connected protocol socket.  Target process, pidfd,
cgroup, transcript, journal, and publication authority remain native.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
import binascii
import hashlib
import os
from pathlib import PurePosixPath
import re
import secrets
import select
import socket
import stat
import struct
import threading


PROTOCOL_MAGIC = 0x50364341
PROTOCOL_VERSION = 1
PROTOCOL_FEATURES: tuple[str, ...] = ()
PROTOCOL_FLAGS = 0
RESPONSE_BIT = 0x8000
ERROR_MESSAGE_TYPE = 0xFFFF

REQUEST_ID_BYTES = 16
OPERATION_ID_BYTES = 16
SHA256_BYTES = 32
HEADER_SIZE = 36
MAX_PAYLOAD_BYTES = 1_048_576
MAX_FRAME_BYTES = HEADER_SIZE + MAX_PAYLOAD_BYTES
MAX_ARGV_COUNT = 128
MAX_ENVIRONMENT_COUNT = 128
MAX_STRING_BYTES = 4096
MAX_PUBLIC_CODE_BYTES = 64
MAX_OPERATIONS = 16
MAX_CREDENTIAL_MANIFEST_BYTES = 32_768
FIELD_HEADER_SIZE = 8
OPERATION_SUMMARY_BYTES = 136
TRANSCRIPT_METADATA_BYTES = 80

_HEADER = struct.Struct(">IHHI16sII")
_FIELD_HEADER = struct.Struct(">HHI")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT = re.compile(r"[0-9a-f]{40}\Z")
_ENVIRONMENT_KEY = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_ENDPOINT_AUTHORITY = "PREOPENED_UNIX_SEQPACKET_DESCRIPTOR"
_LIVE_FALSE_KEYS = frozenset(
    {
        "LIVE_EXECUTION",
        "LIVE_EXECUTION_ENABLED",
        "LIVE_TRADING",
        "LIVE_TRADING_APPROVED",
        "LIVE_TRADING_ENABLED",
    }
)


class MessageType(IntEnum):
    HELLO = 1
    START = 2
    STATUS = 3
    STOP = 4
    RUN_ONCE = 5
    READ_TRANSCRIPT = 6
    PUBLISH_BUNDLE = 7
    ACK = 8
    RECOVER = 9


class OperationState(IntEnum):
    ABSENT = 0
    RESERVED = 1
    EXECUTABLE_PINNED = 2
    CGROUP_CREATED = 3
    CHILD_CLONED = 4
    EXEC_CONFIRMED = 5
    RUNNING = 6
    STOP_REQUESTED = 7
    CGROUP_KILLED = 8
    CGROUP_EMPTY = 9
    CHILD_EXIT_OBSERVED = 10
    CHILD_REAPED = 11
    TRANSCRIPTS_FINAL = 12
    RESULT_RETAINED = 13
    ACKNOWLEDGED = 14
    RECOVERY_REQUIRED = 15


class PublicStatus(IntEnum):
    OK = 0
    INVALID_FRAME = 1
    UNSUPPORTED_VERSION = 2
    UNAUTHORIZED = 3
    INVALID_REQUEST = 4
    NOT_FOUND = 5
    CONFLICT = 6
    LIMIT_EXCEEDED = 7
    TIMEOUT = 8
    RECOVERY_REQUIRED = 9
    INTERNAL = 10


class TranscriptStream(IntEnum):
    STDOUT = 1
    STDERR = 2


class _Field(IntEnum):
    OPERATION_ID = 1
    OPERATION_DIGEST = 2
    RECOVERY_TOKEN = 3
    EXECUTABLE = 4
    ARGV = 5
    ENVIRONMENT = 6
    STREAM = 7
    OFFSET = 8
    LENGTH = 9
    PUBLICATION_ID = 10
    CREDENTIAL_MANIFEST = 11


class CustodianError(RuntimeError):
    """Public-safe base error for native custodian communication."""


class CustodianProtocolError(CustodianError):
    """The endpoint, connection, or response violated the attested contract."""


class CustodianTimeout(CustodianError):
    """The bounded protocol exchange did not complete in time."""


class CustodianRequestError(CustodianError):
    """One exact public error envelope returned by the native authority."""

    def __init__(
        self,
        *,
        status: PublicStatus,
        public_code: str,
        retryable: bool,
        operation_state: OperationState,
        recovery_token: bytes,
    ) -> None:
        super().__init__(f"custodian request failed: {public_code}")
        self.status = status
        self.public_code = public_code
        self.retryable = retryable
        self.operation_state = operation_state
        self.recovery_token = recovery_token


@dataclass(frozen=True, slots=True)
class CustodianAttestation:
    """Approval-bound facts for one already-open native endpoint."""

    helper_binary_sha256: str
    native_source_set_sha256: str
    protocol_version: int
    protocol_features: tuple[str, ...]
    endpoint_authority: str
    peer_pid: int
    peer_uid: int
    peer_gid: int
    candidate_commit: str
    candidate_tree: str
    stage_sha256: str
    fixture_sha256: str
    mode: str
    live_execution_approved: bool
    live_trading_approved: bool

    def __post_init__(self) -> None:
        digest_values = (
            self.helper_binary_sha256,
            self.native_source_set_sha256,
            self.stage_sha256,
            self.fixture_sha256,
        )
        if any(
            type(value) is not str or _SHA256.fullmatch(value) is None
            for value in digest_values
        ):
            raise ValueError("custodian attestation digest is invalid")
        if (
            type(self.candidate_commit) is not str
            or _GIT.fullmatch(self.candidate_commit) is None
            or type(self.candidate_tree) is not str
            or _GIT.fullmatch(self.candidate_tree) is None
        ):
            raise ValueError("custodian candidate identity is invalid")
        if (
            type(self.protocol_version) is not int
            or self.protocol_version != PROTOCOL_VERSION
            or type(self.protocol_features) is not tuple
            or self.protocol_features != PROTOCOL_FEATURES
            or self.endpoint_authority != _ENDPOINT_AUTHORITY
        ):
            raise ValueError("custodian protocol authority is invalid")
        if any(
            type(value) is not int or value < 0
            for value in (self.peer_pid, self.peer_uid, self.peer_gid)
        ) or self.peer_pid < 1:
            raise ValueError("custodian peer identity is invalid")
        if (
            self.mode != "PAPER"
            or self.live_execution_approved is not False
            or self.live_trading_approved is not False
        ):
            raise ValueError("custodian mode authority is invalid")


@dataclass(frozen=True, slots=True)
class NativeOperationRequest:
    operation_id: bytes
    executable: str
    executable_sha256: bytes
    argv: tuple[str, ...]
    environment: tuple[str, ...]
    credential_directory_fd: int | None = None
    credential_manifest: bytes = b""

    def __post_init__(self) -> None:
        _identity(self.operation_id, "operation")
        _digest_bytes(self.executable_sha256)
        _relative_executable(self.executable)
        _string_vector(
            self.argv,
            maximum=MAX_ARGV_COUNT,
            allow_empty=False,
            environment=False,
        )
        _string_vector(
            self.environment,
            maximum=MAX_ENVIRONMENT_COUNT,
            allow_empty=True,
            environment=True,
        )
        has_descriptor = self.credential_directory_fd is not None
        if has_descriptor != bool(self.credential_manifest):
            raise ValueError("credential authority is invalid")
        if has_descriptor:
            descriptor = self.credential_directory_fd
            if type(descriptor) is not int or descriptor <= 2:
                raise ValueError("credential authority is invalid")
            if (
                type(self.credential_manifest) is not bytes
                or not self.credential_manifest.startswith(b"P6CM1")
                or len(self.credential_manifest)
                > MAX_CREDENTIAL_MANIFEST_BYTES
            ):
                raise ValueError("credential authority is invalid")
            try:
                info = os.fstat(descriptor)
                inheritable = os.get_inheritable(descriptor)
            except OSError as error:
                raise ValueError("credential authority is invalid") from error
            if not stat.S_ISDIR(info.st_mode) or inheritable:
                raise ValueError("credential authority is invalid")


@dataclass(frozen=True, slots=True)
class ProtocolHello:
    protocol_version: int
    features: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NativeOperationStatus:
    operation_id: bytes
    recovery_token: bytes
    state: OperationState
    resume_state: OperationState
    authority_retained: bool
    bundle_committed: bool
    stdout_truncated: bool
    stderr_truncated: bool
    acknowledged: bool
    exit_status: int
    request_sha256: bytes
    executable_sha256: bytes
    publication_sha256: bytes


@dataclass(frozen=True, slots=True)
class NativeTranscriptChunk:
    operation_id: bytes
    stream: TranscriptStream
    offset: int
    data: bytes
    observed_size: int
    retained_size: int
    sha256: bytes
    eof: bool
    truncated: bool


@dataclass(frozen=True, slots=True)
class NativeBundleReceipt:
    operation: NativeOperationStatus
    manifest_sha256: bytes


def _identity(value: object, label: str) -> bytes:
    if (
        type(value) is not bytes
        or len(value) != OPERATION_ID_BYTES
        or value == bytes(OPERATION_ID_BYTES)
    ):
        raise ValueError(f"{label} identity is invalid")
    return value


def _recovery_token(value: object) -> bytes:
    if (
        type(value) is not bytes
        or len(value) != OPERATION_ID_BYTES
        or value == bytes(OPERATION_ID_BYTES)
    ):
        raise ValueError("recovery authority is invalid")
    return value


def _digest_bytes(value: object, *, allow_zero: bool = False) -> bytes:
    if type(value) is not bytes or len(value) != SHA256_BYTES:
        raise ValueError("digest authority is invalid")
    if not allow_zero and value == bytes(SHA256_BYTES):
        raise ValueError("digest authority is invalid")
    return value


def _text_bytes(value: object, *, allow_empty: bool = False) -> bytes:
    if type(value) is not str or "\x00" in value:
        raise ValueError("protocol text is invalid")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError as error:
        raise ValueError("protocol text is invalid") from error
    if (
        (not allow_empty and not encoded)
        or len(encoded) > MAX_STRING_BYTES
    ):
        raise ValueError("protocol text is invalid")
    return encoded


def _relative_executable(value: object) -> bytes:
    encoded = _text_bytes(value)
    assert isinstance(value, str)
    pure = PurePosixPath(value)
    if (
        "\\" in value
        or pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != value
    ):
        raise ValueError("executable authority is invalid")
    return encoded


def _string_vector(
    value: object,
    *,
    maximum: int,
    allow_empty: bool,
    environment: bool,
) -> tuple[bytes, ...]:
    if type(value) is not tuple or not 0 <= len(value) <= maximum:
        raise ValueError("protocol string list is invalid")
    if not allow_empty and not value:
        raise ValueError("protocol string list is invalid")
    encoded_items: list[bytes] = []
    seen_keys: set[str] = set()
    for item in value:
        encoded = _text_bytes(item)
        if environment:
            assert isinstance(item, str)
            key, separator, environment_value = item.partition("=")
            if (
                separator != "="
                or _ENVIRONMENT_KEY.fullmatch(key) is None
                or key in seen_keys
            ):
                raise ValueError("environment authority is invalid")
            if key in _LIVE_FALSE_KEYS and environment_value != "false":
                raise ValueError("environment authority is invalid")
            if key == "TRADING_MODE" and environment_value != "paper":
                raise ValueError("environment authority is invalid")
            seen_keys.add(key)
        encoded_items.append(encoded)
    return tuple(encoded_items)


def _encode_vector(
    value: tuple[str, ...],
    *,
    maximum: int,
    allow_empty: bool,
    environment: bool,
) -> bytes:
    encoded = _string_vector(
        value,
        maximum=maximum,
        allow_empty=allow_empty,
        environment=environment,
    )
    output = bytearray(struct.pack(">I", len(encoded)))
    for item in encoded:
        output.extend(struct.pack(">I", len(item)))
        output.extend(item)
    return bytes(output)


def _field(field: _Field, value: bytes) -> bytes:
    if type(value) is not bytes or len(value) > MAX_PAYLOAD_BYTES:
        raise ValueError("protocol field is invalid")
    return _FIELD_HEADER.pack(int(field), 0, len(value)) + value


def _operation_payload(request: NativeOperationRequest) -> bytes:
    if not isinstance(request, NativeOperationRequest):
        raise TypeError("exact native operation request is required")
    fields = [
            _field(_Field.OPERATION_ID, _identity(request.operation_id, "operation")),
            _field(
                _Field.OPERATION_DIGEST,
                _digest_bytes(request.executable_sha256),
            ),
            _field(_Field.EXECUTABLE, _relative_executable(request.executable)),
            _field(
                _Field.ARGV,
                _encode_vector(
                    request.argv,
                    maximum=MAX_ARGV_COUNT,
                    allow_empty=False,
                    environment=False,
                ),
            ),
            _field(
                _Field.ENVIRONMENT,
                _encode_vector(
                    request.environment,
                    maximum=MAX_ENVIRONMENT_COUNT,
                    allow_empty=True,
                    environment=True,
                ),
            ),
    ]
    if request.credential_manifest:
        fields.append(
            _field(_Field.CREDENTIAL_MANIFEST, request.credential_manifest)
        )
    payload = b"".join(fields)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("native operation request is excessive")
    return payload


def _operation_request_sha256(
    message_type: MessageType,
    request: NativeOperationRequest,
) -> bytes:
    if message_type not in {MessageType.START, MessageType.RUN_ONCE}:
        raise ValueError("operation request digest type is invalid")
    return hashlib.sha256(
        struct.pack(">H", int(message_type)) + _operation_payload(request)
    ).digest()


def _authorized_payload(operation_id: bytes, recovery_token: bytes) -> bytes:
    return b"".join(
        (
            _field(_Field.OPERATION_ID, _identity(operation_id, "operation")),
            _field(_Field.RECOVERY_TOKEN, _recovery_token(recovery_token)),
        )
    )


_ERROR_CODES: dict[PublicStatus, frozenset[str]] = {
    PublicStatus.INVALID_FRAME: frozenset({"INVALID_FRAME"}),
    PublicStatus.UNSUPPORTED_VERSION: frozenset({"UNSUPPORTED_VERSION"}),
    PublicStatus.UNAUTHORIZED: frozenset({"UNAUTHORIZED"}),
    PublicStatus.INVALID_REQUEST: frozenset({"INVALID_REQUEST"}),
    PublicStatus.NOT_FOUND: frozenset({"NOT_FOUND"}),
    PublicStatus.CONFLICT: frozenset({"CONFLICT"}),
    PublicStatus.LIMIT_EXCEEDED: frozenset({"LIMIT_EXCEEDED"}),
    PublicStatus.TIMEOUT: frozenset({"TIMEOUT"}),
    PublicStatus.RECOVERY_REQUIRED: frozenset({"RECOVERY_REQUIRED"}),
    PublicStatus.INTERNAL: frozenset({"INTERNAL", "UNSUPPORTED_KERNEL"}),
}
_RETRYABLE_STATUSES = frozenset(
    {PublicStatus.TIMEOUT, PublicStatus.RECOVERY_REQUIRED}
)


def _malformed() -> CustodianProtocolError:
    return CustodianProtocolError("custodian response is malformed")


def _decode_error(payload: bytes) -> CustodianRequestError:
    if len(payload) != 85:
        raise _malformed()
    status_value, retryable_value, state_value = struct.unpack_from(
        ">HBB", payload, 0
    )
    try:
        status = PublicStatus(status_value)
        state = OperationState(state_value)
    except ValueError as error:
        raise _malformed() from error
    if status is PublicStatus.OK or retryable_value not in {0, 1}:
        raise _malformed()
    retryable = bool(retryable_value)
    if retryable != (status in _RETRYABLE_STATUSES):
        raise _malformed()
    token = payload[4:20]
    if (state is OperationState.ABSENT) != (token == bytes(16)):
        raise _malformed()
    code_length = payload[20]
    if not 1 <= code_length <= MAX_PUBLIC_CODE_BYTES:
        raise _malformed()
    code_raw = payload[21 : 21 + code_length]
    padding = payload[21 + code_length :]
    if any(padding):
        raise _malformed()
    try:
        code = code_raw.decode("ascii", "strict")
    except UnicodeError as error:
        raise _malformed() from error
    if code not in _ERROR_CODES.get(status, frozenset()):
        raise _malformed()
    return CustodianRequestError(
        status=status,
        public_code=code,
        retryable=retryable,
        operation_state=state,
        recovery_token=token,
    )


def _decode_summary(
    payload: bytes,
    *,
    expected_operation_id: bytes | None,
    expected_recovery_token: bytes | None = None,
    expected_request_digest: bytes | None = None,
    expected_executable_digest: bytes | None = None,
    expected_publication_digest: bytes | None = None,
    permitted_states: frozenset[OperationState] | None = None,
) -> NativeOperationStatus:
    if len(payload) != OPERATION_SUMMARY_BYTES:
        raise _malformed()
    operation_id = payload[0:16]
    recovery_token = payload[16:32]
    try:
        _identity(operation_id, "operation")
        _recovery_token(recovery_token)
        state = OperationState(payload[32])
        resume_state = OperationState(payload[33])
    except (ValueError, TypeError) as error:
        raise _malformed() from error
    if expected_operation_id is not None and operation_id != expected_operation_id:
        raise _malformed()
    if (
        expected_recovery_token is not None
        and recovery_token != expected_recovery_token
    ):
        raise _malformed()
    flags = struct.unpack_from(">H", payload, 34)[0]
    if flags & ~0x001F:
        raise _malformed()
    exit_status = struct.unpack_from(">i", payload, 36)[0]
    request_digest = payload[40:72]
    executable_digest = payload[72:104]
    publication_digest = payload[104:136]
    if (
        request_digest == bytes(32)
        or executable_digest == bytes(32)
        or (
            expected_request_digest is not None
            and request_digest != expected_request_digest
        )
        or (
            expected_executable_digest is not None
            and executable_digest != expected_executable_digest
        )
        or (
            expected_publication_digest is not None
            and publication_digest != expected_publication_digest
        )
        or (permitted_states is not None and state not in permitted_states)
    ):
        raise _malformed()
    authority_retained = bool(flags & 0x0001)
    bundle_committed = bool(flags & 0x0002)
    acknowledged = bool(flags & 0x0010)
    stdout_truncated = bool(flags & 0x0004)
    stderr_truncated = bool(flags & 0x0008)
    effective_state = resume_state if state is OperationState.RECOVERY_REQUIRED else state
    if (
        state is OperationState.ABSENT
        or (
            state is not OperationState.RECOVERY_REQUIRED
            and resume_state is not state
        )
        or (
            state is OperationState.RECOVERY_REQUIRED
            and resume_state is OperationState.RECOVERY_REQUIRED
        )
        or authority_retained
        is not (state is not OperationState.ACKNOWLEDGED)
        or acknowledged is not (effective_state is OperationState.ACKNOWLEDGED)
        or bundle_committed != (publication_digest != bytes(32))
        or bundle_committed
        and effective_state.value < OperationState.RESULT_RETAINED.value
        or (stdout_truncated or stderr_truncated)
        and effective_state.value < OperationState.TRANSCRIPTS_FINAL.value
        or (
            exit_status == -(2**31)
            and effective_state.value
            >= OperationState.CHILD_EXIT_OBSERVED.value
        )
        or (
            exit_status != -(2**31)
            and effective_state.value
            < OperationState.CHILD_EXIT_OBSERVED.value
        )
    ):
        raise _malformed()
    return NativeOperationStatus(
        operation_id=operation_id,
        recovery_token=recovery_token,
        state=state,
        resume_state=resume_state,
        authority_retained=authority_retained,
        bundle_committed=bundle_committed,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        acknowledged=acknowledged,
        exit_status=exit_status,
        request_sha256=request_digest,
        executable_sha256=executable_digest,
        publication_sha256=publication_digest,
    )


class CustodianClient:
    """One synchronous strict client for an attested pre-opened endpoint."""

    def __init__(
        self,
        protocol_socket: socket.socket,
        attestation: CustodianAttestation,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        if type(protocol_socket) is not socket.socket:
            raise TypeError("exact custodian protocol socket is required")
        if not isinstance(attestation, CustodianAttestation):
            raise TypeError("exact custodian attestation is required")
        if (
            type(timeout_seconds) not in {int, float}
            or isinstance(timeout_seconds, bool)
            or not 0 < float(timeout_seconds) <= 60
        ):
            raise ValueError("custodian timeout is invalid")
        try:
            endpoint_valid = (
                protocol_socket.family == socket.AF_UNIX
                and protocol_socket.getsockopt(
                    socket.SOL_SOCKET, socket.SO_TYPE
                )
                == socket.SOCK_SEQPACKET
                and not protocol_socket.get_inheritable()
                and protocol_socket.fileno() > 2
            )
        except OSError as error:
            raise CustodianProtocolError(
                "custodian endpoint attestation failed"
            ) from error
        if not endpoint_valid:
            raise CustodianProtocolError("custodian endpoint attestation failed")
        self._socket = protocol_socket
        self._attestation = attestation
        self._timeout_seconds = float(timeout_seconds)
        self._request_ids: set[bytes] = set()
        self._known: dict[bytes, NativeOperationStatus] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._verify_peer()

    @property
    def attestation(self) -> CustodianAttestation:
        return self._attestation

    def _verify_peer(self) -> None:
        try:
            raw = self._socket.getsockopt(
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                struct.calcsize("3i"),
            )
            peer = struct.unpack("3i", raw)
        except (OSError, struct.error) as error:
            raise CustodianProtocolError(
                "custodian endpoint attestation failed"
            ) from error
        self._validate_peer_identity(peer)

    def _validate_peer_identity(self, peer: tuple[int, int, int]) -> None:
        expected = (
            self._attestation.peer_pid,
            self._attestation.peer_uid,
            self._attestation.peer_gid,
        )
        if peer != expected:
            raise CustodianProtocolError(
                "custodian endpoint attestation failed"
            )

    def close(self) -> None:
        """Close only the client protocol socket; never perform target cleanup."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._socket.close()
            except OSError:
                pass

    def _send_receive(
        self,
        packet: bytes,
        descriptors: tuple[int, ...] = (),
    ) -> bytes:
        try:
            _readable, writable, _exceptional = select.select(
                (), (self._socket,), (), self._timeout_seconds
            )
            if not writable:
                raise CustodianTimeout("custodian request timed out")
            if descriptors:
                amount = self._socket.sendmsg(
                    [packet],
                    [
                        (
                            socket.SOL_SOCKET,
                            socket.SCM_RIGHTS,
                            struct.pack(f"{len(descriptors)}i", *descriptors),
                        )
                    ],
                    socket.MSG_NOSIGNAL,
                )
            else:
                amount = self._socket.send(packet)
            if amount != len(packet):
                raise CustodianProtocolError(
                    "custodian endpoint is disconnected"
                )
            readable, _writable, _exceptional = select.select(
                (self._socket,), (), (), self._timeout_seconds
            )
            if not readable:
                raise CustodianTimeout("custodian response timed out")
            response, ancillary, response_flags, _address = (
                self._socket.recvmsg(
                    MAX_FRAME_BYTES + 1,
                    socket.CMSG_SPACE(64 * struct.calcsize("i")),
                )
            )
        except CustodianError:
            raise
        except OSError as error:
            raise CustodianProtocolError(
                "custodian endpoint is disconnected"
            ) from error
        if not response:
            raise CustodianProtocolError(
                "custodian endpoint is disconnected"
            )
        allowed_flags = getattr(socket, "MSG_EOR", 0)
        rejected = bool(ancillary) or bool(response_flags & ~allowed_flags)
        if rejected:
            for level, kind, raw in ancillary:
                if (
                    level != socket.SOL_SOCKET
                    or kind != socket.SCM_RIGHTS
                ):
                    continue
                descriptor_bytes = struct.calcsize("i")
                usable = len(raw) - (len(raw) % descriptor_bytes)
                for (descriptor,) in struct.iter_unpack(
                    "i", raw[:usable]
                ):
                    if descriptor < 0:
                        continue
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            raise _malformed()
        return response

    def _request_id(self) -> bytes:
        request_id = secrets.token_bytes(REQUEST_ID_BYTES)
        if (
            type(request_id) is not bytes
            or len(request_id) != REQUEST_ID_BYTES
            or request_id == bytes(REQUEST_ID_BYTES)
            or request_id in self._request_ids
        ):
            raise CustodianProtocolError(
                "custodian request identity is unavailable"
            )
        self._request_ids.add(request_id)
        return request_id

    @staticmethod
    def _packet(
        message_type: MessageType,
        request_id: bytes,
        payload: bytes,
    ) -> bytes:
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise ValueError("custodian request is excessive")
        return _HEADER.pack(
            PROTOCOL_MAGIC,
            PROTOCOL_VERSION,
            int(message_type),
            PROTOCOL_FLAGS,
            request_id,
            len(payload),
            binascii.crc32(payload) & 0xFFFFFFFF,
        ) + payload

    def _exchange(
        self,
        message_type: MessageType,
        payload: bytes,
        *,
        descriptors: tuple[int, ...] = (),
    ) -> bytes:
        with self._lock:
            if self._closed:
                raise CustodianProtocolError("custodian endpoint is disconnected")
            self._verify_peer()
            request_id = self._request_id()
            packet = self._packet(message_type, request_id, payload)
            response = (
                self._send_receive(packet, descriptors)
                if descriptors
                else self._send_receive(packet)
            )
            if (
                len(response) > MAX_FRAME_BYTES
                or len(response) < HEADER_SIZE
            ):
                raise _malformed()
            try:
                (
                    magic,
                    version,
                    response_type,
                    flags,
                    response_id,
                    payload_length,
                    checksum,
                ) = _HEADER.unpack(response[:HEADER_SIZE])
            except struct.error as error:
                raise _malformed() from error
            response_payload = response[HEADER_SIZE:]
            if (
                magic != PROTOCOL_MAGIC
                or version != PROTOCOL_VERSION
                or flags != PROTOCOL_FLAGS
                or response_id != request_id
                or payload_length > MAX_PAYLOAD_BYTES
                or payload_length != len(response_payload)
                or checksum
                != (binascii.crc32(response_payload) & 0xFFFFFFFF)
            ):
                raise _malformed()
            if response_type == ERROR_MESSAGE_TYPE:
                raise _decode_error(response_payload)
            if response_type != RESPONSE_BIT | int(message_type):
                raise _malformed()
            return response_payload

    def hello(self) -> ProtocolHello:
        payload = self._exchange(MessageType.HELLO, b"")
        if payload != b"\x00\x01\x00\x00\x00\x00\x00\x00":
            raise _malformed()
        return ProtocolHello(
            protocol_version=PROTOCOL_VERSION,
            features=PROTOCOL_FEATURES,
        )

    def start(self, request: NativeOperationRequest) -> NativeOperationStatus:
        request_payload = _operation_payload(request)
        request_digest = _operation_request_sha256(
            MessageType.START, request
        )
        descriptors = (
            ()
            if request.credential_directory_fd is None
            else (request.credential_directory_fd,)
        )
        payload = self._exchange(
            MessageType.START,
            request_payload,
            descriptors=descriptors,
        )
        result = _decode_summary(
            payload,
            expected_operation_id=request.operation_id,
            expected_request_digest=request_digest,
            expected_executable_digest=request.executable_sha256,
            permitted_states=frozenset(
                {
                    OperationState.RUNNING,
                    OperationState.RESULT_RETAINED,
                    OperationState.RECOVERY_REQUIRED,
                    OperationState.ACKNOWLEDGED,
                }
            ),
        )
        self._known[result.operation_id] = result
        return result

    def status(
        self, operation_id: bytes, recovery_token: bytes
    ) -> NativeOperationStatus:
        operation = _identity(operation_id, "operation")
        token = _recovery_token(recovery_token)
        prior = self._known.get(operation)
        if prior is None:
            raise CustodianProtocolError(
                "custodian continuity authority is unavailable"
            )
        payload = self._exchange(
            MessageType.STATUS,
            _authorized_payload(operation, token),
        )
        result = _decode_summary(
            payload,
            expected_operation_id=operation,
            expected_recovery_token=token,
            expected_request_digest=prior.request_sha256,
            expected_executable_digest=prior.executable_sha256,
            expected_publication_digest=prior.publication_sha256,
            permitted_states=frozenset(
                {
                    prior.state,
                    OperationState.RESULT_RETAINED,
                    OperationState.RECOVERY_REQUIRED,
                    OperationState.ACKNOWLEDGED,
                }
            ),
        )
        self._known[operation] = result
        return result

    def stop(
        self, operation_id: bytes, recovery_token: bytes
    ) -> NativeOperationStatus:
        operation = _identity(operation_id, "operation")
        token = _recovery_token(recovery_token)
        prior = self._known.get(operation)
        if prior is None:
            raise CustodianProtocolError(
                "custodian continuity authority is unavailable"
            )
        payload = self._exchange(
            MessageType.STOP,
            _authorized_payload(operation, token),
        )
        result = _decode_summary(
            payload,
            expected_operation_id=operation,
            expected_recovery_token=token,
            expected_request_digest=prior.request_sha256,
            expected_executable_digest=prior.executable_sha256,
            expected_publication_digest=prior.publication_sha256,
            permitted_states=frozenset(
                {
                    OperationState.RESULT_RETAINED,
                    OperationState.RECOVERY_REQUIRED,
                    OperationState.ACKNOWLEDGED,
                }
            ),
        )
        self._known[operation] = result
        return result

    def run_once(
        self, request: NativeOperationRequest
    ) -> NativeOperationStatus:
        request_payload = _operation_payload(request)
        descriptors = (
            ()
            if request.credential_directory_fd is None
            else (request.credential_directory_fd,)
        )
        payload = self._exchange(
            MessageType.RUN_ONCE,
            request_payload,
            descriptors=descriptors,
        )
        result = _decode_summary(
            payload,
            expected_operation_id=request.operation_id,
            expected_request_digest=_operation_request_sha256(
                MessageType.RUN_ONCE, request
            ),
            expected_executable_digest=request.executable_sha256,
            permitted_states=frozenset(
                {
                    OperationState.RESULT_RETAINED,
                    OperationState.RECOVERY_REQUIRED,
                    OperationState.ACKNOWLEDGED,
                }
            ),
        )
        self._known[result.operation_id] = result
        return result

    def read_transcript(
        self,
        operation_id: bytes,
        recovery_token: bytes,
        stream: TranscriptStream,
        *,
        offset: int,
        length: int,
    ) -> NativeTranscriptChunk:
        operation = _identity(operation_id, "operation")
        token = _recovery_token(recovery_token)
        prior = self._known.get(operation)
        if prior is None:
            raise CustodianProtocolError(
                "custodian continuity authority is unavailable"
            )
        if type(stream) is not TranscriptStream:
            raise TypeError("exact transcript stream is required")
        if type(offset) is not int or not 0 <= offset <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("transcript offset is invalid")
        if (
            type(length) is not int
            or not 1 <= length <= MAX_PAYLOAD_BYTES - TRANSCRIPT_METADATA_BYTES
        ):
            raise ValueError("transcript length is invalid")
        request_payload = b"".join(
            (
                _field(_Field.OPERATION_ID, operation),
                _field(_Field.RECOVERY_TOKEN, token),
                _field(_Field.STREAM, bytes((int(stream),))),
                _field(_Field.OFFSET, struct.pack(">Q", offset)),
                _field(_Field.LENGTH, struct.pack(">I", length)),
            )
        )
        payload = self._exchange(MessageType.READ_TRANSCRIPT, request_payload)
        if not TRANSCRIPT_METADATA_BYTES <= len(payload) <= (
            TRANSCRIPT_METADATA_BYTES + length
        ):
            raise _malformed()
        if payload[0:16] != operation or payload[16] != int(stream):
            raise _malformed()
        flags = payload[17]
        if flags & ~0x03 or payload[18:20] != b"\x00\x00":
            raise _malformed()
        response_offset, count, observed_size, retained_size = (
            struct.unpack_from(">QIQQ", payload, 20)
        )
        digest = payload[48:80]
        content = payload[80:]
        if (
            response_offset != offset
            or count != len(content)
            or count > length
            or retained_size > observed_size
            or offset > retained_size
            or count > retained_size - offset
        ):
            raise _malformed()
        eof = bool(flags & 0x01)
        truncated = bool(flags & 0x02)
        if eof != (offset + count == retained_size):
            raise _malformed()
        if truncated != (retained_size < observed_size):
            raise _malformed()
        if eof and offset == 0 and count == retained_size:
            if hashlib.sha256(content).digest() != digest:
                raise _malformed()
        return NativeTranscriptChunk(
            operation_id=operation,
            stream=stream,
            offset=offset,
            data=content,
            observed_size=observed_size,
            retained_size=retained_size,
            sha256=digest,
            eof=eof,
            truncated=truncated,
        )

    def publish_bundle(
        self,
        operation_id: bytes,
        recovery_token: bytes,
        publication_id: bytes,
    ) -> NativeBundleReceipt:
        operation = _identity(operation_id, "operation")
        token = _recovery_token(recovery_token)
        prior = self._known.get(operation)
        if prior is None:
            raise CustodianProtocolError(
                "custodian continuity authority is unavailable"
            )
        publication = _digest_bytes(publication_id)
        request_payload = b"".join(
            (
                _field(_Field.OPERATION_ID, operation),
                _field(_Field.RECOVERY_TOKEN, token),
                _field(_Field.PUBLICATION_ID, publication),
            )
        )
        payload = self._exchange(
            MessageType.PUBLISH_BUNDLE, request_payload
        )
        if len(payload) != OPERATION_SUMMARY_BYTES + SHA256_BYTES:
            raise _malformed()
        result = _decode_summary(
            payload[:OPERATION_SUMMARY_BYTES],
            expected_operation_id=operation,
            expected_recovery_token=token,
            expected_request_digest=prior.request_sha256,
            expected_executable_digest=prior.executable_sha256,
            permitted_states=frozenset(
                {
                    OperationState.RESULT_RETAINED,
                    OperationState.RECOVERY_REQUIRED,
                }
            ),
        )
        manifest = payload[OPERATION_SUMMARY_BYTES:]
        if (
            not result.bundle_committed
            or manifest == bytes(SHA256_BYTES)
            or manifest != result.publication_sha256
        ):
            raise _malformed()
        self._known[operation] = result
        return NativeBundleReceipt(
            operation=result,
            manifest_sha256=manifest,
        )

    def acknowledge(
        self,
        operation_id: bytes,
        recovery_token: bytes,
        publication_digest: bytes,
    ) -> NativeOperationStatus:
        operation = _identity(operation_id, "operation")
        token = _recovery_token(recovery_token)
        prior = self._known.get(operation)
        if prior is None:
            raise CustodianProtocolError(
                "custodian continuity authority is unavailable"
            )
        publication = _digest_bytes(publication_digest, allow_zero=True)
        request_payload = b"".join(
            (
                _field(_Field.OPERATION_ID, operation),
                _field(_Field.RECOVERY_TOKEN, token),
                _field(_Field.PUBLICATION_ID, publication),
            )
        )
        payload = self._exchange(MessageType.ACK, request_payload)
        result = _decode_summary(
            payload,
            expected_operation_id=operation,
            expected_recovery_token=token,
            expected_request_digest=prior.request_sha256,
            expected_executable_digest=prior.executable_sha256,
            expected_publication_digest=publication,
            permitted_states=frozenset({OperationState.ACKNOWLEDGED}),
        )
        if (
            result.state is not OperationState.ACKNOWLEDGED
            or not result.acknowledged
            or result.publication_sha256 != publication
        ):
            raise _malformed()
        self._known[operation] = result
        return result

    def recover(self) -> tuple[NativeOperationStatus, ...]:
        payload = self._exchange(MessageType.RECOVER, b"")
        if len(payload) < 4:
            raise _malformed()
        count = struct.unpack_from(">I", payload, 0)[0]
        if (
            count > MAX_OPERATIONS
            or len(payload) != 4 + count * OPERATION_SUMMARY_BYTES
        ):
            raise _malformed()
        results: list[NativeOperationStatus] = []
        previous: bytes | None = None
        for index in range(count):
            offset = 4 + index * OPERATION_SUMMARY_BYTES
            result = _decode_summary(
                payload[offset : offset + OPERATION_SUMMARY_BYTES],
                expected_operation_id=None,
            )
            if previous is not None and result.operation_id <= previous:
                raise _malformed()
            previous = result.operation_id
            self._known[result.operation_id] = result
            results.append(result)
        return tuple(results)


__all__ = [
    "CustodianAttestation",
    "CustodianClient",
    "CustodianError",
    "CustodianProtocolError",
    "CustodianRequestError",
    "CustodianTimeout",
    "MessageType",
    "NativeBundleReceipt",
    "NativeOperationRequest",
    "NativeOperationStatus",
    "NativeTranscriptChunk",
    "OperationState",
    "PROTOCOL_FEATURES",
    "PROTOCOL_VERSION",
    "ProtocolHello",
    "PublicStatus",
    "TranscriptStream",
]
