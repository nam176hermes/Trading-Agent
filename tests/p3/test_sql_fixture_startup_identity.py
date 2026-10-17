"""Fence the ready server identity without binding transient startup argv."""
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.job_worker import p3_fixture_sql as fixture
from services.job_worker.recovery import ProcessIdentity


class FenceReached(Exception):
    pass


@pytest.fixture
def private_root():
    with tempfile.TemporaryDirectory(prefix="p3-startup-", dir="/tmp") as directory:
        yield Path(directory) / "sql"


def startup(monkeypatch, private_root, fault=None):
    state = {"ready": False, "inspections": 0, "ready_inspections": 0}
    events = []
    process = SimpleNamespace(pid=71001, poll=lambda: 1 if fault == "dead" else None)
    initial = ProcessIdentity(71001, 71001, 42, hashlib.sha256(b"").hexdigest())
    stable = ProcessIdentity(71001, 71001, 42, "b" * 64)

    class Inspector:
        def inspect(self, pid):
            assert pid == process.pid
            state["inspections"] += 1
            if not state["ready"]:
                return initial if state["inspections"] == 1 else stable
            state["ready_inspections"] += 1
            if fault == "absent":
                return None
            if fault == "wrong_group":
                return ProcessIdentity(71001, 71002, 42, "b" * 64)
            if fault == "reused_start":
                return ProcessIdentity(71001, 71001, 43, "b" * 64)
            if fault == "changed_command" and state["ready_inspections"] > 1:
                return ProcessIdentity(71001, 71001, 42, "c" * 64)
            return stable

    def run(argv, **kwargs):
        name = fixture.Path(argv[0]).name
        assert name in {"initdb", "pg_isready"}, "SQL must follow the fence"
        events.append(name)
        if name == "pg_isready":
            state["ready"] = True
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    def open_pidfd(pid, flags):
        assert pid == process.pid and flags == 0
        events.append("pidfd_open")
        return os.open(os.devnull, os.O_RDONLY)

    def signal_pidfd(fd, number, info, flags):
        assert number == 0 and info is None and flags == 0
        events.append("pidfd_fence")

    def heartbeat(identity):
        assert state["ready"] and identity == stable
        events.append("bound")
        raise FenceReached()

    def cleanup(root, data, sock, started, run, **kwargs):
        assert started and kwargs["process"] is process
        events.append("cleanup")
        shutil.rmtree(root)

    monkeypatch.setattr(fixture, "ProcProcessInspector", Inspector)
    monkeypatch.setattr(fixture.subprocess, "run", run)
    monkeypatch.setattr(fixture.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(fixture, "_default_pidfd_api", lambda: (open_pidfd, signal_pidfd))
    monkeypatch.setattr(fixture, "_cleanup_cluster", cleanup)
    root = private_root
    return root, heartbeat, state, events


def test_sql_binds_ready_identity_after_startup_command_transition(monkeypatch, private_root):
    root, heartbeat, state, events = startup(monkeypatch, private_root)
    with pytest.raises(FenceReached):
        fixture.run_sql_fixture(None, owned_root=root, heartbeat=heartbeat)
    assert events == ["initdb", "pidfd_open", "pg_isready", "pidfd_fence", "bound", "cleanup"]
    assert state["ready_inspections"] >= 2
    assert not root.exists()


@pytest.mark.parametrize("fault", ["absent", "wrong_group", "reused_start", "changed_command", "dead"])
def test_sql_startup_still_rejects_identity_faults_before_binding(monkeypatch, private_root, fault):
    root, heartbeat, _, events = startup(monkeypatch, private_root, fault)
    with pytest.raises(RuntimeError, match="process identity changed before fencing"):
        fixture.run_sql_fixture(None, owned_root=root, heartbeat=heartbeat)
    assert "bound" not in events and "pidfd_fence" not in events
    assert events[-1] == "cleanup" and not root.exists()


@pytest.mark.parametrize('ambient', [False, True])
@pytest.mark.parametrize('cleanup_fails', [False, True])
@pytest.mark.parametrize('outcome', ['success', 'error', 'cancel'])
def test_sql_cleanup_uses_only_own_primary(monkeypatch, outcome, cleanup_fails, ambient):
    """Exact source cleanup seam only; SQL body replaced, no SQL/process/socket IO."""
    import ast
    import copy
    import inspect

    original = ast.parse(inspect.getsource(fixture.run_sql_fixture)).body[0]
    owning_try, = [node for node in original.body if isinstance(node, ast.Try) and node.finalbody]
    seam = ast.parse('''
def cleanup_seam(body):
    root = Path('/synthetic-owned-sql-fixture')
    data, sock = root / 'data', root / 'socket'
    started = False
    process = identity = None
    run = None
    checks = ['SYNTHETIC_CHECK_PASS']
''').body[0]
    # Preserve the production initializer, handlers and finally verbatim; replace only SQL body.
    seam.body.extend(copy.deepcopy(node) for node in original.body if
        isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id == 'primary_error')
    extracted = copy.deepcopy(owning_try)
    extracted.body = ast.parse('body()').body
    seam.body.append(extracted)
    calls = []
    cleanup = RuntimeError('synthetic cleanup failure')
    primary = (KeyboardInterrupt('synthetic cancellation') if outcome == 'cancel'
               else ValueError('synthetic local failure'))
    origin = RuntimeError('synthetic original cause')
    primary.__cause__ = origin
    unrelated = KeyError('synthetic unrelated caller exception')

    def clean(*args, **kwargs):
        calls.append((args, kwargs))
        if cleanup_fails: raise cleanup

    def body():
        if outcome != 'success': raise primary

    monkeypatch.setattr(fixture, '_cleanup_cluster', clean)
    namespace = dict(vars(fixture))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[seam], type_ignores=[])),
                 inspect.getsourcefile(fixture.run_sql_fixture), 'exec'), namespace)
    raised = None
    try:
        if ambient: raise unrelated
        namespace['cleanup_seam'](body)
    except KeyError:
        try: namespace['cleanup_seam'](body)
        except BaseException as error: raised = error
    except BaseException as error:
        raised = error
    assert len(calls) == 1
    assert calls[0][0][:4] == (Path('/synthetic-owned-sql-fixture'),
        Path('/synthetic-owned-sql-fixture/data'), Path('/synthetic-owned-sql-fixture/socket'), False)
    assert calls[0][1] == dict(process=None, identity=None)
    if cleanup_fails:
        assert isinstance(raised, fixture.SQLFixtureCleanupError)
        if outcome == 'success':
            assert raised.__cause__ is cleanup
        else:
            assert isinstance(raised.__cause__, BaseExceptionGroup)
            assert raised.__cause__.exceptions == (primary, cleanup)
    else:
        assert raised is (None if outcome == 'success' else primary)
    assert not hasattr(unrelated, 'last_check'), 'caller exception must not acquire fixture metadata'
    if outcome != 'success':
        assert primary.last_check == 'SYNTHETIC_CHECK_PASS'
        assert primary.__cause__ is origin
