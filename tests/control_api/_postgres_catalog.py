from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

import psycopg
from psycopg import sql

from trading_control.db import DatabaseSettings


AUTHORITY_ROLES = (
    "postgres",
    "trading_owner",
    "trading_migrator",
    "trading_reader",
    "trading_jobs",
    "trading_job_api",
    "trading_job_worker",
    "trading_job_scheduler",
)

_VARCHAR_ARRAY_TEXT_CAST = re.compile(
    r"ARRAY\[(?P<values>(?:'(?:''|[^'])*'::character varying(?:::text)?(?:, )?)+)\]"
    r"(?P<array_cast>::text\[\])?"
)


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    semantic_digests: dict[str, str]
    semantic_row_counts: dict[str, int]
    semantic_subgroup_digests: dict[str, dict[str, str]]
    semantic_subgroup_row_counts: dict[str, dict[str, int]]
    semantic_rows: dict[str, tuple[tuple[object, ...], ...]]
    raw_acl_sha256: str
    raw_acl_row_count: int
    table_row_counts: dict[str, int]


def _canonical_rows(rows: list[tuple[object, ...]]) -> bytes:
    records = [
        json.dumps(
            list(row),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        for row in rows
    ]
    return b"\n".join(sorted(records)) + (b"\n" if records else b"")


def _digest(rows: list[tuple[object, ...]]) -> tuple[str, int]:
    canonical = _canonical_rows(rows)
    return hashlib.sha256(canonical).hexdigest(), len(rows)


def _subgroup_digests(
    rows: list[tuple[object, ...]],
) -> tuple[dict[str, str], dict[str, int]]:
    grouped: dict[str, list[tuple[object, ...]]] = {}
    for row in rows:
        if not row or not isinstance(row[0], str) or not row[0]:
            raise ValueError("catalog semantic row has no subgroup identity")
        grouped.setdefault(row[0], []).append(row)
    digests: dict[str, str] = {}
    counts: dict[str, int] = {}
    for name in sorted(grouped):
        digests[name], counts[name] = _digest(grouped[name])
    return digests, counts


def _query(
    connection: psycopg.Connection,
    record_type: str,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> list[tuple[object, ...]]:
    return [
        (record_type, *row)
        for row in connection.execute(statement, parameters).fetchall()
    ]


def _canonicalize_constraint_definition(definition: str) -> str:
    def replace(match: re.Match[str]) -> str:
        values = match.group("values")
        element_count = values.count("::character varying")
        element_text_count = values.count("::character varying::text")
        has_array_cast = match.group("array_cast") is not None
        if not (
            (has_array_cast and element_text_count == 0)
            or (not has_array_cast and element_text_count == element_count)
        ):
            return match.group(0)
        normalized = values.replace(
            "::character varying::text",
            "::character varying",
        )
        return f"ARRAY[{normalized}]::text[]"

    return _VARCHAR_ARRAY_TEXT_CAST.sub(replace, definition)


def _identity_rows(connection: psycopg.Connection) -> list[tuple[object, ...]]:
    rows = _query(
        connection,
        "schema",
        "SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname = 'public'",
    )
    rows += _query(
        connection,
        "relation",
        """
        SELECT n.nspname, c.relname, c.relkind
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p','S','v','m','f')
        """,
    )
    rows += _query(
        connection,
        "function",
        """
        SELECT n.nspname, p.proname,
               pg_catalog.pg_get_function_identity_arguments(p.oid),
               pg_catalog.pg_get_function_result(p.oid)
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
        """,
    )
    rows += _query(
        connection,
        "trigger",
        """
        SELECT n.nspname, c.relname, t.tgname
        FROM pg_catalog.pg_trigger AS t
        JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND NOT t.tgisinternal
        """,
    )
    rows += _query(
        connection,
        "policy",
        """
        SELECT schemaname, tablename, policyname
        FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
        """,
    )
    rows += _query(
        connection,
        "extension",
        """
        SELECT e.extname, e.extversion, n.nspname
        FROM pg_catalog.pg_extension AS e
        JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
        WHERE e.extname = 'pgcrypto'
        """,
    )
    return rows


def _owner_rows(connection: psycopg.Connection) -> list[tuple[object, ...]]:
    rows = _query(
        connection,
        "database_owner",
        """
        SELECT datname, pg_catalog.pg_get_userbyid(datdba)
        FROM pg_catalog.pg_database WHERE datname = current_database()
        """,
    )
    rows += _query(
        connection,
        "schema_owner",
        """
        SELECT nspname, pg_catalog.pg_get_userbyid(nspowner)
        FROM pg_catalog.pg_namespace WHERE nspname = 'public'
        """,
    )
    rows += _query(
        connection,
        "relation_owner",
        """
        SELECT n.nspname, c.relname, c.relkind,
               pg_catalog.pg_get_userbyid(c.relowner)
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p','S','v','m','f')
        """,
    )
    rows += _query(
        connection,
        "function_owner",
        """
        SELECT n.nspname, p.proname,
               pg_catalog.pg_get_function_identity_arguments(p.oid),
               pg_catalog.pg_get_userbyid(p.proowner)
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
        """,
    )
    rows += _query(
        connection,
        "extension_owner",
        """
        SELECT extname, pg_catalog.pg_get_userbyid(extowner)
        FROM pg_catalog.pg_extension WHERE extname = 'pgcrypto'
        """,
    )
    return rows


def _effective_acl_rows(
    connection: psycopg.Connection,
) -> list[tuple[object, ...]]:
    roles = list(AUTHORITY_ROLES)
    rows = _query(
        connection,
        "database_acl",
        """
        SELECT r.rolname, privilege,
               pg_catalog.has_database_privilege(
                 r.rolname, current_database(), privilege
               )
        FROM pg_catalog.pg_roles AS r
        CROSS JOIN unnest(ARRAY['CONNECT','CREATE','TEMP']::text[]) AS p(privilege)
        WHERE r.rolname = ANY(%s)
        """,
        (roles,),
    )
    rows += _query(
        connection,
        "public_database_acl",
        """
        SELECT d.datname, x.privilege_type, x.is_grantable
        FROM pg_catalog.pg_database AS d
        CROSS JOIN LATERAL pg_catalog.aclexplode(
          COALESCE(d.datacl, pg_catalog.acldefault('d', d.datdba))
        ) AS x
        WHERE d.datname = current_database() AND x.grantee = 0
        """,
    )
    rows += _query(
        connection,
        "schema_acl",
        """
        SELECT r.rolname, privilege,
               pg_catalog.has_schema_privilege(r.rolname, n.oid, privilege)
        FROM pg_catalog.pg_roles AS r
        CROSS JOIN pg_catalog.pg_namespace AS n
        CROSS JOIN unnest(ARRAY['USAGE','CREATE']::text[]) AS p(privilege)
        WHERE r.rolname = ANY(%s) AND n.nspname = 'public'
        """,
        (roles,),
    )
    rows += _query(
        connection,
        "public_schema_acl",
        """
        SELECT n.nspname, x.privilege_type, x.is_grantable
        FROM pg_catalog.pg_namespace AS n
        CROSS JOIN LATERAL pg_catalog.aclexplode(
          COALESCE(n.nspacl, pg_catalog.acldefault('n', n.nspowner))
        ) AS x
        WHERE n.nspname = 'public' AND x.grantee = 0
        """,
    )
    rows += _query(
        connection,
        "table_acl",
        """
        SELECT n.nspname, c.relname, r.rolname, privilege,
               pg_catalog.has_table_privilege(r.rolname, c.oid, privilege)
        FROM pg_catalog.pg_roles AS r
        CROSS JOIN pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        CROSS JOIN unnest(
          ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']::text[]
        ) AS p(privilege)
        WHERE r.rolname = ANY(%s)
          AND n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f')
        """,
        (roles,),
    )
    rows += _query(
        connection,
        "sequence_acl",
        """
        SELECT n.nspname, c.relname, r.rolname, privilege,
               pg_catalog.has_sequence_privilege(r.rolname, c.oid, privilege)
        FROM pg_catalog.pg_roles AS r
        CROSS JOIN pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        CROSS JOIN unnest(ARRAY['SELECT','USAGE','UPDATE']::text[]) AS p(privilege)
        WHERE r.rolname = ANY(%s)
          AND n.nspname = 'public' AND c.relkind = 'S'
        """,
        (roles,),
    )
    rows += _query(
        connection,
        "function_acl",
        """
        SELECT n.nspname, p.proname,
               pg_catalog.pg_get_function_identity_arguments(p.oid),
               r.rolname,
               pg_catalog.has_function_privilege(r.rolname, p.oid, 'EXECUTE')
        FROM pg_catalog.pg_roles AS r
        CROSS JOIN pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE r.rolname = ANY(%s) AND n.nspname = 'public'
        """,
        (roles,),
    )
    rows += _query(
        connection,
        "column_acl",
        """
        SELECT n.nspname, c.relname, a.attname, r.rolname, privilege,
               pg_catalog.has_column_privilege(
                 r.rolname, c.oid, a.attnum, privilege
               )
        FROM pg_catalog.pg_roles AS r
        CROSS JOIN pg_catalog.pg_attribute AS a
        JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        CROSS JOIN unnest(
          ARRAY['SELECT','INSERT','UPDATE','REFERENCES']::text[]
        ) AS p(privilege)
        WHERE r.rolname = ANY(%s) AND n.nspname = 'public'
          AND c.relkind IN ('r','p','v','m','f')
          AND a.attnum > 0 AND NOT a.attisdropped
        """,
        (roles,),
    )
    rows += _query(
        connection,
        "public_relation_acl",
        """
        SELECT n.nspname, c.relname, c.relkind,
               x.privilege_type, x.is_grantable
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(
          COALESCE(c.relacl, pg_catalog.acldefault(
            CASE WHEN c.relkind = 'S' THEN 's'::"char" ELSE 'r'::"char" END,
            c.relowner
          ))
        ) AS x
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r','p','S','v','m','f') AND x.grantee = 0
        """,
    )
    rows += _query(
        connection,
        "public_function_acl",
        """
        SELECT n.nspname, p.proname,
               pg_catalog.pg_get_function_identity_arguments(p.oid),
               x.privilege_type, x.is_grantable
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(
          COALESCE(p.proacl, pg_catalog.acldefault('f', p.proowner))
        ) AS x
        WHERE n.nspname = 'public' AND x.grantee = 0
        """,
    )
    rows += _query(
        connection,
        "public_column_acl",
        """
        SELECT n.nspname, c.relname, a.attname,
               x.privilege_type, x.is_grantable
        FROM pg_catalog.pg_attribute AS a
        JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) AS x
        WHERE n.nspname = 'public' AND x.grantee = 0
          AND c.relkind IN ('r','p','v','m','f')
          AND a.attnum > 0 AND NOT a.attisdropped
        """,
    )
    rows += _query(
        connection,
        "default_acl",
        """
        SELECT pg_catalog.pg_get_userbyid(d.defaclrole),
               COALESCE(n.nspname, ''), d.defaclobjtype,
               CASE WHEN x.grantee = 0 THEN 'PUBLIC'
                    ELSE pg_catalog.pg_get_userbyid(x.grantee) END,
               x.privilege_type, x.is_grantable
        FROM pg_catalog.pg_default_acl AS d
        LEFT JOIN pg_catalog.pg_namespace AS n ON n.oid = d.defaclnamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) AS x
        WHERE n.nspname = 'public' OR d.defaclnamespace = 0
        """,
    )
    return rows


def _structural_rows(
    connection: psycopg.Connection,
) -> list[tuple[object, ...]]:
    rows = _query(
        connection,
        "relation_security",
        """
        SELECT n.nspname, c.relname, c.relkind, c.relpersistence,
               c.relrowsecurity, c.relforcerowsecurity, c.relreplident
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p','S','v','m','f')
        """,
    )
    rows += _query(
        connection,
        "column",
        """
        SELECT n.nspname, c.relname, a.attnum, a.attname,
               pg_catalog.format_type(a.atttypid, a.atttypmod),
               a.attnotnull, a.attidentity, a.attgenerated
        FROM pg_catalog.pg_attribute AS a
        JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f')
          AND a.attnum > 0 AND NOT a.attisdropped
        """,
    )
    constraint_rows = _query(
        connection,
        "constraint",
        """
        SELECT n.nspname, c.relname, con.conname, con.contype,
               con.condeferrable, con.condeferred, con.convalidated,
               pg_catalog.pg_get_constraintdef(con.oid, true)
        FROM pg_catalog.pg_constraint AS con
        JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        """,
    )
    rows += [
        (*row[:-1], _canonicalize_constraint_definition(str(row[-1])))
        for row in constraint_rows
    ]
    rows += _query(
        connection,
        "index",
        """
        SELECT n.nspname, c.relname, i.relname,
               pg_catalog.pg_get_indexdef(i.oid)
        FROM pg_catalog.pg_index AS x
        JOIN pg_catalog.pg_class AS c ON c.oid = x.indrelid
        JOIN pg_catalog.pg_class AS i ON i.oid = x.indexrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        """,
    )
    rows += _query(
        connection,
        "role",
        """
        SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb,
               rolcanlogin, rolreplication, rolbypassrls, rolconnlimit
        FROM pg_catalog.pg_roles WHERE rolname = ANY(%s)
        """,
        (list(AUTHORITY_ROLES),),
    )
    rows += _query(
        connection,
        "role_membership",
        """
        SELECT parent.rolname, member.rolname, grantor.rolname, m.admin_option
        FROM pg_catalog.pg_auth_members AS m
        JOIN pg_catalog.pg_roles AS parent ON parent.oid = m.roleid
        JOIN pg_catalog.pg_roles AS member ON member.oid = m.member
        JOIN pg_catalog.pg_roles AS grantor ON grantor.oid = m.grantor
        WHERE parent.rolname = ANY(%s) OR member.rolname = ANY(%s)
        """,
        (list(AUTHORITY_ROLES), list(AUTHORITY_ROLES)),
    )
    # pg_default_acl row presence is a storage representation, not separate
    # structural authority. Its complete effective semantics (owner, schema,
    # object kind, grantee, privilege and grantability) are already blocking
    # in _effective_acl_rows. Empty/default-equivalent rows may be normalized
    # by dump/restore and belong only to the informational raw-ACL digest.
    return rows


def _function_rows(connection: psycopg.Connection) -> list[tuple[object, ...]]:
    return _query(
        connection,
        "function",
        """
        SELECT n.nspname, p.proname,
               pg_catalog.pg_get_function_identity_arguments(p.oid),
               pg_catalog.pg_get_function_result(p.oid), l.lanname,
               p.prosecdef, p.proleakproof, p.provolatile, p.proisstrict,
               p.proparallel, COALESCE(p.proconfig::text, ''),
               pg_catalog.pg_get_functiondef(p.oid)
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        JOIN pg_catalog.pg_language AS l ON l.oid = p.prolang
        WHERE n.nspname = 'public'
        """,
    )


def _trigger_policy_rows(
    connection: psycopg.Connection,
) -> list[tuple[object, ...]]:
    rows = _query(
        connection,
        "trigger",
        """
        SELECT n.nspname, c.relname, t.tgname, t.tgenabled,
               pg_catalog.pg_get_triggerdef(t.oid, true)
        FROM pg_catalog.pg_trigger AS t
        JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND NOT t.tgisinternal
        """,
    )
    rows += _query(
        connection,
        "policy",
        """
        SELECT schemaname, tablename, policyname, permissive, roles::text,
               cmd, COALESCE(qual, ''), COALESCE(with_check, '')
        FROM pg_catalog.pg_policies WHERE schemaname = 'public'
        """,
    )
    return rows


def _raw_acl_rows(connection: psycopg.Connection) -> list[tuple[object, ...]]:
    rows = _query(
        connection,
        "database",
        "SELECT datname, COALESCE(datacl::text, '') "
        "FROM pg_database WHERE datname = current_database()",
    )
    rows += _query(
        connection,
        "schema",
        "SELECT nspname, COALESCE(nspacl::text, '') FROM pg_namespace WHERE nspname = 'public'",
    )
    rows += _query(
        connection,
        "relation",
        """
        SELECT n.nspname, c.relname, c.relkind, COALESCE(c.relacl::text, '')
        FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p','S','v','m','f')
        """,
    )
    rows += _query(
        connection,
        "function",
        """
        SELECT n.nspname, p.proname,
               pg_get_function_identity_arguments(p.oid), COALESCE(p.proacl::text, '')
        FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
        """,
    )
    rows += _query(
        connection,
        "column",
        """
        SELECT n.nspname, c.relname, a.attname, COALESCE(a.attacl::text, '')
        FROM pg_attribute AS a
        JOIN pg_class AS c ON c.oid = a.attrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f')
          AND a.attnum > 0 AND NOT a.attisdropped
        """,
    )
    rows += _query(
        connection,
        "default_acl",
        """
        SELECT pg_get_userbyid(d.defaclrole), COALESCE(n.nspname, ''),
               d.defaclobjtype, COALESCE(d.defaclacl::text, '')
        FROM pg_default_acl AS d
        LEFT JOIN pg_namespace AS n ON n.oid = d.defaclnamespace
        WHERE n.nspname = 'public' OR d.defaclnamespace = 0
        """,
    )
    return rows


def _table_row_counts(connection: psycopg.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in connection.execute(
            """
            SELECT tablename FROM pg_catalog.pg_tables
            WHERE schemaname = 'public' ORDER BY tablename COLLATE "C"
            """
        ).fetchall()
    ]
    counts: dict[str, int] = {}
    for table in tables:
        statement = sql.SQL("SELECT count(*) FROM public.{}").format(
            sql.Identifier(table)
        )
        counts[table] = connection.execute(statement).fetchone()[0]
    return counts


def capture_catalog(settings: DatabaseSettings) -> CatalogSnapshot:
    with psycopg.connect(settings.conninfo()) as connection:
        groups = {
            "identity": _identity_rows(connection),
            "owner": _owner_rows(connection),
            "effective_acl": _effective_acl_rows(connection),
            "structural_security": _structural_rows(connection),
            "functions": _function_rows(connection),
            "triggers_policies": _trigger_policy_rows(connection),
        }
        raw_acl_rows = _raw_acl_rows(connection)
        row_counts = _table_row_counts(connection)
    digests: dict[str, str] = {}
    counts: dict[str, int] = {}
    subgroup_digests: dict[str, dict[str, str]] = {}
    subgroup_counts: dict[str, dict[str, int]] = {}
    for name, rows in groups.items():
        digests[name], counts[name] = _digest(rows)
        subgroup_digests[name], subgroup_counts[name] = _subgroup_digests(rows)
    raw_acl_sha256, raw_acl_count = _digest(raw_acl_rows)
    return CatalogSnapshot(
        semantic_digests=digests,
        semantic_row_counts=counts,
        semantic_subgroup_digests=subgroup_digests,
        semantic_subgroup_row_counts=subgroup_counts,
        semantic_rows={name: tuple(rows) for name, rows in groups.items()},
        raw_acl_sha256=raw_acl_sha256,
        raw_acl_row_count=raw_acl_count,
        table_row_counts=row_counts,
    )
