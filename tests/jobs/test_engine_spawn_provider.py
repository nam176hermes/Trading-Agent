from __future__ import annotations

import fcntl
import gc
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import weakref
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from uuid import UUID

import pytest

import services.job_worker.engine_spawn as engine_spawn_module
from packages.engine_contracts import (
    ArtifactReference,
    CURRENT_SCHEMA_VERSION,
    EngineCommandEnvelope,
    RunBacktest,
    RunBacktestSimulation,
    ValidatePaperCompatibility,
    canonical_json_bytes,
    payload_digest,
)
from services.job_worker.engine_spawn import (
    CompleteEngineClosureAttestation,
    HashBoundEngineInput,
    EngineSpawnError,
    EngineSpawnProvider,
    NativeEntryGuardAttestation,
    OsSandboxProof,
    ReadOnlyClosureMount,
    consume_prepared_engine_spawn,
)
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY


SOURCE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
DEPENDENCY_IMPORT_POLICY = (
    "native-guarded-stdlib-first-sealed-wheel-path-v1"
)
_UNSET = object()
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
    tmp_path: Path,
    *,
    profile: str = "zero-order",
    with_closure_manifest: bool = False,
    native_guard: bool = False,
    manifest_schema_version: int | None = None,
    dependency_import_policy: object = _UNSET,
) -> CompleteEngineClosureAttestation:
    sandbox = tmp_path / "sealed" / "bin" / "sandbox"
    sandbox.parent.mkdir(parents=True)
    sandbox.write_bytes(b"reviewed-os-sandbox-v1")
    sandbox.chmod(0o500)
    release = tmp_path / "sealed" / "engine-release"
    release.mkdir(mode=0o700)
    entrypoint = release / "bin" / (
        "nautilus-entry-guard" if native_guard else "engine"
    )
    entrypoint.parent.mkdir(mode=0o700)
    entrypoint_value = (
        b"reviewed-native-entry-guard-v1"
        if native_guard
        else b"reviewed-engine-entrypoint-v1"
    )
    entrypoint.write_bytes(entrypoint_value)
    entrypoint.chmod(0o500)
    guarded_python = release / "usr/bin/python3.12"
    if native_guard:
        guarded_python.parent.mkdir(parents=True, mode=0o700)
        guarded_python.write_bytes(b"reviewed-sealed-cpython-v1")
        guarded_python.chmod(0o500)
        guarded_python.parent.chmod(0o500)
    closure_manifest_path = release / "closure-manifest.json"
    if with_closure_manifest:
        closure_manifest_path.write_bytes(b'{"files":[]}')
        closure_manifest_path.chmod(0o400)
    entrypoint.parent.chmod(0o500)
    release.chmod(0o500)
    entrypoint_target = (
        PurePosixPath("/engine/bin/nautilus-entry-guard")
        if native_guard
        else PurePosixPath("/engine/bin/engine")
    )
    mounts = (
        _closure_file(entrypoint, str(entrypoint_target)),
        *(
            (_closure_file(guarded_python, "/usr/bin/python3.12"),)
            if native_guard
            else ()
        ),
    )
    native_attestation = (
        NativeEntryGuardAttestation(
            target=entrypoint_target,
            guarded_executable=PurePosixPath("/usr/bin/python3.12"),
            binary_sha256=hashlib.sha256(entrypoint_value).hexdigest(),
            binary_size=len(entrypoint_value),
            mode=0o500,
            source="engines/nautilus/native_entry_guard/src/main.rs",
            source_sha256="1" * 64,
            cargo_manifest="engines/nautilus/native_entry_guard/Cargo.toml",
            cargo_manifest_sha256="2" * 64,
            cargo_lock="engines/nautilus/native_entry_guard/Cargo.lock",
            cargo_lock_sha256="3" * 64,
            cargo_identity="cargo 1.95.0 (fixture)",
            rustc_identity="rustc 1.95.0 (fixture)",
            rust_toolchain_policy_sha256="4" * 64,
            llvm_toolchain_policy_sha256="5" * 64,
            target_triple="x86_64-unknown-linux-gnu",
        )
        if native_guard
        else None
    )
    if manifest_schema_version is None:
        manifest_schema_version = (
            5 if native_guard else 4 if with_closure_manifest else 1
        )
    dependency_import_policy_fields = (
        {
            "dependency_import_policy": (
                DEPENDENCY_IMPORT_POLICY
                if dependency_import_policy is _UNSET
                else dependency_import_policy
            )
        }
        if manifest_schema_version == 6
        else {}
    )
    return CompleteEngineClosureAttestation(
        manifest_schema_version=manifest_schema_version,
        profile=profile,
        source_commit=SOURCE_COMMIT,
        closure_sha256="a" * 64,
        mounts=mounts,
        entrypoint=entrypoint_target,
        argv_prefix=(
            (
                "/usr/bin/python3.12",
                "-I",
                "-S",
                "/engine/launcher/nautilus_backtest.py",
                "--profile",
                "execution-simulation",
            )
            if native_guard
            else ("run-backtest",)
        ),
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
        semantic_profile=(
            "nautilus-execution-simulation-v2"
            if profile == "execution-simulation"
            else None
        ),
        closure_manifest=(
            _closure_file(
                closure_manifest_path, "/engine/closure-manifest.json"
            )
            if with_closure_manifest
            else None
        ),
        native_entry_guard=native_attestation,
        **dependency_import_policy_fields,
    )


