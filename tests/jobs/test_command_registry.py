from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import FrozenInstanceError, dataclass, field, replace
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
    path = Path(tempfile.mkdtemp(prefix="task7-command-"))
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


@dataclass
class _FixtureIdentityEvidence:
    """Record the real fixture identity before modeling only its root uid."""

    release_root: Path
    manifest_path: Path
    release_entries: tuple[Path, ...]
    _declared_paths: frozenset[Path] = field(init=False, repr=False)
    _injected_xattrs: dict[Path, tuple[str, ...]] = field(default_factory=dict, repr=False)
    _actual_xattr_paths: set[Path] = field(default_factory=set, repr=False)
    _observed_lstats: dict[Path, os.stat_result] = field(default_factory=dict, repr=False)
    _observed_xattrs: dict[Path, tuple[str, ...]] = field(default_factory=dict, repr=False)
    _lstat_paths: list[str] = field(default_factory=list, repr=False)
    _xattr_paths: list[str] = field(default_factory=list, repr=False)
    _diagnostic: dict[str, object] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        declared: set[Path] = set()
        for path in (self.release_root, self.manifest_path, *self.release_entries):
            current = path.absolute()
            while True:
                declared.add(current)
                if current == Path(current.anchor):
                    break
                current = current.parent
        self._declared_paths = frozenset(declared)

    def _path(self, value: Path) -> Path:
        return value.absolute()

    def _redact(self, path: Path) -> str:
        if path == Path(path.anchor):
            return "<filesystem-root>"
        if path == Path("/tmp"):
            return "<tmp-root>"
        if path == self.manifest_path.absolute():
            return "<release-manifest>"
        try:
            path.relative_to(self.release_root.absolute())
        except ValueError:
            current = self.release_root.absolute().parent
            index = 0
            while True:
                if path == current:
                    return f"<fixture-ancestor-{index}>"
                if current == Path(current.anchor):
                    break
                current = current.parent
                index += 1
            return "<declared-ancestor>"
        return "<release-root>" if path == self.release_root.absolute() else "<release-entry>"

    def lstat(self, value: Path) -> os.stat_result:
        path = self._path(value)
        real = path.lstat()
        self._observed_lstats[path] = real
        self._lstat_paths.append(self._redact(path))
        if path not in self._declared_paths:
            self.record_fixture_boundary()
            raise AssertionError("command fixture inspected a path outside its declared identity")
        values = list(real)
        values[4] = 0
        return os.stat_result(values, real.__reduce__()[1][1])

    def listxattr(self, value: Path) -> tuple[str, ...]:
        path = self._path(value)
        observed = tuple(os.listxattr(path, follow_symlinks=False))
        self._observed_xattrs[path] = observed
        self._xattr_paths.append(self._redact(path))
        if path not in self._declared_paths:
            self.record_fixture_boundary()
            raise AssertionError("command fixture inspected xattrs outside its declared identity")
        if path in self._injected_xattrs:
            return self._injected_xattrs[path]
        if path in self._actual_xattr_paths:
            return observed
        return ()

    def inject_xattrs(self, value: Path, attributes: tuple[str, ...]) -> None:
        path = self._path(value)
        if path not in self._declared_paths:
            raise AssertionError("command fixture injected xattrs outside its declared identity")
        self._injected_xattrs[path] = attributes

    def require_actual_xattrs(self, value: Path) -> None:
        path = self._path(value)
        if path not in self._declared_paths:
            raise AssertionError("command fixture requested actual xattrs outside its declared identity")
        self._actual_xattr_paths.add(path)

    def assert_complete_observation(self) -> None:
        if set(self._observed_lstats) != set(self._declared_paths):
            raise AssertionError("command fixture did not physically observe every declared lstat path")
        if set(self._observed_xattrs) != set(self._declared_paths):
            raise AssertionError("command fixture did not physically observe every declared xattr path")

    def record_rejection(self, error: CommandRegistryError) -> None:
        reason = error.reason_code
        if "XATTR" in reason:
            category, outcome = "xattr", "present_or_unreadable"
        elif reason == "COMMAND_RELEASE_EXACT_SET_MISMATCH":
            category, outcome = "manifest_exact_set", "mismatch"
        elif reason == "COMMAND_RELEASE_FILE_NOT_APPROVED":
            category, outcome = "digest", "mismatch"
        elif reason in {
            "COMMAND_ANCESTOR_OWNER_UNSAFE",
            "COMMAND_PATH_OWNER_UNSAFE",
        }:
            category, outcome = "lstat", "ownership"
        elif reason in {
            "COMMAND_ANCESTOR_MODE_UNSAFE",
            "COMMAND_PATH_MODE_UNSAFE",
            "COMMAND_EXECUTABLE_MISSING",
        }:
            category, outcome = "lstat", "mode"
        elif reason in {
            "COMMAND_ANCESTOR_SYMLINK",
            "COMMAND_RELEASE_SYMLINK",
            "COMMAND_RELEASE_TYPE_UNSAFE",
        }:
            category, outcome = "lstat", "type"
        elif reason in {
            "COMMAND_ANCESTOR_MISSING",
            "COMMAND_CWD_MISSING",
            "COMMAND_ARTIFACT_MISSING",
            "COMMAND_PATH_UNREADABLE",
            "COMMAND_RELEASE_UNREADABLE",
            "COMMAND_RELEASE_FILE_UNREADABLE",
        }:
            category, outcome = "race", "missing_or_unreadable"
        else:
            category, outcome = "unclassified", "unexpected_reason"
        self._diagnostic = {
            "category": category,
            "outcome": outcome,
            "reason_code": reason,
            "lstat_paths": tuple(self._lstat_paths),
            "xattr_paths": tuple(self._xattr_paths),
        }

    def record_fixture_boundary(self) -> None:
        self._diagnostic = {
            "category": "fixture_boundary",
            "outcome": "undeclared_path",
            "reason_code": "FIXTURE_IDENTITY_UNDECLARED_PATH",
            "lstat_paths": tuple(self._lstat_paths),
            "xattr_paths": tuple(self._xattr_paths),
        }

    def diagnostic(self) -> dict[str, object]:
        if self._diagnostic is not None:
            return self._diagnostic
        return {
            "category": "verified",
            "outcome": "no_rejection",
            "reason_code": None,
            "lstat_paths": tuple(self._lstat_paths),
            "xattr_paths": tuple(self._xattr_paths),
        }


