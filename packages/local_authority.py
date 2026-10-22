"""One serialized authority transaction for the LOCAL protected issuer store.

Re-reading an approval and then acting on it is check-then-act: a revocation that lands in
between is reported after the fact rather than prevented. Adding more re-reads, a digest
token or an atomic rename moves that window without closing it. What closes it is a single
owner protocol in which every writer and every admission passes through the same lock, and
the commit under that lock is the linearization point.

Scope. This module owns admission only. It issues no approval, grants no authority of its
own, and starts no service. An admission authorises exactly one bounded action; a later
action obtains its own. Already committed actions are never retroactively undone, so
long-running execution keeps its existing expiry and revocation fences.

Trust boundary. The store owner is root and is trusted. Root-owned 0400 is a permission
boundary, not immutable bytes. Nothing here defends against a malicious owner; it prevents
a cooperating writer and a consumer from interleaving.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from packages.engine_contracts.serialization import canonical_json_bytes

PROTOCOL_VERSION = 'p3-local-authority-transaction-v1'
TOKEN_DOMAIN = b'trading-agent/local-authority-admission/v1\0'
STORE_ROOT = Path('/etc/trading-agent/local-qualification')
MAX_GENERATION = 2**63 - 1
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
BUNDLE_MEMBERS = ('request.json', 'prerequisites.json', 'descriptor.json')
ATTESTATION_NAME = 'store-attestation.json'
CONSUMPTION_NAME = 'consumed.json'
ADMITTED_ACTIONS = ('ISSUE_RUNTIME_PROFILE', 'ISSUE_QUALIFICATION_DECISION')


class AuthorityHeld(ValueError):
    """Fail closed. There is no unlocked revalidation path to fall back to.

    A ValueError deliberately: every existing consumer of the protected store already treats
    ValueError as a safety block, so an admission refusal reaches those handlers instead of
    escaping them as an unhandled exception.
    """


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One immutable read of the whole issuer store, taken under the lock."""
    generation: int
    issuer: str
    approvals: tuple[tuple[str, str], ...]        # (gate, sha256) ordered by gate
    receipts: tuple[tuple[str, str], ...]         # (gate, sha256) ordered by gate
    prerequisite_digest: str

    def digest(self) -> str:
        return hashlib.sha256(canonical_json_bytes(dict(
            generation=self.generation, issuer=self.issuer,
            approvals=[list(pair) for pair in self.approvals],
            receipts=[list(pair) for pair in self.receipts],
            prerequisite_digest=self.prerequisite_digest))).hexdigest()