def _provider(
    tmp_path: Path,
    attestor,
    *,
    attest_inputs=None,
    expected_manifest_schema_version: int = 1,
    profile_policy=None,
) -> EngineSpawnProvider:
    transport = tmp_path / "transport"
    transport.mkdir(mode=0o700, exist_ok=True)
    return EngineSpawnProvider(
        transport_root=transport,
        attest_closure=attestor,
        attest_inputs=attest_inputs,
        expected_manifest_schema_version=expected_manifest_schema_version,
        profile_policy=profile_policy,
        monotonic_ns=lambda: 1_000_000_000,
    )


def _p1_closure(tmp_path: Path) -> CompleteEngineClosureAttestation:
    closure = _closure(
        tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
        manifest_schema_version=8,
        dependency_import_policy=DEPENDENCY_IMPORT_POLICY,
    )
    lineage_path = tmp_path / "p1-product-lineage.json"
    lineage_path.write_bytes(
        (
            json.dumps(
                {
                    "closure_sha256": closure.closure_sha256,
                    "engine_version": P1_REAL_BACKTEST_POLICY.engine_version,
                    "event_schema": P1_REAL_BACKTEST_POLICY.event_schema,
                    "profile": P1_REAL_BACKTEST_POLICY.profile,
                    "profile_manifest_schema_version": 8,
                    "runtime_family": P1_REAL_BACKTEST_POLICY.runtime_family,
                    "runtime_inventory_sha256": P1_REAL_BACKTEST_POLICY.runtime_inventory_sha256,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()
    )
    lineage_path.chmod(0o400)
    return replace(
        closure,
        profile=P1_REAL_BACKTEST_POLICY.profile,
        semantic_profile=P1_REAL_BACKTEST_POLICY.semantic_profile,
        argv_prefix=P1_REAL_BACKTEST_POLICY.argv_prefix,
        timeout_seconds=P1_REAL_BACKTEST_POLICY.timeout_seconds,
        result_validator_id=P1_REAL_BACKTEST_POLICY.result_validator_id,
        runtime_family=P1_REAL_BACKTEST_POLICY.runtime_family,
        engine_version=P1_REAL_BACKTEST_POLICY.engine_version,
        engine_upstream_commit=P1_REAL_BACKTEST_POLICY.engine_upstream_commit,
        event_schema=P1_REAL_BACKTEST_POLICY.event_schema,
        runtime_inventory_sha256=P1_REAL_BACKTEST_POLICY.runtime_inventory_sha256,
        dependency_import_policy=P1_REAL_BACKTEST_POLICY.dependency_import_policy,
        product_lineage=_closure_file(
            lineage_path, "/engine/p1-product-lineage.json"
        ),
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
        manifest_schema_version=1,
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


def test_p1_provider_uses_only_the_code_owned_schema8_profile(
    secure_tmp_path: Path,
) -> None:
    closure = _p1_closure(secure_tmp_path)
    provider = _provider(
        secure_tmp_path,
        lambda: closure,
        expected_manifest_schema_version=8,
        profile_policy=P1_REAL_BACKTEST_POLICY,
    )
    spawn = consume_prepared_engine_spawn(provider.prepare(_envelope()))
    try:
        assert spawn.argv[-9:] == (
            "/engine/bin/nautilus-entry-guard",
            *P1_REAL_BACKTEST_POLICY.argv_prefix,
            "/inputs/request.json",
            "/inputs/request.sha256",
        )
        assert spawn.result_validator_id == P1_REAL_BACKTEST_POLICY.result_validator_id
        assert "/engine/p1-product-lineage.json" in spawn.argv
    finally:
        _close_spawn_fds(spawn)


@pytest.mark.parametrize(
    "mutation",
    (
        {"manifest_schema_version": 7},
        {"profile": "execution-simulation"},
        {"argv_prefix": P1_REAL_BACKTEST_POLICY.argv_prefix + ("--extra",)},
        {"result_validator_id": "nautilus-backtest-result-v1"},
        {"runtime_family": "cython-v2"},
    ),
)
def test_p1_provider_rejects_generation_profile_or_protocol_substitution(
    secure_tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    closure = replace(_p1_closure(secure_tmp_path), **mutation)
    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: closure,
            expected_manifest_schema_version=8,
            profile_policy=P1_REAL_BACKTEST_POLICY,
        ).prepare(_envelope())


def test_p1_provider_rejects_tampered_derived_lineage(
    secure_tmp_path: Path,
) -> None:
    closure = _p1_closure(secure_tmp_path)
    assert closure.product_lineage is not None
    source = closure.product_lineage.source
    source.chmod(0o600)
    document = json.loads(source.read_bytes())
    document["closure_sha256"] = "0" * 64
    source.write_bytes((json.dumps(document, separators=(",", ":"), sort_keys=True) + "\n").encode())
    source.chmod(0o400)
    tampered = replace(
        closure,
        product_lineage=_closure_file(source, "/engine/p1-product-lineage.json"),
    )
    with pytest.raises(EngineSpawnError, match="lineage"):
        _provider(
            secure_tmp_path,
            lambda: tampered,
            expected_manifest_schema_version=8,
            profile_policy=P1_REAL_BACKTEST_POLICY,
        ).prepare(_envelope())


def test_schema7_remains_unavailable_to_the_product_worker(
    secure_tmp_path: Path,
) -> None:
    closure = replace(_p1_closure(secure_tmp_path), manifest_schema_version=7)
    with pytest.raises(ValueError, match="schema"):
        _provider(
            secure_tmp_path,
            lambda: closure,
            expected_manifest_schema_version=7,
            profile_policy=P1_REAL_BACKTEST_POLICY,
        )
    with pytest.raises(ValueError, match="schema"):
        _provider(
            secure_tmp_path,
            lambda: _p1_closure(secure_tmp_path),
            expected_manifest_schema_version=8,
        )


def _close_spawn_fds(spawn) -> None:
    for descriptor in spawn.close_after_spawn_fds:
        os.close(descriptor)


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


def _native_simulation_envelope() -> EngineCommandEnvelope:
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
    return _simulation_envelope(references)


def _paper_compatibility_command() -> ValidatePaperCompatibility:
    references = tuple(
        ArtifactReference(
            artifact_id=UUID(
                f"{index}{index}{index}{index}{index}{index}{index}{index}"
                "-1111-4111-8111-111111111111"
            ),
            sha256=f"{index}" * 64,
            media_type="application/json",
        )
        for index in range(1, 4)
    )
    return ValidatePaperCompatibility(
        command_type="ValidatePaperCompatibility",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        strategy_source_sha256="4" * 64,
        scenario_campaign_sha256="5" * 64,
    )


def test_provider_admits_paper_command_only_with_the_exact_paper_guard_profile(
    secure_tmp_path: Path,
) -> None:
    simulation = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
        manifest_schema_version=6,
    )
    paper = replace(
        simulation,
        profile="paper-compatibility",
        argv_prefix=(
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_paper_compat.py",
            "--profile",
            "paper-compatibility",
        ),
        result_validator_id="nautilus-paper-compatibility-result-v1",
        semantic_profile="nautilus-paper-compatibility-v1",
    )
    command = _paper_compatibility_command()

    with pytest.raises(EngineSpawnError, match="PROFILE_MISMATCH|CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: simulation,
            expected_manifest_schema_version=6,
        ).prepare(command)

    spawn = consume_prepared_engine_spawn(
        _provider(
            secure_tmp_path,
            lambda: paper,
            expected_manifest_schema_version=6,
        ).prepare(command)
    )
    try:
        assert spawn.argv[-9:] == (
            "/engine/bin/nautilus-entry-guard",
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_paper_compat.py",
            "--profile",
            "paper-compatibility",
            "/inputs/request.json",
            "/inputs/request.sha256",
        )
        assert dict(spawn.environment) == {}
    finally:
        _close_spawn_fds(spawn)


def test_provider_spawns_native_guard_with_cpython_as_exact_guarded_argv(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
    )

    spawn = consume_prepared_engine_spawn(
        _provider(
            secure_tmp_path,
            lambda: closure,
            expected_manifest_schema_version=5,
        ).prepare(
            _native_simulation_envelope()
        )
    )
    try:
        assert spawn.argv[-9:] == (
            "/engine/bin/nautilus-entry-guard",
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
            "/inputs/request.json",
            "/inputs/request.sha256",
        )
        assert _data_fd(spawn.argv, "/engine/bin/nautilus-entry-guard") in (
            spawn.pass_fds
        )
        assert _data_fd(spawn.argv, "/usr/bin/python3.12") in spawn.pass_fds
        assert spawn.argv[-9] != spawn.argv[-8]
    finally:
        _close_spawn_fds(spawn)


def test_provider_rejects_native_contract_forged_as_direct_python_entry(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
    )
    python_mount = next(
        mount
        for mount in closure.mounts
        if mount.target == PurePosixPath("/usr/bin/python3.12")
    )
    assert closure.native_entry_guard is not None
    direct = replace(
        closure,
        entrypoint=python_mount.target,
        native_entry_guard=replace(
            closure.native_entry_guard,
            target=python_mount.target,
            guarded_executable=python_mount.target,
            binary_sha256=python_mount.sha256,
            binary_size=python_mount.size,
        ),
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: direct,
            expected_manifest_schema_version=5,
        ).prepare(
            _native_simulation_envelope()
        )


def test_provider_rejects_direct_python_when_v5_native_attestation_is_removed(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
    )
    python_mount = next(
        mount
        for mount in closure.mounts
        if mount.target == PurePosixPath("/usr/bin/python3.12")
    )
    direct = replace(
        closure,
        entrypoint=python_mount.target,
        argv_prefix=(
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ),
        native_entry_guard=None,
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: direct,
            expected_manifest_schema_version=5,
        ).prepare(
            _native_simulation_envelope()
        )


def test_provider_rejects_v5_manifest_sidecar_removal_with_guard_retained(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: replace(closure, closure_manifest=None),
            expected_manifest_schema_version=5,
        ).prepare(_native_simulation_envelope())


def _strip_v5_native_contract(
    closure: CompleteEngineClosureAttestation,
) -> CompleteEngineClosureAttestation:
    python_mount = next(
        mount
        for mount in closure.mounts
        if mount.target == PurePosixPath("/usr/bin/python3.12")
    )
    return replace(
        closure,
        entrypoint=python_mount.target,
        argv_prefix=(
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
        ),
        closure_manifest=None,
        native_entry_guard=None,
    )


def test_provider_rejects_strip_both_v5_downgrade_during_prepare(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
    )
    direct = _strip_v5_native_contract(closure)

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: direct,
            expected_manifest_schema_version=5,
        ).prepare(
            _native_simulation_envelope()
        )


def test_provider_rejects_strip_both_and_schema_version_downgrade(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
    )
    direct = replace(
        _strip_v5_native_contract(closure),
        manifest_schema_version=3,
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: direct,
            expected_manifest_schema_version=5,
        ).prepare(_native_simulation_envelope())


def test_provider_rejects_strip_both_v5_downgrade_during_consume(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
    )
    attestations = iter((closure, _strip_v5_native_contract(closure)))
    provider = _provider(
        secure_tmp_path,
        lambda: next(attestations),
        expected_manifest_schema_version=5,
    )
    prepared = provider.prepare(_native_simulation_envelope())

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        consume_prepared_engine_spawn(prepared)


@pytest.mark.parametrize(
    ("actual_schema_version", "expected_schema_version"),
    ((4, 5), (5, 4)),
)
def test_provider_rejects_manifest_schema_generation_mismatch(
    secure_tmp_path: Path,
    actual_schema_version: int,
    expected_schema_version: int,
) -> None:
    native_guard = actual_schema_version == 5
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=native_guard,
        manifest_schema_version=actual_schema_version,
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: closure,
            expected_manifest_schema_version=expected_schema_version,
        ).prepare(_native_simulation_envelope())


@pytest.mark.parametrize("expected_schema_version", (True, 0, 7, None))
def test_provider_requires_one_exact_supported_manifest_schema_generation(
    secure_tmp_path: Path,
    expected_schema_version: object,
) -> None:
    transport = secure_tmp_path / "transport"
    transport.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="exact supported closure manifest schema"):
        EngineSpawnProvider(
            transport_root=transport,
            attest_closure=lambda: _closure(secure_tmp_path),
            expected_manifest_schema_version=expected_schema_version,  # type: ignore[arg-type]
            monotonic_ns=lambda: 1_000_000_000,
        )


