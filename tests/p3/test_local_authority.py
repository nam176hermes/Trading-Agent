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
import time
from pathlib import Path

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.local_authority import (
    AuthorityHeld, AuthorityStore, BUNDLE_MEMBERS, _deadline_ns, admit, consume,
    consumed_generation, enrol, recover, require_no_bypassing_writer)

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
    # Enrolment is explicit. Admission refuses a store nobody placed under the protocol.
    enrol(value, GATES)
    return value


def _identity():
    return dict(IDENTITY, boot_id='b'*36)


def _bundle():
    return {name: canonical_json_bytes(dict(member=name)) + b'\n' for name in BUNDLE_MEMBERS}


def _prepare(identity=None, source=None, expires_at=FUTURE, bundle=None, hook=None):
    """Stand in for the caller's semantic validator, which `admit` runs under the lock."""
    def prepare(snapshot):
        if hook is not None:
            hook(snapshot)
        return dict(identity=_identity() if identity is None else identity,
                    source=SOURCE if source is None else source,
                    expires_at=expires_at,
                    bundle=_bundle() if bundle is None else bundle)
    return prepare


def _admit(store, **overrides):
    prepare = overrides.pop('prepare', None) or _prepare(
        **{k: overrides.pop(k) for k in ('identity', 'source', 'expires_at', 'bundle', 'hook')
           if k in overrides})
    arguments = dict(action='ISSUE_RUNTIME_PROFILE', gate='PRE_P3_READY', gates=GATES,
        proposal_identity=_identity(), proposal_source=SOURCE, prepare=prepare)
    overrides.pop('expires_at', None)
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
    store.replace_approval('PRE_P3_READY', _approval(revoked=True), GATES)
    with pytest.raises(AuthorityHeld, match='revoked'):
        _consume(store)


def test_revocation_before_commitment_prevents_it(store):
    store.replace_approval('P1_H_COMPLETE', _approval(revoked=True), GATES)
    with pytest.raises(AuthorityHeld, match='revoked'):
        _admit(store)


def test_same_receipt_approval_replacement_is_detected(store):
    """A different but equally valid approval for the same receipt must not pass.

    Binding receipt digests alone would miss this: the receipt never changes.
    """
    _admit(store)
    store.replace_approval('PRE_P3_READY', _approval(marker='second'), GATES)
    with pytest.raises(AuthorityHeld, match='generation moved|no longer matches'):
        _consume(store)


def test_aba_replacement_is_detected_by_the_generation(store):
    """Restoring the original bytes does not restore the admission."""
    original = (store.path/'PRE_P3_READY.json').read_bytes()
    _admit(store)
    store.replace_approval('PRE_P3_READY', _approval(marker='second'), GATES)
    _admit(store)
    store.replace_approval('PRE_P3_READY', original, GATES)
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
    # A rolled-back counter no longer matches the attestation published with it, which is
    # the same evidence a bypassing write leaves behind.
    with pytest.raises(AuthorityHeld, match='not current for this generation'):
        _consume(store)


def test_expiry_is_checked_independently_of_token_equality(store):
    """A token that still matches byte for byte grants nothing once the deadline passes."""
    record = _admit(store)
    with pytest.raises(AuthorityHeld, match='expired'):
        _consume(store, now_ns=2**62)
    assert record['token']


def test_an_expired_deadline_is_never_admitted(store):
    with pytest.raises(AuthorityHeld, match='deadline passed'):
        _admit(store, expires_at=PAST)


def _moving_clock(monkeypatch, readings):
    """Make the module's wall clock advance between reads.

    `admit` reads the clock more than once on purpose, so a test about elapsed time has to
    let it move. Patching the clock exercises the production path, where `now_ns` is None,
    rather than an injected constant that can never change.
    """
    stream = iter(readings)
    last = [readings[-1]]

    def clock():
        try:
            last[0] = next(stream)
        except StopIteration:
            pass
        return last[0]

    monkeypatch.setattr('packages.local_authority.time.time_ns', clock)


