from __future__ import annotations

from collections.abc import Callable
import binascii
import hashlib
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time

import pytest

from services.paper_runtime.custodian_client import (
    CustodianAttestation,
    CustodianClient,
    CustodianProtocolError,
    CustodianRequestError,
    CustodianTimeout,
    MessageType,
    NativeOperationRequest,
    NativeOperationStatus,
    OperationState,
    PublicStatus,
    TranscriptStream,
    _operation_payload,
    _operation_request_sha256,
)


ROOT = Path(__file__).resolve().parents[2]
NATIVE_ROOT = ROOT / "native/package6_custodian"

MAGIC = 0x50364341
VERSION = 1
HEADER = struct.Struct(">IHHI16sII")
HEADER_SIZE = 36
MAX_PAYLOAD = 1_048_576
RESPONSE_BIT = 0x8000
ERROR_TYPE = 0xFFFF
SUMMARY_SIZE = 136
TRANSCRIPT_METADATA_SIZE = 80

OPERATION_ID = bytes.fromhex("00112233445566778899aabbccddeeff")
RECOVERY_TOKEN = bytes.fromhex("ffeeddccbbaa99887766554433221100")
REQUEST_DIGEST = bytes.fromhex("11" * 32)
EXECUTABLE_DIGEST = bytes.fromhex("22" * 32)
PUBLICATION_ID = bytes.fromhex("33" * 32)
PUBLICATION_DIGEST = bytes.fromhex("44" * 32)


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


def _frame(
    message_type: int,
    request_id: bytes,
    payload: bytes,
    *,
    magic: int = MAGIC,
    version: int = VERSION,
    flags: int = 0,
    payload_length: int | None = None,
    crc32: int | None = None,
) -> bytes:
    length = len(payload) if payload_length is None else payload_length
    checksum = (
        binascii.crc32(payload) & 0xFFFFFFFF if crc32 is None else crc32
    )
    return HEADER.pack(
        magic,
        version,
        message_type,
        flags,
        request_id,
        length,
        checksum,
    ) + payload


def _summary(
    *,
    operation_id: bytes = OPERATION_ID,
    recovery_token: bytes = RECOVERY_TOKEN,
    state: int = OperationState.RESULT_RETAINED,
    resume_state: int = OperationState.RESULT_RETAINED,
    flags: int = 0x0001,
    exit_status: int = 0,
    request_digest: bytes = REQUEST_DIGEST,
    executable_digest: bytes = EXECUTABLE_DIGEST,
    publication_digest: bytes = bytes(32),
) -> bytes:
    return (
        operation_id
        + recovery_token
        + bytes((int(state), int(resume_state)))
        + struct.pack(">Hi", flags, exit_status)
        + request_digest
        + executable_digest
        + publication_digest
    )


def _error_payload(
    status: PublicStatus,
    *,
    retryable: bool = False,
    state: OperationState = OperationState.ABSENT,
    recovery_token: bytes = bytes(16),
    code: str | None = None,
) -> bytes:
    public_code = code or status.name
    encoded = public_code.encode("ascii")
    return (
        struct.pack(">HBB", int(status), int(retryable), int(state))
        + recovery_token
        + bytes((len(encoded),))
        + encoded
        + bytes(64 - len(encoded))
    )


def _request() -> NativeOperationRequest:
    return NativeOperationRequest(
        operation_id=OPERATION_ID,
        executable="stage/application/.venv/bin/python3.11",
        executable_sha256=EXECUTABLE_DIGEST,
        argv=(
            "stage/application/.venv/bin/python3.11",
            "-I",
            "-m",
            "apps.job_api.main",
        ),
        environment=(
            "LANG=C.UTF-8",
            "LIVE_EXECUTION_ENABLED=false",
            "LIVE_TRADING_APPROVED=false",
            "TRADING_MODE=paper",
        ),
    )


def _request_summary(
    message_type: MessageType,
    *,
    state: OperationState,
    resume_state: OperationState,
    exit_status: int,
) -> bytes:
    request = _request()
    return _summary(
        state=state,
        resume_state=resume_state,
        exit_status=exit_status,
        request_digest=_operation_request_sha256(message_type, request),
    )


def _scripted_client(
    response: Callable[[bytes, bytes], bytes],
    observed: list[bytes],
    *,
    timeout_seconds: float,
) -> CustodianClient:
    client = CustodianClient.__new__(CustodianClient)
    client._attestation = _attestation()
    client._timeout_seconds = timeout_seconds
    client._request_ids = set()
    client._lock = threading.Lock()
    client._closed = False
    client._known = {
        OPERATION_ID: NativeOperationStatus(
            operation_id=OPERATION_ID,
            recovery_token=RECOVERY_TOKEN,
            state=OperationState.RESULT_RETAINED,
            resume_state=OperationState.RESULT_RETAINED,
            authority_retained=True,
            bundle_committed=False,
            stdout_truncated=False,
            stderr_truncated=False,
            acknowledged=False,
            exit_status=0,
            request_sha256=REQUEST_DIGEST,
            executable_sha256=EXECUTABLE_DIGEST,
            publication_sha256=bytes(32),
        )
    }
    client._verify_peer = lambda: None
    client.close = lambda: None

    def send_receive(packet: bytes) -> bytes:
        observed.append(packet)
        return response(packet, packet[12:28])

    client._send_receive = send_receive
    return client


