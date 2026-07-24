"""Strict consumer for the fresh Phase 4B safety-state snapshot."""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from packages.runtime_release import config as runtime_config
from packages.runtime_release.config import (
    RuntimeAuthorityV2,
    load_runtime_authority_v2,
)
from services.safety_state_exporter.exporter import SNAPSHOT_TTL_SECONDS

from .errors import SafetyBlockedError
from .safety import KillSwitchState, SafetyMode, SafetySnapshot, assert_safe


_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_MAX_SNAPSHOT_BYTES = 8192
_FIELDS = frozenset({
    "schema_version",
    "exporter_commit",
    "generated_at",
    "expires_at",
    "requested_mode",
    "effective_mode",
    "live_execution_enabled",
    "live_trading_approved",
    "kill_switch_state",
    "source_fingerprint",
})


@dataclass(frozen=True, slots=True)
class SafetyEvidence(SafetySnapshot):
    """Exact identity and validity window for one safe snapshot read."""

    snapshot_sha256: str
    generated_at: datetime
    expires_at: datetime


def validate_current_safety_evidence(
    evidence: object,
    now: datetime,
) -> SafetyEvidence:
    """Defend the final spawn boundary against forged or aged typed evidence."""

    if not isinstance(evidence, SafetyEvidence):
        _blocked("SAFETY_STATE_INVALID", "typed safety evidence is required")
    if _SHA256.fullmatch(evidence.snapshot_sha256) is None:
        _blocked("SAFETY_STATE_INVALID", "safety evidence digest is invalid")
    if (
        not isinstance(evidence.generated_at, datetime)
        or evidence.generated_at.tzinfo is None
        or evidence.generated_at.utcoffset() is None
        or not isinstance(evidence.expires_at, datetime)
        or evidence.expires_at.tzinfo is None
        or evidence.expires_at.utcoffset() is None
    ):
        _blocked("SAFETY_STATE_INVALID", "safety evidence timestamps are invalid")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        _blocked("SAFETY_STATE_CLOCK_INVALID", "worker safety clock is invalid")
    generated = evidence.generated_at.astimezone(UTC)
    expires = evidence.expires_at.astimezone(UTC)
    current = now.astimezone(UTC)
    if expires - generated != timedelta(seconds=SNAPSHOT_TTL_SECONDS):
        _blocked("SAFETY_STATE_WINDOW_INVALID", "safety evidence validity window is invalid")
    if generated > current:
        _blocked("SAFETY_STATE_FROM_FUTURE", "safety evidence is from the future")
    if current >= expires:
        _blocked("SAFETY_STATE_STALE", "safety evidence is stale")
    assert_safe(evidence)
    return evidence


def _blocked(reason: str, message: str, cause: BaseException | None = None) -> None:
    error = SafetyBlockedError(reason, message)
    if cause is None:
        raise error
    raise error from cause


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate safety-state field")
        result[key] = value
    return result


def _parse_timestamp(raw: object) -> datetime:
    if not isinstance(raw, str) or _TIMESTAMP.fullmatch(raw) is None:
        raise ValueError("invalid safety-state timestamp")
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


