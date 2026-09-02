from __future__ import annotations

import hashlib
import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.operator_control.contracts import (
    OperatorActorV1,
    OperatorSafetyEvidenceV1,
    SetKillSwitchV1,
    SetRequestedModeV1,
    SubmitOperatorCommandV1,
)
from packages.operator_control.hashing import evidence_sha256
from packages.operator_control.policy import OperatorCommandRejected
from services.job_worker.errors import SafetyBlockedError
from services.job_worker.safety import KillSwitchState, SafetyMode, SafetySnapshot
from services.job_worker.safety_state import SafetyEvidence
from services.operator_control.composition import (
    OperatorControlRuntimeSettings,
    build_production_operator_control_service,
)
from services.operator_control.journal import CommandJournal
from services.operator_control.safety_adapter import normalize_operator_safety_evidence
from services.operator_control.service import OperatorControlService
from services.operator_control import service as service_module
from services.operator_control.state_store import OperatorStateStore, RecoveryError
from tests.hwc.fixtures.operator_state import provision_operator_state, write_private


NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def actor(interface: str = "CLI") -> OperatorActorV1:
    return OperatorActorV1(
        schema_version="operator-actor-v1",
        principal_id=f"operator.{interface.lower()}",
        interface=interface,
    )


def request(
    command: SetRequestedModeV1 | SetKillSwitchV1,
    *,
    idempotency_key: str = "idem.1",
    expected_state_sha256: str | None = None,
) -> SubmitOperatorCommandV1:
    return SubmitOperatorCommandV1(
        schema_version="submit-operator-command-v1",
        command_id="cmd_0123456789abcdef0123456789abcdef",
        idempotency_key=idempotency_key,
        correlation_id="corr.1",
        expected_state_sha256=expected_state_sha256,
        command=command,
    )


def safety(
    *, observed_at: datetime = NOW, source_fingerprint: str = "d" * 64
) -> OperatorSafetyEvidenceV1:
    payload = {
        "schema_version": "operator-safety-evidence-v1",
        "requested_mode": "PAPER",
        "effective_mode": "PAPER",
        "live_execution_enabled": False,
        "live_trading_approved": False,
        "kill_switch_state": "ACTIVE",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "source_fingerprint": source_fingerprint,
    }
    return OperatorSafetyEvidenceV1.model_validate(
        {**payload, "evidence_sha256": evidence_sha256(payload)}
    )


def service_for(paths, **changes) -> OperatorControlService:
    values = {
        "state_store": OperatorStateStore(paths),
        "journal": CommandJournal(paths),
        "safety_provider": lambda: safety(),
        "clock": lambda: NOW,
    }
    values.update(changes)
    return OperatorControlService(**values)


def mode_request(**changes) -> SubmitOperatorCommandV1:
    return request(
        SetRequestedModeV1(
            command_type="SET_REQUESTED_MODE", desired_mode="PAPER"
        ),
        **changes,
    )


def test_cli_reads_state_and_web_cannot_read_or_set_mode(tmp_path: Path) -> None:
    paths = provision_operator_state(tmp_path)
    service = service_for(paths)
    assert service.read_state(actor()).requested_mode == "UNKNOWN"
    with pytest.raises(OperatorCommandRejected, match="CAPABILITY_FORBIDDEN"):
        service.read_state(actor("WEB"))
    with pytest.raises(OperatorCommandRejected, match="CAPABILITY_FORBIDDEN"):
        service.execute(actor("WEB"), mode_request())


