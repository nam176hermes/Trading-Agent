"""Launch Codex with this project's tool exclusions, preserving shared settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def build_command(
    arguments: list[str], *, root: Path = ROOT, codex_home: Path | None = None,
) -> list[str]:
    project = tomllib.loads((root / ".codex/config.toml").read_text())
    home = codex_home or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    user_path = home / "config.toml"
    user = tomllib.loads(user_path.read_text()) if user_path.exists() else {}
    command = ["codex", "--cd", str(root)]

    # CLI 0.147.0 omits project skill exclusions from its rendered prompt.
    # Keep shared denials when promoting this project's entries to CLI overrides.
    entries = user.get("skills", {}).get("config", []) + project.get("skills", {}).get("config", [])
    if entries:
        values = [
            "{path=" + json.dumps(entry["path"])
            + ",enabled=" + str(entry["enabled"]).lower() + "}"
            for entry in entries
        ]
        command.extend(["-c", "skills.config=[" + ",".join(values) + "]"])
    for section in ("mcp_servers", "plugins", "apps"):
        for name, settings in project.get(section, {}).items():
            if settings.get("enabled") is False:
                command.extend(["-c", f"{section}.{name}.enabled=false"])
    return command + arguments


if __name__ == "__main__":
    os.execvp("codex", build_command(sys.argv[1:]))
