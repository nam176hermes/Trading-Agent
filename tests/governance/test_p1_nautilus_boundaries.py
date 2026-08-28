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
                    "frozen.py": {
                        "maximum_bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
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
    (root / "frozen.py").write_text("pass\npass\n", encoding="utf-8")
    with pytest.raises(BoundaryError, match="frozen launcher"):
        check_boundaries(root, budget)