def test_provider_rejects_schema_5_downgrade_when_schema_6_expected(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
        manifest_schema_version=5,
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: closure,
            expected_manifest_schema_version=6,
        ).prepare(_native_simulation_envelope())


@pytest.mark.parametrize(
    "dependency_import_policy",
    ("ambient-site-packages-first", True),
)
def test_provider_rejects_schema_6_unknown_or_boolean_import_policy_during_prepare(
    secure_tmp_path: Path,
    dependency_import_policy: object,
) -> None:
    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: _closure(
                secure_tmp_path,
                profile="execution-simulation",
                with_closure_manifest=True,
                native_guard=True,
                manifest_schema_version=6,
                dependency_import_policy=dependency_import_policy,
            ),
            expected_manifest_schema_version=6,
        ).prepare(_native_simulation_envelope())


def test_provider_rejects_import_policy_changed_after_prepare_before_consume(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
        manifest_schema_version=6,
    )
    attestations = iter(
        (
            closure,
            replace(
                closure,
                dependency_import_policy="ambient-site-packages-first",
            ),
        )
    )
    provider = _provider(
        secure_tmp_path,
        lambda: next(attestations),
        expected_manifest_schema_version=6,
    )
    prepared = provider.prepare(_native_simulation_envelope())

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        consume_prepared_engine_spawn(prepared)


