"""Bind the reviewed holdout instrument in SQL; execution remains closed."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = '0025_p3_holdout_intent'
down_revision = '0024_p3_workflow_claim'
branch_labels = None
depends_on = None


def _authority_definition(digest):
    rows=op.get_bind().execute(text("""
        SELECT p.prosrc,pg_catalog.pg_get_functiondef(p.oid)
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE p.oid='job_plane.accept_p3_operation_authorization(text,text,text)'::regprocedure
          AND r.rolname='trading_p3_owner' AND p.prosecdef AND p.provolatile='v'
          AND p.proparallel='u' AND p.pronargdefaults=0 AND p.prokind='f'
          AND l.lanname='plpgsql' AND p.proconfig=ARRAY['search_path=pg_catalog']
          AND NOT p.proleakproof AND NOT p.proisstrict AND NOT p.proretset
          AND pg_catalog.pg_get_function_result(p.oid)='text'
          AND p.proargnames=ARRAY['auth_text','intent_text','review_text'] AND p.proargmodes IS NULL
          AND NOT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb
          AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
          AND EXISTS (SELECT 1 FROM pg_catalog.pg_roles a WHERE a.rolname='trading_p3_authority'
            AND a.rolcanlogin AND NOT a.rolsuper AND NOT a.rolcreatedb AND NOT a.rolcreaterole
            AND NOT a.rolreplication AND NOT a.rolbypassrls
            AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=a.oid OR m.roleid=a.oid))
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
            WHERE a.grantee NOT IN (p.proowner,(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='trading_p3_authority'))
              OR a.privilege_type<>'EXECUTE' OR a.is_grantable)
          AND EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a
            WHERE a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='trading_p3_authority')
              AND a.privilege_type='EXECUTE')
    """)).fetchall()
    if len(rows)!=1 or hashlib.sha256(rows[0][0].encode()).hexdigest()!=digest:
        raise RuntimeError('0025 reviewed authority function drift')
    return rows[0][1]


def upgrade():
    if op.get_bind().execute(text("""SELECT current_user='trading_owner' AND session_user='trading_owner'
        AND (SELECT version_num FROM public.alembic_version)='0024_p3_workflow_claim'""")).scalar() is not True:
        raise RuntimeError('0025 requires the exact reviewed parent and owner')
    spec=importlib.util.spec_from_file_location('p3_holdout_intent_parent',
        Path(__file__).with_name('0023_p3_output_custody.py'))
    if spec is None or spec.loader is None:
        raise RuntimeError('0025 reviewed migration helpers unavailable')
    parent=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    parent._catalog(upgraded=True)
    parent._reviewed('p3_checked_object','text,text,text[]',
        'd15cb5d2a69030a30a564ae3c503e1cea9768922e7075fb9dbb4c32bd35c699c',
        arguments='raw,schema_name,keys',language='plpgsql',volatility='i',parallel='u',
        result='jsonb',security=False,worker=False)
    definition=_authority_definition('465947d67367d8c5bba60df666bbc31c3cb6072502d8f015641b71f5e6733467')
    definition=parent._replace(definition,
        "'context_dataset_ref','buffer_ref','environment_ref','policy_digest'",
        "'context_dataset_ref','buffer_ref','environment_ref','instrument_spec_ref','policy_digest'")
    op.execute('GRANT CREATE ON SCHEMA job_plane TO trading_p3_owner; SET LOCAL ROLE trading_p3_owner')
    op.execute(definition)
    _authority_definition(parent._body_digest(definition))
    op.execute('RESET ROLE; REVOKE CREATE ON SCHEMA job_plane FROM trading_p3_owner')
    parent._catalog(upgraded=True)


def downgrade():
    raise RuntimeError('0025 holdout intent binding is forward-only; use a reviewed forward repair')
