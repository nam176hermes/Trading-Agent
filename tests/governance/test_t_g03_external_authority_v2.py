from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

from scripts import t_g03_capability_topology as topology
from scripts import validate_disposable_postgres_approval as pg_approval
from scripts import validate_disposable_postgres_fixture_plan as pg_plan


INVENTORY = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")


def _digest(document: object) -> str:
    return hashlib.sha256(
        json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"),
    ).hexdigest()


def _context() -> dict[str, object]:
    return {
        "schema_version": "t-g03a-foundation-context/v1",
        "foundation_run_id": "31641536482",
        "foundation_head_sha": "18f22198c65c7bc735aeb848d8fda55209d01e78",
        "foundation_validation_date": "2026-08-13",
        "foundation_context_sha256": "4" * 64,
    }


def _absent_authority(code: str) -> dict[str, object]:
    if code in {
        "EXT-DISPOSABLE-PG-GREEN", "EXT-DISPOSABLE-PG-RED",
        "EXT-DISPOSABLE-PG-RED-EVIDENCE",
    }:
        green = code == "EXT-DISPOSABLE-PG-GREEN"
        evidence = code == "EXT-DISPOSABLE-PG-RED-EVIDENCE"
        return {
            "authority_kind": (
                "DISPOSABLE_POSTGRES_GREEN_AUTHORITY_V1"
                if green else (
                    "DISPOSABLE_POSTGRES_RED_EVIDENCE_AUTHORITY_V1"
                    if evidence else "DISPOSABLE_POSTGRES_RED_AUTHORITY_V1"
                )
            ),
            "scope": "DISPOSABLE_PG_GREEN" if green else "DISPOSABLE_PG_RED",
            "approval_record_status": "ABSENT",
            "approval_record_sha256": topology.EMPTY_SHA256,
            "approved_operation_count": 0,
            "source_binding_count": 0,
            "fixture_plan_status": "ABSENT" if green else "NOT_REQUIRED",
            "fixture_plan_sha256": topology.EMPTY_SHA256,
            "fixture_slot_count": 0,
            "postgres_bin_status": "NOT_CHECKED",
            "postgres_major_version": 16,
            "postgres_executable_manifest_sha256": topology.EMPTY_SHA256,
            "postgres_executable_count": 0,
        }
    if code == "EXT-PHASE3B-CORPUS":
        return {
            "authority_kind": "PHASE3B_REVIEWED_CORPUS_V1",
            "regular_directory_status": "ABSENT",
            "expected_inventory_sha256": topology.PHASE3B_EXPECTED_INVENTORY_SHA256,
            "observed_inventory_sha256": topology.EMPTY_SHA256,
            "required_entry_manifest_sha256": topology.EMPTY_SHA256,
            "required_entry_count": 0,
            "expected_decision_total": 16517,
            "observed_decision_total": 0,
            "expected_cost_sessions": 20,
            "observed_cost_sessions": 0,
            "expected_asset_count": 17,
            "observed_asset_count": 0,
            "expected_asset_source_files": 2209,
            "observed_asset_source_files": 0,
        }
    if code == "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS":
        return topology._absent_nautilus_external_authority()
    return topology._legacy_absent_authority()


def _external_receipt(
    code: str = "EXT-PHASE3B-CORPUS", **overrides: object,
) -> dict[str, object]:
    context = _context()
    rows = topology.load_inventory(INVENTORY)
    expected = list(topology._expected_rows(rows, code)[1])
    document: dict[str, object] = {
        "schema_version": "t-g03a-external-authority-receipt/v2",
        "foundation_run_id": context["foundation_run_id"],
        "foundation_head_sha": context["foundation_head_sha"],
        "foundation_validation_date": context["foundation_validation_date"],
        "foundation_context_sha256": context["foundation_context_sha256"],
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "lane": "external-authorities",
        "capability_or_authority_code": code,
        "expected_node_ids": expected,
        "collected_node_ids": [],
        "preflight_state": "ABSENT",
        "redacted_fact_class": (
            "AUTHORITY_EXECUTABLE_ABSENT"
            if code == "EXT-LEGACY-UV-AUTHORITY"
            else (
                "AUTHORITY_RECORD_ABSENT"
                if code.startswith("EXT-DISPOSABLE-PG-")
                else "AUTHORITY_ROOT_ABSENT"
            )
        ),
        "authority": _absent_authority(code),
        "selected_test_count": 0,
        "passed": 0,
        "failed": 0,
        "unavailable": len(expected),
        "completeness_sha256": "",
        "outcome": "DEFERRED",
        "receipt_sha256": "",
    }
    document.update(overrides)
    document["completeness_sha256"] = _digest({
        key: value
        for key, value in document.items()
        if key not in {"completeness_sha256", "receipt_sha256"}
    })
    document["receipt_sha256"] = _digest({
        key: value for key, value in document.items() if key != "receipt_sha256"
    })
    return document


def _safe_fixture_root() -> tempfile.TemporaryDirectory[str]:
    anchor = Path(f"/run/user/{os.geteuid()}")
    info = anchor.lstat()
    assert info.st_uid == os.geteuid()
    assert info.st_gid == os.getegid()
    assert info.st_mode & 0o777 == 0o700
    return tempfile.TemporaryDirectory(dir=anchor)


