from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from packages.job_contracts import BacktestPayload, DebatePayload, JobType, ReplayPayload, SnapshotPayload
from services.job_worker.command_registry import (
    APPROVED_BACKEND_CWD,
    APPROVED_BACKEND_PYTHON,
    APPROVED_BACKEND_REVISION,
    APPROVED_RELEASE_MANIFEST_PATH,
    COMMAND_REGISTRY,
    CommandRegistryError,
    PreparedSpawn,
    ValidatedCommandCapability,
    attest_command_capability,
    build_command,
    consume_prepared_spawn,
    prepare_immediate_spawn,
)
from packages.runtime_release.config import (
    CommandManifestAuthority,
    ReleaseAuthority,
    RuntimeAuthority,
    SafetyAuthority,
    SemanticAuthority,
)
from packages.runtime_release.semantic import SemanticEvidence
from packages.runtime_release.v2 import paper_command_manifest
from tests.jobs.backend_contract_fixtures import BACKEND_COMMIT


@pytest.fixture
def tmp_path():
    path = Path(tempfile.mkdtemp(prefix="task7-command-", dir="/home/thenam176/.cache"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _job(job_type: JobType, payload: object) -> SimpleNamespace:
    return SimpleNamespace(job_type=job_type, payload=payload)


def test_worker_v2_command_policy_matches_release_authority_manifest() -> None:
    import services.job_worker.command_registry as module

    install_root = Path("/opt/trading-agent-v2/releases/" + "a" * 40)
    authority = cast(
        Any,
        SimpleNamespace(
            backend_root=install_root / "backend",
            backend_python=install_root / "backend/.venv/bin/python3.11",
        ),
    )

    assert module._expected_v2_command_document(authority) == paper_command_manifest(
        install_root
    )


def _semantic_evidence(version: str = "semantic-v1") -> SemanticEvidence:
    generated = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    return SemanticEvidence(
        active_authority_sha256=("7" if version == "semantic-v1" else "8") * 64,
        version_manifest_sha256=("9" if version == "semantic-v1" else "a") * 64,
        semantic_input_fingerprint=("b" if version == "semantic-v1" else "c") * 64,
        manifest_version=version,
        generated_at=generated,
        expires_at=generated + timedelta(minutes=30),
        policy_sha256="4" * 64,
    )


def _runtime_authority() -> RuntimeAuthority:
    app = ReleaseAuthority(
        "a" * 40, Path(f"/opt/trading-agent-phase4/releases/app-{'a' * 40}"),
        Path(f"/opt/trading-agent-phase4/manifests/app-{'a' * 40}.manifest.json"),
        "1" * 64, Path(f"/opt/trading-agent-phase4/releases/app-{'a' * 40}/.venv/bin/python3.11"),
        "CPython 3.11.13",
    )
    backend = ReleaseAuthority(
        BACKEND_COMMIT, Path(f"/opt/trading-agent-phase4/releases/backend-{BACKEND_COMMIT}"),
        Path(f"/opt/trading-agent-phase4/manifests/backend-{BACKEND_COMMIT}.manifest.json"),
        "2" * 64, Path(f"/opt/trading-agent-phase4/releases/backend-{BACKEND_COMMIT}/.venv/bin/python3.11"),
        "CPython 3.11.13",
    )
    return RuntimeAuthority(
        app, backend,
        CommandManifestAuthority(Path(f"/opt/trading-agent-phase4/manifests/commands-{BACKEND_COMMIT}.json"), "3" * 64),
        SemanticAuthority(Path("/etc/trading-agent/research-input-manifests/phase4-v1.json"), "4" * 64),
        SafetyAuthority("a" * 40, Path("/run/user/1000/trading-agent/safety-state.json"), "5" * 64),
        (1, 2), "6" * 64,
    )


def _command_document(authority: RuntimeAuthority) -> dict[str, object]:
    commands = {
        job_type.value: {
            "executable": str(authority.backend.python_path),
            "cwd": str(authority.backend.release_root),
            "argv_prefix": list(spec.argv_prefix),
            "timeout_seconds": spec.timeout_seconds,
            "max_attempts": spec.max_attempts,
            "result_validator": spec.result_validator_id,
            "shell": False,
        }
        for job_type, spec in sorted(COMMAND_REGISTRY.items(), key=lambda item: item[0].value)
    }
    return {
        "manifest_version": 1,
        "backend_commit": authority.backend.git_commit,
        "commands": commands,
        "aggregate_sha256": hashlib.sha256(
            json.dumps(commands, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest(),
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executable", "/bin/sh"),
        ("cwd", "/tmp"),
        ("argv_prefix", ["--exec"]),
        ("timeout_seconds", 1),
        ("timeout_seconds", True),
        ("result_validator", "arbitrary"),
        ("shell", True),
    ],
)
def test_external_command_manifest_must_exactly_equal_code_owned_registry(
    monkeypatch, field, value
):
    import services.job_worker.command_registry as module

    authority = _runtime_authority()
    document = _command_document(authority)
    document["commands"]["SNAPSHOT"][field] = value  # type: ignore[index]
    monkeypatch.setattr(module, "_load_runtime_authority", lambda: authority)
    monkeypatch.setattr(module, "_attest_release", module._attest_release_v1)
    monkeypatch.setattr(module, "_verify_release", lambda *args, **kwargs: True)
    monkeypatch.setattr(module, "_read_command_manifest", lambda *args: document)
    monkeypatch.setattr(module, "_runtime_python_path", lambda: authority.application.python_path)

    with pytest.raises(CommandRegistryError) as raised:
        attest_command_capability()
    assert raised.value.reason_code == "COMMAND_MANIFEST_MISMATCH"


def _entry(root: Path, path: Path) -> dict[str, object]:
    relative = path.relative_to(root).as_posix()
    if path.is_dir():
        return {"path": relative, "type": "directory", "mode": "0555", "size": 0, "sha256": hashlib.sha256(b"").hexdigest()}
    content = path.read_bytes()
    return {"path": relative, "type": "file", "mode": format(stat.S_IMODE(path.stat().st_mode), "04o"), "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _deployment(tmp_path: Path, monkeypatch):
    # Nested capability fixtures pass a not-yet-existing parent. Create and
    # harden it explicitly because parents=True otherwise uses the ambient
    # umask for intermediate directories, which can produce unsafe 0775 paths.
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_path.chmod(0o700)
    root = tmp_path / APPROVED_BACKEND_REVISION
    files = {
        ".venv/bin/python": b"python-binary",
        ".venv/lib/python3.11/site-packages/sitecustomize.py": b"raise RuntimeError('reviewed')\n",
        ".venv/lib/python3.11/site-packages/authority.pth": b"/untrusted/import/path\n",
        ".venv/lib/python3.11/site-packages/native.so": b"native-extension",
        ".ignored-config": b"ignored-but-attested=true\n",
        "config/runtime.json": b'{"mode":"research"}\n',
        "data/model.bin": b"model-data",
        "main.py": b"# reviewed entrypoint\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o555 if relative == ".venv/bin/python" else 0o444)
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        path.chmod(0o555)
    root.chmod(0o555)
    entries = [_entry(root, path) for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())]
    manifest = {"schema_version": "trading-agent-release-manifest/v1", "release_id": APPROVED_BACKEND_REVISION, "entries": entries}
    raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_bytes(raw)
    manifest_path.chmod(0o444)
    monkeypatch.setattr("services.job_worker.command_registry.APPROVED_BACKEND_CWD", root)
    monkeypatch.setattr("services.job_worker.command_registry.APPROVED_BACKEND_PYTHON", root / ".venv/bin/python")
    monkeypatch.setattr("services.job_worker.command_registry.APPROVED_RELEASE_MANIFEST_PATH", manifest_path)
    real_lstat = Path.lstat
    def root_owned_lstat(path: Path):
        values = list(real_lstat(path))
        values[4] = 0
        return os.stat_result(values)
    monkeypatch.setattr("services.job_worker.command_registry._lstat", root_owned_lstat)
    authority = RuntimeAuthority(
        ReleaseAuthority("a" * 40, root, manifest_path, "1" * 64, root / ".venv/bin/python", "CPython 3.11.13"),
        ReleaseAuthority(BACKEND_COMMIT, root, manifest_path, hashlib.sha256(raw).hexdigest(), root / ".venv/bin/python", "CPython 3.11.13"),
        CommandManifestAuthority(manifest_path, "3" * 64),
        SemanticAuthority(Path("/etc/trading-agent/research-input-manifests/phase4-v1.json"), "4" * 64),
        SafetyAuthority("a" * 40, Path("/run/user/1000/trading-agent/safety-state.json"), "5" * 64),
        (1, 2), "6" * 64,
    )
    import services.job_worker.command_registry as module

    def fixture_verify_release(*args, **kwargs):
        module._validate_artifact(root, directory=True)
        module._validate_artifact(manifest_path, directory=False)
        observed = module._walk_release(root)
        expected = {entry["path"]: entry for entry in entries}
        if set(observed) != set(expected):
            module._blocked("COMMAND_RELEASE_EXACT_SET_MISMATCH", "fixture release mismatch")
        for relative, entry in expected.items():
            info = observed[relative]
            is_directory = stat.S_ISDIR(info.st_mode)
            if is_directory != (entry["type"] == "directory"):
                module._blocked("COMMAND_RELEASE_TYPE_UNSAFE", "fixture type mismatch")
            if info.st_uid != 0:
                module._blocked("COMMAND_PATH_OWNER_UNSAFE", "fixture owner mismatch")
            if info.st_mode & (0o222 | 0o7000) or format(stat.S_IMODE(info.st_mode), "04o") != entry["mode"]:
                module._blocked("COMMAND_PATH_MODE_UNSAFE", "fixture mode mismatch")
            if not is_directory and (
                info.st_size != entry["size"] or module._sha256_file(root / relative) != entry["sha256"]
            ):
                module._blocked("COMMAND_RELEASE_FILE_NOT_APPROVED", "fixture content mismatch")
        module._validate_artifact(root / ".venv/bin/python", directory=False, executable=True)
        return True

    monkeypatch.setattr("services.job_worker.command_registry._load_runtime_authority", lambda: authority)
    monkeypatch.setattr(
        "services.job_worker.command_registry._attest_release",
        module._attest_release_v1,
    )
    monkeypatch.setattr("services.job_worker.command_registry._verify_release", fixture_verify_release)
    monkeypatch.setattr("services.job_worker.command_registry._read_command_manifest", lambda selected: _command_document(selected))
    monkeypatch.setattr(
        "services.job_worker.command_registry._attest_semantic_authority",
        lambda selected: _semantic_evidence(),
    )
    monkeypatch.setattr("services.job_worker.command_registry._runtime_python_path", lambda: authority.application.python_path)
    monkeypatch.setattr(RuntimeAuthority, "recheck", lambda self: self)
    return root, root / ".venv/bin/python", manifest_path


def _capability(tmp_path: Path, monkeypatch):
    _deployment(tmp_path, monkeypatch)
    return attest_command_capability()


def test_review_constants_pin_root_owned_release_manifest_contract():
    assert APPROVED_BACKEND_REVISION == BACKEND_COMMIT
    assert APPROVED_BACKEND_CWD == Path(f"/opt/trading-agent-phase4/releases/backend-{BACKEND_COMMIT}")
    assert APPROVED_BACKEND_PYTHON == APPROVED_BACKEND_CWD / ".venv/bin/python3.11"
    assert APPROVED_RELEASE_MANIFEST_PATH == Path(f"/opt/trading-agent-phase4/manifests/backend-{BACKEND_COMMIT}.manifest.json")


def test_real_startup_remains_blocked_until_ops_provisions_manifest_and_release():
    with pytest.raises(CommandRegistryError):
        attest_command_capability()


def test_manifest_covers_venv_native_pth_data_config_dot_and_ignored_files(tmp_path, monkeypatch):
    _deployment(tmp_path, monkeypatch)
    assert len(attest_command_capability().fingerprint) == 64


@pytest.mark.parametrize("extra", [".gitignored", ".venv/lib/python3.11/site-packages/extra.pth", ".venv/lib/python3.11/site-packages/evil.so"])
def test_exact_set_walk_rejects_every_extra_file(tmp_path, monkeypatch, extra):
    root, _, _ = _deployment(tmp_path, monkeypatch)
    root.chmod(0o755)
    path = root / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o755)
    path.write_bytes(b"extra")
    path.chmod(0o444)
    for parent in path.parents:
        if parent == root.parent:
            break
        parent.chmod(0o555)
    with pytest.raises(CommandRegistryError) as raised:
        attest_command_capability()
    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"


@pytest.mark.parametrize(("mutation", "reason"), [("missing", "COMMAND_RELEASE_EXACT_SET_MISMATCH"), ("changed", "COMMAND_RELEASE_FILE_NOT_APPROVED"), ("symlink", "COMMAND_RELEASE_SYMLINK")])
def test_exact_set_walk_rejects_missing_changed_and_symlink_entries(tmp_path, monkeypatch, mutation, reason):
    root, _, _ = _deployment(tmp_path, monkeypatch)
    target = root / "config/runtime.json"
    root.chmod(0o755); (root / "config").chmod(0o755); target.chmod(0o644)
    if mutation == "missing":
        target.unlink()
    elif mutation == "changed":
        target.write_bytes(b"changed"); target.chmod(0o444)
    else:
        target.unlink(); target.symlink_to(root / "main.py")
    (root / "config").chmod(0o555); root.chmod(0o555)
    with pytest.raises(CommandRegistryError) as raised:
        attest_command_capability()
    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"


@pytest.mark.parametrize("target", ["ancestor", "root", "manifest", "python", "data"])
def test_attestation_rejects_wrong_owner_and_writable_ancestors_or_artifacts(tmp_path, monkeypatch, target):
    root, python, manifest = _deployment(tmp_path, monkeypatch)
    selected = {"ancestor": root.parent, "root": root, "manifest": manifest, "python": python, "data": root / "data/model.bin"}[target]
    import services.job_worker.command_registry as module
    base = module._lstat
    def unsafe_lstat(path: Path):
        values = list(base(path))
        if path == selected:
            if target == "ancestor": values[0] |= 0o002
            elif target == "root": values[4] = 1000
            else: values[0] |= 0o200
        return os.stat_result(values)
    monkeypatch.setattr(module, "_lstat", unsafe_lstat)
    with pytest.raises(CommandRegistryError) as raised:
        attest_command_capability()
    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"


@pytest.mark.parametrize("target", ["ancestor", "root", "manifest", "python", "data"])
def test_attestation_rejects_special_mode_bits_everywhere(tmp_path, monkeypatch, target):
    root, python, manifest = _deployment(tmp_path, monkeypatch)
    selected = {"ancestor": root.parent, "root": root, "manifest": manifest, "python": python, "data": root / "data/model.bin"}[target]
    import services.job_worker.command_registry as module
    base = module._lstat
    def special_lstat(path: Path):
        values = list(base(path))
        if path == selected: values[0] |= 0o4000
        return os.stat_result(values)
    monkeypatch.setattr(module, "_lstat", special_lstat)
    with pytest.raises(CommandRegistryError) as raised: attest_command_capability()
    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"


@pytest.mark.parametrize("target", ["ancestor", "root", "manifest", "python", "data"])
def test_attestation_rejects_any_extended_attribute_on_protected_paths(tmp_path, monkeypatch, target):
    root, python, manifest = _deployment(tmp_path, monkeypatch)
    selected = {"ancestor": root.parent, "root": root, "manifest": manifest, "python": python, "data": root / "data/model.bin"}[target]
    import services.job_worker.command_registry as module
    monkeypatch.setattr(module, "_listxattr", lambda path: ("security.capability",) if path == selected else ())
    with pytest.raises(CommandRegistryError) as raised: attest_command_capability()
    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"


def test_attestation_rejects_real_xattr_when_filesystem_supports_it(tmp_path, monkeypatch):
    root, _, _ = _deployment(tmp_path, monkeypatch)
    target = root / "data/model.bin"
    target.chmod(0o644)
    try:
        os.setxattr(target, "user.task7-test", b"present", follow_symlinks=False)
    except (AttributeError, OSError):
        pytest.skip("filesystem does not support test xattrs")
    finally:
        target.chmod(0o444)
    assert "user.task7-test" in os.listxattr(target, follow_symlinks=False)
    with pytest.raises(CommandRegistryError) as raised: attest_command_capability()
    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"


def test_attestation_rejects_symlinked_ancestor(tmp_path, monkeypatch):
    root, _, _ = _deployment(tmp_path, monkeypatch)
    import services.job_worker.command_registry as module
    base = module._lstat
    def symlinked_lstat(path: Path):
        values = list(base(path))
        if path == root.parent: values[0] = stat.S_IFLNK | 0o777
        return os.stat_result(values)
    monkeypatch.setattr(module, "_lstat", symlinked_lstat)
    with pytest.raises(CommandRegistryError) as raised: attest_command_capability()
    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"


def test_attestation_has_no_git_subprocess_or_injectable_runner_surface():
    import inspect
    import services.job_worker.command_registry as module
    source = inspect.getsource(module)
    assert "subprocess" not in source
    assert "run_git" not in source
    assert tuple(inspect.signature(attest_command_capability).parameters) == ()


def test_capability_is_opaque_frozen_short_lived_and_single_use(tmp_path, monkeypatch):
    capability = _capability(tmp_path, monkeypatch)
    assert isinstance(capability, ValidatedCommandCapability)
    rendered = repr(capability)
    assert str(tmp_path) not in rendered
    assert "sha256" not in rendered.lower()
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)): capability.fingerprint = "forged"
    job = _job(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None})
    build_command(job, capability)
    with pytest.raises(CommandRegistryError) as reused: build_command(job, capability)
    assert reused.value.reason_code == "COMMAND_CAPABILITY_INVALID"
    expired = _capability(tmp_path / "expired", monkeypatch)
    import services.job_worker.command_registry as module
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: expired._issued_at_ns + module._CAPABILITY_TTL_NS + 1)
    with pytest.raises(CommandRegistryError) as stale: build_command(job, expired)
    assert stale.value.reason_code == "COMMAND_CAPABILITY_EXPIRED"


