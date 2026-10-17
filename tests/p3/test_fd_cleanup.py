"""Actual P3 owners must retire every FD even when Linux close reports failure."""
import errno
import sys
import os

import pytest

from tests.p3.test_holdout_session import session_inputs  # noqa: F401
from tests.p3.test_reference_input import reference_seed  # noqa: F401
from tests.p3.test_native_spawn import native_provider  # noqa: F401
from tests.p3.test_spawn_capability import synthetic_provider  # noqa: F401
from tests.p3.test_output_custody import custody


@pytest.fixture
def close_fault(monkeypatch):
    owned, attempted, failures = [], [], {}
    real_close = os.close

    def close(fd):
        if fd in owned:
            attempted.append(fd)
            real_close(fd)
            if fd in failures:
                raise failures[fd]
        else:
            real_close(fd)

    # Inject below the real owner. Failed close has already relinquished the FD;
    # retrying its number can close an unrelated, newly allocated descriptor.
    with monkeypatch.context() as patch:
        patch.setattr(os, 'close', close)
        yield owned, attempted, failures
    for fd in set(owned) - set(attempted):
        real_close(fd)  # Only leaks that the RED implementation never attempted.


def assert_closed_once(owned, attempted):
    assert len(owned) >= 2
    assert sorted(attempted) == sorted(owned)
    for fd in owned:
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == errno.EBADF


def exception_chain(error):
    pending, seen = [error], []
    while pending:
        current = pending.pop()
        if current is None or any(current is item for item in seen):
            continue
        seen.append(current)
        pending.extend((current.__cause__, current.__context__))
    return seen


@pytest.mark.parametrize('reject', [False, True], ids=['retained-abandon', 'failed-retain'])
def test_output_close_error_retires_all_owned_fds(tmp_path, monkeypatch, close_fault, reject):
    owned, attempted, failures = close_fault
    handle, output, _ = custody(tmp_path)
    owned.extend((handle._output, handle._parent))
    for name in ('r1', 'r2', 'r1/artifacts', 'r2/artifacts'):
        (output/name).mkdir(mode=0o700)
    primary = ValueError('synthetic output validation refusal')
    first = OSError(errno.EIO, 'synthetic first close failure')
    later = OSError(errno.EPERM, 'synthetic later close failure')
    real_open = os.open

    def track_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if flags & os.O_DIRECTORY and kwargs.get('dir_fd') in owned:
            owned.append(fd)
            if len(owned) == 3:
                failures[fd] = first
            elif len(owned) == 4:
                failures[fd] = later
        return fd

    monkeypatch.setattr(os, 'open', track_open)
    if reject:
        def refuse(*args):
            raise primary
        handle._before_retain = refuse
    with pytest.raises(OSError) as failure:
        if reject:
            handle.retain()
        else:
            handle.retain()
            handle.abandon()
    assert failure.value is first
    if reject:
        assert primary in exception_chain(failure.value)
    assert_closed_once(owned, attempted)
    assert handle._output == handle._parent == -1
    assert handle._retained is None
    assert all((output/name).is_dir() for name in ('r1', 'r2', 'r1/artifacts', 'r2/artifacts'))
    handle.abandon()
    assert_closed_once(owned, attempted)


