"""Explicit disposable SQL regression; never protected qualification."""
import os
import pytest
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.pre_p3_provenance import canonical_source_identity
from services.job_worker import p3_fixture_sql
from services.job_worker.p3_operation_fixture import REQUIRED_OPERATION_CHECKS


@pytest.mark.runtime_postgres
@pytest.mark.skipif(os.environ.get('P3_OPERATION_SQL_SOURCE_TEST') != '1',
                   reason='explicit disposable SQL source selection required')
def test_operation_authority_in_disposable_postgres(tmp_path):
    source = SourceIdentity.model_validate(canonical_source_identity(p3_fixture_sql.ROOT))
    import hashlib
    import json
    import tempfile
    from pathlib import Path
    from packages.engine_contracts import canonical_json_bytes
    from packages.alpha_lifecycle.contracts.authority import IntegrationReceipt
    from services.job_worker.p3_qualification_readback import validate_integration_receipt
    from services.job_worker.process_runner import HeartbeatDecision
    from tests.p3.test_qualification_readback import _native_proof,_integration_proof,_seal
    # Native streams/review are explicit synthetic source fixtures; only SQL is real here.
    receipt_ref,context=_integration_proof(_native_proof(tmp_path,source))
    heartbeats=[]
    def heartbeat(identity):
        heartbeats.append(identity)
        return HeartbeatDecision.CONTINUE
    with tempfile.TemporaryDirectory(prefix='p3-readback-',dir='/tmp') as private:
        owned=Path(private)/('sql-'+hashlib.sha256(f"{context['job_id']}/{context['attempt_id']}".encode()).hexdigest()[:32])
        result=p3_fixture_sql.run_sql_fixture(source,heartbeat=heartbeat,owned_root=owned)
        assert not owned.exists()
    assert heartbeats and len({(identity.pid,identity.start_ticks) for identity in heartbeats})==1
    store=context['store'];receipt=json.loads(store.read_bytes(receipt_ref))
    cleanup=json.loads(store.read_bytes(receipt['cleanup_proof_ref']))
    cleanup['sql_cleanup']=result['cleanup']
    receipt['sql_proof_ref']=store.put_bytes(canonical_json_bytes(result),media_type='application/json')
    receipt['cleanup_proof_ref']=store.put_bytes(canonical_json_bytes(cleanup),media_type='application/json')
    receipt_ref=_seal(store,IntegrationReceipt,**receipt)
    context['postgres_binary_sha256']=hashlib.sha256((p3_fixture_sql.BIN/'postgres').read_bytes()).hexdigest()
    assert validate_integration_receipt(receipt_ref,**context).status=='PASS'
    assert result['cleanup']['root_absent'] and result['cleanup']['server_stopped']
    assert REQUIRED_OPERATION_CHECKS <= set(result['checks'])
