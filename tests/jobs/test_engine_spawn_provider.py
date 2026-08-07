from __future__ import annotations

import fcntl
import gc
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
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
    RunBacktestSimulation,
    canonical_json_bytes,
    payload_digest,
)
from services.job_worker.engine_spawn import (
    CompleteEngineClosureAttestation,
    HashBoundEngineInput,
    EngineSpawnError,
    EngineSpawnProvider,
    OsSandboxProof,
    ReadOnlyClosureMount,
    consume_prepared_engine_spawn,
)


SOURCE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
SANDBOX_PROFILE_SHA256 = (
    "742d3d2cf313a0dc5832fd88d277da1d00e07c6e4abcc4ca51bf0ebcd7c3936e"
)
F_GET_SEALS = 1034
F_SEAL_SEAL = 0x0001
F_SEAL_SHRINK = 0x0002
F_SEAL_GROW = 0x0004
F_SEAL_WRITE = 0x0008


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


def _simulation_envelope(
    references: tuple[ArtifactReference, ...],
) -> EngineCommandEnvelope:
    command = RunBacktestSimulation(
        command_type="RunBacktestSimulation",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        simulation_scenario=references[4],
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    return _envelope().model_copy(
        update={
            "config_digest": payload_digest(
                {
                    "engine_configuration": command.engine_configuration,
                    "instrument_catalog": command.instrument_catalog,
                    "strategy_configuration": command.strategy_configuration,
                }
            ),
            "payload_digest": payload_digest(command),
            "payload": command,
        }
    )


def _identity(path: Path) -> tuple[int, int]:
    observed = path.stat(follow_symlinks=False)
    return observed.st_dev, observed.st_ino


def _closure_file(source: Path, target: str) -> ReadOnlyClosureMount:
    observed = source.stat(follow_symlinks=False)
    value = source.read_bytes()
    return ReadOnlyClosureMount(
        source=source,
        target=PurePosixPath(target),
        identity=(observed.st_dev, observed.st_ino),
        size=len(value),
        mode=observed.st_mode & 0o777,
        sha256=hashlib.sha256(value).hexdigest(),
    )


def _closure(
    tmp_path: Path, *, profile: str = "zero-order"
) -> CompleteEngineClosureAttestation:
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
        profile=profile,
        source_commit=SOURCE_COMMIT,
        closure_sha256="a" * 64,
        mounts=(
            _closure_file(entrypoint, "/engine/bin/engine"),
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
            version="bubblewrap 0.9.0",
            capabilities=("--perms", "--ro-bind-data"),
        ),
    )


def _provider(
    tmp_path: Path,
    attestor,
    *,
    attest_inputs=None,
) -> EngineSpawnProvider:
    transport = tmp_path / "transport"
    transport.mkdir(mode=0o700, exist_ok=True)
    return EngineSpawnProvider(
        transport_root=transport,
        attest_closure=attestor,
        attest_inputs=attest_inputs,
        monotonic_ns=lambda: 1_000_000_000,
    )


