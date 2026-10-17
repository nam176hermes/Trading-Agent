"""Private pipe handshake; synthetic transport never grants runtime authority."""
import os
import selectors
import subprocess
import sys

import pytest


REQUEST = b'\x00P3_REPLICA_FENCE_V1:'
ACK = b'P3_REPLICA_GO_V1:'


def test_fence_parser_handles_fragmented_frames_and_bounded_log_lines():
    from services.job_worker.p3_spawn_interface import ReplicaFenceRequests
    parser = ReplicaFenceRequests()
    assert parser.feed(b'x' * 1000000) is None
    assert parser.feed(REQUEST + b'1\n') is None  # Still part of the discarded log line.
    assert parser.feed(REQUEST[:8]) is None
    assert parser.feed(REQUEST[8:] + b'1\nordinary log\n') == 1
    assert parser.feed(REQUEST + b'2\n') == 2


@pytest.mark.parametrize('raw', [REQUEST+b'0\n', REQUEST+b'01\n', REQUEST+b'2\n',
    REQUEST+b'no\n', REQUEST+b'1\n'+REQUEST+b'2\n', REQUEST+b'1'*100+b'\n'])
def test_fence_parser_rejects_invalid_or_pipelined_requests(raw):
    from services.job_worker.p3_spawn_interface import ReplicaFenceRequests, P3SpawnError
    with pytest.raises(P3SpawnError): ReplicaFenceRequests().feed(raw)


def test_fence_parser_rejects_replayed_request():
    from services.job_worker.p3_spawn_interface import ReplicaFenceRequests, P3SpawnError
    parser = ReplicaFenceRequests()
    assert parser.feed(REQUEST+b'1\n') == 1
    with pytest.raises(P3SpawnError): parser.feed(REQUEST+b'1\n')


@pytest.mark.parametrize('reply', ['valid', 'wrong', 'eof'])
def test_driver_waits_for_a_matching_parent_grant_on_real_pipes(reply):
    code = '''
from services.job_worker.p3_spawn_interface import parent_replica_fence
fence = parent_replica_fence()
for _ in range(2):
    fence()
print('two grants')
'''
    process = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None and process.stderr is not None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stderr, selectors.EVENT_READ)
            for sequence in (1, 2):
                assert selector.select(5), 'driver did not request its fresh fence'
                assert process.stderr.readline(64) == REQUEST+str(sequence).encode()+b'\n'
                if reply == 'eof':
                    process.stdin.close()
                    process.stdin = None
                    break
                value = sequence if reply == 'valid' else sequence+1
                os.write(process.stdin.fileno(), ACK+str(value).encode()+b'\n')
                if reply == 'wrong': break
        stdout, stderr = process.communicate(timeout=5)
        if reply == 'valid':
            assert process.returncode == 0, stderr
            assert stdout == b'two grants\n'
        else:
            assert process.returncode != 0 and b'P3_REPLICA_FENCE_REJECTED' in stderr
            assert not stdout
    finally:
        if process.poll() is None: process.kill()
        process.communicate()


@pytest.mark.parametrize('remaining, expected_wait', [(45, 45), (600, 300)])
def test_parent_grant_wait_is_bounded_by_authorization(monkeypatch, remaining, expected_wait):
    from datetime import datetime, timedelta, timezone
    from services.job_worker import p3_spawn_interface as interface
    now = datetime.now(timezone.utc)

    class Clock:
        @staticmethod
        def now(_zone):
            return now

    monkeypatch.setattr(interface, 'datetime', Clock, raising=False)
    writes = []
    def write(fd, raw):
        writes.append((fd, raw))
        return len(raw)
    def ready(readers, writers, errors, timeout):
        assert (readers, writers, errors, timeout) == ([0], [], [], expected_wait)
        return readers, [], []
    monkeypatch.setattr(interface.os, 'write', write)
    monkeypatch.setattr(interface.os, 'read', lambda fd, size: ACK+b'1\n')
    monkeypatch.setattr(interface.select, 'select', ready)
    interface.parent_replica_fence(expires_at=now+timedelta(seconds=remaining))()
    assert writes == [(2, REQUEST+b'1\n')]


@pytest.mark.parametrize('expired_before', [True, False])
def test_parent_grant_rejects_expiry_before_request_or_after_response(monkeypatch, expired_before):
    from datetime import datetime, timedelta, timezone
    from services.job_worker import p3_spawn_interface as interface
    start = datetime.now(timezone.utc)
    expires = start + timedelta(seconds=1)
    current = expires if expired_before else start

    class Clock:
        @staticmethod
        def now(_zone):
            return current

    monkeypatch.setattr(interface, 'datetime', Clock, raising=False)
    writes = []
    def write(fd, raw):
        writes.append(raw)
        return len(raw)
    def response(readers, writers, errors, timeout):
        nonlocal current
        assert timeout == 1
        current = expires
        return readers, [], []
    monkeypatch.setattr(interface.os, 'write', write)
    monkeypatch.setattr(interface.os, 'read', lambda fd, size: ACK+b'1\n')
    monkeypatch.setattr(interface.select, 'select', response)
    with pytest.raises(interface.P3SpawnError, match='expired'):
        interface.parent_replica_fence(expires_at=expires)()
    assert writes == ([] if expired_before else [REQUEST+b'1\n'])
