"""Expose only exact accepted payload authority for current LOCAL API admission."""
from alembic import op
from sqlalchemy import text
from services.job_store.p3_catalog import SESSION_CATALOG_SQL

revision='0032_p3_enqueue_freshness'
down_revision='0031_p3_local_identity'
branch_labels=None
depends_on=None
PARENT_CATALOG='55db6daf246896ff3f17c5e6a125a4a3d64cdae93a9a726db3db52ca7c6fb310'


def upgrade():
    connection=op.get_bind()
    if connection.execute(text("""SELECT current_user='trading_owner' AND session_user='trading_owner'
        AND (SELECT version_num FROM public.alembic_version)='0031_p3_local_identity'""")).scalar() is not True:
        raise RuntimeError('0032 requires the exact local parent and owner')
    previous=connection.execute(text('SHOW search_path')).scalar()
    try:
        connection.execute(text('SET LOCAL search_path=pg_catalog'))
        if connection.execute(text(SESSION_CATALOG_SQL)).scalar()!=PARENT_CATALOG:
            raise RuntimeError('0032 parent authority catalog differs')
    finally:
        connection.execute(text("SELECT set_config('search_path',:value,true)"),{'value':previous})
    op.execute('GRANT CREATE ON SCHEMA job_plane TO trading_p3_owner; SET LOCAL ROLE trading_p3_owner')
    op.execute("""CREATE FUNCTION job_plane.api_read_p3_enqueue_authority(p_payload_text text)
      RETURNS TABLE(authorization_text text,intent_text text)
      LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $reader$
      DECLARE p jsonb;
      BEGIN
        IF session_user<>'trading_job_api' THEN
          RAISE EXCEPTION 'P3 enqueue reader session rejected' USING ERRCODE='42501';
        END IF;
        IF p_payload_text IS NULL OR octet_length(p_payload_text)>65536 THEN
          RAISE EXCEPTION 'P3 enqueue payload rejected' USING ERRCODE='22023';
        END IF;
        p:=p_payload_text::jsonb;
        IF public.canonical_domain_json(p) IS DISTINCT FROM p_payload_text
          OR job_plane.p3_payload_authorized(p,NULL) IS NOT TRUE THEN
          RAISE EXCEPTION 'P3 enqueue payload rejected' USING ERRCODE='22023';
        END IF;
        RETURN QUERY SELECT a.authorization_text,o.intent_text
          FROM public.p3_campaign_authorizations a
          LEFT JOIN public.p3_operation_authorizations o ON o.authorization_digest=a.request_digest
          WHERE a.request_digest=p#>>'{authorization_ref,content_sha256}'
            AND a.source_identity_text=public.canonical_domain_json(p->'expected_source')
            AND job_plane.p3_valid_ref(p->'authorization_ref')
            AND (p#>>'{authorization_ref,size_bytes}')::numeric=octet_length(a.authorization_text);
        IF NOT FOUND THEN
          RAISE EXCEPTION 'P3 enqueue authority unavailable' USING ERRCODE='22023';
        END IF;
      END;
      $reader$;
      REVOKE ALL ON FUNCTION job_plane.api_read_p3_enqueue_authority(text)
        FROM PUBLIC,trading_owner,trading_jobs,trading_migrator,trading_reader,
        trading_job_api,trading_job_worker,trading_job_scheduler,trading_p3_authority,trading_p3_custodian;
      GRANT EXECUTE ON FUNCTION job_plane.api_read_p3_enqueue_authority(text) TO trading_job_api;
      RESET ROLE; REVOKE CREATE ON SCHEMA job_plane FROM trading_p3_owner;
    """)


def downgrade():
    raise RuntimeError('enqueue freshness is forward-only; use a reviewed repair')
