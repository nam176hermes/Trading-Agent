from __future__ import annotations

from pathlib import Path
import shlex

import pytest


ROOT = Path(__file__).resolve().parents[2]
UNIT_DIR = ROOT / "ops" / "systemd"
APP_COMMIT = "fdc085a05019d700ccbce59370941e2c97ef899a"
BACKEND_COMMIT = "41f055b48033714c660f44cc20498b7545366e75"
APP_ROOT = f"/opt/trading-agent-phase4/releases/app-{APP_COMMIT}"
BACKEND_ROOT = f"/opt/trading-agent-phase4/releases/backend-{BACKEND_COMMIT}"
PYTHON = f"{APP_ROOT}/.venv/bin/python3.11"
SAFETY_RUNTIME = "/run/user/1000/trading-agent"
SERVICE_NAMES = (
    "trading-safety-state-export.service",
    "trading-control-api.service",
    "trading-job-api.service",
    "trading-job-worker.service",
    "trading-job-scheduler.service",
    "trading-semantic-input-refresh.service",
)
USER_SERVICE_NAMES = SERVICE_NAMES[:-1]
ENV_EXAMPLE_NAMES = (
    "job-api.env.example",
    "job-scheduler.env.example",
    "job-worker.env.example",
)
SAFE_ENVIRONMENT = {
    "TRADING_MODE": "paper",
    "LIVE_EXECUTION_ENABLED": "false",
    "LIVE_TRADING_APPROVED": "false",
}


def _text(name: str) -> str:
    return (UNIT_DIR / name).read_text(encoding="utf-8")


def _directives(name: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for raw_line in _text(name).splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "[")):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        values.setdefault(key, []).append(value)
    return values


def _systemd_words(values: list[str]) -> list[str]:
    words: list[str] = []
    for value in values:
        # Tracked units need no C-style escapes, continuations, or systemd
        # specifiers. Reject them instead of approximating expansion semantics.
        assert "\\" not in value, "systemd backslash syntax is forbidden"
        assert "%" not in value, "systemd specifier syntax is forbidden"
        try:
            words.extend(shlex.split(value, comments=False, posix=True))
        except ValueError as error:
            raise AssertionError("invalid systemd word quoting") from error
    return words


def _assert_exact_safe_environment(directives: dict[str, list[str]]) -> None:
    environment_values = directives.get("Environment", [])
    assert environment_values
    assert all(value.strip() for value in environment_values), (
        "Environment= reset is forbidden"
    )
    assignments = _systemd_words(environment_values)
    assert all(
        assignment.partition("=")[0] and "=" in assignment
        for assignment in assignments
    )
    observed = [
        assignment
        for assignment in assignments
        if assignment.partition("=")[0] in SAFE_ENVIRONMENT
    ]
    expected = [f"{key}={value}" for key, value in SAFE_ENVIRONMENT.items()]
    assert sorted(observed) == sorted(expected)
    for unset in _systemd_words(directives.get("UnsetEnvironment", [])):
        assert unset.partition("=")[0] not in SAFE_ENVIRONMENT


def _safe_systemd_environment() -> dict[str, list[str]]:
    return {
        "Environment": [
            f"{key}={value}" for key, value in SAFE_ENVIRONMENT.items()
        ]
    }


def test_safe_environment_validator_parses_all_systemd_tokens() -> None:
    directives = {
        "Environment": [
            'OTHER=value "TRADING_MODE=paper" LIVE_EXECUTION_ENABLED=false',
            'LIVE_TRADING_APPROVED=false "DISPLAY_NAME=paper worker"',
        ]
    }

    _assert_exact_safe_environment(directives)


@pytest.mark.parametrize(
    ("directive", "value"),
    [
        ("Environment", '"TRADING_MODE=invalid"'),
        ("Environment", ""),
        ("UnsetEnvironment", "TRADING_MODE"),
        ("UnsetEnvironment", '"LIVE_EXECUTION_ENABLED=false"'),
        ("UnsetEnvironment", "OTHER=value LIVE_TRADING_APPROVED"),
    ],
)
def test_safe_environment_validator_rejects_reset_override_and_unset(
    directive: str, value: str,
) -> None:
    directives = _safe_systemd_environment()
    directives.setdefault(directive, []).append(value)

    with pytest.raises(AssertionError):
        _assert_exact_safe_environment(directives)


