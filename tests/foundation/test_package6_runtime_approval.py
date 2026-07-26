from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import Callable, cast

import pytest

from packages.runtime_release.staging_v2 import (
    STAGING_SCOPE,
    build_staging_activation_v2,
    build_staging_release_authority_v2,
    canonical_digest,
    canonical_json_bytes,
)
from scripts.validate_package6_runtime_approval import (
    PACKAGE6_SOURCE_BINDING_PATHS,
    Package6ApprovalContext,
    Package6ApprovalRejected,
    canonical_record_sha256,
    validate_package6_runtime_approval,
    validate_source_binding_files,
)


COMMIT = "7dd5336e050d255e6d75bf907dc792011f322b99"
TREE = "679847e0f3738d71d1ab130512d9fe285a9bcd80"
PG_APPROVAL = "a" * 64
FIXTURE = "b" * 64
CANONICAL_BINDINGS = PACKAGE6_SOURCE_BINDING_PATHS
NOW = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class StagingMaterial:
    private: Path
    stage: Path
    application: Path
    application_python: Path
    backend_python: Path
    authority_path: Path
    authority_sha256: str
    fixture_path: Path
    fixture_sha256: str
    safety_sha256: str
    semantic_sha256: str
    generated: datetime
    expires: datetime
    application_sha256: str
    backend_sha256: str
    command_sha256: str


def _object(document: dict[str, object], key: str) -> dict[str, object]:
    return cast(dict[str, object], document[key])


def _objects(document: dict[str, object], key: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], document[key])


def _write_fixed(path: Path, value: object, mode: int = 0o444) -> str:
    raw = canonical_json_bytes(value)
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(mode)
    return hashlib.sha256(raw).hexdigest()


def _staging_material(root: Path) -> StagingMaterial:
    private = root / "package6-staging"
    private.mkdir(mode=0o700, exist_ok=True)
    for path in private.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
    stage = private / "stage"
    application = stage / "application"
    backend = stage / "backend"
    for component in (application, backend):
        (component / ".venv/bin").mkdir(parents=True, exist_ok=True)
    application_python = application / ".venv/bin/python3.11"
    backend_python = backend / ".venv/bin/python3.11"
    for path, raw in (
        (application_python, b"application-python\n"),
        (backend_python, b"backend-python\n"),
        (application / "app.py", b"PAPER = True\n"),
        (backend / "paper_main.py", b"PAPER = True\n"),
    ):
        if path.exists():
            path.chmod(0o600)
        path.write_bytes(raw)
    for path in sorted(stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() or path.name == "python3.11" else 0o444)
    stage.chmod(0o555)

    runtime = private / "runtime"
    semantic = private / "semantic"
    authority = private / "authority"
    for path in (runtime, semantic, authority):
        path.mkdir(mode=0o700, exist_ok=True)
    (semantic / "input").mkdir(mode=0o711, exist_ok=True)
    (semantic / "input").chmod(0o711)
    for name in ("artifacts", "reports", "scratch", "signals"):
        (runtime / name).mkdir(mode=0o700, exist_ok=True)
    generated = NOW
    expires = generated + timedelta(minutes=30)
    timestamp = lambda value: value.strftime("%Y-%m-%dT%H:%M:%SZ")
    safety = {
        "effective_mode": "PAPER",
        "expires_at": timestamp(expires),
        "exporter_commit": COMMIT,
        "generated_at": timestamp(generated),
        "kill_switch_state": "INACTIVE",
        "live_execution_enabled": False,
        "live_trading_approved": False,
        "requested_mode": "PAPER",
        "schema_version": 1,
        "source_fingerprint": "d" * 64,
    }
    safety_path = runtime / "safety-state.json"
    safety_sha256 = _write_fixed(safety_path, safety, 0o600)
    semantic_path = semantic / "active.json"
    semantic_sha256 = _write_fixed(
        semantic_path,
        {
            "classification": "PACKAGE6_PROVIDER_FREE_SEMANTIC",
            "schema_version": 1,
            "source_commit": COMMIT,
        },
    )
    runtime_paths = {
        "artifact_root": runtime / "artifacts",
        "reports_root": runtime / "reports",
        "safety_snapshot": safety_path,
        "scratch_root": runtime / "scratch",
        "semantic_authority": semantic_path,
        "semantic_input_root": semantic / "input",
        "signals_root": runtime / "signals",
    }
    static, _ = build_staging_release_authority_v2(
        stage,
        source_commit=COMMIT,
        source_tree=TREE,
        disposable_root=private,
        production_release_authority_sha256="e" * 64,
        runtime_paths=runtime_paths,
        generated_at=generated,
        expires_at=expires,
    )
    authority_path = authority / "release-authority-v2.json"
    authority_sha256 = _write_fixed(authority_path, static)
    fixture_path = authority / "fixture.json"
    fixture_sha256 = _write_fixed(
        fixture_path,
        {
            "schema_version": 1,
            "provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
            "as_of": timestamp(generated),
            "assets": {
                "BTC": {
                    "current_price": 1,
                    "market_cap": 2,
                    "total_volume": 3,
                    "price_change_percentage_24h": 0,
                }
            },
        },
    )
    components = cast(dict[str, dict[str, str]], static["components"])
    return StagingMaterial(
        private=private,
        stage=stage,
        application=application,
        application_python=application_python,
        backend_python=backend_python,
        authority_path=authority_path,
        authority_sha256=authority_sha256,
        fixture_path=fixture_path,
        fixture_sha256=fixture_sha256,
        safety_sha256=safety_sha256,
        semantic_sha256=semantic_sha256,
        generated=generated,
        expires=expires,
        application_sha256=components["application"]["artifact_set_sha256"],
        backend_sha256=components["backend"]["artifact_set_sha256"],
        command_sha256=canonical_digest(static["command_manifest"]),
    )


