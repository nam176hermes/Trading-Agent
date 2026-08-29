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
MARKET = ROOT / "tests/fixtures/p1_nautilus/e2e/btcusdt-1m.jsonl"

EXPECTED_FIXTURES = {
    "engine_configuration_sha256": "38fa348e0422607052851028ed84b2478740d930ce09832dc5e42cbb86b78f60",
    "instrument_catalog_sha256": "22a6c061b06d0eef539509a5cfa4a1128843a80b1f48eb473a9b65126f74d822",
    "market_data_sha256": "d390750a1d51b6f333efc7092cd99f2c6752ca6ab51daeaa800171ea92005c9c",
    "strategy_configuration_sha256": "c4002efb2f0f2b14c94699db59ef8c5733602e41c3bfe60999670fb7c0671470",
}


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
        lambda: SimpleNamespace(authority_document_sha256="3" * 64),
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
