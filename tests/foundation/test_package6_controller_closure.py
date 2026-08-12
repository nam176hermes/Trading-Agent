from __future__ import annotations

import ast
import dis
import errno
import fcntl
import gc
import hashlib
import importlib.machinery
import importlib.util
import inspect
import json
import os
from pathlib import Path
import signal
import stat
import sys
import types
from typing import Any, Callable, cast

import pytest

from tests.foundation._package6_staging_fixture import (
    Package6StagingLease,
    create_package6_staging_lease,
    package6_staging_lease,
)
from services.paper_runtime import evidence as evidence_module
from services.paper_runtime.evidence import (
    EvidenceIncomplete,
    _validate_closure_custodian_authority,
)
from scripts import finalize_package6_controller_evidence as finalizer_script
from scripts.finalize_package6_controller_evidence import _parser
from scripts.validate_package6_runtime_approval import (
    PACKAGE6_CUSTODIAN_OPERATIONS,
    PACKAGE6_CUSTODIAN_SOURCE_PATHS,
)


ROOT = Path(__file__).resolve().parents[2]
HEX40_A = "a" * 40
HEX40_B = "b" * 40
HEX64_C = "c" * 64
HEX64_D = "d" * 64
HEX64_E = "e" * 64
HEX64_F = "f" * 64
EXPECTED_REVIEWED_PATHS = (
    "Makefile",
    "docs/implementation/package6-descriptor-custody-fix.md",
    "docs/implementation/package6-single-container-publication.md",
    "docs/plans/trading-agent-foundation-upgrade-2026-07-22/"
    "06-paper-runtime-foundation-validation.md",
    "docs/plans/trading-agent-foundation-upgrade-2026-07-22/"
    "06b-native-custody-authority-r11-design.md",
    "docs/plans/trading-agent-foundation-upgrade-2026-07-22/"
    "06c-package6-release-authority-v2-closure-plan.md",
    "native/package6_custodian/Makefile",
    "native/package6_custodian/include/p6c_protocol.h",
    "native/package6_custodian/include/p6c_types.h",
    "native/package6_custodian/src/cgroup.c",
    "native/package6_custodian/src/journal.c",
    "native/package6_custodian/src/linux_authority.c",
    "native/package6_custodian/src/main.c",
    "native/package6_custodian/src/process.c",
    "native/package6_custodian/src/protocol.c",
    "native/package6_custodian/src/publication.c",
    "native/package6_custodian/src/python_fd_custody.c",
    "native/package6_custodian/src/sha256.c",
    "native/package6_custodian/src/transcript.c",
    "native/package6_custodian/tests/test_authority.c",
    "native/package6_custodian/tests/test_protocol.c",
    "native/package6_custodian/tests/test_publication.c",
    "native/package6_custodian/tests/test_service_main.c",
    "packages/runtime_release/supervisor_v2.py",
    "schemas/package6-paper-runtime-approval.schema.json",
    "scripts/check_test_governance.py",
    "scripts/finalize_package6_controller_evidence.py",
    "scripts/validate_package6_runtime_approval.py",
    "services/paper_runtime/__init__.py",
    "services/paper_runtime/controller.py",
    "services/paper_runtime/custodian_client.py",
    "services/paper_runtime/evidence.py",
    "services/paper_runtime/integration.py",
    "tests/foundation/test_package6_controller_closure.py",
    "tests/foundation/test_package6_custodian_contract.py",
    "tests/foundation/test_package6_runtime_approval.py",
    "tests/foundation/test_package6_runtime_controller.py",
    "tests/foundation/test_package6_runtime_integration.py",
    "tests/governance/test_test_governance.py",
    "tests/native/test_package6_custodian.py",
    "tests/runtime_release/test_supervisor_v2.py",
    "tests/skip-allowlist.yaml",
)


