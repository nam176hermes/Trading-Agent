from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, cast

import pytest

from packages.runtime_release.v2 import (
    PAPER_ARTIFACT_CLASS,
    PAPER_APPLICATION_SOURCE_MAPPING,
    PAPER_APPLICATION_SOURCE_PATHS,
    PAPER_BACKEND_SOURCE_MAPPING,
    PAPER_BACKEND_SOURCE_PATHS,
    PAPER_PYTHON_RUNTIME_PROVENANCE,
    PAPER_UV_PROVENANCE,
    ReleaseAuthorityV2Error,
    build_release_activation_v2,
    build_static_release_authority_v2,
    canonical_json_bytes,
    construct_pinned_uv_tool,
    construct_python_runtime,
    install_paper_application_import_path,
    parse_release_activation_v2,
    parse_static_release_authority_v2,
    python_runtime_core_sha256,
    render_candidate_units,
    verify_static_release_authority_v2,
    _artifact_digest,
    _authority_binding,
    _fragment,
    _sha256_bytes,
    _walk_sealed_stage,
)


ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "ops/release-v2/verify-stage.py"
PRIOR = hashlib.sha256(b"{}\n").hexdigest()


@pytest.fixture
def tmp_path() -> Path:
    """Use the Linux filesystem so mode, link, owner, and xattr checks are real."""

    path = Path(tempfile.mkdtemp(prefix="release-v2-test-", dir="/tmp"))
    try:
        yield path
    finally:
        for item in sorted(path.rglob("*"), key=lambda child: len(child.parts), reverse=True):
            if not item.is_symlink():
                try:
                    item.chmod(0o755 if item.is_dir() else 0o644)
                except OSError:
                    pass
        path.chmod(0o755)
        shutil.rmtree(path, ignore_errors=True)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, data: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    path.chmod(mode)


def _test_runtime_source(root: Path) -> tuple[Path, str]:
    source = root / "fixture-runtime-source"
    launcher = """#!/bin/sh
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
printf '{"identity":"CPython 3.11.15","prefixes":["%s","%s","%s","%s","%s/lib/python3.11"]}\n' \
  "$root" "$root" "$root" "$root" "$root"
"""
    _write(source / "bin/python3.11", launcher, 0o755)
    _write(source / "lib/python3.11/os.py", "name = 'posix'\n")
    digest = python_runtime_core_sha256(source, allow_internal_source_links=True)
    return source, digest


def _test_runtime_core(stage: Path) -> str:
    return python_runtime_core_sha256(stage / "application/.venv")