def test_capability_expiry_is_checked_after_costly_reattestation(tmp_path, monkeypatch):
    capability = _capability(tmp_path, monkeypatch)
    import services.job_worker.command_registry as module
    ticks = iter((capability._issued_at_ns + module._CAPABILITY_TTL_NS - 1, capability._issued_at_ns + module._CAPABILITY_TTL_NS + 1))
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: next(ticks))
    with pytest.raises(CommandRegistryError) as raised:
        build_command(_job(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None}), capability)
    assert raised.value.reason_code == "COMMAND_CAPABILITY_EXPIRED"


def test_full_reattestation_tokens_have_bounded_operational_budget_and_accept_at_deadline(
    tmp_path, monkeypatch,
):
    import services.job_worker.command_registry as module

    minimum_full_scan_budget = 60 * 1_000_000_000
    maximum_authority_lifetime = 5 * 60 * 1_000_000_000
    assert minimum_full_scan_budget <= module._CAPABILITY_TTL_NS <= maximum_authority_lifetime
    assert (
        minimum_full_scan_budget
        <= module._PREPARED_SPAWN_TTL_NS
        <= maximum_authority_lifetime
    )

    capability = _capability(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_attest_release", lambda: capability._attestation)
    capability_ticks = iter((
        capability._issued_at_ns + module._CAPABILITY_TTL_NS - 1,
        capability._issued_at_ns + module._CAPABILITY_TTL_NS,
    ))
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: next(capability_ticks))
    built = build_command(
        _job(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None}),
        capability,
    )
    assert built.argv[-1:] == ("paper_main.py",)

    monkeypatch.undo()
    _deployment(tmp_path / "prepared", monkeypatch)
    prepared = prepare_immediate_spawn(
        _job(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None})
    )
    monkeypatch.setattr(module, "_attest_release", lambda: prepared._attestation)
    prepared_ticks = iter((prepared._deadline_ns - 1, prepared._deadline_ns))
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: next(prepared_ticks))
    assert consume_prepared_spawn(prepared).argv[-1:] == ("paper_main.py",)