@pytest.mark.parametrize(
    "line",
    [
        'Environment = "TRADING_MODE=live"',
        "UnsetEnvironment = TRADING_MODE",
        "Environment =",
    ],
)
def test_safe_environment_validator_rejects_whitespace_key_bypasses(
    monkeypatch: pytest.MonkeyPatch, line: str,
) -> None:
    source = "\n".join(
        [
            "Environment=TRADING_MODE=paper",
            "Environment=LIVE_EXECUTION_ENABLED=false",
            "Environment=LIVE_TRADING_APPROVED=false",
            line,
        ]
    )
    monkeypatch.setitem(globals(), "_text", lambda _name: source)

    with pytest.raises(AssertionError):
        _assert_exact_safe_environment(_directives("fixture.service"))


@pytest.mark.parametrize(
    "value",
    [
        r"TRADING_\x4dODE=live",
        "OTHER=value\\" + "\ncontinued",
    ],
    ids=("c-escape", "line-continuation"),
)
def test_safe_environment_validator_rejects_backslash_syntax(value: str) -> None:
    directives = _safe_systemd_environment()
    directives["Environment"].append(value)

    with pytest.raises(AssertionError):
        _assert_exact_safe_environment(directives)


@pytest.mark.parametrize(
    ("directive", "value"),
    [
        ("Environment", "TRADING_%iMODE=live"),
        ("UnsetEnvironment", "TRADING_%iMODE"),
    ],
)
def test_safe_environment_validator_rejects_systemd_specifiers(
    directive: str, value: str,
) -> None:
    directives = _safe_systemd_environment()
    directives.setdefault(directive, []).append(value)

    with pytest.raises(AssertionError):
        _assert_exact_safe_environment(directives)


def test_all_services_use_exact_frozen_app_release_and_fixed_modules() -> None:
    expected = {
        "trading-safety-state-export.service": (
            "services.safety_state_exporter.main", "--once"
        ),
        "trading-job-api.service": ("apps.job_api.main", ""),
        "trading-control-api.service": ("control_api.main", ""),
        "trading-job-worker.service": ("services.job_worker.main", ""),
        "trading-job-scheduler.service": ("services.job_scheduler.main", ""),
        "trading-semantic-input-refresh.service": (
            "services.semantic_input_refresher.main", "--apply"
        ),
    }
    forbidden = (
        "/current", "/usr/bin/python", "/bin/sh", "/bin/bash", "shell=True",
        "run_status.json", "trading-agent.service", "trading-dashboard.service",
    )
    assert set(expected) == set(SERVICE_NAMES)
    assert {path.name for path in UNIT_DIR.glob("*.service")} == set(SERVICE_NAMES)
    for name, (module, suffix) in expected.items():
        text = _text(name)
        directives = _directives(name)
        command = f"{PYTHON} -m {module}" + (f" {suffix}" if suffix else "")
        assert directives["WorkingDirectory"] == [APP_ROOT]
        assert directives["ExecStart"] == [command]
        assert any(value == f"PYTHONPATH={APP_ROOT}" or value.startswith(
            f"PYTHONPATH={APP_ROOT}:"
        ) for value in directives["Environment"])
        assert all(token not in text for token in forbidden)


def test_job_api_is_loopback_only() -> None:
    api = _directives("trading-job-api.service")
    assert "TRADING_JOB_API_HOST=127.0.0.1" in api["Environment"]
    assert "TRADING_JOB_API_PORT=8401" in api["Environment"]


def test_all_six_services_fix_the_exact_safe_environment() -> None:
    for name in SERVICE_NAMES:
        _assert_exact_safe_environment(_directives(name))


