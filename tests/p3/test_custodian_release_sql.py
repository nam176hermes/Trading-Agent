"""Separately selected disposable SQL test; no protected data or host changes."""
import os
import pytest

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.pre_p3_provenance import canonical_source_identity
from services.job_worker import p3_fixture_sql


@pytest.mark.runtime_postgres
@pytest.mark.skipif(os.environ.get('P3_CUSTODIAN_SQL_SOURCE_TEST')!='1',
    reason='explicit disposable custodian SQL approval/selection required')
def test_custodian_release_is_committed_once_and_rechecks_authority():
    source=SourceIdentity.model_validate(canonical_source_identity(p3_fixture_sql.ROOT))
    result=p3_fixture_sql.run_sql_fixture(source,custodian_release_source_checks=True)
    assert result['sql_revision']=='0027_p3_custodian_release'
    assert result['cleanup']['root_absent'] and result['cleanup']['server_stopped']