def test_mode_activation_and_clear_create_final_receipts(tmp_path: Path) -> None:
    (tmp_path / "mode").mkdir()
    mode_paths = provision_operator_state(tmp_path / "mode")
    mode_result = service_for(mode_paths).execute(actor(), mode_request())
    assert mode_result.deduplicated is False
    assert mode_result.receipt.outcome == "APPLIED"
    assert mode_paths.mode_path.read_bytes() == b"paper\n"

    no_change = service_for(mode_paths).execute(
        actor(), mode_request(idempotency_key="idem.mode.no-change")
    )
    assert no_change.receipt.outcome == "NO_CHANGE"

    (tmp_path / "kill").mkdir()
    kill_paths = provision_operator_state(tmp_path / "kill")
    activation = request(
        SetKillSwitchV1(
            command_type="SET_KILL_SWITCH", desired_state="ACTIVE", reason="drill"
        )
    )
    activated = service_for(kill_paths).execute(actor("WEB"), activation)
    assert activated.receipt.outcome_code == "KILL_SWITCH_ACTIVATED"
    assert kill_paths.kill_switch_path.read_bytes() == (
        b"2026-09-02T12:00:00Z: drill\n"
    )
    receipt_path = (
        kill_paths.command_root
        / "receipts"
        / f"{hashlib.sha256(b'idem.1').hexdigest()}.json"
    )
    assert b"drill" not in receipt_path.read_bytes()

    active = OperatorStateStore(kill_paths).read_state()
    clear = request(
        SetKillSwitchV1(
            command_type="SET_KILL_SWITCH", desired_state="INACTIVE", reason=None
        ),
        idempotency_key="idem.clear",
        expected_state_sha256=active.state_sha256,
    )
    with pytest.raises(OperatorCommandRejected, match="CAPABILITY_FORBIDDEN"):
        service_for(kill_paths).execute(actor("WEB"), clear)
    cleared = service_for(kill_paths).execute(actor(), clear)
    assert cleared.receipt.outcome_code == "KILL_SWITCH_CLEARED"
    assert OperatorStateStore(kill_paths).read_state().kill_switch_state == "INACTIVE"


def test_idempotency_replays_exact_receipt_and_rejects_conflicts(
    tmp_path: Path,
) -> None:
    paths = provision_operator_state(tmp_path)
    service = service_for(paths)
    first = service.execute(actor(), mode_request())
    repeated = service.execute(actor(), mode_request())
    assert repeated.deduplicated is True
    assert repeated.receipt == first.receipt

    conflict = request(
        SetKillSwitchV1(
            command_type="SET_KILL_SWITCH", desired_state="ACTIVE", reason="different"
        )
    )
    with pytest.raises(OperatorCommandRejected, match="IDEMPOTENCY_CONFLICT"):
        service.execute(actor(), conflict)


def test_concurrent_same_request_creates_one_receipt(tmp_path: Path) -> None:
    paths = provision_operator_state(tmp_path)
    service = service_for(paths)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.execute(actor(), mode_request()), range(2)))
    assert sorted(result.deduplicated for result in results) == [False, True]
    assert results[0].receipt == results[1].receipt
    assert len(list((paths.command_root / "receipts").iterdir())) == 1


def test_clear_rejects_safety_drift_after_durable_intent(tmp_path: Path) -> None:
    paths = provision_operator_state(tmp_path)
    write_private(paths.mode_path, b"paper\n")
    write_private(paths.kill_switch_path, b"2026-09-02T11:59:59Z: incident\n")
    current = OperatorStateStore(paths).read_state()
    clear = request(
        SetKillSwitchV1(
            command_type="SET_KILL_SWITCH", desired_state="INACTIVE", reason=None
        ),
        expected_state_sha256=current.state_sha256,
    )
    observed = iter((safety(), safety(observed_at=NOW - timedelta(seconds=1))))
    service = service_for(paths, safety_provider=lambda: next(observed))
    with pytest.raises(OperatorCommandRejected, match="SAFETY_EVIDENCE_CHANGED"):
        service.execute(actor(), clear)
    assert paths.kill_switch_path.exists()
    snapshot = CommandJournal(paths).load(hashlib.sha256(b"idem.1").hexdigest())
    assert snapshot.intent is not None and snapshot.applied is None


def test_clear_retry_rejects_safety_that_expired_after_durable_intent(
    tmp_path: Path,
) -> None:
    paths = provision_operator_state(tmp_path)
    write_private(paths.mode_path, b"paper\n")
    write_private(paths.kill_switch_path, b"2026-09-02T11:59:59Z: incident\n")
    current = OperatorStateStore(paths).read_state()
    clear = request(
        SetKillSwitchV1(
            command_type="SET_KILL_SWITCH", desired_state="INACTIVE", reason=None
        ),
        expected_state_sha256=current.state_sha256,
    )

    def crash(name: str) -> None:
        if name == "AFTER_INTENT_FSYNC":
            raise RuntimeError("synthetic crash")

    interrupted = service_for(paths, journal=CommandJournal(paths, failpoint=crash))
    with pytest.raises(RuntimeError, match="synthetic crash"):
        interrupted.execute(actor(), clear)

    expired = service_for(paths, clock=lambda: NOW + timedelta(seconds=6))
    with pytest.raises(OperatorCommandRejected, match="KILL_SWITCH_CLEAR_UNSAFE"):
        expired.execute(actor(), clear)
    assert paths.kill_switch_path.exists()