def _seal(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
    root.chmod(0o555)


def _git_object(kind: str, raw: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode("ascii") + raw).hexdigest()


def _source_proof(stage: Path) -> dict[str, object]:
    application_sources = dict(PAPER_APPLICATION_SOURCE_MAPPING)
    backend_sources = dict(PAPER_BACKEND_SOURCE_MAPPING)
    tracked_stage_paths = [
        *(f"application/{path}" for path in PAPER_APPLICATION_SOURCE_PATHS),
        *(f"backend/{path}" for path in PAPER_BACKEND_SOURCE_PATHS),
    ]
    entries: list[dict[str, object]] = []
    tree: dict[str, object] = {}
    for stage_path in tracked_stage_paths:
        if stage_path.startswith("backend/"):
            source_path = backend_sources[stage_path.removeprefix("backend/")]
        else:
            source_path = application_sources[stage_path.removeprefix("application/")]
        path = stage / stage_path
        raw = path.read_bytes()
        mode = "100755" if path.stat().st_mode & 0o111 else "100644"
        blob = _git_object("blob", raw)
        entries.append(
            {
                "git_blob": blob,
                "mode": mode,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "source_path": source_path,
                "stage_path": stage_path,
            }
        )
        current = tree
        parts = source_path.split("/")
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = (mode, blob)

    excluded_raw = b"raise SystemExit('legacy live archive')\n"
    excluded_source_path = "legacy/research-backend/main.py"
    excluded_blob = _git_object("blob", excluded_raw)
    entries.append(
        {
            "git_blob": excluded_blob,
            "mode": "100644",
            "sha256": hashlib.sha256(excluded_raw).hexdigest(),
            "size": len(excluded_raw),
            "source_path": excluded_source_path,
            "stage_path": None,
        }
    )
    current = tree
    parts = excluded_source_path.split("/")
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        assert isinstance(child, dict)
        current = child
    current[parts[-1]] = ("100644", excluded_blob)

    tree_ids: dict[str, str] = {}

    def seal_tree(node: dict[str, object], prefix: str = "") -> str:
        material = bytearray()
        ordered: list[tuple[str, object]] = sorted(
            node.items(), key=lambda item: os.fsencode(item[0] + ("/" if isinstance(item[1], dict) else "")),
        )
        for name, value in ordered:
            if isinstance(value, dict):
                object_id = seal_tree(value, f"{prefix}/{name}".strip("/"))
                mode = "40000"
            else:
                mode, object_id = value
            material.extend(f"{mode} {name}\0".encode("utf-8"))
            material.extend(bytes.fromhex(object_id))
        object_id = _git_object("tree", bytes(material))
        tree_ids[prefix] = object_id
        return object_id

    root_tree = seal_tree(tree)
    commit_object = (
        f"tree {root_tree}\n"
        "author Release Test <release@example.invalid> 0 +0000\n"
        "committer Release Test <release@example.invalid> 0 +0000\n\n"
        "fixture\n"
    ).encode("utf-8")
    return {
        "commit": _git_object("commit", commit_object),
        "commit_object_hex": commit_object.hex(),
        "entries": sorted(entries, key=lambda item: os.fsencode(str(item["source_path"]))),
        "tree": root_tree,
    }


def _source_input(document: dict[str, object]) -> dict[str, object]:
    return {
        key: deepcopy(document["source"][key])
        for key in ("commit", "commit_object_hex", "entries", "tree")
    }


def _test_dependency_manifest(stage: Path, destination: Path) -> Path:
    files: list[dict[str, object]] = []
    digest = hashlib.sha256(canonical_json_bytes(files)[:-1]).hexdigest()
    document = {
        "files": files,
        "installed_file_set_sha256": digest,
        "lock_sha256": _sha(stage / "application/uv.lock"),
        "provenance_file_set_sha256": digest,
        "schema_version": 1,
        "uv": PAPER_UV_PROVENANCE,
        "wheelhouse_aggregate_sha256": "1" * 64,
        "wheels": [],
    }
    destination.write_bytes(canonical_json_bytes(document))
    destination.chmod(0o444)
    return destination


def make_release_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object], str]:
    stage = tmp_path / "sealed-stage"
    runtime_source, runtime_core = _test_runtime_source(tmp_path)
    for relative, repository_source in PAPER_APPLICATION_SOURCE_MAPPING:
        _write(
            stage / "application" / relative,
            (ROOT / repository_source).read_text(encoding="utf-8"),
        )
    construct_python_runtime(
        runtime_source,
        stage / "application/.venv",
        expected_core_sha256=runtime_core,
    )
    install_paper_application_import_path(stage / "application")
    for relative, repository_source in PAPER_BACKEND_SOURCE_MAPPING:
        _write(
            stage / "backend" / relative,
            (ROOT / repository_source).read_text(encoding="utf-8"),
        )
    construct_python_runtime(
        runtime_source,
        stage / "backend/.venv",
        expected_core_sha256=runtime_core,
    )

    source_proof = _source_proof(stage)
    install_root = Path(f"/opt/trading-agent-v2/releases/{source_proof['commit']}")
    for name, raw in render_candidate_units(install_root).items():
        _write(stage / "units" / name, raw.decode("utf-8"))

    dependency_manifest = _test_dependency_manifest(
        stage,
        tmp_path / "application-dependency-manifest.json",
    )
    dependency_document = json.loads(dependency_manifest.read_bytes())
    dependency_provenance = {
        "file_count": len(dependency_document["files"]),
        "installed_file_set_sha256": dependency_document["installed_file_set_sha256"],
        "lock_sha256": dependency_document["lock_sha256"],
        "manifest_sha256": _sha(dependency_manifest),
        "provenance_file_set_sha256": dependency_document["provenance_file_set_sha256"],
        "schema_version": dependency_document["schema_version"],
        "uv_sha256": dependency_document["uv"]["sha256"],
        "wheel_count": len(dependency_document["wheels"]),
        "wheelhouse_aggregate_sha256": dependency_document["wheelhouse_aggregate_sha256"],
    }
    verifier = tmp_path / "external-verify-stage.py"
    verifier_source = VERIFY.read_text(encoding="utf-8")
    production_pin = str(PAPER_PYTHON_RUNTIME_PROVENANCE["normalized_core_sha256"])
    pin_assignment = (
        '_EXPECTED_PYTHON_RUNTIME_CORE_SHA256 = (\n'
        f'    "{production_pin}"\n'
        ')'
    )
    test_assignment = (
        '_EXPECTED_PYTHON_RUNTIME_CORE_SHA256 = (\n'
        f'    "{runtime_core}"\n'
        ')'
    )
    dependency_assignment = next(
        line
        for line in verifier_source.splitlines()
        if line.startswith("_EXPECTED_APPLICATION_DEPENDENCY_PROVENANCE = ")
    )
    test_dependency_assignment = (
        "_EXPECTED_APPLICATION_DEPENDENCY_PROVENANCE = "
        + json.dumps(dependency_provenance, sort_keys=True)
    )
    assert verifier_source.count(pin_assignment) == 1
    assert verifier_source.count(dependency_assignment) == 1
    verifier.write_text(
        verifier_source.replace(pin_assignment, test_assignment).replace(
            dependency_assignment,
            test_dependency_assignment,
        ),
        encoding="utf-8",
    )
    verifier.chmod(0o555)
    _seal(stage)

    document, digest = build_static_release_authority_v2(
        stage,
        source_proof=source_proof,
        application_python_identity="CPython 3.11.15",
        backend_python_identity="CPython 3.11.15",
        external_verifier=verifier,
        application_dependency_manifest=dependency_manifest,
        prior_release_sha256=PRIOR,
        test_expected_python_runtime_core_sha256=runtime_core,
    )
    authority = tmp_path / "authority-v2.json"
    authority.write_bytes(canonical_json_bytes(document))
    authority.chmod(0o444)
    return stage, authority, verifier, document, digest


