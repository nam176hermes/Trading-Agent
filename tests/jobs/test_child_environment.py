from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

import pytest

from packages.runtime_release import RuntimeAuthorityV2, RuntimePathsV2
from services.job_worker.environment import (
    APPROVED_DATA_ROOT,
    FIXED_PATH,
    EnvironmentValidationError,
    ResearchEnvironmentSettings,
    build_child_environment,
)
import services.job_worker.environment as environment_module


def test_research_data_root_is_dedicated_and_not_the_active_legacy_tree():
    assert APPROVED_DATA_ROOT == Path(
        "/home/thenam176/.local/share/trading-agent/research-input"
    )
    assert APPROVED_DATA_ROOT != Path("/home/thenam176/.hermes/crypto-research")


@pytest.fixture
def tmp_path():
    path = Path(tempfile.mkdtemp(prefix="task7-environment-", dir="/home/thenam176/.cache"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _settings(tmp_path: Path, monkeypatch, source=None):
    roots = {
        "data_root": tmp_path / "data",
        "reports_dir": tmp_path / "output/reports",
        "signal_output_dir": tmp_path / "output/signals",
        "scratch_home": tmp_path / "scratch/home",
    }
    tmp_path.chmod(0o700)
    for path in roots.values():
        path.mkdir(parents=True, mode=0o700)
        path.chmod(0o700)
    # Path.mkdir(parents=True) applies its mode only to the leaf. Lock the
    # shared fixture ancestors explicitly so ambient umask cannot make them
    # group-writable and trigger the production fail-closed policy.
    for ancestor in (tmp_path / "output", tmp_path / "scratch"):
        ancestor.chmod(0o700)
    roots["data_root"].chmod(0o711)
    # The real semantic tree is root-owned.  This fixture cannot chown, so it
    # changes only the expected authority UID while exercising the same exact
    # owner/mode checks used at runtime.
    monkeypatch.setattr(environment_module, "SEMANTIC_ROOT_OWNER_UID", roots["data_root"].stat().st_uid)
    for name, path in roots.items():
        monkeypatch.setattr(f"services.job_worker.environment.APPROVED_{name.upper()}", path)
    return ResearchEnvironmentSettings.from_source(source or {}), roots


def test_child_environment_is_fixed_empty_start_and_dedicated_names_only(tmp_path, monkeypatch):
    source = {
        "PATH": "/tmp/attacker",
        "HOME": "/home/attacker",
        "OPENAI_API_KEY": "generic-must-not-leak",
        "DEEPSEEK_API_KEY": "generic-must-not-leak",
        "TRADING_RESEARCH_OPENAI_API_KEY": "dedicated-research-only",
        "TRADING_JOB_API_TOKEN": "must-not-leak",
        "DATABASE_URL": "must-not-leak",
        "BINANCE_API_KEY": "must-not-leak",
        "UNRELATED": "must-not-leak",
    }
    settings, roots = _settings(tmp_path, monkeypatch, source)
    child = build_child_environment(settings)
    assert child == {
        "PATH": FIXED_PATH,
        "HOME": str(roots["scratch_home"]),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "TRADING_DATA_ROOT": str(roots["data_root"]),
        "TRADING_REPORTS_DIR": str(roots["reports_dir"]),
        "TRADING_SIGNAL_OUTPUT_DIR": str(roots["signal_output_dir"]),
        "TRADING_RESEARCH_OPENAI_API_KEY": "dedicated-research-only",
        "TRADING_MODE": "paper",
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
        "LIVE_TRADING_ENABLED": "false",
    }
    assert not ({"OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DATABASE_URL", "BINANCE_API_KEY"} & child.keys())


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "KRAKEN_API_KEY",
        "KRAKEN_PRIVATE_KEY",
        "ALPACA_API_KEY",
        "APCA_API_SECRET_KEY",
        "BROKER_API_KEY",
        "BROKER_ACCESS_TOKEN",
        "WITHDRAWAL_API_KEY",
        "WITHDRAWAL_PRIVATE_KEY",
        "TRADING_DASHBOARD_SERVICE_TOKEN",
        "DASHBOARD_SERVICE_TOKEN",
        "TRADING_JOB_API_TOKEN",
        "DATABASE_URL",
        "DATABASE_OWNER_URL",
        "POSTGRES_PASSWORD",
    ],
)
def test_paper_child_strips_trading_and_privileged_credentials(
    tmp_path,
    monkeypatch,
    forbidden_key,
):
    settings, _ = _settings(tmp_path, monkeypatch, {forbidden_key: "must-not-leak"})

    child = build_child_environment(settings)

    assert forbidden_key not in child
    assert "must-not-leak" not in child.values()
    assert child["TRADING_MODE"] == "paper"
    assert child["LIVE_EXECUTION_ENABLED"] == "false"
    assert child["LIVE_TRADING_APPROVED"] == "false"
    assert child["LIVE_TRADING_ENABLED"] == "false"


