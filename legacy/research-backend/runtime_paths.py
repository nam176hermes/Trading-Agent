"""Central resolvers for external research runtime state."""

from __future__ import annotations

import os
from pathlib import Path


def _configured_path(name: str) -> Path | None:
    configured = os.environ.get(name, "").strip()
    return Path(configured).expanduser().resolve() if configured else None


def data_root() -> Path:
    return _configured_path("TRADING_DATA_ROOT") or Path.home() / ".local" / "share" / "trading-agent"


def reports_dir() -> Path:
    return _configured_path("TRADING_REPORTS_DIR") or data_root() / "reports"


def signal_output_dir() -> Path:
    return _configured_path("TRADING_SIGNAL_OUTPUT_DIR") or data_root() / "signals"


def mode_file() -> Path:
    return _configured_path("TRADING_MODE_FILE") or data_root() / ".mode"


def kill_switch_file() -> Path:
    return _configured_path("TRADING_KILL_SWITCH_PATH") or data_root() / ".kill_switch"


def configured_env_file() -> Path | None:
    """Return the only environment file callers are allowed to load."""
    return _configured_path("TRADING_ENV_FILE")
