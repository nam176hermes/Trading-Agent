from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import shutil
import tempfile

import pytest


@pytest.fixture
def tmp_path():
    path = Path(tempfile.mkdtemp(prefix="task2-authority-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


APP_COMMIT = "a" * 40
BACKEND_COMMIT = "b" * 40


def _document() -> dict[str, object]:
    from packages.safety_evidence import CANONICAL_SAFETY_SOURCE_ROOT, safety_source_fingerprint

    base = "/opt/trading-agent-phase4"
    app_root = f"{base}/releases/app-{APP_COMMIT}"
    backend_root = f"{base}/releases/backend-{BACKEND_COMMIT}"
    manifests = f"{base}/manifests"
    return {
        "manifest_version": 1,
        "application": {
            "git_commit": APP_COMMIT,
            "release_root": app_root,
            "manifest_path": f"{manifests}/app-{APP_COMMIT}.manifest.json",
            "manifest_sha256": "1" * 64,
            "python_path": f"{app_root}/.venv/bin/python3.11",
            "python_identity": "CPython 3.11.13",
        },
        "backend": {
            "git_commit": BACKEND_COMMIT,
            "release_root": backend_root,
            "manifest_path": f"{manifests}/backend-{BACKEND_COMMIT}.manifest.json",
            "manifest_sha256": "2" * 64,
            "python_path": f"{backend_root}/.venv/bin/python3.11",
            "python_identity": "CPython 3.11.13",
        },
        "command_manifest": {
            "path": f"{manifests}/commands-{BACKEND_COMMIT}.json",
            "sha256": "3" * 64,
        },
        "semantic": {
            "authority_path": "/etc/trading-agent/research-input-manifests/phase4-v1.json",
            "policy_sha256": "4" * 64,
        },
        "safety": {
            "exporter_commit": APP_COMMIT,
            "snapshot_path": f"/run/user/{os.geteuid()}/trading-agent/safety-state.json",
            "source_fingerprint": safety_source_fingerprint(CANONICAL_SAFETY_SOURCE_ROOT),
        },
    }


def _canonical(document: dict[str, object]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def _install(tmp_path: Path, monkeypatch, raw: bytes | None = None):
    from packages.runtime_release import config

    root = tmp_path / "authority-root"
    root.mkdir(mode=0o755)
    path = root / "phase4-authority.json"
    if raw is not None:
        path.write_bytes(raw)
        path.chmod(0o444)
    monkeypatch.setattr(config, "AUTHORITY_PATH", path)
    monkeypatch.setattr(config, "_EXPECTED_UID", os.getuid())
    monkeypatch.setattr(config, "_EXPECTED_GID", os.getgid())
    monkeypatch.setattr(
        config,
        "_safe_directory",
        lambda metadata: stat.S_ISDIR(metadata.st_mode),
    )
    return path


def _deployment_document(tmp_path, monkeypatch):
    from packages.runtime_release import config
    from packages.runtime_release.semantic import semantic_policy_digest
    from packages.safety_evidence import safety_source_fingerprint

    paths = {}
    for name in ("safety_source_root", "safety_mounted_root", "semantic_reports_root",
                 "semantic_macro_root", "semantic_input_root", "snapshot", "manifests"):
        paths[name] = tmp_path / name
        paths[name].mkdir(mode=0o700)
    document = _document()
    document["manifest_version"] = 2
    document["deployment"] = {
        "deployment_id": "m4-rehearsal",
        **{name: str(paths[name]) for name in (
            "safety_source_root", "safety_mounted_root", "semantic_reports_root",
            "semantic_macro_root", "semantic_input_root")},
        "runtime_uid": os.getuid(), "runtime_gid": os.getgid(),
    }
    document["safety"]["snapshot_path"] = str(paths["snapshot"] / "safety.json")
    document["safety"]["source_fingerprint"] = safety_source_fingerprint(paths["safety_source_root"])
    manifest = paths["manifests"] / "active.json"
    document["semantic"] = {
        "authority_path": str(manifest),
        "policy_sha256": semantic_policy_digest(BACKEND_COMMIT, manifest, input_root=paths["semantic_input_root"]),
    }
    # Only exempt the shared test tmp ancestor; leaf/other ancestor rules remain real.
    temporary = Path(tempfile.gettempdir()).stat()
    original = config._safe_deployment_ancestor
    monkeypatch.setattr(config, "_safe_deployment_ancestor", lambda info, uid:
        (info.st_dev, info.st_ino) == (temporary.st_dev, temporary.st_ino) or original(info, uid))
    return document, paths


@pytest.mark.parametrize("root_name", ["first", "second"])
def test_phase4_deployment_binding_is_issued_and_rechecked(tmp_path, monkeypatch, root_name):
    from dataclasses import replace
    from packages.runtime_release import config

    root = tmp_path / root_name
    root.mkdir()
    document, paths = _deployment_document(root, monkeypatch)
    path = _install(root, monkeypatch, _canonical(document))
    authority = config.load_runtime_authority()
    binding = authority.require_deployment()
    assert binding.safety_source_root == paths["safety_source_root"]
    assert authority.semantic.input_root == paths["semantic_input_root"]
    assert authority.recheck() is authority
    with pytest.raises(config.ProtectedAuthorityError):
        replace(authority, deployment=replace(binding)).require_deployment()
    with pytest.raises(config.ProtectedAuthorityError):
        replace(authority, safety=replace(authority.safety, source_fingerprint="0" * 64)).recheck()
    path.chmod(0o644)
    document["deployment"]["deployment_id"] = "replacement"
    path.write_bytes(_canonical(document))
    path.chmod(0o444)
    with pytest.raises(config.ProtectedAuthorityError):
        authority.recheck()


@pytest.mark.parametrize("fault", ["bool_version", "unknown_version", "uid_bool", "gid_negative",
    "relative", "alias", "overlap", "fingerprint", "policy", "symlink", "writable"])
def test_phase4_deployment_binding_rejects_invalid_scope(tmp_path, monkeypatch, fault):
    from packages.runtime_release import config

    document, paths = _deployment_document(tmp_path, monkeypatch)
    if fault == "bool_version": document["manifest_version"] = True
    elif fault == "unknown_version": document["manifest_version"] = 3
    elif fault == "uid_bool": document["deployment"]["runtime_uid"] = True
    elif fault == "gid_negative": document["deployment"]["runtime_gid"] = -1
    elif fault == "relative": document["deployment"]["safety_source_root"] = "relative"
    elif fault == "alias": document["deployment"]["safety_source_root"] += "/../safety_source_root"
    elif fault == "overlap": document["deployment"]["semantic_input_root"] = str(paths["semantic_reports_root"])
    elif fault == "fingerprint": document["safety"]["source_fingerprint"] = "0" * 64
    elif fault == "policy": document["semantic"]["policy_sha256"] = "0" * 64
    elif fault == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(paths["safety_mounted_root"], target_is_directory=True)
        document["deployment"]["safety_mounted_root"] = str(alias)
    elif fault == "writable": paths["safety_source_root"].chmod(0o777)
    _install(tmp_path, monkeypatch, _canonical(document))
    with pytest.raises(config.ProtectedAuthorityError):
        authority = config.load_runtime_authority()
        authority.recheck(deployment_role="exporter" if fault == "symlink" else "operator")


def test_m4_producer_consumer_and_operator_share_protected_source(tmp_path, monkeypatch):
    from packages.runtime_release import config
    from services.safety_state_exporter.main import build_exporter
    from services.safety_state.provider import authority_bound_safety_provider
    from services.operator_control.composition import OperatorControlRuntimeSettings, build_production_operator_control_service
    from tests.hwc.test_operator_service import actor

    document, paths = _deployment_document(tmp_path, monkeypatch)
    path = _install(tmp_path, monkeypatch, _canonical(document))
    mode = paths["safety_mounted_root"] / ".mode"
    mode.write_text("paper")
    mode.chmod(0o600)
    exporter = build_exporter({"TRADING_SAFETY_EXPORTER_COMMIT": APP_COMMIT,
        "LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"})
    reader = authority_bound_safety_provider()
    service = build_production_operator_control_service(OperatorControlRuntimeSettings())
    assert service.state_store.paths.data_root == paths["safety_source_root"]
    exporter.export_once()
    before = exporter.output_path.read_bytes()
    assert reader().snapshot_sha256 == hashlib.sha256(before).hexdigest()
    assert json.loads(before)["source_fingerprint"] == document["safety"]["source_fingerprint"]
    from services.job_worker.errors import SafetyBlockedError
    from packages.safety_evidence import CANONICAL_SAFETY_SOURCE_ROOT, safety_source_fingerprint
    stale = json.loads(before)
    stale["source_fingerprint"] = safety_source_fingerprint(CANONICAL_SAFETY_SOURCE_ROOT)
    exporter.output_path.write_text(json.dumps(stale, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(SafetyBlockedError): reader()
    exporter.output_path.write_bytes(before)
    path.chmod(0o644)
    document["deployment"]["deployment_id"] = "changed"
    path.write_bytes(_canonical(document))
    path.chmod(0o444)
    for operation in (exporter.export_once, reader, lambda: service.read_state(actor())):
        with pytest.raises(config.ProtectedAuthorityError): operation()
    assert exporter.output_path.read_bytes() == before
    assert not (paths["safety_source_root"] / ".operator-commands").exists()


def test_m4_semantic_plan_cannot_apply_after_binding_changes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from packages.runtime_release import config
    from services.semantic_input_refresher import main
    from tests.runtime_release.test_semantic_refresher import _fixture_sources, NOW

    document, paths = _deployment_document(tmp_path, monkeypatch)
    path = _install(tmp_path, monkeypatch, _canonical(document))
    source_reports, source_macro = _fixture_sources(tmp_path / "fixtures")
    for source, target in ((source_reports, paths["semantic_reports_root"]), (source_macro, paths["semantic_macro_root"])):
        for item in source.iterdir(): shutil.copy2(item, target / item.name)
    calls = []
    def builder(**kwargs):
        calls.append(kwargs)
        assert kwargs["destination_root"] == paths["semantic_input_root"]
        assert kwargs["manifest_path"] == Path(document["semantic"]["authority_path"])
        assert kwargs["backend_commit"] == BACKEND_COMMIT
        assert (kwargs["runtime_uid"], kwargs["runtime_gid"]) == (os.getuid(), os.getgid())
        kwargs["authority_recheck"]()
        path.chmod(0o644)
        document["deployment"]["deployment_id"] = "changed"
        path.write_bytes(_canonical(document))
        path.chmod(0o444)
        return SimpleNamespace(plan_digest="a" * 64)
    monkeypatch.setattr(main, "build_semantic_manifest", builder)
    monkeypatch.setattr(main.os, "geteuid", lambda: 0)
    with pytest.raises(config.ProtectedAuthorityError): main.refresh(clock=lambda: NOW, apply=True)
    assert len(calls) == 1 and calls[0]["apply"] is False


def test_m4_exporter_does_not_require_unmounted_private_sources(tmp_path, monkeypatch):
    from packages.runtime_release import config
    from services.safety_state_exporter.main import build_exporter

    document, paths = _deployment_document(tmp_path, monkeypatch)
    _install(tmp_path, monkeypatch, _canonical(document))
    for name in ("safety_source_root", "semantic_reports_root", "semantic_macro_root",
                 "semantic_input_root", "manifests"):
        paths[name].rmdir()
    exporter = build_exporter({"TRADING_SAFETY_EXPORTER_COMMIT": APP_COMMIT,
        "LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"})
    exporter.export_once()
    assert exporter.output_path.is_file()
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority().recheck(deployment_role="operator")


@pytest.mark.parametrize("producer", ["safety", "semantic", "operator"])
def test_m4_compositions_never_fall_back_without_protected_authority(tmp_path, monkeypatch, producer):
    from packages.runtime_release import config
    _install(tmp_path, monkeypatch)
    with pytest.raises(config.ProtectedAuthorityError):
        if producer == "safety":
            from services.safety_state_exporter.main import build_exporter
            build_exporter({"TRADING_SAFETY_EXPORTER_COMMIT": APP_COMMIT})
        elif producer == "semantic":
            from services.semantic_input_refresher.main import refresh
            refresh()
        else:
            from services.operator_control.composition import OperatorControlRuntimeSettings, build_production_operator_control_service
            build_production_operator_control_service(OperatorControlRuntimeSettings())


def test_valid_exact_authority_is_descriptor_anchored_and_recheckable(tmp_path, monkeypatch):
    from packages.runtime_release.config import load_runtime_authority

    _install(tmp_path, monkeypatch, _canonical(_document()))
    authority = load_runtime_authority()

    assert authority.application.git_commit == APP_COMMIT
    assert authority.backend.git_commit == BACKEND_COMMIT
    assert authority.recheck() is authority


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b'{"manifest_version":1,"manifest_version":1}\n',
        _canonical({**_document(), "unexpected": True}),
        json.dumps(_document(), indent=2).encode() + b"\n",
        b" " * (64 * 1024 + 1),
    ],
    ids=["malformed", "duplicate-key", "extra-key", "noncanonical", "oversized"],
)
def test_invalid_authority_documents_fail_closed_without_disclosure(tmp_path, monkeypatch, raw):
    from packages.runtime_release.config import ProtectedAuthorityError, load_runtime_authority

    path = _install(tmp_path, monkeypatch, raw)
    with pytest.raises(ProtectedAuthorityError) as raised:
        load_runtime_authority()

    rendered = repr(raised.value) + str(raised.value)
    assert str(path) not in rendered
    assert "1" * 64 not in rendered


def test_missing_writable_symlink_and_misowned_authority_fail_closed(tmp_path, monkeypatch):
    from packages.runtime_release import config

    path = _install(tmp_path, monkeypatch)
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()

    path.write_bytes(_canonical(_document()))
    path.chmod(0o644)
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()

    path.unlink()
    target = path.with_suffix(".target")
    target.write_bytes(_canonical(_document()))
    target.chmod(0o444)
    path.symlink_to(target)
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()

    path.unlink()
    path.write_bytes(_canonical(_document()))
    path.chmod(0o444)
    monkeypatch.setattr(config, "_EXPECTED_UID", os.getuid() + 1)
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()


def test_symlinked_or_writable_authority_ancestor_fails_closed(tmp_path, monkeypatch):
    from packages.runtime_release import config

    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    file = real / "phase4-authority.json"
    file.write_bytes(_canonical(_document()))
    file.chmod(0o444)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(config, "AUTHORITY_PATH", alias / file.name)
    monkeypatch.setattr(config, "_EXPECTED_UID", os.getuid())
    monkeypatch.setattr(config, "_EXPECTED_GID", os.getgid())
    monkeypatch.setattr(config, "_safe_directory", lambda metadata: stat.S_ISDIR(metadata.st_mode))
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()

    monkeypatch.setattr(config, "AUTHORITY_PATH", file)
    selected = real.stat().st_ino
    monkeypatch.setattr(
        config,
        "_safe_directory",
        lambda metadata: stat.S_ISDIR(metadata.st_mode) and metadata.st_ino != selected,
    )
    with pytest.raises(config.ProtectedAuthorityError):
        config.load_runtime_authority()

@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("application", "git_commit", "f" * 39),
        ("backend", "release_root", "/opt/trading-agent-phase4/releases/backend-wrong"),
        ("application", "python_identity", "Python 3.11.13"),
        ("backend", "manifest_sha256", "A" * 64),
        ("command_manifest", "sha256", "x" * 64),
        ("semantic", "authority_path", "/tmp/semantic.json"),
        ("safety", "snapshot_path", "/tmp/safety.json"),
        ("safety", "snapshot_path", f"/run/user/{os.geteuid() + 1}/trading-agent/safety-state.json"),
        ("safety", "source_fingerprint", "0" * 63),
        ("safety", "source_fingerprint", "5" * 64),
        ("safety", "exporter_commit", "c" * 40),
    ],
)
def test_wrong_commit_path_python_digest_semantic_or_safety_binding_is_rejected(
    tmp_path, monkeypatch, section, field, value
):
    from packages.runtime_release.config import ProtectedAuthorityError, load_runtime_authority

    document = _document()
    document[section][field] = value  # type: ignore[index]
    _install(tmp_path, monkeypatch, _canonical(document))

    with pytest.raises(ProtectedAuthorityError):
        load_runtime_authority()


def test_authority_never_accepts_secret_or_environment_fields(tmp_path, monkeypatch):
    from packages.runtime_release.config import ProtectedAuthorityError, load_runtime_authority

    document = _document()
    document["job_api_token"] = "secret"
    _install(tmp_path, monkeypatch, _canonical(document))
    with pytest.raises(ProtectedAuthorityError):
        load_runtime_authority()


def test_safety_path_exactly_matches_exporter_fixed_runtime_path(tmp_path, monkeypatch):
    from packages.runtime_release.config import load_runtime_authority
    from services.safety_state_exporter.exporter import DEFAULT_SNAPSHOT_PATH

    _install(tmp_path, monkeypatch, _canonical(_document()))
    assert load_runtime_authority().safety.snapshot_path == DEFAULT_SNAPSHOT_PATH


def test_recheck_rejects_inode_or_content_rotation(tmp_path, monkeypatch):
    from packages.runtime_release.config import ProtectedAuthorityError, load_runtime_authority

    path = _install(tmp_path, monkeypatch, _canonical(_document()))
    authority = load_runtime_authority()
    replacement = path.with_suffix(".new")
    replacement.write_bytes(_canonical(_document()))
    replacement.chmod(0o444)
    replacement.replace(path)

    with pytest.raises(ProtectedAuthorityError):
        authority.recheck()


def test_application_attestation_rejects_wrong_running_interpreter(monkeypatch):
    from packages.runtime_release import config

    authority = object.__new__(config.RuntimeAuthority)
    release = config.ReleaseAuthority(
        APP_COMMIT,
        Path(f"/opt/trading-agent-phase4/releases/app-{APP_COMMIT}"),
        Path(f"/opt/trading-agent-phase4/manifests/app-{APP_COMMIT}.manifest.json"),
        "1" * 64,
        Path(f"/opt/trading-agent-phase4/releases/app-{APP_COMMIT}/.venv/bin/python3.11"),
        "CPython 3.11.13",
    )
    object.__setattr__(authority, "application", release)
    monkeypatch.setattr(config, "load_runtime_authority", lambda: authority)
    monkeypatch.setattr(config, "verify_release", lambda *args, **kwargs: True)
    monkeypatch.setattr(config, "_runtime_python_path", lambda: Path("/usr/bin/python3.11"), raising=False)

    with pytest.raises(config.ProtectedAuthorityError) as raised:
        config.attest_application_authority()
    assert raised.value.reason_code == "APPLICATION_RELEASE_INVALID"
