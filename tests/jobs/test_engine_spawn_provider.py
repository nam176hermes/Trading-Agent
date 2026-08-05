from __future__ import annotations

import hashlib
import gc
import os
import shutil
import tempfile
import weakref
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

import pytest

from packages.engine_contracts import (
    ArtifactReference,
    CURRENT_SCHEMA_VERSION,
    EngineCommandEnvelope,
    RunBacktest,
    canonical_json_bytes,
    payload_digest,
)
from services.job_worker.engine_spawn import (
    CompleteEngineClosureAttestation,
    EngineSpawnError,
    EngineSpawnProvider,
    OsSandboxProof,
    ReadOnlyClosureMount,
    consume_prepared_engine_spawn,
)


SOURCE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
SANDBOX_PROFILE_SHA256 = (
    "21d08b1c58f18318008495604ab2eac04885805c638029483c93a160ce146a8a"
)


@pytest.fixture
def secure_tmp_path() -> Path:
    path = Path(tempfile.mkdtemp(prefix="engine-spawn-test-", dir="/tmp"))
    try:
        yield path
    finally:
        for directory, child_directories, files in os.walk(path):
            Path(directory).chmod(0o700)
            for child in child_directories:
                candidate = Path(directory) / child
                if not candidate.is_symlink():
                    candidate.chmod(0o700)
            for child in files:
                candidate = Path(directory) / child
                if not candidate.is_symlink():
                    candidate.chmod(0o600)
        shutil.rmtree(path)


def _envelope() -> EngineCommandEnvelope:
    artifact = ArtifactReference(
        artifact_id=UUID("11111111-1111-4111-8111-111111111111"),
        sha256="1" * 64,
        media_type="application/json",
    )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=artifact,
        instrument_catalog=artifact,
        strategy_configuration=artifact,
        market_data=ArtifactReference(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            sha256="4" * 64,
            media_type="application/jsonl",
        ),
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    return EngineCommandEnvelope(
        message_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        correlation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        causation_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        engine_run_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        stream_sequence=1,
        event_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        initialization_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        schema_version=CURRENT_SCHEMA_VERSION,
        producer_identity="worker-authority-1",
        source_commit=SOURCE_COMMIT,
        config_digest=payload_digest(
            {
                "engine_configuration": artifact,
                "instrument_catalog": artifact,
                "strategy_configuration": artifact,
            }
        ),
        payload_digest=payload_digest(command),
        payload=command,
    )


def _identity(path: Path) -> tuple[int, int]:
    observed = path.stat(follow_symlinks=False)
    return observed.st_dev, observed.st_ino


def _closure(tmp_path: Path) -> CompleteEngineClosureAttestation:
    sandbox = tmp_path / "sealed" / "bin" / "sandbox"
    sandbox.parent.mkdir(parents=True)
    sandbox.write_bytes(b"reviewed-os-sandbox-v1")
    sandbox.chmod(0o500)
    release = tmp_path / "sealed" / "engine-release"
    release.mkdir(mode=0o700)
    entrypoint = release / "bin" / "engine"
    entrypoint.parent.mkdir(mode=0o700)
    entrypoint.write_bytes(b"reviewed-engine-entrypoint-v1")
    entrypoint.chmod(0o500)
    entrypoint.parent.chmod(0o500)
    release.chmod(0o500)
    return CompleteEngineClosureAttestation(
        source_commit=SOURCE_COMMIT,
        closure_sha256="a" * 64,
        mounts=(
            ReadOnlyClosureMount(
                source=release,
                target=PurePosixPath("/engine"),
                identity=_identity(release),
            ),
        ),
        entrypoint=PurePosixPath("/engine/bin/engine"),
        argv_prefix=("run-backtest",),
        timeout_seconds=10,
        result_validator_id="engine-event-v1",
        sandbox=OsSandboxProof(
            executable=sandbox,
            identity=_identity(sandbox),
            executable_sha256=hashlib.sha256(sandbox.read_bytes()).hexdigest(),
            profile_sha256=SANDBOX_PROFILE_SHA256,
        ),
    )


def _provider(
    tmp_path: Path,
    attestor,
) -> EngineSpawnProvider:
    transport = tmp_path / "transport"
    transport.mkdir(mode=0o700, exist_ok=True)
    return EngineSpawnProvider(
        transport_root=transport,
        attest_closure=attestor,
        monotonic_ns=lambda: 1_000_000_000,
    )


def _close_spawn_fds(spawn) -> None:
    for descriptor in spawn.close_after_spawn_fds:
        os.close(descriptor)