def _closure_documents() -> tuple[dict[str, object], dict[str, object]]:
    native_source_set = [
        {"path": path, "sha256": f"{index + 1:064x}"}
        for index, path in enumerate(PACKAGE6_CUSTODIAN_SOURCE_PATHS)
    ]
    native_source_set_sha256 = hashlib.sha256(
        json.dumps(
            native_source_set,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    approval = {
        "custodian_authority": {
            "authority_mode": "DISPOSABLE_TEST_NATIVE_ONLY",
            "helper_binary_sha256": HEX64_C,
            "native_source_set": native_source_set,
            "native_source_set_sha256": native_source_set_sha256,
            "protocol_version": "1",
            "protocol_features": [],
            "endpoint_authority": "PREOPENED_UNIX_SEQPACKET_DESCRIPTOR",
            "production_socket_activation": False,
            "operations": list(PACKAGE6_CUSTODIAN_OPERATIONS),
            "candidate_commit": HEX40_A,
            "candidate_tree": HEX40_B,
            "stage_sha256": HEX64_E,
            "fixture_identity": {
                "sha256": HEX64_F,
                "provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
            },
            "child_environment_contract": {
                "job_api": list(
                    getattr(evidence_module, "PACKAGE6_JOB_API_ENVIRONMENT_KEYS")
                ),
                "worker": list(
                    getattr(evidence_module, "PACKAGE6_WORKER_ENVIRONMENT_KEYS")
                ),
            },
            "mode": "PAPER",
            "live_execution_approved": False,
            "live_trading_approved": False,
        }
    }
    runtime = {
        "source": {"commit": HEX40_A, "tree": HEX40_B},
        "chain": {
            "job_api_stop": {"native_operation_id": "1" * 32},
            "worker_stop": {"native_operation_id": "3" * 32},
            "native_publications": {
                "job_api": {
                    "operation_id": "1" * 32,
                    "manifest_sha256": "2" * 64,
                },
                "worker": {
                    "operation_id": "3" * 32,
                    "manifest_sha256": "4" * 64,
                },
            }
        },
    }
    return approval, runtime


def _authority_arguments() -> dict[str, object]:
    approval, _runtime = _closure_documents()
    authority = cast(dict[str, object], approval["custodian_authority"])
    return {
        "candidate_commit": HEX40_A,
        "candidate_tree": HEX40_B,
        "custodian_helper_binary_sha256": HEX64_C,
        "custodian_native_source_set_sha256": authority[
            "native_source_set_sha256"
        ],
        "custodian_protocol_version": 1,
        "custodian_protocol_features": [],
        "custodian_endpoint_authority": (
            "PREOPENED_UNIX_SEQPACKET_DESCRIPTOR"
        ),
        "custodian_operations": list(PACKAGE6_CUSTODIAN_OPERATIONS),
        "custodian_stage_sha256": HEX64_E,
        "custodian_fixture_sha256": HEX64_F,
        "custodian_publications": [
            f"job_api={'2' * 64}",
            f"worker={'4' * 64}",
        ],
    }


def test_controller_source_has_no_python_target_custody() -> None:
    source = (ROOT / "services/paper_runtime/controller.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "ctypes" not in imported
    assert "subprocess" not in imported
    for forbidden in (
        "Popen",
        "killpg",
        "waitpid",
        "pidfd",
        "process_group",
        "transcript.unlink",
        "os.unlink",
    ):
        assert forbidden not in source


def test_controller_delegates_the_complete_native_operation_set() -> None:
    source = (ROOT / "services/paper_runtime/controller.py").read_text(
        encoding="utf-8"
    )
    for method in (
        "start",
        "status",
        "stop",
        "recover",
        "run_once",
        "read_transcript",
        "publish_bundle",
        "acknowledge",
    ):
        assert f"self._client.{method}(" in source


def test_aggregate_evidence_has_no_target_transcript_file_authority() -> None:
    source = inspect.getsource(evidence_module)
    validator = inspect.getsource(evidence_module._validate_stop_transcripts)

    assert '"path"' not in validator
    assert "_bind_stop_transcript_files" not in source
    assert "_open_transcript_root" not in source
    assert "_read_bound_transcript" not in source


def test_runtime_chain_publishes_before_acknowledgement() -> None:
    source = (
        ROOT / "services/paper_runtime/integration.py"
    ).read_text(encoding="utf-8")
    publish = source.index("controller.publish_evidence(")
    acknowledge = source.index("controller.acknowledge_stop(")

    assert publish < acknowledge
    assert "custodian_client: CustodianClient" in source
    assert "native_publications" in source


def test_finalizer_cli_requires_native_closure_authority() -> None:
    actions = {
        action.dest: action
        for action in _parser()._actions
        if action.dest != "help"
    }
    required = {
        "custodian_helper_binary_sha256",
        "custodian_native_source_set_sha256",
        "custodian_protocol_version",
        "custodian_endpoint_authority",
        "custodian_operations",
        "custodian_stage_sha256",
        "custodian_fixture_sha256",
        "custodian_publications",
        "expected_seal_manifest_sha256",
    }

    assert required <= set(actions)
    assert all(actions[name].required for name in required)
    assert "custodian_protocol_features" in actions


def test_finalizer_cli_recovers_and_closes_failure_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeAuthority:
        publication_committed = True
        publication_commit_uncertain = False
        recovery_required = True

        def recover(self) -> bool:
            events.append("recover")
            self.recovery_required = False
            return True

        def close(self) -> bool:
            events.append("close")
            return True

    authority = FakeAuthority()

    def fail_finalization(**_arguments: object) -> object:
        raise evidence_module.FinalPublicationFailure(
            cast(Any, authority)
        )

    monkeypatch.setattr(
        finalizer_script,
        "_parser",
        lambda: types.SimpleNamespace(
            parse_args=lambda: types.SimpleNamespace()
        ),
    )
    monkeypatch.setattr(
        finalizer_script,
        "finalize_controller_evidence",
        fail_finalization,
    )

    with pytest.raises(EvidenceIncomplete, match="recovered"):
        finalizer_script.main()

    assert events == ["recover", "close"]
    assert authority.recovery_required is False


def test_closure_record_binds_exact_native_authority_and_publications() -> None:
    approval, runtime = _closure_documents()
    authority = _validate_closure_custodian_authority(
        approval=approval,
        runtime=runtime,
        **_authority_arguments(),
    )

    assert authority == {
        **cast(dict[str, object], approval["custodian_authority"]),
        "publications": {
            "job_api": {
                "operation_id": "1" * 32,
                "manifest_sha256": "2" * 64,
            },
            "worker": {
                "operation_id": "3" * 32,
                "manifest_sha256": "4" * 64,
            },
        },
    }
    assert "path" not in str(authority["publications"])


@pytest.mark.parametrize(
    "case",
    (
        "helper",
        "source-set",
        "protocol-version",
        "protocol-features",
        "endpoint",
        "operations-missing",
        "operations-reordered",
        "candidate-commit",
        "candidate-tree",
        "stage",
        "fixture",
        "mode",
        "live-execution",
        "live-trading",
        "publication-missing",
        "publication-extra",
        "publication-digest",
        "publication-operation",
        "publication-runtime-mismatch",
    ),
)
def test_closure_rejects_each_native_authority_tamper(
    case: str,
) -> None:
    approval, runtime = _closure_documents()
    arguments = _authority_arguments()
    authority = cast(dict[str, object], approval["custodian_authority"])
    if case == "helper":
        arguments["custodian_helper_binary_sha256"] = "0" * 64
    elif case == "source-set":
        arguments["custodian_native_source_set_sha256"] = "0" * 64
    elif case == "protocol-version":
        arguments["custodian_protocol_version"] = 2
    elif case == "protocol-features":
        arguments["custodian_protocol_features"] = ["PIDFD_DELEGATION"]
    elif case == "endpoint":
        arguments["custodian_endpoint_authority"] = "FILESYSTEM_SOCKET"
    elif case == "operations-missing":
        cast(list[str], arguments["custodian_operations"]).pop()
    elif case == "operations-reordered":
        cast(list[str], arguments["custodian_operations"]).reverse()
    elif case == "candidate-commit":
        arguments["candidate_commit"] = "0" * 40
    elif case == "candidate-tree":
        arguments["candidate_tree"] = "0" * 40
    elif case == "stage":
        arguments["custodian_stage_sha256"] = "0" * 64
    elif case == "fixture":
        arguments["custodian_fixture_sha256"] = "0" * 64
    elif case == "mode":
        authority["mode"] = "LIVE"
    elif case == "live-execution":
        authority["live_execution_approved"] = True
    elif case == "live-trading":
        authority["live_trading_approved"] = True
    elif case == "publication-missing":
        cast(list[str], arguments["custodian_publications"]).pop()
    elif case == "publication-extra":
        cast(list[str], arguments["custodian_publications"]).append(
            f"extra={'5' * 64}"
        )
    elif case == "publication-digest":
        cast(list[str], arguments["custodian_publications"])[0] = (
            f"job_api={'0' * 64}"
        )
    elif case == "publication-operation":
        publications = cast(
            dict[str, dict[str, str]],
            cast(dict[str, object], runtime["chain"])[
                "native_publications"
            ],
        )
        publications["worker"]["operation_id"] = "1" * 32
    elif case == "publication-runtime-mismatch":
        publications = cast(
            dict[str, dict[str, str]],
            cast(dict[str, object], runtime["chain"])[
                "native_publications"
            ],
        )
        publications["worker"]["manifest_sha256"] = "0" * 64
    else:  # pragma: no cover - exhaustive cases
        raise AssertionError(case)

    with pytest.raises(EvidenceIncomplete, match="custodian"):
        _validate_closure_custodian_authority(
            approval=approval,
            runtime=runtime,
            **arguments,
        )


def _private_json(root: Path, name: str, value: object) -> Path:
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    path = root / name
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _finalizer_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    lease: Package6StagingLease,
) -> tuple[dict[str, Any], str]:
    from tests.foundation.test_package6_runtime_controller import (
        _sealed_runtime_fixture,
    )

    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir(mode=0o700)
    fixture = _sealed_runtime_fixture(fixture_root, monkeypatch, lease=lease)
    runtime_snapshot = evidence_module._load_runtime_evidence_snapshot(
        fixture.bundle
    )
    runtime = json.loads(runtime_snapshot["runtime.json"])
    approval = json.loads(runtime_snapshot["approval.json"])
    candidate_commit = cast(str, runtime["source"]["commit"])
    candidate_tree = cast(str, runtime["source"]["tree"])
    source_diff_sha256 = "7" * 64
    reviewed_base_commit = "8" * 40
    reviewed_patch_sha256 = source_diff_sha256
    expected_seal_manifest_sha256 = "9" * 64
    reviewed_paths = list(EXPECTED_REVIEWED_PATHS)
    candidate_root = Path(cast(str, runtime["disposable_root"]))
    assert candidate_root == fixture.disposable_root
    assert list(candidate_root.iterdir()) == []
    candidate_root.rmdir()
    postgres_root = Path(cast(str, approval["postgres_authority"]["pgdata"]))
    if postgres_root.exists():
        assert list(postgres_root.iterdir()) == []
        postgres_root.rmdir()

    inputs = tmp_path / "inputs"
    review = _private_json(
        inputs,
        "review.json",
        {
            "schema_version": 1,
            "verdict": "PASS",
            "reviewed_base_commit": reviewed_base_commit,
            "patch_algorithm": "PACKAGE6_GOAL2_PATCH_V1",
            "reviewed_patch_sha256": reviewed_patch_sha256,
            "reviewed_patch_bytes": 12345,
            "reviewed_paths": reviewed_paths,
            "source_diff_sha256": source_diff_sha256,
            "findings": [],
            "scope_integrity": "PASS",
            "test_adequacy": "PASS",
            "seal_manifest_sha256": expected_seal_manifest_sha256,
            "seal_integrity": "PASS",
            "production_authority_status": "TEST_ONLY",
            "live_execution_approved": False,
            "live_trading_approved": False,
        },
    )
    transcript_metadata: dict[str, dict[str, dict[str, object]]] = {}
    for component, marker in (("worker", "1"), ("job_api", "2")):
        transcript_metadata[component] = {}
        for stream_name in ("stdout", "stderr"):
            raw = f"{component}-{stream_name}".encode()
            transcript_metadata[component][stream_name] = {
                "path": str(
                    tmp_path.resolve()
                    / "diagnostics"
                    / (
                        f"package6-{component}-{marker * 32}."
                        f"{stream_name}.transcript"
                    )
                ),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "truncated": False,
            }
    diagnostic = _private_json(
        inputs,
        "diagnostic.json",
        {
            "schema_version": 1,
            "verdict": "PASS",
            "candidate_commit": candidate_commit,
            "candidate_tree": candidate_tree,
            "source_diff_sha256": source_diff_sha256,
            "runtime_attempt": "D1",
            "test_nodeid": (
                "tests/foundation/test_package6_runtime_integration.py::"
                "test_complete_package6_runtime_chain"
            ),
            "exit_code": 0,
            "passed": 1,
            "failed": 0,
            "transcript_metadata": transcript_metadata,
            "live_execution_approved": False,
            "live_trading_approved": False,
        },
    )
    cleanup = _private_json(
        inputs,
        "cleanup.json",
        {
            "schema_version": 1,
            "candidate_commit": candidate_commit,
            "candidate_tree": candidate_tree,
            "source_diff_sha256": source_diff_sha256,
            "process_refs": 0,
            "surviving_processes": [],
            "surviving_listener_ports": [],
            "candidate_root": str(candidate_root),
            "candidate_root_exists": False,
            "postgres_root": str(postgres_root),
            "postgres_root_exists": False,
            "evidence_preserved_outside_disposable_root": True,
            "live_execution_approved": False,
            "live_trading_approved": False,
        },
    )
    output = tmp_path / "closure"
    output.mkdir(mode=0o700)
    output.chmod(0o700)
    authority = cast(dict[str, object], approval["custodian_authority"])
    publications = cast(
        dict[str, dict[str, str]],
        runtime["chain"]["native_publications"],
    )
    fixture_identity = cast(dict[str, object], authority["fixture_identity"])
    arguments: dict[str, Any] = {
        "runtime_bundle": fixture.bundle,
        "output_dir": output,
        "cleanup_path": cleanup,
        "review_path": review,
        "diagnostic_index_path": diagnostic,
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
        "reviewed_base_commit": reviewed_base_commit,
        "patch_algorithm": "PACKAGE6_GOAL2_PATCH_V1",
        "reviewed_patch_sha256": reviewed_patch_sha256,
        "reviewed_patch_bytes": 12345,
        "reviewed_paths": reviewed_paths,
        "source_diff_sha256": source_diff_sha256,
        "expected_seal_manifest_sha256": expected_seal_manifest_sha256,
        "review_verdict_sha256": hashlib.sha256(review.read_bytes()).hexdigest(),
        "diagnostic_index_sha256": hashlib.sha256(
            diagnostic.read_bytes()
        ).hexdigest(),
        "runtime_bundle_index_sha256": hashlib.sha256(
            runtime_snapshot["index.json"]
        ).hexdigest(),
        "cleanup_evidence_sha256": hashlib.sha256(
            cleanup.read_bytes()
        ).hexdigest(),
        "custodian_helper_binary_sha256": authority["helper_binary_sha256"],
        "custodian_native_source_set_sha256": authority[
            "native_source_set_sha256"
        ],
        "custodian_protocol_version": 1,
        "custodian_protocol_features": authority["protocol_features"],
        "custodian_endpoint_authority": authority["endpoint_authority"],
        "custodian_operations": authority["operations"],
        "custodian_stage_sha256": authority["stage_sha256"],
        "custodian_fixture_sha256": fixture_identity["sha256"],
        "custodian_publications": [
            f"{component}={publications[component]['manifest_sha256']}"
            for component in ("job_api", "worker")
        ],
    }
    before = hashlib.sha256(fixture.bundle.read_bytes()).hexdigest()
    return arguments, before


def test_finalizer_arguments_retains_lease_until_outer_fixture_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The closure completes on one live lease; only the outer fixture ends it."""

    class PortableDescriptorOwner:
        """Test-only owner over the real descriptor required by this proof."""

        def __init__(self, descriptor: int) -> None:
            self.descriptor = descriptor
            info = os.fstat(descriptor)
            self.identity = (info.st_dev, info.st_ino)
            self._closed = False

        def abandon_uncertain_generation(self) -> None:
            self.close()

        def close(self) -> bool:
            if self._closed:
                return True
            try:
                os.close(self.descriptor)
            except OSError:
                return False
            self._closed = True
            return True

    class PortableDescriptorCustody:
        """The narrow missing-extension seam; all opens remain real OS opens."""

        @staticmethod
        def open(path: bytes, flags: int, mode: int) -> PortableDescriptorOwner:
            return PortableDescriptorOwner(os.open(path, flags, mode))

        @staticmethod
        def openat(
            directory: int, path: bytes, flags: int, mode: int
        ) -> PortableDescriptorOwner:
            return PortableDescriptorOwner(
                os.open(path, flags, mode, dir_fd=directory)
            )

    fixture_lifetime = package6_staging_lease.__wrapped__()
    lease = next(fixture_lifetime)
    root = lease.root
    monkeypatch.setattr(
        evidence_module, "_NATIVE_FD_CUSTODY", PortableDescriptorCustody()
    )
    try:
        arguments, before = _finalizer_arguments(tmp_path, monkeypatch, lease=lease)
        runtime_bundle = cast(Path, arguments["runtime_bundle"])
        assert runtime_bundle.is_file()
        assert before == hashlib.sha256(runtime_bundle.read_bytes()).hexdigest()
        assert cast(Path, arguments["review_path"]).is_file()
        assert cast(Path, arguments["diagnostic_index_path"]).is_file()
        assert cast(Path, arguments["cleanup_path"]).is_file()
        assert cast(Path, arguments["output_dir"]).is_dir()
        lease.assert_valid()
        assert root.is_dir()
    finally:
        with pytest.raises(StopIteration):
            next(fixture_lifetime)

    assert not root.exists()


_CONTROLLER_FINAL_NAME = "package6-controller-final.json"


def _run_finalizer_child(
    arguments: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    install: Callable[[], None] | None = None,
) -> int:
    child = os.fork()
    if child == 0:
        try:
            if install is not None:
                install()
            authority = evidence_module.finalize_controller_evidence(**arguments)
            if authority is not None:
                assert authority.revalidate_identity() is True
                assert authority.close() is True
            os._exit(0)
        except BaseException:
            os._exit(91)
    _pid, status = os.waitpid(child, 0)
    return status


def _kill_child() -> None:
    os.kill(os.getpid(), signal.SIGKILL)


def test_controller_result_name_absent_before_commit_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    output = cast(Path, arguments["output_dir"])
    real_link = evidence_module._link_owned_tmpfile

    def inspect_then_kill(descriptor: int, directory: int, name: str) -> None:
        assert name == _CONTROLLER_FINAL_NAME
        assert list(output.iterdir()) == []
        _kill_child()
        real_link(descriptor, directory, name)

    status = _run_finalizer_child(
        arguments,
        monkeypatch,
        lambda: monkeypatch.setattr(
            evidence_module, "_link_owned_tmpfile", inspect_then_kill
        ),
    )
    assert os.waitstatus_to_exitcode(status) == -signal.SIGKILL
    assert list(output.iterdir()) == []


def test_controller_result_has_single_atomic_commit_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    output = cast(Path, arguments["output_dir"])
    counter = tmp_path / "link-count"
    real_link = evidence_module._link_owned_tmpfile

    def counted_link(descriptor: int, directory: int, name: str) -> None:
        counter.write_text(
            str(int(counter.read_text()) + 1) if counter.exists() else "1"
        )
        real_link(descriptor, directory, name)
        _kill_child()

    status = _run_finalizer_child(
        arguments,
        monkeypatch,
        lambda: monkeypatch.setattr(
            evidence_module, "_link_owned_tmpfile", counted_link
        ),
    )
    assert os.waitstatus_to_exitcode(status) == -signal.SIGKILL
    assert counter.read_text() == "1"
    assert [path.name for path in output.iterdir()] == [_CONTROLLER_FINAL_NAME]


def test_controller_result_never_exposes_partial_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    output = cast(Path, arguments["output_dir"])
    real_write = evidence_module.os.write
    writes = 0

    def kill_during_write(descriptor: int, raw: object) -> int:
        nonlocal writes
        writes += 1
        written = real_write(descriptor, raw)
        if writes == 1:
            _kill_child()
        return written

    status = _run_finalizer_child(
        arguments,
        monkeypatch,
        lambda: monkeypatch.setattr(evidence_module.os, "write", kill_during_write),
    )
    assert os.waitstatus_to_exitcode(status) == -signal.SIGKILL
    assert list(output.iterdir()) == []


def test_controller_result_descriptor_matches_final_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    output = cast(Path, arguments["output_dir"])
    proof = tmp_path / "descriptor-path-identity.json"
    real_confirm = evidence_module._confirm_final_publication_identity

    def capture_identity(authority: object) -> None:
        descriptor_authority = authority.canonical_descriptor_authority  # type: ignore[attr-defined]
        directory_authority = authority.output_directory_authority  # type: ignore[attr-defined]
        descriptor_info = os.fstat(descriptor_authority.descriptor)
        path_info = os.stat(
            authority.final_name,  # type: ignore[attr-defined]
            dir_fd=directory_authority.descriptor,
            follow_symlinks=False,
        )
        proof.write_text(
            json.dumps(
                {
                    "descriptor": [descriptor_info.st_dev, descriptor_info.st_ino],
                    "path": [path_info.st_dev, path_info.st_ino],
                },
                sort_keys=True,
            )
        )
        real_confirm(authority)  # type: ignore[arg-type]
        _kill_child()

    status = _run_finalizer_child(
        arguments,
        monkeypatch,
        lambda: monkeypatch.setattr(
            evidence_module,
            "_confirm_final_publication_identity",
            capture_identity,
        ),
    )
    assert os.waitstatus_to_exitcode(status) == -signal.SIGKILL
    observed = json.loads(proof.read_text())
    assert observed["descriptor"] == observed["path"]
    assert [path.name for path in output.iterdir()] == [_CONTROLLER_FINAL_NAME]


def test_controller_result_mutation_after_check_blocks_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    output = cast(Path, arguments["output_dir"])
    runtime = cast(Path, arguments["runtime_bundle"])
    real_revalidate = evidence_module._RuntimeEvidenceSnapshot.revalidate
    calls = 0

    def mutate_before_revalidate(snapshot: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            runtime.write_bytes(runtime.read_bytes() + b"x")
        try:
            real_revalidate(snapshot)  # type: ignore[arg-type]
        except EvidenceIncomplete:
            _kill_child()
            raise

    status = _run_finalizer_child(
        arguments,
        monkeypatch,
        lambda: monkeypatch.setattr(
            evidence_module._RuntimeEvidenceSnapshot,
            "revalidate",
            mutate_before_revalidate,
        ),
    )
    assert os.waitstatus_to_exitcode(status) == -signal.SIGKILL
    assert list(output.iterdir()) == []


def test_controller_result_commit_reads_retained_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    output = cast(Path, arguments["output_dir"])
    real_read = evidence_module._read_final_publication
    reads = 0

    def poison(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("final output pathname reopen is forbidden")

    def read_then_kill(
        descriptor: int,
        *,
        expected_link_count: int,
    ) -> tuple[bytes, tuple[int, ...]]:
        nonlocal reads
        result = real_read(
            descriptor,
            expected_link_count=expected_link_count,
        )
        reads += 1
        if reads == 2:
            _kill_child()
        return result

    def install() -> None:
        monkeypatch.setattr(Path, "read_bytes", poison)
        monkeypatch.setattr(Path, "read_text", poison)
        monkeypatch.setattr(
            evidence_module,
            "_read_final_publication",
            read_then_kill,
        )

    status = _run_finalizer_child(
        arguments,
        monkeypatch,
        install,
    )
    assert os.waitstatus_to_exitcode(status) == -signal.SIGKILL
    assert list(output.iterdir()) == []


def test_crash_boundary_loops_own_a_fresh_lease_until_scenario_completion() -> None:
    """Each crash boundary needs an isolated real staging-fixture lifetime."""

    source = Path(__file__).read_text(encoding="utf-8")
    module = ast.parse(source)
    functions = {
        node.name: node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
    }

    for name in (
        "test_controller_result_crash_before_link_leaves_no_output",
        "test_controller_result_crash_after_link_has_one_complete_output",
    ):
        function = functions[name]
        arguments = (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
        assert all(argument.arg != "package6_staging_lease" for argument in arguments)

        loops = [node for node in function.body if isinstance(node, ast.For)]
        assert len(loops) == 1
        loop = loops[0]
        factory_assignments = [
            statement
            for statement in loop.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == "create_package6_staging_lease"
        ]
        assert len(factory_assignments) == 1
        factory_assignment = factory_assignments[0]
        lease_name = cast(ast.Name, factory_assignment.targets[0]).id
        scenario = next(
            (
                statement
                for statement in loop.body
                if isinstance(statement, ast.Try)
            ),
            None,
        )
        assert scenario is not None
        assert loop.body.index(factory_assignment) < loop.body.index(scenario)

        finalizer_calls = [
            node
            for node in ast.walk(scenario)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_finalizer_arguments"
        ]
        assert len(finalizer_calls) == 1
        assert any(
            keyword.arg == "lease"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == lease_name
            for keyword in finalizer_calls[0].keywords
        )

        child_calls = [
            node
            for node in ast.walk(scenario)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_run_finalizer_child"
        ]
        assert len(child_calls) == 1
        child_statement_index = next(
            index
            for index, statement in enumerate(scenario.body)
            if child_calls[0] in ast.walk(statement)
        )
        assert any(
            isinstance(statement, ast.Assert)
            for statement in scenario.body[child_statement_index + 1 :]
        )
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == lease_name
            and node.func.attr == "cleanup"
            for statement in scenario.finalbody
            for node in ast.walk(statement)
        )
        scenario_index = loop.body.index(scenario)
        assert any(
            isinstance(statement, ast.Assert)
            for statement in loop.body[scenario_index + 1 :]
        )


def test_controller_result_crash_before_link_leaves_no_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundaries = (
        "open",
        "file-fsync",
        "retained-read",
        "semantic",
        "revalidate",
        "second-retained-read",
    )
    for boundary in boundaries:
        case_lease = create_package6_staging_lease()
        lease_root = case_lease.root
        evidence_root = lease_root / "evidence"
        try:
            case_lease.assert_valid()
            assert lease_root.parent == Path("/tmp")
            assert not evidence_root.exists()

            case = tmp_path / boundary
            case.mkdir(mode=0o700)
            arguments, _before = _finalizer_arguments(
                case, monkeypatch, lease=case_lease
            )
            output = cast(Path, arguments["output_dir"])
            assert evidence_root.is_dir()
            case_lease.assert_valid()
            real_open = evidence_module._open_publication_tmpfile
            real_fsync = evidence_module.os.fsync
            real_read = getattr(evidence_module, "_read_final_publication", None)
            real_verify = getattr(
                evidence_module, "_verify_controller_final_container", None
            )
            real_revalidate = evidence_module._RuntimeEvidenceSnapshot.revalidate
            reads = 0
            revalidations = 0

            def install() -> None:
                if boundary == "open":
                    monkeypatch.setattr(
                        evidence_module,
                        "_open_publication_tmpfile",
                        lambda *args, **kwargs: (_kill_child()),
                    )
                elif boundary == "file-fsync":
                    monkeypatch.setattr(
                        evidence_module.os,
                        "fsync",
                        lambda fd: _kill_child()
                        if stat.S_ISREG(os.fstat(fd).st_mode)
                        else real_fsync(fd),
                    )
                elif boundary in {"retained-read", "second-retained-read"}:
                    assert callable(real_read)
                    retained_reader = cast(Callable[..., object], real_read)

                    def kill_on_retained_read(
                        *args: object,
                        **kwargs: object,
                    ) -> object:
                        nonlocal reads
                        reads += 1
                        if boundary == "retained-read" or reads == 2:
                            _kill_child()
                        return retained_reader(*args, **kwargs)

                    monkeypatch.setattr(
                        evidence_module,
                        "_read_final_publication",
                        kill_on_retained_read,
                    )
                elif boundary == "semantic":
                    assert callable(real_verify)
                    monkeypatch.setattr(
                        evidence_module,
                        "_verify_controller_final_container",
                        lambda *a: _kill_child(),
                    )
                else:
                    def kill_on_second_revalidation(snapshot: object) -> None:
                        nonlocal revalidations
                        revalidations += 1
                        if revalidations == 2:
                            _kill_child()
                        real_revalidate(snapshot)  # type: ignore[arg-type]

                    monkeypatch.setattr(
                        evidence_module._RuntimeEvidenceSnapshot,
                        "revalidate",
                        kill_on_second_revalidation,
                    )

            status = _run_finalizer_child(arguments, monkeypatch, install)
            assert os.waitstatus_to_exitcode(status) == -signal.SIGKILL, boundary
            assert list(output.iterdir()) == []
            case_lease.assert_valid()
            assert lease_root.is_dir()
            assert evidence_root.is_dir()
        finally:
            case_lease.cleanup()

        assert not lease_root.exists()
        assert not evidence_root.exists()


def test_controller_result_crash_after_link_has_one_complete_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for boundary in ("link", "directory-fsync", "identity"):
        case_lease = create_package6_staging_lease()
        lease_root = case_lease.root
        evidence_root = lease_root / "evidence"
        try:
            case_lease.assert_valid()
            assert lease_root.parent == Path("/tmp")
            assert not evidence_root.exists()

            case = tmp_path / boundary
            case.mkdir(mode=0o700)
            arguments, _before = _finalizer_arguments(
                case, monkeypatch, lease=case_lease
            )
            output = cast(Path, arguments["output_dir"])
            assert evidence_root.is_dir()
            case_lease.assert_valid()
            real_fsync = evidence_module.os.fsync
            real_link = evidence_module._link_owned_tmpfile
            real_identity = getattr(
                evidence_module, "_confirm_final_publication_identity", None
            )

            def install() -> None:
                if boundary == "link":
                    def link_then_kill(*args: object) -> None:
                        real_link(*args)  # type: ignore[arg-type]
                        _kill_child()

                    monkeypatch.setattr(
                        evidence_module, "_link_owned_tmpfile", link_then_kill
                    )
                elif boundary == "directory-fsync":
                    monkeypatch.setattr(
                        evidence_module.os,
                        "fsync",
                        lambda fd: _kill_child()
                        if stat.S_ISDIR(os.fstat(fd).st_mode)
                        else real_fsync(fd),
                    )
                else:
                    assert callable(real_identity)
                    monkeypatch.setattr(
                        evidence_module,
                        "_confirm_final_publication_identity",
                        lambda *a: _kill_child(),
                    )

            status = _run_finalizer_child(arguments, monkeypatch, install)
            assert os.waitstatus_to_exitcode(status) == -signal.SIGKILL
            files = list(output.iterdir())
            assert [path.name for path in files] == [_CONTROLLER_FINAL_NAME]
            raw = files[0].read_bytes()
            assert hashlib.sha256(raw).hexdigest()
            assert json.loads(raw)["container_kind"] == (
                "PACKAGE6_CONTROLLER_FINAL_PUBLICATION"
            )
            case_lease.assert_valid()
            assert lease_root.is_dir()
            assert evidence_root.is_dir()
        finally:
            case_lease.cleanup()

        assert not lease_root.exists()
        assert not evidence_root.exists()


def test_controller_result_postlink_failure_retains_recovery_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    output = cast(Path, arguments["output_dir"])
    real_fsync = evidence_module.os.fsync

    def fail_committed_directory_fsync(descriptor: int) -> None:
        if (
            stat.S_ISDIR(os.fstat(descriptor).st_mode)
            and (output / _CONTROLLER_FINAL_NAME).exists()
        ):
            raise OSError(errno.EIO, "private final directory fsync sentinel")
        real_fsync(descriptor)

    monkeypatch.setattr(
        evidence_module.os,
        "fsync",
        fail_committed_directory_fsync,
    )
    with pytest.raises(evidence_module.FinalPublicationFailure) as raised:
        evidence_module.finalize_controller_evidence(**arguments)

    authority = raised.value.authority
    assert authority.publication_committed is True
    assert authority.recovery_required is True
    assert authority.identity_confirmed is False
    assert set(authority.read_logical_entries()) == {
        "controller-final-decision.json",
        "index.json",
    }
    assert [path.name for path in output.iterdir()] == [_CONTROLLER_FINAL_NAME]
    assert authority.close() is True
    assert authority.recovery_required is True


def test_controller_result_link_success_interruption_retains_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    output = cast(Path, arguments["output_dir"])
    real_link = evidence_module._link_owned_tmpfile

    def link_then_interrupt(*args: object) -> None:
        real_link(*args)  # type: ignore[arg-type]
        raise KeyboardInterrupt("private post-link assignment sentinel")

    monkeypatch.setattr(
        evidence_module,
        "_link_owned_tmpfile",
        link_then_interrupt,
    )
    with pytest.raises(evidence_module.FinalPublicationFailure) as raised:
        evidence_module.finalize_controller_evidence(**arguments)

    authority = raised.value.authority
    assert authority.publication_committed is False
    assert authority.publication_commit_uncertain is True
    assert authority.recovery_required is True
    assert authority.recover() is True
    assert authority.publication_committed is True
    assert authority.publication_commit_uncertain is False
    assert authority.identity_confirmed is True
    assert authority.recovery_required is False
    assert [path.name for path in output.iterdir()] == [_CONTROLLER_FINAL_NAME]
    assert authority.close() is True
    assert authority.recovery_required is False


def test_controller_result_file_exists_after_link_attempt_retains_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    output = cast(Path, arguments["output_dir"])
    existing = output / _CONTROLLER_FINAL_NAME
    existing.write_bytes(b"preexisting")
    existing.chmod(0o600)

    with pytest.raises(evidence_module.FinalPublicationFailure) as raised:
        evidence_module.finalize_controller_evidence(**arguments)

    authority = raised.value.authority
    assert authority.publication_committed is False
    assert authority.publication_commit_uncertain is True
    assert authority.recovery_required is True
    assert authority.recover() is False
    assert existing.read_bytes() == b"preexisting"
    assert authority.close() is True
    assert authority.recovery_required is True


def test_controller_result_post_success_file_exists_retains_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    real_link = evidence_module._link_owned_tmpfile

    def link_then_file_exists(*args: object) -> None:
        real_link(*args)  # type: ignore[arg-type]
        raise FileExistsError("private post-success link sentinel")

    monkeypatch.setattr(
        evidence_module,
        "_link_owned_tmpfile",
        link_then_file_exists,
    )
    with pytest.raises(evidence_module.FinalPublicationFailure) as raised:
        evidence_module.finalize_controller_evidence(**arguments)

    authority = raised.value.authority
    assert authority.publication_committed is False
    assert authority.publication_commit_uncertain is True
    assert authority.recover() is True
    assert authority.publication_committed is True
    assert authority.close() is True


@pytest.mark.parametrize("recycle_kind", ("canonical-file", "output-directory"))
def test_controller_result_never_closes_recycled_retained_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
    recycle_kind: str,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    output = cast(Path, arguments["output_dir"])
    decoy_file = tmp_path / "final-publication-decoy"
    decoy_file.write_bytes(b"decoy")
    decoy_directory = tmp_path / "final-publication-decoy-directory"
    decoy_directory.mkdir(mode=0o700)
    sentinel = KeyboardInterrupt(f"private {recycle_kind} generation sentinel")
    real_fsync = evidence_module.os.fsync
    real_close = os.close
    real_open = os.open
    recycled_descriptor = -1

    def recycle_on_fsync(descriptor: int) -> None:
        nonlocal recycled_descriptor
        info = os.fstat(descriptor)
        matches = (
            recycle_kind == "canonical-file"
            and stat.S_ISREG(info.st_mode)
            and info.st_nlink == 0
        ) or (
            recycle_kind == "output-directory"
            and stat.S_ISDIR(info.st_mode)
            and (info.st_dev, info.st_ino)
            == (output.stat().st_dev, output.stat().st_ino)
        )
        if matches and recycled_descriptor < 0:
            real_close(descriptor)
            target = decoy_file if recycle_kind == "canonical-file" else decoy_directory
            flags = os.O_RDONLY | os.O_CLOEXEC
            if recycle_kind == "output-directory":
                flags |= os.O_DIRECTORY
            recycled_descriptor = real_open(target, flags)
            assert recycled_descriptor == descriptor
            raise sentinel
        real_fsync(descriptor)

    monkeypatch.setattr(evidence_module.os, "fsync", recycle_on_fsync)
    try:
        with pytest.raises(evidence_module.FinalPublicationFailure) as raised:
            evidence_module.finalize_controller_evidence(**arguments)
        authority = raised.value.authority
        assert authority.recovery_required is True
        assert authority.close() is False
        os.fstat(recycled_descriptor)
        if recycle_kind == "canonical-file":
            assert authority.publication_committed is False
            assert list(output.iterdir()) == []
        else:
            assert authority.publication_committed is True
            assert [path.name for path in output.iterdir()] == [
                _CONTROLLER_FINAL_NAME
            ]
    finally:
        if recycled_descriptor >= 0:
            real_close(recycled_descriptor)


def test_controller_result_retained_read_requires_exact_link_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "linked-controller-final"
    target.write_bytes(b"payload")
    target.chmod(0o600)
    descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        with pytest.raises(EvidenceIncomplete, match="policy"):
            evidence_module._read_final_publication(
                descriptor,
                expected_link_count=0,
            )
        raw, _metadata = evidence_module._read_final_publication(
            descriptor,
            expected_link_count=1,
        )
    finally:
        os.close(descriptor)

    assert raw == b"payload"


def test_controller_result_retained_policy_failure_sets_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    authority = evidence_module.finalize_controller_evidence(**arguments)
    assert isinstance(authority, evidence_module.FinalPublicationAuthority)
    extra_link = tmp_path / "controller-final-extra-link"
    os.link(authority.path, extra_link)
    try:
        with pytest.raises(EvidenceIncomplete, match="policy"):
            authority.read_canonical_bytes()
        assert authority.recovery_required is True
        assert authority.close() is True
        assert authority.recovery_required is True
    finally:
        extra_link.unlink(missing_ok=True)


def test_controller_result_retained_read_rejects_metadata_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    authority = evidence_module.finalize_controller_evidence(**arguments)
    assert isinstance(authority, evidence_module.FinalPublicationAuthority)
    before = authority.path.stat()
    os.utime(
        authority.path,
        ns=(before.st_atime_ns, before.st_mtime_ns),
    )
    with pytest.raises(EvidenceIncomplete, match="changed"):
        authority.read_canonical_bytes()
    assert authority.recovery_required is True
    assert authority.close() is True


def test_controller_result_input_close_failure_preserves_output_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    real_close = evidence_module._RuntimeEvidenceSnapshot.close

    def close_but_report_failure(snapshot: object) -> bool:
        assert real_close(snapshot) is True  # type: ignore[arg-type]
        return False

    monkeypatch.setattr(
        evidence_module._RuntimeEvidenceSnapshot,
        "close",
        close_but_report_failure,
    )
    with pytest.raises(evidence_module.FinalPublicationFailure) as raised:
        evidence_module.finalize_controller_evidence(**arguments)

    authority = raised.value.authority
    assert authority.publication_committed is True
    assert authority.recovery_required is True
    assert set(authority.read_logical_entries()) == {
        "controller-final-decision.json",
        "index.json",
    }
    assert authority.close() is True


def _rewrite_controller_input(
    arguments: dict[str, Any],
    *,
    path_key: str,
    digest_key: str,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    path = cast(Path, arguments[path_key])
    document = cast(
        dict[str, object],
        json.loads(path.read_text(encoding="utf-8")),
    )
    mutation(document)
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    path.chmod(0o600)
    arguments[digest_key] = hashlib.sha256(path.read_bytes()).hexdigest()


def _mutate_diagnostic_case(document: dict[str, object], case: str) -> None:
    metadata = cast(
        dict[str, dict[str, dict[str, object]]],
        document["transcript_metadata"],
    )
    if case == "missing-field":
        document.pop("verdict")
    elif case == "extra-field":
        document["entries"] = []
    elif case == "schema-version":
        document["schema_version"] = 2
    elif case == "schema-version-bool":
        document["schema_version"] = True
    elif case == "verdict":
        document["verdict"] = "NO-GO"
    elif case == "candidate-commit":
        document["candidate_commit"] = "0" * 40
    elif case == "candidate-tree":
        document["candidate_tree"] = "0" * 40
    elif case == "source-diff":
        document["source_diff_sha256"] = "0" * 64
    elif case == "runtime-attempt":
        document["runtime_attempt"] = "R4"
    elif case == "test-nodeid":
        document["test_nodeid"] = "tests/foundation/test_other.py::test_other"
    elif case == "exit-code":
        document["exit_code"] = 1
    elif case == "exit-code-bool":
        document["exit_code"] = False
    elif case == "passed":
        document["passed"] = 0
    elif case == "passed-bool":
        document["passed"] = True
    elif case == "failed":
        document["failed"] = 1
    elif case == "failed-bool":
        document["failed"] = False
    elif case == "missing-component":
        metadata.pop("job_api")
    elif case == "extra-component":
        metadata["provider"] = {}
    elif case == "missing-stream":
        metadata["worker"].pop("stderr")
    elif case == "extra-stream":
        metadata["worker"]["combined"] = {}
    elif case == "relative-path":
        metadata["worker"]["stdout"]["path"] = "private.stdout.transcript"
    elif case == "wrong-filename":
        metadata["worker"]["stdout"]["path"] = str(
            Path(cast(str, metadata["worker"]["stdout"]["path"])).with_name(
                "diagnostic.log"
            )
        )
    elif case == "sha256":
        metadata["worker"]["stdout"]["sha256"] = "A" * 64
    elif case == "size":
        metadata["worker"]["stdout"]["size"] = -1
    elif case == "size-bool":
        metadata["worker"]["stdout"]["size"] = False
    elif case == "truncated":
        metadata["worker"]["stdout"]["truncated"] = 0
    elif case == "live-execution":
        document["live_execution_approved"] = True
    elif case == "live-trading":
        document["live_trading_approved"] = True
    else:  # pragma: no cover - exhaustive parametrization
        raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    (
        "missing-field",
        "extra-field",
        "schema-version",
        "schema-version-bool",
        "verdict",
        "candidate-commit",
        "candidate-tree",
        "source-diff",
        "runtime-attempt",
        "test-nodeid",
        "exit-code",
        "exit-code-bool",
        "passed",
        "passed-bool",
        "failed",
        "failed-bool",
        "missing-component",
        "extra-component",
        "missing-stream",
        "extra-stream",
        "relative-path",
        "wrong-filename",
        "sha256",
        "size",
        "size-bool",
        "truncated",
        "live-execution",
        "live-trading",
    ),
)
def test_finalizer_rejects_each_diagnostic_authority_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
    case: str,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    _rewrite_controller_input(
        arguments,
        path_key="diagnostic_index_path",
        digest_key="diagnostic_index_sha256",
        mutation=lambda document: _mutate_diagnostic_case(document, case),
    )

    with pytest.raises(EvidenceIncomplete):
        evidence_module.finalize_controller_evidence(**arguments)
    assert list(cast(Path, arguments["output_dir"]).iterdir()) == []


def _mutate_review_case(document: dict[str, object], case: str) -> None:
    if case == "missing-field":
        document.pop("verdict")
    elif case == "extra-field":
        document["reviewer"] = "worker"
    elif case == "schema-version":
        document["schema_version"] = 2
    elif case == "schema-version-bool":
        document["schema_version"] = True
    elif case == "verdict":
        document["verdict"] = "FAIL"
    elif case == "base":
        document["reviewed_base_commit"] = "0" * 40
    elif case == "algorithm":
        document["patch_algorithm"] = "PACKAGE6_OTHER_PATCH_V1"
    elif case == "patch-sha":
        document["reviewed_patch_sha256"] = "0" * 64
    elif case == "patch-bytes":
        document["reviewed_patch_bytes"] = -1
    elif case == "patch-bytes-bool":
        document["reviewed_patch_bytes"] = True
    elif case == "paths-missing":
        cast(list[str], document["reviewed_paths"]).pop()
    elif case == "paths-extra":
        cast(list[str], document["reviewed_paths"]).append("unexpected.py")
    elif case == "paths-reordered":
        cast(list[str], document["reviewed_paths"]).reverse()
    elif case == "source-diff":
        document["source_diff_sha256"] = "0" * 64
    elif case == "findings":
        document["findings"] = ["unresolved"]
    elif case == "findings-object":
        document["findings"] = {}
    elif case == "scope":
        document["scope_integrity"] = "FAIL"
    elif case == "adequacy":
        document["test_adequacy"] = "FAIL"
    elif case == "seal-manifest":
        document["seal_manifest_sha256"] = "0" * 64
    elif case == "seal-integrity":
        document["seal_integrity"] = "FAIL"
    elif case == "production-authority":
        document["production_authority_status"] = "PRODUCTION"
    elif case == "live-execution":
        document["live_execution_approved"] = True
    elif case == "live-trading":
        document["live_trading_approved"] = True
    else:  # pragma: no cover - exhaustive parametrization
        raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    (
        "missing-field",
        "extra-field",
        "schema-version",
        "schema-version-bool",
        "verdict",
        "base",
        "algorithm",
        "patch-sha",
        "patch-bytes",
        "patch-bytes-bool",
        "paths-missing",
        "paths-extra",
        "paths-reordered",
        "source-diff",
        "findings",
        "findings-object",
        "scope",
        "adequacy",
        "seal-manifest",
        "seal-integrity",
        "production-authority",
        "live-execution",
        "live-trading",
    ),
)
def test_finalizer_rejects_each_independent_review_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
    case: str,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    _rewrite_controller_input(
        arguments,
        path_key="review_path",
        digest_key="review_verdict_sha256",
        mutation=lambda document: _mutate_review_case(document, case),
    )

    with pytest.raises(EvidenceIncomplete):
        evidence_module.finalize_controller_evidence(**arguments)
    assert list(cast(Path, arguments["output_dir"]).iterdir()) == []


def test_finalizer_rejects_source_diff_distinct_from_reviewed_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    different_source_diff = "a" * 64
    arguments["source_diff_sha256"] = different_source_diff
    for path_key, digest_key in (
        ("review_path", "review_verdict_sha256"),
        ("diagnostic_index_path", "diagnostic_index_sha256"),
        ("cleanup_path", "cleanup_evidence_sha256"),
    ):
        _rewrite_controller_input(
            arguments,
            path_key=path_key,
            digest_key=digest_key,
            mutation=lambda document: document.update(
                source_diff_sha256=different_source_diff
            ),
        )

    with pytest.raises(EvidenceIncomplete, match="review|digest|identity"):
        evidence_module.finalize_controller_evidence(**arguments)

    assert list(cast(Path, arguments["output_dir"]).iterdir()) == []


def _mutate_cleanup_case(document: dict[str, object], case: str) -> None:
    if case == "missing-field":
        document.pop("process_refs")
    elif case == "extra-field":
        document["cleanup_complete"] = True
    elif case == "schema-version":
        document["schema_version"] = 2
    elif case == "schema-version-bool":
        document["schema_version"] = True
    elif case == "candidate-commit":
        document["candidate_commit"] = "0" * 40
    elif case == "candidate-tree":
        document["candidate_tree"] = "0" * 40
    elif case == "source-diff":
        document["source_diff_sha256"] = "0" * 64
    elif case == "process-refs":
        document["process_refs"] = 1
    elif case == "process-refs-bool":
        document["process_refs"] = False
    elif case == "surviving-process":
        document["surviving_processes"] = [123]
    elif case == "surviving-port":
        document["surviving_listener_ports"] = [12345]
    elif case == "candidate-root-exists":
        document["candidate_root_exists"] = True
    elif case == "postgres-root-exists":
        document["postgres_root_exists"] = True
    elif case == "evidence-not-preserved":
        document["evidence_preserved_outside_disposable_root"] = False
    elif case == "live-execution":
        document["live_execution_approved"] = True
    elif case == "live-trading":
        document["live_trading_approved"] = True
    else:  # pragma: no cover - exhaustive parametrization
        raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    (
        "missing-field",
        "extra-field",
        "schema-version",
        "schema-version-bool",
        "candidate-commit",
        "candidate-tree",
        "source-diff",
        "process-refs",
        "process-refs-bool",
        "surviving-process",
        "surviving-port",
        "candidate-root-exists",
        "postgres-root-exists",
        "evidence-not-preserved",
        "live-execution",
        "live-trading",
    ),
)
def test_finalizer_rejects_each_cleanup_authority_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
    case: str,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    _rewrite_controller_input(
        arguments,
        path_key="cleanup_path",
        digest_key="cleanup_evidence_sha256",
        mutation=lambda document: _mutate_cleanup_case(document, case),
    )

    with pytest.raises(EvidenceIncomplete):
        evidence_module.finalize_controller_evidence(**arguments)
    assert list(cast(Path, arguments["output_dir"]).iterdir()) == []


@pytest.mark.parametrize(
    "case",
    (
        "candidate-commit",
        "candidate-tree",
        "reviewed-base",
        "patch-sha",
        "patch-bytes",
        "reviewed-paths",
        "source-diff",
        "seal-manifest",
        "review-digest",
        "diagnostic-digest",
        "bundle-digest",
        "cleanup-digest",
        "custodian-helper",
        "custodian-source-set",
        "custodian-protocol",
        "custodian-endpoint",
        "custodian-operations",
        "custodian-stage",
        "custodian-fixture",
        "custodian-publications",
    ),
)
def test_finalizer_rejects_each_top_level_binding_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
    case: str,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    if case == "candidate-commit":
        arguments["candidate_commit"] = "0" * 40
    elif case == "candidate-tree":
        arguments["candidate_tree"] = "0" * 40
    elif case == "reviewed-base":
        arguments["reviewed_base_commit"] = "0" * 40
    elif case == "patch-sha":
        arguments["reviewed_patch_sha256"] = "0" * 64
    elif case == "patch-bytes":
        arguments["reviewed_patch_bytes"] = 12346
    elif case == "reviewed-paths":
        arguments["reviewed_paths"] = cast(list[str], arguments["reviewed_paths"])[1:]
    elif case == "source-diff":
        arguments["source_diff_sha256"] = "0" * 64
    elif case == "seal-manifest":
        arguments["expected_seal_manifest_sha256"] = "0" * 64
    elif case == "review-digest":
        arguments["review_verdict_sha256"] = "0" * 64
    elif case == "diagnostic-digest":
        arguments["diagnostic_index_sha256"] = "0" * 64
    elif case == "bundle-digest":
        arguments["runtime_bundle_index_sha256"] = "0" * 64
    elif case == "cleanup-digest":
        arguments["cleanup_evidence_sha256"] = "0" * 64
    elif case == "custodian-helper":
        arguments["custodian_helper_binary_sha256"] = "0" * 64
    elif case == "custodian-source-set":
        arguments["custodian_native_source_set_sha256"] = "0" * 64
    elif case == "custodian-protocol":
        arguments["custodian_protocol_version"] = 2
    elif case == "custodian-endpoint":
        arguments["custodian_endpoint_authority"] = "FILESYSTEM_SOCKET"
    elif case == "custodian-operations":
        arguments["custodian_operations"] = cast(
            list[str], arguments["custodian_operations"]
        )[:-1]
    elif case == "custodian-stage":
        arguments["custodian_stage_sha256"] = "0" * 64
    elif case == "custodian-fixture":
        arguments["custodian_fixture_sha256"] = "0" * 64
    elif case == "custodian-publications":
        arguments["custodian_publications"] = cast(
            list[str], arguments["custodian_publications"]
        )[:-1]
    else:  # pragma: no cover - exhaustive parametrization
        raise AssertionError(case)

    with pytest.raises(EvidenceIncomplete):
        evidence_module.finalize_controller_evidence(**arguments)
    assert list(cast(Path, arguments["output_dir"]).iterdir()) == []


def test_finalizer_writes_single_private_container_without_mutating_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )

    result = evidence_module.finalize_controller_evidence(**arguments)
    output = cast(Path, arguments["output_dir"])
    assert isinstance(result, evidence_module.FinalPublicationAuthority)
    final_path = output / _CONTROLLER_FINAL_NAME
    assert result.path == final_path
    assert result.size == result.canonical_byte_size
    assert stat.S_IMODE(final_path.stat().st_mode) == 0o600
    assert not (output / "controller-final-decision.json").exists()
    assert not (output / "index.json").exists()
    entries = result.read_logical_entries()
    record = json.loads(entries["controller-final-decision.json"])
    assert record["verdict"] == "GO - PAPER FOUNDATION RUNTIME VERIFIED"
    assert record["patch_algorithm"] == "PACKAGE6_GOAL2_PATCH_V1"
    assert tuple(record["reviewed_paths"]) == EXPECTED_REVIEWED_PATHS
    assert record["seal_manifest_sha256"] == "9" * 64
    assert record["seal_integrity"] == "PASS"
    assert record["production_authority_status"] == "TEST_ONLY"
    assert record["live_execution_approved"] is False
    assert record["live_trading_approved"] is False
    runtime_bundle = cast(Path, arguments["runtime_bundle"])
    after = hashlib.sha256(runtime_bundle.read_bytes()).hexdigest()

    assert after == before
    assert result.revalidate_identity() is True
    assert result.close() is True


def test_finalizer_rejects_runtime_mutation_after_descriptor_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    runtime_bundle = cast(Path, arguments["runtime_bundle"])
    real_open = evidence_module._open_runtime_evidence_snapshot
    mutated = False

    def mutate_after_snapshot(root: object) -> object:
        nonlocal mutated
        snapshot = real_open(root)  # type: ignore[arg-type]
        if not mutated:
            mutated = True
            mutable_snapshot = dict(snapshot)
            runtime = json.loads(mutable_snapshot["runtime.json"])
            runtime["verdict"] = "FORGED AFTER VERIFICATION"
            mutable_snapshot["runtime.json"] = json.dumps(
                    runtime,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            runtime_bundle.write_bytes(
                evidence_module._encode_runtime_evidence_container(
                    mutable_snapshot
                )
            )
            runtime_bundle.chmod(0o600)
        return snapshot

    monkeypatch.setattr(
        evidence_module,
        "_open_runtime_evidence_snapshot",
        mutate_after_snapshot,
    )

    with pytest.raises(EvidenceIncomplete):
        evidence_module.finalize_controller_evidence(**arguments)

    assert mutated is True
    assert list(cast(Path, arguments["output_dir"]).iterdir()) == []


def _replace_runtime_container_verdict(bundle: Path, verdict: str) -> None:
    snapshot = evidence_module._load_runtime_evidence_snapshot(bundle)
    mutable_snapshot = dict(snapshot)
    runtime = json.loads(mutable_snapshot["runtime.json"])
    runtime["verdict"] = verdict
    mutable_snapshot["runtime.json"] = json.dumps(
        runtime,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bundle.write_bytes(
        evidence_module._encode_runtime_evidence_container(mutable_snapshot)
    )
    bundle.chmod(0o600)


def test_finalizer_rejects_runtime_mutation_after_second_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    runtime_bundle = cast(Path, arguments["runtime_bundle"])
    real_revalidate = evidence_module._RuntimeEvidenceSnapshot.revalidate
    revalidations = 0

    def mutate_after_second_snapshot(
        snapshot: evidence_module._RuntimeEvidenceSnapshot,
    ) -> None:
        nonlocal revalidations
        revalidations += 1
        real_revalidate(snapshot)
        if revalidations == 1:
            mutable_snapshot = dict(snapshot)
            runtime = json.loads(mutable_snapshot["runtime.json"])
            runtime["verdict"] = "FORGED AFTER SECOND SNAPSHOT"
            mutable_snapshot["runtime.json"] = json.dumps(
                runtime,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            runtime_bundle.write_bytes(
                evidence_module._encode_runtime_evidence_container(
                    mutable_snapshot
                )
            )
            runtime_bundle.chmod(0o600)

    monkeypatch.setattr(
        evidence_module._RuntimeEvidenceSnapshot,
        "revalidate",
        mutate_after_second_snapshot,
    )

    with pytest.raises(EvidenceIncomplete):
        evidence_module.finalize_controller_evidence(**arguments)

    assert revalidations == 2
    assert list(cast(Path, arguments["output_dir"]).iterdir()) == []


def test_finalizer_rejects_runtime_mutation_during_final_output_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package6_staging_lease: Package6StagingLease,
) -> None:
    arguments, _before = _finalizer_arguments(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    runtime_bundle = cast(Path, arguments["runtime_bundle"])
    output = cast(Path, arguments["output_dir"])
    real_fsync = evidence_module.os.fsync
    mutated = False

    def mutate_during_output_fsync(descriptor: int) -> None:
        nonlocal mutated
        real_fsync(descriptor)
        try:
            descriptor_path = os.readlink(f"/proc/self/fd/{descriptor}")
            info = os.fstat(descriptor)
        except OSError:
            return
        if (
            not mutated
            and stat.S_ISREG(info.st_mode)
            and descriptor_path.startswith(f"{output}/")
        ):
            mutated = True
            _replace_runtime_container_verdict(
                runtime_bundle,
                "FORGED DURING FINAL PUBLICATION",
            )

    monkeypatch.setattr(evidence_module.os, "fsync", mutate_during_output_fsync)

    with pytest.raises(EvidenceIncomplete):
        evidence_module.finalize_controller_evidence(**arguments)

    assert mutated is True
    assert list(output.iterdir()) == []


def _open_descriptor_set() -> set[int]:
    candidates = [int(item.name) for item in Path("/proc/self/fd").iterdir()]
    observed: set[int] = set()
    for descriptor in candidates:
        try:
            os.fstat(descriptor)
        except OSError:
            continue
        observed.add(descriptor)
    return observed


def _new_open_descriptors(before: set[int]) -> list[int]:
    candidates = [int(item.name) for item in Path("/proc/self/fd").iterdir()]
    observed: list[int] = []
    for descriptor in candidates:
        if descriptor in before:
            continue
        try:
            os.fstat(descriptor)
        except OSError:
            continue
        observed.append(descriptor)
    return observed


def _raise_at_opcode(
    target: Callable[..., object],
    offset: int,
    sentinel: BaseException,
) -> Callable[[Any, str, Any], Any]:
    def inject(frame: Any, event: str, _argument: Any) -> Any:
        if event == "call" and frame.f_code is target.__code__:
            frame.f_trace_opcodes = True
            return inject
        if (
            event == "opcode"
            and frame.f_code is target.__code__
            and frame.f_lasti == offset
        ):
            sys.settrace(None)
            raise sentinel
        return inject

    return inject


def _native_fd_custody_test_api() -> Any:
    native = getattr(evidence_module, "_NATIVE_FD_CUSTODY", None)
    assert native is not None, "native descriptor custody extension is required"
    assert not hasattr(evidence_module, "_LIBC_OPEN")
    assert not hasattr(evidence_module, "_LIBC_OPENAT")
    return native


def test_native_fd_custody_loader_uses_retained_descriptor() -> None:
    loaded = evidence_module._NATIVE_FD_CUSTODY
    assert loaded is not None
    specification = loaded.__spec__
    assert specification is not None
    loader = specification.loader
    assert isinstance(loader, importlib.machinery.ExtensionFileLoader)
    assert loader.path.startswith("/proc/self/fd/")


def test_native_fd_custody_rejects_malformed_expected_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
        "not-a-sha256",
    )

    with pytest.raises(RuntimeError, match="expected.*SHA-256"):
        evidence_module._load_native_fd_custody()


def test_native_fd_custody_rejects_digest_mismatch_before_module_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = tmp_path / "digest-loader"
    private.mkdir(mode=0o700)
    extension = private / "candidate-extension.so"
    extension.write_bytes(b"candidate extension bytes")
    extension.chmod(0o600)
    mismatched_digest = "0" * 64
    assert hashlib.sha256(extension.read_bytes()).hexdigest() != mismatched_digest
    monkeypatch.setenv(
        "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
        mismatched_digest,
    )
    monkeypatch.delitem(sys.modules, "_package6_fd_custody", raising=False)
    monkeypatch.setattr(
        evidence_module,
        "_native_fd_custody_extension_path",
        lambda: extension,
    )
    monkeypatch.setattr(
        evidence_module,
        "_open_native_fd_custody_artifact",
        lambda _extension: (
            os.open(extension, os.O_RDONLY | os.O_CLOEXEC),
            (),
        ),
    )
    module_construction_reached = False

    def reject_module_construction(_specification: object) -> types.ModuleType:
        nonlocal module_construction_reached
        module_construction_reached = True
        raise AssertionError("native module construction was reached")

    monkeypatch.setattr(
        evidence_module.importlib.util,
        "module_from_spec",
        reject_module_construction,
    )

    with pytest.raises(RuntimeError, match="digest"):
        evidence_module._load_native_fd_custody()
    assert module_construction_reached is False


def test_native_fd_custody_revalidates_policy_after_artifact_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = tmp_path / "private-native-loader"
    private.mkdir(mode=0o700)
    artifact = private / "candidate-extension.so"
    artifact.write_bytes(b"original")
    artifact.chmod(0o600)
    replacement = private / "replacement-extension.so"
    replacement.write_bytes(b"replacement")
    replacement.chmod(0o644)
    real_open = evidence_module.os.open
    replaced = False
    before = _open_descriptor_set()

    def replace_before_artifact_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        if path == artifact.name and dir_fd is not None and not replaced:
            replaced = True
            os.replace(replacement, artifact)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(evidence_module.os, "open", replace_before_artifact_open)
    with pytest.raises(RuntimeError, match="artifact policy"):
        evidence_module._open_native_fd_custody_artifact(artifact)

    assert replaced is True
    assert _new_open_descriptors(before) == []


def test_native_fd_custody_rejects_writable_artifact_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = evidence_module._native_fd_custody_extension_path()
    assert source is not None
    unsafe = tmp_path / "writable-parent"
    unsafe.mkdir(mode=0o700)
    candidate = unsafe / source.name
    candidate.write_bytes(source.read_bytes())
    candidate.chmod(0o600)
    unsafe.chmod(0o777)
    monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(candidate))

    with pytest.raises(RuntimeError, match="parent"):
        evidence_module._native_fd_custody_extension_path()


def test_native_fd_custody_rejects_preloaded_python_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = types.ModuleType("_package6_fd_custody")
    extension = evidence_module._native_fd_custody_extension_path()
    assert extension is not None
    specification = importlib.util.spec_from_file_location(
        "_package6_fd_custody",
        extension,
    )
    assert specification is not None
    fake.__spec__ = specification
    fake.__file__ = str(extension)
    setattr(fake, "OWNERSHIP_MODEL", "NATIVE_OBJECT_V1")
    setattr(fake, "FdOwner", type("FdOwner", (), {}))
    setattr(fake, "open", lambda *_args: 7)
    setattr(fake, "openat", lambda *_args: 7)
    monkeypatch.setitem(sys.modules, "_package6_fd_custody", fake)

    with pytest.raises(RuntimeError, match="native descriptor custody"):
        evidence_module._load_native_fd_custody()


def _native_open_exception_at_first_result_opcode(
    root: Path,
    sentinel: BaseException,
) -> None:
    target = evidence_module._native_open
    instructions = tuple(dis.get_instructions(target))
    injection_offset = next(
        instruction.offset
        for index, instruction in enumerate(instructions)
        if instruction.opname == "STORE_FAST"
        and instruction.argval == "owner"
        and instructions[index - 1].opname == "CALL"
    )
    try:
        sys.settrace(_raise_at_opcode(target, injection_offset, sentinel))
        with pytest.raises(type(sentinel)) as raised:
            evidence_module._native_open(
                root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
    finally:
        sys.settrace(None)
    assert raised.value is sentinel


def test_native_open_converter_entry_exception_cannot_leak_fd(
    tmp_path: Path,
) -> None:
    native = _native_fd_custody_test_api()
    root = tmp_path / "native-entry-fault"
    root.mkdir(mode=0o700)
    native._test_reset()
    native._test_fail_after_open_once()
    before = _open_descriptor_set()

    with pytest.raises(RuntimeError, match="injected native ownership fault"):
        evidence_module._native_open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )

    assert _new_open_descriptors(before) == []
    assert native._test_close_call_count() == 1


def test_native_open_first_opcode_exception_cannot_leak_fd(
    tmp_path: Path,
) -> None:
    native = _native_fd_custody_test_api()
    root = tmp_path / "native-first-opcode"
    root.mkdir(mode=0o700)
    native._test_reset()
    before = _open_descriptor_set()

    _native_open_exception_at_first_result_opcode(
        root,
        RuntimeError("private first-opcode sentinel"),
    )

    assert _new_open_descriptors(before) == []
    assert native._test_close_call_count() == 1


def test_native_open_keyboard_interrupt_cannot_leak_fd(
    tmp_path: Path,
) -> None:
    native = _native_fd_custody_test_api()
    root = tmp_path / "native-keyboard-interrupt"
    root.mkdir(mode=0o700)
    native._test_reset()
    before = _open_descriptor_set()

    _native_open_exception_at_first_result_opcode(
        root,
        KeyboardInterrupt("private native-open keyboard sentinel"),
    )

    assert _new_open_descriptors(before) == []
    assert native._test_close_call_count() == 1


def test_native_open_system_exit_cannot_leak_fd(
    tmp_path: Path,
) -> None:
    native = _native_fd_custody_test_api()
    root = tmp_path / "native-system-exit"
    root.mkdir(mode=0o700)
    native._test_reset()
    before = _open_descriptor_set()

    _native_open_exception_at_first_result_opcode(
        root,
        SystemExit("private native-open system-exit sentinel"),
    )

    assert _new_open_descriptors(before) == []
    assert native._test_close_call_count() == 1


def test_native_open_owner_destructor_closes_exactly_once(
    tmp_path: Path,
) -> None:
    native = _native_fd_custody_test_api()
    root = tmp_path / "native-owner-destructor"
    root.mkdir(mode=0o700)
    native._test_reset()
    authority = evidence_module._native_open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    descriptor = authority.descriptor
    assert type(authority.owner).__module__ == "_package6_fd_custody"
    assert type(authority.owner).__name__ == "FdOwner"
    assert not isinstance(authority.owner, int)
    assert fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC

    del authority
    gc.collect()
    gc.collect()

    with pytest.raises(OSError) as closed:
        os.fstat(descriptor)
    assert closed.value.errno == errno.EBADF
    assert native._test_close_call_count() == 1


def test_native_open_close_error_never_retries_reused_number(
    tmp_path: Path,
) -> None:
    native = _native_fd_custody_test_api()
    root = tmp_path / "native-close-reuse"
    root.mkdir(mode=0o700)
    decoy = tmp_path / "native-close-reuse-decoy"
    decoy.write_bytes(b"decoy")
    native._test_reset()
    authority = evidence_module._native_open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    descriptor = authority.descriptor
    replacement = os.open(decoy, os.O_RDONLY | os.O_CLOEXEC)
    assert replacement != descriptor
    try:
        native._test_fail_close_after_reuse_once(replacement)
        assert authority.prove_closed() is False
        assert native._test_close_call_count() == 1
        assert authority.prove_closed() is True
        assert native._test_close_call_count() == 1
        assert os.fstat(descriptor).st_ino == os.fstat(replacement).st_ino
        del authority
        gc.collect()
        assert native._test_close_call_count() == 1
        assert os.fstat(descriptor).st_ino == os.fstat(replacement).st_ino
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        os.close(replacement)


def test_native_open_interruption_never_adopts_preexisting_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "native-preexisting-interruption"
    root.mkdir(mode=0o700)
    preexisting = root / "record.json"
    preexisting.write_bytes(b"preexisting")
    preexisting.chmod(0o600)
    sentinel = KeyboardInterrupt("private native preexisting sentinel")

    def interrupt_before_native_call(*_args: object) -> object:
        raise sentinel

    native = _native_fd_custody_test_api()
    monkeypatch.setattr(native, "openat", interrupt_before_native_call)

    with pytest.raises(KeyboardInterrupt) as raised:
        evidence_module._write_files(root, {"record.json": b"new"})

    assert raised.value is sentinel
    assert preexisting.read_bytes() == b"preexisting"


def test_native_open_return_interruption_never_leaves_created_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "native-return-substitution"
    root.mkdir(mode=0o700)
    target = root / "record.json"
    sentinel = SystemExit("private native return interruption sentinel")
    native = _native_fd_custody_test_api()
    real_openat = native.openat

    def interrupt_after_native_return(
        directory: int,
        path: bytes,
        flags: int,
        mode: int,
    ) -> object:
        owner = real_openat(directory, path, flags, mode)
        assert owner.close() is True
        raise sentinel

    monkeypatch.setattr(
        native,
        "openat",
        interrupt_after_native_return,
    )

    with pytest.raises(SystemExit) as raised:
        evidence_module._write_files(root, {"record.json": b"new"})

    assert raised.value is sentinel
    assert not target.exists()


@pytest.mark.parametrize(
    "exception_type",
    (KeyboardInterrupt, SystemExit),
    ids=("keyboard-interrupt", "system-exit"),
)
def test_evidence_native_open_return_boundary_never_leaks_descriptor(
    tmp_path: Path,
    exception_type: type[BaseException],
) -> None:
    root = tmp_path / "native-return-boundary"
    root.mkdir(mode=0o700)
    target = evidence_module._write_files
    instructions = tuple(dis.get_instructions(target))
    injection_offset = next(
        instruction.offset
        for index, instruction in enumerate(instructions)
        if instruction.opname == "STORE_FAST"
        and instruction.argval == "descriptor"
        and instructions[index - 1].opname == "CALL"
    )
    sentinel = exception_type("private evidence native-return sentinel")
    before = _open_descriptor_set()
    leaked: list[int] = []
    try:
        sys.settrace(_raise_at_opcode(target, injection_offset, sentinel))
        with pytest.raises(exception_type) as raised:
            evidence_module._write_files(root, {"record.json": b"payload"})
        leaked = _new_open_descriptors(before)
        remaining = list(root.iterdir())
    finally:
        sys.settrace(None)
        for descriptor in leaked:
            try:
                os.close(descriptor)
            except OSError:
                pass
        (root / "record.json").unlink(missing_ok=True)

    assert raised.value is sentinel
    assert leaked == []
    assert remaining == []


@pytest.mark.parametrize(
    "exception_type",
    (KeyboardInterrupt, SystemExit),
    ids=("keyboard-interrupt", "system-exit"),
)
def test_native_open_binds_custody_before_its_call_to_store_boundary(
    tmp_path: Path,
    exception_type: type[BaseException],
) -> None:
    root = tmp_path / "native-open-call-to-store"
    root.mkdir(mode=0o700)
    target = evidence_module._native_open
    instructions = tuple(dis.get_instructions(target))
    injection_offset = next(
        instruction.offset
        for index, instruction in enumerate(instructions)
        if instruction.opname == "STORE_FAST"
        and instruction.argval == "owner"
        and instructions[index - 1].opname == "CALL"
    )
    sentinel = exception_type("private native-open call-to-store sentinel")
    before = _open_descriptor_set()
    leaked: list[int] = []
    try:
        sys.settrace(_raise_at_opcode(target, injection_offset, sentinel))
        with pytest.raises(exception_type) as raised:
            evidence_module._native_open(
                root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
        leaked = _new_open_descriptors(before)
    finally:
        sys.settrace(None)
        for descriptor in leaked:
            try:
                os.close(descriptor)
            except OSError:
                pass

    assert raised.value is sentinel
    assert leaked == []


@pytest.mark.parametrize(
    "exception_type",
    (KeyboardInterrupt, SystemExit),
    ids=("keyboard-interrupt", "system-exit"),
)
def test_evidence_post_create_boundary_rolls_back_exact_file(
    tmp_path: Path,
    exception_type: type[BaseException],
) -> None:
    root = tmp_path / "post-create-boundary"
    root.mkdir(mode=0o700)
    target = evidence_module._write_files
    instructions = tuple(dis.get_instructions(target))
    created_info_store = next(
        index
        for index, instruction in enumerate(instructions)
        if instruction.opname == "STORE_FAST"
        and instruction.argval == "created_info"
    )
    sentinel = exception_type("private evidence post-create sentinel")
    before = _open_descriptor_set()
    leaked: list[int] = []
    try:
        sys.settrace(
            _raise_at_opcode(
                target,
                instructions[created_info_store + 1].offset,
                sentinel,
            )
        )
        with pytest.raises(exception_type) as raised:
            evidence_module._write_files(root, {"record.json": b"payload"})
        leaked = _new_open_descriptors(before)
        remaining = list(root.iterdir())
    finally:
        sys.settrace(None)
        for descriptor in leaked:
            try:
                os.close(descriptor)
            except OSError:
                pass
        (root / "record.json").unlink(missing_ok=True)

    assert raised.value is sentinel
    assert leaked == []
    assert remaining == []


@pytest.mark.parametrize(
    "exception_type",
    (KeyboardInterrupt, SystemExit),
    ids=("keyboard-interrupt", "system-exit"),
)
def test_evidence_post_write_baseexception_rolls_back_all_created_files(
    tmp_path: Path,
    exception_type: type[BaseException],
) -> None:
    root = tmp_path / "post-write-boundary"
    root.mkdir(mode=0o700)
    attacker = root / "attacker-retained"
    attacker.write_bytes(b"attacker")
    attacker.chmod(0o600)
    sentinel = exception_type("private evidence post-write sentinel")

    def fail_after_writes() -> None:
        raise sentinel

    with pytest.raises(exception_type) as raised:
        evidence_module._write_files(
            root,
            {"record.json": b"complete"},
            post_write_check=fail_after_writes,
        )

    assert raised.value is sentinel
    assert {path.name: path.read_bytes() for path in root.iterdir()} == {
        "attacker-retained": b"attacker"
    }


def test_precommit_failure_never_enters_pathname_rollback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "precommit-no-rollback"
    root.mkdir(mode=0o700)
    target = root / "record.json"
    sentinel = KeyboardInterrupt("private precommit sentinel")
    assert not hasattr(evidence_module, "_rename_noreplace")

    def fail_after_write() -> None:
        raise sentinel

    with pytest.raises(KeyboardInterrupt) as raised:
        evidence_module._write_files(
            root,
            {"record.json": b"owned"},
            post_write_check=fail_after_write,
        )

    assert raised.value is sentinel
    assert not target.exists()
    assert list(root.iterdir()) == []


def test_postlink_fsync_failure_preserves_one_complete_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "postlink-complete"
    root.mkdir(mode=0o700)
    target = root / "record.json"
    sentinel = KeyboardInterrupt("private postlink fsync sentinel")
    real_fsync = evidence_module.os.fsync

    def fail_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise sentinel
        real_fsync(descriptor)

    monkeypatch.setattr(evidence_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(KeyboardInterrupt) as raised:
        evidence_module._write_files(root, {"record.json": b"owned"})

    assert raised.value is sentinel
    assert {path.name: path.read_bytes() for path in root.iterdir()} == {
        "record.json": b"owned"
    }
    assert target.stat().st_nlink == 1


def test_evidence_publication_never_closes_recycled_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "recycled-publication"
    root.mkdir(mode=0o700)
    decoy = tmp_path / "descriptor-decoy"
    decoy.write_bytes(b"decoy")
    sentinel = KeyboardInterrupt("private recycled descriptor sentinel")
    real_fsync = evidence_module.os.fsync
    real_close = os.close
    real_open = os.open
    recycled_descriptor = -1

    def recycle_file_descriptor(descriptor: int) -> None:
        nonlocal recycled_descriptor
        info = os.fstat(descriptor)
        if stat.S_ISREG(info.st_mode) and recycled_descriptor < 0:
            real_close(descriptor)
            recycled_descriptor = real_open(decoy, os.O_RDONLY | os.O_CLOEXEC)
            assert recycled_descriptor == descriptor
            raise sentinel
        real_fsync(descriptor)

    monkeypatch.setattr(evidence_module.os, "fsync", recycle_file_descriptor)
    try:
        with pytest.raises(KeyboardInterrupt) as raised:
            evidence_module._write_files(root, {"record.json": b"payload"})
        assert raised.value is sentinel
        os.fstat(recycled_descriptor)
        assert list(root.iterdir()) == []
    finally:
        if recycled_descriptor >= 0:
            try:
                real_close(recycled_descriptor)
            except OSError:
                pass
        (root / "record.json").unlink(missing_ok=True)


def test_evidence_publication_rejects_multiple_commit_names(
    tmp_path: Path,
) -> None:
    root = tmp_path / "multi-file-rejected"
    root.mkdir(mode=0o700)

    with pytest.raises(EvidenceIncomplete, match="multi-file"):
        evidence_module._write_files(
            root,
            {"record.json": b"payload", "index.json": b"index"},
        )

    assert list(root.iterdir()) == []


def test_owned_descriptor_close_is_one_shot_across_number_reuse(
    tmp_path: Path,
) -> None:
    native = _native_fd_custody_test_api()
    target = tmp_path / "one-shot"
    target.write_bytes(b"payload")
    target.chmod(0o600)
    authority = evidence_module._native_open(
        target,
        os.O_RDONLY | os.O_NOFOLLOW,
    )
    descriptor = authority.descriptor
    replacement = os.open(target, os.O_RDONLY | os.O_CLOEXEC)
    assert replacement != descriptor
    native._test_reset()
    try:
        native._test_fail_close_after_reuse_once(replacement)
        assert authority.prove_closed() is False
        assert authority.prove_closed() is True
        assert native._test_close_call_count() == 1
        os.fstat(descriptor)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        os.close(replacement)


def _runtime_container_files(prefix: str = "payload") -> dict[str, bytes]:
    return {
        name: f"{prefix}:{name}".encode()
        for name in evidence_module._runtime_evidence_required_names()
    }


@pytest.mark.parametrize(
    "mutation",
    ("mode", "link-count", "ctime", "ownership-touch"),
)
def test_runtime_container_read_rejects_complete_metadata_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    target = tmp_path / "runtime-container.json"
    target.write_bytes(b"runtime-container")
    target.chmod(0o600)
    extra_link = tmp_path / "runtime-container.link"
    descriptor = os.open(target, os.O_RDONLY | os.O_CLOEXEC)
    real_read = evidence_module.os.read
    mutated = False

    def mutate_after_read(fd: int, maximum: int) -> bytes:
        nonlocal mutated
        raw = real_read(fd, maximum)
        if not mutated:
            mutated = True
            before = os.fstat(fd)
            if mutation == "mode":
                os.fchmod(fd, 0o400)
            elif mutation == "link-count":
                os.link(target, extra_link)
            elif mutation == "ctime":
                os.utime(
                    target,
                    ns=(before.st_atime_ns, before.st_mtime_ns),
                )
            elif mutation == "ownership-touch":
                os.fchown(fd, -1, before.st_gid)
            else:  # pragma: no cover - exhaustive parametrization
                raise AssertionError(mutation)
        return raw

    monkeypatch.setattr(evidence_module.os, "read", mutate_after_read)
    try:
        with pytest.raises(EvidenceIncomplete, match="changed"):
            evidence_module._read_owned_evidence_container(descriptor)
    finally:
        os.close(descriptor)
        target.chmod(0o600)
        extra_link.unlink(missing_ok=True)

    assert mutated is True


def test_evidence_container_verify_failure_rolls_back_exact_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "container-root"
    root.mkdir(mode=0o700)
    bundle = root / "package6-test-container.json"
    sentinel = KeyboardInterrupt("private verify sentinel")

    def fail(_snapshot: object) -> bool:
        raise sentinel

    with pytest.raises(KeyboardInterrupt) as raised:
        evidence_module._publish_evidence_container(
            root,
            bundle,
            _runtime_container_files(),
            fail,
        )

    assert raised.value is sentinel
    assert list(root.iterdir()) == []


def test_runtime_evidence_publication_has_no_generation_directory_primitive() -> None:
    assert not hasattr(evidence_module, "_publish_evidence_generation")
    assert not hasattr(evidence_module, "_EvidenceGenerationRecovery")


def test_evidence_container_rejects_mutation_after_verified_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "container-verified-snapshot-root"
    root.mkdir(mode=0o700)
    bundle = root / "package6-test-container.json"

    def verify_then_mutate(snapshot: object) -> bool:
        assert isinstance(snapshot, dict) is False
        bundle.write_bytes(b"forged-after-verification")
        bundle.chmod(0o600)
        return True

    with pytest.raises(FileExistsError):
        evidence_module._publish_evidence_container(
            root,
            bundle,
            _runtime_container_files("verified"),
            verify_then_mutate,
        )

    assert {path.name: path.read_bytes() for path in root.iterdir()} == {
        bundle.name: b"forged-after-verification"
    }

def test_evidence_container_preserves_preexisting_regular_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "container-preexisting-root"
    root.mkdir(mode=0o700)
    bundle = root / "package6-test-container.json"
    bundle.write_bytes(b"preexisting")
    bundle.chmod(0o600)

    with pytest.raises(FileExistsError):
        evidence_module._publish_evidence_container(
            root,
            bundle,
            _runtime_container_files("new"),
            lambda _snapshot: True,
        )

    assert bundle.read_bytes() == b"preexisting"


def test_evidence_container_snapshot_is_immutable_and_byte_bound(
    tmp_path: Path,
) -> None:
    root = tmp_path / "container-snapshot-root"
    root.mkdir(mode=0o700)
    bundle = root / "package6-test-container.json"
    original = _runtime_container_files("original")
    bundle.write_bytes(
        evidence_module._encode_runtime_evidence_container(original)
    )
    bundle.chmod(0o600)

    snapshot = evidence_module._load_runtime_evidence_snapshot(bundle)
    mutated = _runtime_container_files("mutated")
    bundle.write_bytes(
        evidence_module._encode_runtime_evidence_container(mutated)
    )
    bundle.chmod(0o600)

    assert dict(snapshot) == original
    with pytest.raises(TypeError):
        snapshot["runtime.json"] = b"forged"  # type: ignore[index]


def test_evidence_container_rejects_noncanonical_outer_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "container-noncanonical-root"
    root.mkdir(mode=0o700)
    bundle = root / "package6-test-container.json"
    canonical = evidence_module._encode_runtime_evidence_container(
        _runtime_container_files()
    )
    document = json.loads(canonical)
    bundle.write_text(json.dumps(document, indent=2), encoding="utf-8")
    bundle.chmod(0o600)

    with pytest.raises(EvidenceIncomplete, match="schema"):
        evidence_module._load_runtime_evidence_snapshot(bundle)


def test_evidence_container_rejects_noncanonical_entry_order() -> None:
    document = json.loads(
        evidence_module._encode_runtime_evidence_container(
            _runtime_container_files()
        )
    )
    document["entries"] = list(reversed(document["entries"]))

    with pytest.raises(EvidenceIncomplete, match="schema"):
        evidence_module._decode_runtime_evidence_container(
            evidence_module._canonical(document)
        )


def test_evidence_container_rejects_noncanonical_base64_pad_bits() -> None:
    files = _runtime_container_files()
    target_name = sorted(files)[0]
    files[target_name] = b"a"
    document = json.loads(
        evidence_module._encode_runtime_evidence_container(files)
    )
    entry = next(
        item for item in document["entries"] if item["path"] == target_name
    )
    assert entry["content_base64"] == "YQ=="
    entry["content_base64"] = "YR=="

    with pytest.raises(EvidenceIncomplete, match="content"):
        evidence_module._decode_runtime_evidence_container(
            evidence_module._canonical(document)
        )


def test_evidence_container_postlink_fsync_failure_preserves_complete_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "container-fsync-root"
    root.mkdir(mode=0o700)
    bundle = root / "package6-test-container.json"
    verified = False
    real_fsync = evidence_module.os.fsync

    def verify(_snapshot: object) -> bool:
        nonlocal verified
        verified = True
        return True

    def fail_after_verify(descriptor: int) -> None:
        if verified:
            raise OSError(errno.EIO, "private container fsync sentinel")
        real_fsync(descriptor)

    monkeypatch.setattr(evidence_module.os, "fsync", fail_after_verify)
    with pytest.raises(OSError, match="private container fsync sentinel"):
        evidence_module._publish_evidence_container(
            root,
            bundle,
            _runtime_container_files(),
            verify,
        )

    assert bundle.exists()
    snapshot = evidence_module._decode_runtime_evidence_container(
        bundle.read_bytes()
    )
    assert dict(snapshot) == _runtime_container_files()
    assert [path.name for path in root.iterdir()] == [bundle.name]


def test_prelink_failure_never_requires_pathname_rollback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "publication-recovery"
    root.mkdir(mode=0o700)
    sentinel = SystemExit("private publication recovery sentinel")
    assert not hasattr(evidence_module, "_rollback_created_files")

    def fail_after_write() -> None:
        raise sentinel

    with pytest.raises(SystemExit) as raised:
        evidence_module._write_files(
            root,
            {"record.json": b"payload"},
            post_write_check=fail_after_write,
        )

    assert raised.value is sentinel
    assert not (root / "record.json").exists()
    assert list(root.iterdir()) == []


def test_diagnostic_transcript_metadata_uses_private_path_schema(
    tmp_path: Path,
) -> None:
    metadata: dict[str, dict[str, dict[str, object]]] = {}
    for component, marker in (("worker", "1"), ("job_api", "2")):
        metadata[component] = {}
        for stream_name in ("stdout", "stderr"):
            raw = f"{component}-{stream_name}".encode()
            metadata[component][stream_name] = {
                "path": str(
                    tmp_path.resolve()
                    / (
                        f"package6-{component}-{marker * 32}."
                        f"{stream_name}.transcript"
                    )
                ),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "truncated": False,
            }

    evidence_module._validate_diagnostic_transcript_metadata(metadata)
