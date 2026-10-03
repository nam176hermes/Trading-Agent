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