def test_static_authority_binds_one_source_complete_components_and_snapshot_only(
    tmp_path: Path,
) -> None:
    stage, authority, verifier, document, digest = make_release_fixture(tmp_path)

    with pytest.raises(ReleaseAuthorityV2Error):
        parse_static_release_authority_v2(authority.read_bytes())
    parsed = parse_static_release_authority_v2(
        authority.read_bytes(),
        test_expected_application_dependency_provenance=cast(
            dict[str, Any], document["dependency_manifests"]
        )["application"],
    )

    assert parsed.digest == digest
    assert parsed.source_commit == document["source"]["commit"]
    assert parsed.source_tree == document["source"]["tree"]
    assert set(document["components"]) == {"application", "backend"}
    assert {item["path"] for item in document["lockfiles"].values()} == {
        "application/uv.lock",
        "backend/paper_runtime_manifest.json",
    }
    assert document["database"] == {
        "api_role": "trading_job_api",
        "expected_revision": "0006_job_transition_database_authority",
        "worker_role": "trading_job_worker",
    }
    command = document["command_manifest"]["commands"]
    assert [item["job_type"] for item in command] == ["SNAPSHOT"]
    assert document["artifact_policy"]["artifact_class"] == PAPER_ARTIFACT_CLASS
    backend_python = f"{document['installation_root']}/backend/.venv/bin/python3.11"
    assert command[0]["argv"] == [
        backend_python, "-I", "-B", "paper_main.py",
    ]
    assert command[0]["shell"] is False
    assert command[0]["environment_policy"] == "CANONICAL_PAPER_CHILD_V1"
    assert "trading-job-scheduler.timer" not in document["units"]
    source_stage_paths = {
        entry["stage_path"]
        for entry in document["source"]["entries"]
        if entry["stage_path"] is not None
    }
    assert "application/services/job_worker/main.py" in source_stage_paths
    assert "backend/paper_main.py" in source_stage_paths
    assert not any(
        path.startswith(("dashboard/", "application/alembic/", "application/generated/"))
        for path in source_stage_paths
    )
    assert set(document["interpreters"]) == {
        "application_python",
        "backend_python",
    }
    assert document["job_plane_policy"] == {
        "allowed_job_types": ["SNAPSHOT"],
        "scheduler_timer_enabled": False,
        "worker_concurrency": 1,
        "worker_lease_seconds": 600,
    }
    assert document["runtime_paths"] == {
        "activation": "/etc/trading-agent-v2/release-activation-v2.json",
        "job_artifacts_root": "/var/lib/trading-agent-v2/job-artifacts",
        "reports_root": "/var/lib/trading-agent-v2/research-output/reports",
        "safety_snapshot": "/run/trading-agent-v2/safety-state.json",
        "scratch_root": "/var/lib/trading-agent-v2/research-output/scratch",
        "semantic_active": "/etc/trading-agent-v2/research-input-manifests/active.json",
        "semantic_input_root": "/var/lib/trading-agent-v2/research-input",
        "signals_root": "/var/lib/trading-agent-v2/research-output/signals",
        "static_authority": "/etc/trading-agent-v2/release-authority-v2.json",
    }
    worker_unit = render_candidate_units(
        Path(document["installation_root"])
    )["trading-job-worker.service"]
    assert b"LEASE_SECONDS" not in worker_unit
    for forced_line in (
        b"Environment=TRADING_MODE=paper\n",
        b"Environment=LIVE_EXECUTION_ENABLED=false\n",
        b"Environment=LIVE_TRADING_APPROVED=false\n",
        b"Environment=LIVE_TRADING_ENABLED=false\n",
    ):
        assert forced_line in worker_unit

    credential_sources = {
        "trading-job-api.service": {
            "database-host": "/etc/trading-agent-v2/credentials/job-api/database-host",
            "database-name": "/etc/trading-agent-v2/credentials/job-api/database-name",
            "database-password": "/etc/trading-agent-v2/credentials/job-api/database-password",
            "database-port": "/etc/trading-agent-v2/credentials/job-api/database-port",
            "job-api-principal-id": "/etc/trading-agent-v2/credentials/job-api/principal-id",
            "job-api-principal-type": "/etc/trading-agent-v2/credentials/job-api/principal-type",
            "job-api-token": "/etc/trading-agent-v2/credentials/job-api/token",
        },
        "trading-job-worker.service": {
            "database-host": "/etc/trading-agent-v2/credentials/job-worker/database-host",
            "database-name": "/etc/trading-agent-v2/credentials/job-worker/database-name",
            "database-password": "/etc/trading-agent-v2/credentials/job-worker/database-password",
            "database-port": "/etc/trading-agent-v2/credentials/job-worker/database-port",
        },
    }
    rendered_units = render_candidate_units(Path(cast(str, document["installation_root"])))
    unit_documents = cast(dict[str, dict[str, Any]], document["units"])
    for name, expected_sources in credential_sources.items():
        unit = rendered_units[name]
        assert b"EnvironmentFile=" not in unit
        assert (
            b"UnsetEnvironment=LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH PYTHONHOME PYTHONPATH\n"
            in unit
        )
        unit_lines = unit.splitlines()
        assert not any(line.startswith(b"Environment=LD_PRELOAD") for line in unit_lines)
        assert not any(line.startswith(b"Environment=LD_AUDIT") for line in unit_lines)
        assert not any(
            line.startswith(b"Environment=LD_LIBRARY_PATH") for line in unit_lines
        )
        assert unit_documents[name]["credential_references"] == expected_sources
        assert "credential_reference" not in unit_documents[name]
        assert {
            line.decode("utf-8").removeprefix("LoadCredential=")
            for line in unit.splitlines()
            if line.startswith(b"LoadCredential=")
        } == {f"{key}:{value}" for key, value in expected_sources.items()}
    assert verify_static_release_authority_v2(
        stage, authority.read_bytes(), expected_digest=digest, verifier_path=verifier,
            test_expected_python_runtime_core_sha256=_test_runtime_core(stage),
    ) is True