@pytest.mark.parametrize('primary_type', [ValueError, KeyboardInterrupt], ids=['refusal', 'cancellation'])
@pytest.mark.parametrize('cleanup_fails', [False, True], ids=['cleanup-success', 'cleanup-failure'])
def test_native_failed_consume_closes_every_snapshot(
    native_provider, monkeypatch, close_fault, primary_type, cleanup_fails,
):
    from services.job_worker import p3_native_spawn as native
    owned, attempted, failures = close_fault
    provider, _, _, _ = native_provider
    token = provider.prepare('PRIMARY', 'R1')
    sandbox, snapshot = native._sealed_sandbox_snapshot, native._sealed_closure_file_snapshot
    primary = primary_type('synthetic second mount refusal')
    origin = RuntimeError('synthetic original native cause')
    first = OSError(errno.EIO, 'synthetic sandbox close failure')
    later = OSError(errno.EPERM, 'synthetic mount close failure')

    def track_sandbox(proof):
        fd = sandbox(proof)
        owned.append(fd)
        if cleanup_fails:
            failures[fd] = first
        return fd

    def refuse_second_mount(mount):
        if len(owned) == 2:
            raise primary from origin
        fd = snapshot(mount)
        owned.append(fd)
        if cleanup_fails:
            failures[fd] = later
        return fd

    monkeypatch.setattr(native, '_sealed_sandbox_snapshot', track_sandbox)
    monkeypatch.setattr(native, '_sealed_closure_file_snapshot', refuse_second_mount)
    # A caller's handled error must never become this owner's primary.
    try:
        raise KeyError('unrelated caller error')
    except KeyError:
        with pytest.raises(BaseException) as failure:
            native.consume_prepared_p3_native_spawn(token, job_id=provider._claim.job_id,
                attempt_id=provider._claim.attempt_id)
    assert failure.value is primary
    assert type(failure.value) is primary_type
    assert failure.value.__cause__ is (first if cleanup_fails else origin)
    if cleanup_fails:
        assert first.__cause__ is origin
        assert later not in exception_chain(failure.value)
    assert_closed_once(owned, attempted)
    assert not provider._pending and provider._failed
    with pytest.raises(ValueError, match='consumed'):
        native.consume_prepared_p3_native_spawn(token, job_id=provider._claim.job_id,
            attempt_id=provider._claim.attempt_id)
    with pytest.raises(ValueError):
        provider.prepare('PRIMARY', 'R1')
    assert_closed_once(owned, attempted)


def test_native_success_transfers_fds_inside_unrelated_handler(native_provider, monkeypatch, close_fault):
    from services.job_worker import p3_native_spawn as native
    owned, attempted, failures = close_fault
    provider, _, _, _ = native_provider
    token = provider.prepare('PRIMARY', 'R1')
    first = OSError(errno.EIO, 'synthetic transferred FD close failure')

    def track(make):
        def create(*args, **kwargs):
            fd = make(*args, **kwargs)
            owned.append(fd)
            failures[fd] = first
            return fd
        return create

    for name in ('_sealed_sandbox_snapshot', '_sealed_closure_file_snapshot', '_sealed_memfd'):
        monkeypatch.setattr(native, name, track(getattr(native, name)))
    try:
        raise KeyError('unrelated caller error')
    except KeyError:
        built = native.consume_prepared_p3_native_spawn(token, job_id=provider._claim.job_id,
            attempt_id=provider._claim.attempt_id)
    assert built.close_after_spawn_fds == built.pass_fds == tuple(owned)
    assert not attempted
    assert not provider._pending and not provider._failed
    for fd in owned:
        os.fstat(fd)
    with pytest.raises(ValueError, match='consumed'):
        native.consume_prepared_p3_native_spawn(token, job_id=provider._claim.job_id,
            attempt_id=provider._claim.attempt_id)
    assert not attempted
    # The caller owns these now; its cleanup has no primary, so the helper's
    # first OSError remains visible and all transferred FDs are attempted.
    with pytest.raises(OSError) as failure:
        native._close_descriptors(*built.close_after_spawn_fds)
    assert failure.value is first
    assert_closed_once(owned, attempted)


