from pathlib import Path

import pytest

from packages.alpha_lifecycle.sandbox import BubblewrapExecutor, SandboxHeld, require_official_sandbox


def test_missing_bubblewrap_remains_held(tmp_path: Path) -> None:
    with pytest.raises(SandboxHeld, match="HELD"):
        require_official_sandbox(tmp_path / "missing-bwrap")


def test_bubblewrap_recipe_denies_network_and_credentials() -> None:
    source = Path(__file__).parents[2].joinpath("packages/alpha_lifecycle/sandbox.py").read_text()
    assert '"--unshare-all"' in source
    assert '"--clearenv"' in source
    assert '"--ro-bind"' in source
    assert "RLIMIT_CPU" in source and "RLIMIT_AS" in source
    assert "stdin=subprocess.DEVNULL" in source