def test_a_proposal_that_outlives_its_deadline_is_not_committed(store, monkeypatch):
    """`prepare` is unbounded, so the clock has to be read after it, not before.

    Reading it before and checking the deadline against that reading admitted whatever
    `prepare` took: an admission with two seconds left could spend five seconds preparing and
    still be committed, with the monotonic deadline built from a later reading and the earlier
    delta, so the elapsed time was added to the validity window instead of subtracted.
    """
    start = _deadline_ns('2026-09-19T00:00:00Z')
    _moving_clock(monkeypatch, [start, start + 5_000_000_000])
    with pytest.raises(AuthorityHeld, match='deadline passed while the proposal was prepared'):
        _admit(store, expires_at='2026-09-19T00:00:02Z')
    assert store.generation() == 0, 'nothing may be committed for a proposal that expired'


def test_a_deadline_that_passes_while_the_bundle_is_staged_is_not_committed(store, monkeypatch):
    """Staging is writes and fsyncs, so it is unbounded too.

    Publishing after the deadline would record a commitment that was never valid at its own
    linearization point.
    """
    start = _deadline_ns('2026-09-19T00:00:00Z')
    _moving_clock(monkeypatch, [start, start, start + 5_000_000_000])
    with pytest.raises(AuthorityHeld, match='deadline passed while the bundle was staged'):
        _admit(store, expires_at='2026-09-19T00:00:02Z')
    assert not (store.path/'current-commit.json').exists(), 'no commit may be published'


def test_the_monotonic_deadline_does_not_absorb_the_preparation_time(store, monkeypatch):
    """The wall and monotonic readings have to be taken together, not one on each side."""
    start = _deadline_ns('2026-09-19T00:00:00Z')
    _moving_clock(monkeypatch, [start, start + 1_000_000_000])
    record = _admit(store, expires_at='2026-09-19T00:00:10Z', monotonic_ns=500)
    # Nine seconds remain when the clock is read after prepare, so the monotonic deadline is
    # that reading plus nine -- not plus the ten that was left before prepare started.
    assert record['monotonic_deadline_ns'] == 500 + 9_000_000_000


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


# ---------------------------------------------------------------------------
# A writer that skips the protocol must be detected, not tolerated
# ---------------------------------------------------------------------------

def test_a_store_never_placed_under_the_protocol_is_refused(tmp_path, monkeypatch):
    """No attestation means no protocol, and admission must not enrol one implicitly.

    The fixture store is enrolled, so this builds an unenrolled one. Admission used to
    advance the generation and attest afterwards, which stamped exactly this store valid.
    """
    from packages.local_authority import require_no_bypassing_writer
    root = tmp_path/'unenrolled'
    (root/'operator-a').mkdir(parents=True)
    bare = AuthorityStore('operator-a', root=root)
    for gate in GATES:
        (bare.path/f'{gate}.json').write_bytes(_approval())
    monkeypatch.setattr('packages.local_authority._boot_identity', lambda: 'b'*36)
    with pytest.raises(AuthorityHeld, match='not under the authority protocol'):
        require_no_bypassing_writer(bare, GATES)
    with pytest.raises(AuthorityHeld, match='not under the authority protocol'):
        _admit(bare)
    assert not (bare.path/'generation.json').exists(), 'a refused admission advanced the store'


def test_a_write_that_skipped_the_lock_is_detected(store):
    """The signature of a bypass is bytes moving while the generation stands still.

    The helpers that install approvals are root processes outside this repository, so they
    cannot be forced to take the lock by changing consumers. They can be caught.
    """
    from packages.local_authority import require_no_bypassing_writer
    _admit(store)
    require_no_bypassing_writer(store, GATES)
    os.chmod(store.path/'PRE_P3_READY.json', 0o600)
    (store.path/'PRE_P3_READY.json').write_bytes(_approval(marker='installed-out-of-band'))
    with pytest.raises(AuthorityHeld, match='changed outside the authority protocol'):
        require_no_bypassing_writer(store, GATES)


def test_a_stale_attestation_is_refused(store):
    """An attestation from an earlier generation cannot vouch for the current store."""
    from packages.local_authority import ATTESTATION_NAME, require_no_bypassing_writer
    _admit(store)
    stale = json.loads((store.path/ATTESTATION_NAME).read_bytes())
    stale['generation'] -= 1
    os.chmod(store.path/ATTESTATION_NAME, 0o600)
    (store.path/ATTESTATION_NAME).write_bytes(canonical_json_bytes(stale) + b'\n')
    with pytest.raises(AuthorityHeld, match='not current for this generation'):
        require_no_bypassing_writer(store, GATES)