def _record(root: Path) -> dict[str, object]:
    source_root = root / "source"
    source_root.mkdir(exist_ok=True)
    bindings = []
    for relative in CANONICAL_BINDINGS:
        binding = source_root / relative
        binding.parent.mkdir(parents=True, exist_ok=True)
        binding.write_text(f"# bound: {relative}\n", encoding="utf-8")
        binding.chmod(0o600)
        bindings.append(
            {"path": relative, "sha256": hashlib.sha256(binding.read_bytes()).hexdigest()}
        )
    material = _staging_material(root)
    fixture_path = material.fixture_path
    fixture_authority_path = material.private / "authority/fixture-authority.json"
    interpreter = material.application_python
    interpreter_sha256 = hashlib.sha256(interpreter.read_bytes()).hexdigest()
    document: dict[str, object] = {
        "record_kind": "PACKAGE6_PAPER_RUNTIME_APPROVAL",
        "schema_version": 2,
        "record_id": "PACKAGE6_RUNTIME_FOUNDATION_001",
        "scope": "PACKAGE6_PAPER_RUNTIME",
        "source": {"commit": COMMIT, "tree": TREE},
        "validity": {
            "approved_at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at_utc": (NOW + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "review": {
            "decision": "APPROVED",
            "operator_identity": "operator.example",
            "reviewer_identity": "reviewer.example",
            "runtime_greenlight": "APPROVE PACKAGE 6 RUNTIME",
        },
        "operations": [
            {
                "operation_id": "job-api.start",
                "action": "START",
                "component": "JOB_API",
                "argv": [str(interpreter), "-I", "-m", "apps.job_api.main"],
                "cwd": str(material.application),
                "bind_host": "127.0.0.1",
                "port": 8401,
                "executable_sha256": interpreter_sha256,
            },
            {
                "operation_id": "job-api.stop",
                "action": "STOP",
                "component": "JOB_API",
                "argv": [],
                "cwd": str(material.application),
                "bind_host": "127.0.0.1",
                "port": 8401,
                "executable_sha256": None,
            },
            {
                "operation_id": "worker.start",
                "action": "START",
                "component": "WORKER",
                "argv": [str(interpreter), "-I", "-m", "services.job_worker.main"],
                "cwd": str(material.application),
                "bind_host": None,
                "port": None,
                "executable_sha256": interpreter_sha256,
            },
            {
                "operation_id": "worker.stop",
                "action": "STOP",
                "component": "WORKER",
                "argv": [],
                "cwd": str(material.application),
                "bind_host": None,
                "port": None,
                "executable_sha256": None,
            },
        ],
        "source_bindings": bindings,
        "constraints": {
            "disposable_root": str(root / "disposable"),
            "evidence_root": str(root / "evidence"),
            "max_processes": 2,
            "startup_timeout_seconds": 10,
            "operation_timeout_seconds": 30,
            "cleanup_timeout_seconds": 10,
            "max_output_bytes": 65536,
            "live_execution_approved": False,
            "live_trading_approved": False,
            "systemd_allowed": False,
            "persistent_services_allowed": False,
            "network_policy": "LOOPBACK_ONLY",
        },
        "postgres_authority": {
            "approval_sha256": PG_APPROVAL,
            "bind_host": "127.0.0.1",
            "port": 18432,
            "database_name": "trading_agent_disposable_test",
            "pgdata": str(root / "disposable" / "pgdata"),
            "cluster_name": "trading-agent-disposable-tests",
            "service_roles": ["trading_job_api", "trading_job_worker"],
        },
        "fixture_authority": {
            "fixture_sha256": material.fixture_sha256,
            "provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
            "path": str(fixture_authority_path),
        },
        "request": {
            "job_type": "SNAPSHOT",
            "actor": "FOUNDATION_VALIDATION",
            "idempotency_key": "foundation:manual:snapshot:foundation-001",
            "expected_job_count": 1,
        },
        "authority_digests": {
            "release": "e" * 64,
            "application": material.application_sha256,
            "backend": material.backend_sha256,
            "command": material.command_sha256,
            "semantic": "f" * 64,
            "fixture": material.fixture_sha256,
            "safety": material.safety_sha256,
        },
        "canonical_record_sha256": "0" * 64,
    }
    document["canonical_record_sha256"] = canonical_record_sha256(document)
    approval_sha256 = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    activation, _ = build_staging_activation_v2(
        authority_sha256=material.authority_sha256,
        package6_approval_sha256=approval_sha256,
        safety_snapshot_sha256=material.safety_sha256,
        safety_exporter_commit=COMMIT,
        safety_source_fingerprint="d" * 64,
        semantic_active_authority_sha256=material.semantic_sha256,
        semantic_version_manifest_sha256=material.semantic_sha256,
        semantic_input_fingerprint="1" * 64,
        semantic_manifest_version="package6-provider-free-v1",
        semantic_policy_sha256="f" * 64,
        semantic_generated_at=material.generated,
        semantic_expires_at=material.expires,
        generated_at=material.generated,
        expires_at=material.expires,
    )
    activation_path = material.private / "authority/release-activation-v2.json"
    _write_fixed(activation_path, activation)
    fixture_authority = {
        "schema_version": 1,
        "classification": "PACKAGE6_PROVIDER_FREE_FIXTURE",
        "package6_approval_sha256": approval_sha256,
        "backend_commit": COMMIT,
        "generated_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (NOW + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fixture_path": str(fixture_path),
        "fixture_sha256": material.fixture_sha256,
    }
    _write_fixed(fixture_authority_path, fixture_authority)
    return document


def _context(root: Path) -> Package6ApprovalContext:
    return Package6ApprovalContext(
        source_commit=COMMIT,
        source_tree=TREE,
        operation_ids=(
            "job-api.start",
            "job-api.stop",
            "worker.start",
            "worker.stop",
        ),
        disposable_postgres_approval_sha256=PG_APPROVAL,
        postgres_bind_host="127.0.0.1",
        postgres_port=18432,
        postgres_database_name="trading_agent_disposable_test",
        postgres_pgdata=str(root / "disposable" / "pgdata"),
        postgres_cluster_name="trading-agent-disposable-tests",
        postgres_service_roles=("trading_job_api", "trading_job_worker"),
        now=NOW + timedelta(minutes=1),
        source_root=root / "source",
        staging_scope=STAGING_SCOPE,
        staging_authority_path=root / "package6-staging/authority/release-authority-v2.json",
        staging_activation_path=root / "package6-staging/authority/release-activation-v2.json",
    )


def _resign(document: dict[str, object]) -> dict[str, object]:
    document["canonical_record_sha256"] = canonical_record_sha256(document)
    return document


def _rebind_dynamic_authorities(
    document: dict[str, object], root: Path
) -> dict[str, object]:
    _resign(document)
    approval_sha256 = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    for relative in (
        "package6-staging/authority/release-activation-v2.json",
        "package6-staging/authority/fixture-authority.json",
    ):
        path = root / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        value["package6_approval_sha256"] = approval_sha256
        _write_fixed(path, value)
    return document


def test_exact_candidate_approval_returns_private_capability(tmp_path: Path) -> None:
    document = _record(tmp_path)

    capability = validate_package6_runtime_approval(document, _context(tmp_path))
    validate_source_binding_files(document, tmp_path / "source")

    assert capability.source_commit == COMMIT
    assert capability.source_tree == TREE
    assert capability.operation_ids == _context(tmp_path).operation_ids
    assert capability.fixture_sha256 == _object(document, "fixture_authority")[
        "fixture_sha256"
    ]
    assert "approval_sha256" not in repr(capability)


def test_job_api_uses_only_canonical_port_8401(tmp_path: Path) -> None:
    document = _record(tmp_path)
    for operation in _objects(document, "operations"):
        if operation["component"] == "JOB_API":
            operation["port"] = 8401
    _resign(document)

    capability = validate_package6_runtime_approval(document, _context(tmp_path))

    assert capability.operations["job-api.start"].port == 8401


def test_arbitrary_executable_and_interpreter_escape_are_rejected(
    tmp_path: Path,
) -> None:
    for argv in (
        ["/bin/sh", "-c", "true"],
        ["/usr/bin/env", "python3.11", "-m", "apps.job_api.main"],
        ["/usr/bin/python3.11", "-c", "print('escape')"],
        ["/usr/bin/python3.11", "-I", "-m", "unapproved.module"],
    ):
        document = _record(tmp_path)
        _objects(document, "operations")[0]["argv"] = argv
        _resign(document)
        with pytest.raises(Package6ApprovalRejected, match="argv|executable|shape"):
            validate_package6_runtime_approval(document, _context(tmp_path))


def test_stop_operations_are_signal_authority_not_new_commands(tmp_path: Path) -> None:
    document = _record(tmp_path)
    for operation in _objects(document, "operations"):
        if operation["action"] == "STOP":
            operation["argv"] = []
    _resign(document)

    capability = validate_package6_runtime_approval(document, _context(tmp_path))

    assert capability.operations["job-api.stop"].argv == ()
    assert capability.operations["worker.stop"].argv == ()


def test_capability_retains_all_typed_runtime_authority(tmp_path: Path) -> None:
    document = _record(tmp_path)
    capability = validate_package6_runtime_approval(document, _context(tmp_path))

    assert capability.postgres.approval_sha256 == PG_APPROVAL
    assert capability.request.idempotency_key == _object(document, "request")[
        "idempotency_key"
    ]
    assert capability.fixture.path == Path(
        cast(str, _object(document, "fixture_authority")["path"])
    )
    assert capability.listener.host == "127.0.0.1"
    assert capability.listener.port == 8401
    assert set(capability.authority_digests) == {
        "release",
        "application",
        "backend",
        "command",
        "semantic",
        "fixture",
        "safety",
    }


@pytest.mark.parametrize(
    "key",
    ("release", "application", "backend", "command", "semantic", "fixture", "safety"),
)
def test_each_syntactically_valid_wrong_authority_digest_is_rejected(
    tmp_path: Path, key: str
) -> None:
    document = _record(tmp_path)
    _object(document, "authority_digests")[key] = "9" * 64
    _rebind_dynamic_authorities(document, tmp_path)

    with pytest.raises(Package6ApprovalRejected, match="digest"):
        validate_package6_runtime_approval(document, _context(tmp_path))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("staging_scope", "PACKAGE6_OTHER"),
        ("staging_authority_path", Path("/tmp/wrong-authority.json")),
        ("staging_activation_path", Path("/tmp/wrong-activation.json")),
        ("source_root", Path("/tmp/wrong-source")),
    ],
)
def test_exact_nonambient_staging_context_is_required(
    tmp_path: Path, field: str, value: object
) -> None:
    document = _record(tmp_path)

    with pytest.raises(Package6ApprovalRejected):
        validate_package6_runtime_approval(
            document, _context(tmp_path)._replace(**{field: value})
        )


