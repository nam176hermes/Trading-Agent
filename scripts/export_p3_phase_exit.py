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
from services.job_worker.p3_phase_readback import (
    export_phase_exit, phase_exit_v1, validate_phase_exit_receipt, terminal_phase_observation,
)


def current_source(root):
    if _git(root,'status','--porcelain'):
        raise ValueError('phase exporter requires clean source')
    return SourceIdentity.model_validate(canonical_source_identity(root))


def _write_new(path, raw):
    """Create once outside source; an existing destination must already hold the same bytes."""
    descriptor,name=tempfile.mkstemp(prefix='.phase-export-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        try:
            os.link(temporary,path,follow_symlinks=False)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes()!=raw:raise ValueError('export destination differs')
    finally:
        temporary.unlink()


def github_v1(ref,*,repository,store,run_id,attempt):
    """v1 bytes for the tracked receipt, from this run's protected attestation and inputs."""
    from packages.alpha_lifecycle.github_research import read_github_research_inputs
    from packages.alpha_lifecycle.github_run import read_github_run
    run,_=read_github_run(run_id,attempt,admission=False)
    receipt=validate_phase_exit_receipt(ref,repository=repository,store=store)
    research=read_github_research_inputs(run,source=receipt.source,input_set_ref=receipt.input_ref)
    return canonical_json_bytes(phase_exit_v1(ref,repository=repository,store=store,run=run,research=research))+b'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    selection=parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--job-id')
    selection.add_argument('--terminal-detail',type=Path)
    parser.add_argument('--store-root',type=Path,required=True)
    parser.add_argument('--credentials-directory',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--v1-output',type=Path)
    parser.add_argument('--expected-run',help='GITHUB run as <run_id>-<attempt>; required with --v1-output')
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):parser.error('phase evidence must remain external to source')
    if (args.v1_output is None)!=(args.expected_run is None) or args.v1_output is not None and (
            args.job_id is None or args.v1_output.resolve().is_relative_to(ROOT)):
        parser.error('v1 export needs a committed job, its GitHub run and a destination outside source')
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
            v1=None
            if args.v1_output is not None:
                run_id,_,attempt=args.expected_run.partition('-')
                v1=github_v1(ref,repository=repository,store=store,run_id=int(run_id),attempt=int(attempt))
    if receipt.source!=source or current_source(ROOT)!=source:
        raise ValueError('phase exit source differs from this clean exporter')
    _write_new(args.output,canonical_json_bytes(dict(receipt_ref=ref,receipt=receipt))+b'\n')
    if args.v1_output is not None and v1 is not None:
        _write_new(args.v1_output,v1)
    return 0


if __name__=='__main__':raise SystemExit(main())
