"""Executable P1 Nautilus ownership boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.check_p1_nautilus_boundaries import BoundaryError, check_boundaries


ROOT = Path(__file__).resolve().parents[2]
BUDGET = ROOT / "docs/implementation/p1-real-nautilus/growth-budget.json"


def _fixture(tmp_path: Path, source: str, *, relative: str) -> tuple[Path, Path]:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    frozen = tmp_path / "frozen.py"
    frozen.write_text("pass\n", encoding="utf-8")
    raw = frozen.read_bytes()
    budget = tmp_path / "budget.json"
    budget.write_text(
        json.dumps(
            {
                "schema": "trading-agent-p1-growth-budget/v1",
                "runtime_family": "cython-v1",
                "frozen_files": {
                    "engines/nautilus/launcher/nautilus_backtest.py": {
                        "maximum_bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    },
                    "engines/nautilus/launcher/target_portfolio_strategy.py": {
                        "maximum_bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    for relative in (
        "engines/nautilus/launcher/nautilus_backtest.py",
        "engines/nautilus/launcher/target_portfolio_strategy.py",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return tmp_path, budget


def test_current_p1_boundaries_pass() -> None:
    check_boundaries(ROOT, BUDGET)


@pytest.mark.parametrize(
    ("relative", "source", "message"),
    (
        ("packages/bad.py", "import nautilus_trader\n", "root Nautilus"),
        ("engines/nautilus/runtime_v1/bad.py", "import socket\n", "network"),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "runtime_v1 = 1\nruntime_v2 = 2\n",
            "mixed runtime",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "PROFILE = 'p1-real-backtest'\n",
            "profile name",
        ),
    ),
)
def test_seeded_boundary_violations_fail(
    tmp_path: Path, relative: str, source: str, message: str
) -> None:
    root, budget = _fixture(tmp_path, source, relative=relative)
    with pytest.raises(BoundaryError, match=message):
        check_boundaries(root, budget)


def test_frozen_launcher_growth_fails(tmp_path: Path) -> None:
    root, budget = _fixture(tmp_path, "pass\n", relative="safe.py")
    (root / "engines/nautilus/launcher/nautilus_backtest.py").write_text(
        "pass\npass\n", encoding="utf-8"
    )
    with pytest.raises(BoundaryError, match="frozen launcher"):
        check_boundaries(root, budget)


@pytest.mark.parametrize(
    ("relative", "source", "message"),
    (
        (
            "engines/nautilus/launcher/new_runtime.py",
            "import nautilus_trader\n",
            "root Nautilus",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "from urllib import request\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "import smtplib\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "value = __import__('socket')\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "loader = __import__\nvalue = loader('socket')\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "value = __builtins__['__import__']('socket')\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "from ..launcher import nautilus_backtest\n",
            "network",
        ),
        ("services/job_worker/bad.py", "PROFILE='p1-real-backtest'\n", "profile"),
        (
            "services/job_worker/bad.py",
            "PROFILE='p1-' 'real-backtest'\n",
            "profile",
        ),
        (
            "services/job_worker/bad.py",
            "PROFILE='p1-' + 'real-backtest'\n",
            "profile",
        ),
    ),
)
def test_additional_seeded_boundary_violations_fail(
    tmp_path: Path, relative: str, source: str, message: str
) -> None:
    root, budget = _fixture(tmp_path, source, relative=relative)
    with pytest.raises(BoundaryError, match=message):
        check_boundaries(root, budget)


def test_growth_budget_is_closed(tmp_path: Path) -> None:
    root, budget = _fixture(tmp_path, "pass\n", relative="safe.py")
    budget.write_text(
        json.dumps(
            {
                "schema": "trading-agent-p1-growth-budget/v1",
                "runtime_family": "cython-v2",
                "frozen_files": {},
                "unknown": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BoundaryError, match="growth budget"):
        check_boundaries(root, budget)


def test_runtime_relative_import_is_allowed(tmp_path: Path) -> None:
    root, budget = _fixture(
        tmp_path,
        "from .generated_protocol import P1_EVENT_SCHEMA\n",
        relative="engines/nautilus/runtime_v1/main.py",
    )
    check_boundaries(root, budget)
