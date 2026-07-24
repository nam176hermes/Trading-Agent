from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _deterministic_process_umask() -> None:
    """Keep release fixture modes deterministic without changing the caller shell."""
    caller_umask = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(caller_umask)