def _exchange(
    call: Callable[[CustodianClient], object],
    response: Callable[[bytes, bytes], bytes],
    *,
    timeout_seconds: float = 0.5,
) -> tuple[object, bytes]:
    observed: list[bytes] = []
    client = _scripted_client(
        response,
        observed,
        timeout_seconds=timeout_seconds,
    )
    try:
        result = call(client)
    finally:
        client.close()
    assert len(observed) == 1
    return result, observed[0]


def _decode_request(packet: bytes) -> tuple[int, bytes, list[tuple[int, bytes]]]:
    magic, version, message_type, flags, request_id, length, checksum = (
        HEADER.unpack(packet[:HEADER_SIZE])
    )
    payload = packet[HEADER_SIZE:]
    assert magic == MAGIC
    assert version == VERSION
    assert flags == 0
    assert request_id != bytes(16)
    assert length == len(payload)
    assert checksum == binascii.crc32(payload) & 0xFFFFFFFF
    if message_type in {MessageType.HELLO, MessageType.RECOVER}:
        return message_type, request_id, []
    fields: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(payload):
        field_id, reserved, field_length = struct.unpack_from(
            ">HHI", payload, offset
        )
        assert reserved == 0
        offset += 8
        value = payload[offset : offset + field_length]
        assert len(value) == field_length
        fields.append((field_id, value))
        offset += field_length
    assert offset == len(payload)
    assert [field_id for field_id, _value in fields] == sorted(
        field_id for field_id, _value in fields
    )
    assert len({field_id for field_id, _value in fields}) == len(fields)
    return message_type, request_id, fields


def test_hello_is_byte_exact_and_binds_protocol_features() -> None:
    result, packet = _exchange(
        lambda client: client.hello(),
        lambda _packet, request_id: _frame(
            RESPONSE_BIT | MessageType.HELLO,
            request_id,
            b"\x00\x01\x00\x00\x00\x00\x00\x00",
        ),
    )

    message_type, _request_id, fields = _decode_request(packet)
    assert message_type == MessageType.HELLO
    assert fields == []
    assert result.protocol_version == 1
    assert result.features == ()


def test_custodian_client_authority_is_exported_from_package() -> None:
    from services.paper_runtime import CustodianClient as ExportedClient

    assert ExportedClient is CustodianClient


@pytest.mark.parametrize(
    ("call", "request_type", "expected_fields", "response_payload"),
    (
        (
            lambda client: client.start(_request()),
            MessageType.START,
            (1, 2, 4, 5, 6),
            _request_summary(
                MessageType.START,
                state=OperationState.RUNNING,
                resume_state=OperationState.RUNNING,
                exit_status=-(2**31),
            ),
        ),
        (
            lambda client: client.status(OPERATION_ID, RECOVERY_TOKEN),
            MessageType.STATUS,
            (1, 3),
            _summary(),
        ),
        (
            lambda client: client.stop(OPERATION_ID, RECOVERY_TOKEN),
            MessageType.STOP,
            (1, 3),
            _summary(),
        ),
        (
            lambda client: client.run_once(_request()),
            MessageType.RUN_ONCE,
            (1, 2, 4, 5, 6),
            _request_summary(
                MessageType.RUN_ONCE,
                state=OperationState.RESULT_RETAINED,
                resume_state=OperationState.RESULT_RETAINED,
                exit_status=0,
            ),
        ),
        (
            lambda client: client.acknowledge(
                OPERATION_ID, RECOVERY_TOKEN, PUBLICATION_DIGEST
            ),
            MessageType.ACK,
            (1, 3, 10),
            _summary(
                state=OperationState.ACKNOWLEDGED,
                resume_state=OperationState.ACKNOWLEDGED,
                flags=0x0012,
                publication_digest=PUBLICATION_DIGEST,
            ),
        ),
    ),
)
def test_operation_requests_use_exact_types_fields_and_summary_layout(
    call: Callable[[CustodianClient], object],
    request_type: MessageType,
    expected_fields: tuple[int, ...],
    response_payload: bytes,
) -> None:
    result, packet = _exchange(
        call,
        lambda _packet, request_id: _frame(
            RESPONSE_BIT | request_type, request_id, response_payload
        ),
    )

    message_type, _request_id, fields = _decode_request(packet)
    assert message_type == request_type
    assert tuple(field_id for field_id, _value in fields) == expected_fields
    assert result.operation_id == OPERATION_ID
    assert result.recovery_token == RECOVERY_TOKEN


def test_start_encodes_exact_bounded_string_lists_without_exposing_them() -> None:
    result, packet = _exchange(
        lambda client: client.start(_request()),
        lambda _packet, request_id: _frame(
            RESPONSE_BIT | MessageType.START,
            request_id,
            _request_summary(
                MessageType.START,
                state=OperationState.RUNNING,
                resume_state=OperationState.RUNNING,
                exit_status=-(2**31),
            ),
        ),
    )

    _message_type, _request_id, fields = _decode_request(packet)
    values = dict(fields)
    assert values[1] == OPERATION_ID
    assert values[2] == EXECUTABLE_DIGEST
    assert values[4] == b"stage/application/.venv/bin/python3.11"
    assert struct.unpack_from(">I", values[5], 0) == (4,)
    assert struct.unpack_from(">I", values[6], 0) == (4,)
    assert not hasattr(result, "argv")
    assert not hasattr(result, "environment")
    assert "stage/application" not in repr(result)
    assert "LIVE_EXECUTION" not in repr(result)