def _protected_bytes(path: Path, *, max_bytes: int = MAX_BUNDLE_BYTES) -> bytes:
    """Read one protected regular file, authenticating identity around the read.

    Metadata is provenance, so the bytes are what the caller binds to. The fstat before and
    after catches a replacement that happens while the file is being read.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise AuthorityHeld(f'{path} is not a regular file')
        if before.st_uid not in {0, os.geteuid()} or stat.S_IMODE(before.st_mode) & 0o022:
            raise AuthorityHeld(f'{path} is not owner-restricted')
        if before.st_size > max_bytes:
            raise AuthorityHeld(f'{path} exceeds the accepted size')
        raw = os.read(fd, before.st_size + 1)
        after = os.fstat(fd)
        if len(raw) != before.st_size or (before.st_dev, before.st_ino, before.st_size,
                                          before.st_mtime_ns, before.st_ctime_ns) != \
           (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise AuthorityHeld(f'{path} changed while it was being read')
        return raw
    finally:
        os.close(fd)


def _deadline_ns(expires_at: str) -> int:
    """Canonical UTC only. A local-time or offset-bearing string is refused rather than
    normalised, because two readers disagreeing on a deadline is the failure this prevents.
    """
    from datetime import datetime
    if not isinstance(expires_at, str) or not expires_at.endswith('Z'):
        raise AuthorityHeld('admission expiry must be canonical UTC')
    try:
        moment = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
    except ValueError as error:
        raise AuthorityHeld('admission expiry is not a timestamp') from error
    return int(moment.timestamp() * 1_000_000_000)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(path: Path, payload: bytes, mode: int = 0o444) -> None:
    """Write, fsync, rename, fsync the parent. One rename establishes activation."""
    staging = path.with_name(path.name + '.staging')
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as handle:
            # Explicit, because the creation mode is masked by the caller's umask and the
            # protected readers check for an exact mode rather than a permissive one.
            os.fchmod(handle.fileno(), mode)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    os.replace(staging, path)
    _fsync_directory(path.parent)


def _publish_once(path: Path, payload: bytes, mode: int = 0o444) -> None:
    """Create-only publication: a unique staging file, then one atomic hard link.

    `os.link` fails if `path` exists, so two concurrent writers cannot both succeed and no
    existing record is ever replaced; the stat-then-rename of `_publish` cannot promise that.
    """
    staging = path.with_name(f'.{path.name}.{uuid.uuid4().hex}')
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(staging, path, follow_symlinks=False)
    finally:
        staging.unlink(missing_ok=True)
    _fsync_directory(path.parent)


class AuthorityStore:
    """The issuer's protected store, and the only place admission is decided.

    The lock is one stable directory entry. Its identity is rechecked after acquisition,
    because a lock whose path can be replaced underneath the holder locks nothing.
    """

    def __init__(self, issuer: str, root: Path = STORE_ROOT):
        if not issuer or '/' in issuer or issuer in ('.', '..'):
            raise AuthorityHeld('issuer identity is not a single protected store name')
        self.issuer = issuer
        self.path = root / issuer
        self._lock_path = self.path / 'authority.lock'
        self._generation_path = self.path / 'generation.json'
        self._commit_path = self.path / 'current-commit.json'

    # -- generation ------------------------------------------------------

    def generation(self) -> int:
        """Monotonic and persisted, covering the whole store.

        Scoped to the store rather than to one gate: a per-gate counter would let two gates
        be read from different moments and produce an incoherent snapshot.
        """
        try:
            value = json.loads(_protected_bytes(self._generation_path))
        except FileNotFoundError:
            return 0
        generation = value.get('generation')
        if type(generation) is not int or not 0 <= generation <= MAX_GENERATION:
            raise AuthorityHeld('authority generation is not a bounded integer')
        return generation

    def _advance(self, current: int) -> int:
        if current >= MAX_GENERATION:
            raise AuthorityHeld('authority generation would overflow')
        following = current + 1
        _publish(self._generation_path, canonical_json_bytes(
            dict(schema_version=PROTOCOL_VERSION, generation=following,
                 issuer=self.issuer)) + b'\n')
        return following

    # -- lock ------------------------------------------------------------

    @contextmanager
    def locked(self) -> Iterator[int]:
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as error:
                raise AuthorityHeld('authority lock could not be acquired') from error
            held = os.fstat(fd)
            named = os.stat(self._lock_path, follow_symlinks=False)
            if (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
                raise AuthorityHeld('authority lock entry was replaced while held')
            if held.st_uid not in {0, os.geteuid()}:
                raise AuthorityHeld('authority lock is not owned by the authority owner')
            yield fd
        finally:
            os.close(fd)

    # -- writer path -----------------------------------------------------

    def replace_approvals(self, payloads: Mapping[str, bytes],
                          gates: tuple[str, ...]) -> int:
        """Install, replace or revoke approvals through the protocol, in one critical section.

        This is the writer half of the contract. A writer that takes this path advances the
        generation and refreshes the attestation without releasing the lock, so consumers see
        a coherent store. A writer that does not take it is caught by that attestation.

        It takes a set rather than a single gate because `store_digest` is defined over the
        whole admitted gate set, so installing a store one gate at a time is not expressible:
        the first call would advance the generation, write its approval, and then fail
        computing a digest over gates that do not exist yet -- leaving an approval installed,
        the generation moved, and no attestation, which no consumer accepts and `recover` does
        not repair. Accepting the set the digest is defined over removes that state rather
        than documenting it, and it is what lets a store be placed under the protocol at all
        without a bare write that skips it.
        """
        if not payloads:
            raise AuthorityHeld('a writer transaction must install at least one approval')
        unknown = sorted(set(payloads) - set(gates))
        if unknown:
            raise AuthorityHeld(f'{", ".join(unknown)} is not in the admitted gate set')
        with self.locked():
            # Everything the post-state digest will read has to be readable before anything
            # is written, through the same reader that digest uses: the generation advance is
            # not reversible, so a failure discovered afterwards cannot be backed out.
            for gate in sorted(set(gates) - set(payloads)):
                try:
                    _protected_bytes(self.path / f'{gate}.json')
                except FileNotFoundError as error:
                    raise AuthorityHeld(
                        f'{gate} has no approval in this store; install the admitted gate set '
                        'in one transaction') from error
            generation = self._advance(self.generation())
            for gate in sorted(payloads):
                _publish(self.path / f'{gate}.json', payloads[gate])
            record = dict(schema_version=PROTOCOL_VERSION, issuer=self.issuer,
                          generation=generation, store_digest=store_digest(self, gates))
            _publish(self.path / ATTESTATION_NAME, canonical_json_bytes(record) + b'\n')
            return generation

    def replace_approval(self, gate: str, payload: bytes, gates: tuple[str, ...]) -> int:
        """The one-gate case of `replace_approvals`, for a store already under the protocol."""
        return self.replace_approvals({gate: payload}, gates)

    # -- snapshot --------------------------------------------------------

    def snapshot(self, gates: tuple[str, ...], *, now_ns: int | None = None) -> Snapshot:
        """One immutable read of every gate, taken while the lock is held.

        The approval bytes are digested, not their metadata, so an approval replaced by a
        different but equally valid one for the same receipt changes the snapshot.

        An approval that carries its own expiry is checked here, at the moment the snapshot
        is taken. Validity is a property of the approval, not of the admission built on top
        of it: reading an expired approval and then bounding the admission by a shorter
        deadline would still be admitting authority that its issuer had already withdrawn.
        """
        now = time.time_ns() if now_ns is None else now_ns
        approvals, receipts = [], []
        for gate in sorted(gates):
            approval_raw = _protected_bytes(self.path / f'{gate}.json')
            approval = json.loads(approval_raw)
            if approval.get('revoked') is not False:
                raise AuthorityHeld(f'{gate} approval is revoked')
            expires_at = approval.get('expires_at')
            if expires_at is not None and _deadline_ns(str(expires_at)) <= now:
                raise AuthorityHeld(f'{gate} approval has expired')
            approvals.append((gate, hashlib.sha256(approval_raw).hexdigest()))
            reference = approval.get('receipt_ref', {})
            receipts.append((gate, str(reference.get('content_sha256', ''))))
        prerequisite = hashlib.sha256(canonical_json_bytes(dict(
            approvals=[list(p) for p in approvals],
            receipts=[list(p) for p in receipts]))).hexdigest()
        return Snapshot(self.generation(), self.issuer, tuple(approvals), tuple(receipts),
                        prerequisite)


def store_digest(store: 'AuthorityStore', gates: tuple[str, ...], read=None) -> str:
    """Digest of every gate approval's exact bytes, in gate order.

    `read` lets a caller supply the protected reader its own layer already authenticates
    through, so the store is not read twice by two different mechanisms.
    """
    read = read or _protected_bytes
    members = []
    for gate in sorted(gates):
        members.append([gate, hashlib.sha256(
            read(store.path / f'{gate}.json')).hexdigest()])
    return hashlib.sha256(canonical_json_bytes(
        dict(issuer=store.issuer, approvals=members))).hexdigest()


def enrol(store: 'AuthorityStore', gates: tuple[str, ...]) -> dict[str, Any]:
    """Place a store under the protocol for the first time. A deliberate act, not a default.

    Admission never does this implicitly: a store nobody enrolled is indistinguishable from
    one a bypassing writer left, and both are refused.
    """
    with store.locked():
        return attest(store, gates)


def attest(store: 'AuthorityStore', gates: tuple[str, ...]) -> dict[str, Any]:
    """Publish what the store contains at the current generation.

    This is what makes a bypassing writer detectable: a consumer refuses when the bytes moved
    without the generation moving with them, which is the observable signature of a write that
    skipped the protocol. Detection is the backstop, not the design. `replace_approvals` is the
    writer every installer is expected to take, and `scripts/p3_local_install_approval.py` is
    the one that does; an approval installed by a bare rename is a defect this catches after
    the fact, not a supported second path.
    """
    record = dict(schema_version=PROTOCOL_VERSION, issuer=store.issuer,
                  generation=store.generation(), store_digest=store_digest(store, gates))
    _publish(store.path / ATTESTATION_NAME, canonical_json_bytes(record) + b'\n')
    return record


def require_no_bypassing_writer(store: 'AuthorityStore', gates: tuple[str, ...],
                                read: Callable[[Path], bytes] | None = None) -> dict[str, Any]:
    """Hold when the store changed outside the protocol. There is no unlocked fallback.

    An absent attestation means the store has never been placed under the protocol, which is
    indistinguishable from one that left it. Both are refused rather than admitted on trust.
    """
    read = read or _protected_bytes
    try:
        raw = read(store.path / ATTESTATION_NAME)
    except (FileNotFoundError, OSError) as error:
        raise AuthorityHeld(
            'issuer store is not under the authority protocol; no unlocked revalidation') from error
    record = json.loads(raw)
    if record.get('schema_version') != PROTOCOL_VERSION or record.get('issuer') != store.issuer:
        raise AuthorityHeld('store attestation does not belong to this issuer protocol')
    generation = json.loads(read(store._generation_path)).get('generation', 0) \
        if read is not _protected_bytes else store.generation()
    if record.get('generation') != generation:
        raise AuthorityHeld('store attestation is not current for this generation')
    if record.get('store_digest') != store_digest(store, gates, read):
        raise AuthorityHeld('issuer store changed outside the authority protocol')
    return record


def admission_token(*, action: str, gate: str, issuer: str, identity: dict[str, Any],
                    source: dict[str, Any],
                    snapshot: Snapshot, expires_at: str, monotonic_deadline_ns: int,
                    boot_id: str) -> str:
    """Canonical, domain-separated envelope binding everything the admission depends on.

    Domain separation keeps this digest from ever colliding with another envelope in the
    system. Time is bound here for the record, but equality of this token is never treated
    as evidence that the deadline has not passed: expiry is checked independently.
    """
    return hashlib.sha256(TOKEN_DOMAIN + canonical_json_bytes(dict(
        protocol_version=PROTOCOL_VERSION, action=action, gate=gate, issuer=issuer,
        identity=identity, source=source, generation=snapshot.generation,
        approvals=[list(pair) for pair in snapshot.approvals],
        receipts=[list(pair) for pair in snapshot.receipts],
        prerequisite_digest=snapshot.prerequisite_digest, expires_at=expires_at,
        monotonic_deadline_ns=monotonic_deadline_ns, boot_id=boot_id))).hexdigest()


def _boot_identity() -> str:
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def _require_admitted_action(action: str) -> None:
    if action not in ADMITTED_ACTIONS:
        raise AuthorityHeld(f'action {action} is not an admitted bounded action')


def admit(store: AuthorityStore, *, action: str, gate: str, gates: tuple[str, ...],
          proposal_identity: dict[str, Any], proposal_source: dict[str, Any],
          prepare: Callable[[Snapshot], dict[str, Any]], now_ns: int | None = None,
          monotonic_ns: int | None = None) -> dict[str, Any]:
    """Admit exactly one bounded action, and publish it durably before releasing the lock.

    The whole decision happens inside the lock, `prepare` included. `prepare` is the caller's
    semantic validator: it re-derives the prerequisites, the identity, the source and the
    bundle from the protected store as it stands at this instant, and everything committed
    here is what it returned. Running that validation before the lock, as the earlier version
    did, admitted the prerequisites of one generation alongside the snapshot of another --
    the approvals could be replaced in between, and the commit would record them as admitted
    anyway.

    The identity is taken from `prepare`, never from the caller's request. Comparing a
    request field with itself, which is what passing the same value as both the identity and
    the proposal amounted to, binds nothing; the proposal is accepted only when it equals the
    identity the prerequisite receipts actually carry.

    A concurrent revocation must take the same lock, so it either precedes this point and
    prevents the commitment, or follows it and prevents the next admission.
    """
    _require_admitted_action(action)
    if not callable(prepare):
        raise AuthorityHeld('admission requires a semantic validator to run under the lock')
    with store.locked():
        # Refuse a store that is not under the protocol before touching the generation. The
        # earlier order advanced first and re-attested after publishing, so a store that had
        # never been enrolled, or that a writer had changed out of band, was stamped valid by
        # the very call that should have held. Enrolment is a separate deliberate act.
        require_no_bypassing_writer(store, gates)
        boot_id = _boot_identity()
        started = time.time_ns() if now_ns is None else now_ns
        # The snapshot the validator is handed is the store as it is before this admission
        # advances it, so the validator and the commitment observe the same approval bytes.
        validated = store.snapshot(gates, now_ns=started)
        proposal = prepare(validated)
        identity, source, expires_at, bundle = (proposal['identity'], proposal['source'],
                                                proposal['expires_at'], proposal['bundle'])
        if set(bundle) != set(BUNDLE_MEMBERS):
            raise AuthorityHeld('admission bundle must contain exactly the accepted members')
        if proposal_identity != identity:
            raise AuthorityHeld('proposal identity differs from the admitted prerequisite identity')
        if proposal_source != source:
            raise AuthorityHeld('proposal source differs from the admitted prerequisite source')
        if identity.get('boot_id') != boot_id:
            raise AuthorityHeld('admission is for a different boot')
        # Time is read again here, and the wall and monotonic readings are taken together.
        # `prepare` is the caller's semantic validation and is unbounded: the earlier version
        # checked the deadline against a reading taken before it, then built the monotonic
        # deadline from a reading taken after it, so however long `prepare` ran was added to
        # the validity window and an admission whose deadline passed during it could still be
        # committed.
        now = time.time_ns() if now_ns is None else now_ns
        elapsed = time.monotonic_ns() if monotonic_ns is None else monotonic_ns
        deadline_ns = _deadline_ns(expires_at)
        if deadline_ns <= now:
            raise AuthorityHeld('admission deadline passed while the proposal was prepared')
        # Advance, then re-read: the admission is recorded at a generation, and the snapshot
        # must be of the store as it stands at that same generation. Snapshotting only before
        # the advance embeds the previous generation and no consumer could ever match it.
        generation = store._advance(store.generation())
        # Re-snapshotted at the current time, so an approval that expired during `prepare` is
        # refused here rather than carried through on the reading taken before it.
        snapshot = store.snapshot(gates, now_ns=now)
        if (snapshot.approvals, snapshot.receipts) != (validated.approvals, validated.receipts):
            raise AuthorityHeld('issuer store changed between validation and commitment')
        monotonic_deadline_ns = elapsed + (deadline_ns - now)
        token = admission_token(action=action, gate=gate, issuer=store.issuer,
            identity=identity, source=source, snapshot=snapshot, expires_at=expires_at,
            monotonic_deadline_ns=monotonic_deadline_ns, boot_id=boot_id)
        staged = store.path / 'staging' / f'{generation:020d}'
        staged.mkdir(mode=0o700, parents=True, exist_ok=False)
        manifest = {}
        for name in BUNDLE_MEMBERS:
            payload = bundle[name]
            if len(payload) > MAX_BUNDLE_BYTES:
                raise AuthorityHeld(f'{name} exceeds the accepted bundle size')
            _publish(staged / name, payload)
            manifest[name] = hashlib.sha256(payload).hexdigest()
        _fsync_directory(staged)
        # Staging the bundle is unbounded too: these are writes and fsyncs. Publishing a
        # commitment whose deadline has already passed would record an admission that was
        # never valid at its own linearization point.
        if deadline_ns <= (time.time_ns() if now_ns is None else now_ns):
            raise AuthorityHeld('admission deadline passed while the bundle was staged')
        record = dict(schema_version=PROTOCOL_VERSION, state='COMMITTED', action=action,
            gate=gate, issuer=store.issuer, identity=identity, source=source,
            generation=generation, snapshot_digest=snapshot.digest(),
            prerequisite_digest=snapshot.prerequisite_digest, token=token,
            expires_at=expires_at, monotonic_deadline_ns=monotonic_deadline_ns,
            boot_id=boot_id, bundle=manifest, bundle_path=str(staged))
        # One rename activates the whole bundle. Publishing each artifact separately would
        # expose a half-active state that a reader could consume.
        _publish(store._commit_path, canonical_json_bytes(record) + b'\n')
        attest(store, gates)
        return record


def consumed_generation(store: AuthorityStore) -> int:
    """The highest generation whose admission has been spent.

    An admission authorises one action. Without a durable record of that, the published
    commit stays readable and every re-read returns the same COMMITTED record, so the one
    action it authorised can be performed again for as long as the deadline holds.
    """
    try:
        value = json.loads(_protected_bytes(store.path / CONSUMPTION_NAME))
    except FileNotFoundError:
        return 0
    generation = value.get('generation')
    if value.get('schema_version') != PROTOCOL_VERSION or value.get('issuer') != store.issuer:
        raise AuthorityHeld('consumption ledger does not belong to this issuer protocol')
    if type(generation) is not int or not 0 <= generation <= MAX_GENERATION:
        raise AuthorityHeld('consumption ledger is not a bounded generation')
    return generation


def consume(store: AuthorityStore, *, action: str, gate: str, identity: dict[str, Any],
            source: dict[str, Any], gates: tuple[str, ...],
            apply: Callable[[dict[str, Any], Path], object] | None = None,
            now_ns: int | None = None, monotonic_ns: int | None = None) -> dict[str, Any]:
    """Spend one admission, verifying it against the live store under the lock.

    A consumer never trusts a token it was handed. It re-derives the snapshot itself and
    requires the published commit to match the store as it is now, so a revocation that
    landed after the commit prevents this admission rather than being discovered later.

    `apply` is the effect the admission authorises, and it runs inside this same critical
    section. The consumption is recorded *before* the effect is applied: a crash in between
    leaves the admission spent with nothing published, which fails closed and needs a fresh
    admission, whereas applying first would let the same effect land twice.
    """
    _require_admitted_action(action)
    if apply is not None and not callable(apply):
        raise AuthorityHeld('an admitted effect must be callable')
    with store.locked():
        try:
            record = json.loads(_protected_bytes(store._commit_path))
        except FileNotFoundError as error:
            raise AuthorityHeld('no committed admission is published') from error
        if record.get('schema_version') != PROTOCOL_VERSION or record.get('state') != 'COMMITTED':
            raise AuthorityHeld('published admission is not a committed record of this protocol')
        # The bundle must live under this store's own staging directory. A record that
        # points anywhere else would let a consumer be aimed at an arbitrary path.
        bundle_path = Path(record.get('bundle_path', ''))
        if bundle_path.parent != store.path / 'staging' or not bundle_path.is_dir():
            raise AuthorityHeld('published admission does not reference this store bundle')
        if record['action'] != action or record['gate'] != gate:
            raise AuthorityHeld('published admission is for a different action')
        if record['identity'] != identity or record['source'] != source:
            raise AuthorityHeld('published admission is for a different identity or source')
        if record['boot_id'] != _boot_identity():
            raise AuthorityHeld('published admission is from a different boot')
        # Protocol integrity is checked before anything else about this store is trusted,
        # the consumption ledger included: a store that left the protocol tells you nothing
        # reliable about what has already been spent.
        require_no_bypassing_writer(store, gates)
        spent = consumed_generation(store)
        if record['generation'] <= spent:
            raise AuthorityHeld('this admission has already been consumed')
        now = time.time_ns() if now_ns is None else now_ns
        snapshot = store.snapshot(gates, now_ns=now)
        if snapshot.generation != record['generation']:
            raise AuthorityHeld('authority generation moved after this admission was published')
        if snapshot.digest() != record['snapshot_digest']:
            raise AuthorityHeld('issuer store no longer matches the admitted snapshot')
        if _deadline_ns(record['expires_at']) <= now:
            raise AuthorityHeld('admitted action has expired')
        # The wall clock alone is settable, so a rollback would extend a deadline that has
        # already passed. The monotonic deadline is not settable, and the boot check above is
        # what makes it comparable: a monotonic reading from another boot means nothing.
        elapsed = time.monotonic_ns() if monotonic_ns is None else monotonic_ns
        if record['monotonic_deadline_ns'] <= elapsed:
            raise AuthorityHeld('admitted action has expired on the monotonic deadline')
        for name, digest in sorted(record['bundle'].items()):
            if hashlib.sha256(_protected_bytes(bundle_path / name)).hexdigest() != digest:
                raise AuthorityHeld(f'bundle member {name} differs from the committed manifest')
        _publish(store.path / CONSUMPTION_NAME, canonical_json_bytes(dict(
            schema_version=PROTOCOL_VERSION, issuer=store.issuer,
            generation=record['generation'], action=action, gate=gate,
            token=record['token'], consumed_at_ns=now)) + b'\n')
        if apply is not None:
            apply(record, bundle_path)
        return record


def recover(store: AuthorityStore) -> dict[str, Any]:
    """Report what a crash left behind. A recovered commit is never replayed here.

    Staged generations above the published commit are the signature of a crash between
    admission and durable publication. They are retained for inspection and are not
    consumable: a new effect requires a fresh admission. A published commit at or below the
    consumption ledger is equally not consumable, which is what makes `replayable=False` a
    property of the store rather than a note in this report.
    """
    published = 0
    try:
        published = json.loads(_protected_bytes(store._commit_path))['generation']
    except (FileNotFoundError, KeyError, ValueError):
        published = 0
    staging = store.path / 'staging'
    orphaned = []
    if staging.is_dir():
        for entry in sorted(staging.iterdir()):
            if entry.is_dir() and int(entry.name) > published:
                orphaned.append(entry.name)
    spent = consumed_generation(store)
    return dict(published_generation=published, store_generation=store.generation(),
                consumed_generation=spent, orphaned_generations=orphaned,
                published_commit_consumable=published > spent, replayable=False,
                note='A recovered commit requires a fresh admission before any new effect.')
