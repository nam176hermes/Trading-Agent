"""P3 SQL stays an explicit runtime prerequisite, outside portable validation."""
import json
import os
from pathlib import Path
import subprocess
import sys

from scripts.check_test_governance import build_governed_report


NODE = 'tests/p3/test_operation_sql.py::test_operation_authority_in_disposable_postgres'


def test_portable_collection_accounts_for_p3_sql_without_executing_it(tmp_path):
    report = tmp_path / 'collection.json'
    environment = {**os.environ, 'TEST_GOVERNANCE_REPORT': str(report),
        'TEST_GOVERNANCE_COMPONENT': 'root', 'TEST_GOVERNANCE_COLLECTION_ONLY': '1'}
    environment.pop('P3_OPERATION_SQL_SOURCE_TEST', None)
    completed = subprocess.run([sys.executable, '-m', 'pytest', '-q', '--collect-only',
        '-m', 'not runtime_postgres and not host_coupled',
        '-p', 'scripts.test_governance_pytest', NODE], env=environment,
        stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False)
    assert completed.returncode == 5, completed.stderr.decode()
    records = json.loads(report.read_bytes())['tests']
    assert len(records) == 1
    assert records[0]['outcome'] == 'deselected'
    assert records[0]['reason'] == 'marker expression deselected: runtime_postgres'
    entries = [entry for entry in json.loads(Path('tests/skip-allowlist.yaml').read_bytes())['entries']
        if entry['component'] == 'root' and entry['test_node_id'] == NODE]
    assert len(entries) == 1
    assert entries[0]['security_critical'] is True
    assert entries[0]['reason_category'] == 'DISPOSABLE_POSTGRES_REQUIRED'
    build_governed_report(records, entries)