@pytest.mark.parametrize(
    "relative",
    (
        "package6-staging/stage/application/app.py",
        "package6-staging/stage/application/.venv/bin/python3.11",
        "package6-staging/runtime/safety-state.json",
        "package6-staging/semantic/active.json",
        "package6-staging/authority/fixture.json",
    ),
)
def test_staged_candidate_dynamic_and_fixture_byte_drift_is_rejected(
    tmp_path: Path, relative: str
) -> None:
    document = _record(tmp_path)
    path = tmp_path / relative
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b"drift\n")
    path.chmod(
        0o555 if path.name == "python3.11" else 0o600 if "safety-state" in relative else 0o444
    )

    with pytest.raises(Package6ApprovalRejected, match="staging|fixture"):
        validate_package6_runtime_approval(document, _context(tmp_path))


def test_source_binding_set_is_canonical_complete_and_ordered(tmp_path: Path) -> None:
    from scripts.validate_package6_runtime_approval import (
        PACKAGE6_SOURCE_BINDING_PATHS,
    )
    from packages.runtime_release.v2 import (
        PAPER_APPLICATION_SOURCE_MAPPING,
        PAPER_BACKEND_SOURCE_MAPPING,
    )

    document = _record(tmp_path)
    assert tuple(item["path"] for item in _objects(document, "source_bindings")) == (
        PACKAGE6_SOURCE_BINDING_PATHS
    )
    release_sources = {
        source
        for _, source in (
            *PAPER_APPLICATION_SOURCE_MAPPING,
            *PAPER_BACKEND_SOURCE_MAPPING,
        )
    }
    assert release_sources <= set(PACKAGE6_SOURCE_BINDING_PATHS)

    _objects(document, "source_bindings").reverse()
    _resign(document)
    with pytest.raises(Package6ApprovalRejected, match="canonical order"):
        validate_package6_runtime_approval(document, _context(tmp_path))