def _write_regular(path: Path, content: bytes = b"fixture\n", *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(content)
    path.chmod(mode)


def _complete_phase3b(root: Path) -> None:
    root.mkdir(mode=0o700)
    for relative, is_directory in topology.PHASE3B_REQUIRED_ENTRIES:
        path = root / relative
        if is_directory:
            path.mkdir(parents=True, mode=0o700)
            path.chmod(0o700)
        else:
            _write_regular(path)


def _phase3b_analysis() -> SimpleNamespace:
    return SimpleNamespace(
        inventory_hash=topology.PHASE3B_EXPECTED_INVENTORY_SHA256,
        decision_total=16517,
        cost_sessions=20,
        asset_count=17,
        asset_source_files=2209,
    )


def _complete_legacy(root: Path) -> None:
    root.mkdir(mode=0o700)
    for relative, _ in topology.LEGACY_CLOSURE_ENTRIES:
        path = root / relative
        if relative == ".venv/bin/python":
            target = root / ".venv/bin/python3"
            _write_regular(target, b"python fixture\n", mode=0o700)
            path.symlink_to(target)
        else:
            _write_regular(path)


def _complete_postgres_bin(root: Path) -> None:
    root.mkdir(mode=0o700)
    for executable in ("initdb", "pg_ctl", "psql", "pg_dump", "pg_restore"):
        _write_regular(root / executable, b"postgres-16-fixture\n", mode=0o755)


def _custody() -> dict[str, str]:
    return {
        **topology.PORTABLE_ROOT_POLICY,
        "native_custody_extension_identity": "1:2:1000:600:1",
        "native_custody_extension_sha256": "9" * 64,
    }


def _governance_raw(nodes: tuple[str, ...]) -> bytes:
    return json.dumps({
        "schema_version": 1,
        "component": "root",
        "pytest_exit_status": 0,
        "custody_policy": _custody(),
        "tests": [
            {
                "test_node_id": node,
                "component": "root",
                "outcome": "passed",
                "reason": "",
                "phase": "call",
            }
            for node in nodes
        ],
    }, sort_keys=True).encode("utf-8")


def _phase3b_pass_receipt() -> dict[str, object]:
    rows = topology.load_inventory(INVENTORY)
    code = "EXT-PHASE3B-CORPUS"
    expected = list(topology._expected_rows(rows, code)[1])
    authority = {
        **_absent_authority(code),
        "regular_directory_status": "PRIVATE_CURRENT_USER_DIRECTORY",
        "observed_inventory_sha256": topology.PHASE3B_EXPECTED_INVENTORY_SHA256,
        "required_entry_manifest_sha256": "8" * 64,
        "required_entry_count": len(topology.PHASE3B_REQUIRED_ENTRIES),
        "observed_decision_total": 16517,
        "observed_cost_sessions": 20,
        "observed_asset_count": 17,
        "observed_asset_source_files": 2209,
    }
    return _external_receipt(
        code,
        collected_node_ids=expected,
        preflight_state="VALID",
        redacted_fact_class="AUTHORITY_COMPLETE_VALIDATED",
        authority=authority,
        selected_test_count=len(expected),
        passed=len(expected),
        failed=0,
        unavailable=0,
        outcome="PASS",
    )


def test_external_v2_binds_exact_context_nodes_counts_authority_and_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: flat or weak external evidence omits context, authority, or counts."""
    rows = topology.load_inventory(INVENTORY)
    context = _context()
    receipt = _external_receipt()

    assert topology.validate_receipt(
        topology.canonical_json_bytes(receipt), rows=rows,
        foundation_run_id=str(context["foundation_run_id"]),
        foundation_head_sha=str(context["foundation_head_sha"]),
        foundation_context=context,
    ) == receipt

    real_geteuid = topology.os.geteuid
    real_getegid = topology.os.getegid
    monkeypatch.setattr(topology.os, "geteuid", lambda: 1001)
    monkeypatch.setattr(topology.os, "getegid", lambda: 1001)
    runner_absence = _external_receipt("EXT-LEGACY-UV-AUTHORITY")
    legacy_expected = list(topology._expected_rows(
        rows, "EXT-LEGACY-UV-AUTHORITY",
    )[1])
    runner_pass_authority = {
        **_absent_authority("EXT-LEGACY-UV-AUTHORITY"),
        "regular_file_status": "PRIVATE_CURRENT_USER_EXECUTABLE",
        "observed_uv_sha256": topology.LEGACY_UV_SHA256,
        "observed_uv_version": topology.LEGACY_UV_VERSION,
        "expected_uid": 1001,
        "observed_uid": 1001,
        "expected_gid": 1001,
        "observed_gid": 1001,
        "observed_mode": 0o755,
        "legacy_closure_manifest_sha256": "8" * 64,
        "legacy_closure_entry_count": len(topology.LEGACY_CLOSURE_ENTRIES),
        "sync_exit_code": 0,
    }
    runner_pass = _external_receipt(
        "EXT-LEGACY-UV-AUTHORITY",
        collected_node_ids=legacy_expected,
        preflight_state="VALID",
        redacted_fact_class="AUTHORITY_COMPLETE_VALIDATED",
        authority=runner_pass_authority,
        selected_test_count=len(legacy_expected),
        passed=len(legacy_expected),
        unavailable=0,
        outcome="PASS",
    )
    monkeypatch.setattr(topology.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(topology.os, "getegid", lambda: 1000)
    cross_host_results: list[str] = []
    for cross_host_receipt in (runner_absence, runner_pass):
        try:
            topology.validate_receipt(
                topology.canonical_json_bytes(cross_host_receipt), rows=rows,
                foundation_run_id=str(context["foundation_run_id"]),
                foundation_head_sha=str(context["foundation_head_sha"]),
                foundation_context=context,
            )
        except topology.TopologyError as error:
            cross_host_results.append(str(error))
        else:
            cross_host_results.append("VALID")
    assert (
        runner_absence["authority"]["expected_uid"],
        runner_absence["authority"]["expected_gid"],
        cross_host_results,
    ) == (-1, -1, ["VALID", "VALID"])

    forged_receipts: list[tuple[dict[str, object], str]] = []
    forged_absence_identity = json.loads(json.dumps(runner_absence))
    forged_absence_identity["authority"]["expected_uid"] = 7
    forged_receipts.append((forged_absence_identity, "exact absence"))
    forged_absence_digest = json.loads(json.dumps(runner_absence))
    forged_absence_digest["authority"]["observed_uv_sha256"] = "7" * 64
    forged_receipts.append((forged_absence_digest, "exact absence"))
    forged_pass_mismatch = json.loads(json.dumps(runner_pass))
    forged_pass_mismatch["authority"]["observed_uid"] = 1002
    forged_receipts.append((forged_pass_mismatch, "PASS authority facts"))
    forged_pass_negative = json.loads(json.dumps(runner_pass))
    forged_pass_negative["authority"]["expected_uid"] = -1
    forged_pass_negative["authority"]["observed_uid"] = -1
    forged_receipts.append((forged_pass_negative, "PASS authority facts"))
    forged_outcome = json.loads(json.dumps(runner_absence))
    forged_outcome["outcome"] = "PASS"
    forged_receipts.append((forged_outcome, "execution proof"))
    for forged, message in forged_receipts:
        forged["completeness_sha256"] = topology.external_completeness_sha256(
            forged,
        )
        forged["receipt_sha256"] = topology.payload_sha256(forged)
        with pytest.raises(topology.TopologyError, match=message):
            topology.validate_receipt(
                topology.canonical_json_bytes(forged), rows=rows,
                foundation_run_id=str(context["foundation_run_id"]),
                foundation_head_sha=str(context["foundation_head_sha"]),
                foundation_context=context,
            )
    monkeypatch.setattr(topology.os, "geteuid", real_geteuid)
    monkeypatch.setattr(topology.os, "getegid", real_getegid)
    assert topology.os.geteuid is real_geteuid
    assert topology.os.getegid is real_getegid

    for mutation in (
        {"foundation_context_sha256": "5" * 64},
        {"expected_node_ids": []},
        {"selected_test_count": False},
        {"unavailable": 2},
    ):
        forged = _external_receipt(**mutation)
        with pytest.raises(topology.TopologyError):
            topology.validate_receipt(
                topology.canonical_json_bytes(forged), rows=rows,
                foundation_run_id=str(context["foundation_run_id"]),
                foundation_head_sha=str(context["foundation_head_sha"]),
                foundation_context=context,
            )

    nautilus = _external_receipt("EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS")
    assert topology.validate_receipt(
        topology.canonical_json_bytes(nautilus), rows=rows,
        foundation_run_id=str(context["foundation_run_id"]),
        foundation_head_sha=str(context["foundation_head_sha"]),
        foundation_context=context,
    ) == nautilus
    forged = json.loads(json.dumps(nautilus))
    forged["authority"].pop("artifact_wheel_sha256")
    forged["completeness_sha256"] = topology.external_completeness_sha256(forged)
    forged["receipt_sha256"] = topology.payload_sha256(forged)
    with pytest.raises(topology.TopologyError, match="authority facts"):
        topology.parse_receipt(topology.canonical_json_bytes(forged))

    for code in (
        "EXT-DISPOSABLE-PG-GREEN", "EXT-DISPOSABLE-PG-RED",
        "EXT-DISPOSABLE-PG-RED-EVIDENCE",
    ):
        disposable_pg = _external_receipt(code)
        assert topology.validate_receipt(
            topology.canonical_json_bytes(disposable_pg), rows=rows,
            foundation_run_id=str(context["foundation_run_id"]),
            foundation_head_sha=str(context["foundation_head_sha"]),
            foundation_context=context,
        ) == disposable_pg

    visible_node = _external_receipt()
    visible_node["expected_node_ids"] = [
        "tests/example.py::test_example[param with visible spaces]",
    ]
    visible_node["completeness_sha256"] = topology.external_completeness_sha256(
        visible_node,
    )
    visible_node["receipt_sha256"] = topology.payload_sha256(visible_node)
    assert topology.parse_receipt(
        topology.canonical_json_bytes(visible_node),
    )["expected_node_ids"] == visible_node["expected_node_ids"]
    for hostile_node in ("tests/example.py::test_example[line\nbreak]", "tests/é.py::test"):
        hostile = json.loads(json.dumps(visible_node))
        hostile["expected_node_ids"] = [hostile_node]
        hostile["completeness_sha256"] = topology.external_completeness_sha256(
            hostile,
        )
        hostile["receipt_sha256"] = topology.payload_sha256(hostile)
        with pytest.raises(topology.TopologyError, match="expected_node_ids"):
            topology.parse_receipt(topology.canonical_json_bytes(hostile))

    monkeypatch.setenv("DATABASE_URL", "postgresql://hostile.example/runtime")
    monkeypatch.setenv("TRADING_DB_FOREIGN", "hostile")
    monkeypatch.setenv("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE", "FOREIGN")
    monkeypatch.setenv("DISPOSABLE_PG_RED_APPROVAL_RECORD", "/foreign/record")
    hostile_git_environment = {
        "GIT_DIR": "/foreign/git-dir",
        "GIT_WORK_TREE": "/foreign/worktree",
        "GIT_INDEX_FILE": "/foreign/index",
        "GIT_OBJECT_DIRECTORY": "/foreign/objects",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/foreign/alternate-objects",
        "GIT_CONFIG": "/foreign/config",
        "GIT_CONFIG_GLOBAL": "/foreign/global-config",
        "GIT_CONFIG_SYSTEM": "/foreign/system-config",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "/foreign/monitor",
        "GIT_EXTERNAL_DIFF": "/foreign/diff",
    }
    for name, value in hostile_git_environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/foreign/xdg")
    monkeypatch.setenv("PATH", "/foreign/bin")
    isolated = topology._pg_exact_environment((("PG_SAFE_OVERLAY", "exact"),))
    assert isolated["PG_SAFE_OVERLAY"] == "exact"
    assert not {
        "DATABASE_URL", "TRADING_DB_FOREIGN",
        "TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE",
        "DISPOSABLE_PG_RED_APPROVAL_RECORD",
    } & isolated.keys()
    assert not set(hostile_git_environment) & isolated.keys()
    assert "HOME" not in isolated
    assert "XDG_CONFIG_HOME" not in isolated
    assert isolated["PATH"] == "/usr/bin:/bin"
    with pytest.raises(topology.TopologyError, match="runtime authority"):
        topology._pg_exact_environment((("PGPASSWORD", "hostile"),))

    monkeypatch.setattr(
        topology.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("PostgreSQL preflight must not start a child process"),
        ),
    )
    with _safe_fixture_root() as raw:
        root = Path(raw)
        approval = root / "approval.json"
        plan = root / "plan.json"
        postgres_bin = root / "postgres-bin"
        _write_regular(approval, b'{"kind":"approval"}')
        _write_regular(plan, b'{"kind":"plan"}')
        _complete_postgres_bin(postgres_bin)
        qualified: list[tuple[str, object, object]] = []

        def qualifier(
            code: str, approval_document: object, plan_document: object,
            executable_manifest_sha256: str,
        ) -> dict[str, object]:
            qualified.append((code, approval_document, plan_document))
            green = code == "EXT-DISPOSABLE-PG-GREEN"
            evidence = code == "EXT-DISPOSABLE-PG-RED-EVIDENCE"
            assert plan_document == ({"kind": "plan"} if green else None)
            return {
                "authority_kind": (
                    "DISPOSABLE_POSTGRES_GREEN_AUTHORITY_V1"
                    if green else (
                        "DISPOSABLE_POSTGRES_RED_EVIDENCE_AUTHORITY_V1"
                        if evidence else "DISPOSABLE_POSTGRES_RED_AUTHORITY_V1"
                    )
                ),
                "scope": "DISPOSABLE_PG_GREEN" if green else "DISPOSABLE_PG_RED",
                "approval_record_status": "PRIVATE_RETAINED_APPROVAL_RECORD",
                "approval_record_sha256": hashlib.sha256(
                    topology.canonical_json_bytes(approval_document),
                ).hexdigest(),
                "approved_operation_count": 26 if green else (2 if evidence else 3),
                "source_binding_count": 13,
                "fixture_plan_status": (
                    "PRIVATE_RETAINED_FIXTURE_PLAN" if green else "NOT_REQUIRED"
                ),
                "fixture_plan_sha256": (
                    hashlib.sha256(topology.canonical_json_bytes(plan_document)).hexdigest()
                    if green else topology.EMPTY_SHA256
                ),
                "fixture_slot_count": 4 if green else 0,
                "postgres_bin_status": "RETAINED_POSTGRESQL_16_BINARIES",
                "postgres_major_version": 16,
                "postgres_executable_manifest_sha256": executable_manifest_sha256,
                "postgres_executable_count": 5,
            }

        copy_roots: list[Path] = []
        for code in (
            "EXT-DISPOSABLE-PG-GREEN", "EXT-DISPOSABLE-PG-RED",
            "EXT-DISPOSABLE-PG-RED-EVIDENCE",
        ):
            with topology._retained_external_authority(
                code,
                pg_approval_path=approval,
                pg_fixture_plan_path=(plan if code.endswith("GREEN") else None),
                pg_postgres_bin=postgres_bin,
                pg_copy_parent=root,
                pg_qualifier=qualifier,
            ) as session:
                assert (session.state, session.fact) == (
                    "VALID", "AUTHORITY_COMPLETE_VALIDATED",
                )
                environment = dict(session.execution_environment)
                git_home = Path(environment["HOME"])
                assert git_home.parent == Path(
                    environment["TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"],
                ).parent
                assert stat.S_IMODE(git_home.stat().st_mode) == 0o700
                assert environment["PATH"] == "/usr/bin:/bin"
                assert {
                    name: environment[name]
                    for name in (
                        "GIT_ATTR_NOSYSTEM", "GIT_CONFIG", "GIT_CONFIG_GLOBAL",
                        "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_COUNT",
                        "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
                        "GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1",
                        "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT",
                    )
                } == {
                    "GIT_ATTR_NOSYSTEM": "1",
                    "GIT_CONFIG": "/dev/null",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_COUNT": "2",
                    "GIT_CONFIG_KEY_0": "core.fsmonitor",
                    "GIT_CONFIG_VALUE_0": "false",
                    "GIT_CONFIG_KEY_1": "core.hooksPath",
                    "GIT_CONFIG_VALUE_1": "/dev/null",
                    "GIT_OPTIONAL_LOCKS": "0",
                    "GIT_TERMINAL_PROMPT": "0",
                }
                assert environment["TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE"] == (
                    "DISPOSABLE_PG_GREEN" if code.endswith("GREEN") else "DISPOSABLE_PG_RED"
                )
                assert environment["TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"] != str(approval)
                assert Path(environment["TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"]).read_bytes() == (
                    topology.canonical_json_bytes({"kind": "approval"})
                )
                if code.endswith("GREEN"):
                    assert environment["TRADING_TEST_DISPOSABLE_FIXTURE_PLAN"] != str(plan)
                else:
                    assert "TRADING_TEST_DISPOSABLE_FIXTURE_PLAN" not in environment
                if code.endswith("RED-EVIDENCE"):
                    output = Path(
                        environment["TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR"],
                    )
                    assert output.is_dir()
                    assert stat.S_IMODE(output.stat().st_mode) == 0o700
                else:
                    assert (
                        "TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR"
                        not in environment
                    )
                copy_roots.append(
                    Path(environment["TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"]).parent,
                )
                topology._postcheck_external_authority(session)
            assert not copy_roots[-1].exists()

        hardlink = root / "approval-hardlink.json"
        os.link(approval, hardlink)
        forbidden = lambda *_args: (_ for _ in ()).throw(
            AssertionError("unsafe authority reached the validator"),
        )
        with topology._retained_external_authority(
            "EXT-DISPOSABLE-PG-RED",
            pg_approval_path=hardlink,
            pg_postgres_bin=postgres_bin,
            pg_copy_parent=root,
            pg_qualifier=forbidden,
        ) as session:
            assert (session.state, session.fact) == ("INVALID", "AUTHORITY_INVALID")
        hardlink.unlink()

        approval_symlink = root / "approval-symlink.json"
        approval_symlink.symlink_to(approval)
        with topology._retained_external_authority(
            "EXT-DISPOSABLE-PG-RED", pg_approval_path=approval_symlink,
            pg_postgres_bin=postgres_bin, pg_copy_parent=root,
            pg_qualifier=forbidden,
        ) as session:
            assert (session.state, session.fact) == ("INVALID", "AUTHORITY_INVALID")

        with topology._retained_external_authority(
            "EXT-DISPOSABLE-PG-GREEN", pg_approval_path=approval,
            pg_postgres_bin=postgres_bin, pg_copy_parent=root,
            pg_qualifier=forbidden,
        ) as session:
            assert (session.state, session.fact) == ("PARTIAL", "AUTHORITY_PARTIAL")

        unsafe_copy_parent = root / "unsafe-copy-parent"
        unsafe_copy_parent.mkdir(mode=0o777)
        unsafe_copy_parent.chmod(0o777)
        with topology._retained_external_authority(
            "EXT-DISPOSABLE-PG-RED", pg_approval_path=approval,
            pg_postgres_bin=postgres_bin, pg_copy_parent=unsafe_copy_parent,
            pg_qualifier=qualifier,
        ) as session:
            assert (session.state, session.fact) == ("INVALID", "AUTHORITY_INVALID")

        with topology._retained_external_authority(
            "EXT-DISPOSABLE-PG-RED-EVIDENCE", pg_approval_path=approval,
            pg_postgres_bin=postgres_bin, pg_copy_parent=root,
            pg_qualifier=qualifier,
        ) as session:
            output = Path(dict(session.execution_environment)[
                "TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR"
            ])
            output.chmod(0o755)
            with pytest.raises(topology.TopologyError, match="authority changed"):
                topology._postcheck_external_authority(session)
        assert len(qualified) == 4

        head = "a" * 40
        tree = "b" * 40
        monkeypatch.setattr(
            pg_approval, "validate_disposable_postgres_approval_record",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            pg_approval, "validate_source_binding_files",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            pg_plan, "validate_disposable_postgres_fixture_plan",
            lambda *_args, **_kwargs: tuple(
                SimpleNamespace(test_path=path, operation_id=operation, ordinal=1)
                for path, operation in sorted(topology.PG_GREEN_PLANNED_OPERATIONS)
            ),
        )
        sql_digest = hashlib.sha256(
            (Path.cwd() / topology.PG_RED_SQL_PATH).read_bytes(),
        ).hexdigest()

        def authority_document(code: str) -> dict[str, object]:
            binding = None
            if code != "EXT-DISPOSABLE-PG-GREEN":
                binding = {
                    "operation_id": topology.PG_RED_BINDING_OPERATION[code],
                    "sql_path": topology.PG_RED_SQL_PATH,
                    "sql_sha256": sql_digest,
                }
            return {
                "source": {"commit": head, "tree": tree},
                "approved_operations": [
                    {"test_path": path, "operation_id": operation}
                    for path, operation in sorted(
                        topology._disposable_pg_operations(code),
                    )
                ],
                "red_sql_binding": binding,
            }

        for code in sorted(topology.DISPOSABLE_PG_CODES):
            document = authority_document(code)
            plan_document = {} if code.endswith("GREEN") else None
            facts = topology._qualify_disposable_pg_authority(
                code, document, plan_document, "c" * 64,
                foundation_head_sha=head, foundation_source_tree=tree,
            )
            assert facts["approved_operation_count"] == len(
                topology._disposable_pg_operations(code),
            )

        cross_code = authority_document("EXT-DISPOSABLE-PG-RED-EVIDENCE")
        with pytest.raises(topology.TopologyError, match="operation authority"):
            topology._qualify_disposable_pg_authority(
                "EXT-DISPOSABLE-PG-RED", cross_code, None, "c" * 64,
                foundation_head_sha=head, foundation_source_tree=tree,
            )
        wrong_binding = authority_document("EXT-DISPOSABLE-PG-RED")
        wrong_binding["red_sql_binding"] = {
            "operation_id": topology.PG_RED_BINDING_OPERATION[
                "EXT-DISPOSABLE-PG-RED-EVIDENCE"
            ],
            "sql_path": topology.PG_RED_SQL_PATH,
            "sql_sha256": sql_digest,
        }
        with pytest.raises(topology.TopologyError, match="SQL binding"):
            topology._qualify_disposable_pg_authority(
                "EXT-DISPOSABLE-PG-RED", wrong_binding, None, "c" * 64,
                foundation_head_sha=head, foundation_source_tree=tree,
            )

        for code in sorted(topology.DISPOSABLE_PG_CODES):
            green = code.endswith("GREEN")
            evidence = code.endswith("RED-EVIDENCE")
            expected = list(topology._expected_rows(
                topology.load_inventory(INVENTORY), code,
            )[1])
            authority = {
                **_absent_authority(code),
                "approval_record_status": "PRIVATE_RETAINED_APPROVAL_RECORD",
                "approval_record_sha256": "d" * 64,
                "approved_operation_count": 26 if green else (2 if evidence else 3),
                "source_binding_count": 13,
                "fixture_plan_status": (
                    "PRIVATE_RETAINED_FIXTURE_PLAN" if green else "NOT_REQUIRED"
                ),
                "fixture_plan_sha256": "e" * 64 if green else topology.EMPTY_SHA256,
                "fixture_slot_count": 4 if green else 0,
                "postgres_bin_status": "RETAINED_POSTGRESQL_16_BINARIES",
                "postgres_executable_manifest_sha256": "f" * 64,
                "postgres_executable_count": 5,
            }
            passed = _external_receipt(
                code, collected_node_ids=expected, preflight_state="VALID",
                redacted_fact_class="AUTHORITY_COMPLETE_VALIDATED",
                authority=authority, selected_test_count=len(expected),
                passed=len(expected), unavailable=0, outcome="PASS",
            )
            assert topology.validate_receipt(
                topology.canonical_json_bytes(passed),
                rows=topology.load_inventory(INVENTORY),
                foundation_run_id=str(_context()["foundation_run_id"]),
                foundation_head_sha=str(_context()["foundation_head_sha"]),
                foundation_context=_context(),
            ) == passed
            forged = json.loads(json.dumps(passed))
            forged["authority"]["approved_operation_count"] += 1
            forged["completeness_sha256"] = topology.external_completeness_sha256(
                forged,
            )
            forged["receipt_sha256"] = topology.payload_sha256(forged)
            with pytest.raises(topology.TopologyError, match="authority facts drift"):
                topology.validate_receipt(
                    topology.canonical_json_bytes(forged),
                    rows=topology.load_inventory(INVENTORY),
                    foundation_run_id=str(_context()["foundation_run_id"]),
                    foundation_head_sha=str(_context()["foundation_head_sha"]),
                    foundation_context=_context(),
                )

        captured_environment: dict[str, str] = {}
        exact_node = "tests/example.py::test_exact"
        exact_report = root / "exact-governance.json"

        def exact_subprocess(
            _command: list[str], **kwargs: object,
        ) -> subprocess.CompletedProcess[bytes]:
            environment = kwargs["env"]
            assert isinstance(environment, dict)
            captured_environment.update(environment)
            exact_report.write_text(json.dumps({
                "tests": [{"test_node_id": exact_node, "outcome": "passed"}],
            }), encoding="utf-8")
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

        monkeypatch.setattr(topology.subprocess, "run", exact_subprocess)
        assert topology._run_exact(
            (exact_node,), exact_report,
            environment_overlay=tuple(sorted({
                **topology.PG_PAPER_ENVIRONMENT,
                "TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE": "DISPOSABLE_PG_RED",
            }.items())),
        ) == (exact_node,)
        assert captured_environment["TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE"] == (
            "DISPOSABLE_PG_RED"
        )
        assert "DATABASE_URL" not in captured_environment
        assert "TRADING_DB_FOREIGN" not in captured_environment


def test_external_v1_is_stale_without_changing_native_v2() -> None:
    """Break caught: an external flat-v1 receipt remains accepted after migration."""
    rows = topology.load_inventory(INVENTORY)
    context = _context()
    code = "EXT-PHASE3B-CORPUS"
    expected = list(topology._expected_rows(rows, code)[1])
    stale: dict[str, object] = {
        "schema_version": "t-g03a-capability-receipt/v1",
        "foundation_run_id": context["foundation_run_id"],
        "foundation_head_sha": context["foundation_head_sha"],
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "lane": "external-authorities",
        "capability_or_authority_code": code,
        "expected_node_ids": expected,
        "collected_node_ids": [],
        "completeness_sha256": "",
        "preflight_state": "ABSENT",
        "redacted_fact_class": "AUTHORITY_ROOT_ABSENT",
        "outcome": "DEFERRED",
        "receipt_sha256": "",
    }
    stale["completeness_sha256"] = _digest({
        field: stale[field]
        for field in (
            "lane", "capability_or_authority_code", "expected_node_ids",
            "collected_node_ids",
        )
    })
    stale["receipt_sha256"] = _digest({
        key: value for key, value in stale.items() if key != "receipt_sha256"
    })

    with pytest.raises(topology.TopologyError, match="external v1"):
        topology.validate_receipt(
            topology.canonical_json_bytes(stale), rows=rows,
            foundation_run_id=str(context["foundation_run_id"]),
            foundation_head_sha=str(context["foundation_head_sha"]),
            foundation_context=context,
        )


def test_external_parent_chain_includes_the_filesystem_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: checking descendants but trusting an unsafe anchor is incomplete."""
    safe = SimpleNamespace(
        st_mode=0o040755, st_uid=0, st_gid=0, st_dev=1, st_ino=1,
    )
    unsafe_anchor = SimpleNamespace(
        st_mode=0o040777, st_uid=0, st_gid=0, st_dev=1, st_ino=2,
    )

    def fake_lstat(path: Path) -> SimpleNamespace:
        return unsafe_anchor if path == Path("/") else safe

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    assert topology._external_parent_chain_safe(Path("/authority/file")) is False


def test_phase3b_session_retains_root_and_revalidates_manifest_and_analysis() -> None:
    """Break caught: tuple preflight cannot detect required-entry drift before acceptance."""
    with _safe_fixture_root() as raw:
        root = Path(raw) / "corpus"
        _complete_phase3b(root)
        with topology._retained_external_authority(
            "EXT-PHASE3B-CORPUS",
            corpus_root=root,
            corpus_validator=lambda _root: _phase3b_analysis(),
        ) as session:
            assert (session.state, session.fact) == (
                "VALID", "AUTHORITY_COMPLETE_VALIDATED",
            )
            assert session.authority["required_entry_count"] == len(
                topology.PHASE3B_REQUIRED_ENTRIES,
            )
            assert set(session.authority).isdisjoint({"path", "root", "contents"})
            topology._postcheck_external_authority(session)
            (root / "asset_registry.py").write_bytes(b"changed\n")
            with pytest.raises(topology.TopologyError, match="authority"):
                topology._postcheck_external_authority(session)


def test_legacy_session_executes_only_retained_uv_and_detects_closure_drift() -> None:
    """Break caught: UV is closed after preflight or a version-only impostor is accepted."""
    commands: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append((command, kwargs))
        stdout = b"fixture-uv 1.0\n" if command[1:] == ["--version"] else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    with _safe_fixture_root() as raw:
        root = Path(raw)
        legacy = root / "legacy"
        _complete_legacy(legacy)
        uv = root / "uv"
        _write_regular(uv, b"fixture uv authority\n", mode=0o755)
        expected = hashlib.sha256(uv.read_bytes()).hexdigest()
        with topology._retained_external_authority(
            "EXT-LEGACY-UV-AUTHORITY",
            uv_path=uv,
            legacy_root=legacy,
            expected_uv_sha256=expected,
            expected_uv_version="fixture-uv 1.0",
            runner=runner,
        ) as session:
            assert (session.state, session.fact) == (
                "VALID", "AUTHORITY_COMPLETE_VALIDATED",
            )
            assert [command[1:] for command, _ in commands] == [
                ["--version"], ["sync", "--frozen", "--extra", "test"],
            ]
            assert all(command[0].startswith("/proc/self/fd/") for command, _ in commands)
            assert all(kwargs["pass_fds"] == (session.descriptors[0],) for _, kwargs in commands)
            topology._postcheck_external_authority(session)
            (legacy / "uv.lock").write_bytes(b"changed\n")
            with pytest.raises(topology.TopologyError, match="authority"):
                topology._postcheck_external_authority(session)


def test_legacy_group_writable_exception_is_scoped_to_exact_real_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: the real 0775 exception is broadened to an arbitrary root."""

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        stdout = b"fixture-uv 1.0\n" if command[1:] == ["--version"] else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    with _safe_fixture_root() as raw:
        root = Path(raw)
        uv = root / "uv"
        _write_regular(uv, b"fixture uv authority\n", mode=0o755)
        projects = root / "projects"
        projects.mkdir(mode=0o775)
        projects.chmod(0o775)
        arbitrary = projects / "arbitrary"
        _complete_legacy(arbitrary)
        with topology._retained_external_authority(
            "EXT-LEGACY-UV-AUTHORITY", uv_path=uv, legacy_root=arbitrary,
            expected_uv_sha256=hashlib.sha256(uv.read_bytes()).hexdigest(),
            expected_uv_version="fixture-uv 1.0", runner=runner,
        ) as session:
            assert session.state == "INVALID"

        exact_legacy = projects / "legacy"
        _complete_legacy(exact_legacy)
        monkeypatch.setattr(topology, "REAL_LEGACY_ROOT", exact_legacy)
        assert topology._external_parent_chain_safe(exact_legacy) is False
        assert topology._external_parent_chain_safe(
            exact_legacy, legacy_component_policy=True,
        ) is True
        assert topology._external_parent_chain_safe(
            arbitrary, legacy_component_policy=True,
        ) is False


@pytest.mark.parametrize(
    ("unsafe_mode", "unsafe_uid", "unsafe_gid"),
    (
        (0o040777, os.geteuid(), os.getegid()),
        (0o040755, os.geteuid() + 10000, os.getegid()),
        (0o040755, os.geteuid(), os.getegid() + 10000),
        (0o120777, os.geteuid(), os.getegid()),
        (0o100600, os.geteuid(), os.getegid()),
    ),
)
def test_real_legacy_ancestor_exception_rejects_world_foreign_symlink_and_special(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_mode: int,
    unsafe_uid: int,
    unsafe_gid: int,
) -> None:
    """Break caught: the exact real-path exception weakens non-group-write policy."""
    real_legacy = topology.ROOT / "legacy/research-backend"
    unsafe_at = topology.ROOT.parent

    def fake_lstat(path: Path) -> SimpleNamespace:
        if path == unsafe_at:
            return SimpleNamespace(
                st_mode=unsafe_mode, st_uid=unsafe_uid, st_gid=unsafe_gid,
                st_dev=1, st_ino=2, st_size=0, st_mtime_ns=0, st_ctime_ns=0,
            )
        return SimpleNamespace(
            st_mode=0o040755, st_uid=0, st_gid=0,
            st_dev=1, st_ino=1, st_size=0, st_mtime_ns=0, st_ctime_ns=0,
        )

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    assert topology._external_parent_chain_safe(
        real_legacy, legacy_component_policy=True,
    ) is False


def test_phase_and_uv_never_inherit_real_legacy_group_write_exception() -> None:
    """Break caught: the scoped legacy policy leaks into strict Phase or UV paths."""
    with _safe_fixture_root() as raw:
        root = Path(raw)
        ancestor = root / "group-writable"
        ancestor.mkdir(mode=0o775)
        ancestor.chmod(0o775)
        corpus = ancestor / "corpus"
        _complete_phase3b(corpus)
        uv = ancestor / "uv"
        _write_regular(uv, b"uv fixture\n", mode=0o755)

        phase_state, phase_descriptor, _ = topology._open_external_directory(
            corpus, exact_mode=0o700,
        )
        uv_state, uv_descriptor, _ = topology._open_external_regular_executable(uv)
        for descriptor in (phase_descriptor, uv_descriptor):
            if descriptor >= 0:
                os.close(descriptor)
        assert phase_state == "INVALID"
        assert uv_state == "INVALID"


@topology.contextmanager
def _valid_session_with_mutable_ancestor(kind: str, root: Path):
    if kind == "phase":
        ancestor = root / "phase-parent"
        ancestor.mkdir(mode=0o700)
        authority_root = ancestor / "corpus"
        _complete_phase3b(authority_root)
        with topology._retained_external_authority(
            "EXT-PHASE3B-CORPUS", corpus_root=authority_root,
            corpus_validator=lambda _root: _phase3b_analysis(),
        ) as session:
            assert session.state == "VALID"
            yield session, ancestor, authority_root
        return

    uv_parent = root / "uv-parent"
    uv_parent.mkdir(mode=0o700)
    uv = uv_parent / "uv"
    _write_regular(uv, b"fixture uv authority\n", mode=0o755)
    ancestor = root / "legacy-parent"
    ancestor.mkdir(mode=0o700)
    authority_root = ancestor / "legacy"
    _complete_legacy(authority_root)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        stdout = b"fixture-uv 1.0\n" if command[1:] == ["--version"] else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    with topology._retained_external_authority(
        "EXT-LEGACY-UV-AUTHORITY", uv_path=uv, legacy_root=authority_root,
        expected_uv_sha256=hashlib.sha256(uv.read_bytes()).hexdigest(),
        expected_uv_version="fixture-uv 1.0", runner=runner,
    ) as session:
        assert session.state == "VALID"
        yield session, ancestor, authority_root


def _drift_ancestor(ancestor: Path, authority_root: Path, drift: str) -> None:
    if drift == "mode":
        ancestor.chmod(0o777)
        return
    moved = ancestor.with_name(ancestor.name + "-moved")
    ancestor.rename(moved)
    if drift == "symlink":
        ancestor.symlink_to(moved, target_is_directory=True)
        return
    ancestor.mkdir(mode=0o700)
    (moved / authority_root.name).rename(ancestor / authority_root.name)


@pytest.mark.parametrize("kind", ("phase", "legacy"))
@pytest.mark.parametrize("drift", ("mode", "identity", "symlink"))
def test_valid_external_session_rejects_ancestor_drift(
    kind: str, drift: str,
) -> None:
    """Break caught: retained leaf identity hides ancestor policy drift."""
    with _safe_fixture_root() as raw:
        with _valid_session_with_mutable_ancestor(kind, Path(raw)) as (
            session, ancestor, authority_root,
        ):
            _drift_ancestor(ancestor, authority_root, drift)
            with pytest.raises(topology.TopologyError, match="authority changed"):
                topology._postcheck_external_authority(session)


@pytest.mark.parametrize("kind", ("phase", "legacy"))
@pytest.mark.parametrize("drift", ("mode", "identity", "symlink"))
@pytest.mark.parametrize("boundary", ("before-bundle", "after-bundle"))
def test_external_transaction_rechecks_ancestor_policy_at_both_boundaries(
    monkeypatch: pytest.MonkeyPatch, kind: str, drift: str, boundary: str,
) -> None:
    """Break caught: one Architecture-A pre-marker boundary trusts stale ancestry."""
    rows = topology.load_inventory(INVENTORY)
    with _safe_fixture_root() as raw:
        root = Path(raw)
        evidence = root / "evidence"
        with _valid_session_with_mutable_ancestor(kind, root) as (
            session, ancestor, authority_root,
        ):
            expected = topology._expected_rows(rows, session.code)[1]
            receipt = topology.make_external_receipt(
                context=_context(), code=session.code, expected=expected,
                collected=expected, session=session, outcome="PASS",
                selected_test_count=3, passed=3, failed=0, unavailable=0,
            )
            governance = _governance_raw(expected)
            if boundary == "before-bundle":
                _drift_ancestor(ancestor, authority_root, drift)
            else:
                real_publish = topology._publish_external_candidate_bundle

                def publish_then_drift(candidate: Path, destination: Path) -> None:
                    real_publish(candidate, destination)
                    _drift_ancestor(ancestor, authority_root, drift)

                monkeypatch.setattr(
                    topology, "_publish_external_candidate_bundle",
                    publish_then_drift,
                )

            with pytest.raises(topology.TopologyError, match="authority changed"):
                topology._publish_external_receipt_transaction(
                    receipt=receipt, evidence_root=evidence, session=session,
                    governance_raw=governance,
                )
            topology_root = evidence / "capability-topology"
            marker = topology_root / f"{session.code}.json"
            bundle = topology_root / f"{session.code}.artifacts"
            assert not marker.exists()
            assert bundle.exists() is (boundary == "after-bundle")


def test_external_architecture_a_accepts_only_exact_bundle_then_marker() -> None:
    """Break caught: an external PASS can still use flat receipt/governance leaves."""
    rows = topology.load_inventory(INVENTORY)
    context = _context()
    receipt = _phase3b_pass_receipt()
    expected = tuple(receipt["expected_node_ids"])
    governance = _governance_raw(expected)
    with _safe_fixture_root() as raw:
        topology_root = Path(raw) / "capability-topology"
        topology_root.mkdir(mode=0o700)
        candidate = topology._stage_external_candidate(
            topology_root, receipt, governance,
        )
        marker = topology_root / "EXT-PHASE3B-CORPUS.json"
        topology._publish_external_candidate_bundle(
            candidate, marker.with_suffix(".artifacts"),
        )
        topology._publish_external_acceptance_marker(
            marker, topology.canonical_json_bytes(receipt),
        )

        accepted, executed = topology.validate_external_artifact_set(
            marker, rows=rows, foundation_context=context,
            sealed_custody=_custody(),
        )
        assert accepted == receipt
        assert executed == expected
        assert marker.read_bytes() == (
            marker.with_suffix(".artifacts") / "receipt.json"
        ).read_bytes()
        assert set(path.name for path in marker.with_suffix(".artifacts").iterdir()) == {
            "receipt.json", "governance.json", "manifest.json",
        }
        assert not marker.with_suffix(".governance.json").exists()


@pytest.mark.parametrize("tamper", ("marker", "manifest", "governance", "filename"))
def test_external_architecture_a_rejects_bound_artifact_tamper(tamper: str) -> None:
    """Break caught: one mutable or renamed bundle component can qualify PASS."""
    rows = topology.load_inventory(INVENTORY)
    receipt = _phase3b_pass_receipt()
    governance = _governance_raw(tuple(receipt["expected_node_ids"]))
    with _safe_fixture_root() as raw:
        topology_root = Path(raw) / "capability-topology"
        topology_root.mkdir(mode=0o700)
        marker = topology_root / "EXT-PHASE3B-CORPUS.json"
        bundle = marker.with_suffix(".artifacts")
        candidate = topology._stage_external_candidate(
            topology_root, receipt, governance,
        )
        topology._publish_external_candidate_bundle(candidate, bundle)
        topology._publish_external_acceptance_marker(
            marker, topology.canonical_json_bytes(receipt),
        )
        candidate_marker = marker
        if tamper == "marker":
            marker.write_bytes(b"{}")
        elif tamper == "manifest":
            manifest = bundle / "manifest.json"
            manifest.write_bytes(manifest.read_bytes() + b"\n")
        elif tamper == "governance":
            (bundle / "governance.json").unlink()
        else:
            candidate_marker = topology_root / "renamed.json"
            marker.rename(candidate_marker)

        with pytest.raises((topology.TopologyError, OSError)):
            topology.validate_external_artifact_set(
                candidate_marker, rows=rows, foundation_context=_context(),
                sealed_custody=_custody(),
            )


def test_external_publication_preserves_foreign_occupancy_without_unlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: retry cleanup deletes a foreign bundle or marker before failing."""
    receipt = _external_receipt()
    session = topology.ExternalAuthoritySession(
        "EXT-PHASE3B-CORPUS", "ABSENT", "AUTHORITY_ROOT_ABSENT",
        _absent_authority("EXT-PHASE3B-CORPUS"), (), lambda: None,
    )
    original_unlink = Path.unlink
    unlinked: list[Path] = []

    def observed_unlink(path: Path, *args: object, **kwargs: object) -> None:
        unlinked.append(path)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", observed_unlink)
    with _safe_fixture_root() as raw:
        root = Path(raw)
        foreign_bundle_root = root / "bundle-conflict"
        topology_root = foreign_bundle_root / "capability-topology"
        topology_root.mkdir(parents=True, mode=0o700)
        bundle = topology_root / "EXT-PHASE3B-CORPUS.artifacts"
        bundle.mkdir(mode=0o700)
        foreign = bundle / "foreign"
        foreign.write_bytes(b"preserve me")
        foreign.chmod(0o600)

        with pytest.raises(topology.TopologyError):
            topology._publish_external_receipt_transaction(
                receipt=receipt, evidence_root=foreign_bundle_root,
                session=session, governance_raw=None,
            )
        assert foreign.read_bytes() == b"preserve me"

        foreign_marker_root = root / "marker-conflict"
        topology_root = foreign_marker_root / "capability-topology"
        topology_root.mkdir(parents=True, mode=0o700)
        marker = topology_root / "EXT-PHASE3B-CORPUS.json"
        marker.write_bytes(b"foreign marker")
        marker.chmod(0o600)
        with pytest.raises(topology.TopologyError):
            topology._publish_external_receipt_transaction(
                receipt=receipt, evidence_root=foreign_marker_root,
                session=session, governance_raw=None,
            )
        assert marker.read_bytes() == b"foreign marker"
        assert unlinked == []


def test_external_ambiguous_marker_success_requires_exact_retained_reread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a post-rename exception is either blindly failed or trusted."""
    rows = topology.load_inventory(INVENTORY)
    receipt = _external_receipt()
    with _safe_fixture_root() as raw:
        topology_root = Path(raw) / "capability-topology"
        topology_root.mkdir(mode=0o700)
        marker = topology_root / "EXT-PHASE3B-CORPUS.json"
        candidate = topology._stage_external_candidate(topology_root, receipt, None)
        topology._publish_external_candidate_bundle(
            candidate, marker.with_suffix(".artifacts"),
        )
        real_publish = topology._publish_external_acceptance_marker

        def ambiguous(path: Path, content: bytes) -> None:
            real_publish(path, content)
            raise OSError("status lost after successful marker rename")

        monkeypatch.setattr(
            topology, "_publish_external_acceptance_marker", ambiguous,
        )
        topology._publish_external_marker_or_resolve(
            marker, topology.canonical_json_bytes(receipt), None,
        )
        accepted, executed = topology.validate_external_artifact_set(
            marker, rows=rows, foundation_context=_context(),
            sealed_custody=_custody(),
        )
        assert accepted == receipt
        assert executed == ()


def test_external_absence_uses_architecture_a_and_host_require_pass_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: portable absence is mislabeled PASS or host qualification accepts it."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    with _safe_fixture_root() as raw:
        root = Path(raw)
        evidence = root / "evidence"
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )

        appearance = root / "external-appearance/home/thenam176/nautilus"
        with topology._retained_external_authority(
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS",
            nautilus_base_root=appearance / "base",
            nautilus_artifact_root=appearance / "artifact",
        ) as session:
            assert (session.state, session.fact) == (
                "ABSENT", "AUTHORITY_ROOT_ABSENT",
            )
            assert len(session.descriptors) == 2
            assert all(
                stat.S_ISDIR(os.fstat(descriptor).st_mode)
                for descriptor in session.descriptors
            )
            topology._postcheck_external_authority(session)
            (root / "external-appearance").mkdir(mode=0o700)
            with pytest.raises(topology.TopologyError, match="changed"):
                topology._postcheck_external_authority(session)
            retained_descriptors = session.descriptors
        for descriptor in retained_descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)

        hosted_corpus = root / "phase-hosted-home/thenam176/.hermes/crypto-research"
        with topology._retained_external_authority(
            "EXT-PHASE3B-CORPUS",
            corpus_root=hosted_corpus,
        ) as session:
            assert (session.state, session.fact) == (
                "ABSENT", "AUTHORITY_ROOT_ABSENT",
            )
            assert len(session.descriptors) == 1
            topology._postcheck_external_authority(session)
            (root / "phase-hosted-home").mkdir(mode=0o700)
            with pytest.raises(topology.TopologyError, match="changed"):
                topology._postcheck_external_authority(session)
            phase_absent_descriptor = session.descriptors[0]
        with pytest.raises(OSError):
            os.fstat(phase_absent_descriptor)

        phase_blocked_target = root / "phase-blocked-target"
        phase_blocked_target.mkdir(mode=0o700)
        phase_blocked_symlink = root / "phase-blocked-symlink"
        phase_blocked_symlink.symlink_to(
            phase_blocked_target, target_is_directory=True,
        )
        phase_blocked_unsafe = root / "phase-blocked-unsafe"
        phase_blocked_unsafe.mkdir(mode=0o777)
        phase_blocked_unsafe.chmod(0o777)
        for blocked_corpus in (phase_blocked_symlink, phase_blocked_unsafe):
            with topology._retained_external_authority(
                "EXT-PHASE3B-CORPUS",
                corpus_root=blocked_corpus / "home/thenam176/.hermes/crypto-research",
            ) as session:
                assert (session.state, session.fact) == (
                    "INVALID", "AUTHORITY_INVALID",
                )

        legacy_root = root / "legacy-present"
        _complete_legacy(legacy_root)
        hosted_uv = root / "legacy-hosted-home/thenam176/.local/bin/uv"
        with topology._retained_external_authority(
            "EXT-LEGACY-UV-AUTHORITY",
            uv_path=hosted_uv,
            legacy_root=legacy_root,
        ) as session:
            assert (session.state, session.fact) == (
                "ABSENT", "AUTHORITY_EXECUTABLE_ABSENT",
            )
            assert len(session.descriptors) == 1
            topology._postcheck_external_authority(session)
            (root / "legacy-hosted-home").mkdir(mode=0o700)
            with pytest.raises(topology.TopologyError, match="changed"):
                topology._postcheck_external_authority(session)
            legacy_absent_descriptor = session.descriptors[0]
        with pytest.raises(OSError):
            os.fstat(legacy_absent_descriptor)

        blocked_target = root / "legacy-uv-blocked-target"
        blocked_target.mkdir(mode=0o700)
        blocked_symlink = root / "legacy-uv-blocked-symlink"
        blocked_symlink.symlink_to(blocked_target, target_is_directory=True)
        blocked_unsafe = root / "legacy-uv-blocked-unsafe"
        blocked_unsafe.mkdir(mode=0o777)
        blocked_unsafe.chmod(0o777)
        for blocked_uv in (blocked_symlink, blocked_unsafe):
            with topology._retained_external_authority(
                "EXT-LEGACY-UV-AUTHORITY",
                uv_path=blocked_uv / "home/thenam176/.local/bin/uv",
                legacy_root=legacy_root,
            ) as session:
                assert (session.state, session.fact) == (
                    "INVALID", "AUTHORITY_INVALID",
                )

        initial_symlink_target = root / "external-initial-symlink-target"
        initial_symlink_target.mkdir(mode=0o700)
        initial_symlink = root / "external-initial-symlink"
        initial_symlink.symlink_to(initial_symlink_target, target_is_directory=True)
        initial_unsafe = root / "external-initial-unsafe"
        initial_unsafe.mkdir(mode=0o777)
        initial_unsafe.chmod(0o777)
        for blocked in (initial_symlink, initial_unsafe):
            with topology._retained_external_authority(
                "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS",
                nautilus_base_root=blocked / "home/thenam176/base",
                nautilus_artifact_root=blocked / "home/thenam176/artifact",
            ) as session:
                assert (session.state, session.fact) == (
                    "INVALID", "AUTHORITY_INVALID",
                )

        replaceable = root / "external-replaceable"
        replaceable.mkdir(mode=0o700)
        replaceable_authority = replaceable / "home/thenam176/nautilus"
        with topology._retained_external_authority(
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS",
            nautilus_base_root=replaceable_authority / "base",
            nautilus_artifact_root=replaceable_authority / "artifact",
        ) as session:
            moved = root / "external-replaceable-moved"
            replaceable.rename(moved)
            replaceable.mkdir(mode=0o700)
            assert os.fstat(session.descriptors[0]).st_ino == moved.lstat().st_ino
            assert os.fstat(session.descriptors[0]).st_ino != replaceable.lstat().st_ino
            with pytest.raises(topology.TopologyError, match="changed"):
                topology._postcheck_external_authority(session)

        symlinked = root / "external-symlink/home/thenam176/nautilus"
        with topology._retained_external_authority(
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS",
            nautilus_base_root=symlinked / "base",
            nautilus_artifact_root=symlinked / "artifact",
        ) as session:
            assert len(session.descriptors) == 2
            symlink_target = root / "external-symlink-target"
            symlink_target.mkdir(mode=0o700)
            (root / "external-symlink").symlink_to(
                symlink_target, target_is_directory=True,
            )
            with pytest.raises(topology.TopologyError, match="changed"):
                topology._postcheck_external_authority(session)

        unsafe_prefix = root / "external-unsafe"
        unsafe_prefix.mkdir(mode=0o700)
        unsafe = unsafe_prefix / "home/thenam176/nautilus"
        with topology._retained_external_authority(
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS",
            nautilus_base_root=unsafe / "base",
            nautilus_artifact_root=unsafe / "artifact",
        ) as session:
            unsafe_prefix.chmod(0o777)
            with pytest.raises(topology.TopologyError, match="changed"):
                topology._postcheck_external_authority(session)
            unsafe_prefix.chmod(0o700)

        partial = root / "external-partial"
        partial.mkdir(mode=0o700)
        partial_base = partial / "base"
        partial_base.mkdir(mode=0o500)
        with topology._retained_external_authority(
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS",
            nautilus_base_root=partial_base,
            nautilus_artifact_root=partial / "artifact",
        ) as session:
            assert (session.state, session.fact) == (
                "PARTIAL", "AUTHORITY_PARTIAL",
            )

        @topology.contextmanager
        def absent_factory(code: str):
            with topology._retained_external_authority(
                code,
                corpus_root=root / "absent-corpus",
                uv_path=root / "absent-uv",
                legacy_root=root / "absent-legacy",
                nautilus_base_root=(
                    root / "hosted-home/thenam176/nautilus/base"
                ),
                nautilus_artifact_root=(
                    root / "hosted-home/thenam176/nautilus/artifact"
                ),
            ) as session:
                yield session

        invoked: list[tuple[str, ...]] = []
        publications = topology.run_lane(
            lane="external-authorities", inventory=INVENTORY,
            evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
            external_session_factory=absent_factory,
            exact_runner=lambda nodes, _report: invoked.append(nodes) or nodes,
        )
        assert len(publications) == 6
        assert invoked == []
        rows = topology._installed_inventory_rows(INVENTORY, evidence)
        context = topology.load_foundation_context(
            context_path, run_id=run_id, head_sha=head,
        )
        topology_root = evidence / "capability-topology"
        assert topology.validate_external_artifacts(
            topology_root, rows=rows, foundation_context=context,
            sealed_custody=_custody(), require_pass=False,
        ) == "DEFERRED"
        with pytest.raises(topology.TopologyError, match="requires PASS"):
            topology.validate_external_artifacts(
                topology_root, rows=rows, foundation_context=context,
                sealed_custody=_custody(), require_pass=True,
            )


@pytest.mark.parametrize("state", ("PARTIAL", "INVALID", "DRIFTED"))
def test_external_nonqualifying_state_publishes_strict_fail_and_stops_lane(
    monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    """Break caught: a present broken authority is downgraded to portable deferral."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with _safe_fixture_root() as raw:
        evidence = Path(raw) / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )

        @topology.contextmanager
        def invalid_factory(code: str):
            yield topology.ExternalAuthoritySession(
                code, state, "AUTHORITY_INVALID",
                topology._invalid_external_authority(code), (), lambda: None,
            )

        with pytest.raises(topology.TopologyError, match=f"preflight is {state}"):
            topology.run_lane(
                lane="external-authorities", inventory=INVENTORY,
                evidence_root=evidence, run_id=run_id, head_sha=head,
                foundation_context_path=context_path,
                external_session_factory=invalid_factory,
            )
        marker = evidence / "capability-topology/EXT-DISPOSABLE-PG-GREEN.json"
        receipt = topology.parse_receipt(marker.read_bytes())
        assert receipt["schema_version"] == topology.EXTERNAL_RECEIPT_SCHEMA
        assert receipt["outcome"] == "FAIL"
        assert receipt["preflight_state"] == state
        assert not (marker.with_suffix(".artifacts") / "governance.json").exists()