def test_build_consumes_then_revalidates_manifest(tmp_path, monkeypatch):
    root, _, _ = _deployment(tmp_path, monkeypatch)
    capability = attest_command_capability()
    target = root / "data/model.bin"
    root.chmod(0o755); (root / "data").chmod(0o755); target.chmod(0o644)
    target.write_bytes(b"changed-after-attestation")
    target.chmod(0o444); (root / "data").chmod(0o555); root.chmod(0o555)
    job = _job(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None})
    with pytest.raises(CommandRegistryError) as raised: build_command(job, capability)
    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"
    with pytest.raises(CommandRegistryError) as reused: build_command(job, capability)
    assert reused.value.reason_code == "COMMAND_CAPABILITY_INVALID"


def test_build_uses_exact_snapshot_only_isolated_argv_and_attested_paths(tmp_path, monkeypatch):
    built = build_command(
        _job(JobType.SNAPSHOT, SnapshotPayload(scope="default", requested_as_of=None)),
        _capability(tmp_path, monkeypatch),
    )
    assert set(COMMAND_REGISTRY) == {JobType.SNAPSHOT}
    assert built.argv == (
        str(built.executable), "-I", "-B", "paper_main.py",
    )
    assert built.cwd == tmp_path / APPROVED_BACKEND_REVISION
    assert built.backend_revision == BACKEND_COMMIT
    assert built.shell is False