def test_retry_recovers_mutation_interrupted_before_applied_record(
    tmp_path: Path,
) -> None:
    paths = provision_operator_state(tmp_path)

    def crash(name: str) -> None:
        if name == "AFTER_STATE_APPLY":
            raise RuntimeError("synthetic crash")

    interrupted = service_for(paths, state_store=OperatorStateStore(paths, failpoint=crash))
    with pytest.raises(RuntimeError, match="synthetic crash"):
        interrupted.execute(actor(), mode_request())

    recovered = service_for(paths).execute(actor(), mode_request())
    assert recovered.deduplicated is True
    assert recovered.receipt.outcome == "RECOVERED_APPLIED"
    assert recovered.receipt.outcome_code == "MODE_SET_PAPER"


@pytest.mark.parametrize("failpoint", ("AFTER_APPLIED_FSYNC", "AFTER_RECEIPT_FSYNC"))
def test_retry_finalizes_or_returns_durable_receipt(
    tmp_path: Path, failpoint: str
) -> None:
    paths = provision_operator_state(tmp_path)

    def crash(name: str) -> None:
        if name == failpoint:
            raise RuntimeError("synthetic crash")

    interrupted = service_for(paths, journal=CommandJournal(paths, failpoint=crash))
    with pytest.raises(RuntimeError, match="synthetic crash"):
        interrupted.execute(actor(), mode_request())

    recovered = service_for(paths).execute(actor(), mode_request())
    assert recovered.deduplicated is True
    assert recovered.receipt.outcome == "APPLIED"


def test_retry_preserves_unknown_outcome_after_external_state_drift(
    tmp_path: Path,
) -> None:
    paths = provision_operator_state(tmp_path)

    def crash(name: str) -> None:
        if name == "AFTER_INTENT_FSYNC":
            raise RuntimeError("synthetic crash")

    interrupted = service_for(paths, journal=CommandJournal(paths, failpoint=crash))
    with pytest.raises(RuntimeError, match="synthetic crash"):
        interrupted.execute(actor(), mode_request())
    write_private(paths.mode_path, b"live\n")

    with pytest.raises(RecoveryError, match="COMMAND_OUTCOME_UNKNOWN"):
        service_for(paths).execute(actor(), mode_request())


def test_safety_adapter_requires_protected_evidence_and_normalizes_digest() -> None:
    exported = SafetyEvidence(
        requested_mode=SafetyMode.PAPER,
        effective_mode=SafetyMode.PAPER,
        live_execution_enabled=False,
        live_trading_approved=False,
        kill_switch_state=KillSwitchState.ACTIVE,
        snapshot_sha256="a" * 64,
        generated_at=NOW,
        expires_at=NOW + timedelta(seconds=5),
    )
    normalized = normalize_operator_safety_evidence(
        exported, source_fingerprint="d" * 64
    )
    assert normalized.observed_at == NOW
    assert normalized.evidence_sha256 == evidence_sha256(
        normalized.model_dump(mode="json")
    )

    unprotected = SafetySnapshot(
        SafetyMode.PAPER,
        SafetyMode.PAPER,
        False,
        False,
        KillSwitchState.ACTIVE,
    )
    with pytest.raises(SafetyBlockedError, match="protected safety evidence"):
        normalize_operator_safety_evidence(
            unprotected, source_fingerprint="d" * 64  # type: ignore[arg-type]
        )


def test_production_composition_rejects_noncanonical_root(tmp_path: Path) -> None:
    settings = OperatorControlRuntimeSettings(data_root=tmp_path)
    with pytest.raises(ValueError, match="canonical"):
        build_production_operator_control_service(settings)


def test_command_service_has_no_runtime_database_or_broker_imports() -> None:
    source = inspect.getsource(service_module)
    for forbidden in ("runtime_release", "psycopg", "trading_control.db", "broker"):
        assert forbidden not in source
