"""Build each immutable research seed once per pytest session (per worker)."""
import shutil

import pytest

from tests.p3.research_fixtures import build_retained_baseline, build_synthetic_oos


@pytest.fixture(scope='session')
def retained_baseline(tmp_path_factory):
    return build_retained_baseline(tmp_path_factory)


@pytest.fixture(scope='session')
def synthetic_oos(tmp_path_factory):
    return build_synthetic_oos(tmp_path_factory)


@pytest.fixture(scope='session')
def default_baseline_seed(tmp_path_factory):
    from tests.p3.test_replica_execution import baseline_inputs
    return baseline_inputs(tmp_path_factory.mktemp('default-baseline-seed')/'inputs')


@pytest.fixture
def isolated_baseline_input(default_baseline_seed,tmp_path):
    from packages.data_catalog.artifact_store import LocalArtifactStore
    store,reference=default_baseline_seed
    # Copy real files and preserve links so custody faults remain visible.
    shutil.copytree(store._root,tmp_path/'inputs',symlinks=True)
    return LocalArtifactStore(tmp_path/'inputs'),reference
