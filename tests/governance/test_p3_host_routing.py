"""P3 SQL stays an explicit runtime prerequisite, outside portable validation."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.check_test_governance import build_governed_report


NODE = 'tests/p3/test_operation_sql.py::test_operation_authority_in_disposable_postgres'


@pytest.mark.parametrize('node,marker,critical,category', [
    (NODE, 'runtime_postgres', True, 'DISPOSABLE_POSTGRES_REQUIRED'),
    *[('tests/p3/test_local_timing.py::test_local_private_policy_checks_actual_bubblewrap_copy['+value+']',
       'host_coupled', False, 'MISSING_HOST_CAPABILITY') for value in ('True','False')],
])
def test_portable_collection_accounts_for_host_requirements(tmp_path, node, marker, critical, category):
    report = tmp_path / 'collection.json'
    environment = {**os.environ, 'TEST_GOVERNANCE_REPORT': str(report),
        'TEST_GOVERNANCE_COMPONENT': 'root', 'TEST_GOVERNANCE_COLLECTION_ONLY': '1'}
    environment.pop('P3_OPERATION_SQL_SOURCE_TEST', None)
    completed = subprocess.run([sys.executable, '-m', 'pytest', '-q', '--collect-only',
        '-m', 'not runtime_postgres and not host_coupled',
        '-p', 'scripts.test_governance_pytest', node], env=environment,
        stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False)
    assert completed.returncode == 5, completed.stderr.decode()
    records = json.loads(report.read_bytes())['tests']
    assert len(records) == 1
    assert records[0]['outcome'] == 'deselected'
    assert records[0]['reason'] == 'marker expression deselected: '+marker
    entries = [entry for entry in json.loads(Path('tests/skip-allowlist.yaml').read_bytes())['entries']
        if entry['component'] == 'root' and entry['test_node_id'] == node]
    assert len(entries) == 1
    assert entries[0]['security_critical'] is critical
    assert entries[0]['reason_category'] == category
    build_governed_report(records, entries)
