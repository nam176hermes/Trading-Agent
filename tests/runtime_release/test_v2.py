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

import pytest

from packages.runtime_release.v2 import (
    ReleaseAuthorityV2Error,
    build_release_activation_v2,
    build_static_release_authority_v2,
    canonical_json_bytes,
    parse_release_activation_v2,
    parse_static_release_authority_v2,
    render_candidate_units,
    verify_static_release_authority_v2,
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


def _seal(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
    root.chmod(0o555)


def _git_object(kind: str, raw: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode("ascii") + raw).hexdigest()


def _source_proof(stage: Path) -> dict[str, object]:
    tracked_stage_paths = [
        "application/uv.lock",
        *sorted(
            path.relative_to(stage).as_posix()
            for path in (stage / "application/alembic/versions").glob("*.py")
        ),
        *sorted(
            path.relative_to(stage).as_posix()
            for path in (stage / "application/ops/systemd").glob("*")
        ),
        "application/generated/openapi/openapi.json",
        "application/generated/job-api/openapi/openapi.json",
        "application/generated/dashboard/api-schemas.ts",
        "application/generated/dashboard/api-types.ts",
        "backend/uv.lock",
        "backend/main.py",
        "dashboard/package-lock.json",
    ]
    entries: list[dict[str, object]] = []
    tree: dict[str, object] = {}
    for stage_path in tracked_stage_paths:
        if stage_path.startswith("backend/"):
            source_path = "legacy/research-backend/" + stage_path.removeprefix("backend/")
        elif stage_path.startswith("dashboard/"):
            source_path = "apps/dashboard/" + stage_path.removeprefix("dashboard/")
        else:
            source_path = stage_path.removeprefix("application/")
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


def make_release_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object], str]:
    stage = tmp_path / "sealed-stage"
    _write(stage / "application/uv.lock", "root-lock\n")
    _write(
        stage / "application/alembic/versions/0001_phase3_operational_store.py",
        "revision = '0001_phase3_operational_store'\ndown_revision = None\n",
    )
    _write(
        stage / "application/alembic/versions/0004_durable_research_jobs.py",
        "revision = '0004_durable_research_jobs'\n"
        "down_revision = '0001_phase3_operational_store'\n",
    )
    _write(
        stage / "application/alembic/versions/0005_job_plane_role_split.py",
        "revision = '0005_job_plane_role_split'\n"
        "down_revision = '0004_durable_research_jobs'\n",
    )
    _write(
        stage
        / "application/alembic/versions/0006_job_transition_database_authority.py",
        "revision = '0006_job_transition_database_authority'\n"
        "down_revision = '0005_job_plane_role_split'\n",
    )
    _write(stage / "application/generated/openapi/openapi.json", "{}\n")
    _write(stage / "application/generated/job-api/openapi/openapi.json", "{}\n")
    _write(stage / "application/generated/dashboard/api-schemas.ts", "export {};\n")
    _write(stage / "application/generated/dashboard/api-types.ts", "export {};\n")
    _write(stage / "application/ops/systemd/safety-export.timer", "[Timer]\nOnUnitActiveSec=2s\n")
    _write(stage / "application/.venv/bin/python3.11", "#!/bin/sh\nexit 0\n", 0o755)
    _write(stage / "backend/uv.lock", "backend-lock\n")
    _write(stage / "backend/main.py", "raise SystemExit(0)\n")
    _write(stage / "backend/.venv/bin/python3.11", "#!/bin/sh\nexit 0\n", 0o755)
    _write(stage / "dashboard/package-lock.json", '{"lockfileVersion":3}\n')
    _write(stage / "dashboard/.next/BUILD_ID", "fixture-build\n")

    source_proof = _source_proof(stage)
    install_root = Path(f"/opt/trading-agent-v2/releases/{source_proof['commit']}")
    for name, raw in render_candidate_units(install_root).items():
        _write(stage / "units" / name, raw.decode("utf-8"))

    verifier = tmp_path / "external-verify-stage.py"
    shutil.copyfile(VERIFY, verifier)
    verifier.chmod(0o555)
    node = Path("/usr/bin/node").resolve(strict=True)
    node_identity = "Node.js " + subprocess.run(
        [node, "--version"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    _seal(stage)

    document, digest = build_static_release_authority_v2(
        stage,
        source_proof=source_proof,
        application_python_identity="CPython 3.11.15",
        backend_python_identity="CPython 3.11.15",
        node_executable=node,
        node_identity=node_identity,
        external_verifier=verifier,
        prior_release_sha256=PRIOR,
    )
    authority = tmp_path / "authority-v2.json"
    authority.write_bytes(canonical_json_bytes(document))
    authority.chmod(0o444)
    return stage, authority, verifier, document, digest


def test_static_authority_binds_one_source_complete_components_and_snapshot_only(
    tmp_path: Path,
) -> None:
    stage, authority, verifier, document, digest = make_release_fixture(tmp_path)

    parsed = parse_static_release_authority_v2(authority.read_bytes())

    assert parsed.digest == digest
    assert parsed.source_commit == document["source"]["commit"]
    assert parsed.source_tree == document["source"]["tree"]
    assert set(document["components"]) == {"application", "backend", "dashboard"}
    assert {item["path"] for item in document["lockfiles"].values()} == {
        "application/uv.lock",
        "backend/uv.lock",
        "dashboard/package-lock.json",
    }
    assert (
        document["database"]["alembic_head"]
        == "0006_job_transition_database_authority"
    )
    assert document["database"]["api_role"] == "trading_job_api"
    assert document["database"]["worker_role"] == "trading_job_worker"
    assert document["database"]["scheduler_role"] == "trading_job_scheduler"
    revisions = {
        item["revision"]: item["down_revision"]
        for item in document["database"]["alembic_revisions"]
    }
    assert revisions["0006_job_transition_database_authority"] == (
        "0005_job_plane_role_split"
    )
    assert revisions["0005_job_plane_role_split"] == "0004_durable_research_jobs"
    command = document["command_manifest"]["commands"]
    assert [item["job_type"] for item in command] == ["SNAPSHOT"]
    backend_python = f"{document['installation_root']}/backend/.venv/bin/python3.11"
    assert command[0]["argv"] == [
        backend_python, "-I", "-B", "main.py", "--mode", "snapshot", "--research-only",
    ]
    assert command[0]["shell"] is False
    assert command[0]["environment_policy"] == "EMPTY_ALLOWLIST_RESEARCH_ONLY_V1"
    assert "trading-job-scheduler.timer" not in document["units"]
    assert any(
        entry["stage_path"] == "application/ops/systemd/safety-export.timer"
        for entry in document["source"]["entries"]
    )
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
    assert b"LEASE_SECONDS" not in render_candidate_units(
        Path(document["installation_root"])
    )["trading-job-worker.service"]
    assert verify_static_release_authority_v2(
        stage, authority.read_bytes(), expected_digest=digest, verifier_path=verifier,
    ) is True


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
        value["components"]["dashboard"]["source_tree"] = "d" * 40
    elif name == "lock_hash":
        value["lockfiles"]["application"]["sha256"] = "c" * 64
    elif name == "interpreter":
        value["interpreters"]["backend_python"]["identity"] = "CPython 3.12.0"
    elif name == "node":
        value["interpreters"]["dashboard_node"]["sha256"] = "0" * 64
    elif name == "contract":
        value["contracts"]["entries"][0]["sha256"] = "b" * 64
    elif name == "head":
        value["database"]["alembic_head"] = "0005_job_plane_role_split"
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
        "lock_hash", "interpreter", "node", "contract", "head", "role", "command",
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
        )


@pytest.mark.parametrize("mutation", ["extra", "missing", "symlink", "hardlink", "mode"])
def test_v2_rejects_stage_complete_set_and_metadata_tamper(
    tmp_path: Path, mutation: str,
) -> None:
    stage, authority, verifier, _, digest = make_release_fixture(tmp_path)
    target = stage / "backend/main.py"
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
        target.symlink_to("uv.lock")
    elif mutation == "hardlink":
        os.link(target, tmp_path / "outside-hardlink")
    else:
        target.chmod(0o644)

    with pytest.raises(ReleaseAuthorityV2Error):
        verify_static_release_authority_v2(
            stage, authority.read_bytes(), expected_digest=digest, verifier_path=verifier,
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
        )


def test_content_copy_requires_production_root_or_explicit_nonroot_fake_mode(tmp_path: Path) -> None:
    stage, authority, verifier, _, digest = make_release_fixture(tmp_path)
    with pytest.raises(ReleaseAuthorityV2Error):
        verify_static_release_authority_v2(
            stage,
            authority.read_bytes(),
            expected_digest=digest,
            verifier_path=verifier,
            content_copy=True,
        )
    if os.geteuid() == 0:
        with pytest.raises(ReleaseAuthorityV2Error):
            verify_static_release_authority_v2(
                stage,
                authority.read_bytes(),
                expected_digest=digest,
                verifier_path=verifier,
                content_copy=True,
                test_fake_root_copy=True,
            )
    else:
        assert verify_static_release_authority_v2(
            stage,
            authority.read_bytes(),
            expected_digest=digest,
            verifier_path=verifier,
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
        )


def test_static_builder_rejects_user_owned_node_runtime(tmp_path: Path) -> None:
    stage, _, verifier, document, _ = make_release_fixture(tmp_path)
    node = tmp_path / "user-node"
    _write(node, "#!/bin/sh\nexit 0\n", 0o555)
    with pytest.raises(ReleaseAuthorityV2Error):
        build_static_release_authority_v2(
            stage,
            source_proof=_source_input(document),
            application_python_identity="CPython 3.11.15",
            backend_python_identity="CPython 3.11.15",
            node_executable=node,
            node_identity="Node.js v22.17.0",
            external_verifier=verifier,
            prior_release_sha256=PRIOR,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "second_head",
        "wrong_0005_revision",
        "wrong_0006_parent",
        "disconnected_cycle",
    ],
)
def test_static_builder_rejects_invalid_alembic_graph(
    tmp_path: Path, mutation: str,
) -> None:
    stage, _, verifier, document, _ = make_release_fixture(tmp_path)
    versions = stage / "application/alembic/versions"
    for path in (stage, stage / "application", stage / "application/alembic", versions):
        path.chmod(0o755)
    if mutation == "second_head":
        _write(
            versions / "9999_second_head.py",
            "revision = '9999_second_head'\ndown_revision = '0005_job_plane_role_split'\n",
            0o444,
        )
    elif mutation == "wrong_0005_revision":
        migration = versions / "0005_job_plane_role_split.py"
        migration.chmod(0o644)
        migration.write_text(
            "revision = '0005_wrong_revision'\n"
            "down_revision = '0004_durable_research_jobs'\n",
            encoding="utf-8",
        )
    elif mutation == "wrong_0006_parent":
        migration = versions / "0006_job_transition_database_authority.py"
        migration.chmod(0o644)
        migration.write_text(
            "revision = '0006_job_transition_database_authority'\n"
            "down_revision = '0004_durable_research_jobs'\n",
            encoding="utf-8",
        )
    else:
        _write(versions / "9000_cycle_a.py", "revision = 'cycle_a'\ndown_revision = 'cycle_b'\n", 0o444)
        _write(versions / "9001_cycle_b.py", "revision = 'cycle_b'\ndown_revision = 'cycle_a'\n", 0o444)
    _seal(stage)

    node = Path(document["interpreters"]["dashboard_node"]["path"])
    with pytest.raises(ReleaseAuthorityV2Error):
        build_static_release_authority_v2(
            stage,
            source_proof=_source_proof(stage),
            application_python_identity="CPython 3.11.15",
            backend_python_identity="CPython 3.11.15",
            node_executable=node,
            node_identity=document["interpreters"]["dashboard_node"]["identity"],
            external_verifier=verifier,
            prior_release_sha256=PRIOR,
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
            node_executable=Path(document["interpreters"]["dashboard_node"]["path"]),
            node_identity=document["interpreters"]["dashboard_node"]["identity"],
            external_verifier=verifier,
            prior_release_sha256=PRIOR,
        )


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
            node_executable=Path(document["interpreters"]["dashboard_node"]["path"]),
            node_identity=document["interpreters"]["dashboard_node"]["identity"],
            external_verifier=verifier,
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
    _write(repository / "legacy/research-backend/main.py", "print('backend')\n")
    _write(repository / "apps/dashboard/package-lock.json", "{}\n")
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
        "application/root file.txt", "backend/main.py", "dashboard/package-lock.json",
    }