def test_consuming_refuses_a_store_that_left_the_protocol(store):
    """The bypass check guards admission itself, not only a separate helper."""
    _admit(store)
    assert _consume(store)['state'] == 'COMMITTED'
    os.chmod(store.path/'P1_H_COMPLETE.json', 0o600)
    (store.path/'P1_H_COMPLETE.json').write_bytes(_approval(marker='out-of-band'))
    with pytest.raises(AuthorityHeld, match='outside the authority protocol'):
        _consume(store)


# ---------------------------------------------------------------------------
# The semantic decision has to happen inside the transaction, not before it
# ---------------------------------------------------------------------------

def test_an_approval_replaced_during_validation_is_refused(store):
    """Validating before the lock admits one generation's prerequisites with another's bytes.

    The validator now runs inside the critical section, and what it observed is compared
    with what is committed. A write that lands in between is the exact case the earlier
    ordering could not see: the semantic check had already passed on bytes that were gone by
    the time the commit recorded them as admitted.
    """
    def replace(_snapshot):
        target = store.path/'PRE_P3_READY.json'
        os.chmod(target, 0o600)
        target.write_bytes(_approval(marker='replaced-mid-validation'))
        os.chmod(target, 0o444)

    with pytest.raises(AuthorityHeld, match='changed between validation and commitment'):
        _admit(store, hook=replace)


def test_an_identity_the_prerequisites_do_not_carry_is_refused(store):
    """The proposal is checked against the validator's identity, not against itself.

    Passing the request's own identity as both sides compared a value with itself and bound
    nothing. Here the validator returns the identity the receipts carry and the request asks
    for a different one.
    """
    with pytest.raises(AuthorityHeld, match='proposal identity differs'):
        _admit(store, identity=dict(_identity(), run_id='00000000-0000-4000-8000-00000000dead'))


def test_a_source_the_prerequisites_do_not_carry_is_refused(store):
    with pytest.raises(AuthorityHeld, match='proposal source differs'):
        _admit(store, source=dict(SOURCE, commit_sha='9'*40))


def test_an_expired_approval_is_never_snapshotted(store):
    """Validity belongs to the approval. Bounding the admission more tightly is not a cure."""
    store.replace_approval('P1_H_COMPLETE', canonical_json_bytes(dict(
        schema_version='local-qualification-approval-v1', issuer_identity='operator-a',
        receipt_ref=dict(content_sha256='a'*64, size_bytes=10,
                         media_type='application/json', locator='a'*64+'.blob'),
        issued_at='2026-09-19T00:00:00Z', expires_at=PAST, revoked=False,
        marker='first')) + b'\n', GATES)
    with pytest.raises(AuthorityHeld, match='approval has expired'):
        _admit(store)


# ---------------------------------------------------------------------------
# One admission, one action
# ---------------------------------------------------------------------------

def test_an_admission_is_consumable_exactly_once(store):
    """Without a durable record of the spend, every re-read returns the same admission."""
    record = _admit(store)
    assert consumed_generation(store) == 0
    assert _consume(store)['token'] == record['token']
    assert consumed_generation(store) == record['generation']
    with pytest.raises(AuthorityHeld, match='already been consumed'):
        _consume(store)


def test_the_admitted_effect_runs_inside_the_consumption(store):
    """The effect is applied under the same lock that spends the admission."""
    seen = []
    _admit(store)
    _consume(store, apply=lambda record, bundle: seen.append((record['generation'], bundle)))
    assert len(seen) == 1
    generation, bundle = seen[0]
    assert (bundle/'descriptor.json').is_file()
    assert consumed_generation(store) == generation


def test_a_failed_effect_still_spends_the_admission(store):
    """Recording the spend first is what makes the effect at-most-once.

    A crash between the spend and the effect leaves nothing published and needs a fresh
    admission, which fails closed. Applying first and recording afterwards would leave the
    same effect repeatable.
    """
    def failing(record, bundle):
        raise RuntimeError('the effect failed after the admission was spent')

    record = _admit(store)
    with pytest.raises(RuntimeError):
        _consume(store, apply=failing)
    assert consumed_generation(store) == record['generation']
    with pytest.raises(AuthorityHeld, match='already been consumed'):
        _consume(store)


