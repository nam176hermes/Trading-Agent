from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
RUNTIME_PARENT = ROOT / "engines/nautilus"
sys.path.insert(0, str(RUNTIME_PARENT))

from runtime_v1 import input_loader  # noqa: E402
from runtime_v1.input_loader import InputLoadError, load_inputs  # noqa: E402


FIXTURES = ROOT / "tests/fixtures/p1_nautilus/contracts"
ARTIFACTS = {
    "engine_configuration": ("engine_configuration", "engine-configuration.json"),
    "instrument_catalog": ("instrument_catalog", "instrument-catalog.json"),
    "strategy_configuration": ("target_schedule", "target-schedule.json"),
    "market_data": ("market_data_manifest", "market-data-manifest.json"),
}


def canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    artifact_overrides: dict[str, bytes] | None = None,
    request_update: dict[str, object] | None = None,
) -> tuple[Path, dict[str, bytes], dict[str, object]]:
    artifacts = {
        name: (FIXTURES / filename).read_bytes()
        for name, (_, filename) in ARTIFACTS.items()
    }
    artifacts.update(artifact_overrides or {})
    catalog_digest = hashlib.sha256(artifacts["instrument_catalog"]).hexdigest()
    manifest = json.loads(artifacts["market_data"])
    manifest["catalog_sha256"] = catalog_digest
    artifacts["market_data"] = canonical(manifest) + b"\n"

    references: dict[str, dict[str, str]] = {}
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True)
    for index, (name, raw) in enumerate(artifacts.items(), start=1):
        digest = hashlib.sha256(raw).hexdigest()
        reference = {
            "artifact_id": f"{index}" * 8 + "-1111-4111-8111-111111111111",
            "media_type": "application/json",
            "sha256": digest,
        }
        references[name] = reference
        path = artifact_root / f"{name}-{digest}.json"
        path.write_bytes(raw)
        path.chmod(0o400)

    payload: dict[str, object] = {
        "command_type": "RunBacktest",
        **references,
        "start_time": manifest["first_timestamp"],
        "end_time": manifest["last_timestamp"],
    }
    request: dict[str, object] = {
        "causation_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "config_digest": hashlib.sha256(
            canonical(
                {
                    name: references[name]
                    for name in (
                        "engine_configuration",
                        "instrument_catalog",
                        "strategy_configuration",
                    )
                }
            )
        ).hexdigest(),
        "correlation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "engine_run_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "event_time": manifest["first_timestamp"],
        "initialization_time": manifest["first_timestamp"],
        "message_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "payload": payload,
        "payload_digest": hashlib.sha256(canonical(payload)).hexdigest(),
        "producer_identity": "worker-authority-1",
        "schema_version": "1.0.0",
        "source_commit": "0123456789abcdef0123456789abcdef01234567",
        "stream_sequence": 1,
    }
    request.update(request_update or {})
    raw_request = canonical(request)
    request_path = tmp_path / "request.json"
    request_path.write_bytes(raw_request)
    request_path.chmod(0o400)
    sidecar_path = tmp_path / "request.sha256"
    sidecar_path.write_bytes(hashlib.sha256(raw_request).hexdigest().encode() + b"\n")
    sidecar_path.chmod(0o400)
    monkeypatch.setattr(input_loader, "REQUEST", str(request_path))
    monkeypatch.setattr(input_loader, "SIDECAR", str(sidecar_path))
    monkeypatch.setattr(input_loader, "ARTIFACT_ROOT", str(artifact_root))
    return artifact_root, artifacts, request


def test_loads_exact_request_and_four_immutable_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, request = sandbox(tmp_path, monkeypatch)

    first = load_inputs()
    second = load_inputs()

    assert first == second
    assert first.request.message_id == request["message_id"]
    assert first.request.command_type == "RunBacktest"
    assert first.market_data_manifest[0][0] == "catalog_sha256"
    with pytest.raises(FrozenInstanceError):
        first.request.message_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.instrument_catalog[0] = ("symbol", "ETHUSDT")  # type: ignore[index]


@pytest.mark.parametrize(
    "mutation",
    (
        {"schema_version": "2.0.0"},
        {"stream_sequence": 0},
        {"producer_identity": "worker authority"},
        {"source_commit": "A" * 40},
        {"payload_digest": "0" * 64},
        {"unknown": True},
    ),
)
def test_rejects_nonexact_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: dict[str, object]
) -> None:
    sandbox(tmp_path, monkeypatch, request_update=mutation)

    with pytest.raises(InputLoadError):
        load_inputs()


def test_rejects_command_window_or_catalog_binding_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, request = sandbox(tmp_path, monkeypatch)
    payload = dict(request["payload"])  # type: ignore[arg-type]
    payload["end_time"] = "2026-08-05T12:02:00Z"
    request["payload"] = payload
    request["payload_digest"] = hashlib.sha256(canonical(payload)).hexdigest()
    raw = canonical(request)
    Path(input_loader.REQUEST).chmod(0o600)
    Path(input_loader.REQUEST).write_bytes(raw)
    Path(input_loader.REQUEST).chmod(0o400)
    Path(input_loader.SIDECAR).chmod(0o600)
    Path(input_loader.SIDECAR).write_bytes(hashlib.sha256(raw).hexdigest().encode() + b"\n")
    Path(input_loader.SIDECAR).chmod(0o400)

    with pytest.raises(InputLoadError, match="window"):
        load_inputs()