def test_pinned_uv_projection_rejects_fake_executable(tmp_path: Path) -> None:
    source = tmp_path / "fake-uv"
    _write(source, "#!/bin/sh\nexit 0\n", 0o755)
    private = tmp_path / "private-build-root"
    private.mkdir(mode=0o700)

    with pytest.raises(ReleaseAuthorityV2Error):
        construct_pinned_uv_tool(source, private / "uv")
    assert not (private / "uv").exists()


@pytest.mark.host_coupled
def test_pinned_uv_projection_identity_matches_builder_probe(tmp_path: Path) -> None:
    resolved = shutil.which("uv")
    if resolved is None:
        pytest.skip("pinned uv executable is unavailable")
    source = Path(resolved).resolve()
    assert _sha(source) == PAPER_UV_PROVENANCE["sha256"]
    private = tmp_path / "private-build-root"
    private.mkdir(mode=0o700)
    projected = private / "uv"
    construct_pinned_uv_tool(source, projected)

    completed = subprocess.run(
        [os.fspath(projected), "--version"],
        check=True,
        capture_output=True,
        env={},
        text=True,
    )
    assert completed.stdout.strip() == PAPER_UV_PROVENANCE["identity"]


def test_activation_api_is_disabled_until_promotion_and_rotating_evidence_are_separated(
    tmp_path: Path,
) -> None:
    _, _, _, static_document, static_digest = make_release_fixture(tmp_path)
    now = datetime(2026, 7, 16, 18, 0, tzinfo=UTC)
    safety = {
        "snapshot_sha256": "7" * 64,
        "generated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
        "valid_until": (now - timedelta(seconds=5) + timedelta(seconds=6)).isoformat().replace("+00:00", "Z"),
        "requested_mode": "paper",
        "effective_mode": "paper",
        "live_execution_enabled": False,
        "live_trading_approved": False,
        "kill_switch": "INACTIVE",
    }
    semantic = {
        "active_sha256": "8" * 64,
        "manifest_sha256": "9" * 64,
        "semantic_input_fingerprint": "a" * 64,
        "version": "snapshot-2026-07-16T1800Z",
        "generated_at": (now - timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
        "valid_until": (now + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
    }

    with pytest.raises(ReleaseAuthorityV2Error):
        build_release_activation_v2(
            static_document,
            static_authority_sha256=static_digest,
            safety=safety,
            semantic=semantic,
            safety_evidence_sha256=hashlib.sha256(canonical_json_bytes(safety)).hexdigest(),
            semantic_evidence_sha256=hashlib.sha256(canonical_json_bytes(semantic)).hexdigest(),
            created_at=now,
            nonce="b" * 64,
        )
    with pytest.raises(ReleaseAuthorityV2Error):
        parse_release_activation_v2(
            b"{}\n",
            static_document,
            now=now,
            expected_safety_evidence_sha256="7" * 64,
            expected_semantic_evidence_sha256="8" * 64,
        )


def _mutated(document: dict[str, object], name: str) -> bytes:
    value = deepcopy(document)
    if name == "source_commit":
        value["source"]["commit"] = "f" * 40
    elif name == "source_tree":
        value["source"]["tree"] = "e" * 40
    elif name == "component_prefix":
        value["components"]["backend"]["source_prefix"] = "backend"
    elif name == "component_tree":
        value["components"]["application"]["source_tree"] = "d" * 40
    elif name == "lock_hash":
        value["lockfiles"]["application"]["sha256"] = "c" * 64
    elif name == "interpreter":
        value["interpreters"]["backend_python"]["identity"] = "CPython 3.12.0"
    elif name == "head":
        value["database"]["expected_revision"] = "0005_job_plane_role_split"
    elif name == "role":
        value["database"]["worker_role"] = "trading_jobs"
    elif name == "command":
        value["command_manifest"]["commands"][0]["job_type"] = "DEBATE"
    elif name == "unit":
        value["units"]["trading-job-worker.service"]["sha256"] = "a" * 64
    elif name == "effective_command":
        value["units"]["trading-job-api.service"]["argv"].append("--unsafe")
    elif name == "verifier":
        value["external_verifier"]["sha256"] = "9" * 64
    elif name == "stage_path":
        value["stage"]["path"] = "/tmp/not-the-stage"
    elif name == "prior":
        value["prior_release_sha256"] = "8" * 64
    elif name == "unknown":
        value["unexpected"] = True
    elif name == "v1":
        value["schema_version"] = 1
    elif name == "owner":
        value["stage"]["uid"] += 1
    else:
        raise AssertionError(name)
    return canonical_json_bytes(value)


@pytest.mark.parametrize(
    "mutation",
    [
        "source_commit", "source_tree", "component_prefix", "component_tree",
        "lock_hash", "interpreter", "head", "role", "command",
        "unit", "effective_command", "verifier", "stage_path", "prior",
        "unknown", "v1", "owner",
    ],
)
def test_v2_rejects_authority_field_tamper(tmp_path: Path, mutation: str) -> None:
    stage, _, verifier, document, _ = make_release_fixture(tmp_path)
    raw = _mutated(document, mutation)
    with pytest.raises(ReleaseAuthorityV2Error):
        verify_static_release_authority_v2(
            stage,
            raw,
            expected_digest=hashlib.sha256(raw).hexdigest(),
            verifier_path=verifier,
            test_expected_python_runtime_core_sha256=_test_runtime_core(stage),
        )


@pytest.mark.parametrize("mutation", ["extra", "missing", "symlink", "hardlink", "mode"])
def test_v2_rejects_stage_complete_set_and_metadata_tamper(
    tmp_path: Path, mutation: str,
) -> None:
    stage, authority, verifier, _, digest = make_release_fixture(tmp_path)
    target = stage / "backend/paper_main.py"
    if mutation == "extra":
        stage.chmod(0o755)
        _write(stage / "extra", "unexpected\n", 0o444)
        stage.chmod(0o555)
    elif mutation == "missing":
        target.parent.chmod(0o755)
        target.unlink()
    elif mutation == "symlink":
        target.parent.chmod(0o755)
        target.unlink()
        target.symlink_to("job_attribution.py")
    elif mutation == "hardlink":
        os.link(target, tmp_path / "outside-hardlink")
    else:
        target.chmod(0o644)

    with pytest.raises(ReleaseAuthorityV2Error):
        verify_static_release_authority_v2(
            stage, authority.read_bytes(), expected_digest=digest, verifier_path=verifier,
            test_expected_python_runtime_core_sha256=_test_runtime_core(stage),
        )


def test_external_verifier_is_standalone_and_rejects_tamper(tmp_path: Path) -> None:
    stage, authority, verifier, _, digest = make_release_fixture(tmp_path)
    source = verifier.read_text(encoding="utf-8")
    assert "packages.runtime_release" not in source
    command = [
        os.fspath(verifier), os.fspath(stage), os.fspath(authority),
        "--expected-authority-sha256", digest,
    ]

    accepted = subprocess.run(command, capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout == "release authority v2 stage verified\n"

    (stage / "application/uv.lock").chmod(0o644)
    (stage / "application/uv.lock").write_text("tampered\n", encoding="utf-8")
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert rejected.stderr == "release authority v2 stage rejected\n"


def test_external_verifier_content_tamper_is_rejected(tmp_path: Path) -> None:
    stage, authority, verifier, document, digest = make_release_fixture(tmp_path)
    del document
    verifier.chmod(0o755)
    verifier.write_bytes(verifier.read_bytes() + b"\n# tampered\n")
    verifier.chmod(0o555)

    with pytest.raises(ReleaseAuthorityV2Error):
        verify_static_release_authority_v2(
            stage, authority.read_bytes(), expected_digest=digest, verifier_path=verifier,
            test_expected_python_runtime_core_sha256=_test_runtime_core(stage),
        )


def test_content_copy_requires_production_root_or_explicit_nonroot_fake_mode(tmp_path: Path) -> None:
    stage, authority, verifier, _, digest = make_release_fixture(tmp_path)
    with pytest.raises(ReleaseAuthorityV2Error):
        verify_static_release_authority_v2(
            stage,
            authority.read_bytes(),
            expected_digest=digest,
            verifier_path=verifier,
            test_expected_python_runtime_core_sha256=_test_runtime_core(stage),
            content_copy=True,
        )
    if os.geteuid() == 0:
        with pytest.raises(ReleaseAuthorityV2Error):
            verify_static_release_authority_v2(
                stage,
                authority.read_bytes(),
                expected_digest=digest,
                verifier_path=verifier,
            test_expected_python_runtime_core_sha256=_test_runtime_core(stage),
                content_copy=True,
                test_fake_root_copy=True,
            )
    else:
        assert verify_static_release_authority_v2(
            stage,
            authority.read_bytes(),
            expected_digest=digest,
            verifier_path=verifier,
            test_expected_python_runtime_core_sha256=_test_runtime_core(stage),
            content_copy=True,
            test_fake_root_copy=True,
        ) is True


def test_static_authority_rejects_nonroot_install_owner_policy(tmp_path: Path) -> None:
    stage, _, verifier, document, _ = make_release_fixture(tmp_path)
    tampered = deepcopy(document)
    tampered["stage"]["installation_uid"] = os.geteuid() or 1
    material = deepcopy(tampered)
    material.pop("binding_sha256")
    tampered["binding_sha256"] = hashlib.sha256(canonical_json_bytes(material)[:-1]).hexdigest()
    raw = canonical_json_bytes(tampered)
    with pytest.raises(ReleaseAuthorityV2Error):
        verify_static_release_authority_v2(
            stage,
            raw,
            expected_digest=hashlib.sha256(raw).hexdigest(),
            verifier_path=verifier,
            test_expected_python_runtime_core_sha256=_test_runtime_core(stage),
        )


@pytest.mark.parametrize(
    "relative",
    ["application/.venv/lib/python3.11/site-packages/pkg/__pycache__/seed.cpython-311.pyc", "backend/seed.pyc"],
)
def test_static_builder_rejects_preseeded_bytecode(tmp_path: Path, relative: str) -> None:
    stage, _, verifier, document, _ = make_release_fixture(tmp_path)
    ancestors: list[Path] = []
    parent = (stage / relative).parent
    while parent != stage.parent:
        ancestors.append(parent)
        parent = parent.parent
    for ancestor in reversed(ancestors):
        if ancestor.exists():
            ancestor.chmod(0o755)
    _write(stage / relative, "not-bytecode\n", 0o555)
    _seal(stage)
    with pytest.raises(ReleaseAuthorityV2Error):
        build_static_release_authority_v2(
            stage,
            source_proof=_source_input(document),
            application_python_identity="CPython 3.11.15",
            backend_python_identity="CPython 3.11.15",
            external_verifier=verifier,
            application_dependency_manifest=tmp_path / "application-dependency-manifest.json",
            prior_release_sha256=PRIOR,
        )


@pytest.mark.parametrize(
    "relative",
    [
        "application/.venv/lib/python3.11/site-packages/fastapi/implant.py",
        "application/.venv/lib/python3.11/site-packages/surprise_package/__init__.py",
    ],
)
def test_static_builder_rejects_site_package_outside_dependency_manifest(
    tmp_path: Path,
    relative: str,
) -> None:
    stage, _, verifier, document, _ = make_release_fixture(tmp_path)
    target = stage / relative
    for parent in target.parents:
        if parent == stage.parent:
            break
        if parent.exists():
            parent.chmod(0o755)
    _write(target, "raise SystemExit('implanted')\n", 0o444)
    _seal(stage)

    with pytest.raises(ReleaseAuthorityV2Error):
        build_static_release_authority_v2(
            stage,
            source_proof=_source_input(document),
            application_python_identity="CPython 3.11.15",
            backend_python_identity="CPython 3.11.15",
            external_verifier=verifier,
            application_dependency_manifest=tmp_path / "application-dependency-manifest.json",
            prior_release_sha256=PRIOR,
            test_expected_python_runtime_core_sha256=_test_runtime_core(stage),
        )


@pytest.mark.parametrize("mutation", ["missing", "modified", "extra"])
def test_standalone_verifier_rejects_resealed_application_import_path(
    tmp_path: Path,
    mutation: str,
) -> None:
    stage, authority, verifier, document, _ = make_release_fixture(tmp_path)
    import_path = (
        stage
        / "application/.venv/lib/python3.11/site-packages/trading-agent-paper-application.pth"
    )
    for parent in import_path.parents:
        if parent == stage.parent:
            break
        if parent.exists():
            parent.chmod(0o755)
    if mutation == "missing":
        import_path.unlink()
    elif mutation == "modified":
        import_path.chmod(0o644)
        import_path.write_bytes(b"/tmp\n")
        import_path.chmod(0o444)
    else:
        _write(import_path.with_name("escape.pth"), "/tmp\n", 0o444)
    _seal(stage)

    uid, gid, entries = _walk_sealed_stage(stage)
    resealed = cast(dict[str, Any], deepcopy(document))
    resealed["stage"]["entries"] = entries
    resealed["stage"]["file_set_sha256"] = _sha256_bytes(_fragment(entries))
    resealed["stage"]["uid"] = uid
    resealed["stage"]["gid"] = gid
    resealed["components"]["application"]["artifact_set_sha256"] = _artifact_digest(
        entries,
        "application",
    )
    resealed["binding_sha256"] = _authority_binding(resealed)
    raw = canonical_json_bytes(resealed)
    authority.chmod(0o644)
    authority.write_bytes(raw)
    authority.chmod(0o444)

    rejected = subprocess.run(
        [
            os.fspath(verifier),
            os.fspath(stage),
            os.fspath(authority),
            "--expected-authority-sha256",
            hashlib.sha256(raw).hexdigest(),
        ],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert rejected.stderr == "release authority v2 stage rejected\n"


def test_standalone_verifier_rejects_resealed_site_package_implant(tmp_path: Path) -> None:
    stage, authority, verifier, document, _ = make_release_fixture(tmp_path)
    implant = stage / "application/.venv/lib/python3.11/site-packages/fastapi/implant.py"
    for parent in implant.parents:
        if parent == stage.parent:
            break
        if parent.exists():
            parent.chmod(0o755)
    _write(implant, "raise SystemExit('implanted')\n", 0o444)
    _seal(stage)

    uid, gid, entries = _walk_sealed_stage(stage)
    resealed = cast(dict[str, Any], deepcopy(document))
    resealed["stage"]["entries"] = entries
    resealed["stage"]["file_set_sha256"] = _sha256_bytes(_fragment(entries))
    resealed["stage"]["uid"] = uid
    resealed["stage"]["gid"] = gid
    resealed["components"]["application"]["artifact_set_sha256"] = _artifact_digest(
        entries,
        "application",
    )
    resealed["binding_sha256"] = _authority_binding(resealed)
    raw = canonical_json_bytes(resealed)
    authority.chmod(0o644)
    authority.write_bytes(raw)
    authority.chmod(0o444)

    rejected = subprocess.run(
        [
            os.fspath(verifier),
            os.fspath(stage),
            os.fspath(authority),
            "--expected-authority-sha256",
            hashlib.sha256(raw).hexdigest(),
        ],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert rejected.stderr == "release authority v2 stage rejected\n"


def test_standalone_verifier_rejects_resealed_environment_file_unit(
    tmp_path: Path,
) -> None:
    stage, authority, verifier, document, _ = make_release_fixture(tmp_path)
    unit_name = "trading-job-worker.service"
    unit_path = stage / "units" / unit_name
    unit_path.chmod(0o644)
    malicious = unit_path.read_bytes().replace(
        b"Type=simple\n",
        b"Type=simple\n"
        b"EnvironmentFile=/tmp/attacker-controlled.env\n"
        b"Environment=LD_PRELOAD=/tmp/attacker-controlled.so\n",
        1,
    )
    unit_path.write_bytes(malicious)
    unit_path.chmod(0o444)
    _seal(stage)

    uid, gid, entries = _walk_sealed_stage(stage)
    resealed = cast(dict[str, Any], deepcopy(document))
    resealed["stage"]["entries"] = entries
    resealed["stage"]["file_set_sha256"] = _sha256_bytes(_fragment(entries))
    resealed["stage"]["uid"] = uid
    resealed["stage"]["gid"] = gid
    resealed["units"][unit_name]["sha256"] = _sha256_bytes(malicious)
    resealed["binding_sha256"] = _authority_binding(resealed)
    raw = canonical_json_bytes(resealed)
    authority.chmod(0o644)
    authority.write_bytes(raw)
    authority.chmod(0o444)

    rejected = subprocess.run(
        [
            os.fspath(verifier),
            os.fspath(stage),
            os.fspath(authority),
            "--expected-authority-sha256",
            hashlib.sha256(raw).hexdigest(),
        ],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert rejected.stderr == "release authority v2 stage rejected\n"


@pytest.mark.parametrize(
    "relative",
    [
        "units/trading-job-scheduler.service",
        "units/trading-job-worker.service.d/override.conf",
    ],
)
def test_static_builder_rejects_extra_candidate_unit_or_dropin(
    tmp_path: Path, relative: str,
) -> None:
    stage, _, verifier, document, _ = make_release_fixture(tmp_path)
    stage.chmod(0o755)
    (stage / "units").chmod(0o755)
    _write(stage / relative, "[Service]\nExecStart=/unsafe\n", 0o444)
    _seal(stage)

    with pytest.raises(ReleaseAuthorityV2Error):
        build_static_release_authority_v2(
            stage,
            source_proof=_source_input(document),
            application_python_identity="CPython 3.11.15",
            backend_python_identity="CPython 3.11.15",
            external_verifier=verifier,
            application_dependency_manifest=tmp_path / "application-dependency-manifest.json",
            prior_release_sha256=PRIOR,
        )


@pytest.mark.host_coupled
def test_extended_attributes_are_rejected_when_supported(tmp_path: Path) -> None:
    if not hasattr(os, "setxattr"):
        pytest.skip("extended attributes unavailable")
    stage, authority, verifier, _, digest = make_release_fixture(tmp_path)
    target = stage / "backend/main.py"
    try:
        os.setxattr(target, "user.release-test", b"1", follow_symlinks=False)
    except OSError:
        pytest.skip("filesystem does not support user xattrs")
    try:
        with pytest.raises(ReleaseAuthorityV2Error):
            verify_static_release_authority_v2(
                stage, authority.read_bytes(), expected_digest=digest, verifier_path=verifier,
            test_expected_python_runtime_core_sha256=_test_runtime_core(stage),
            )
    finally:
        os.removexattr(target, "user.release-test", follow_symlinks=False)


def test_capture_source_proof_reconstructs_exact_git_objects(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(["git", "-C", repository, "config", "user.name", "Release Test"], check=True)
    subprocess.run(
        ["git", "-C", repository, "config", "user.email", "release@example.invalid"], check=True,
    )
    _write(repository / "root file.txt", "root\n")
    _write(
        repository / "packages/runtime_release/paper_backend/job_attribution.py",
        "def bind_job():\n    return None\n",
    )
    _write(repository / "legacy/research-backend/main.py", "print('excluded')\n")
    _write(
        repository / "packages/runtime_release/paper_application/command_registry.py",
        "COMMAND_REGISTRY = {}\n",
    )
    _write(
        repository / "packages/runtime_release/paper_backend/paper_main.py",
        "def main():\n    return 0\n",
    )
    _write(
        repository / "packages/runtime_release/paper_backend/provider_free_fixture.py",
        "def load_fixture():\n    return None\n",
    )
    _write(
        repository / "packages/runtime_release/paper_backend/research_semantics.py",
        "def research():\n    return None\n",
    )
    _write(
        repository / "packages/runtime_release/staging_v2.py",
        "STAGING_SCOPE = 'PACKAGE6_STAGING_ONLY'\n",
    )
    subprocess.run(["git", "-C", repository, "add", "--all"], check=True)
    subprocess.run(["git", "-C", repository, "commit", "-qm", "fixture"], check=True)
    commit = subprocess.run(
        ["git", "-C", repository, "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    output = tmp_path / "source-proof.json"

    captured = subprocess.run(
        [
            sys.executable,
            os.fspath(ROOT / "packages/runtime_release/v2.py"),
            "capture-source-proof",
            "--repo", os.fspath(repository),
            "--commit", commit,
            "--output", os.fspath(output),
        ],
        capture_output=True,
        text=True,
    )

    assert captured.returncode == 0, captured.stderr
    proof = json.loads(output.read_bytes())
    assert proof["commit"] == commit
    assert proof["tree"] == subprocess.run(
        ["git", "-C", repository, "rev-parse", f"{commit}^{{tree}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert {entry["stage_path"] for entry in proof["entries"]} == {
        None,
        "application/services/job_worker/command_registry.py",
            "backend/job_attribution.py",
            "backend/paper_main.py",
            "backend/provider_free_fixture.py",
            "backend/research_semantics.py",
            "application/packages/runtime_release/staging_v2.py",
        }