def _deployment(tmp_path: Path, monkeypatch, *, diagnostics: list[_FixtureIdentityEvidence] | None = None):
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
    evidence = _FixtureIdentityEvidence(
        root,
        manifest_path,
        tuple(root / str(entry["path"]) for entry in entries),
    )
    if diagnostics is not None:
        diagnostics.append(evidence)
    monkeypatch.setattr("services.job_worker.command_registry._lstat", evidence.lstat)
    monkeypatch.setattr("services.job_worker.command_registry._listxattr", evidence.listxattr)
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
        try:
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
            evidence.assert_complete_observation()
            return True
        except CommandRegistryError as error:
            evidence.record_rejection(error)
            raise
        except AssertionError:
            evidence.record_fixture_boundary()
            raise

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


def test_deployment_identity_recorder_exposes_lstat_mode_before_outer_collapse(
    tmp_path, monkeypatch,
):
    diagnostics = []
    root, _, _ = _deployment(tmp_path, monkeypatch, diagnostics=diagnostics)
    import services.job_worker.command_registry as module

    base = module._lstat

    def mode_unsafe_lstat(path: Path) -> os.stat_result:
        values = list(base(path))
        if Path(path).absolute() == root.parent.absolute():
            values[0] |= stat.S_IWOTH
        return os.stat_result(values)

    monkeypatch.setattr(module, "_lstat", mode_unsafe_lstat)

    with pytest.raises(CommandRegistryError) as raised:
        _attest_fixture_capability(diagnostics[0])

    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"
    diagnostic = diagnostics[0].diagnostic()
    assert diagnostic["category"] == "lstat"
    assert diagnostic["outcome"] == "mode"
    assert diagnostic["reason_code"] == "COMMAND_ANCESTOR_MODE_UNSAFE"
    assert diagnostic["lstat_paths"][0] == "<filesystem-root>"
    assert all(path.startswith("<") and path.endswith(">") for path in diagnostic["lstat_paths"])
    assert all(path.startswith("<") and path.endswith(">") for path in diagnostic["xattr_paths"])
    assert str(tmp_path) not in json.dumps(diagnostic, sort_keys=True)
    note = raised.value.__notes__[-1]
    assert note.startswith("fixture identity diagnostic (redacted): ")
    assert json.loads(note.removeprefix("fixture identity diagnostic (redacted): ")) == json.loads(
        json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
    )
    assert str(tmp_path) not in note