def test_package6_behavior_sources_are_bound_at_exact_reviewed_cardinality() -> None:
    required_behavior_sources = {
        "ops/release-v2/provision-root.sh",
        "ops/release-v2/verify-stage.py",
        "packages/runtime_release/config.py",
    }
    schema = json.loads(
        Path("schemas/package6-paper-runtime-approval.schema.json").read_text(
            encoding="utf-8"
        )
    )
    binding_schema = schema["properties"]["source_bindings"]

    assert required_behavior_sources <= set(PACKAGE6_SOURCE_BINDING_PATHS)
    assert (
        binding_schema["minItems"]
        == binding_schema["maxItems"]
        == len(PACKAGE6_SOURCE_BINDING_PATHS)
        == 76
    )


def test_public_schema_matches_and_enforces_canonical_source_binding_count(
    tmp_path: Path,
) -> None:
    from scripts.validate_package6_runtime_approval import (
        PACKAGE6_SOURCE_BINDING_PATHS,
    )

    schema = json.loads(
        Path("schemas/package6-paper-runtime-approval.schema.json").read_text(
            encoding="utf-8"
        )
    )
    binding_schema = schema["properties"]["source_bindings"]
    assert binding_schema["minItems"] == len(PACKAGE6_SOURCE_BINDING_PATHS)
    assert binding_schema["maxItems"] == len(PACKAGE6_SOURCE_BINDING_PATHS)
    document = _record(tmp_path)
    validate_package6_runtime_approval(document, _context(tmp_path))
    for count in (36, 73, len(PACKAGE6_SOURCE_BINDING_PATHS) - 1):
        stale = dict(document)
        stale["source_bindings"] = _objects(document, "source_bindings")[:count]
        _resign(stale)
        with pytest.raises(Package6ApprovalRejected):
            validate_package6_runtime_approval(stale, _context(tmp_path))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d.update(extra=True), "fields"),
        (lambda d: d.pop("scope"), "fields"),
        (
            lambda d: _object(d, "review").update(operator_identity="TBD"),
            "placeholder",
        ),
        (
            lambda d: _object(d, "validity").update(expires_at_utc="not-a-time"),
            "timestamp",
        ),
        (lambda d: _object(d, "source").update(commit="f" * 40), "candidate"),
        (
            lambda d: _objects(d, "operations")[0].update(
                operation_id="other.start"
            ),
            "operation",
        ),
        (
            lambda d: _objects(d, "operations")[0].update(bind_host="0.0.0.0"),
            "loopback",
        ),
        (lambda d: _objects(d, "operations")[0].update(port=0), "port"),
        (
            lambda d: _objects(d, "operations")[0].update(port=55432),
            "port",
        ),
        (
            lambda d: _object(d, "constraints").update(max_processes=0),
            "process",
        ),
        (
            lambda d: _object(d, "constraints").update(
                operation_timeout_seconds=3601
            ),
            "timeout",
        ),
        (
            lambda d: _object(d, "constraints").update(systemd_allowed=True),
            "systemd",
        ),
        (
            lambda d: _object(d, "constraints").update(
                persistent_services_allowed=True
            ),
            "persistent",
        ),
        (
            lambda d: _object(d, "constraints").update(
                live_execution_approved=True
            ),
            "live",
        ),
        (
            lambda d: _object(d, "constraints").update(
                live_trading_approved=True
            ),
            "live",
        ),
        (
            lambda d: _objects(d, "operations")[0].update(
                argv=["systemctl", "start", "trading-job-api"]
            ),
            "forbidden",
        ),
        (
            lambda d: _objects(d, "operations")[0].update(
                argv=["curl", "https://api.binance.com/order"]
            ),
            "forbidden",
        ),
        (
            lambda d: _object(d, "constraints").update(
                evidence_root="/var/lib/trading-agent/evidence"
            ),
            "root",
        ),
        (
            lambda d: _object(d, "postgres_authority").update(
                approval_sha256="c" * 64
            ),
            "PostgreSQL",
        ),
        (
            lambda d: _object(d, "fixture_authority").update(
                provenance="COINGECKO_PUBLIC"
            ),
            "fixture",
        ),
    ],
)
def test_forbidden_runtime_boundaries_fail_closed(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], object],
    message: str,
) -> None:
    document = _record(tmp_path)
    mutation(document)
    _resign(document)

    with pytest.raises(Package6ApprovalRejected, match=message):
        validate_package6_runtime_approval(document, _context(tmp_path))


