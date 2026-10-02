"""Provider-free P3 routing; neither type conveys execution authority."""

import os
import select
from collections.abc import Callable
from datetime import UTC, datetime

_REQUEST = b'\x00P3_REPLICA_FENCE_V1:'
_ACK = b'P3_REPLICA_GO_V1:'


class P3PreparedSpawnMarker:
    __slots__ = ()


class P3NativePreparedSpawnMarker:
    """Routing only; the concrete native provider checks one-use authority."""
    __slots__ = ()


class P3SpawnError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(f'{reason}: {message}')
        self.reason = reason


class ReplicaFenceRequests:
    """Bounded control frames in the driver's existing stderr stream."""

    def __init__(self) -> None:
        self._line: bytes | None = b''
        self._sequence = 0

    def feed(self, chunk: bytes) -> int | None:
        request = None
        parts = chunk.split(b'\n')
        for index, part in enumerate(parts):
            if self._line is not None:
                self._line += part
                if len(self._line) > 64:
                    if self._line.startswith(_REQUEST):
                        raise P3SpawnError('P3_REPLICA_FENCE_REJECTED', 'oversized replica fence frame')
                    self._line = None
            if index == len(parts)-1:
                break
            if self._line is not None and self._line.startswith(_REQUEST):
                value = self._line[len(_REQUEST):]
                if (not 1 <= len(value) <= 9 or not value.isdigit() or value.startswith(b'0')
                    or int(value) != self._sequence+1 or request is not None):
                    raise P3SpawnError('P3_REPLICA_FENCE_REJECTED', 'invalid replica fence sequence')
                self._sequence = int(value)
                request = self._sequence
            self._line = b''
        return request


def replica_fence_ack(sequence: int) -> bytes:
    return _ACK + str(sequence).encode('ascii') + b'\n'


def parent_replica_fence(*, expires_at: datetime | None = None) -> Callable[[], None]:
    """Block each replica until its own worker pipe returns a fresh grant.

    Replica children use DEVNULL streams and cannot inherit this handshake.
    The grant is not an execution receipt or a reusable authority artifact.
    Official callers carry their validated authorization deadline: full-source
    parent checks may exceed30s, but a wait never exceeds300s or that deadline.
    """
    sequence = 0

    def check() -> None:
        nonlocal sequence
        sequence += 1
        request = _REQUEST + str(sequence).encode('ascii') + b'\n'
        expected = replica_fence_ack(sequence)
        timeout = 30.0
        if expires_at is not None:
            timeout = min(300.0, (expires_at-datetime.now(UTC)).total_seconds())
            if timeout <= 0:
                raise P3SpawnError('P3_REPLICA_FENCE_REJECTED', 'replica authorization expired')
        try:
            if (os.write(2, request) != len(request) or not select.select([0], [], [], timeout)[0]
                or os.read(0, len(expected)+1) != expected):
                raise P3SpawnError('P3_REPLICA_FENCE_REJECTED', 'parent did not grant the current replica')
            if expires_at is not None and datetime.now(UTC) >= expires_at:
                raise P3SpawnError('P3_REPLICA_FENCE_REJECTED', 'replica authorization expired')
        except OSError as error:
            raise P3SpawnError('P3_REPLICA_FENCE_REJECTED', 'parent fence pipe is unavailable') from error

    return check
