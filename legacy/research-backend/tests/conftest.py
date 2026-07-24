"""
tests/conftest.py — pytest fixtures for WSL crypto-research test suite.
"""

import atexit
import json
import os
import sys
import tempfile
import pytest
from pathlib import Path

# This file is a standalone, stateful diagnostic script, not a pytest module.
collect_ignore = ["test_integration.py"]

# Resolve every module-level runtime path into an isolated directory before
# pytest imports test modules. A fixture would run too late during collection.
_ORIGINAL_TRADING_DATA_ROOT = os.environ.get("TRADING_DATA_ROOT")
_SESSION_RUNTIME_DIRECTORY = tempfile.TemporaryDirectory(prefix="trading-agent-pytest-")
os.environ["TRADING_DATA_ROOT"] = _SESSION_RUNTIME_DIRECTORY.name

# Ensure the project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _cleanup_session_runtime_directory():
    """Restore the caller environment after pytest has released its capture files."""
    if _ORIGINAL_TRADING_DATA_ROOT is None:
        os.environ.pop("TRADING_DATA_ROOT", None)
    else:
        os.environ["TRADING_DATA_ROOT"] = _ORIGINAL_TRADING_DATA_ROOT
    _SESSION_RUNTIME_DIRECTORY.cleanup()


atexit.register(_cleanup_session_runtime_directory)


@pytest.fixture
def tmp_memory(tmp_path, monkeypatch):
    """Redirect all memory/paper/ file paths to a fresh temp directory."""
    import paper_trader as pt
    import portfolio_stats as ps

    mem = tmp_path / "memory"
    paper = mem / "paper"
    paper.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(pt, "PAPER_DIR", paper)
    monkeypatch.setattr(pt, "PORTFOLIO_FILE", paper / "portfolio.json")
    monkeypatch.setattr(pt, "ORDERS_FILE", paper / "orders.jsonl")
    monkeypatch.setattr(pt, "JOURNAL_FILE", mem / "trade_journal.jsonl")
    monkeypatch.setattr(ps, "PAPER_DIR", paper)
    monkeypatch.setattr(ps, "PORTFOLIO_FILE", paper / "portfolio.json")
    monkeypatch.setattr(ps, "JOURNAL_FILE", mem / "trade_journal.jsonl")

    yield tmp_path


@pytest.fixture
def empty_portfolio(tmp_memory):
    import paper_trader as pt
    pf = pt._init_portfolio()
    pt.save_portfolio(pf)
    return pf