class SafetyStateClient:
    def __init__(
        self,
        path: Path,
        *,
        expected_exporter_commit: str | None,
        expected_source_fingerprint: str,
        expected_owner_uid: int | None = None,
        protected_root_owned: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not isinstance(expected_exporter_commit, str)
            or _COMMIT.fullmatch(expected_exporter_commit) is None
        ):
            _blocked(
                "SAFETY_STATE_EXPORTER_COMMIT_INVALID",
                "expected safety exporter commit is absent or invalid",
            )
        if (
            not isinstance(expected_source_fingerprint, str)
            or _SHA256.fullmatch(expected_source_fingerprint) is None
        ):
            _blocked(
                "SAFETY_STATE_SOURCE_INVALID",
                "expected safety source fingerprint is invalid",
            )
        self._path = Path(path)
        self._expected_exporter_commit = expected_exporter_commit
        self._expected_source_fingerprint = expected_source_fingerprint
        self._expected_owner_uid = (
            os.geteuid() if expected_owner_uid is None else expected_owner_uid
        )
        self._protected_root_owned = protected_root_owned
        self._clock = clock or (lambda: datetime.now(UTC))

    def _read(self) -> bytes:
        if self._protected_root_owned:
            try:
                raw = runtime_config.read_protected_file_current(self._path)
            except Exception as exc:
                _blocked(
                    "SAFETY_STATE_UNREADABLE",
                    "safety-state snapshot cannot be opened safely",
                    exc,
                )
            if len(raw) > _MAX_SNAPSHOT_BYTES:
                _blocked("SAFETY_STATE_INVALID", "safety-state snapshot is too large")
            return raw
        descriptor = -1
        try:
            descriptor = os.open(
                self._path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError as exc:
            _blocked("SAFETY_STATE_MISSING", "safety-state snapshot is missing", exc)
        except OSError as exc:
            reason = "SAFETY_STATE_INVALID" if exc.errno == errno.ELOOP else "SAFETY_STATE_UNREADABLE"
            _blocked(reason, "safety-state snapshot cannot be opened safely", exc)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                _blocked("SAFETY_STATE_INVALID", "safety-state snapshot is not a regular file")
            if info.st_uid != self._expected_owner_uid:
                _blocked("SAFETY_STATE_OWNER_UNSAFE", "safety-state snapshot owner is unsafe")
            if stat.S_IMODE(info.st_mode) != 0o600:
                _blocked("SAFETY_STATE_MODE_UNSAFE", "safety-state snapshot mode is unsafe")
            chunks: list[bytes] = []
            observed = 0
            while True:
                chunk = os.read(
                    descriptor, min(4096, _MAX_SNAPSHOT_BYTES + 1 - observed),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                observed += len(chunk)
                if observed > _MAX_SNAPSHOT_BYTES:
                    _blocked("SAFETY_STATE_INVALID", "safety-state snapshot is too large")
            return b"".join(chunks)
        except SafetyBlockedError:
            raise
        except OSError as exc:
            _blocked("SAFETY_STATE_UNREADABLE", "safety-state snapshot cannot be read", exc)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def evidence(self) -> SafetyEvidence:
        """Return authenticated current evidence without applying worker policy."""

        try:
            raw = self._read()
            document = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_pairs,
            )
            if not isinstance(document, dict) or set(document) != _FIELDS:
                raise ValueError("safety-state schema fields are invalid")
            if type(document["schema_version"]) is not int or document["schema_version"] != 1:
                raise ValueError("safety-state schema version is invalid")
            commit = document["exporter_commit"]
            fingerprint = document["source_fingerprint"]
            if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
                raise ValueError("safety-state exporter commit is invalid")
            if not isinstance(fingerprint, str) or _SHA256.fullmatch(fingerprint) is None:
                raise ValueError("safety-state source fingerprint is invalid")
            if type(document["live_execution_enabled"]) is not bool:
                raise ValueError("safety-state execution gate is invalid")
            if type(document["live_trading_approved"]) is not bool:
                raise ValueError("safety-state approval gate is invalid")
            generated = _parse_timestamp(document["generated_at"])
            expires = _parse_timestamp(document["expires_at"])
            requested = SafetyMode(document["requested_mode"])
            effective = SafetyMode(document["effective_mode"])
            kill_switch = KillSwitchState(document["kill_switch_state"])
        except SafetyBlockedError:
            raise
        except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            _blocked("SAFETY_STATE_INVALID", "safety-state snapshot schema is invalid", exc)

        if not hmac.compare_digest(commit, self._expected_exporter_commit):
            _blocked(
                "SAFETY_STATE_EXPORTER_COMMIT_MISMATCH",
                "safety-state exporter commit does not match authority",
            )
        if not hmac.compare_digest(fingerprint, self._expected_source_fingerprint):
            _blocked(
                "SAFETY_STATE_SOURCE_MISMATCH",
                "safety-state source does not match the canonical allowlist",
            )
        if expires - generated != timedelta(seconds=SNAPSHOT_TTL_SECONDS):
            _blocked("SAFETY_STATE_WINDOW_INVALID", "safety-state validity window is invalid")
        now = self._clock()
        if now.tzinfo is None:
            _blocked("SAFETY_STATE_CLOCK_INVALID", "worker safety clock is invalid")
        now = now.astimezone(UTC)
        if generated > now:
            _blocked("SAFETY_STATE_FROM_FUTURE", "safety-state snapshot is from the future")
        if now >= expires:
            _blocked("SAFETY_STATE_STALE", "safety-state snapshot is stale")
        try:
            current_raw = self._read()
        except SafetyBlockedError:
            raise
        if not hmac.compare_digest(
            hashlib.sha256(raw).digest(), hashlib.sha256(current_raw).digest()
        ):
            _blocked(
                "SAFETY_STATE_CHANGED",
                "safety-state snapshot changed during validation",
            )
        snapshot = SafetyEvidence(
            requested_mode=requested,
            effective_mode=effective,
            live_execution_enabled=document["live_execution_enabled"],
            live_trading_approved=document["live_trading_approved"],
            kill_switch_state=kill_switch,
            snapshot_sha256=hashlib.sha256(raw).hexdigest(),
            generated_at=generated,
            expires_at=expires,
        )
        return snapshot

    def snapshot(self) -> SafetyEvidence:
        snapshot = self.evidence()
        assert_safe(snapshot)
        return snapshot


class AuthorityBoundSafetyPreflight:
    """Read a fresh snapshot only while its protected authority remains pinned."""

    def __init__(
        self,
        pinned_authority: object,
        client: SafetyStateClient,
        *,
        authority_loader: Callable[[], object] | None = None,
    ) -> None:
        self._pinned = pinned_authority
        self._client = client
        self._authority_loader = authority_loader or load_runtime_authority_v2

    def _matches_v1_fixture(self, current: object) -> bool:
        safety = getattr(current, "safety", None)
        return (
            getattr(current, "_identity", None) == self._pinned.authority_identity
            and isinstance(getattr(current, "_document_sha256", None), str)
            and hmac.compare_digest(
                current._document_sha256, self._pinned.authority_document_sha256
            )
            and getattr(safety, "snapshot_path", None) == self._pinned.safety_snapshot_path
            and getattr(safety, "exporter_commit", None) == self._pinned.safety_exporter_commit
            and getattr(safety, "source_fingerprint", None) == self._pinned.safety_source_fingerprint
        )

    def _matches_v2(self, current: object) -> bool:
        safety = getattr(current, "safety", None)
        pinned_runtime = getattr(self._pinned, "runtime_authority", None)
        return (
            isinstance(current, RuntimeAuthorityV2)
            and isinstance(pinned_runtime, RuntimeAuthorityV2)
            and getattr(current, "_authority_pin", None)
            == getattr(self._pinned, "authority_pin", None)
            == getattr(pinned_runtime, "_authority_pin", None)
            and getattr(safety, "snapshot_path", None)
            == self._pinned.safety_snapshot_path
            and getattr(safety, "exporter_commit", None)
            == self._pinned.safety_exporter_commit
            and getattr(safety, "source_fingerprint", None)
            == self._pinned.safety_source_fingerprint
        )

    def __call__(self) -> SafetyEvidence:
        try:
            before = self._authority_loader()
            is_v2 = isinstance(before, RuntimeAuthorityV2)
            matches = self._matches_v2 if is_v2 else self._matches_v1_fixture
            if not matches(before):
                raise ValueError
            snapshot = self._client.snapshot()
            after = self._authority_loader() if is_v2 else before.recheck()
            if not matches(after):
                raise ValueError
            if is_v2:
                before_dynamic = getattr(before, "_dynamic_evidence_pin", None)
                after_dynamic = getattr(after, "_dynamic_evidence_pin", None)
                if (
                    not isinstance(before_dynamic, tuple)
                    or not before_dynamic
                    or before_dynamic != after_dynamic
                    or before_dynamic[0] != snapshot.snapshot_sha256
                ):
                    raise ValueError
            return snapshot
        except SafetyBlockedError:
            raise
        except Exception:
            _blocked(
                "SAFETY_AUTHORITY_CHANGED",
                "protected safety authority changed around snapshot validation",
            )


__all__ = [
    "AuthorityBoundSafetyPreflight",
    "SafetyEvidence",
    "SafetyStateClient",
    "validate_current_safety_evidence",
]
