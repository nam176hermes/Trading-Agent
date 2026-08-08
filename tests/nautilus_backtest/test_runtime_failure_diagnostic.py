from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.engine_contracts import EngineCommandEnvelope, canonical_json_bytes
from packages.nautilus_backtest import (
    build_canonical_simulation_fixture,
    build_simulation_envelope,
)
from services.job_worker.engine_spawn_interface import EngineSpawnError


@pytest.fixture
def diagnostic():
    try:
        return importlib.import_module(
            "scripts.diagnose_nautilus_v12_runtime_failure"
        )
    except ModuleNotFoundError:
        return None


def _simulated_stderr() -> bytes:
    return hashlib.sha256(b"inert-runtime-failure-fixture").digest()


class _Harness:
    def __init__(
        self,
        *,
        rollback_schema: object = 3,
        candidate_schema: object = 5,
        returncode: int = 19,
        stdout: bytes | None = None,
        stderr: bytes | None = None,
        popen_error: OSError | None = None,
        consume_error: EngineSpawnError | None = None,
    ) -> None:
        self.rollback_schema = rollback_schema
        self.candidate_schema = candidate_schema
        self.returncode = returncode
        self.stdout = (
            hashlib.sha256(b"inert-runtime-stdout-fixture").digest()
            if stdout is None
            else stdout
        )
        self.stderr = _simulated_stderr() if stderr is None else stderr
        self.popen_error = popen_error
        self.consume_error = consume_error
        self.attest_calls: list[tuple[str, Path]] = []
        self.provider_kwargs: list[dict[str, object]] = []
        self.prepare_calls: list[EngineCommandEnvelope] = []
        self.input_sources: list[Path] = []
        self.input_modes: list[tuple[int, int]] = []
        self.consume_calls = 0
        self.popen_calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.events: list[str] = []
        self._candidate_attestation = SimpleNamespace(
            manifest_schema_version=self.candidate_schema,
            closure_sha256="b" * 64,
            closure_manifest=SimpleNamespace(sha256="c" * 64),
            profile="execution-simulation",
        )

    def attest(self, config, *, expected_profile: str):
        self.events.append(f"attest:{expected_profile}")
        self.attest_calls.append((expected_profile, config.artifact_directory))
        if expected_profile == "zero-order":
            return SimpleNamespace(
                manifest_schema_version=self.rollback_schema,
                closure_sha256="a" * 64,
                closure_manifest=None,
                profile="zero-order",
            )
        return self._candidate_attestation

    def provider_factory(self, **kwargs):
        harness = self
        self.provider_kwargs.append(kwargs)

        class Provider:
            def prepare(self, envelope: EngineCommandEnvelope):
                harness.events.append("prepare")
                kwargs["attest_closure"]()
                inputs = kwargs["attest_inputs"](envelope.payload)
                harness.input_sources.extend(item.source for item in inputs)
                harness.input_modes.extend(
                    (
                        stat.S_IMODE(item.source.stat().st_mode),
                        stat.S_IMODE(item.source.parent.stat().st_mode),
                    )
                    for item in inputs
                )
                run_directory = (
                    kwargs["transport_root"]
                    / f"run-{envelope.engine_run_id.hex}"
                )
                run_directory.mkdir(mode=0o700)
                for name in ("request.json", "request.sha256"):
                    path = run_directory / name
                    path.write_bytes(b"sealed")
                    path.chmod(0o400)
                harness.prepare_calls.append(envelope)
                return SimpleNamespace(envelope=envelope)

        return Provider()

    def consume(self, prepared):
        self.events.append("consume")
        self.consume_calls += 1
        if self.consume_error is not None:
            raise self.consume_error
        return SimpleNamespace(
            argv=("inert-sandbox", "long-accounting"),
            cwd=Path("/inert-runtime"),
            environment={"INERT_RUNTIME": "1"},
            pass_fds=(),
            close_after_spawn_fds=(),
            timeout_seconds=11,
        )

    def popen(self, argv, **kwargs):
        self.events.append("popen")
        self.popen_calls.append((argv, kwargs))
        if self.popen_error is not None:
            raise self.popen_error
        harness = self

        class Process:
            returncode = harness.returncode

            def communicate(self, *, timeout: int):
                harness.events.append("communicate")
                assert timeout == 11
                return harness.stdout, harness.stderr

        return Process()


