from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/run_p1_nautilus_vertical_slice.py"
HOST_AUTHORITY_BUILDER = ROOT / "scripts/build_p1_package6_host_authority.py"
MARKET = ROOT / "tests/fixtures/p1_nautilus/e2e/btcusdt-1m.jsonl"

EXPECTED_FIXTURES = {
    "engine_configuration_sha256": "38fa348e0422607052851028ed84b2478740d930ce09832dc5e42cbb86b78f60",
    "instrument_catalog_sha256": "22a6c061b06d0eef539509a5cfa4a1128843a80b1f48eb473a9b65126f74d822",
    "market_data_sha256": "d390750a1d51b6f333efc7092cd99f2c6752ca6ab51daeaa800171ea92005c9c",
    "strategy_configuration_sha256": "c4002efb2f0f2b14c94699db59ef8c5733602e41c3bfe60999670fb7c0671470",
}
_RUNTIME_NATIVE_ENV = (
    "P1_NAUTILUS_BASE_RUNTIME",
    "P1_NAUTILUS_ARTIFACT_DIRECTORY",
    "P1_NAUTILUS_SANDBOX",
    "P1_NAUTILUS_TRANSPORT_ROOT",
    "TRADING_PACKAGE6_APPROVAL_SHA256",
    "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH",
    "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH",
    "TRADING_PACKAGE6_STAGING_SCOPE",
)
_RUNTIME_POSTGRES_ENV = (
    "TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES",
    "TRADING_TEST_DISPOSABLE_APPROVAL_RECORD",
    "TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE",
    "TRADING_TEST_DISPOSABLE_FIXTURE_PLAN",
)
_PACKAGE6_CHILD_ENV = (
    "TRADING_PACKAGE6_APPROVAL_SHA256",
    "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH",
    "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH",
    "TRADING_PACKAGE6_STAGING_SCOPE",
)


