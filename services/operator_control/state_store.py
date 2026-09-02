"""Protected source-state parsing, mutation, tombstones, and recovery classification."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Literal

from pydantic import TypeAdapter, ValidationError

from packages.engine_contracts.serialization import CanonicalUtcDateTime
from packages.operator_control.contracts import CommandIntentV1, OperatorSourceStateV1
from packages.operator_control.hashing import reason_sha256, state_sha256

from .protected_fs import (
    ProtectedFilesystemError,
    open_private_directory,
    read_private_file,
    rename_private_file_noreplace,
    replace_private_file,
)


_MAX_MODE_BYTES = 128
_MAX_KILL_SWITCH_BYTES = 1024
_TIMESTAMP = TypeAdapter(CanonicalUtcDateTime)
_SHA256_NAME = set("0123456789abcdef")


@dataclass(frozen=True, slots=True)
class OperatorStatePaths:
    data_root: Path
    command_root: Path
    mode_path: Path
    kill_switch_path: Path

    def __post_init__(self) -> None:
        expected = (
            self.data_root / ".operator-commands",
            self.data_root / ".mode",
            self.data_root / ".kill_switch",
        )
        values = (self.command_root, self.mode_path, self.kill_switch_path)
        if not self.data_root.is_absolute() or values != expected:
            raise ValueError(
                "operator state paths must be canonical children of an absolute root"
            )


@dataclass(frozen=True, slots=True)
class ClearResult:
    state: OperatorSourceStateV1
    tombstone_sha256: str


class RecoveryError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_mode(raw: bytes | None) -> tuple[str, str | None]:
    if raw is None:
        return "UNKNOWN", None
    digest = _digest(raw)
    return (
        {b"paper\n": "PAPER", b"dryrun\n": "DRYRUN", b"live\n": "LIVE"}.get(
            raw, "UNKNOWN"
        ),
        digest,
    )


def _parse_kill_switch(
    raw: bytes | None,
) -> tuple[str, datetime | None, str | None, str | None]:
    if raw is None:
        return "INACTIVE", None, None, None
    digest = _digest(raw)
    try:
        text = raw.decode("utf-8")
        if not text.endswith("\n") or text.count("\n") != 1 or "\r" in text:
            raise ValueError
        timestamp, separator, reason = text[:-1].partition(": ")
        if not separator or not (1 <= len(reason) <= 256) or reason.strip() != reason:
            raise ValueError
        activated_at = _TIMESTAMP.validate_python(timestamp)
        return "ACTIVE", activated_at, reason, digest
    except (UnicodeError, ValueError, ValidationError):
        return "UNKNOWN", None, None, digest


class OperatorStateStore:
    def __init__(
        self,
        paths: OperatorStatePaths,
        *,
        failpoint: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.paths = paths
        self._failpoint = failpoint

    def read_state(self) -> OperatorSourceStateV1:
        with open_private_directory(self.paths.data_root) as data:
            try:
                mode_raw = read_private_file(
                    data,
                    self.paths.mode_path.name,
                    max_bytes=_MAX_MODE_BYTES,
                    missing_ok=True,
                )
            except ProtectedFilesystemError:
                mode_raw = None
                mode_unsafe = True
            else:
                mode_unsafe = False
            try:
                kill_raw = read_private_file(
                    data,
                    self.paths.kill_switch_path.name,
                    max_bytes=_MAX_KILL_SWITCH_BYTES,
                    missing_ok=True,
                )
            except ProtectedFilesystemError:
                kill_raw = None
                kill_unsafe = True
            else:
                kill_unsafe = False
        mode, mode_digest = _parse_mode(mode_raw)
        kill, activated_at, reason, kill_digest = _parse_kill_switch(kill_raw)
        if mode_unsafe:
            mode, mode_digest = "UNKNOWN", None
        if kill_unsafe:
            kill, activated_at, reason, kill_digest = "UNKNOWN", None, None, None
        payload = {
            "schema_version": "operator-source-state-v1",
            "requested_mode": mode,
            "kill_switch_state": kill,
            "kill_switch_activated_at": _canonical_timestamp(activated_at)
            if activated_at
            else None,
            "kill_switch_reason": reason,
            "mode_file_sha256": mode_digest,
            "kill_switch_file_sha256": kill_digest,
        }
        return OperatorSourceStateV1.model_validate(
            {**payload, "state_sha256": state_sha256(payload)}
        )

    def write_mode_bytes(
        self, value: bytes, *, expected_sha256: str | None = None
    ) -> OperatorSourceStateV1:
        if value != b"paper\n":
            raise ProtectedFilesystemError(
                "only exact PAPER source bytes are permitted"
            )
        with open_private_directory(self.paths.data_root) as data:
            replace_private_file(
                data,
                self.paths.mode_path.name,
                value,
                max_bytes=_MAX_MODE_BYTES,
                expected_sha256=expected_sha256,
            )
        return self.read_state()

    def apply_mode(self, intent: CommandIntentV1) -> OperatorSourceStateV1:
        desired = b"paper\n"
        current = self.read_state()
        if (
            intent.command_type != "SET_REQUESTED_MODE"
            or intent.desired_state != "PAPER"
            or intent.desired_file_sha256 != _digest(desired)
            or current.state_sha256 != intent.prior_state_sha256
        ):
            raise ProtectedFilesystemError(
                "mode intent does not bind the current state"
            )
        self._failpoint("BEFORE_STATE_APPLY")
        result = self.write_mode_bytes(
            desired, expected_sha256=current.mode_file_sha256
        )
        self._failpoint("AFTER_STATE_APPLY")
        return result

    def activate_kill_switch(
        self, intent: CommandIntentV1, desired_bytes: bytes
    ) -> OperatorSourceStateV1:
        state, activated_at, reason, digest = _parse_kill_switch(desired_bytes)
        current = self.read_state()
        if (
            intent.command_type != "SET_KILL_SWITCH"
            or intent.desired_state != "KILL_SWITCH_ACTIVE"
            or state != "ACTIVE"
            or activated_at != intent.accepted_at
            or reason is None
            or intent.reason_sha256 is None
            or not hmac.compare_digest(reason_sha256(reason), intent.reason_sha256)
            or digest != intent.desired_file_sha256
            or current.state_sha256 != intent.prior_state_sha256
            or current.kill_switch_state != "INACTIVE"
        ):
            raise ProtectedFilesystemError(
                "activation intent does not bind exact bytes"
            )
        self._failpoint("BEFORE_STATE_APPLY")
        with open_private_directory(self.paths.data_root) as data:
            replace_private_file(
                data,
                self.paths.kill_switch_path.name,
                desired_bytes,
                max_bytes=_MAX_KILL_SWITCH_BYTES,
                expected_sha256=None,
            )
        result = self.read_state()
        self._failpoint("AFTER_STATE_APPLY")
        return result

    def clear_kill_switch(self, intent: CommandIntentV1) -> ClearResult:
        current = self.read_state()
        if (
            intent.command_type != "SET_KILL_SWITCH"
            or intent.desired_state != "KILL_SWITCH_INACTIVE"
            or current.state_sha256 != intent.prior_state_sha256
            or current.kill_switch_state != "ACTIVE"
            or current.kill_switch_file_sha256 is None
            or current.kill_switch_file_sha256 != intent.desired_file_sha256
        ):
            raise ProtectedFilesystemError(
                "clear intent does not bind the active state"
            )
        self._failpoint("BEFORE_STATE_APPLY")
        with (
            open_private_directory(self.paths.data_root) as data,
            open_private_directory(
                self.paths.command_root / "tombstones"
            ) as tombstones,
        ):
            raw = rename_private_file_noreplace(
                data,
                self.paths.kill_switch_path.name,
                tombstones,
                f"{intent.idempotency_key_sha256}.kill-switch",
                max_bytes=_MAX_KILL_SWITCH_BYTES,
                expected_sha256=intent.desired_file_sha256,
            )
        result = ClearResult(self.read_state(), _digest(raw))
        self._failpoint("AFTER_STATE_APPLY")
        return result

    def tombstone_sha256(self, idempotency_digest: str) -> str | None:
        if len(idempotency_digest) != 64 or set(idempotency_digest) - _SHA256_NAME:
            raise ProtectedFilesystemError("tombstone digest name is invalid")
        with open_private_directory(
            self.paths.command_root / "tombstones"
        ) as tombstones:
            raw = read_private_file(
                tombstones,
                f"{idempotency_digest}.kill-switch",
                max_bytes=_MAX_KILL_SWITCH_BYTES,
                missing_ok=True,
            )
        return _digest(raw) if raw is not None else None


RecoveryDisposition = Literal[
    "RETRY",
    "RECOVERED_MODE_REPLACEMENT",
    "RECOVERED_KILL_SWITCH_CREATE",
    "RECOVERED_KILL_SWITCH_CLEAR",
]


def classify_recovery(
    intent: CommandIntentV1,
    current: OperatorSourceStateV1,
    *,
    tombstone_sha256: str | None,
) -> RecoveryDisposition:
    if intent.desired_state == "KILL_SWITCH_INACTIVE":
        if tombstone_sha256 is not None:
            if (
                tombstone_sha256 == intent.desired_file_sha256
                and current.kill_switch_state == "INACTIVE"
            ):
                return "RECOVERED_KILL_SWITCH_CLEAR"
            raise RecoveryError("COMMAND_OUTCOME_UNKNOWN")
        if current.state_sha256 == intent.prior_state_sha256:
            return "RETRY"
    elif tombstone_sha256 is not None:
        raise RecoveryError("COMMAND_OUTCOME_UNKNOWN")
    elif current.state_sha256 == intent.prior_state_sha256:
        return "RETRY"
    elif (
        intent.desired_state == "PAPER"
        and current.mode_file_sha256 == intent.desired_file_sha256
    ):
        return "RECOVERED_MODE_REPLACEMENT"
    elif (
        intent.desired_state == "KILL_SWITCH_ACTIVE"
        and current.kill_switch_file_sha256 == intent.desired_file_sha256
    ):
        return "RECOVERED_KILL_SWITCH_CREATE"
    raise RecoveryError("COMMAND_OUTCOME_UNKNOWN")