def test_deployment_identity_recorder_rejects_an_undeclared_existing_path(
    tmp_path, monkeypatch,
):
    diagnostics = []
    _deployment(tmp_path, monkeypatch, diagnostics=diagnostics)
    outside = tmp_path.parent / f"undeclared-{tmp_path.name}"
    outside.mkdir()

    with pytest.raises(AssertionError, match="outside its declared identity"):
        diagnostics[0].lstat(outside)
    assert outside.absolute() in diagnostics[0]._observed_lstats

    with pytest.raises(AssertionError, match="outside its declared identity"):
        diagnostics[0].listxattr(outside)
    assert outside.absolute() in diagnostics[0]._observed_xattrs
    diagnostic = diagnostics[0].diagnostic()
    assert diagnostic["category"] == "fixture_boundary"
    assert diagnostic["outcome"] == "undeclared_path"
    assert str(outside) not in json.dumps(diagnostic, sort_keys=True)


def test_deployment_identity_records_real_declared_ancestor_xattrs_before_normal_view(
    tmp_path, monkeypatch,
):
    diagnostics = []
    root, _, _ = _deployment(tmp_path, monkeypatch, diagnostics=diagnostics)
    evidence = diagnostics[0]
    ancestor = root.parent.absolute()
    observed_attribute = "user.c42-controlled-ancestor"
    real_listxattr = os.listxattr

    def controlled_listxattr(path, *, follow_symlinks=True):
        if Path(path).absolute() == ancestor:
            return [observed_attribute]
        return real_listxattr(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "listxattr", controlled_listxattr)

    assert evidence.listxattr(ancestor) == ()
    assert evidence._observed_xattrs[ancestor] == (observed_attribute,)
    diagnostic = evidence.diagnostic()
    assert diagnostic["xattr_paths"] == ("<fixture-ancestor-0>",)
    rendered = json.dumps(diagnostic, sort_keys=True)
    assert str(ancestor) not in rendered
    assert observed_attribute not in rendered


@pytest.mark.parametrize(
    ("reason", "category", "outcome"),
    [
        ("COMMAND_PATH_OWNER_UNSAFE", "lstat", "ownership"),
        ("COMMAND_PATH_MODE_UNSAFE", "lstat", "mode"),
        ("COMMAND_RELEASE_TYPE_UNSAFE", "lstat", "type"),
        ("COMMAND_PATH_XATTR_UNSAFE", "xattr", "present_or_unreadable"),
        ("COMMAND_RELEASE_EXACT_SET_MISMATCH", "manifest_exact_set", "mismatch"),
        ("COMMAND_RELEASE_FILE_NOT_APPROVED", "digest", "mismatch"),
        ("COMMAND_RELEASE_UNREADABLE", "race", "missing_or_unreadable"),
    ],
)
def test_deployment_identity_recorder_keeps_inner_rejection_categories_distinct(
    tmp_path, monkeypatch, reason, category, outcome,
):
    diagnostics = []
    _deployment(tmp_path, monkeypatch, diagnostics=diagnostics)
    diagnostics[0].record_rejection(CommandRegistryError(reason, "fixture diagnostic"))

    diagnostic = diagnostics[0].diagnostic()
    assert diagnostic["category"] == category
    assert diagnostic["outcome"] == outcome
    assert diagnostic["reason_code"] == reason


def _attest_fixture_capability(evidence: _FixtureIdentityEvidence):
    """Retain redacted fixture evidence when v1 intentionally collapses it."""

    try:
        return attest_command_capability()
    except CommandRegistryError as error:
        diagnostic = evidence.diagnostic()
        assert error.reason_code == "COMMAND_RELEASE_NOT_APPROVED"
        assert diagnostic["category"] != "verified"
        assert diagnostic["outcome"] != "no_rejection"
        assert diagnostic["reason_code"] is not None
        assert all(
            path.startswith("<") and path.endswith(">")
            for path in (*diagnostic["lstat_paths"], *diagnostic["xattr_paths"])
        )
        error.add_note(
            "fixture identity diagnostic (redacted): "
            + json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
        )
        raise


def _capability(tmp_path: Path, monkeypatch):
    diagnostics = []
    _deployment(tmp_path, monkeypatch, diagnostics=diagnostics)
    return _attest_fixture_capability(diagnostics[0])


def test_review_constants_pin_root_owned_release_manifest_contract():
    assert APPROVED_BACKEND_REVISION == BACKEND_COMMIT
    assert APPROVED_BACKEND_CWD == Path(f"/opt/trading-agent-phase4/releases/backend-{BACKEND_COMMIT}")
    assert APPROVED_BACKEND_PYTHON == APPROVED_BACKEND_CWD / ".venv/bin/python3.11"
    assert APPROVED_RELEASE_MANIFEST_PATH == Path(f"/opt/trading-agent-phase4/manifests/backend-{BACKEND_COMMIT}.manifest.json")