def test_all_three_env_examples_fix_the_exact_safe_environment() -> None:
    assert {path.name for path in UNIT_DIR.glob("*.env.example")} == set(
        ENV_EXAMPLE_NAMES
    )
    for name in ENV_EXAMPLE_NAMES:
        assignments = [
            line
            for raw_line in _text(name).splitlines()
            if (line := raw_line.strip()) and not line.startswith("#")
        ]
        _assert_exact_safe_environment({"Environment": assignments})


def test_job_env_examples_match_role_split_and_reject_code_owned_overrides() -> None:
    api = _text("job-api.env.example")
    worker = _text("job-worker.env.example")

    assert "TRADING_JOB_API_EXPECTED_REVISION=0005_job_plane_role_split" in api
    assert "TRADING_CODE_COMMIT" not in worker
    assert "TRADING_WORKER_LEASE_SECONDS" not in worker


def test_control_api_is_loopback_postgres_only_and_reads_current_protected_safety() -> None:
    control = _directives("trading-control-api.service")
    assert control["EnvironmentFile"] == ["/etc/trading-agent/control-api.env"]
    assert control["ExecStart"] == [f"{PYTHON} -m control_api.main"]
    assert control["Wants"] == ["network.target trading-safety-state-export.timer"]
    assert control["Requires"] == ["trading-safety-state-export.service"]
    assert control["ProtectHome"] == ["tmpfs"]
    assert control["BindReadOnlyPaths"] == [
        "/etc/trading-agent/phase4-runtime-authority.json",
        SAFETY_RUNTIME,
    ]
    assert control["RestrictAddressFamilies"] == ["AF_UNIX AF_INET"]


def test_exporter_uses_private_runtime_directory_and_only_exact_safety_files() -> None:
    exporter = _directives("trading-safety-state-export.service")
    text = _text("trading-safety-state-export.service")
    assert exporter["Type"] == ["oneshot"]
    assert exporter["RuntimeDirectory"] == ["trading-agent", "trading-agent/safety-sources"]
    assert exporter["RuntimeDirectoryMode"] == ["0700"]
    assert exporter["RuntimeDirectoryPreserve"] == ["yes"]
    assert exporter["ProtectHome"] == ["tmpfs"]
    assert exporter["BindReadOnlyPaths"] == [
        f"/home/thenam176/.hermes/crypto-research/.mode:{SAFETY_RUNTIME}/safety-sources/.mode",
        f"-/home/thenam176/.hermes/crypto-research/.kill_switch:{SAFETY_RUNTIME}/safety-sources/.kill_switch",
    ]
    assert "/home/thenam176/.hermes/crypto-research:" not in text
    assert ".env" not in text and ".keys.enc" not in text


def test_worker_binds_safety_directory_and_only_approved_runtime_roots() -> None:
    worker = _directives("trading-job-worker.service")
    text = _text("trading-job-worker.service")
    assert worker["After"] == [
        "network.target trading-job-api.service trading-safety-state-export.service"
    ]
    assert worker["Requires"] == ["trading-safety-state-export.service"]
    assert worker["ProtectHome"] == ["tmpfs"]
    assert worker["BindReadOnlyPaths"] == [
        APP_ROOT,
        BACKEND_ROOT,
        "/opt/trading-agent-phase4/manifests",
        "/etc/trading-agent/phase4-runtime-authority.json",
        "/etc/trading-agent/research-input-manifests",
        "/home/thenam176/.local/share/trading-agent/research-input",
        SAFETY_RUNTIME,
    ]
    assert worker["BindPaths"] == [
        "/home/thenam176/.local/share/trading-agent/job-artifacts",
        "/home/thenam176/.local/share/trading-agent/research-output/reports",
        "/home/thenam176/.local/share/trading-agent/research-output/signals",
        "/home/thenam176/.local/run/trading-agent/research-home",
    ]
    assert f"{SAFETY_RUNTIME}/safety-state.json" not in worker["BindReadOnlyPaths"]
    assert "/home/thenam176/.hermes" not in text
    assert "/.env" not in text and ".keys.enc" not in text


