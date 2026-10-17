"""Read an already committed phase exit; never enqueue, calculate or disclose."""
from pathlib import Path
import argparse
import os
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from packages.pre_p3_provenance import _git,canonical_source_identity
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.config import JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.p3_phase_readback import export_phase_exit,validate_phase_exit_receipt,terminal_phase_observation


def current_source(root):
    if _git(root,'status','--porcelain'):
        raise ValueError('phase exporter requires clean source')
    return SourceIdentity.model_validate(canonical_source_identity(root))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    selection=parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--job-id')
    selection.add_argument('--terminal-detail',type=Path)
    parser.add_argument('--store-root',type=Path,required=True)
    parser.add_argument('--credentials-directory',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):parser.error('phase evidence must remain external to source')
    source=current_source(ROOT)
    store=LocalArtifactStore(args.store_root)
    if args.terminal_detail is not None:
        from apps.job_api.contracts import JobDetailEnvelope
        with args.terminal_detail.open('rb') as stream:observed=stream.read(1048577)
        if len(observed)>1048576:raise ValueError('terminal observation exceeds bound')
        envelope=JobDetailEnvelope.model_validate_json(observed)
        observed_ref=store.put_bytes(canonical_json_bytes(envelope),media_type='application/json')
        receipt=terminal_phase_observation(observed_ref,store=store)
        ref=store.put_bytes(canonical_json_bytes(receipt),media_type='application/json')
    else:
        if args.credentials_directory is None:parser.error('committed export requires worker read credentials')
        settings=JobStoreSettings.from_systemd_credentials(
            {'CREDENTIALS_DIRECTORY':str(args.credentials_directory)},expected_user='trading_job_worker')
        with WorkerRepository(settings) as worker:
            worker.assert_session_runtime_identity()
            repository=worker.alpha_publication_repository(store)
            ref=export_phase_exit(args.job_id,repository=repository,store=store)
            receipt=validate_phase_exit_receipt(ref,repository=repository,store=store)
    if receipt.source!=source or current_source(ROOT)!=source:
        raise ValueError('phase exit source differs from this clean exporter')
    raw=canonical_json_bytes(dict(receipt_ref=ref,receipt=receipt))+b'\n'
    descriptor,name=tempfile.mkstemp(prefix='.phase-export-',dir=args.output.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        try:
            os.link(temporary,args.output,follow_symlinks=False)
        except FileExistsError:
            if args.output.is_symlink() or args.output.read_bytes()!=raw:raise ValueError('export destination differs')
    finally:
        temporary.unlink()
    return 0


if __name__=='__main__':raise SystemExit(main())