def test_start_sends_one_credential_directory_and_manifest(
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials"
    credentials.mkdir(mode=0o700)
    leaf = credentials / "database-password"
    leaf.write_bytes(b"private-value")
    leaf.chmod(0o600)
    descriptor = os.open(
        credentials,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    info = os.fstat(descriptor)
    leaf_info = leaf.stat(follow_symlinks=False)
    manifest = (
        b"P6CM1"
        + struct.pack(
            ">IQQIII",
            1,
            info.st_dev,
            info.st_ino,
            info.st_uid,
            info.st_gid,
            info.st_mode,
        )
        + struct.pack(">I", len(leaf.name))
        + leaf.name.encode()
        + struct.pack(
            ">QQIIIQQqq",
            leaf_info.st_dev,
            leaf_info.st_ino,
            leaf_info.st_uid,
            leaf_info.st_gid,
            leaf_info.st_mode,
            leaf_info.st_nlink,
            leaf_info.st_size,
            leaf_info.st_mtime_ns,
            leaf_info.st_ctime_ns,
        )
        + hashlib.sha256(leaf.read_bytes()).digest()
    )
    request = NativeOperationRequest(
        operation_id=OPERATION_ID,
        executable="stage/application/.venv/bin/python3.11",
        executable_sha256=EXECUTABLE_DIGEST,
        argv=("stage/application/.venv/bin/python3.11",),
        environment=(
            "CREDENTIALS_DIRECTORY=/proc/self/fd/5",
            "HOME=/tmp",
            "LANG=C.UTF-8",
            "LC_ALL=C.UTF-8",
            "LIVE_EXECUTION_ENABLED=false",
            "LIVE_TRADING_APPROVED=false",
            "LIVE_TRADING_ENABLED=false",
            "PATH=/usr/bin:/bin",
            f"TRADING_PACKAGE6_APPROVAL_SHA256={'a' * 64}",
            "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH=/tmp/activation.json",
            "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH=/tmp/authority.json",
            "TRADING_PACKAGE6_STAGING_SCOPE=PACKAGE6_STAGING_V2",
            "TRADING_MODE=paper",
            "TZ=UTC",
        ),
        credential_directory_fd=descriptor,
        credential_manifest=manifest,
    )
    observed: list[tuple[bytes, list[tuple[int, int, bytes]]]] = []
    client_socket, server_socket = socket.socketpair(
        socket.AF_UNIX, socket.SOCK_SEQPACKET
    )
    client_socket.set_inheritable(False)
    server_socket.set_inheritable(False)
    client = _scripted_client(
        lambda _packet, _request_id: b"",
        [],
        timeout_seconds=0.5,
    )
    client._socket = client_socket
    client._send_receive = CustodianClient._send_receive.__get__(
        client, CustodianClient
    )

    def serve() -> None:
        packet, ancillary, _flags, _address = server_socket.recvmsg(
            MAX_PAYLOAD + HEADER_SIZE,
            socket.CMSG_SPACE(struct.calcsize("i")),
        )
        observed.append((packet, ancillary))
        for _level, _kind, raw in ancillary:
            for (received,) in struct.iter_unpack("i", raw):
                os.close(received)
        request_id = packet[12:28]
        response = _frame(
            RESPONSE_BIT | MessageType.START,
            request_id,
            _summary(
                state=OperationState.RUNNING,
                resume_state=OperationState.RUNNING,
                exit_status=-(2**31),
                request_digest=hashlib.sha256(
                    struct.pack(">H", int(MessageType.START))
                    + _operation_payload(request)
                ).digest(),
            ),
        )
        assert os.write(server_socket.fileno(), response) == len(response)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        result = client.start(request)
        assert result.state is OperationState.RUNNING
        os.fstat(descriptor)
    finally:
        thread.join(timeout=2)
        client_socket.close()
        server_socket.close()
        os.close(descriptor)

    packet, ancillary = observed[0]
    assert _decode_request(packet)[2][-1][0] == 11
    assert len(ancillary) == 1
    level, kind, raw = ancillary[0]
    assert (level, kind, len(raw)) == (
        socket.SOL_SOCKET,
        socket.SCM_RIGHTS,
        struct.calcsize("i"),
    )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload[:16] + b"\x77" * 16 + payload[32:],
        lambda payload: payload[:40] + b"\x66" * 32 + payload[72:],
        lambda payload: payload[:72] + b"\x55" * 32 + payload[104:],
        lambda payload: payload[:32]
        + bytes((OperationState.RUNNING, OperationState.RESULT_RETAINED))
        + payload[34:],
        lambda payload: payload[:34]
        + struct.pack(">H", 0)
        + payload[36:],
        lambda payload: payload[:36] + struct.pack(">i", 0) + payload[40:],
    ),
)
def test_authorized_summary_rejects_identity_and_state_incoherence(
    mutation: Callable[[bytes], bytes],
) -> None:
    prior = _summary(
        state=OperationState.RUNNING,
        resume_state=OperationState.RUNNING,
        exit_status=-(2**31),
    )
    payload = mutation(prior)
    with pytest.raises(CustodianProtocolError, match="malformed"):
        _exchange(
            lambda client: client.status(OPERATION_ID, RECOVERY_TOKEN),
            lambda _packet, request_id: _frame(
                RESPONSE_BIT | MessageType.STATUS,
                request_id,
                payload,
            ),
        )


