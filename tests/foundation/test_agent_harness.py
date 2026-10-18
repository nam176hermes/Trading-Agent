"""Regression checks for project-local agent configuration, not an OS sandbox."""

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_serena_uses_current_checkout_instead_of_a_fixed_main_path():
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
    arguments = config["mcp_servers"]["serena"]["args"]
    assert "--project-from-cwd" in arguments
    assert not any(arg == "--project" or arg.startswith("--project=") for arg in arguments)
    assert arguments[arguments.index("--enable-web-dashboard") + 1] == "False"


def test_project_defaults_do_not_override_machine_security_or_credentials():
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
    assert {"approval_policy", "sandbox_mode", "model_provider", "model_providers"}.isdisjoint(config)
    for server in config.get("mcp_servers", {}).values():
        assert {"env", "http_headers", "bearer_token"}.isdisjoint(server)


def test_project_tool_exclusions_preserve_engineering_and_quantitative_skills():
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
    excluded = {
        Path(item["path"]).parent.name
        for item in config["skills"]["config"] if item["enabled"] is False
    }
    assert {"adaptyv", "qiskit", "clinical-reports", "autoskill", "taste-skill"} <= excluded
    assert excluded.isdisjoint({
        "property-based-testing", "gh-fix-ci", "gh-address-comments", "polars",
        "scikit-learn", "statistical-analysis", "timesfm-forecasting", "playwright",
    })
    disabled_servers = {
        name for name, settings in config["mcp_servers"].items()
        if settings.get("enabled") is False
    }
    assert disabled_servers == {"deepwiki", "sequential-thinking"}
    assert "_default" not in config["apps"]


def test_project_launcher_preserves_global_denials_and_forwards_arguments(tmp_path):
    from scripts.codex_project import build_command

    project = tmp_path / "project"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex/config.toml").write_text(
        '[skills]\nconfig = [{ path = "/project/unused/SKILL.md", enabled = false }]\n'
        '[mcp_servers.deepwiki]\nenabled = false\n'
        '[apps.unused]\nenabled = false\n'
        '[plugins."unused@local"]\nenabled = false\n'
    )
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[skills]\nconfig = [{ path = "/shared/disabled/SKILL.md", enabled = false }]\n'
    )
    command = build_command(["debug", "prompt-input", "check"], root=project, codex_home=home)
    assert command[:3] == ["codex", "--cd", str(project)]
    assert command[-3:] == ["debug", "prompt-input", "check"]
    overrides = [command[i + 1] for i, value in enumerate(command[:-1]) if value == "-c"]
    skills = next(tomllib.loads(value)["skills"]["config"] for value in overrides if value.startswith("skills.config="))
    assert {entry["path"] for entry in skills if entry["enabled"] is False} == {
        "/project/unused/SKILL.md", "/shared/disabled/SKILL.md",
    }
    assert "apps.unused.enabled=false" in overrides
    assert "plugins.unused@local.enabled=false" in overrides
    assert "mcp_servers.deepwiki.enabled=false" in overrides