def test_real_startup_remains_blocked_until_ops_provisions_manifest_and_release():
    with pytest.raises(CommandRegistryError):
        attest_command_capability()


def test_manifest_covers_venv_native_pth_data_config_dot_and_ignored_files(tmp_path, monkeypatch):
    diagnostics = []
    root, _, _ = _deployment(tmp_path, monkeypatch, diagnostics=diagnostics)
    assert len(_attest_fixture_capability(diagnostics[0]).fingerprint) == 64
    evidence = diagnostics[0]
    evidence.assert_complete_observation()
    for path, actual in tuple(evidence._observed_lstats.items()):
        viewed = evidence.lstat(path)
        assert stat.S_IFMT(viewed.st_mode) == stat.S_IFMT(actual.st_mode)
        assert stat.S_IMODE(viewed.st_mode) == stat.S_IMODE(actual.st_mode)
        assert viewed.st_size == actual.st_size
        assert viewed.st_dev == actual.st_dev
        assert viewed.st_ino == actual.st_ino
        assert viewed.st_gid == actual.st_gid
        assert viewed.st_nlink == actual.st_nlink
        assert viewed.st_atime_ns == actual.st_atime_ns
        assert viewed.st_mtime_ns == actual.st_mtime_ns
        assert viewed.st_ctime_ns == actual.st_ctime_ns
        assert viewed.st_blksize == actual.st_blksize
        assert viewed.st_blocks == actual.st_blocks
        assert viewed.st_rdev == actual.st_rdev
        assert viewed.st_uid == 0
    target = root / "data/model.bin"
    assert target.read_bytes() == b"model-data"


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


@pytest.mark.parametrize(
    ("target", "inner_reason"),
    [
        ("ancestor", "COMMAND_ANCESTOR_XATTR_UNSAFE"),
        ("root", "COMMAND_PATH_XATTR_UNSAFE"),
        ("manifest", "COMMAND_PATH_XATTR_UNSAFE"),
        ("python", "COMMAND_PATH_XATTR_UNSAFE"),
        ("data", "COMMAND_PATH_XATTR_UNSAFE"),
    ],
)
def test_attestation_rejects_any_extended_attribute_on_protected_paths(
    tmp_path, monkeypatch, target, inner_reason,
):
    diagnostics = []
    root, python, manifest = _deployment(tmp_path, monkeypatch, diagnostics=diagnostics)
    selected = {"ancestor": root.parent, "root": root, "manifest": manifest, "python": python, "data": root / "data/model.bin"}[target]
    diagnostics[0].inject_xattrs(selected, ("security.capability",))
    with pytest.raises(CommandRegistryError) as raised: attest_command_capability()
    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"
    diagnostic = diagnostics[0].diagnostic()
    assert diagnostic["category"] == "xattr"
    assert diagnostic["outcome"] == "present_or_unreadable"
    assert diagnostic["reason_code"] == inner_reason
    assert diagnostic["xattr_paths"]
    assert all(
        path.startswith("<") and path.endswith(">")
        for path in (*diagnostic["lstat_paths"], *diagnostic["xattr_paths"])
    )
    rendered = json.dumps(diagnostic, sort_keys=True)
    assert str(tmp_path) not in rendered
    assert "security.capability" not in rendered


def test_attestation_rejects_real_xattr_when_filesystem_supports_it(tmp_path, monkeypatch):
    diagnostics = []
    root, _, _ = _deployment(tmp_path, monkeypatch, diagnostics=diagnostics)
    evidence = diagnostics[0]
    target = root / "data/model.bin"
    target.chmod(0o644)
    try:
        os.setxattr(target, "user.task7-test", b"present", follow_symlinks=False)
    except (AttributeError, OSError):
        pytest.skip("filesystem does not support test xattrs")
    finally:
        target.chmod(0o444)
    assert "user.task7-test" in os.listxattr(target, follow_symlinks=False)
    evidence.require_actual_xattrs(target)
    with pytest.raises(CommandRegistryError) as raised: attest_command_capability()
    assert raised.value.reason_code == "COMMAND_RELEASE_NOT_APPROVED"
    assert "user.task7-test" in evidence._observed_xattrs[target]
    assert evidence.diagnostic()["reason_code"] == "COMMAND_PATH_XATTR_UNSAFE"


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