def _initialize_git_repository(path: Path, marker: str) -> tuple[str, str]:
    path.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["/usr/bin/git", "-C", str(path), "config", "user.email", "p1@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["/usr/bin/git", "-C", str(path), "config", "user.name", "P1 test"],
        check=True,
    )
    (path / "marker.txt").write_text(marker, encoding="utf-8")
    subprocess.run(["/usr/bin/git", "-C", str(path), "add", "marker.txt"], check=True)
    subprocess.run(
        ["/usr/bin/git", "-C", str(path), "commit", "-q", "-m", marker],
        check=True,
    )
    commit = subprocess.check_output(
        ["/usr/bin/git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    tree = subprocess.check_output(
        ["/usr/bin/git", "-C", str(path), "rev-parse", "HEAD^{tree}"],
        text=True,
    ).strip()
    return commit, tree


def _complete_arguments(tmp_path: Path) -> list[str]:
    return [
        "--p1-closure-root",
        str(tmp_path / "closure"),
        "--p1-closure-artifacts",
        str(tmp_path / "artifacts"),
        "--bubblewrap",
        str(tmp_path / "bwrap"),
        "--transport-root",
        str(tmp_path / "transport"),
        "--postgres-approval",
        str(tmp_path / "approval.json"),
        "--postgres-scope",
        "DISPOSABLE_PG_GREEN",
        "--pgdata",
        str(tmp_path / "pgdata"),
        "--pg-port",
        "49152",
        "--engine-configuration",
        str(tmp_path / "engine-configuration.json"),
        "--instrument-catalog",
        str(tmp_path / "instrument-catalog.json"),
        "--strategy-configuration",
        str(tmp_path / "strategy-configuration.json"),
        "--market-data",
        str(tmp_path / "market-data.jsonl"),
    ]


def _patch_complete_native_preflight(
    monkeypatch: pytest.MonkeyPatch,
    vertical: object,
    *,
    closure_sha256: str,
) -> None:
    monkeypatch.setattr(
        vertical,
        "_source_identity",
        lambda: ("1" * 40, "2" * 40),
    )
    monkeypatch.setattr(vertical, "_validate_external_fixtures", lambda _args: None)
    monkeypatch.setattr(vertical, "_validate_sandbox_executable", lambda _path: None)
    monkeypatch.setattr(vertical, "_validate_transport_root", lambda _path: None)
    monkeypatch.setattr(vertical, "load_protected_approval_record", lambda _path: {})
    monkeypatch.setattr(
        vertical,
        "validate_disposable_postgres_approval_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        vertical,
        "validate_disposable_postgres_approval",
        lambda *_args: None,
    )
    monkeypatch.setattr(vertical, "validate_source_binding_files", lambda *_args: None)
    monkeypatch.setattr(
        vertical,
        "attest_worker_runtime_authority",
        lambda: SimpleNamespace(
            application_revision="1" * 40,
            authority_document_sha256="3" * 64,
            backend_revision="1" * 40,
            runtime_authority=SimpleNamespace(source_tree="2" * 40),
        ),
    )
    monkeypatch.setattr(
        vertical,
        "attest_p1_nautilus_closure",
        lambda _config: SimpleNamespace(closure_sha256=closure_sha256),
    )


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "python", str(SCRIPT), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _package6_vertical_slice_command(
    material: object, arguments: list[str]
) -> list[str]:
    application_python = getattr(material, "application_python")
    assert isinstance(application_python, Path)
    return [
        str(application_python),
        "-I",
        "-B",
        str(HOST_AUTHORITY_BUILDER),
        "activate-and-exec",
        "--",
        *arguments,
    ]


def _package6_vertical_slice_environment(
    source: dict[str, str],
) -> dict[str, str]:
    return {name: source[name] for name in _PACKAGE6_CHILD_ENV}


def test_required_runtime_command_uses_validated_package6_python() -> None:
    application_python = Path("/validated/package6/application/.venv/bin/python3.11")

    command = _package6_vertical_slice_command(
        SimpleNamespace(application_python=application_python), ["--execute"]
    )

    assert command == [
        str(application_python),
        "-I",
        "-B",
        str(HOST_AUTHORITY_BUILDER),
        "activate-and-exec",
        "--",
        "--execute",
    ]


def test_required_runtime_environment_strips_ambient_loader_controls() -> None:
    source = {
        name: f"accepted-{index}"
        for index, name in enumerate(_PACKAGE6_CHILD_ENV)
    }
    source.update(
        {
            "LD_AUDIT": "/poison/audit.so",
            "LD_LIBRARY_PATH": "/poison/library",
            "LD_PRELOAD": "/poison/preload.so",
            "PYTHONHOME": "/poison/python-home",
            "PYTHONPATH": "/poison/python-path",
            "UNRELATED_AMBIENT": "poison",
        }
    )

    child = _package6_vertical_slice_environment(source)

    assert child == {name: source[name] for name in _PACKAGE6_CHILD_ENV}


def test_internal_authority_injection_is_not_a_cli_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    injected = object.__new__(vertical.WorkerRuntimeAuthority)
    observed: list[object] = []
    monkeypatch.setattr(
        vertical,
        "_validate_complete",
        lambda _arguments, authority=None: (
            observed.append(authority)
            or ({"source_commit": "1" * 40}, authority)
        ),
    )

    result = vertical.main(_complete_arguments(tmp_path), worker_authority=injected)  # type: ignore[arg-type]

    assert result == 0
    assert observed == [injected]
    assert "worker-authority" not in vertical._parser().format_help()
    assert json.loads(capsys.readouterr().out)["status"] == "READY"


def test_internal_authority_injection_requires_exact_issued_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    calls: list[str] = []
    monkeypatch.setattr(
        vertical,
        "_validate_complete",
        lambda *_args, **_kwargs: calls.append("validated"),
    )

    result = vertical.main(_complete_arguments(tmp_path), worker_authority=object())  # type: ignore[arg-type]

    assert result == 2
    assert calls == []
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"


def test_absent_external_authority_is_canonical_deferred_without_job_mutation() -> None:
    result = _run()

    assert result.returncode == 0
    assert result.stderr == ""
    receipt = json.loads(result.stdout)
    assert receipt == {
        "authority_limits": {
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        },
        "evidence": {},
        "external_authority": {"native": "ABSENT", "postgres": "ABSENT"},
        "fixture_authority": {
            "account_id": "p1-btcusdt-fixture-account",
            **EXPECTED_FIXTURES,
            "liquidity_side": "TAKER",
            "opening_source": "p1-engine-configuration",
            "opening_source_revision": EXPECTED_FIXTURES[
                "engine_configuration_sha256"
            ],
            "other_money": "0",
            "reconciliation_source": "VENUE",
            "starting_cash": "1000000",
            "starting_currency": "USDT",
            "strategy_id": "p1-target-strategy-v1",
            "window": {
                "end": "2026-08-05T12:01:00Z",
                "start": "2026-08-05T12:00:00Z",
            },
        },
        "job_mutated": False,
        "reason": "EXTERNAL_AUTHORITY_ABSENT",
        "schema": "trading-agent-p1-nautilus-vertical-slice/v1",
        "status": "DEFERRED",
    }


def test_partial_external_authority_is_blocked_and_does_not_leak_paths() -> None:
    secret_path = "/tmp/private-p1-closure"
    result = _run("--p1-closure-root", secret_path)

    assert result.returncode == 2
    assert result.stderr == ""
    assert secret_path not in result.stdout
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason"] == "EXTERNAL_AUTHORITY_PARTIAL_OR_INVALID"
    assert receipt["job_mutated"] is False


def test_e2e_market_fixture_is_exact_canonical_two_row_authority() -> None:
    expected = (
        b'{"ask":"100","bid":"99","close":"100","event_time":"2026-08-05T12:00:00Z",'
        b'"high":"101","low":"98","open":"99","quote_time":"2026-08-05T12:00:00Z",'
        b'"sequence":1,"volume":"2"}\n'
        b'{"ask":"102","bid":"101","close":"102","event_time":"2026-08-05T12:01:00Z",'
        b'"high":"103","low":"100","open":"101","quote_time":"2026-08-05T12:01:00Z",'
        b'"sequence":2,"volume":"3"}\n'
    )

    assert MARKET.read_bytes() == expected


def test_network_credentials_are_not_an_operator_interface() -> None:
    marker = "must-not-appear"
    result = _run("--exchange-api-key", marker)

    assert result.returncode == 2
    assert marker not in result.stdout
    assert marker not in result.stderr


def test_invalid_authority_values_are_redacted() -> None:
    marker = "/tmp/private-invalid-value"
    result = _run("--postgres-scope", marker)

    assert result.returncode == 2
    assert marker not in result.stdout
    assert marker not in result.stderr


def test_execute_cannot_bypass_unpromoted_database_runtime_pin() -> None:
    result = _run("--execute")

    assert result.returncode == 2
    receipt = json.loads(result.stdout)
    assert receipt["reason"] == "EXTERNAL_AUTHORITY_PARTIAL_OR_INVALID"
    assert receipt["job_mutated"] is False


def test_transport_authority_rejects_checkout_and_symlink_paths(
    tmp_path: Path,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    assert hasattr(vertical, "_validate_transport_root")
    outside = tmp_path / "transport"
    outside.mkdir(mode=0o700)
    vertical._validate_transport_root(outside)

    link = tmp_path / "transport-link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(vertical.VerticalSliceError):
        vertical._validate_transport_root(link)
    with pytest.raises(vertical.VerticalSliceError):
        vertical._validate_transport_root(ROOT / "tests")


def test_source_identity_ignores_ambient_foreign_git_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    source = tmp_path / "source"
    expected = _initialize_git_repository(source, "accepted")
    foreign = tmp_path / "foreign"
    _initialize_git_repository(foreign, "foreign")
    monkeypatch.setattr(vertical, "ROOT", source)
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))

    assert vertical._source_identity() == expected