@pytest.mark.parametrize('custody_failure', [False, True], ids=['input-refusal', 'custody-refusal'])
def test_provider_failure_closes_every_fd(synthetic_provider, monkeypatch, close_fault, custody_failure):
    from services.job_worker import p3_spawn as module
    owned, attempted, failures = close_fault
    provider, job, _ = synthetic_provider
    token = provider.prepare(job)
    primary = ValueError('synthetic final input refusal')
    first = OSError(errno.EIO, 'synthetic first close failure')
    later = OSError(errno.EPERM, 'synthetic later close failure')
    inputs = provider._inputs
    calls = 0

    def final_inputs(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            # Capture the real consume owner's live FD collection at its final
            # validator, excluding transient descriptors already closed by reads.
            owned.extend(sys._getframe(1).f_locals['descriptors'])
            if not custody_failure:
                failures[owned[0]] = first
                failures[owned[1]] = later
                raise primary
        return inputs(*args)

    monkeypatch.setattr(provider, '_inputs', final_inputs)
    if custody_failure:
        real_custody = module.P3OutputCustody
        def track_custody(parent_fd, output_fd, *args, **kwargs):
            handle = real_custody(parent_fd, output_fd, *args, **kwargs)
            owned.extend((handle._output, handle._parent))
            # The provider closes its original parent after creating custody.
            # Failure here must not retry that FD, nor skip provider cleanup when
            # the real custody abandon also fails while closing its own output.
            nonlocal primary
            primary = OSError(errno.EIO, 'synthetic original parent close failure')
            failures[parent_fd] = primary
            failures[handle._output] = first
            failures[owned[0]] = later
            return handle
        monkeypatch.setattr(module, 'P3OutputCustody', track_custody)
    with pytest.raises(Exception) as failure:
        module.consume_prepared_p3_spawn(token)
    assert_closed_once(owned, attempted)
    chain = exception_chain(failure.value)
    assert primary in chain and first in chain
    assert later not in chain
    from services.job_worker.p3_spawn_interface import P3SpawnError
    assert isinstance(failure.value, P3SpawnError)
    assert failure.value.reason == 'P3_SPAWN_HELD'
    assert failure.value.__cause__ is first
    with pytest.raises(P3SpawnError, match='consumed'):
        module.consume_prepared_p3_spawn(token)
    assert_closed_once(owned, attempted)


def test_provider_preserves_original_cancellation_cause(synthetic_provider, monkeypatch):
    from services.job_worker import p3_spawn as module
    provider, job, _ = synthetic_provider
    token = provider.prepare(job)
    inputs = provider._inputs
    interrupt = KeyboardInterrupt('synthetic bounded cancellation')
    cause = ValueError('synthetic original cancellation cause')
    calls, owned = 0, []
    def cancel_final_inputs(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            owned.extend(sys._getframe(1).f_locals['descriptors'])
            raise interrupt from cause
        return inputs(*args)
    monkeypatch.setattr(provider, '_inputs', cancel_final_inputs)
    with pytest.raises(KeyboardInterrupt) as failure:
        module.consume_prepared_p3_spawn(token)
    assert failure.value is interrupt
    for fd in owned:
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == errno.EBADF
    assert failure.value.__cause__ is cause


@pytest.mark.parametrize('cleanup_fails', [False, True], ids=['cleanup-success', 'cleanup-failure'])
def test_provider_cancellation_preserves_cause_after_custody(
    synthetic_provider, monkeypatch, close_fault, cleanup_fails,
):
    from services.job_worker import p3_spawn as module
    owned, attempted, failures = close_fault
    provider, job, _ = synthetic_provider
    token = provider.prepare(job)
    interrupt = KeyboardInterrupt('synthetic cancellation after custody creation')
    origin = ValueError('synthetic original cancellation cause')
    first = OSError(errno.EIO, 'synthetic custody close failure')
    later = OSError(errno.EPERM, 'synthetic provider close failure')
    inputs, real_custody, close = provider._inputs, module.P3OutputCustody, os.close
    calls, handles, parent = 0, [], []

    def final_inputs(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            owned.extend(sys._getframe(1).f_locals['descriptors'])
        return inputs(*args)

    def track_custody(parent_fd, output_fd, *args, **kwargs):
        handle = real_custody(parent_fd, output_fd, *args, **kwargs)
        handles.append(handle)
        parent.append(parent_fd)
        owned.extend((handle._output, handle._parent))
        if cleanup_fails:
            failures[handle._output] = first
            failures[owned[0]] = later
        return handle

    def cancel_after_parent_close(fd):
        close(fd)
        if parent and fd == parent[0]:
            raise interrupt from origin

    monkeypatch.setattr(provider, '_inputs', final_inputs)
    monkeypatch.setattr(module, 'P3OutputCustody', track_custody)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'close', cancel_after_parent_close)
        with pytest.raises(KeyboardInterrupt) as failure:
            module.consume_prepared_p3_spawn(token)
    assert failure.value is interrupt
    assert_closed_once(owned, attempted)
    assert handles[0]._output == handles[0]._parent == -1
    assert handles[0]._retained is None
    chain = exception_chain(failure.value)
    assert origin in chain
    if cleanup_fails:
        assert failure.value.__cause__ is first
        assert later not in chain
    else:
        assert failure.value.__cause__ is origin
    with pytest.raises(module.P3SpawnError, match='consumed'):
        module.consume_prepared_p3_spawn(token)
    handles[0].abandon()
    assert_closed_once(owned, attempted)
