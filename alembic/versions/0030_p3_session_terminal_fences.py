"""Fence terminal session writes and qualify the expanded publication catalog."""
from alembic import op
from sqlalchemy import text

from services.job_store.p3_catalog import CUSTODIAN_CATALOG_SQL, SESSION_CATALOG_SQL

revision = '0030_p3_session_terminal_fences'
down_revision = '0029_p3_session_holdout_claim'
branch_labels = None
depends_on = None

PARENT_CATALOG = 'bb704c68e3d2e60c01142ace4346f26ca4fc9b3b8b42cddb323f6aa45563225f'
PARENT_SESSION_CATALOG = '39ddbaf17a54f0530b4981125112edf52bb9b82f66ddc95ee79d6fbcefe3c271'


def upgrade() -> None:
    connection = op.get_bind()
    if connection.execute(text("""SELECT current_user='trading_owner' AND session_user='trading_owner'
        AND (SELECT version_num FROM public.alembic_version)='0029_p3_session_holdout_claim'""")).scalar() is not True:
        raise RuntimeError('0030 requires the reviewed session parent and owner')
    previous = connection.execute(text('SHOW search_path')).scalar()
    try:
        connection.execute(text("SELECT set_config('search_path','pg_catalog',true)"))
        if (connection.execute(text(CUSTODIAN_CATALOG_SQL)).scalar() != PARENT_CATALOG
            or connection.execute(text(SESSION_CATALOG_SQL)).scalar() != PARENT_SESSION_CATALOG):
            raise RuntimeError('0030 parent authority catalog differs')
    finally:
        connection.execute(text("SELECT set_config('search_path',:value,true)"), {'value': previous})
    signatures = (
        ('worker_finalize_alpha_campaign(text,text,text,text,text,text,text,text,text,text,integer,text,text,jsonb,text,text,boolean,text,jsonb)',
         '          RETURN true;',
         """          IF p_final_state='SUCCEEDED' AND (
            job_plane.p3_payload_authorized(current_job.payload,p_job_id) IS NOT TRUE
            OR current_job.lease_expires_at IS NULL OR current_job.lease_expires_at<=clock_timestamp()
            OR current_attempt.lease_expires_at IS NULL OR current_attempt.lease_expires_at<=clock_timestamp()) THEN
            RAISE EXCEPTION 'P3 authority expired during finalize' USING ERRCODE='22023';
          END IF;
"""),
        ('worker_commit_alpha_campaign(text,text,text,text,text,text)',
         "          RETURN jsonb_build_object('result',v_result,",
         """          IF job_plane.p3_publication_authorized(v_job.payload,p_job_id,v_request,v_transport->'entries') IS NOT TRUE
            OR v_job.lease_expires_at IS NULL OR v_job.lease_expires_at<=clock_timestamp()
            OR v_attempt.lease_expires_at IS NULL OR v_attempt.lease_expires_at<=clock_timestamp() THEN
            RAISE EXCEPTION 'P3 authority expired during publication' USING ERRCODE='P3D03';
          END IF;
"""),
    )
    definitions = []
    for signature, boundary, guard in signatures:
        definition = connection.execute(text('SELECT pg_get_functiondef(to_regprocedure(:signature))'),
            {'signature': 'job_plane.'+signature}).scalar()
        if not isinstance(definition, str) or definition.count(boundary) != 1:
            raise RuntimeError('0030 reviewed terminal boundary differs')
        definitions.append(definition.replace(boundary, guard+boundary))
    op.execute('GRANT CREATE ON SCHEMA job_plane TO trading_p3_owner; SET LOCAL ROLE trading_p3_owner')
    for definition in definitions:
        op.execute(definition)
    op.execute('RESET ROLE; REVOKE CREATE ON SCHEMA job_plane FROM trading_p3_owner')


def downgrade() -> None:
    raise RuntimeError('session terminal fences are forward-only; use a reviewed repair')
