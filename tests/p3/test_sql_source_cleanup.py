import subprocess

import pytest

from scripts.check_p3_sql_source import _cleanup_cluster


def test_cleanup_preserves_owned_data_when_shutdown_is_uncertain(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'postmaster.pid').write_text('123')
    socket = tmp_path / 'socket'
    socket.mkdir()

    def failed_stop(_args):
        raise subprocess.TimeoutExpired('pg_ctl', 60)

    with pytest.raises(RuntimeError, match='retained'):
        _cleanup_cluster(tmp_path, data, socket, True, failed_stop)
    assert (data / 'postmaster.pid').exists()


def test_cleanup_removes_owned_root_after_verified_stop(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    pid = data / 'postmaster.pid'
    pid.write_text('123')
    socket = tmp_path / 'socket'
    socket.mkdir()
    calls = []

    def stop(args):
        calls.append(args)
        pid.unlink()

    _cleanup_cluster(tmp_path, data, socket, True, stop)
    assert calls[0][-1] == 'stop'
    assert not tmp_path.exists()


def test_cleanup_reports_stop_error_even_after_root_removed(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    socket = tmp_path / 'socket'
    socket.mkdir()

    def failed_start_stop(_args):
        raise RuntimeError('no server started')

    with pytest.raises(RuntimeError, match='no server started'):
        _cleanup_cluster(tmp_path, data, socket, True, failed_start_stop)
    assert not tmp_path.exists()


def test_cleanup_retains_root_when_postgres_descendants_survive(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from services.job_worker import p3_fixture_sql
    data = tmp_path/'data'
    data.mkdir()
    sock = tmp_path/'socket'
    sock.mkdir()
    monkeypatch.setattr(p3_fixture_sql,'_session_members_proc',lambda identity:(object(),),raising=False)
    with pytest.raises(RuntimeError,match='session.*retained'):
        _cleanup_cluster(tmp_path,data,sock,True,lambda args:None,
                         process=SimpleNamespace(send_signal=lambda _:None,wait=lambda **kwargs:0),identity=object())
    assert tmp_path.exists()