def test_validate_external_cli_carries_the_explicit_require_pass_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: the future host caller cannot distinguish DEFERRED from PASS."""
    observed: list[bool] = []
    monkeypatch.setattr(topology, "_active_foundation_identity", lambda: ("7", "a" * 40))
    monkeypatch.setattr(topology, "load_foundation_context", lambda *_a, **_k: _context())
    monkeypatch.setattr(topology, "_installed_inventory_rows", lambda *_a, **_k: ())
    monkeypatch.setattr(
        topology, "load_portable_root_baseline",
        lambda *_a, **_k: {"collector_policy": _custody()},
    )
    monkeypatch.setattr(
        topology, "validate_external_artifacts",
        lambda *_a, require_pass, **_k: observed.append(require_pass) or "PASS",
    )

    assert topology.main([
        "validate-external", "--require-pass",
        "--evidence-root", "/tmp/test-external-cli-evidence",
        "--foundation-context-path", "/tmp/test-external-cli-context.json",
    ]) == 0
    assert observed == [True]


def test_standalone_external_make_target_builds_custody_and_baseline_once() -> None:
    """Break caught: VALID external exact nodes cannot run from the standalone target."""
    source = Path("Makefile").read_text(encoding="utf-8")
    recipe = source.split("test-external-authorities:\n", 1)[1].split(
        "\n\ntest-portable-root-remainder:", 1,
    )[0]

    assert "package6-custodian-external-authorities" in recipe
    assert "PACKAGE6_FD_CUSTODY_EXTENSION_PATH" in recipe
    assert "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256" in recipe
    assert recipe.count("scripts.t_g03_capability_topology collect-baseline") == 1
    assert recipe.count(
        "scripts.t_g03_capability_topology run-lane --lane external-authorities",
    ) == 1
    assert recipe.index(" collect-baseline ") < recipe.index(" run-lane ")