def test_source_identity_ignores_git_replace_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    source = tmp_path / "source"
    expected = _initialize_git_repository(source, "accepted")
    (source / "marker.txt").write_text("replacement", encoding="utf-8")
    subprocess.run(
        ["/usr/bin/git", "-C", str(source), "commit", "-q", "-am", "replacement"],
        check=True,
    )
    replacement = subprocess.check_output(
        ["/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    subprocess.run(
        ["/usr/bin/git", "-C", str(source), "checkout", "-q", "--detach", expected[0]],
        check=True,
    )
    subprocess.run(
        ["/usr/bin/git", "-C", str(source), "replace", expected[0], replacement],
        check=True,
    )
    monkeypatch.setattr(vertical, "ROOT", source)

    assert vertical._source_identity() == expected


def test_wrong_sandbox_executable_is_blocked_before_native_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    bad_sandbox = tmp_path / "bwrap"
    bad_sandbox.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    bad_sandbox.chmod(0o755)
    policy = tmp_path / "sealed-uv-exec-policy.json"
    policy.write_text(
        json.dumps(
            {
                "sandbox_gid": os.getegid(),
                "sandbox_mode": "0755",
                "sandbox_path": str(bad_sandbox),
                "sandbox_sha256": "0" * 64,
                "sandbox_uid": os.geteuid(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vertical, "SANDBOX_POLICY", policy)
    monkeypatch.setattr(
        vertical,
        "_source_identity",
        lambda: ("1" * 40, "2" * 40),
    )
    monkeypatch.setattr(vertical, "_validate_external_fixtures", lambda _args: None)
    monkeypatch.setattr(vertical, "_validate_transport_root", lambda _path: None)
    monkeypatch.setattr(vertical, "load_protected_approval_record", lambda _path: {})
    monkeypatch.setattr(
        vertical,
        "validate_disposable_postgres_approval_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        vertical,
        "validate_disposable_postgres_approval",
        lambda *_args: None,
    )
    monkeypatch.setattr(vertical, "validate_source_binding_files", lambda *_args: None)
    native_calls: list[str] = []
    monkeypatch.setattr(
        vertical,
        "attest_worker_runtime_authority",
        lambda: native_calls.append("worker")
        or SimpleNamespace(authority_document_sha256="3" * 64),
    )
    monkeypatch.setattr(
        vertical,
        "attest_p1_nautilus_closure",
        lambda _config: native_calls.append("closure")
        or SimpleNamespace(
            closure_sha256=P1_REAL_BACKTEST_POLICY.closure_sha256,
        ),
    )

    result = vertical.main(_complete_arguments(tmp_path))

    assert result == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "BLOCKED"
    assert receipt["job_mutated"] is False
    assert native_calls == []


def test_exact_sandbox_policy_identity_passes_without_executing_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    sandbox = tmp_path / "bwrap"
    sandbox.write_text("must not execute\n", encoding="utf-8")
    sandbox.chmod(0o755)
    policy = tmp_path / "sealed-uv-exec-policy.json"
    policy.write_text(
        json.dumps(
            {
                "sandbox_gid": os.getegid(),
                "sandbox_mode": "0755",
                "sandbox_path": str(sandbox),
                "sandbox_sha256": hashlib.sha256(sandbox.read_bytes()).hexdigest(),
                "sandbox_uid": os.geteuid(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vertical, "SANDBOX_POLICY", policy)

    vertical._validate_sandbox_executable(sandbox)


def test_postgres_authority_is_validated_before_native_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    monkeypatch.setattr(
        vertical,
        "_source_identity",
        lambda: ("1" * 40, "2" * 40),
    )
    monkeypatch.setattr(vertical, "_validate_external_fixtures", lambda _args: None)
    monkeypatch.setattr(vertical, "_validate_sandbox_executable", lambda _path: None)
    monkeypatch.setattr(vertical, "_validate_transport_root", lambda _path: None)
    monkeypatch.setattr(vertical, "load_protected_approval_record", lambda _path: {})
    monkeypatch.setattr(
        vertical,
        "validate_disposable_postgres_approval_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            vertical.VerticalSliceError("PostgreSQL authority rejected")
        ),
    )
    native_calls: list[str] = []
    monkeypatch.setattr(
        vertical,
        "attest_worker_runtime_authority",
        lambda: native_calls.append("worker"),
    )
    monkeypatch.setattr(
        vertical,
        "attest_p1_nautilus_closure",
        lambda _config: native_calls.append("closure"),
    )

    result = vertical.main(_complete_arguments(tmp_path))

    assert result == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "BLOCKED"
    assert receipt["job_mutated"] is False
    assert native_calls == []


def test_schema_valid_wrong_closure_digest_is_blocked_before_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    _patch_complete_native_preflight(
        monkeypatch,
        vertical,
        closure_sha256="a" * 64,
    )
    result = vertical.main(_complete_arguments(tmp_path))

    assert result == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "BLOCKED"
    assert receipt["job_mutated"] is False
    assert receipt["evidence"] == {}


def test_ambient_database_setting_is_blocked_before_downstream_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    _patch_complete_native_preflight(
        monkeypatch,
        vertical,
        closure_sha256=P1_REAL_BACKTEST_POLICY.closure_sha256,
    )
    monkeypatch.setattr(vertical, "_validate_transport_root", lambda _path: None)
    monkeypatch.setattr(vertical, "load_protected_approval_record", lambda _path: {})
    monkeypatch.setenv("TRADING_DATABASE_URL", "postgresql://ambient/not-authorized")
    observed_names: list[frozenset[str]] = []
    downstream_calls: list[str] = []

    def reject_ambient(
        _record: object,
        **kwargs: object,
    ) -> None:
        names = kwargs["runtime_setting_names"]
        assert isinstance(names, frozenset)
        observed_names.append(names)
        raise vertical.VerticalSliceError("ambient database authority is forbidden")

    monkeypatch.setattr(
        vertical,
        "validate_disposable_postgres_approval_record",
        reject_ambient,
    )
    monkeypatch.setattr(
        vertical,
        "validate_disposable_postgres_approval",
        lambda *_args: downstream_calls.append("specific-context"),
    )
    monkeypatch.setattr(
        vertical,
        "validate_source_binding_files",
        lambda *_args: downstream_calls.append("source-bindings"),
    )

    result = vertical.main(_complete_arguments(tmp_path))

    assert result == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "BLOCKED"
    assert receipt["job_mutated"] is False
    assert len(observed_names) == 1
    assert "TRADING_DATABASE_URL" in observed_names[0]
    assert downstream_calls == []


@pytest.mark.parametrize(
    "mutation",
    ("application_revision", "backend_revision", "source_tree"),
)
def test_worker_runtime_authority_must_bind_exact_checkout_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY

    _patch_complete_native_preflight(
        monkeypatch,
        vertical,
        closure_sha256=P1_REAL_BACKTEST_POLICY.closure_sha256,
    )
    authority = {
        "application_revision": "1" * 40,
        "authority_document_sha256": "3" * 64,
        "backend_revision": "1" * 40,
        "runtime_authority": SimpleNamespace(source_tree="2" * 40),
    }
    if mutation == "source_tree":
        authority["runtime_authority"] = SimpleNamespace(source_tree="9" * 40)
    else:
        authority[mutation] = "9" * 40
    monkeypatch.setattr(
        vertical,
        "attest_worker_runtime_authority",
        lambda: SimpleNamespace(**authority),
    )

    result = vertical.main(_complete_arguments(tmp_path))

    assert result == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason"] == "EXTERNAL_AUTHORITY_PARTIAL_OR_INVALID"
    assert receipt["job_mutated"] is False


def test_required_runtime_vertical_slice_reaches_exact_durable_success(
    tmp_path: Path,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical
    from packages.runtime_release.staging_v2 import load_staging_authority_material
    from tests.jobs._postgres import (
        POSTGRES_BIN,
        POSTGRES_EXECUTABLES,
        disposable_database,
        upgrade_to_head,
    )

    supplied = {
        name: os.environ.get(name, "")
        for name in (*_RUNTIME_NATIVE_ENV, *_RUNTIME_POSTGRES_ENV)
    }
    if not any(supplied.values()):
        pytest.skip("exact external P1 native/PostgreSQL authority is absent")
    assert all(supplied.values()), "external P1 runtime authority is partial"
    assert supplied["TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES"] == "YES"
    assert supplied["TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE"] == (
        "DISPOSABLE_PG_GREEN"
    )
    approval_path = Path(supplied["TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"])
    fixture_plan_path = Path(supplied["TRADING_TEST_DISPOSABLE_FIXTURE_PLAN"])
    assert approval_path.is_file() and fixture_plan_path.is_file()
    assert json.loads(approval_path.read_bytes())["scope"] == "DISPOSABLE_PG_GREEN"
    assert all((POSTGRES_BIN / name).is_file() for name in POSTGRES_EXECUTABLES)
    runtime_environment = dict(os.environ)
    material = load_staging_authority_material(runtime_environment)

    fixture_root = tmp_path / "vertical-slice-inputs"
    fixture_root.mkdir(mode=0o700)
    external_fixtures: dict[str, Path] = {}
    for name, source in vertical.CANONICAL_FIXTURES.items():
        destination = fixture_root / source.name
        destination.write_bytes(source.read_bytes())
        destination.chmod(0o400)
        external_fixtures[name] = destination

    operation_id = "p1-vertical-slice-v1"
    with disposable_database(operation_id=operation_id, planned=True) as owner:
        upgrade_to_head(owner)
        plan = json.loads(
            fixture_plan_path.read_bytes()
        )
        slots = [
            slot
            for slot in plan["slots"]
            if slot["test_path"] == "tests/p1_nautilus/test_vertical_slice_e2e.py"
            and slot["operation_id"] == operation_id
            and slot["port"] == owner.port
        ]
        assert len(slots) == 1
        slot = slots[0]
        arguments = [
            "--execute",
            "--p1-closure-root",
            supplied["P1_NAUTILUS_BASE_RUNTIME"],
            "--p1-closure-artifacts",
            supplied["P1_NAUTILUS_ARTIFACT_DIRECTORY"],
            "--bubblewrap",
            supplied["P1_NAUTILUS_SANDBOX"],
            "--transport-root",
            supplied["P1_NAUTILUS_TRANSPORT_ROOT"],
            "--postgres-approval",
            supplied["TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"],
            "--postgres-scope",
            supplied["TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE"],
            "--pgdata",
            slot["pgdata"],
            "--pg-port",
            str(owner.port),
            *[
                value
                for name, path in external_fixtures.items()
                for value in (f"--{name.replace('_', '-')}", str(path))
            ],
        ]
        command = _package6_vertical_slice_command(material, arguments)
        assert command[:7] == [
            str(material.application_python),
            "-I",
            "-B",
            str(HOST_AUTHORITY_BUILDER),
            "activate-and-exec",
            "--",
            "--execute",
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=_package6_vertical_slice_environment(runtime_environment),
            check=False,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 0, "validated Package6 vertical slice failed"
    receipt_lines = result.stdout.splitlines()
    assert len(receipt_lines) == 1
    receipt = json.loads(receipt_lines[0])
    assert receipt["status"] == "PASS"
    assert receipt["reason"] == "P1_VERTICAL_SLICE_COMPLETED"
    assert receipt["job_mutated"] is True
    assert receipt["authority_limits"] == {
        "live_authorized": False,
        "network_trading_authorized": False,
        "production_authorized": False,
    }
    evidence = receipt["evidence"]
    assert evidence["worker_run_count"] == 1
    assert evidence["final_job_state"] == "SUCCEEDED"
    assert evidence["result_sha256"] == evidence["batch_sha256"]
    assert len(evidence["engine_request_sha256"]) == 64
    assert evidence["engine_request_sha256"] == evidence[
        "engine_request_sha256"
    ].lower()
    assert len(evidence["engine_event_receipt_sha256"]) == 64
    assert len(evidence["p1_portfolio_parity_sha256"]) == 64
    assert len(evidence["final_portfolio_state_hash"]) == 64