def test_expired_and_excessive_validity_fail_closed(tmp_path: Path) -> None:
    for approved, expires, now in (
        ("2026-07-26T12:00:00Z", "2026-07-26T12:31:00Z", "2026-07-26T12:10:00Z"),
        ("2026-07-26T12:00:00Z", "2026-07-26T12:30:00Z", "2026-07-26T12:31:00Z"),
    ):
        document = _record(tmp_path)
        document["validity"] = {
            "approved_at_utc": approved,
            "expires_at_utc": expires,
        }
        _resign(document)
        context = _context(tmp_path)._replace(
            now=datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        )
        with pytest.raises(Package6ApprovalRejected, match="validity|expired"):
            validate_package6_runtime_approval(document, context)


def test_startup_requires_paired_explicit_start_and_stop(tmp_path: Path) -> None:
    document = _record(tmp_path)
    document["operations"] = _objects(document, "operations")[:-1]
    _resign(document)

    with pytest.raises(Package6ApprovalRejected, match="START.*STOP"):
        validate_package6_runtime_approval(document, _context(tmp_path))


@pytest.mark.parametrize(
    "relative",
    (
        "ops/release-v2/provision-root.sh",
        "ops/release-v2/verify-stage.py",
        "packages/runtime_release/config.py",
    ),
)
def test_each_new_behavior_source_binding_rejects_file_drift(
    tmp_path: Path, relative: str
) -> None:
    document = _record(tmp_path)
    binding = tmp_path / "source" / relative
    binding.write_bytes(binding.read_bytes() + b"# drift\n")

    with pytest.raises(Package6ApprovalRejected, match="digest"):
        validate_source_binding_files(document, tmp_path / "source")


def test_source_binding_rejects_drift_symlink_non_regular_and_traversal(
    tmp_path: Path,
) -> None:
    document = _record(tmp_path)
    binding = tmp_path / "source" / CANONICAL_BINDINGS[0]
    binding.write_text("DRIFT = True\n", encoding="utf-8")
    with pytest.raises(Package6ApprovalRejected, match="digest"):
        validate_source_binding_files(document, tmp_path / "source")

    binding.unlink()
    binding.symlink_to(tmp_path / "target.py")
    with pytest.raises(Package6ApprovalRejected, match="regular"):
        validate_source_binding_files(document, tmp_path / "source")

    document = _record(tmp_path)
    _objects(document, "source_bindings")[0]["path"] = "../bound.py"
    _resign(document)
    with pytest.raises(Package6ApprovalRejected, match="path"):
        validate_source_binding_files(document, tmp_path / "source")


def test_validator_is_side_effect_free(tmp_path: Path, monkeypatch) -> None:
    document = _record(tmp_path)
    called: list[str] = []
    monkeypatch.setattr(os, "system", lambda _value: called.append("system"))

    validate_package6_runtime_approval(document, _context(tmp_path))

    assert called == []
    assert not (tmp_path / "disposable").exists()