def test_read_transcript_validates_exact_metadata_reserved_bytes_and_digest() -> None:
    retained = b"bounded transcript"
    digest = hashlib.sha256(retained).digest()
    metadata = (
        OPERATION_ID
        + bytes((TranscriptStream.STDOUT, 0x01))
        + b"\x00\x00"
        + struct.pack(">QIQQ", 0, len(retained), len(retained), len(retained))
        + digest
    )
    result, packet = _exchange(
        lambda client: client.read_transcript(
            OPERATION_ID,
            RECOVERY_TOKEN,
            TranscriptStream.STDOUT,
            offset=0,
            length=4096,
        ),
        lambda _packet, request_id: _frame(
            RESPONSE_BIT | MessageType.READ_TRANSCRIPT,
            request_id,
            metadata + retained,
        ),
    )

    message_type, _request_id, fields = _decode_request(packet)
    assert message_type == MessageType.READ_TRANSCRIPT
    assert tuple(field_id for field_id, _value in fields) == (1, 3, 7, 8, 9)
    assert result.operation_id == OPERATION_ID
    assert result.stream is TranscriptStream.STDOUT
    assert result.offset == 0
    assert result.data == retained
    assert result.eof is True
    assert result.truncated is False
    assert result.sha256 == digest


def test_publish_returns_exact_native_manifest_receipt() -> None:
    payload = (
        _summary(
            flags=0x0003,
            publication_digest=PUBLICATION_DIGEST,
        )
        + PUBLICATION_DIGEST
    )
    result, packet = _exchange(
        lambda client: client.publish_bundle(
            OPERATION_ID, RECOVERY_TOKEN, PUBLICATION_ID
        ),
        lambda _packet, request_id: _frame(
            RESPONSE_BIT | MessageType.PUBLISH_BUNDLE,
            request_id,
            payload,
        ),
    )

    message_type, _request_id, fields = _decode_request(packet)
    assert message_type == MessageType.PUBLISH_BUNDLE
    assert tuple(field_id for field_id, _value in fields) == (1, 3, 10)
    assert dict(fields)[10] == PUBLICATION_ID
    assert result.operation.operation_id == OPERATION_ID
    assert result.manifest_sha256 == PUBLICATION_DIGEST


def test_recover_requires_bounded_sorted_unique_exact_summaries() -> None:
    second_id = bytes.fromhex("10112233445566778899aabbccddeeff")
    payload = (
        struct.pack(">I", 2)
        + _summary(operation_id=OPERATION_ID)
        + _summary(operation_id=second_id)
    )
    result, packet = _exchange(
        lambda client: client.recover(),
        lambda _packet, request_id: _frame(
            RESPONSE_BIT | MessageType.RECOVER,
            request_id,
            payload,
        ),
    )

    message_type, _request_id, fields = _decode_request(packet)
    assert message_type == MessageType.RECOVER
    assert fields == []
    assert tuple(item.operation_id for item in result) == (
        OPERATION_ID,
        second_id,
    )


@pytest.mark.parametrize(
    ("mutate", "match"),
    (
        (lambda frame, _request: frame[:-1], "malformed"),
        (
            lambda frame, _request: _frame(
                RESPONSE_BIT | MessageType.HELLO,
                frame[12:28],
                b"\x00\x01\x00\x00\x00\x00\x00\x00",
                magic=0,
            ),
            "malformed",
        ),
        (
            lambda frame, _request: _frame(
                RESPONSE_BIT | MessageType.HELLO,
                frame[12:28],
                b"\x00\x01\x00\x00\x00\x00\x00\x00",
                version=2,
            ),
            "malformed",
        ),
        (
            lambda frame, _request: _frame(
                RESPONSE_BIT | MessageType.HELLO,
                frame[12:28],
                b"\x00\x01\x00\x00\x00\x00\x00\x00",
                flags=1,
            ),
            "malformed",
        ),
        (
            lambda frame, _request: _frame(
                MessageType.HELLO,
                frame[12:28],
                b"\x00\x01\x00\x00\x00\x00\x00\x00",
            ),
            "malformed",
        ),
        (
            lambda frame, _request: _frame(
                RESPONSE_BIT | MessageType.STATUS,
                frame[12:28],
                b"\x00\x01\x00\x00\x00\x00\x00\x00",
            ),
            "malformed",
        ),
        (
            lambda frame, _request: _frame(
                RESPONSE_BIT | MessageType.HELLO,
                b"\x99" * 16,
                b"\x00\x01\x00\x00\x00\x00\x00\x00",
            ),
            "malformed",
        ),
        (
            lambda frame, _request: _frame(
                RESPONSE_BIT | MessageType.HELLO,
                frame[12:28],
                b"\x00\x01\x00\x00\x00\x00\x00\x00",
                payload_length=7,
            ),
            "malformed",
        ),
        (
            lambda frame, _request: _frame(
                RESPONSE_BIT | MessageType.HELLO,
                frame[12:28],
                b"\x00\x01\x00\x00\x00\x00\x00\x00",
                crc32=0,
            ),
            "malformed",
        ),
        (
            lambda frame, _request: frame + b"\x00",
            "malformed",
        ),
    ),
)
def test_malformed_frames_fail_closed_without_echoing_private_bytes(
    mutate: Callable[[bytes, bytes], bytes],
    match: str,
) -> None:
    private = b"/tmp/private-stage SECRET=value"

    def response(_packet: bytes, request_id: bytes) -> bytes:
        valid = _frame(
            RESPONSE_BIT | MessageType.HELLO,
            request_id,
            b"\x00\x01\x00\x00\x00\x00\x00\x00",
        )
        return mutate(valid, request_id)

    with pytest.raises(CustodianProtocolError, match=match) as raised:
        _exchange(lambda client: client.hello(), response)

    assert private.decode() not in str(raised.value)
    assert not raised.value.args or raised.value.args == (
        "custodian response is malformed",
    )


