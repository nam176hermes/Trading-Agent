from __future__ import annotations

import json
import hashlib
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.runtime_release.config import RuntimeAuthorityV2
from services.job_worker.errors import SafetyBlockedError
from services.job_worker.safety import KillSwitchState, SafetyMode
from services.job_worker.safety_state import SafetyStateClient
from services.safety_state_exporter.exporter import source_fingerprint


COMMIT = "a" * 40
NOW = datetime(2026, 7, 12, 16, 0, 3, tzinfo=UTC)


@pytest.fixture
def tmp_path():
    path = Path(tempfile.mkdtemp(prefix="phase4b-client-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _document(**changes) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "exporter_commit": COMMIT,
        "generated_at": "2026-07-12T16:00:00Z",
        "expires_at": "2026-07-12T16:00:06Z",
        "requested_mode": "PAPER",
        "effective_mode": "PAPER",
        "live_execution_enabled": False,
        "live_trading_approved": False,
        "kill_switch_state": "INACTIVE",
        "source_fingerprint": source_fingerprint(Path("/canonical/legacy")),
    }
    values.update(changes)
    return values


def _write(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    path.chmod(0o600)


def _client(path: Path, **changes) -> SafetyStateClient:
    values = {
        "expected_exporter_commit": COMMIT,
        "expected_source_fingerprint": source_fingerprint(Path("/canonical/legacy")),
        "expected_owner_uid": os.geteuid(),
        "clock": lambda: NOW,
    }
    values.update(changes)
    return SafetyStateClient(path, **values)


def test_client_accepts_only_exact_fresh_safe_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "safety-state.json"
    _write(path, _document())

    snapshot = _client(path).snapshot()

    assert snapshot.requested_mode is SafetyMode.PAPER
    assert snapshot.effective_mode is SafetyMode.PAPER
    assert snapshot.live_execution_enabled is False
    assert snapshot.live_trading_approved is False
    assert snapshot.kill_switch_state is KillSwitchState.INACTIVE
    assert snapshot.snapshot_sha256 == hashlib.sha256(
        json.dumps(_document(), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert snapshot.generated_at == datetime(2026, 7, 12, 16, 0, tzinfo=UTC)
    assert snapshot.expires_at == datetime(2026, 7, 12, 16, 0, 6, tzinfo=UTC)


def test_protected_client_uses_root_owned_safe_path_reader(monkeypatch) -> None:
    from services.job_worker import safety_state as module

    raw = json.dumps(_document(), separators=(",", ":")).encode("utf-8")
    observed: list[Path] = []

    def protected_read(path: Path) -> bytes:
        observed.append(path)
        return raw

    monkeypatch.setattr(module.runtime_config, "read_protected_file_current", protected_read)
    client = SafetyStateClient(
        Path("/run/trading-agent-v2/safety-state.json"),
        expected_exporter_commit=COMMIT,
        expected_source_fingerprint=source_fingerprint(Path("/canonical/legacy")),
        protected_root_owned=True,
        clock=lambda: NOW,
    )

    snapshot = client.snapshot()

    assert snapshot.snapshot_sha256 == hashlib.sha256(raw).hexdigest()
    assert observed == [
        Path("/run/trading-agent-v2/safety-state.json"),
        Path("/run/trading-agent-v2/safety-state.json"),
    ]


def test_v2_preflight_allows_safe_rotation_between_calls_not_during_call() -> None:
    from services.job_worker.safety_state import AuthorityBoundSafetyPreflight

    immutable_pin = ((1, 2), "a" * 64, (3, 4), "b" * 64)
    safety = SimpleNamespace(
        snapshot_path=Path("/run/trading-agent-v2/safety-state.json"),
        exporter_commit=COMMIT,
        source_fingerprint=source_fingerprint(Path("/canonical/legacy")),
    )

    def authority(dynamic_digest: str) -> RuntimeAuthorityV2:
        current = object.__new__(RuntimeAuthorityV2)
        object.__setattr__(current, "_authority_pin", immutable_pin)
        object.__setattr__(
            current, "_dynamic_evidence_pin", (dynamic_digest, "c" * 64)
        )
        object.__setattr__(current, "safety", safety)
        return current

    first_digest = "d" * 64
    second_digest = "e" * 64
    evidence = SimpleNamespace(snapshot_sha256=first_digest)
    client = SimpleNamespace(snapshot=lambda: evidence)
    pinned_authority = authority(first_digest)
    pinned = SimpleNamespace(
        runtime_authority=pinned_authority,
        authority_pin=immutable_pin,
        safety_snapshot_path=safety.snapshot_path,
        safety_exporter_commit=safety.exporter_commit,
        safety_source_fingerprint=safety.source_fingerprint,
    )
    observed = iter(
        (
            authority(first_digest),
            authority(first_digest),
            authority(second_digest),
            authority(second_digest),
            authority(second_digest),
            authority(first_digest),
        )
    )
    preflight = AuthorityBoundSafetyPreflight(
        pinned, client, authority_loader=lambda: next(observed)
    )

    assert preflight() is evidence
    evidence.snapshot_sha256 = second_digest
    assert preflight() is evidence
    with pytest.raises(SafetyBlockedError) as raised:
        preflight()

    assert raised.value.reason_code == "SAFETY_AUTHORITY_CHANGED"


def test_client_rejects_snapshot_rotation_during_validation(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "safety-state.json"
    _write(path, _document())
    client = _client(path)
    original = client._read
    reads = 0

    def rotating_read():
        nonlocal reads
        reads += 1
        raw = original()
        if reads == 1:
            _write(
                path,
                _document(
                    generated_at="2026-07-12T16:00:01Z",
                    expires_at="2026-07-12T16:00:07Z",
                ),
            )
        return raw

    monkeypatch.setattr(client, "_read", rotating_read)

    with pytest.raises(SafetyBlockedError) as raised:
        client.snapshot()

    assert raised.value.reason_code == "SAFETY_STATE_CHANGED"


def test_evidence_reader_authenticates_current_unsafe_state_without_applying_worker_policy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "safety-state.json"
    _write(path, _document(kill_switch_state="ACTIVE"))

    snapshot = _client(path).evidence()

    assert snapshot.requested_mode is SafetyMode.PAPER
    assert snapshot.kill_switch_state is KillSwitchState.ACTIVE
    with pytest.raises(SafetyBlockedError) as raised:
        _client(path).snapshot()
    assert raised.value.reason_code == "SAFETY_KILL_SWITCH_ACTIVE"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"exporter_commit": "b" * 40}, "SAFETY_STATE_EXPORTER_COMMIT_MISMATCH"),
        ({"source_fingerprint": "b" * 64}, "SAFETY_STATE_SOURCE_MISMATCH"),
        ({"generated_at": "2026-07-12T16:00:04Z", "expires_at": "2026-07-12T16:00:10Z"}, "SAFETY_STATE_FROM_FUTURE"),
        ({"generated_at": "2026-07-12T15:59:50Z", "expires_at": "2026-07-12T15:59:56Z"}, "SAFETY_STATE_STALE"),
        ({"expires_at": "2026-07-12T16:00:07Z"}, "SAFETY_STATE_WINDOW_INVALID"),
        ({"requested_mode": "LIVE", "effective_mode": "PAPER"}, "SAFETY_REQUESTED_MODE_NOT_PAPER"),
        ({"kill_switch_state": "ACTIVE"}, "SAFETY_KILL_SWITCH_ACTIVE"),
    ],
)
def test_client_rejects_stale_mismatched_or_non_safe_snapshots(
    tmp_path: Path, changes: dict[str, object], reason: str,
) -> None:
    path = tmp_path / "safety-state.json"
    _write(path, _document(**changes))

    with pytest.raises(SafetyBlockedError) as raised:
        _client(path).snapshot()

    assert raised.value.reason_code == reason


@pytest.mark.parametrize(
    "document",
    [
        {},
        _document(extra="forbidden"),
        _document(schema_version=True),
        _document(live_execution_enabled=0),
        _document(generated_at="not-a-time"),
        _document(exporter_commit="A" * 40),
    ],
)
def test_client_rejects_invalid_or_non_strict_schema(tmp_path: Path, document: object) -> None:
    path = tmp_path / "safety-state.json"
    _write(path, document)

    with pytest.raises(SafetyBlockedError) as raised:
        _client(path).snapshot()

    assert raised.value.reason_code == "SAFETY_STATE_INVALID"


def test_client_rejects_missing_symlink_unsafe_mode_and_unsafe_owner(tmp_path: Path) -> None:
    path = tmp_path / "safety-state.json"
    with pytest.raises(SafetyBlockedError) as missing:
        _client(path).snapshot()
    assert missing.value.reason_code == "SAFETY_STATE_MISSING"

    target = tmp_path / "target.json"
    _write(target, _document())
    path.symlink_to(target)
    with pytest.raises(SafetyBlockedError) as symlink:
        _client(path).snapshot()
    assert symlink.value.reason_code == "SAFETY_STATE_INVALID"
    path.unlink()

    _write(path, _document())
    path.chmod(0o640)
    with pytest.raises(SafetyBlockedError) as mode:
        _client(path).snapshot()
    assert mode.value.reason_code == "SAFETY_STATE_MODE_UNSAFE"
    path.chmod(0o600)

    with pytest.raises(SafetyBlockedError) as owner:
        _client(path, expected_owner_uid=os.geteuid() + 1).snapshot()
    assert owner.value.reason_code == "SAFETY_STATE_OWNER_UNSAFE"


def test_client_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "safety-state.json"
    raw = json.dumps(_document(), separators=(",", ":"))
    path.write_text(raw[:-1] + ',"schema_version":1}', encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(SafetyBlockedError) as raised:
        _client(path).snapshot()

    assert raised.value.reason_code == "SAFETY_STATE_INVALID"