def _real_bwrap_closure(
    tmp_path: Path,
) -> tuple[CompleteEngineClosureAttestation, Path, Path]:
    required = (
        Path("/usr/bin/bwrap"),
        Path("/bin/dash"),
        Path("/lib/x86_64-linux-gnu/libc.so.6"),
        Path("/lib64/ld-linux-x86-64.so.2"),
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        pytest.fail(f"real Bubblewrap closure dependencies are missing: {missing}")

    sealed = tmp_path / "real-bwrap-sealed"
    sandbox = sealed / "sandbox" / "bwrap"
    sandbox.parent.mkdir(parents=True)
    shutil.copyfile(required[0], sandbox)
    sandbox.chmod(0o500)
    version_probe = subprocess.run(
        [str(sandbox), "--version"],
        check=False,
        env={},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )
    capability_probe = subprocess.run(
        [str(sandbox), "--help"],
        check=False,
        env={},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )
    assert version_probe.returncode == 0, version_probe.stderr
    assert capability_probe.returncode == 0, capability_probe.stderr
    for required_option in (b"--perms", b"--ro-bind-data"):
        assert required_option in capability_probe.stdout
    sandbox_version = version_probe.stdout.decode("ascii").strip()

    roots = {
        "bin": sealed / "closure-bin",
        "lib": sealed / "closure-lib",
        "lib64": sealed / "closure-lib64",
        "engine": sealed / "closure-engine",
    }
    files = (
        (required[1], roots["bin"] / "dash", "/bin/dash", 0o500),
        (
            required[2],
            roots["lib"] / "libc.so.6",
            "/lib/x86_64-linux-gnu/libc.so.6",
            0o500,
        ),
        (
            required[3],
            roots["lib64"] / "ld-linux-x86-64.so.2",
            "/lib64/ld-linux-x86-64.so.2",
            0o500,
        ),
    )
    for source, destination, _target, mode in files:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(mode)
    script = roots["engine"] / "run.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(
        b'IFS= read -r request < "$1"\n'
        b'IFS= read -r sidecar < "$2"\n'
        b'IFS= read -r runtime < /engine/runtime.txt\n'
        b'printf "%s\\n%s\\n%s\\n" "$request" "$sidecar" "$runtime"\n'
    )
    script.chmod(0o500)
    runtime = script.with_name("runtime.txt")
    runtime.write_bytes(b"attested-runtime\n")
    runtime.chmod(0o400)
    for root in roots.values():
        root.chmod(0o500)

    closure = CompleteEngineClosureAttestation(
        profile="zero-order",
        source_commit=SOURCE_COMMIT,
        closure_sha256="b" * 64,
        mounts=(
            *tuple(
                _closure_file(destination, target)
                for _source, destination, target, _mode in files
            ),
            _closure_file(script, "/engine/run.sh"),
            _closure_file(runtime, "/engine/runtime.txt"),
        ),
        entrypoint=PurePosixPath("/bin/dash"),
        argv_prefix=("/engine/run.sh",),
        timeout_seconds=10,
        result_validator_id="engine-event-v1",
        sandbox=OsSandboxProof(
            executable=sandbox,
            identity=_identity(sandbox),
            executable_sha256=hashlib.sha256(sandbox.read_bytes()).hexdigest(),
            profile_sha256=SANDBOX_PROFILE_SHA256,
            version=sandbox_version,
            capabilities=("--perms", "--ro-bind-data"),
        ),
    )
    return closure, roots["bin"] / "dash", script


def _close_spawn_fds(spawn) -> None:
    for descriptor in spawn.close_after_spawn_fds:
        os.close(descriptor)


def _fd_targets() -> dict[int, str]:
    observed: dict[int, str] = {}
    for entry in Path("/proc/self/fd").iterdir():
        try:
            observed[int(entry.name)] = os.readlink(entry)
        except FileNotFoundError:
            pass
    return observed


def _data_fd(argv: tuple[str, ...] | list[str], target: str) -> int:
    for index in range(len(argv) - 2):
        if argv[index] == "--ro-bind-data" and argv[index + 2] == target:
            assert argv[index - 2] == "--perms"
            return int(argv[index + 1])
    raise AssertionError(f"missing read-only FD-data target: {target}")