@pytest.mark.parametrize(
    "payload",
    (
        _summary(state=16),
        _summary(resume_state=16),
        _summary(flags=0x8000),
        _summary(operation_id=b"\x99" * 16),
        _summary(recovery_token=bytes(16)),
        _summary()[:-1],
    ),
)
def test_summary_ids_enums_lengths_and_flags_fail_closed(payload: bytes) -> None:
    with pytest.raises(CustodianProtocolError, match="malformed"):
        _exchange(
            lambda client: client.status(OPERATION_ID, RECOVERY_TOKEN),
            lambda _packet, request_id: _frame(
                RESPONSE_BIT | MessageType.STATUS,
                request_id,
                payload,
            ),
        )


@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: payload[:18] + b"\x00\x01" + payload[20:],
        lambda payload: payload[:17] + b"\x80" + payload[18:],
        lambda payload: payload[:16] + b"\x02" + payload[17:],
        lambda payload: b"\x99" * 16 + payload[16:],
        lambda payload: payload[:28] + struct.pack(">I", 99) + payload[32:],
        lambda payload: payload[:20] + struct.pack(">Q", 1) + payload[28:],
    ),
)
def test_transcript_unknown_or_reserved_metadata_fails_closed(
    mutate: Callable[[bytes], bytes],
) -> None:
    retained = b"x"
    payload = (
        OPERATION_ID
        + bytes((TranscriptStream.STDOUT, 0x01))
        + b"\x00\x00"
        + struct.pack(">QIQQ", 0, 1, 1, 1)
        + hashlib.sha256(retained).digest()
        + retained
    )

    with pytest.raises(CustodianProtocolError, match="malformed"):
        _exchange(
            lambda client: client.read_transcript(
                OPERATION_ID,
                RECOVERY_TOKEN,
                TranscriptStream.STDOUT,
                offset=0,
                length=1,
            ),
            lambda _packet, request_id: _frame(
                RESPONSE_BIT | MessageType.READ_TRANSCRIPT,
                request_id,
                mutate(payload),
            ),
        )


@pytest.mark.parametrize(
    ("status", "retryable", "code"),
    (
        (PublicStatus.INVALID_FRAME, False, "INVALID_FRAME"),
        (PublicStatus.UNSUPPORTED_VERSION, False, "UNSUPPORTED_VERSION"),
        (PublicStatus.UNAUTHORIZED, False, "UNAUTHORIZED"),
        (PublicStatus.INVALID_REQUEST, False, "INVALID_REQUEST"),
        (PublicStatus.NOT_FOUND, False, "NOT_FOUND"),
        (PublicStatus.CONFLICT, False, "CONFLICT"),
        (PublicStatus.LIMIT_EXCEEDED, False, "LIMIT_EXCEEDED"),
        (PublicStatus.TIMEOUT, True, "TIMEOUT"),
        (PublicStatus.RECOVERY_REQUIRED, True, "RECOVERY_REQUIRED"),
        (PublicStatus.INTERNAL, False, "INTERNAL"),
        (PublicStatus.INTERNAL, False, "UNSUPPORTED_KERNEL"),
    ),
)
def test_public_error_status_mapping_is_exact_and_public_only(
    status: PublicStatus,
    retryable: bool,
    code: str,
) -> None:
    with pytest.raises(CustodianRequestError) as raised:
        _exchange(
            lambda client: client.status(OPERATION_ID, RECOVERY_TOKEN),
            lambda _packet, request_id: _frame(
                ERROR_TYPE,
                request_id,
                _error_payload(
                    status,
                    retryable=retryable,
                    code=code,
                ),
            ),
        )

    assert raised.value.status is status
    assert raised.value.public_code == code
    assert raised.value.retryable is retryable
    assert str(raised.value) == f"custodian request failed: {code}"
    assert not hasattr(raised.value, "errno")
    assert not hasattr(raised.value, "payload")