def test_semantic_refresher_is_root_oneshot_with_two_source_directories_only() -> None:
    service = _directives("trading-semantic-input-refresh.service")
    text = _text("trading-semantic-input-refresh.service")
    assert service["Type"] == ["oneshot"]
    assert service["ExecStart"] == [
        f"{PYTHON} -m services.semantic_input_refresher.main --apply"
    ]
    assert service["ProtectHome"] == ["tmpfs"]
    assert service["BindReadOnlyPaths"] == [
        "/home/thenam176/.hermes/crypto-research/reports",
        "/home/thenam176/.hermes/crypto-research/memory/macro",
    ]
    assert service["BindPaths"] == [
        "/home/thenam176/.local/share/trading-agent/research-input",
        "/etc/trading-agent/research-input-manifests",
    ]
    assert service["RestrictAddressFamilies"] == ["AF_UNIX"]
    assert service["CapabilityBoundingSet"] == [
        "CAP_CHOWN CAP_FOWNER CAP_DAC_READ_SEARCH"
    ]
    assert service["AmbientCapabilities"] == [
        "CAP_CHOWN CAP_FOWNER CAP_DAC_READ_SEARCH"
    ]
    assert "/home/thenam176/.hermes/crypto-research:" not in text
    assert ".env" not in text and ".keys.enc" not in text


def test_hardening_and_minimal_address_families_are_explicit() -> None:
    expected = {
        "UMask": "0077", "NoNewPrivileges": "true", "PrivateTmp": "true",
        "PrivateDevices": "true", "ProtectSystem": "strict",
        "ProtectKernelTunables": "true", "ProtectKernelModules": "true",
        "ProtectControlGroups": "true", "RestrictNamespaces": "true",
        "RestrictSUIDSGID": "true", "RestrictRealtime": "true",
        "LockPersonality": "true", "SystemCallArchitectures": "native",
        "CapabilityBoundingSet": "", "AmbientCapabilities": "",
    }
    for name in USER_SERVICE_NAMES:
        directives = _directives(name)
        for key, value in expected.items():
            assert directives[key] == [value]
    semantic = _directives("trading-semantic-input-refresh.service")
    for key, value in expected.items():
        if key not in {"CapabilityBoundingSet", "AmbientCapabilities"}:
            assert semantic[key] == [value]
    assert _directives("trading-job-api.service")["RestrictAddressFamilies"] == ["AF_UNIX AF_INET"]
    assert _directives("trading-control-api.service")["RestrictAddressFamilies"] == ["AF_UNIX AF_INET"]
    assert _directives("trading-job-scheduler.service")["RestrictAddressFamilies"] == ["AF_UNIX AF_INET"]
    assert _directives("trading-job-worker.service")["RestrictAddressFamilies"] == ["AF_UNIX AF_INET AF_INET6"]


def test_timers_are_nonpersistent_and_do_not_enable_themselves() -> None:
    safety = _directives("trading-safety-state-export.timer")
    scheduler = _directives("trading-job-scheduler.timer")
    semantic = _directives("trading-semantic-input-refresh.timer")
    assert safety["OnUnitActiveSec"] == ["2s"]
    assert safety["Persistent"] == ["false"]
    assert scheduler["OnCalendar"] == ["*-*-* *:*:00"]
    assert scheduler["Persistent"] == ["false"]
    assert semantic["OnUnitActiveSec"] == ["10min"]
    assert semantic["Persistent"] == ["false"]
    for name in (
        "trading-safety-state-export.timer", "trading-job-scheduler.timer",
        "trading-semantic-input-refresh.timer",
    ):
        assert "enable" not in _text(name).lower()


def test_user_services_use_uid_owned_env_files_but_root_authority_is_not_an_env() -> None:
    for name, env_name in (
        ("trading-control-api.service", "control-api.env"),
        ("trading-job-api.service", "job-api.env"),
        ("trading-job-worker.service", "job-worker.env"),
        ("trading-job-scheduler.service", "job-scheduler.env"),
    ):
        directives = _directives(name)
        assert directives["EnvironmentFile"] == [f"/etc/trading-agent/{env_name}"]
        assert all("phase4-runtime-authority" not in value for value in directives["EnvironmentFile"])
