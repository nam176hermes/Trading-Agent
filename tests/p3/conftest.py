"""Build each immutable research seed once per pytest session (per worker)."""
import pytest

from tests.p3.research_fixtures import build_retained_baseline, build_synthetic_oos


@pytest.fixture(scope='session')
def retained_baseline(tmp_path_factory):
    return build_retained_baseline(tmp_path_factory)


@pytest.fixture(scope='session')
def synthetic_oos(tmp_path_factory):
    return build_synthetic_oos(tmp_path_factory)