@pytest.mark.parametrize(
    "payload",
    (
        _error_payload(PublicStatus.OK, code="OK"),
        _error_payload(PublicStatus.TIMEOUT, retryable=False),
        _error_payload(PublicStatus.CONFLICT, retryable=True),
        _error_payload(PublicStatus.INTERNAL, code="PRIVATE_PATH"),
        _error_payload(PublicStatus.CONFLICT)[:-1],
        _error_payload(PublicStatus.CONFLICT) + b"x",
    ),
)
def test_malformed_error_envelopes_fail_closed(payload: bytes) -> None:
    with pytest.raises(CustodianProtocolError, match="malformed"):
        _exchange(
            lambda client: client.status(OPERATION_ID, RECOVERY_TOKEN),
            lambda _packet, request_id: _frame(
                ERROR_TYPE,
                request_id,
                payload,
            ),
        )


def test_public_error_non_absent_state_requires_recovery_authority() -> None:
    payload = _error_payload(
        PublicStatus.RECOVERY_REQUIRED,
        retryable=True,
        state=OperationState.RUNNING,
        recovery_token=bytes(16),
    )

    with pytest.raises(CustodianProtocolError, match="malformed"):
        _exchange(
            lambda client: client.start(_request()),
            lambda _packet, request_id: _frame(
                ERROR_TYPE,
                request_id,
                payload,
            ),
        )


@pytest.mark.parametrize(
    "response_flags",
    (
        0,
        socket.MSG_CTRUNC,
        socket.MSG_TRUNC,
        0x40000000,
    ),
    ids=("ancillary", "control-truncated", "packet-truncated", "unknown-flag"),
)
def test_rejected_recvmsg_closes_every_received_rights_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    response_flags: int,
) -> None:
    received_descriptor = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    ancillary = (
        (
            socket.SOL_SOCKET,
            socket.SCM_RIGHTS,
            struct.pack("i", received_descriptor),
        ),
    )

    class ScriptedSocket:
        def send(self, packet: bytes) -> int:
            return len(packet)

        def recvmsg(
            self, _size: int, _ancillary_size: int = 0
        ) -> tuple[bytes, object, int, None]:
            return b"response", ancillary, response_flags, None

    client = CustodianClient.__new__(CustodianClient)
    client._socket = ScriptedSocket()
    client._timeout_seconds = 0.1
    monkeypatch.setattr(
        "services.paper_runtime.custodian_client.select.select",
        lambda read, write, exceptional, timeout: (
            read,
            write,
            exceptional,
        ),
    )
    try:
        with pytest.raises(CustodianProtocolError, match="malformed"):
            client._send_receive(b"request")
        with pytest.raises(OSError):
            os.fstat(received_descriptor)
    finally:
        try:
            os.close(received_descriptor)
        except OSError:
            pass


def test_peer_auth_mismatch_fails_before_any_request() -> None:
    client = CustodianClient.__new__(CustodianClient)
    client._attestation = _attestation(peer_uid=os.geteuid() + 1)

    with pytest.raises(CustodianProtocolError, match="attestation"):
        client._validate_peer_identity(
            (os.getpid(), os.geteuid(), os.getegid())
        )


def test_timeout_and_disconnect_are_distinct_public_fail_closed_results() -> None:
    timeout_client = _scripted_client(
        lambda _packet, _request_id: (_ for _ in ()).throw(
            CustodianTimeout("custodian response timed out")
        ),
        [],
        timeout_seconds=0.02,
    )
    with pytest.raises(CustodianTimeout) as timeout:
        timeout_client.hello()
    assert str(timeout.value) == "custodian response timed out"

    disconnected_client = _scripted_client(
        lambda _packet, _request_id: (_ for _ in ()).throw(
            CustodianProtocolError("custodian endpoint is disconnected")
        ),
        [],
        timeout_seconds=0.02,
    )
    with pytest.raises(CustodianProtocolError, match="disconnected"):
        disconnected_client.hello()


def test_request_id_replay_is_rejected_before_second_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_id = b"\x55" * 16
    monkeypatch.setattr(
        "services.paper_runtime.custodian_client.secrets.token_bytes",
        lambda _size: request_id,
    )
    observed: list[bytes] = []
    client = _scripted_client(
        lambda _packet, observed_id: _frame(
            RESPONSE_BIT | MessageType.HELLO,
            observed_id,
            b"\x00\x01\x00\x00\x00\x00\x00\x00",
        ),
        observed,
        timeout_seconds=0.5,
    )
    client.hello()
    with pytest.raises(CustodianProtocolError, match="request identity"):
        client.hello()
    assert len(observed) == 1


