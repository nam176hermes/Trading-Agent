"""Executable P1 Nautilus ownership boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts.check_p1_nautilus_boundaries import (
    BoundaryError,
    check_boundaries,
    p1_lineage_report,
)


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
                "schema": "trading-agent-p1-growth-budget/v2",
                "runtime_family": "cython-v1",
                "frozen_files": {
                    "engines/nautilus/launcher/nautilus_backtest.py": {
                        "maximum_bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    },
                    "engines/nautilus/launcher/target_portfolio_strategy.py": {
                        "maximum_bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    },
                },
                "source_growth": {
                    "default_max_logical_lines": 500,
                    "exact_files": [],
                    "overrides": {},
                    "roots": [
                        "engines/nautilus/runtime_v1",
                        "packages/engine_portfolio_projection",
                        "packages/nautilus_runtime_contracts",
                    ],
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


def test_current_p1_lineage_is_p1_1231_with_unchanged_legacy_1227() -> None:
    report = p1_lineage_report(
        ROOT, source_identity=("a" * 40, "b" * 40)
    )

    assert report["verdict"] == "PASS"
    assert report["p1_engine_version"] == "1.231.0"
    assert report["legacy_engine_version"] == "1.227.0"
    assert report["legacy_profiles_unchanged"] is True


@pytest.mark.parametrize("replacement", ("1.227.0", "latest"))
def test_p1_lineage_rejects_active_version_or_moving_authority(
    tmp_path: Path, replacement: str
) -> None:
    governed = (
        "docs/implementation/p1-real-nautilus/upgrade/p1-engine-baseline-receipt.json",
        "docs/implementation/p1-real-nautilus/upgrade/candidate-generations/NT1231-U04-G1.json",
        "docs/implementation/p1-real-nautilus/upgrade/pin-inventory.json",
        "engines/nautilus/p1-runtime-closure-policy.json",
        "engines/nautilus/runtime-closure-policy.json",
        "engines/nautilus/paper-compatibility-runtime-closure-policy.json",
        "services/job_worker/nautilus_closure.py",
    )
    for relative in governed:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    policy_path = tmp_path / "engines/nautilus/p1-runtime-closure-policy.json"
    policy = json.loads(policy_path.read_bytes())
    policy["engine_version"] = replacement
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(BoundaryError, match="lineage"):
        p1_lineage_report(
            tmp_path, source_identity=("a" * 40, "b" * 40)
        )


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


def test_p1_source_growth_budget_fails_before_a_module_becomes_a_monolith(
    tmp_path: Path,
) -> None:
    root, budget = _fixture(
        tmp_path,
        "value = 1\n" * 501,
        relative="engines/nautilus/runtime_v1/large.py",
    )
    with pytest.raises(BoundaryError, match="source growth"):
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
            "from nautilus_trader import adapters\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "value = __builtins__['__import__']('socket')\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "value = eval(\"__import__('socket')\")\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "value = globals()['__built'+'ins__']['__im'+'port__']('socket')\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "from importlib import import_module\nvalue = import_module('socket')\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "from importlib.machinery import SourceFileLoader\nvalue = SourceFileLoader('x', '/tmp/x.py').load_module()\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "from importlib.util import spec_from_file_location\nvalue = spec_from_file_location('x', '/tmp/x.py')\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "from importlib.metadata import EntryPoint\nvalue = EntryPoint(name='x', value='socket:socket', group='x').load()\n",
            "network",
        ),
        (
            "engines/nautilus/runtime_v1/bad.py",
            "value = (lambda: 0).__globals__['__built'+'ins__']['__im'+'port__']('socket')\n",
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
        (
            "services/job_worker/bad.py",
            "PROFILE='p1' + '-real-' + 'backtest'\n",
            "profile",
        ),
        (
            "services/job_worker/bad.py",
            "PROFILE=''.join(('p1', '-real-', 'backtest'))\n",
            "profile",
        ),
        (
            "services/job_worker/bad.py",
            "PROFILE='p1%s%s' % ('-real-', 'backtest')\n",
            "profile",
        ),
        (
            "services/job_worker/bad.py",
            "PROFILE=f\"{'p1'}{'-real-'}{'backtest'}\"\n",
            "profile",
        ),
        (
            "services/job_worker/bad.py",
            "PROFILE='p1-%s' % 'real-backtest'\n",
            "profile",
        ),
        (
            "services/job_worker/bad.py",
            "PROFILE='{}{}{}'.format('p1', '-real-', 'backtest')\n",
            "profile",
        ),
        (
            "services/job_worker/bad.py",
            "a='p1'\nb='-real-'\nc='backtest'\nPROFILE=f'{a}{b}{c}'\n",
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


def test_runtime_may_read_version_metadata_without_importing_product(
    tmp_path: Path,
) -> None:
    root, budget = _fixture(
        tmp_path,
        "from importlib.metadata import version as package_version\n",
        relative="engines/nautilus/runtime_v1/main.py",
    )
    check_boundaries(root, budget)
