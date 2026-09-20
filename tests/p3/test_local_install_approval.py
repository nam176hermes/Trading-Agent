"""The installer is the writer half, so what it refuses matters more than what it installs.

Every refusal here exists because the alternative is a store left in a state no consumer
accepts. The install is one transaction over the whole admitted gate set, so a file that turns
out to be unusable after the generation has moved cannot be backed out: it has to be caught
before anything is written.
"""
import hashlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    'p3_local_install_approval', ROOT/'scripts/p3_local_install_approval.py')
assert _spec is not None and _spec.loader is not None
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)

ISSUER = 'operator-a'


def _approval(*, issuer=ISSUER, expires_in=timedelta(hours=1), issued_at=None):
    issued = issued_at or datetime.now(UTC) - timedelta(minutes=1)
    reference = 'a'*64
    value = dict(schema_version='local-qualification-approval-v1', issuer_identity=issuer,
                 receipt_ref=dict(content_sha256=reference, size_bytes=10,
                                  media_type='application/json', locator=reference+'.blob'),
                 issued_at=issued.isoformat().replace('+00:00', 'Z'),
                 expires_at=(issued + expires_in).isoformat().replace('+00:00', 'Z'),
                 revoked=False,
                 authority=dict(broker=False, live=False, network=False, production=False))
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return canonical_json_bytes(value) + b'\n'


def _directory(tmp_path, **files):
    source = tmp_path/'approvals'
    source.mkdir()
    for name, payload in files.items():
        (source/f'{name}.json').write_bytes(payload)
    return source


def test_the_admitted_gate_set_is_read_before_anything_is_installed(tmp_path):
    from packages.local_pre_p3 import LOCAL_PRE_P3_GATES
    source = _directory(tmp_path, **{gate: _approval() for gate in LOCAL_PRE_P3_GATES})
    payloads = installer._read_approvals(source, ISSUER)
    assert sorted(payloads) == sorted(LOCAL_PRE_P3_GATES)


def test_a_gate_that_is_not_a_local_pre_p3_gate_is_refused(tmp_path):
    source = _directory(tmp_path, P1_H_COMPLETE=_approval(), NOT_A_GATE=_approval())
    with pytest.raises(installer.AuthorityHeld, match='not a LOCAL pre-P3 gate'):
        installer._read_approvals(source, ISSUER)


def test_an_approval_issued_to_another_issuer_is_refused(tmp_path):
    source = _directory(tmp_path, P1_H_COMPLETE=_approval(issuer='operator-b'))
    with pytest.raises(installer.AuthorityHeld, match='belongs to issuer operator-b'):
        installer._read_approvals(source, ISSUER)


def test_an_already_expired_approval_is_never_installed(tmp_path):
    # Installing it would move the generation and leave the store holding an approval that
    # every snapshot refuses, which is strictly worse than refusing the install.
    stale = _approval(issued_at=datetime.now(UTC) - timedelta(hours=3))
    source = _directory(tmp_path, P1_H_COMPLETE=stale)
    with pytest.raises(installer.AuthorityHeld, match='expired at'):
        installer._read_approvals(source, ISSUER)


def test_a_malformed_approval_is_refused(tmp_path):
    source = _directory(tmp_path, P1_H_COMPLETE=b'{"schema_version": "wrong"}\n')
    with pytest.raises(Exception) as caught:      # noqa: PT011 -- pydantic's own error type
        installer._read_approvals(source, ISSUER)
    assert 'local-qualification-approval-v1' in str(caught.value)


def test_an_approval_whose_digest_does_not_cover_it_is_refused(tmp_path):
    tampered = json.loads(_approval())
    tampered['revoked'] = True                    # digest still covers revoked=False
    source = _directory(tmp_path, P1_H_COMPLETE=canonical_json_bytes(tampered) + b'\n')
    with pytest.raises(Exception) as caught:      # noqa: PT011
        installer._read_approvals(source, ISSUER)
    assert 'digest is invalid' in str(caught.value)


def test_an_empty_directory_is_refused(tmp_path):
    with pytest.raises(installer.AuthorityHeld, match='contains no gate approvals'):
        installer._read_approvals(_directory(tmp_path), ISSUER)


def test_a_path_that_is_not_a_directory_is_refused(tmp_path):
    target = tmp_path/'file.json'
    target.write_bytes(_approval())
    with pytest.raises(installer.AuthorityHeld, match='is not a directory of approvals'):
        installer._read_approvals(target, ISSUER)


def test_usage_is_refused_without_the_two_arguments(capsys):
    assert installer.main(['p3_local_install_approval.py']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'HELD'


def test_installation_refuses_to_run_as_anyone_but_the_store_owner(capsys, monkeypatch):
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 1000)
    assert installer.main(['p3_local_install_approval.py', ISSUER, '/nonexistent']) == 2
    assert 'store owner' in json.loads(capsys.readouterr().out)['reason']