@pytest.mark.parametrize(
    ("job_type", "payload"),
    [
        (JobType.DEBATE, DebatePayload(asset="btc", horizon="1d")),
        (JobType.REPLAY, ReplayPayload(session_id="Session_2026-07-12")),
        (
            JobType.BACKTEST,
            BacktestPayload(
                asset="eth", strategy_id="legacy-binary-report-v1",
                date_from=None, date_to=None,
            ),
        ),
    ],
)
def test_inactive_job_types_cannot_be_built(tmp_path, monkeypatch, job_type, payload):
    with pytest.raises(CommandRegistryError) as raised:
        build_command(_job(job_type, payload), _capability(tmp_path, monkeypatch))
    assert raised.value.reason_code == "COMMAND_TYPE_INVALID"


def test_task9_spawn_token_is_opaque_short_lived_and_single_use(tmp_path, monkeypatch):
    _deployment(tmp_path, monkeypatch)
    prepared = prepare_immediate_spawn(_job(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None}))
    assert isinstance(prepared, PreparedSpawn)
    assert not hasattr(prepared, "argv")
    built = consume_prepared_spawn(prepared)
    assert built.argv[1:3] == ("-I", "-B")
    with pytest.raises(CommandRegistryError) as reused: consume_prepared_spawn(prepared)
    assert reused.value.reason_code == "COMMAND_PREPARED_SPAWN_INVALID"

    delayed = prepare_immediate_spawn(_job(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None}))
    import services.job_worker.command_registry as module
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: delayed._deadline_ns + 1)
    with pytest.raises(CommandRegistryError) as expired: consume_prepared_spawn(delayed)
    assert expired.value.reason_code == "COMMAND_PREPARED_SPAWN_EXPIRED"