@pytest.fixture
def external_paths() -> dict[str, Path]:
    with tempfile.TemporaryDirectory(
        prefix="nautilus-diagnostic-test-", dir="/tmp"
    ) as directory:
        root = Path(directory)
        root.chmod(0o700)
        paths = {
            "rollback_closure": root / "rollback",
            "rollback_artifact_directory": root / "rollback-artifacts",
            "candidate_closure": root / "candidate",
            "artifact_directory": root / "candidate-artifacts",
            "sandbox": root / "sandbox",
            "transport_root": root / "transport",
            "diagnostic_record": root / "diagnostic" / "failure.json",
        }
        for name in (
            "rollback_closure",
            "rollback_artifact_directory",
            "candidate_closure",
            "artifact_directory",
            "transport_root",
        ):
            paths[name].mkdir(mode=0o700)
        for name in ("rollback_artifact_directory", "artifact_directory"):
            marker = paths[name] / "artifact-manifest.json"
            marker.write_bytes(b"{}")
            marker.chmod(0o400)
            paths[name].chmod(0o500)
        paths["diagnostic_record"].parent.mkdir(mode=0o700)
        yield paths


def _run(diagnostic, paths: dict[str, Path], harness: _Harness, **overrides):
    arguments = {
        **paths,
        "attest_closure": harness.attest,
        "provider_factory": harness.provider_factory,
        "consume_spawn": harness.consume,
        "popen_factory": harness.popen,
    }
    arguments.update(overrides)
    return diagnostic.diagnose_nautilus_v12_runtime_failure(**arguments)


def _path_identity(path: Path) -> tuple[int, int]:
    observed = path.lstat()
    return observed.st_dev, observed.st_ino


def _replace_record(path: Path) -> tuple[int, int]:
    path.unlink()
    path.write_bytes(hashlib.sha256(b"inert-path-replacement").digest())
    path.chmod(0o400)
    return _path_identity(path)


def test_cli_requires_exactly_the_seven_named_arguments(diagnostic, capsys) -> None:
    parser = diagnostic._parser()
    required = [
        "--rollback-closure",
        "/tmp/rollback",
        "--rollback-artifact-directory",
        "/tmp/rollback-artifacts",
        "--candidate-closure",
        "/tmp/candidate",
        "--artifact-directory",
        "/tmp/candidate-artifacts",
        "--sandbox",
        "/tmp/sandbox",
        "--transport-root",
        "/tmp/transport",
        "--diagnostic-record",
        "/tmp/diagnostic.json",
    ]

    parsed = parser.parse_args(required)

    assert vars(parsed) == {
        "rollback_closure": Path("/tmp/rollback"),
        "rollback_artifact_directory": Path("/tmp/rollback-artifacts"),
        "candidate_closure": Path("/tmp/candidate"),
        "artifact_directory": Path("/tmp/candidate-artifacts"),
        "sandbox": Path("/tmp/sandbox"),
        "transport_root": Path("/tmp/transport"),
        "diagnostic_record": Path("/tmp/diagnostic.json"),
    }
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args([*required[:2], *required[4:]])
    with pytest.raises(SystemExit):
        parser.parse_args([*required, "--scenario", "long-accounting"])
    with pytest.raises(SystemExit):
        parser.parse_args([*required, "--runs", "1"])
    capsys.readouterr()


def test_diagnostic_runs_one_fixed_long_accounting_launch_through_normal_boundary(
    diagnostic, external_paths: dict[str, Path]
) -> None:
    harness = _Harness()
    expected = build_simulation_envelope(
        build_canonical_simulation_fixture("long-accounting")
    )

    _run(diagnostic, external_paths, harness)

    assert harness.prepare_calls == [expected]
    assert harness.consume_calls == 1
    assert len(harness.popen_calls) == 1
    assert harness.popen_calls[0][0] == ("inert-sandbox", "long-accounting")
    assert harness.events == [
        "attest:zero-order",
        "attest:execution-simulation",
        "prepare",
        "attest:execution-simulation",
        "consume",
        "popen",
        "communicate",
    ]
    assert len(harness.provider_kwargs) == 1
    assert harness.provider_kwargs[0]["expected_manifest_schema_version"] == 5
    assert harness.provider_kwargs[0]["transport_root"] == external_paths[
        "transport_root"
    ]
    assert len(harness.input_sources) == 5
    assert harness.input_modes == [(0o400, 0o700)] * 5
    assert list(external_paths["transport_root"].iterdir()) == []


def test_diagnostic_attests_distinct_rollback_v3_and_candidate_v5_authorities(
    diagnostic, external_paths: dict[str, Path]
) -> None:
    harness = _Harness()

    _run(diagnostic, external_paths, harness)

    assert harness.attest_calls[0] == (
        "zero-order",
        external_paths["rollback_artifact_directory"],
    )
    assert all(
        profile == "execution-simulation"
        and artifacts == external_paths["artifact_directory"]
        for profile, artifacts in harness.attest_calls[1:]
    )


