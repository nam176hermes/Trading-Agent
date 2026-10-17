from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trusted_test_tmp import prepare_trusted_test_tmp


_TEST_TMP_SESSION = prepare_trusted_test_tmp("root-pytest")
atexit.register(_TEST_TMP_SESSION.cleanup)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--portable-embedded-proof",
        action="store_true",
        default=False,
        help="verify component introductions against embedded evidence",
    )


@pytest.fixture(scope="session", autouse=True)
def disposable_connection_fence():
    if os.environ.get("TRADING_TEST_DISPOSABLE_FIXTURE_PLAN_REQUIRED") == "YES":
        from tests.jobs._postgres import install_disposable_connection_fence
        with pytest.MonkeyPatch.context() as patch:
            install_disposable_connection_fence(patch)
            yield
    else:
        yield
