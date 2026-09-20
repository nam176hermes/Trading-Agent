"""The admission transaction must linearize with revocation, not merely notice it.

Re-reading an approval and then acting is check-then-act. These tests hold the properties
that distinguish a transaction from a recheck: a revocation either precedes the commitment
and prevents it, or follows it and prevents the next admission; an approval replaced by a
different but equally valid one for the same receipt is detected; and a crash between
admission and durable publication leaves nothing consumable.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.local_authority import (
    AuthorityHeld, AuthorityStore, BUNDLE_MEMBERS, admit, consume, recover)

GATES = ('P1_H_COMPLETE', 'PRE_P3_READY')
IDENTITY = dict(issuer_kind='LOCAL', issuer_identity='operator-a',
                run_id='00000000-0000-4000-8000-000000000002', attempt=1,
                host_id='a'*64, boot_id='')
SOURCE = dict(commit_sha='1'*40, tree_sha='2'*40,
              closure_schema_version='trading-agent-source-closure-v1',
              closure_policy_sha256='3'*64, closure_sha256='4'*64)
FUTURE = '2099-01-01T00:00:00Z'
PAST = '2000-01-01T00:00:00Z'


def _approval(receipt='a'*64, revoked=False, marker='first'):
    return canonical_json_bytes(dict(schema_version='local-qualification-approval-v1',
        issuer_identity='operator-a', receipt_ref=dict(content_sha256=receipt,
            size_bytes=10, media_type='application/json', locator=receipt+'.blob'),
        issued_at='2026-09-19T00:00:00Z', expires_at=FUTURE, revoked=revoked,
        marker=marker)) + b'\n'


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path/'local-qualification'
    (root/'operator-a').mkdir(parents=True)
    value = AuthorityStore('operator-a', root=root)
    for gate in GATES:
        (value.path/f'{gate}.json').write_bytes(_approval())
    monkeypatch.setattr('packages.local_authority._boot_identity', lambda: 'b'*36)
    return value


def _identity():
    return dict(IDENTITY, boot_id='b'*36)


def _bundle():
    return {name: canonical_json_bytes(dict(member=name)) + b'\n' for name in BUNDLE_MEMBERS}


def _admit(store, **overrides):
    arguments = dict(action='ISSUE_RUNTIME_PROFILE', gate='PRE_P3_READY', gates=GATES,
        identity=_identity(), source=SOURCE, proposal_identity=_identity(),
        expires_at=FUTURE, bundle=_bundle())
    arguments.update(overrides)
    return admit(store, **arguments)


def _consume(store, **overrides):
    arguments = dict(action='ISSUE_RUNTIME_PROFILE', gate='PRE_P3_READY',
                     identity=_identity(), source=SOURCE, gates=GATES)
    arguments.update(overrides)
    return consume(store, **arguments)


def test_admission_commits_and_is_consumable(store):
    record = _admit(store)
    assert record['state'] == 'COMMITTED'
    assert record['generation'] == 1
    assert set(record['bundle']) == set(BUNDLE_MEMBERS)
    assert _consume(store)['token'] == record['token']


def test_revocation_after_commitment_prevents_the_next_admission(store):
    """The commitment stands, and the revocation stops what has not been admitted yet."""
    _admit(store)
    (store.path/'PRE_P3_READY.json').write_bytes(_approval(revoked=True))
    with pytest.raises(AuthorityHeld, match='revoked'):
        _consume(store)


def test_revocation_before_commitment_prevents_it(store):
    (store.path/'P1_H_COMPLETE.json').write_bytes(_approval(revoked=True))
    with pytest.raises(AuthorityHeld, match='revoked'):
        _admit(store)


def test_same_receipt_approval_replacement_is_detected(store):
    """A different but equally valid approval for the same receipt must not pass.

    Binding receipt digests alone would miss this: the receipt never changes.
    """
    _admit(store)
    (store.path/'PRE_P3_READY.json').write_bytes(_approval(marker='second'))
    with pytest.raises(AuthorityHeld, match='no longer matches the admitted snapshot'):
        _consume(store)


def test_aba_replacement_is_detected_by_the_generation(store):
    """Restoring the original bytes does not restore the admission."""
    original = (store.path/'PRE_P3_READY.json').read_bytes()
    _admit(store)
    (store.path/'PRE_P3_READY.json').write_bytes(_approval(marker='second'))
    _admit(store)
    (store.path/'PRE_P3_READY.json').write_bytes(original)
    with pytest.raises(AuthorityHeld):
        _consume(store)


def test_generation_rollback_is_refused(store):
    """The counter is the ordering authority, so restoring an older one fails closed."""
    _admit(store)
    _admit(store)
    # Published records are 0444, as a protected store requires, so rolling the counter
    # back is itself a privileged act. The point of the test is what happens afterwards.
    os.chmod(store.path/'generation.json', 0o600)
    (store.path/'generation.json').write_bytes(canonical_json_bytes(
        dict(schema_version='p3-local-authority-transaction-v1', generation=1,
             issuer='operator-a')) + b'\n')
    with pytest.raises(AuthorityHeld, match='generation moved'):
        _consume(store)


def test_expiry_is_checked_independently_of_token_equality(store):
    """A token that still matches byte for byte grants nothing once the deadline passes."""
    record = _admit(store)
    with pytest.raises(AuthorityHeld, match='expired'):
        _consume(store, now_ns=2**62)
    assert record['token']


def test_an_expired_deadline_is_never_admitted(store):
    with pytest.raises(AuthorityHeld, match='deadline has already passed'):
        _admit(store, expires_at=PAST)


def test_a_foreign_proposal_identity_is_refused(store):
    foreign = dict(_identity(), attempt=9)
    with pytest.raises(AuthorityHeld, match='proposal identity differs'):
        _admit(store, proposal_identity=foreign)


def test_a_consumer_for_another_action_is_refused(store):
    _admit(store)
    with pytest.raises(AuthorityHeld, match='different action'):
        _consume(store, action='ISSUE_QUALIFICATION_DECISION')


def test_staging_is_not_consumable_as_authority(store):
    """A crash between admission and publication must leave nothing a reader can use."""
    _admit(store)
    published = json.loads((store.path/'current-commit.json').read_bytes())
    os.chmod(store.path/'current-commit.json', 0o600)
    (store.path/'current-commit.json').unlink()
    state = recover(store)
    assert state['replayable'] is False
    assert state['orphaned_generations'] == [f'{published["generation"]:020d}']
    with pytest.raises(AuthorityHeld, match='no committed admission'):
        _consume(store)


def test_a_bundle_member_that_changed_after_commitment_is_refused(store):
    record = _admit(store)
    member = Path(record['bundle_path'])/'request.json'
    os.chmod(member, 0o600)
    member.write_bytes(b'{"member":"tampered"}\n')
    with pytest.raises(AuthorityHeld, match='differs from the committed manifest'):
        _consume(store)


def test_each_admission_authorises_exactly_one_action(store):
    """Two actions need two admissions; the second is not covered by the first."""
    first = _admit(store)
    second = _admit(store, action='ISSUE_QUALIFICATION_DECISION')
    assert second['generation'] == first['generation'] + 1
    assert second['token'] != first['token']
    with pytest.raises(AuthorityHeld, match='different action'):
        _consume(store, action='ISSUE_RUNTIME_PROFILE')


def test_a_writer_cannot_interleave_with_a_held_admission(store):
    """Mutual exclusion is the mechanism, so it is tested against a real second process.

    The linearization claim is only worth as much as the lock. A revocation attempted while
    an admission holds the lock must wait, which is what makes 'either before or after the
    commitment' true rather than aspirational.
    """
    import multiprocessing
    import time as clock

    started = multiprocessing.Event()
    released = multiprocessing.Event()
    observed = multiprocessing.Queue()

    def writer(path, started, released, observed):
        import fcntl as locking
        import os as system
        started.wait(5)
        opened = system.open(path, system.O_RDWR | system.O_CREAT, 0o600)
        begun = clock.monotonic()
        locking.flock(opened, locking.LOCK_EX)
        observed.put(clock.monotonic() - begun)
        locking.flock(opened, locking.LOCK_UN)
        system.close(opened)
        released.set()

    process = multiprocessing.Process(target=writer,
        args=(str(store.path/'authority.lock'), started, released, observed))
    process.start()
    try:
        with store.locked():
            started.set()
            clock.sleep(0.4)          # the writer is blocked for exactly this long
            assert not released.is_set(), 'a writer acquired the lock during an admission'
        assert released.wait(5), 'the writer never acquired the lock after release'
        waited = observed.get(timeout=5)
        assert waited >= 0.3, f'the writer did not actually block, it waited {waited:.3f}s'
    finally:
        process.join(5)