def test_invalid_requests_fail_before_transport_without_private_echo() -> None:
    mutations = (
        lambda request: NativeOperationRequest(
            operation_id=bytes(16),
            executable=request.executable,
            executable_sha256=request.executable_sha256,
            argv=request.argv,
            environment=request.environment,
        ),
        lambda request: NativeOperationRequest(
            operation_id=request.operation_id,
            executable="/absolute/private/path",
            executable_sha256=request.executable_sha256,
            argv=request.argv,
            environment=request.environment,
        ),
        lambda request: NativeOperationRequest(
            operation_id=request.operation_id,
            executable=request.executable,
            executable_sha256=request.executable_sha256,
            argv=(),
            environment=request.environment,
        ),
        lambda request: NativeOperationRequest(
            operation_id=request.operation_id,
            executable=request.executable,
            executable_sha256=request.executable_sha256,
            argv=request.argv,
            environment=("DUP=one", "DUP=two"),
        ),
        lambda request: NativeOperationRequest(
            operation_id=request.operation_id,
            executable=request.executable,
            executable_sha256=request.executable_sha256,
            argv=request.argv,
            environment=("LIVE_EXECUTION_ENABLED=true",),
        ),
        lambda request: NativeOperationRequest(
            operation_id=request.operation_id,
            executable=request.executable,
            executable_sha256=request.executable_sha256,
            argv=("x" * 4097,),
            environment=request.environment,
        ),
    )
    for mutation in mutations:
        observed: list[bytes] = []
        client = _scripted_client(
            lambda _packet, _request_id: b"",
            observed,
            timeout_seconds=0.5,
        )
        with pytest.raises((TypeError, ValueError)) as raised:
            client.start(mutation(_request()))
        assert "/absolute/private/path" not in str(raised.value)
        assert "DUP=one" not in str(raised.value)
        assert observed == []


def test_recover_rejects_duplicate_and_unsorted_operation_ids() -> None:
    for payload in (
        struct.pack(">I", 2) + _summary() + _summary(),
        struct.pack(">I", 2)
        + _summary(operation_id=b"\x10" * 16)
        + _summary(operation_id=b"\x01" * 16),
        struct.pack(">I", 17) + _summary() * 17,
        struct.pack(">I", 1) + _summary() + b"x",
    ):
        with pytest.raises(CustodianProtocolError, match="malformed"):
            _exchange(
                lambda client: client.recover(),
                lambda _packet, request_id, value=payload: _frame(
                    RESPONSE_BIT | MessageType.RECOVER,
                    request_id,
                    value,
                ),
            )


@pytest.fixture(scope="module")
def native_helper() -> Path:
    build_dir = Path(
        tempfile.mkdtemp(prefix="package6-python-client-", dir="/tmp")
    ).resolve()
    completed = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            "-C",
            str(NATIVE_ROOT),
            f"BUILD_DIR={build_dir}",
            "test-service",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    helper = build_dir / "test-package6-custodian"
    assert helper.is_file()
    return helper


def test_native_helper_and_python_client_exchange_exact_hello_over_socketpair(
    native_helper: Path,
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="package6-python-native-", dir="/tmp"
    ) as temporary:
        root = Path(temporary)
        roots = []
        descriptors = []
        for name in ("journal", "source", "cgroup", "evidence"):
            path = root / name
            path.mkdir(mode=0o700)
            roots.append(path)
            descriptors.append(
                os.open(
                    path,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                )
            )
        client_socket, helper_socket = socket.socketpair(
            socket.AF_UNIX, socket.SOCK_SEQPACKET
        )
        client_socket.set_inheritable(False)
        helper_socket.set_inheritable(True)
        helper_hash = hashlib.sha256(native_helper.read_bytes()).hexdigest()
        clean_environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "LISTEN_FDS",
                "LISTEN_PID",
                "P6C_FAILPOINT",
                "LIVE_EXECUTION",
                "LIVE_TRADING",
                "P6C_DELEGATED_CGROUP_TEST_ROOT",
            }
        }
        process = subprocess.Popen(
            [
                str(native_helper),
                f"--socket-fd={helper_socket.fileno()}",
                f"--journal-root-fd={descriptors[0]}",
                f"--source-root-fd={descriptors[1]}",
                f"--cgroup-root-fd={descriptors[2]}",
                f"--evidence-root-fd={descriptors[3]}",
                f"--controller-uid={os.geteuid()}",
                "--live-execution=false",
                "--live-trading=false",
            ],
            pass_fds=(helper_socket.fileno(), *descriptors),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_environment,
        )
        helper_socket.close()
        for descriptor in descriptors:
            os.close(descriptor)
        try:
            client = CustodianClient(
                client_socket,
                _attestation(helper_binary_sha256=helper_hash),
                timeout_seconds=2,
            )
        except CustodianProtocolError as error:
            if not isinstance(error.__cause__, PermissionError):
                raise
            client_socket.close()
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 1
            assert stdout == b""
            assert stderr == b"package6-custodian: service failure\n"
            pytest.skip(
                "native endpoint authority unavailable: "
                "Unix socket attestation was denied"
            )
        try:
            hello = client.hello()
            assert hello.protocol_version == 1
            assert hello.features == ()
        finally:
            client.close()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0
        assert stdout == b""
        assert stderr == b""
        assert not any(roots[2].iterdir())


