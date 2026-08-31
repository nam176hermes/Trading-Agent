"""Canonical no-clobber recovery receipts for P1 Nautilus paper sessions."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from uuid import UUID

from packages.engine_contracts import canonical_json_bytes
from packages.nautilus_runtime_contracts.paper import (
    PaperSessionCheckpoint,
    PaperSessionState,
    parse_paper_command_frame,
)
from packages.nautilus_runtime_contracts.result import P1_ENGINE_VERSION
from packages.safety_evidence import CanonicalKillSwitchState
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

from .nautilus_checkpoint import (
    NautilusCheckpointRecord,
    ZERO_CHECKPOINT_SHA256,
    checkpoint_sha256,
)
from .nautilus_reconciliation import (
    NautilusChildState,
    NautilusRecoveryDisposition,
    NautilusRecoveryEvidence,
    NautilusRecoveryReason,
    reconcile_nautilus_paper,
)


NAUTILUS_RECOVERY_RECEIPT_SCHEMA = "trading-agent-nautilus-paper-recovery/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_TOP_LEVEL_KEYS = {
    "authority_limits",
    "checkpoint_sha256",
    "closure_digest",
    "command_chain_sha256",
    "config_digest",
    "engine_version",
    "evidence",
    "evidence_sha256",
    "reason_codes",
    "schema",
    "session_id",
    "source_commit",
    "verdict",
}
_EVIDENCE_KEYS = {
    "checkpoint",
    "child_outcome_proven",
    "child_state",
    "closure_digest",
    "config_digest",
    "current_child_identity",
    "engine_version",
    "expected_closure_digest",
    "expected_config_digest",
    "expected_engine_version",
    "expected_source_commit",
    "expected_target_schedule_cursor",
    "final_engine_observation_sha256",
    "kill_switch_state",
    "ledger_event_prefix_sha256",
    "ledger_last_event_digest",
    "ledger_last_sequence",
    "portfolio_state_hash",
    "session_id",
    "source_commit",
    "target_schedule_cursor",
}
_CHECKPOINT_RECORD_KEYS = {
    "checkpoint",
    "checkpoint_sha256",
    "event_batch_sha256",
    "parity_receipt_sha256",
}
_AUTHORITY_LIMITS = {
    "live_authorized": False,
    "network_query_allowed": False,
    "production_authorized": False,
}
_STEP_SCHEMA = "trading-agent-nautilus-paper-recovery-step/v1"
_INTENT_SCHEMA = "trading-agent-nautilus-paper-recovery-intent/v1"
_INTENT_NAME = re.compile(r"intent-([0-9]{8})\.json\Z", re.ASCII)
_STEP_NAME = re.compile(r"step-([0-9]{8})\.json\Z", re.ASCII)
_TEMP_NAME = re.compile(
    r"\.(?:intent|step)-[0-9]{8}\.json\.[0-9a-f]{32}\.tmp\Z", re.ASCII
)
_INTENT_KEYS = {
    "closure_digest",
    "command_base64",
    "command_sha256",
    "config_digest",
    "engine_version",
    "expected_checkpoint_sha256",
    "prior_step_sha256",
    "schema",
    "sequence",
    "source_commit",
}
_STEP_KEYS = {
    "checkpoint",
    "closure_digest",
    "command_base64",
    "command_sha256",
    "config_digest",
    "engine_version",
    "intent_sha256",
    "prior_step_sha256",
    "schema",
    "sequence",
    "source_commit",
}


@dataclass(frozen=True, slots=True)
class NautilusRecoveryReceipt:
    schema: str
    verdict: NautilusRecoveryDisposition
    reason_codes: tuple[NautilusRecoveryReason, ...]
    session_id: UUID
    engine_version: str
    closure_digest: str
    command_chain_sha256: str
    source_commit: str
    config_digest: str
    checkpoint_sha256: str
    evidence_sha256: str
    receipt_sha256: str
    evidence: NautilusRecoveryEvidence


@dataclass(frozen=True, slots=True)
class NautilusRecoveryStep:
    sequence: int
    command_raw: bytes
    checkpoint: NautilusCheckpointRecord
    engine_version: str
    closure_digest: str
    source_commit: str
    config_digest: str
    prior_step_sha256: str
    step_sha256: str


@dataclass(frozen=True, slots=True)
class RecoveredNautilusPaperSession:
    session: object
    checkpoint: NautilusCheckpointRecord
    disposition: NautilusRecoveryDisposition


def _checkpoint_document(record: NautilusCheckpointRecord | None) -> object:
    if record is None:
        return None
    return {
        "checkpoint": record.checkpoint.model_dump(mode="json"),
        "checkpoint_sha256": record.checkpoint_sha256,
        "event_batch_sha256": record.event_batch_sha256,
        "parity_receipt_sha256": record.parity_receipt_sha256,
    }


def _evidence_document(evidence: NautilusRecoveryEvidence) -> dict[str, object]:
    return {
        "checkpoint": _checkpoint_document(evidence.checkpoint),
        "child_outcome_proven": evidence.child_outcome_proven,
        "child_state": evidence.child_state.value,
        "closure_digest": evidence.closure_digest,
        "config_digest": evidence.config_digest,
        "current_child_identity": evidence.current_child_identity,
        "engine_version": evidence.engine_version,
        "expected_closure_digest": evidence.expected_closure_digest,
        "expected_config_digest": evidence.expected_config_digest,
        "expected_engine_version": evidence.expected_engine_version,
        "expected_source_commit": evidence.expected_source_commit,
        "expected_target_schedule_cursor": evidence.expected_target_schedule_cursor,
        "final_engine_observation_sha256": evidence.final_engine_observation_sha256,
        "kill_switch_state": evidence.kill_switch_state.value,
        "ledger_event_prefix_sha256": evidence.ledger_event_prefix_sha256,
        "ledger_last_event_digest": evidence.ledger_last_event_digest,
        "ledger_last_sequence": evidence.ledger_last_sequence,
        "portfolio_state_hash": evidence.portfolio_state_hash,
        "session_id": str(evidence.session_id),
        "source_commit": evidence.source_commit,
        "target_schedule_cursor": evidence.target_schedule_cursor,
    }


def _build_document(
    evidence: NautilusRecoveryEvidence, command_chain_sha256: str
) -> dict[str, object]:
    if (
        type(command_chain_sha256) is not str
        or _SHA256.fullmatch(command_chain_sha256) is None
    ):
        raise ValueError("recovery command chain identity is invalid")
    decision = reconcile_nautilus_paper(evidence)
    evidence_document = _evidence_document(evidence)
    checkpoint_sha = (
        ZERO_CHECKPOINT_SHA256
        if evidence.checkpoint is None
        else evidence.checkpoint.checkpoint_sha256
    )
    return {
        "authority_limits": _AUTHORITY_LIMITS,
        "checkpoint_sha256": checkpoint_sha,
        "closure_digest": evidence.closure_digest,
        "command_chain_sha256": command_chain_sha256,
        "config_digest": evidence.config_digest,
        "engine_version": evidence.engine_version,
        "evidence": evidence_document,
        "evidence_sha256": hashlib.sha256(
            canonical_json_bytes(evidence_document)
        ).hexdigest(),
        "reason_codes": [reason.value for reason in decision.reason_codes],
        "schema": NAUTILUS_RECOVERY_RECEIPT_SCHEMA,
        "session_id": str(evidence.session_id),
        "source_commit": evidence.source_commit,
        "verdict": decision.disposition.value,
    }


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("recovery receipt contains a duplicate key")
        result[key] = value
    return result


def _checkpoint_from_document(value: object) -> NautilusCheckpointRecord | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != _CHECKPOINT_RECORD_KEYS:
        raise ValueError("recovery checkpoint record is invalid")
    try:
        checkpoint = PaperSessionCheckpoint.model_validate_json(
            canonical_json_bytes(value["checkpoint"])
        )
        return NautilusCheckpointRecord(
            checkpoint=checkpoint,
            checkpoint_sha256=value["checkpoint_sha256"],
            event_batch_sha256=value["event_batch_sha256"],
            parity_receipt_sha256=value["parity_receipt_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("recovery checkpoint record is invalid") from exc


def _read_sealed(path: Path, maximum: int) -> bytes:
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        return _read_sealed_at(directory_fd, path.name, maximum)
    finally:
        os.close(directory_fd)


def _read_sealed_at(directory_fd: int, name: str, maximum: int) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= maximum
        ):
            raise ValueError("durable recovery evidence is not sealed")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 65_536):
            total += len(chunk)
            if total > maximum:
                raise ValueError("durable recovery evidence is oversized")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_sealed(root: Path, name: str, raw: bytes) -> None:
    directory_fd = os.open(
        root,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        _write_sealed_at(directory_fd, name, raw)
    finally:
        os.close(directory_fd)


def _write_sealed_at(directory_fd: int, name: str, raw: bytes) -> None:
    temporary = f".{name}.{secrets.token_hex(16)}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=directory_fd,
        )
        os.fchmod(descriptor, 0o400)
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("durable recovery write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.unlink(temporary, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _step_document(
    command_raw: bytes,
    checkpoint: NautilusCheckpointRecord,
    *,
    engine_version: str,
    closure_digest: str,
    source_commit: str,
    config_digest: str,
    prior_step_sha256: str,
    intent_sha256: str,
) -> dict[str, object]:
    command = parse_paper_command_frame(command_raw)
    if (
        command.command_sequence != checkpoint.checkpoint.last_accepted_command
        or hashlib.sha256(command_raw).hexdigest()
        != checkpoint.checkpoint.last_command_frame_sha256
        or engine_version != P1_ENGINE_VERSION
        or closure_digest != P1_REAL_BACKTEST_POLICY.closure_sha256
        or checkpoint.checkpoint.closure_digest != closure_digest
        or _COMMIT.fullmatch(source_commit) is None
        or _SHA256.fullmatch(config_digest) is None
        or _SHA256.fullmatch(prior_step_sha256) is None
        or _SHA256.fullmatch(intent_sha256) is None
    ):
        raise ValueError("durable recovery step authority is invalid")
    return {
        "checkpoint": _checkpoint_document(checkpoint),
        "closure_digest": closure_digest,
        "command_base64": base64.b64encode(command_raw).decode("ascii"),
        "command_sha256": hashlib.sha256(command_raw).hexdigest(),
        "config_digest": config_digest,
        "engine_version": engine_version,
        "intent_sha256": intent_sha256,
        "prior_step_sha256": prior_step_sha256,
        "schema": _STEP_SCHEMA,
        "sequence": command.command_sequence,
        "source_commit": source_commit,
    }


def _intent_document(
    command_raw: bytes,
    *,
    expected_checkpoint_sha256: str,
    engine_version: str,
    closure_digest: str,
    source_commit: str,
    config_digest: str,
    prior_step_sha256: str,
) -> dict[str, object]:
    command = parse_paper_command_frame(command_raw)
    if (
        engine_version != P1_ENGINE_VERSION
        or closure_digest != P1_REAL_BACKTEST_POLICY.closure_sha256
        or _COMMIT.fullmatch(source_commit) is None
        or any(
            _SHA256.fullmatch(value) is None
            for value in (
                config_digest,
                expected_checkpoint_sha256,
                prior_step_sha256,
            )
        )
    ):
        raise ValueError("durable recovery intent authority is invalid")
    return {
        "closure_digest": closure_digest,
        "command_base64": base64.b64encode(command_raw).decode("ascii"),
        "command_sha256": hashlib.sha256(command_raw).hexdigest(),
        "config_digest": config_digest,
        "engine_version": engine_version,
        "expected_checkpoint_sha256": expected_checkpoint_sha256,
        "prior_step_sha256": prior_step_sha256,
        "schema": _INTENT_SCHEMA,
        "sequence": command.command_sequence,
        "source_commit": source_commit,
    }


def _decode_intent(raw: bytes, expected_sequence: int, prior: str) -> dict[str, object]:
    if not raw.endswith(b"\n"):
        raise ValueError("durable recovery intent is not canonical")
    try:
        document = json.loads(raw[:-1], object_pairs_hook=_pairs)
        command_raw = base64.b64decode(document["command_base64"], validate=True)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("durable recovery intent is invalid") from exc
    if (
        type(document) is not dict
        or set(document) != _INTENT_KEYS
        or canonical_json_bytes(document) + b"\n" != raw
        or document["schema"] != _INTENT_SCHEMA
        or document["sequence"] != expected_sequence
        or document["prior_step_sha256"] != prior
        or hashlib.sha256(command_raw).hexdigest() != document["command_sha256"]
        or document
        != _intent_document(
            command_raw,
            expected_checkpoint_sha256=document["expected_checkpoint_sha256"],
            engine_version=document["engine_version"],
            closure_digest=document["closure_digest"],
            source_commit=document["source_commit"],
            config_digest=document["config_digest"],
            prior_step_sha256=prior,
        )
    ):
        raise ValueError("durable recovery intent authority is invalid")
    return document


def _decode_step(
    raw: bytes, expected_sequence: int, prior: str, intent_sha256: str
) -> NautilusRecoveryStep:
    if not raw.endswith(b"\n"):
        raise ValueError("durable recovery step is not canonical")
    try:
        document = json.loads(raw[:-1], object_pairs_hook=_pairs)
        command_raw = base64.b64decode(document["command_base64"], validate=True)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("durable recovery step is invalid") from exc
    checkpoint = _checkpoint_from_document(document.get("checkpoint"))
    if (
        type(document) is not dict
        or set(document) != _STEP_KEYS
        or canonical_json_bytes(document) + b"\n" != raw
        or document["schema"] != _STEP_SCHEMA
        or document["sequence"] != expected_sequence
        or document["prior_step_sha256"] != prior
        or document["intent_sha256"] != intent_sha256
        or hashlib.sha256(command_raw).hexdigest() != document["command_sha256"]
        or checkpoint is None
    ):
        raise ValueError("durable recovery step authority is invalid")
    expected = _step_document(
        command_raw,
        checkpoint,
        engine_version=document["engine_version"],
        closure_digest=document["closure_digest"],
        source_commit=document["source_commit"],
        config_digest=document["config_digest"],
        prior_step_sha256=prior,
        intent_sha256=intent_sha256,
    )
    if document != expected:
        raise ValueError("durable recovery step evidence changed")
    return NautilusRecoveryStep(
        sequence=expected_sequence,
        command_raw=command_raw,
        checkpoint=checkpoint,
        engine_version=document["engine_version"],
        closure_digest=document["closure_digest"],
        source_commit=document["source_commit"],
        config_digest=document["config_digest"],
        prior_step_sha256=prior,
        step_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _normalized_step(document: dict[str, object]) -> dict[str, object]:
    normalized = json.loads(canonical_json_bytes(document))
    record = normalized["checkpoint"]
    record["checkpoint_sha256"] = "0" * 64
    record["checkpoint"]["child_identity"] = "0" * 64
    return normalized


class NautilusRecoveryStore:
    """Append-only command/checkpoint chain used to rebuild a local paper child."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("recovery store root must be a Path")
        descriptor = os.open(
            root,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            os.close(descriptor)
            raise ValueError("durable recovery directory is not private")
        self._directory_fd = descriptor
        self._replay_index: int | None = None
        self.steps()

    def __del__(self) -> None:
        descriptor = getattr(self, "_directory_fd", -1)
        self._directory_fd = -1
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def steps(self) -> tuple[NautilusRecoveryStep, ...]:
        entries = tuple(
            name
            for name in sorted(os.listdir(self._directory_fd))
            if not _TEMP_NAME.fullmatch(name)
        )
        steps: list[NautilusRecoveryStep] = []
        prior = ZERO_CHECKPOINT_SHA256
        intents = {int(match.group(1)): name for name in entries if (match := _INTENT_NAME.fullmatch(name))}
        outcomes = {int(match.group(1)): name for name in entries if (match := _STEP_NAME.fullmatch(name))}
        if len(intents) + len(outcomes) != len(entries):
            raise ValueError("durable recovery step inventory is invalid")
        for sequence in range(1, len(outcomes) + 1):
            intent_path = intents.get(sequence)
            path = outcomes.get(sequence)
            if intent_path is None or path is None:
                raise ValueError("durable recovery step inventory is invalid")
            intent_raw = _read_sealed_at(self._directory_fd, intent_path, 256 * 1024)
            intent = _decode_intent(intent_raw, sequence, prior)
            step = _decode_step(
                _read_sealed_at(self._directory_fd, path, 256 * 1024),
                sequence,
                prior,
                hashlib.sha256(intent_raw).hexdigest(),
            )
            expected_checkpoint = ZERO_CHECKPOINT_SHA256
            if steps:
                previous = steps[-1].checkpoint
                expected_checkpoint = checkpoint_sha256(
                    previous.checkpoint.model_copy(
                        update={
                            "child_identity": step.checkpoint.checkpoint.child_identity
                        }
                    )
                )
            if intent["expected_checkpoint_sha256"] != expected_checkpoint:
                raise ValueError("durable recovery intent checkpoint changed")
            steps.append(step)
            prior = step.step_sha256
        if set(intents) not in ({*outcomes}, {*outcomes, len(outcomes) + 1}):
            raise ValueError("durable recovery step inventory is invalid")
        return tuple(steps)

    def begin_replay(self) -> tuple[NautilusRecoveryStep, ...]:
        steps = self.steps()
        if sum(
            _INTENT_NAME.fullmatch(name) is not None
            for name in os.listdir(self._directory_fd)
        ) != len(steps):
            raise ValueError("durable recovery command outcome is uncertain")
        if self._replay_index is not None:
            raise RuntimeError("durable recovery replay is already active")
        self._replay_index = 0
        return steps

    @property
    def chain_sha256(self) -> str:
        steps = self.steps()
        if sum(
            _INTENT_NAME.fullmatch(name) is not None
            for name in os.listdir(self._directory_fd)
        ) != len(steps):
            raise ValueError("durable recovery command outcome is uncertain")
        return steps[-1].step_sha256 if steps else ZERO_CHECKPOINT_SHA256

    def finish_replay(self) -> None:
        if self._replay_index != len(self.steps()):
            self._replay_index = None
            raise ValueError("durable recovery command prefix is incomplete")
        self._replay_index = None

    def cancel_replay(self) -> None:
        self._replay_index = None

    def begin(
        self,
        command_raw: bytes,
        *,
        expected_checkpoint_sha256: str,
        engine_version: str,
        closure_digest: str,
        source_commit: str,
        config_digest: str,
    ) -> None:
        steps = self.steps()
        sequence = len(steps) + 1
        prior = steps[-1].step_sha256 if steps else ZERO_CHECKPOINT_SHA256
        if self._replay_index is not None:
            sequence = self._replay_index + 1
            prior = (
                steps[self._replay_index - 1].step_sha256
                if self._replay_index
                else ZERO_CHECKPOINT_SHA256
            )
        document = _intent_document(
            command_raw,
            expected_checkpoint_sha256=expected_checkpoint_sha256,
            engine_version=engine_version,
            closure_digest=closure_digest,
            source_commit=source_commit,
            config_digest=config_digest,
            prior_step_sha256=prior,
        )
        if document["sequence"] != sequence:
            raise ValueError("durable recovery intent sequence changed")
        raw = canonical_json_bytes(document) + b"\n"
        name = f"intent-{sequence:08d}.json"
        if self._replay_index is not None:
            expected = _read_sealed_at(self._directory_fd, name, 256 * 1024)
            prior_intent = json.loads(expected[:-1], object_pairs_hook=_pairs)
            prior_intent["expected_checkpoint_sha256"] = ZERO_CHECKPOINT_SHA256
            document["expected_checkpoint_sha256"] = ZERO_CHECKPOINT_SHA256
            if prior_intent != document or self._replay_index != sequence - 1:
                raise ValueError("durable recovery replay changed command intent")
            return
        _write_sealed_at(self._directory_fd, name, raw)

    def record(
        self,
        command_raw: bytes,
        checkpoint: NautilusCheckpointRecord,
        *,
        engine_version: str,
        closure_digest: str,
        source_commit: str,
        config_digest: str,
    ) -> None:
        steps = self.steps()
        sequence = len(steps) + 1
        prior = steps[-1].step_sha256 if steps else ZERO_CHECKPOINT_SHA256
        replay_index = self._replay_index
        if replay_index is not None:
            sequence = replay_index + 1
            prior = steps[replay_index - 1].step_sha256 if replay_index else ZERO_CHECKPOINT_SHA256
        document = _step_document(
            command_raw,
            checkpoint,
            engine_version=engine_version,
            closure_digest=closure_digest,
            source_commit=source_commit,
            config_digest=config_digest,
            prior_step_sha256=prior,
            intent_sha256=hashlib.sha256(
                _read_sealed_at(
                    self._directory_fd, f"intent-{sequence:08d}.json", 256 * 1024
                )
            ).hexdigest(),
        )
        if replay_index is not None:
            if replay_index >= len(steps):
                raise ValueError("durable recovery replay exceeded command prefix")
            expected_raw = _read_sealed_at(
                self._directory_fd, f"step-{sequence:08d}.json", 256 * 1024
            )
            expected = json.loads(expected_raw[:-1], object_pairs_hook=_pairs)
            if _normalized_step(document) != _normalized_step(expected):
                raise ValueError("durable recovery replay changed semantic state")
            self._replay_index = replay_index + 1
            return
        raw = canonical_json_bytes(document) + b"\n"
        _write_sealed_at(self._directory_fd, f"step-{sequence:08d}.json", raw)


def recover_nautilus_paper_session(
    store: NautilusRecoveryStore,
    session_factory: Callable[[NautilusRecoveryStore], object],
    *,
    prior_session: object,
    receipt_path: Path,
    expected_receipt_sha256: str,
) -> RecoveredNautilusPaperSession:
    from .nautilus_session import NautilusPaperSession

    if (
        type(store) is not NautilusRecoveryStore
        or not callable(session_factory)
        or type(prior_session) is not NautilusPaperSession
    ):
        raise TypeError("exact durable recovery inputs are required")
    receipt = load_nautilus_recovery_receipt(
        receipt_path, expected_receipt_sha256
    )
    evidence = receipt.evidence
    prior_child = prior_session._child
    if prior_child.is_running():
        raise ValueError("prior child is still running")
    decision = reconcile_nautilus_paper(evidence)
    terminal_steps = store.steps()
    if (
        decision.disposition is not NautilusRecoveryDisposition.RESUME_EXACT_PREFIX
        or receipt.verdict is not decision.disposition
        or receipt.command_chain_sha256 != store.chain_sha256
        or evidence.checkpoint is None
        or not terminal_steps
        or evidence.checkpoint != terminal_steps[-1].checkpoint
        or evidence.checkpoint.checkpoint.child_identity
        != prior_child.process_authority_sha256
        or evidence.session_id != prior_session._request.engine_run_id
        or evidence.closure_digest != prior_child.closure_digest
        or evidence.source_commit != prior_session._request.source_commit
        or evidence.config_digest != prior_session._request.config_digest
    ):
        raise ValueError("sealed recovery authority does not match prior session")
    steps = store.begin_replay()
    if not steps:
        store.cancel_replay()
        raise ValueError("durable recovery prefix is empty")
    last = steps[-1].checkpoint
    if (
        last.checkpoint.state is PaperSessionState.STOPPING
        and last.event_batch_sha256 is not None
    ):
        store.cancel_replay()
        raise ValueError("durable paper session is already stopped")
    session = session_factory(store)
    if type(session) is not NautilusPaperSession:
        store.cancel_replay()
        raise TypeError("recovery factory returned an invalid paper session")
    fresh_child = session._child
    if (
        fresh_child.process_authority_sha256 == prior_child.process_authority_sha256
        or fresh_child.capability_sha256 != prior_child.capability_sha256
        or fresh_child.custodian_authority_sha256
        != prior_child.custodian_authority_sha256
        or fresh_child.closure_digest != prior_child.closure_digest
        or session._request != prior_session._request
    ):
        store.cancel_replay()
        fresh_child.abort()
        raise ValueError("fresh recovery session authority does not match")
    expected = ZERO_CHECKPOINT_SHA256
    result = None
    try:
        for step in steps:
            result = session.execute(
                step.command_raw,
                expected_checkpoint_sha256=expected,
            )
            expected = result.checkpoint.checkpoint_sha256
        store.finish_replay()
    except BaseException:
        try:
            fresh_child.abort()
        finally:
            if fresh_child.is_running():
                raise RuntimeError("failed recovery child could not be terminated")
        store.cancel_replay()
        raise
    assert result is not None
    return RecoveredNautilusPaperSession(
        session=session,
        checkpoint=result.checkpoint,
        disposition=NautilusRecoveryDisposition.RESUME_EXACT_PREFIX,
    )


def _evidence_from_document(value: object) -> NautilusRecoveryEvidence:
    if type(value) is not dict or set(value) != _EVIDENCE_KEYS:
        raise ValueError("recovery evidence is invalid")
    try:
        return NautilusRecoveryEvidence(
            session_id=UUID(value["session_id"]),
            engine_version=value["engine_version"],
            expected_engine_version=value["expected_engine_version"],
            closure_digest=value["closure_digest"],
            expected_closure_digest=value["expected_closure_digest"],
            source_commit=value["source_commit"],
            expected_source_commit=value["expected_source_commit"],
            config_digest=value["config_digest"],
            expected_config_digest=value["expected_config_digest"],
            child_state=NautilusChildState(value["child_state"]),
            current_child_identity=value["current_child_identity"],
            checkpoint=_checkpoint_from_document(value["checkpoint"]),
            ledger_last_sequence=value["ledger_last_sequence"],
            ledger_last_event_digest=value["ledger_last_event_digest"],
            ledger_event_prefix_sha256=value["ledger_event_prefix_sha256"],
            portfolio_state_hash=value["portfolio_state_hash"],
            target_schedule_cursor=value["target_schedule_cursor"],
            expected_target_schedule_cursor=value["expected_target_schedule_cursor"],
            final_engine_observation_sha256=value[
                "final_engine_observation_sha256"
            ],
            child_outcome_proven=value["child_outcome_proven"],
            kill_switch_state=CanonicalKillSwitchState(value["kill_switch_state"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("recovery evidence is invalid") from exc


def _receipt(raw: bytes) -> NautilusRecoveryReceipt:
    if not raw.endswith(b"\n") or len(raw) > 256 * 1024:
        raise ValueError("recovery receipt bytes are invalid")
    try:
        document = json.loads(raw[:-1], object_pairs_hook=_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("recovery receipt is not valid JSON") from exc
    if (
        type(document) is not dict
        or set(document) != _TOP_LEVEL_KEYS
        or canonical_json_bytes(document) + b"\n" != raw
        or document["schema"] != NAUTILUS_RECOVERY_RECEIPT_SCHEMA
        or document["authority_limits"] != _AUTHORITY_LIMITS
    ):
        raise ValueError("recovery receipt authority is invalid")
    evidence = _evidence_from_document(document["evidence"])
    expected = _build_document(evidence, document["command_chain_sha256"])
    if document != expected:
        raise ValueError("recovery receipt evidence does not match verdict")
    try:
        return NautilusRecoveryReceipt(
            schema=NAUTILUS_RECOVERY_RECEIPT_SCHEMA,
            verdict=NautilusRecoveryDisposition(document["verdict"]),
            reason_codes=tuple(
                NautilusRecoveryReason(reason) for reason in document["reason_codes"]
            ),
            session_id=UUID(document["session_id"]),
            engine_version=document["engine_version"],
            closure_digest=document["closure_digest"],
            command_chain_sha256=document["command_chain_sha256"],
            source_commit=document["source_commit"],
            config_digest=document["config_digest"],
            checkpoint_sha256=document["checkpoint_sha256"],
            evidence_sha256=document["evidence_sha256"],
            receipt_sha256=hashlib.sha256(raw).hexdigest(),
            evidence=evidence,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("recovery receipt fields are invalid") from exc


def load_nautilus_recovery_receipt(
    path: Path, expected_sha256: str
) -> NautilusRecoveryReceipt:
    if not isinstance(path, Path):
        raise TypeError("recovery receipt path must be an exact Path")
    if type(expected_sha256) is not str or _SHA256.fullmatch(expected_sha256) is None:
        raise ValueError("recovery receipt expected identity is invalid")
    raw = _read_sealed(path, 256 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("recovery receipt identity changed")
    return _receipt(raw)


def write_nautilus_recovery_receipt(
    path: Path,
    evidence: NautilusRecoveryEvidence,
    *,
    command_chain_sha256: str,
) -> NautilusRecoveryReceipt:
    if not isinstance(path, Path) or type(evidence) is not NautilusRecoveryEvidence:
        raise TypeError("exact recovery receipt inputs are required")
    raw = canonical_json_bytes(
        _build_document(evidence, command_chain_sha256)
    ) + b"\n"
    _write_sealed(path.parent, path.name, raw)
    return _receipt(raw)


__all__ = [
    "NAUTILUS_RECOVERY_RECEIPT_SCHEMA",
    "NautilusRecoveryReceipt",
    "NautilusRecoveryStep",
    "NautilusRecoveryStore",
    "RecoveredNautilusPaperSession",
    "load_nautilus_recovery_receipt",
    "recover_nautilus_paper_session",
    "write_nautilus_recovery_receipt",
]