def _descriptor_source(value: str) -> int:
    prefix = "/proc/self/fd/"
    assert value.startswith(prefix)
    return int(value.removeprefix(prefix))


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
        request_fd, sidecar_fd, sandbox_fd, mount_fd = spawn.pass_fds
        request_bytes = os.pread(request_fd, 1_000_000, 0)
        sidecar_bytes = os.pread(sidecar_fd, 1_000_000, 0)
        assert request_bytes == canonical_json_bytes(envelope)
        assert sidecar_bytes == hashlib.sha256(request_bytes).hexdigest().encode() + b"\n"
        assert spawn.argv == (
            f"/proc/self/fd/{sandbox_fd}",
            "--die-with-parent",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--new-session",
            "--clearenv",
            "--dir",
            "/engine",
            "--dir",
            "/inputs",
            "--dir",
            "/engine/bin",
            "--dir",
            "/inputs/artifacts",
            "--perms",
            "0500",
            "--ro-bind-data",
            str(mount_fd),
            "/engine/bin/engine",
            "--perms",
            "0400",
            "--ro-bind-data",
            str(request_fd),
            "/inputs/request.json",
            "--perms",
            "0400",
            "--ro-bind-data",
            str(sidecar_fd),
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


def test_provider_mounts_each_attested_artifact_as_a_sealed_hash_bound_input(
    secure_tmp_path: Path,
) -> None:
    values = (
        ("engine_configuration", b'{"mode":"zero-order"}\n', "application/json"),
        ("instrument_catalog", b'{"catalog":"fixture"}\n', "application/json"),
        ("strategy_configuration", b'{"positions":[]}\n', "application/json"),
        ("market_data", b'{"close":"1"}\n', "application/jsonl"),
    )
    root = secure_tmp_path / "external-artifacts"
    root.mkdir(mode=0o700)
    references: list[ArtifactReference] = []
    inputs: list[HashBoundEngineInput] = []
    for index, (name, value, media_type) in enumerate(values, start=1):
        source = root / name
        source.write_bytes(value)
        source.chmod(0o400)
        digest = hashlib.sha256(value).hexdigest()
        reference = ArtifactReference(
            artifact_id=UUID(f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111"),
            sha256=digest,
            media_type=media_type,
        )
        references.append(reference)
        observed = source.stat(follow_symlinks=False)
        inputs.append(
            HashBoundEngineInput(
                name=name,
                reference=reference,
                source=source,
                identity=(observed.st_dev, observed.st_ino),
                size=observed.st_size,
                mode=0o400,
                sha256=digest,
            )
        )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    envelope = _envelope().model_copy(
        update={
            "config_digest": payload_digest(
                {
                    "engine_configuration": command.engine_configuration,
                    "instrument_catalog": command.instrument_catalog,
                    "strategy_configuration": command.strategy_configuration,
                }
            ),
            "payload_digest": payload_digest(command),
            "payload": command,
        }
    )

    closure = _closure(secure_tmp_path)
    spawn = consume_prepared_engine_spawn(
        _provider(
            secure_tmp_path,
            lambda: closure,
            attest_inputs=lambda request: tuple(inputs),
        ).prepare(envelope)
    )
    try:
        for item, (_name, value, _media_type) in zip(inputs, values, strict=True):
            extension = ".jsonl" if item.reference.media_type == "application/jsonl" else ".json"
            target = f"/inputs/artifacts/{item.name}-{item.sha256}{extension}"
            descriptor = _data_fd(spawn.argv, target)
            assert os.pread(descriptor, len(value) + 1, 0) == value
            assert fcntl.fcntl(descriptor, F_GET_SEALS) == (
                F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL
            )
    finally:
        _close_spawn_fds(spawn)


def test_simulation_provider_mounts_exactly_five_hash_bound_inputs(
    secure_tmp_path: Path,
) -> None:
    values = (
        ("engine_configuration", b'{"mode":"execution-simulation"}\n', "application/json"),
        ("instrument_catalog", b'{"catalog":"fixture"}\n', "application/json"),
        ("strategy_configuration", b'{"positions":[{}]}\n', "application/json"),
        ("market_data", b'{"close":"1"}\n', "application/jsonl"),
        ("simulation_scenario", b'{"scenario":"event-digest"}\n', "application/json"),
    )
    root = secure_tmp_path / "simulation-artifacts"
    root.mkdir(mode=0o700)
    references: list[ArtifactReference] = []
    inputs: list[HashBoundEngineInput] = []
    for index, (name, value, media_type) in enumerate(values, start=1):
        source = root / name
        source.write_bytes(value)
        source.chmod(0o400)
        digest = hashlib.sha256(value).hexdigest()
        reference = ArtifactReference(
            artifact_id=UUID(
                f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111"
            ),
            sha256=digest,
            media_type=media_type,
        )
        references.append(reference)
        observed = source.stat(follow_symlinks=False)
        inputs.append(
            HashBoundEngineInput(
                name=name,
                reference=reference,
                source=source,
                identity=(observed.st_dev, observed.st_ino),
                size=observed.st_size,
                mode=0o400,
                sha256=digest,
            )
        )
    envelope = _simulation_envelope(tuple(references))
    closure = _closure(secure_tmp_path, profile="execution-simulation")

    spawn = consume_prepared_engine_spawn(
        _provider(
            secure_tmp_path,
            lambda: closure,
            attest_inputs=lambda request: tuple(inputs),
        ).prepare(envelope)
    )
    try:
        input_targets = {
            spawn.argv[index + 2]
            for index, argument in enumerate(spawn.argv[:-2])
            if argument == "--ro-bind-data"
            and spawn.argv[index + 2].startswith("/inputs/artifacts/")
        }
        assert len(input_targets) == 5
        assert any("simulation_scenario-" in target for target in input_targets)
    finally:
        _close_spawn_fds(spawn)


def test_zero_order_request_rejects_an_extra_fifth_input(
    secure_tmp_path: Path,
) -> None:
    envelope = _envelope()
    source = secure_tmp_path / "extra-input"
    source.write_bytes(b"{}\n")
    source.chmod(0o400)
    observed = source.stat(follow_symlinks=False)
    reference = ArtifactReference(
        artifact_id=UUID("55555555-1111-4111-8111-111111111111"),
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        media_type="application/json",
    )
    extra = HashBoundEngineInput(
        name="simulation_scenario",
        reference=reference,
        source=source,
        identity=(observed.st_dev, observed.st_ino),
        size=observed.st_size,
        mode=0o400,
        sha256=reference.sha256,
    )

    with pytest.raises(EngineSpawnError, match="complete hash-bound inputs"):
        _provider(
            secure_tmp_path,
            lambda: _closure(secure_tmp_path),
            attest_inputs=lambda request: (extra,),
        ).prepare(envelope)


def test_closure_profiles_reject_the_opposite_backtest_command(
    secure_tmp_path: Path,
) -> None:
    references = tuple(
        ArtifactReference(
            artifact_id=UUID(
                f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111"
            ),
            sha256=f"{index}" * 64,
            media_type="application/jsonl" if index == 4 else "application/json",
        )
        for index in range(1, 6)
    )
    simulation = _simulation_envelope(references)
    zero_root = secure_tmp_path / "zero-profile"
    simulation_root = secure_tmp_path / "simulation-profile"
    zero_root.mkdir(mode=0o700)
    simulation_root.mkdir(mode=0o700)

    with pytest.raises(EngineSpawnError, match="profile"):
        _provider(
            zero_root,
            lambda: _closure(zero_root, profile="zero-order"),
        ).prepare(simulation)
    with pytest.raises(EngineSpawnError, match="profile"):
        _provider(
            simulation_root,
            lambda: _closure(
                simulation_root, profile="execution-simulation"
            ),
        ).prepare(_envelope())


def test_provider_rejects_an_artifact_changed_after_prepare_before_spawn(
    secure_tmp_path: Path,
) -> None:
    value = b'{"mode":"zero-order"}\n'
    source = secure_tmp_path / "external-artifact"
    source.write_bytes(value)
    source.chmod(0o400)
    reference = ArtifactReference(
        artifact_id=UUID("11111111-1111-4111-8111-111111111111"),
        sha256=hashlib.sha256(value).hexdigest(),
        media_type="application/json",
    )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=reference,
        instrument_catalog=reference,
        strategy_configuration=reference,
        market_data=ArtifactReference(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            sha256=reference.sha256,
            media_type="application/jsonl",
        ),
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    envelope = _envelope().model_copy(
        update={
            "config_digest": payload_digest(
                {
                    "engine_configuration": command.engine_configuration,
                    "instrument_catalog": command.instrument_catalog,
                    "strategy_configuration": command.strategy_configuration,
                }
            ),
            "payload_digest": payload_digest(command),
            "payload": command,
        }
    )
    observed = source.stat(follow_symlinks=False)
    inputs = tuple(
        HashBoundEngineInput(
            name=name,
            reference=getattr(command, name),
            source=source,
            identity=(observed.st_dev, observed.st_ino),
            size=observed.st_size,
            mode=0o400,
            sha256=getattr(command, name).sha256,
        )
        for name in (
            "engine_configuration",
            "instrument_catalog",
            "strategy_configuration",
            "market_data",
        )
    )
    closure = _closure(secure_tmp_path)
    prepared = _provider(
        secure_tmp_path,
        lambda: closure,
        attest_inputs=lambda request: inputs,
    ).prepare(envelope)
    source.chmod(0o600)
    source.write_bytes(b'{"mode":"mutated"}\n')
    source.chmod(0o400)

    with pytest.raises(EngineSpawnError, match="ENGINE_INPUT_STALE"):
        consume_prepared_engine_spawn(prepared)


def test_consumed_request_is_a_sealed_snapshot_not_the_mutable_transport_inode(
    secure_tmp_path: Path,
) -> None:
    envelope = _envelope()
    closure = _closure(secure_tmp_path)
    spawn = consume_prepared_engine_spawn(
        _provider(secure_tmp_path, lambda: closure).prepare(envelope)
    )
    try:
        request_fd = _data_fd(spawn.argv, "/inputs/request.json")
        sidecar_fd = _data_fd(spawn.argv, "/inputs/request.sha256")
        required_seals = (
            F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL
        )
        assert fcntl.fcntl(request_fd, F_GET_SEALS) == required_seals
        assert fcntl.fcntl(sidecar_fd, F_GET_SEALS) == required_seals
        os.fchmod(request_fd, 0o600)
        writable = os.open(f"/proc/self/fd/{request_fd}", os.O_RDWR)
        try:
            with pytest.raises(OSError):
                os.pwrite(writable, b"{}", 0)
        finally:
            os.close(writable)

        run_root = (
            secure_tmp_path / "transport" / f"run-{envelope.engine_run_id.hex}"
        )
        request_path = run_root / "request.json"
        sidecar_path = run_root / "request.sha256"
        request_path.chmod(0o600)
        sidecar_path.chmod(0o600)
        request_path.write_bytes(b"{}")
        sidecar_path.write_bytes(hashlib.sha256(b"{}").hexdigest().encode() + b"\n")

        expected_request = canonical_json_bytes(envelope)
        assert os.pread(request_fd, 1_000_000, 0) == expected_request
        assert os.pread(sidecar_fd, 1_000_000, 0) == (
            hashlib.sha256(expected_request).hexdigest().encode() + b"\n"
        )
    finally:
        _close_spawn_fds(spawn)


def test_consumed_sandbox_and_closure_files_remain_sealed_after_source_swap(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path)
    spawn = consume_prepared_engine_spawn(
        _provider(secure_tmp_path, lambda: closure).prepare(_envelope())
    )
    try:
        sandbox_fd = _descriptor_source(spawn.argv[0])
        mount_fd = _data_fd(spawn.argv, "/engine/bin/engine")
        closure.sandbox.executable.rename(
            closure.sandbox.executable.with_name("sandbox.replaced")
        )
        closure.mounts[0].source.parent.chmod(0o700)
        closure.mounts[0].source.rename(
            closure.mounts[0].source.with_name("engine.replaced")
        )

        assert os.pread(sandbox_fd, 1_000_000, 0) == b"reviewed-os-sandbox-v1"
        assert os.pread(mount_fd, 1_000_000, 0) == b"reviewed-engine-entrypoint-v1"
        assert fcntl.fcntl(mount_fd, F_GET_SEALS) == (
            F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL
        )
        assert sandbox_fd in spawn.pass_fds
        assert mount_fd in spawn.pass_fds
    finally:
        _close_spawn_fds(spawn)


def _run_real_bwrap_spawn(spawn):
    return subprocess.run(
        list(spawn.argv),
        cwd=spawn.cwd,
        env=dict(spawn.environment),
        pass_fds=spawn.pass_fds,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )


def _assert_real_bwrap_output(result, envelope: EngineCommandEnvelope) -> None:
    request = canonical_json_bytes(envelope)
    sidecar = hashlib.sha256(request).hexdigest().encode("ascii") + b"\n"
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert result.stdout == request + b"\n" + sidecar + b"attested-runtime\n"
    assert result.stderr == b""


def test_generated_spawn_executes_real_bwrap_with_sealed_input_data_fds(
    secure_tmp_path: Path,
) -> None:
    envelope = _envelope()
    closure, _copied_dash, _script = _real_bwrap_closure(secure_tmp_path)
    spawn = consume_prepared_engine_spawn(
        _provider(secure_tmp_path, lambda: closure).prepare(envelope)
    )
    try:
        run_root = (
            secure_tmp_path / "transport" / f"run-{envelope.engine_run_id.hex}"
        )
        (run_root / "request.json").chmod(0o600)
        (run_root / "request.json").write_bytes(b"{}")
        (run_root / "request.sha256").chmod(0o600)
        (run_root / "request.sha256").write_bytes(b"0" * 64 + b"\n")
        result = _run_real_bwrap_spawn(spawn)
    finally:
        _close_spawn_fds(spawn)

    _assert_real_bwrap_output(result, envelope)


def test_generated_spawn_executes_real_bwrap_after_closure_source_mutation(
    secure_tmp_path: Path,
) -> None:
    envelope = _envelope()
    closure, copied_dash, script = _real_bwrap_closure(secure_tmp_path)
    spawn = consume_prepared_engine_spawn(
        _provider(secure_tmp_path, lambda: closure).prepare(envelope)
    )
    try:
        copied_dash.chmod(0o700)
        copied_dash.write_bytes(b"same-uid-entrypoint-replacement")
        copied_dash.chmod(0o500)
        script.chmod(0o700)
        script.write_bytes(b"exit 97\n")
        script.chmod(0o500)
        result = _run_real_bwrap_spawn(spawn)
    finally:
        _close_spawn_fds(spawn)

    _assert_real_bwrap_output(result, envelope)


def test_concurrent_consumers_obtain_exactly_one_real_provider_launch(
    secure_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closure = _closure(secure_tmp_path)
    prepared = _provider(secure_tmp_path, lambda: closure).prepare(_envelope())
    original_contains = weakref.WeakSet.__contains__

    def slow_contains(issued, candidate):
        present = original_contains(issued, candidate)
        time.sleep(0.05)
        return present

    monkeypatch.setattr(weakref.WeakSet, "__contains__", slow_contains)
    start = threading.Barrier(3)
    successes = []
    errors = []

    def consume() -> None:
        start.wait()
        try:
            successes.append(consume_prepared_engine_spawn(prepared))
        except EngineSpawnError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    try:
        assert len(successes) == 1
        assert [error.reason for error in errors] == [
            "ENGINE_PREPARED_SPAWN_INVALID"
        ]
        for descriptor in successes[0].pass_fds:
            os.fstat(descriptor)
    finally:
        for descriptor in {
            fd for spawn in successes for fd in spawn.close_after_spawn_fds
        }:
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_real_provider_authority_stays_pinned_through_runner_popen_boundary(
    secure_tmp_path: Path,
) -> None:
    from services.job_worker.process_runner import HeartbeatDecision
    from tests.jobs.test_process_runner import (
        FakeProcess,
        Inspector,
        identity,
        runner,
    )

    envelope = _envelope()
    closure = _closure(secure_tmp_path)
    provider = _provider(secure_tmp_path, lambda: closure)
    process = FakeProcess([None, 0])
    inherited: tuple[int, ...] = ()

    def popen(argv, **kwargs):
        nonlocal inherited
        inherited = kwargs["pass_fds"]
        sandbox_fd = _descriptor_source(argv[0])
        mount_fd = _data_fd(argv, "/engine/bin/engine")
        request_fd = _data_fd(argv, "/inputs/request.json")
        sidecar_fd = _data_fd(argv, "/inputs/request.sha256")
        assert set(inherited) == {sandbox_fd, mount_fd, request_fd, sidecar_fd}

        run_root = (
            secure_tmp_path / "transport" / f"run-{envelope.engine_run_id.hex}"
        )
        request_path = run_root / "request.json"
        sidecar_path = run_root / "request.sha256"
        request_path.chmod(0o600)
        sidecar_path.chmod(0o600)
        request_path.write_bytes(b"{}")
        sidecar_path.write_bytes(hashlib.sha256(b"{}").hexdigest().encode() + b"\n")
        closure.sandbox.executable.rename(
            closure.sandbox.executable.with_name("sandbox.after-consume")
        )
        closure.mounts[0].source.parent.chmod(0o700)
        closure.mounts[0].source.rename(
            closure.mounts[0].source.with_name("engine.after-consume")
        )

        expected_request = canonical_json_bytes(envelope)
        assert os.pread(request_fd, 1_000_000, 0) == expected_request
        assert os.pread(sidecar_fd, 1_000_000, 0) == (
            hashlib.sha256(expected_request).hexdigest().encode() + b"\n"
        )
        assert os.pread(sandbox_fd, 1_000_000, 0) == b"reviewed-os-sandbox-v1"
        assert os.pread(mount_fd, 1_000_000, 0) == (
            b"reviewed-engine-entrypoint-v1"
        )
        return process

    instance = runner(
        secure_tmp_path, process, Inspector([identity()]), [],
    )
    instance._popen = popen
    outcome = instance.run(
        lambda: provider.prepare(envelope),
        object(),
        10,
        lambda _identity: HeartbeatDecision.CONTINUE,
        job_id="job_0123456789abcdef0123456789abcdef",
        attempt_id="attempt_fedcba9876543210fedcba9876543210",
    )

    assert outcome.exit_code == 0
    assert len(inherited) == 4
    for descriptor in inherited:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("failure_stage", ("validation", "safety", "popen"))
def test_runner_closes_every_real_provider_fd_after_post_consume_failure(
    secure_tmp_path: Path, failure_stage: str
) -> None:
    from services.job_worker.errors import SafetyBlockedError
    from services.job_worker.process_runner import HeartbeatDecision
    from tests.jobs.test_process_runner import (
        FakeProcess,
        Inspector,
        identity,
        runner,
        safety_evidence,
    )

    closure = _closure(secure_tmp_path)
    provider = _provider(secure_tmp_path, lambda: closure)
    process = FakeProcess([None, 0])
    instance = runner(
        secure_tmp_path, process, Inspector([identity()]), [],
    )
    timeout = 11 if failure_stage == "validation" else 10
    preflight = None
    expected: type[BaseException] = ValueError
    if failure_stage == "safety":
        good = safety_evidence("3" * 64)
        expired = replace(
            good,
            expires_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        )
        evidence = iter((good, expired))
        preflight = lambda: next(evidence)
        expected = SafetyBlockedError
    elif failure_stage == "popen":
        def fail_popen(*_args, **_kwargs):
            raise OSError("controlled Popen failure")

        instance._popen = fail_popen
        expected = OSError

    before = _fd_targets()
    with pytest.raises(expected):
        instance.run(
            lambda: provider.prepare(_envelope()),
            object(),
            timeout,
            lambda _identity: HeartbeatDecision.CONTINUE,
            preflight=preflight,
            job_id="job_0123456789abcdef0123456789abcdef",
            attempt_id="attempt_fedcba9876543210fedcba9876543210",
        )

    assert _fd_targets() == before


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
        target.parent.chmod(0o700)
        target.rename(original)
        if swapped_path == "sandbox":
            target.write_bytes(b"replacement-sandbox")
            target.chmod(0o500)
            reason = "ENGINE_SANDBOX_PROOF_INVALID"
        else:
            target.write_bytes(b"replacement-engine")
            target.chmod(0o500)
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


@pytest.mark.parametrize(
    "proof_change",
    (
        {"version": ""},
        {"capabilities": ("--perms",)},
    ),
)
def test_sandbox_proof_requires_versioned_ro_bind_data_capability(
    secure_tmp_path: Path, proof_change: dict[str, object]
) -> None:
    closure = _closure(secure_tmp_path)
    unsupported = replace(
        closure,
        sandbox=replace(closure.sandbox, **proof_change),  # type: ignore[arg-type]
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_SANDBOX_PROOF_INVALID"):
        _provider(secure_tmp_path, lambda: unsupported).prepare(_envelope())


def test_closure_mounts_cannot_shadow_sandbox_owned_targets(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path)
    shadow = replace(closure.mounts[0], target=PurePosixPath("/proc/engine-shadow"))
    unsafe = replace(closure, mounts=(*closure.mounts, shadow))

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(secure_tmp_path, lambda: unsafe).prepare(_envelope())


def test_closure_mount_targets_cannot_overlap_each_other(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path)
    overlap = replace(
        closure.mounts[0],
        target=PurePosixPath("/engine/bin/engine/nested-shadow"),
    )
    unsafe = replace(closure, mounts=(*closure.mounts, overlap))

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(secure_tmp_path, lambda: unsafe).prepare(_envelope())


def test_closure_inventory_rejects_a_host_directory_source(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path)
    directory = closure.mounts[0].source.parent
    observed = directory.stat(follow_symlinks=False)
    directory_entry = replace(
        closure.mounts[0],
        source=directory,
        target=PurePosixPath("/engine/bin"),
        identity=(observed.st_dev, observed.st_ino),
        size=0,
        mode=observed.st_mode & 0o777,
        sha256=hashlib.sha256(b"").hexdigest(),
    )
    unsafe = replace(
        closure,
        mounts=(directory_entry,),
        entrypoint=PurePosixPath("/engine/bin"),
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_STALE"):
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