def test_recover_reports_a_spent_commit_as_not_consumable(store):
    _admit(store)
    assert recover(store)['published_commit_consumable'] is True
    _consume(store)
    state = recover(store)
    assert state['published_commit_consumable'] is False
    assert state['consumed_generation'] == state['published_generation']
    assert state['replayable'] is False


# ---------------------------------------------------------------------------
# A settable clock is not a deadline
# ---------------------------------------------------------------------------

def test_a_wall_clock_rollback_does_not_extend_the_monotonic_deadline(store):
    """The wall clock can be set backwards; the monotonic deadline cannot be.

    The boot identity checked above is what makes the monotonic reading comparable at all --
    a monotonic value from another boot would mean nothing.
    """
    now = _deadline_ns('2026-09-19T00:00:00Z')
    record = _admit(store, expires_at='2026-09-19T00:00:02Z', now_ns=now)
    rolled_back = now - 3_600_000_000_000
    assert _deadline_ns(record['expires_at']) > rolled_back, 'the wall check alone would pass'
    with pytest.raises(AuthorityHeld, match='monotonic deadline'):
        _consume(store, now_ns=rolled_back,
                 monotonic_ns=record['monotonic_deadline_ns'] + 1)


def test_the_monotonic_deadline_tracks_the_admitted_window(store):
    now = time.time_ns()
    record = _admit(store, expires_at='2099-01-01T00:00:00Z', now_ns=now)
    remaining = _deadline_ns('2099-01-01T00:00:00Z') - now
    assert record['monotonic_deadline_ns'] - time.monotonic_ns() <= remaining


# -- the writer half -------------------------------------------------------------------
#
# B1: approvals were installed by a bare rename outside this lock. `replace_approvals` is the
# writer that participates, and these hold the properties that make it usable as one -- above
# all that it can place a store under the protocol at all, which is what a writer restricted
# to one gate at a time could not do.


def _fresh(tmp_path):
    root = tmp_path/'local-qualification'
    (root/'operator-a').mkdir(parents=True)
    return AuthorityStore('operator-a', root=root)


def test_a_partial_install_into_a_fresh_store_is_refused_without_mutating(tmp_path):
    value = _fresh(tmp_path)
    with pytest.raises(AuthorityHeld, match='install the admitted gate set'):
        value.replace_approval('PRE_P3_READY', _approval(), GATES)
    # The refusal has to come before the generation moves. A writer that advanced and then
    # failed would leave an approval installed with no attestation covering it, which no
    # consumer accepts and `recover` does not repair.
    assert value.generation() == 0
    assert not (value.path/'PRE_P3_READY.json').exists()
    assert not (value.path/'store-attestation.json').exists()


def test_the_admitted_gate_set_installs_in_one_transaction(tmp_path):
    value = _fresh(tmp_path)
    generation = value.replace_approvals({gate: _approval() for gate in GATES}, GATES)
    assert generation == 1
    require_no_bypassing_writer(value, GATES)


def test_installing_several_approvals_moves_the_generation_once(tmp_path):
    value = _fresh(tmp_path)
    value.replace_approvals({gate: _approval() for gate in GATES}, GATES)
    assert value.generation() == 1
    value.replace_approvals({gate: _approval(marker='second') for gate in GATES}, GATES)
    # One transaction is one linearization point, whatever it touched. Counting per file
    # would let a consumer that read between two files of the same write see a generation
    # that no coherent store ever had.
    assert value.generation() == 2


def test_one_gate_still_replaces_once_the_store_is_under_the_protocol(store):
    before = store.generation()
    store.replace_approval('PRE_P3_READY', _approval(marker='second'), GATES)
    assert store.generation() == before + 1
    require_no_bypassing_writer(store, GATES)


def test_a_gate_outside_the_admitted_set_is_never_written(tmp_path):
    value = _fresh(tmp_path)
    with pytest.raises(AuthorityHeld, match='not in the admitted gate set'):
        value.replace_approvals(dict({g: _approval() for g in GATES}, OTHER=_approval()), GATES)
    assert not (value.path/'OTHER.json').exists()
    assert value.generation() == 0


def test_an_empty_writer_transaction_is_refused(tmp_path):
    value = _fresh(tmp_path)
    with pytest.raises(AuthorityHeld, match='at least one approval'):
        value.replace_approvals({}, GATES)
    assert value.generation() == 0