def test_provider_seals_inputs_and_builds_only_a_sandboxed_fd_bound_launch(
    secure_tmp_path: Path,
) -> None:
    tmp_path = secure_tmp_path
    closure = _closure(tmp_path)
    envelope = _envelope()
    provider = _provider(tmp_path, lambda: closure)

    prepared = provider.prepare(envelope)
    spawn = consume_prepared_engine_spawn(prepared)
    try:
        request_fd, sidecar_fd = spawn.pass_fds
        request_bytes = os.pread(request_fd, 1_000_000, 0)
        sidecar_bytes = os.pread(sidecar_fd, 1_000_000, 0)
        assert request_bytes == canonical_json_bytes(envelope)
        assert sidecar_bytes == hashlib.sha256(request_bytes).hexdigest().encode() + b"\n"
        assert spawn.argv == (
            str(closure.sandbox.executable),
            "--die-with-parent",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--new-session",
            "--clearenv",
            "--dir",
            "/inputs",
            "--ro-bind",
            str(closure.mounts[0].source),
            "/engine",
            "--ro-bind",
            f"/proc/self/fd/{request_fd}",
            "/inputs/request.json",
            "--ro-bind",
            f"/proc/self/fd/{sidecar_fd}",
            "/inputs/request.sha256",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--chdir",
            "/",
            "/engine/bin/engine",
            "run-backtest",
            "/inputs/request.json",
            "/inputs/request.sha256",
        )
        assert dict(spawn.environment) == {}
        assert spawn.cwd == Path("/")
        assert spawn.timeout_seconds == 10
        assert spawn.result_validator_id == "engine-event-v1"
        assert spawn.source_revision == SOURCE_COMMIT

        joined = "\n".join((*spawn.argv, *spawn.environment.keys()))
        assert "JOB_API" not in joined
        assert "DATABASE" not in joined
        assert str(Path(__file__).resolve().parents[2]) not in joined
    finally:
        _close_spawn_fds(spawn)

    run_root = tmp_path / "transport" / f"run-{envelope.engine_run_id.hex}"
    assert (run_root / "request.json").stat().st_mode & 0o777 == 0o400
    assert (run_root / "request.sha256").stat().st_mode & 0o777 == 0o400


@pytest.mark.parametrize("unavailable", (None, object()))
def test_provider_fails_closed_without_typed_complete_closure_and_sandbox_proof(
    secure_tmp_path: Path, unavailable: object
) -> None:
    tmp_path = secure_tmp_path
    provider = _provider(tmp_path, lambda: unavailable)

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_UNAVAILABLE"):
        provider.prepare(_envelope())