def test_v2_child_roots_come_only_from_protected_runtime_authority(
    tmp_path, monkeypatch,
) -> None:
    _, roots = _settings(tmp_path, monkeypatch)
    authority = object.__new__(RuntimeAuthorityV2)
    object.__setattr__(
        authority,
        "runtime_paths",
        RuntimePathsV2(
            safety_snapshot=tmp_path / "safety-state.json",
            semantic_authority=tmp_path / "semantic-active.json",
            semantic_input_root=roots["data_root"],
            reports_root=roots["reports_dir"],
            signals_root=roots["signal_output_dir"],
            scratch_root=roots["scratch_home"],
            artifact_root=tmp_path / "artifacts",
        ),
    )

    settings = ResearchEnvironmentSettings.from_authority(authority, {})
    child = build_child_environment(settings)

    assert child["TRADING_DATA_ROOT"] == str(roots["data_root"])
    assert child["TRADING_REPORTS_DIR"] == str(roots["reports_dir"])
    assert child["TRADING_SIGNAL_OUTPUT_DIR"] == str(roots["signal_output_dir"])


def test_validated_settings_repr_never_exposes_research_credential_values(
    tmp_path, monkeypatch,
):
    sensitive_value = "credential-value-must-never-render"
    settings, _ = _settings(
        tmp_path,
        monkeypatch,
        {
            "TRADING_RESEARCH_OPENAI_API_KEY": sensitive_value,
            "TRADING_RESEARCH_ANTHROPIC_API_KEY": "another-secret-value",
        },
    )

    rendered = repr(settings)

    assert sensitive_value not in rendered
    assert "another-secret-value" not in rendered
    assert rendered == "ResearchEnvironmentSettings(validated=True, credentials=2)"


@pytest.mark.parametrize("key", [
    "TRADING_DATA_ROOT", "TRADING_REPORTS_DIR", "TRADING_SIGNAL_OUTPUT_DIR",
    "TRADING_JOB_ID", "TRADING_JOB_ATTEMPT_ID", "TRADING_ATTEMPT_ID",
    "TRADING_RESEARCH_BACKEND_COMMIT", "TRADING_RESEARCH_SCRATCHPAD_ROOT",
])
def test_path_and_root_overrides_are_rejected(tmp_path, monkeypatch, key):
    with pytest.raises(EnvironmentValidationError) as raised:
        _settings(tmp_path, monkeypatch, {key: "/tmp/attacker/../escape"})
    assert raised.value.reason_code == "ENVIRONMENT_PATH_OVERRIDE_FORBIDDEN"


@pytest.mark.parametrize("root_name", ["data_root", "reports_dir", "signal_output_dir", "scratch_home"])
def test_all_roots_reject_symlinks(tmp_path, monkeypatch, root_name):
    settings, roots = _settings(tmp_path, monkeypatch)
    assert settings
    path = roots[root_name]
    real = path.with_name(path.name + "-real")
    path.rename(real)
    path.symlink_to(real, target_is_directory=True)
    with pytest.raises(EnvironmentValidationError) as raised:
        ResearchEnvironmentSettings.from_source({})
    assert raised.value.reason_code == "ENVIRONMENT_ROOT_SYMLINK"


def test_semantic_root_requires_root_owned_nonwritable_traversable_policy(tmp_path, monkeypatch):
    settings, roots = _settings(tmp_path, monkeypatch)
    assert settings
    assert roots["data_root"].stat().st_mode & 0o777 == 0o711

    for unsafe_mode in (0o700, 0o710, 0o755, 0o733, 0o777):
        roots["data_root"].chmod(unsafe_mode)
        with pytest.raises(EnvironmentValidationError) as raised:
            ResearchEnvironmentSettings.from_source({})
        assert raised.value.reason_code == "ENVIRONMENT_ROOT_MODE_UNSAFE"
    roots["data_root"].chmod(0o711)

    monkeypatch.setattr(environment_module, "SEMANTIC_ROOT_OWNER_UID", roots["data_root"].stat().st_uid + 1)
    with pytest.raises(EnvironmentValidationError) as raised:
        ResearchEnvironmentSettings.from_source({})
    assert raised.value.reason_code == "ENVIRONMENT_ROOT_OWNER_UNSAFE"


