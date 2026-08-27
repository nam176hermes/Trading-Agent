from __future__ import annotations

import atexit
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