@pytest.mark.host_coupled
def test_authority_gated_python_native_full_lifecycle_and_recovery(
    native_helper: Path,
) -> None:
    delegated_value = os.environ.get("P6C_PACKAGE6_HOST_CGROUP_ROOT")
    if (
        os.environ.get("P6C_PACKAGE6_NATIVE_HOST_GATE") != "1"
        or not delegated_value
    ):
        pytest.skip("explicit Package 6 native host authority is required")
    delegated_root = Path(delegated_value).resolve(strict=True)
    helper_hash = hashlib.sha256(native_helper.read_bytes()).hexdigest()

    with tempfile.TemporaryDirectory(
        prefix="package6-python-native-host-", dir="/tmp"
    ) as temporary:
        root = Path(temporary)
        journal_root = root / "journal"
        source_root = root / "source"
        evidence_root = root / "evidence"
        for path in (journal_root, source_root, evidence_root):
            path.mkdir(mode=0o700)
        for name, source in (("sleep", Path("/bin/sleep")), ("true", Path("/bin/true"))):
            target = source_root / name
            shutil.copyfile(source, target)
            target.chmod(0o500)

        clean_environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "LISTEN_FDS",
                "LISTEN_PID",
                "P6C_FAILPOINT",
                "LIVE_EXECUTION",
                "LIVE_TRADING",
                "P6C_DELEGATED_CGROUP_TEST_ROOT",
            }
        }

        def launch() -> tuple[CustodianClient, subprocess.Popen[bytes]]:
            roots = (journal_root, source_root, delegated_root, evidence_root)
            descriptors = tuple(
                os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                for path in roots
            )
            client_socket, helper_socket = socket.socketpair(
                socket.AF_UNIX, socket.SOCK_SEQPACKET
            )
            client_socket.set_inheritable(False)
            helper_socket.set_inheritable(True)
            process = subprocess.Popen(
                [
                    str(native_helper),
                    f"--socket-fd={helper_socket.fileno()}",
                    f"--journal-root-fd={descriptors[0]}",
                    f"--source-root-fd={descriptors[1]}",
                    f"--cgroup-root-fd={descriptors[2]}",
                    f"--evidence-root-fd={descriptors[3]}",
                    f"--controller-uid={os.geteuid()}",
                    "--live-execution=false",
                    "--live-trading=false",
                ],
                pass_fds=(helper_socket.fileno(), *descriptors),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=clean_environment,
            )
            helper_socket.close()
            for descriptor in descriptors:
                os.close(descriptor)
            return (
                CustodianClient(
                    client_socket,
                    _attestation(
                        helper_binary_sha256=helper_hash,
                        peer_pid=process.pid,
                    ),
                    timeout_seconds=10,
                ),
                process,
            )

        def request(
            operation_id: bytes,
            executable: str,
            argv: tuple[str, ...],
        ) -> NativeOperationRequest:
            executable_path = source_root / executable
            environment = tuple(
                sorted(
                    (
                        "HOME=/tmp",
                        "LANG=C.UTF-8",
                        "LC_ALL=C.UTF-8",
                        "LIVE_EXECUTION_ENABLED=false",
                        "LIVE_TRADING_APPROVED=false",
                        "LIVE_TRADING_ENABLED=false",
                        "PATH=/usr/bin:/bin",
                        "TRADING_MODE=paper",
                        f"TRADING_PACKAGE6_APPROVAL_SHA256={'a' * 64}",
                        "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH=/tmp/activation.json",
                        "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH=/tmp/authority.json",
                        "TRADING_PACKAGE6_STAGING_SCOPE=PACKAGE6_STAGING_V2",
                        "TZ=UTC",
                    )
                )
            )
            return NativeOperationRequest(
                operation_id=operation_id,
                executable=executable,
                executable_sha256=hashlib.sha256(
                    executable_path.read_bytes()
                ).digest(),
                argv=argv,
                environment=environment,
            )

        def publish_and_ack(
            client: CustodianClient,
            status: NativeOperationStatus,
            publication_seed: bytes,
        ) -> None:
            for stream in (TranscriptStream.STDOUT, TranscriptStream.STDERR):
                transcript = client.read_transcript(
                    status.operation_id,
                    status.recovery_token,
                    stream,
                    offset=0,
                    length=4096,
                )
                assert transcript.eof
            receipt = client.publish_bundle(
                status.operation_id,
                status.recovery_token,
                hashlib.sha256(publication_seed).digest(),
            )
            acknowledged = client.acknowledge(
                status.operation_id,
                status.recovery_token,
                receipt.manifest_sha256,
            )
            assert acknowledged.state is OperationState.ACKNOWLEDGED

        client, process = launch()
        assert client.hello().protocol_version == 1
        start_request = request(b"\x10" * 16, "sleep", ("sleep", "30"))
        started = client.start(start_request)
        observed = client.status(started.operation_id, started.recovery_token)
        stopped = client.stop(observed.operation_id, observed.recovery_token)
        publish_and_ack(client, stopped, b"start")
        run_request = request(b"\x20" * 16, "true", ("true",))
        completed = client.run_once(run_request)
        publish_and_ack(client, completed, b"run-once")

        disconnect_request = request(
            b"\x30" * 16, "sleep", ("sleep", "30")
        )
        disconnected = client.start(disconnect_request)
        client.close()
        process.communicate(timeout=15)

        recovered_client, recovered_process = launch()
        recovered = {
            status.operation_id: status
            for status in recovered_client.recover()
        }[disconnected.operation_id]
        if recovered.state is not OperationState.RESULT_RETAINED:
            recovered = recovered_client.stop(
                recovered.operation_id, recovered.recovery_token
            )
        publish_and_ack(recovered_client, recovered, b"disconnect")
        recovered_client.close()
        stdout, stderr = recovered_process.communicate(timeout=15)
        assert recovered_process.returncode == 0
        assert stdout == b""
        assert stderr == b""