@pytest.mark.parametrize(
    ("rollback_schema", "candidate_schema", "message"),
    (
        (2, 5, "rollback.*schema 3"),
        (3.0, 5, "rollback.*schema 3"),
        (3, 4, "candidate.*schema 5"),
        (3, 5.0, "candidate.*schema 5"),
    ),
)
def test_diagnostic_rejects_wrong_closure_authority_before_launch_and_record(
    diagnostic,
    external_paths: dict[str, Path],
    rollback_schema: object,
    candidate_schema: object,
    message: str,
) -> None:
    harness = _Harness(
        rollback_schema=rollback_schema,
        candidate_schema=candidate_schema,
    )

    with pytest.raises(diagnostic.RuntimeFailureDiagnosticError, match=message):
        _run(diagnostic, external_paths, harness)

    assert harness.prepare_calls == []
    assert harness.popen_calls == []
    assert not external_paths["diagnostic_record"].exists()


def test_completed_process_writes_only_the_canonical_private_diagnostic_record(
    diagnostic, external_paths: dict[str, Path]
) -> None:
    harness = _Harness()

    _run(diagnostic, external_paths, harness)

    record_path = external_paths["diagnostic_record"]
    record = json.loads(record_path.read_bytes())
    assert set(record) == {
        "schema_version",
        "exit_code",
        "stdout_sha256",
        "stderr_sha256",
        "stderr_base64",
    }
    assert record == {
        "schema_version": "nautilus-v12-runtime-failure-diagnostic-v1",
        "exit_code": harness.returncode,
        "stdout_sha256": hashlib.sha256(harness.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(harness.stderr).hexdigest(),
        "stderr_base64": base64.b64encode(harness.stderr).decode("ascii"),
    }
    assert record_path.read_bytes() == canonical_json_bytes(record) + b"\n"
    assert stat.S_IMODE(record_path.stat().st_mode) == 0o400
    assert harness.events[-2:] == ["popen", "communicate"]


@pytest.mark.parametrize("mutation", ("replace", "remove"))
def test_diagnostic_rejects_record_path_mutation_during_process_reap(
    diagnostic,
    external_paths: dict[str, Path],
    mutation: str,
) -> None:
    harness = _Harness()
    record_path = external_paths["diagnostic_record"]
    replacement_identity: list[tuple[int, int]] = []

    def popen(_argv, **_kwargs):
        class Process:
            returncode = harness.returncode

            def communicate(self, *, timeout: int):
                assert timeout == 11
                if mutation == "replace":
                    replacement_identity.append(_replace_record(record_path))
                else:
                    record_path.unlink()
                return harness.stdout, harness.stderr

        return Process()

    with pytest.raises(
        diagnostic.RuntimeFailureDiagnosticError,
        match="record.*identity|record.*named entry",
    ):
        _run(
            diagnostic,
            external_paths,
            harness,
            popen_factory=popen,
        )

    if mutation == "replace":
        assert _path_identity(record_path) == replacement_identity[0]
    else:
        assert not record_path.exists()


def test_diagnostic_rejects_record_replacement_during_sealing(
    diagnostic,
    external_paths: dict[str, Path],
    monkeypatch,
) -> None:
    harness = _Harness()
    record_path = external_paths["diagnostic_record"]
    replacement_identity: list[tuple[int, int]] = []
    real_fsync = diagnostic.os.fsync

    def replace_record_on_its_fsync(descriptor: int) -> None:
        if record_path.exists():
            named = record_path.lstat()
            opened = os.fstat(descriptor)
            if (
                not replacement_identity
                and (named.st_dev, named.st_ino)
                == (opened.st_dev, opened.st_ino)
            ):
                replacement_identity.append(_replace_record(record_path))
        real_fsync(descriptor)

    monkeypatch.setattr(diagnostic.os, "fsync", replace_record_on_its_fsync)

    with pytest.raises(
        diagnostic.RuntimeFailureDiagnosticError,
        match="record.*identity|record.*named entry",
    ):
        _run(diagnostic, external_paths, harness)

    assert _path_identity(record_path) == replacement_identity[0]


def test_failure_cleanup_preserves_a_replacement_record_inode(
    diagnostic,
    external_paths: dict[str, Path],
) -> None:
    harness = _Harness()
    record_path = external_paths["diagnostic_record"]
    replacement_identity: list[tuple[int, int]] = []

    def replace_then_fail(_argv, **_kwargs):
        replacement_identity.append(_replace_record(record_path))
        raise OSError("inert launch refusal after replacement")

    with pytest.raises(OSError, match="inert launch refusal"):
        _run(
            diagnostic,
            external_paths,
            harness,
            popen_factory=replace_then_fail,
        )

    assert _path_identity(record_path) == replacement_identity[0]


@pytest.mark.parametrize("mutation", ("relative", "checkout", "existing"))
def test_diagnostic_rejects_unsafe_or_existing_record_before_popen(
    diagnostic,
    external_paths: dict[str, Path],
    mutation: str,
) -> None:
    record = external_paths["diagnostic_record"]
    if mutation == "relative":
        record = Path("failure.json")
    elif mutation == "checkout":
        record = Path.cwd() / ".forbidden-runtime-diagnostic.json"
    else:
        record.write_bytes(b"reserved")
        record.chmod(0o400)
    harness = _Harness()

    with pytest.raises(diagnostic.RuntimeFailureDiagnosticError, match="record"):
        _run(diagnostic, external_paths, harness, diagnostic_record=record)

    assert harness.popen_calls == []
    if mutation == "checkout":
        assert not record.exists()


def test_diagnostic_rejects_non_0400_reserved_record_before_popen(
    diagnostic, external_paths: dict[str, Path], monkeypatch
) -> None:
    harness = _Harness()
    real_fchmod = diagnostic.os.fchmod

    def force_wrong_mode(descriptor: int, _mode: int) -> None:
        real_fchmod(descriptor, 0o600)

    monkeypatch.setattr(diagnostic.os, "fchmod", force_wrong_mode)

    with pytest.raises(diagnostic.RuntimeFailureDiagnosticError, match="0400"):
        _run(diagnostic, external_paths, harness)

    assert harness.popen_calls == []
    assert not external_paths["diagnostic_record"].exists()


@pytest.mark.parametrize(
    "mutation", ("relative", "checkout", "empty", "public", "same")
)
def test_diagnostic_rejects_unsafe_artifact_or_transport_authority_before_popen(
    diagnostic,
    external_paths: dict[str, Path],
    mutation: str,
) -> None:
    overrides: dict[str, Path] = {}
    if mutation == "relative":
        overrides["rollback_artifact_directory"] = Path("rollback-artifacts")
    elif mutation == "checkout":
        overrides["artifact_directory"] = Path.cwd()
    elif mutation == "empty":
        (external_paths["transport_root"] / "occupied").write_bytes(b"x")
    elif mutation == "public":
        external_paths["transport_root"].chmod(0o755)
    else:
        overrides["rollback_artifact_directory"] = external_paths[
            "artifact_directory"
        ]
    harness = _Harness()

    with pytest.raises(diagnostic.RuntimeFailureDiagnosticError):
        _run(diagnostic, external_paths, harness, **overrides)

    assert harness.popen_calls == []
    assert not external_paths["diagnostic_record"].exists()


def test_diagnostic_uses_exact_spawn_authority_and_closes_fds_before_reaping(
    diagnostic, external_paths: dict[str, Path]
) -> None:
    harness = _Harness()
    read_fd, write_fd = os.pipe()
    argv = ("inert-sandbox", "long-accounting")
    cwd = Path("/inert-runtime")
    environment = {"INERT_RUNTIME": "1"}

    def consume(_prepared):
        harness.consume_calls += 1
        return SimpleNamespace(
            argv=argv,
            cwd=cwd,
            environment=environment,
            pass_fds=(read_fd,),
            close_after_spawn_fds=(read_fd,),
            timeout_seconds=11,
        )

    def popen(observed_argv, **kwargs):
        os.fstat(read_fd)
        assert observed_argv is argv
        assert kwargs["cwd"] is cwd
        assert kwargs["env"] is environment
        assert kwargs["pass_fds"] == (read_fd,)

        class Process:
            returncode = harness.returncode

            def communicate(self, *, timeout: int):
                assert timeout == 11
                with pytest.raises(OSError):
                    os.fstat(read_fd)
                return harness.stdout, harness.stderr

        return Process()

    try:
        _run(
            diagnostic,
            external_paths,
            harness,
            consume_spawn=consume,
            popen_factory=popen,
        )
    finally:
        os.close(write_fd)
        try:
            os.close(read_fd)
        except OSError:
            pass


@pytest.mark.parametrize("failure", ("launch", "consume"))
def test_launch_or_consume_failure_leaves_no_record_and_never_retries(
    diagnostic,
    external_paths: dict[str, Path],
    failure: str,
) -> None:
    harness = (
        _Harness(popen_error=OSError("inert launch refusal"))
        if failure == "launch"
        else _Harness(
            consume_error=EngineSpawnError(
                "ENGINE_CLOSURE_STALE", "inert authority refusal"
            )
        )
    )

    with pytest.raises((OSError, EngineSpawnError)):
        _run(diagnostic, external_paths, harness)

    assert len(harness.prepare_calls) == 1
    assert harness.consume_calls == 1
    assert len(harness.popen_calls) == (1 if failure == "launch" else 0)
    assert not external_paths["diagnostic_record"].exists()
    assert list(external_paths["transport_root"].iterdir()) == []