@pytest.mark.parametrize("artifact_kind", ("directory", "file", "symlink"))
def test_provider_rejects_every_preexisting_or_symlinked_transport_run(
    secure_tmp_path: Path, artifact_kind: str
) -> None:
    tmp_path = secure_tmp_path
    envelope = _envelope()
    closure = _closure(tmp_path)
    transport = tmp_path / "transport"
    transport.mkdir(mode=0o700)
    run_root = transport / f"run-{envelope.engine_run_id.hex}"
    if artifact_kind == "directory":
        run_root.mkdir()
    elif artifact_kind == "file":
        run_root.write_text("stale", encoding="utf-8")
    else:
        run_root.symlink_to(tmp_path / "attacker")
    provider = EngineSpawnProvider(
        transport_root=transport,
        attest_closure=lambda: closure,
        monotonic_ns=lambda: 1_000_000_000,
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_TRANSPORT_PREEXISTING"):
        provider.prepare(envelope)


def test_prepared_engine_spawn_is_opaque_and_consumed_exactly_once(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path)
    prepared = _provider(secure_tmp_path, lambda: closure).prepare(_envelope())

    assert repr(prepared) == "PreparedEngineSpawn(validated=True)"
    spawn = consume_prepared_engine_spawn(prepared)
    _close_spawn_fds(spawn)

    with pytest.raises(EngineSpawnError, match="ENGINE_PREPARED_SPAWN_INVALID"):
        consume_prepared_engine_spawn(prepared)


def test_abandoned_prepared_authority_closes_all_retained_descriptors(
    secure_tmp_path: Path,
) -> None:
    before = {int(entry.name) for entry in Path("/proc/self/fd").iterdir()}
    closure = _closure(secure_tmp_path)
    prepared = _provider(secure_tmp_path, lambda: closure).prepare(_envelope())
    retained = {
        int(entry.name) for entry in Path("/proc/self/fd").iterdir()
    } - before
    reference = weakref.ref(prepared)
    assert len(retained) >= 4

    del prepared
    gc.collect()

    assert reference() is None
    for descriptor in retained:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_closure_rotation_after_prepare_rejects_before_launch_is_revealed(
    secure_tmp_path: Path,
) -> None:
    original = _closure(secure_tmp_path)
    rotated = replace(original, closure_sha256="c" * 64)
    observed = iter((original, rotated))
    prepared = _provider(secure_tmp_path, lambda: next(observed)).prepare(_envelope())

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_STALE"):
        consume_prepared_engine_spawn(prepared)


@pytest.mark.parametrize("mutation", ("replace", "rewrite"))
def test_swapped_or_modified_request_is_rejected_at_consumption(
    secure_tmp_path: Path, mutation: str
) -> None:
    envelope = _envelope()
    closure = _closure(secure_tmp_path)
    prepared = _provider(secure_tmp_path, lambda: closure).prepare(envelope)
    run_root = (
        secure_tmp_path / "transport" / f"run-{envelope.engine_run_id.hex}"
    )
    request = run_root / "request.json"
    request.chmod(0o600)
    if mutation == "replace":
        request.rename(run_root / "request.original")
        request.write_bytes(b"{}")
    else:
        request.write_bytes(b"{}")
    request.chmod(0o400)

    with pytest.raises(EngineSpawnError, match="ENGINE_INPUT_STALE"):
        consume_prepared_engine_spawn(prepared)


def test_sandbox_proof_must_still_be_available_at_consumption(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path)
    observed = iter((closure, None))
    prepared = _provider(secure_tmp_path, lambda: next(observed)).prepare(_envelope())

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_UNAVAILABLE"):
        consume_prepared_engine_spawn(prepared)


def test_sandbox_or_mounted_release_identity_swap_is_rejected_at_consumption(
    secure_tmp_path: Path,
) -> None:
    for swapped_path in ("sandbox", "release"):
        case_root = secure_tmp_path / swapped_path
        case_root.mkdir(mode=0o700)
        closure = _closure(case_root)
        prepared = _provider(case_root, lambda: closure).prepare(_envelope())
        target = (
            closure.sandbox.executable
            if swapped_path == "sandbox"
            else closure.mounts[0].source
        )
        original = target.with_name(target.name + ".original")
        target.rename(original)
        if swapped_path == "sandbox":
            target.write_bytes(b"replacement-sandbox")
            target.chmod(0o500)
            reason = "ENGINE_SANDBOX_PROOF_INVALID"
        else:
            target.mkdir(mode=0o500)
            reason = "ENGINE_CLOSURE_STALE"

        with pytest.raises(EngineSpawnError, match=reason):
            consume_prepared_engine_spawn(prepared)


def test_typed_closure_without_a_typed_os_sandbox_proof_is_rejected(
    secure_tmp_path: Path,
) -> None:
    incomplete = replace(_closure(secure_tmp_path), sandbox=None)  # type: ignore[arg-type]

    with pytest.raises(EngineSpawnError, match="ENGINE_SANDBOX_PROOF_UNAVAILABLE"):
        _provider(secure_tmp_path, lambda: incomplete).prepare(_envelope())


def test_unreviewed_os_sandbox_profile_proof_is_rejected(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path)
    unreviewed = replace(
        closure,
        sandbox=replace(closure.sandbox, profile_sha256="b" * 64),
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_SANDBOX_PROOF_INVALID"):
        _provider(secure_tmp_path, lambda: unreviewed).prepare(_envelope())


def test_closure_mounts_cannot_shadow_sandbox_owned_targets(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path)
    shadow = replace(closure.mounts[0], target=PurePosixPath("/proc/engine-shadow"))
    unsafe = replace(closure, mounts=(*closure.mounts, shadow))

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(secure_tmp_path, lambda: unsafe).prepare(_envelope())


@pytest.mark.parametrize(
    "malformed",
    (
        {"source_commit": None},
        {"closure_sha256": None},
        {"timeout_seconds": "10"},
        {"result_validator_id": None},
    ),
)
def test_malformed_typed_closure_fields_fail_with_a_closed_refusal(
    secure_tmp_path: Path, malformed: dict[str, object]
) -> None:
    closure = replace(_closure(secure_tmp_path), **malformed)  # type: ignore[arg-type]

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(secure_tmp_path, lambda: closure).prepare(_envelope())


def test_symlinked_transport_root_is_rejected_without_ambient_fallback(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path)
    real_transport = secure_tmp_path / "real-transport"
    real_transport.mkdir(mode=0o700)
    transport = secure_tmp_path / "transport"
    transport.symlink_to(real_transport)
    provider = EngineSpawnProvider(
        transport_root=transport,
        attest_closure=lambda: closure,
        monotonic_ns=lambda: 1_000_000_000,
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_TRANSPORT_UNSAFE"):
        provider.prepare(_envelope())

    assert list(real_transport.iterdir()) == []