@pytest.mark.parametrize(
    "mutation",
    (
        {"mode": 0o400},
        {"binary_sha256": "0" * 64},
        {"guarded_executable": PurePosixPath("/engine/bin/foreign-python")},
        {"source": "engines/nautilus/native_entry_guard/src/foreign.rs"},
        {"cargo_identity": "cargo 1.95.0 foreign"},
        {"target_triple": "aarch64-unknown-linux-gnu"},
    ),
)
def test_provider_rejects_malformed_native_guard_provenance(
    secure_tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
    )
    assert closure.native_entry_guard is not None
    malformed = replace(
        closure,
        native_entry_guard=replace(closure.native_entry_guard, **mutation),
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: malformed,
            expected_manifest_schema_version=5,
        ).prepare(
            _native_simulation_envelope()
        )


def test_provider_rejects_non_attestation_native_guard_object(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
        native_guard=True,
    )
    malformed = replace(closure, native_entry_guard=object())

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: malformed,
            expected_manifest_schema_version=5,
        ).prepare(
            _native_simulation_envelope()
        )


def test_provider_seals_separate_closure_manifest_at_the_fixed_target(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path, with_closure_manifest=True)
    assert closure.closure_manifest is not None
    spawn = consume_prepared_engine_spawn(
        _provider(
            secure_tmp_path,
            lambda: closure,
            expected_manifest_schema_version=4,
        ).prepare(_envelope())
    )
    try:
        descriptor = _data_fd(spawn.argv, "/engine/closure-manifest.json")
        assert os.pread(descriptor, 1_000_000, 0) == b'{"files":[]}'
        assert fcntl.fcntl(descriptor, F_GET_SEALS) == (
            F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL
        )
        target_index = spawn.argv.index("/engine/closure-manifest.json")
        assert spawn.argv[target_index - 4 : target_index] == (
            "--perms",
            "0400",
            "--ro-bind-data",
            str(descriptor),
        )
        assert closure.closure_manifest not in closure.mounts
    finally:
        _close_spawn_fds(spawn)