def test_consume_blocks_runtime_authority_rotation_after_command_preparation(tmp_path, monkeypatch):
    _deployment(tmp_path, monkeypatch)
    prepared = prepare_immediate_spawn(
        _job(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None})
    )
    import services.job_worker.command_registry as module

    rotated = replace(prepared._attestation, authority_document_sha256="f" * 64)
    monkeypatch.setattr(module, "_attest_release", lambda: rotated)

    with pytest.raises(CommandRegistryError) as raised:
        consume_prepared_spawn(prepared)
    assert raised.value.reason_code == "COMMAND_PREPARED_SPAWN_STALE"


def test_consume_blocks_exact_semantic_rotation_after_command_preparation(
    tmp_path, monkeypatch,
):
    _deployment(tmp_path, monkeypatch)
    import services.job_worker.command_registry as module

    evidence = iter(
        (_semantic_evidence(), _semantic_evidence(), _semantic_evidence("semantic-v2"))
    )
    monkeypatch.setattr(module, "_attest_semantic_authority", lambda authority: next(evidence))
    prepared = prepare_immediate_spawn(
        _job(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None})
    )

    with pytest.raises(CommandRegistryError) as raised:
        consume_prepared_spawn(prepared)
    assert raised.value.reason_code == "COMMAND_PREPARED_SPAWN_STALE"


def test_build_rejects_forged_capability_and_untrusted_payload(tmp_path, monkeypatch):
    job = _job(JobType.SNAPSHOT, {"scope": "default", "requested_as_of": None})
    with pytest.raises(CommandRegistryError): build_command(job, ValidatedCommandCapability())
    capability = _capability(tmp_path, monkeypatch)
    with pytest.raises((CommandRegistryError, ValueError)):
        build_command(_job(JobType.DEBATE, {"asset": "BTC;whoami", "horizon": "1d"}), capability)


def test_registry_and_specs_remain_immutable():
    with pytest.raises(TypeError): COMMAND_REGISTRY[JobType.SNAPSHOT] = COMMAND_REGISTRY[JobType.SNAPSHOT]
    with pytest.raises(FrozenInstanceError): COMMAND_REGISTRY[JobType.SNAPSHOT].timeout_seconds = 1
