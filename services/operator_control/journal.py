"""Create-only canonical operator command journal."""

from __future__ import annotations

import fcntl
import hmac
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, TypeVar

from pydantic import BaseModel, ValidationError

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.operator_control.contracts import (
    CommandAppliedV1,
    CommandIntentV1,
    CommandReceiptV1,
)
from packages.operator_control.hashing import journal_sha256

from .protected_fs import (
    ProtectedFilesystemError,
    create_private_file,
    open_private_directory,
    read_private_file,
    require_private_regular_file,
)
from .state_store import OperatorStatePaths


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_JOURNAL_BYTES = 65_536
_Model = TypeVar("_Model", bound=BaseModel)


class CommandJournalError(ValueError):
    """Journal evidence is absent, conflicting, malformed, or unsafe."""

    code = "COMMAND_JOURNAL_UNSAFE"


@dataclass(frozen=True, slots=True)
class JournalSnapshot:
    intent: CommandIntentV1 | None
    applied: CommandAppliedV1 | None
    receipt: CommandReceiptV1 | None


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate journal field")
        result[key] = value
    return result


def _validate_key(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise CommandJournalError("command journal key is unsafe")
    return value


def _validate_digest(model: BaseModel, field: str) -> None:
    observed = getattr(model, field)
    expected = journal_sha256(model, field)
    if not hmac.compare_digest(observed, expected):
        raise CommandJournalError("command journal digest is unsafe")


def _parse(raw: bytes, model_type: type[_Model], digest_field: str) -> _Model:
    try:
        if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
            raise ValueError("journal record must have one final newline")
        document = json.loads(raw[:-1].decode("utf-8"), object_pairs_hook=_pairs)
        model = model_type.model_validate(document)
        _validate_digest(model, digest_field)
        if canonical_json_bytes(model) + b"\n" != raw:
            raise ValueError("journal record is not canonical JSON")
        return model
    except (
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        if isinstance(exc, CommandJournalError):
            raise
        raise CommandJournalError("command journal record is unsafe") from exc


class CommandJournal:
    def __init__(
        self,
        paths: OperatorStatePaths,
        *,
        failpoint: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.paths = paths
        self._failpoint = failpoint

    @contextmanager
    def locked(self) -> Iterator[None]:
        descriptor = -1
        try:
            with open_private_directory(self.paths.command_root) as root:
                descriptor = os.open(
                    "lock",
                    os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=root.descriptor,
                )
                opened = os.fstat(descriptor)
                require_private_regular_file(opened, max_bytes=0)
                named = os.stat("lock", dir_fd=root.descriptor, follow_symlinks=False)
                if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                    raise ProtectedFilesystemError(
                        "command journal lock identity changed"
                    )
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                root.recheck()
                named = os.stat(
                    "lock", dir_fd=root.descriptor, follow_symlinks=False
                )
                require_private_regular_file(named, max_bytes=0)
                if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                    raise ProtectedFilesystemError(
                        "command journal lock identity changed"
                    )
                yield
                root.recheck()
                named = os.stat(
                    "lock", dir_fd=root.descriptor, follow_symlinks=False
                )
                require_private_regular_file(named, max_bytes=0)
                if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                    raise ProtectedFilesystemError(
                        "command journal lock identity changed"
                    )
        except (OSError, ProtectedFilesystemError) as exc:
            raise CommandJournalError("command journal lock is unsafe") from exc
        finally:
            if descriptor >= 0:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _load(
        self,
        directory_name: str,
        key: str,
        model_type: type[_Model],
        digest_field: str,
    ) -> _Model | None:
        try:
            with open_private_directory(
                self.paths.command_root / directory_name
            ) as directory:
                raw = read_private_file(
                    directory,
                    f"{_validate_key(key)}.json",
                    max_bytes=_MAX_JOURNAL_BYTES,
                    missing_ok=True,
                )
            return None if raw is None else _parse(raw, model_type, digest_field)
        except (ProtectedFilesystemError, CommandJournalError) as exc:
            raise CommandJournalError("command journal record is unsafe") from exc

    def load(self, idempotency_key_sha256: str) -> JournalSnapshot:
        key = _validate_key(idempotency_key_sha256)
        snapshot = JournalSnapshot(
            self._load("intents", key, CommandIntentV1, "intent_sha256"),
            self._load("applied", key, CommandAppliedV1, "applied_sha256"),
            self._load("receipts", key, CommandReceiptV1, "receipt_sha256"),
        )
        if (
            snapshot.intent is not None
            and snapshot.intent.idempotency_key_sha256 != key
        ) or (
            snapshot.receipt is not None
            and snapshot.receipt.idempotency_key_sha256 != key
        ):
            raise CommandJournalError("command journal record path is unsafe")
        self._validate_relations(snapshot)
        return snapshot

    @staticmethod
    def _validate_relations(snapshot: JournalSnapshot) -> None:
        intent, applied, receipt = snapshot.intent, snapshot.applied, snapshot.receipt
        if intent is not None:
            CommandJournal._validate_intent(intent)
        if applied is not None and (
            intent is None or applied.intent_sha256 != intent.intent_sha256
        ):
            raise CommandJournalError("command journal is relationally unsafe")
        if intent is not None and applied is not None:
            allowed_kinds = {
                "PAPER": {
                    "NO_CHANGE",
                    "MODE_REPLACED",
                    "RECOVERED_MODE_REPLACEMENT",
                },
                "KILL_SWITCH_ACTIVE": {
                    "NO_CHANGE",
                    "KILL_SWITCH_CREATED",
                    "RECOVERED_KILL_SWITCH_CREATE",
                },
                "KILL_SWITCH_INACTIVE": {
                    "KILL_SWITCH_CLEARED_TO_TOMBSTONE",
                    "RECOVERED_KILL_SWITCH_CLEAR",
                },
            }
            clear = intent.desired_state == "KILL_SWITCH_INACTIVE"
            if (
                applied.application_kind not in allowed_kinds[intent.desired_state]
                or (applied.tombstone_sha256 is not None) != clear
            ):
                raise CommandJournalError("command journal application is unsafe")
        if receipt is None:
            return
        if intent is None or applied is None:
            raise CommandJournalError("command journal is relationally unsafe")
        mirrored = (
            "command_id",
            "idempotency_key_sha256",
            "correlation_id",
            "request_sha256",
            "actor",
            "command_type",
            "desired_state",
            "prior_state_sha256",
            "expected_state_sha256",
            "safety_evidence_sha256",
            "reason_sha256",
            "accepted_at",
            "intent_sha256",
        )
        if (
            any(getattr(receipt, name) != getattr(intent, name) for name in mirrored)
            or receipt.applied_sha256 != applied.applied_sha256
            or receipt.resulting_state_sha256 != applied.resulting_state_sha256
            or receipt.applied_at != applied.applied_at
            or not (receipt.accepted_at <= receipt.applied_at <= receipt.completed_at)
        ):
            raise CommandJournalError("command journal is relationally unsafe")
        recovered = applied.application_kind.startswith("RECOVERED_")
        expected_outcome = (
            "NO_CHANGE"
            if applied.application_kind == "NO_CHANGE"
            else "RECOVERED_APPLIED"
            if recovered
            else "APPLIED"
        )
        if receipt.outcome != expected_outcome:
            raise CommandJournalError("command journal outcome is unsafe")

    @staticmethod
    def _validate_intent(intent: CommandIntentV1) -> None:
        mode = (
            intent.command_type == "SET_REQUESTED_MODE"
            and intent.desired_state == "PAPER"
        )
        activate = (
            intent.command_type == "SET_KILL_SWITCH"
            and intent.desired_state == "KILL_SWITCH_ACTIVE"
            and intent.reason_sha256 is not None
            and intent.safety_evidence_sha256 is None
        )
        clear = (
            intent.command_type == "SET_KILL_SWITCH"
            and intent.desired_state == "KILL_SWITCH_INACTIVE"
            and intent.reason_sha256 is None
            and intent.safety_evidence_sha256 is not None
            and intent.expected_state_sha256 is not None
        )
        if (
            not (mode or activate or clear)
            or intent.desired_file_sha256 is None
            or (
                mode
                and (
                    intent.reason_sha256 is not None
                    or intent.safety_evidence_sha256 is not None
                )
            )
        ):
            raise CommandJournalError("command journal intent is unsafe")

    def _create(
        self, directory_name: str, key: str, model: BaseModel, digest_field: str
    ) -> None:
        _validate_key(key)
        _validate_digest(model, digest_field)
        if isinstance(model, CommandIntentV1):
            self._validate_intent(model)
        raw = canonical_json_bytes(model) + b"\n"
        try:
            with open_private_directory(
                self.paths.command_root / directory_name
            ) as directory:
                create_private_file(
                    directory,
                    f"{key}.json",
                    raw,
                    max_bytes=_MAX_JOURNAL_BYTES,
                )
        except ProtectedFilesystemError as exc:
            raise CommandJournalError(str(exc)) from exc

    def create_intent(self, intent: CommandIntentV1) -> None:
        self._create("intents", intent.idempotency_key_sha256, intent, "intent_sha256")
        self._failpoint("AFTER_INTENT_FSYNC")

    def create_applied(
        self, idempotency_key_sha256: str, applied: CommandAppliedV1
    ) -> None:
        self._create("applied", idempotency_key_sha256, applied, "applied_sha256")
        self._failpoint("AFTER_APPLIED_FSYNC")

    def create_receipt(self, receipt: CommandReceiptV1) -> None:
        self._failpoint("BEFORE_RECEIPT_FSYNC")
        self._create(
            "receipts", receipt.idempotency_key_sha256, receipt, "receipt_sha256"
        )
        self._failpoint("AFTER_RECEIPT_FSYNC")