def test_provider_preserves_v4_sidecar_without_requiring_native_guard(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(
        secure_tmp_path,
        profile="execution-simulation",
        with_closure_manifest=True,
    )

    spawn = consume_prepared_engine_spawn(
        _provider(
            secure_tmp_path,
            lambda: closure,
            expected_manifest_schema_version=4,
        ).prepare(
            _native_simulation_envelope()
        )
    )
    try:
        assert _data_fd(spawn.argv, "/engine/closure-manifest.json") in (
            spawn.pass_fds
        )
        assert closure.native_entry_guard is None
    finally:
        _close_spawn_fds(spawn)


@pytest.mark.parametrize("manifest_schema_version", (1, 2, 3))
def test_provider_preserves_v1_v3_without_native_metadata(
    secure_tmp_path: Path,
    manifest_schema_version: int,
) -> None:
    profile = (
        "execution-simulation"
        if manifest_schema_version == 3
        else "zero-order"
    )
    closure = _closure(
        secure_tmp_path,
        profile=profile,
        manifest_schema_version=manifest_schema_version,
    )
    envelope = (
        _native_simulation_envelope()
        if profile == "execution-simulation"
        else _envelope()
    )

    spawn = consume_prepared_engine_spawn(
        _provider(
            secure_tmp_path,
            lambda: closure,
            expected_manifest_schema_version=manifest_schema_version,
        ).prepare(envelope)
    )
    try:
        assert closure.closure_manifest is None
        assert closure.native_entry_guard is None
    finally:
        _close_spawn_fds(spawn)


def test_provider_rejects_replaced_closure_manifest_and_closes_prepared_fds(
    secure_tmp_path: Path,
) -> None:
    closure = _closure(secure_tmp_path, with_closure_manifest=True)
    assert closure.closure_manifest is not None
    provider = _provider(
        secure_tmp_path,
        lambda: closure,
        expected_manifest_schema_version=4,
    )
    prepared = provider.prepare(_envelope())
    record_fds = (
        prepared._record.request_fd,
        prepared._record.sidecar_fd,
        prepared._record.run_fd,
        prepared._record.root_fd,
    )
    source = closure.closure_manifest.source
    replacement = source.with_name("closure-manifest.replacement.json")
    source.parent.chmod(0o700)
    replacement.write_bytes(source.read_bytes())
    replacement.chmod(0o400)
    os.replace(replacement, source)
    source.parent.chmod(0o500)

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_STALE"):
        consume_prepared_engine_spawn(prepared)

    for descriptor in record_fds:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize(
    "mutation",
    (
        {"target": PurePosixPath("/engine/not-the-closure-manifest.json")},
        {"mode": 0o500},
    ),
)
def test_provider_rejects_malformed_separate_closure_manifest_attestation(
    secure_tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    closure = _closure(secure_tmp_path, with_closure_manifest=True)
    assert closure.closure_manifest is not None
    malformed = replace(
        closure,
        closure_manifest=replace(closure.closure_manifest, **mutation),
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_CLOSURE_INVALID"):
        _provider(
            secure_tmp_path,
            lambda: malformed,
            expected_manifest_schema_version=4,
        ).prepare(_envelope())


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


def test_simulation_provider_rejects_only_scenario_replaced_after_prepare(
    secure_tmp_path: Path,
) -> None:
    values = (
        ("engine_configuration", b'{"mode":"execution-simulation"}\n', "application/json"),
        ("instrument_catalog", b'{"catalog":"fixture"}\n', "application/json"),
        ("strategy_configuration", b'{"positions":[{}]}\n', "application/json"),
        ("market_data", b'{"close":"1"}\n', "application/jsonl"),
        ("simulation_scenario", b'{"scenario":"before"}\n', "application/json"),
    )
    root = secure_tmp_path / "stale-simulation-artifacts"
    root.mkdir(mode=0o700)
    references: list[ArtifactReference] = []
    inputs: list[HashBoundEngineInput] = []
    original_identities: dict[str, tuple[int, int]] = {}
    sources: dict[str, Path] = {}
    for index, (name, value, media_type) in enumerate(values, start=1):
        source = root / name
        source.write_bytes(value)
        source.chmod(0o400)
        observed = source.stat(follow_symlinks=False)
        digest = hashlib.sha256(value).hexdigest()
        reference = ArtifactReference(
            artifact_id=UUID(
                f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111"
            ),
            sha256=digest,
            media_type=media_type,
        )
        references.append(reference)
        sources[name] = source
        original_identities[name] = (observed.st_dev, observed.st_ino)
        inputs.append(
            HashBoundEngineInput(
                name=name,
                reference=reference,
                source=source,
                identity=original_identities[name],
                size=observed.st_size,
                mode=0o400,
                sha256=digest,
            )
        )
    envelope = _simulation_envelope(tuple(references))
    closure = _closure(secure_tmp_path, profile="execution-simulation")
    prepared = _provider(
        secure_tmp_path,
        lambda: closure,
        attest_inputs=lambda request: tuple(inputs),
    ).prepare(envelope)

    replacement = root / "scenario-replacement"
    replacement.write_bytes(b'{"scenario":"after"}\n')
    replacement.chmod(0o400)
    os.replace(replacement, sources["simulation_scenario"])

    with pytest.raises(EngineSpawnError, match="ENGINE_INPUT_STALE"):
        consume_prepared_engine_spawn(prepared)

    assert _identity(sources["simulation_scenario"]) != original_identities[
        "simulation_scenario"
    ]
    assert {
        name: _identity(source)
        for name, source in sources.items()
        if name != "simulation_scenario"
    } == {
        name: identity
        for name, identity in original_identities.items()
        if name != "simulation_scenario"
    }


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
def test_runner_closes_transferred_provider_fds_after_post_consume_failure(
    secure_tmp_path: Path,
    failure_stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.job_worker.errors import SafetyBlockedError
    from services.job_worker import process_runner as process_runner_module
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
    process_fds = (process.stdout.fileno(), process.stderr.fileno())
    transferred_fds: tuple[int, ...] = ()
    consume = process_runner_module.consume_prepared_engine_spawn

    def capture_transferred_fds(prepared):
        nonlocal transferred_fds

        built = consume(prepared)
        transferred_fds = built.close_after_spawn_fds
        return built

    monkeypatch.setattr(
        process_runner_module,
        "consume_prepared_engine_spawn",
        capture_transferred_fds,
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

    assert transferred_fds
    for descriptor in transferred_fds:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    for descriptor in process_fds:
        os.fstat(descriptor)


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
        expected_manifest_schema_version=1,
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


@pytest.mark.parametrize(
    ("owner", "mode", "is_accepted"),
    (
        (0, 0o755, True),
        (os.geteuid(), 0o755, True),
        (0, 0o775, False),
        (
            next(
                candidate
                for candidate in range(1, 1_000)
                if candidate not in {0, os.geteuid()}
            ),
            0o755,
            False,
        ),
    ),
)
def test_sandbox_proof_is_pinned_only_for_trusted_non_group_or_other_writable_executables(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: int,
    mode: int,
    is_accepted: bool,
) -> None:
    """Protects the reviewed sandbox boundary through final FD pinning."""
    closure = _closure(secure_tmp_path)
    original_observed_identity = engine_spawn_module._observed_identity
    original_fstat = engine_spawn_module.os.fstat

    def observed_identity(path: Path, *, reason: str):
        observed, identity = original_observed_identity(path, reason=reason)
        if path == closure.sandbox.executable:
            return (
                SimpleNamespace(
                    st_mode=stat.S_IFREG | mode,
                    st_uid=owner,
                    st_dev=observed.st_dev,
                    st_ino=observed.st_ino,
                    st_size=observed.st_size,
                ),
                identity,
            )
        return observed, identity

    def fstat(descriptor: int):
        observed = original_fstat(descriptor)
        if Path(f"/proc/self/fd/{descriptor}").resolve() == closure.sandbox.executable:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | mode,
                st_uid=owner,
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_size=observed.st_size,
            )
        return observed

    monkeypatch.setattr(
        engine_spawn_module, "_observed_identity", observed_identity
    )
    monkeypatch.setattr(engine_spawn_module.os, "fstat", fstat)

    provider = _provider(secure_tmp_path, lambda: closure)
    if not is_accepted:
        with pytest.raises(EngineSpawnError) as error:
            provider.prepare(_envelope())
        assert error.value.reason == "ENGINE_SANDBOX_PROOF_INVALID"
    else:
        spawn = consume_prepared_engine_spawn(provider.prepare(_envelope()))
        _close_spawn_fds(spawn)


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
        {"manifest_schema_version": True},
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
        expected_manifest_schema_version=1,
        monotonic_ns=lambda: 1_000_000_000,
    )

    with pytest.raises(EngineSpawnError, match="ENGINE_TRANSPORT_UNSAFE"):
        provider.prepare(_envelope())

    assert list(real_transport.iterdir()) == []
