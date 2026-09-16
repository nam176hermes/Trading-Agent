"""Select the workflow-assigned P3 job before taking any job lease."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = '0024_p3_workflow_claim'
down_revision = '0023_p3_output_custody'
branch_labels = None
depends_on = None


def upgrade():
    connection=op.get_bind()
    if connection.execute(text("""SELECT current_user='trading_owner' AND session_user='trading_owner'
        AND (SELECT version_num FROM public.alembic_version)='0023_p3_output_custody'
        AND to_regprocedure('job_plane.worker_claim_bound_alpha_campaign(text,text,text,integer,text,text,boolean,text)') IS NULL""")).scalar() is not True:
        raise RuntimeError('0024 requires the reviewed P3 parent, owner and absent bound capability')
    # Offline-only: the fixture/host owner must keep new worker admission closed
    # until commit. Replacing a function cannot stop an already-entered old call.
    workers=text("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_stat_activity
        WHERE datname=current_database() AND usename='trading_job_worker')""")
    # Session user/database are public; backend_type is hidden from this owner.
    connection.execute(text('SELECT pg_catalog.pg_stat_clear_snapshot()'))
    if connection.execute(workers).scalar() is True:
        raise RuntimeError('0024 requires quiescent worker sessions')
    previous_timeout=connection.execute(text('SHOW lock_timeout')).scalar()
    milliseconds=connection.execute(text("SELECT setting::integer FROM pg_catalog.pg_settings WHERE name='lock_timeout'")).scalar()
    connection.execute(text("SELECT set_config('lock_timeout',:value,true)"),
        {'value':str(min(milliseconds or 5000,5000))})
    op.execute('LOCK TABLE public.jobs IN ACCESS EXCLUSIVE MODE')
    connection.execute(text("SELECT set_config('lock_timeout',:value,true)"),{'value':previous_timeout})
    connection.execute(text('SELECT pg_catalog.pg_stat_clear_snapshot()'))
    if connection.execute(workers).scalar() is True or connection.execute(text("""
        SELECT EXISTS(SELECT 1 FROM public.jobs WHERE job_type='ALPHA_CAMPAIGN'
          AND state IN ('CLAIMED','RUNNING','CANCEL_REQUESTED'))""")).scalar() is True:
        raise RuntimeError('0024 requires quiescent workers and reconciled P3 custody')
    # Reuse the predecessor's exact catalog/ACL verification and replacement guards.
    spec=importlib.util.spec_from_file_location('p3_output_custody_parent',
        Path(__file__).with_name('0023_p3_output_custody.py'))
    if spec is None or spec.loader is None:
        raise RuntimeError('0024 reviewed migration parent unavailable')
    parent=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    parent._catalog(upgraded=True)
    _dependencies(parent)
    signature='text,text,text,integer,text,text,boolean'
    arguments='p_attempt_id,p_worker_id,p_lease_token,p_lease_seconds,p_trace_id,p_event_id,p_fixture_only'
    output_arguments='job_id,job_type,payload,attempt_number,max_attempts,lease_expires_at'
    result='TABLE(job_id text, job_type text, payload jsonb, attempt_number integer, max_attempts smallint, lease_expires_at timestamp with time zone)'
    definition=parent._reviewed('worker_claim_alpha_campaign',signature,
        '10f7a4292c8503c17395c2ff7e991dbc12e25bfdee7f8158c22ec29a5b82c46d',
        arguments=arguments+','+output_arguments,language='plpgsql',volatility='v',parallel='u',
        result=result,modes=['i']*7+['t']*6)
    fixture_definition=parent._replace(definition,'IF p_fixture_only IS NULL OR session_user',
        'IF p_fixture_only IS NOT TRUE OR session_user')
    definition=parent._replace(definition,'job_plane.worker_claim_alpha_campaign(',
        'job_plane.worker_claim_bound_alpha_campaign(')
    definition=parent._replace(definition,'p_fixture_only boolean)',
        'p_fixture_only boolean, p_expected_job_id text)')
    definition=parent._replace(definition,'IF p_fixture_only IS NULL OR session_user',
        "IF p_expected_job_id IS NULL OR p_expected_job_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$' OR p_fixture_only IS NULL OR session_user")
    definition=parent._replace(definition,"WHERE j.job_type='ALPHA_CAMPAIGN' AND j.state='QUEUED'",
        "WHERE j.job_id=p_expected_job_id AND j.job_type='ALPHA_CAMPAIGN' AND j.state='QUEUED'")
    op.execute('GRANT CREATE ON SCHEMA job_plane TO trading_p3_owner; SET LOCAL ROLE trading_p3_owner')
    op.execute(definition)
    op.execute(fixture_definition)
    op.execute('''REVOKE ALL ON FUNCTION job_plane.worker_claim_bound_alpha_campaign(text,text,text,integer,text,text,boolean,text)
        FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler,trading_p3_authority;
        GRANT EXECUTE ON FUNCTION job_plane.worker_claim_bound_alpha_campaign(text,text,text,integer,text,text,boolean,text) TO trading_job_worker''')
    parent._reviewed('worker_claim_bound_alpha_campaign',signature+',text',parent._body_digest(definition),
        arguments=arguments+',p_expected_job_id,'+output_arguments,language='plpgsql',volatility='v',parallel='u',
        result=result,modes=['i']*8+['t']*6)
    parent._reviewed('worker_claim_alpha_campaign',signature,parent._body_digest(fixture_definition),
        arguments=arguments+','+output_arguments,language='plpgsql',volatility='v',parallel='u',
        result=result,modes=['i']*7+['t']*6)
    op.execute('RESET ROLE; REVOKE CREATE ON SCHEMA job_plane FROM trading_p3_owner')
    parent._catalog(upgraded=True)
    _dependencies(parent)


def _dependencies(parent):
    parent._reviewed('p3_worker_lane_matches','jsonb,boolean',
        '22aa095bedb5b8a772ddf945f9d4c75f387e8ceca5f3d60befebaaffdc196490',
        arguments='payload,fixture_only',language='sql',volatility='i',parallel='s',
        result='boolean',security=False)
    parent._reviewed('p3_payload_authorized','jsonb,text',
        '819d2f2e5832a9cda0080bfaf4c3c4678ed0dcb013daf291df41d5c1042335a6',
        arguments='payload,bound_job',language='plpgsql',volatility='v',parallel='u',
        result='boolean',worker=False)


def downgrade():
    raise RuntimeError('0024 workflow claim is forward-only; use a reviewed forward repair')