@pytest.mark.parametrize("unsafe_mode", [0o770, 0o777, 0o1700])
def test_all_roots_reject_writable_or_special_ancestors(
    tmp_path, monkeypatch, unsafe_mode,
):
    settings, _ = _settings(tmp_path, monkeypatch)
    assert settings
    tmp_path.chmod(unsafe_mode)
    with pytest.raises(EnvironmentValidationError) as raised:
        ResearchEnvironmentSettings.from_source({})
    assert raised.value.reason_code == "ENVIRONMENT_ROOT_ANCESTOR_UNSAFE"


def test_all_roots_reject_ancestor_outside_root_or_runtime_owners(tmp_path, monkeypatch):
    settings, _ = _settings(tmp_path, monkeypatch)
    assert settings
    monkeypatch.setattr(
        environment_module.os,
        "geteuid",
        lambda: tmp_path.stat().st_uid + 1,
    )
    with pytest.raises(EnvironmentValidationError) as raised:
        ResearchEnvironmentSettings.from_source({})
    assert raised.value.reason_code == "ENVIRONMENT_ROOT_ANCESTOR_UNSAFE"


def test_world_writable_tmp_ancestor_is_rejected(monkeypatch):
    unsafe_root = Path(tempfile.mkdtemp(prefix="phase4b-unsafe-", dir="/tmp"))
    try:
        with pytest.raises(EnvironmentValidationError) as raised:
            _settings(unsafe_root, monkeypatch)
        assert raised.value.reason_code == "ENVIRONMENT_ROOT_ANCESTOR_UNSAFE"
    finally:
        shutil.rmtree(unsafe_root, ignore_errors=True)


@pytest.mark.parametrize("root_name", ["reports_dir", "signal_output_dir", "scratch_home"])
def test_mutable_roots_still_require_runtime_owner_and_mode_0700(tmp_path, monkeypatch, root_name):
    settings, roots = _settings(tmp_path, monkeypatch)
    assert settings
    roots[root_name].chmod(0o711)
    with pytest.raises(EnvironmentValidationError) as raised:
        ResearchEnvironmentSettings.from_source({})
    assert raised.value.reason_code == "ENVIRONMENT_ROOT_MODE_UNSAFE"


def test_child_can_only_see_bound_semantic_tree_and_exact_output_roots(tmp_path, monkeypatch):
    settings, roots = _settings(tmp_path, monkeypatch, {
        "LEGACY_ROOT": "/home/thenam176/.hermes/crypto-research",
        "TRADING_JOB_API_TOKEN": "must-not-leak",
        "DATABASE_URL": "must-not-leak",
    })
    child = build_child_environment(settings)
    assert child["TRADING_DATA_ROOT"] == str(roots["data_root"])
    assert child["TRADING_REPORTS_DIR"] == str(roots["reports_dir"])
    assert child["TRADING_SIGNAL_OUTPUT_DIR"] == str(roots["signal_output_dir"])
    assert "/home/thenam176/.hermes/crypto-research" not in child.values()
    assert not ({"LEGACY_ROOT", "TRADING_JOB_API_TOKEN", "DATABASE_URL"} & child.keys())


def test_build_requires_validated_settings_not_a_source_mapping():
    with pytest.raises(TypeError):
        build_child_environment({"PATH": "/tmp"})


def test_settings_are_opaque_and_forged_instances_are_rejected():
    with pytest.raises(TypeError):
        ResearchEnvironmentSettings(Path("/tmp"), Path("/tmp"), Path("/tmp"), Path("/tmp"), (("OPENAI_API_KEY", "stolen"),))
    forged = ResearchEnvironmentSettings()
    with pytest.raises((TypeError, EnvironmentValidationError)):
        build_child_environment(forged)


def test_build_revalidates_exact_roots(tmp_path, monkeypatch):
    settings, roots = _settings(tmp_path, monkeypatch, {"TRADING_RESEARCH_OPENAI_API_KEY": "ok"})
    roots["reports_dir"].chmod(0o755)
    with pytest.raises(EnvironmentValidationError) as raised:
        build_child_environment(settings)
    assert raised.value.reason_code == "ENVIRONMENT_ROOT_MODE_UNSAFE"


def test_build_rejects_mutated_credential_authority(tmp_path, monkeypatch):
    settings, _ = _settings(tmp_path, monkeypatch, {"TRADING_RESEARCH_OPENAI_API_KEY": "ok"})
    object.__setattr__(settings, "_credentials", (("OPENAI_API_KEY", "forged"),))
    with pytest.raises(EnvironmentValidationError) as raised:
        build_child_environment(settings)
    assert raised.value.reason_code == "ENVIRONMENT_CREDENTIAL_NOT_APPROVED"
