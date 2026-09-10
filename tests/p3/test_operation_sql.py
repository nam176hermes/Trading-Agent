"""Explicit disposable SQL regression; never protected qualification."""
import os
import pytest
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.pre_p3_provenance import canonical_source_identity
from services.job_worker import p3_fixture_sql
from services.job_worker.p3_operation_fixture import REQUIRED_OPERATION_CHECKS


@pytest.mark.host_coupled
@pytest.mark.skipif(os.environ.get('P3_OPERATION_SQL_SOURCE_TEST') != '1',
                   reason='explicit disposable SQL source selection required')
def test_operation_authority_in_disposable_postgres():
    source = SourceIdentity.model_validate(canonical_source_identity(p3_fixture_sql.ROOT))
    result = p3_fixture_sql.run_sql_fixture(source)
    assert result['cleanup']['root_absent'] and result['cleanup']['server_stopped']
    assert REQUIRED_OPERATION_CHECKS <= set(result['checks'])
