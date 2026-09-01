from __future__ import annotations

import hashlib
from pathlib import Path
import re


MIGRATION = Path("alembic/versions/0019_p2_security_master.py")


def source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_0019_is_serial_additive_forward_only_and_does_not_rewrite_p1() -> None:
    text = source()

    for expected in (
        'revision = "0019_p2_security_master"',
        'down_revision = "0018_p1_paper_closure_rotation"',
        "depends_on = None",
        "CREATE TABLE public.security_master_identities",
        "CREATE TABLE public.security_master_revisions",
        "exec_driver_sql(",
        'execution_options={"no_parameters": True}',
        "RuntimeError",
        "forward-only",
    ):
        assert expected in text
    assert "CREATE OR REPLACE" not in text
    assert "ingest_p1_engine_event_batch_v2" not in text


def test_tables_close_identity_lineage_temporal_and_canonical_seals() -> None:
    text = source()

    for expected in (
        "identity_id uuid PRIMARY KEY",
        "identity_kind varchar(32) NOT NULL",
        "UNIQUE (identity_id, identity_kind)",
        "revision_ordinal bigint NOT NULL",
        "effective_from timestamptz NOT NULL",
        "effective_to timestamptz",
        "known_at timestamptz NOT NULL",
        "recorded_at timestamptz NOT NULL",
        "canonical_revision jsonb NOT NULL",
        "canonical_revision_text text NOT NULL",
        "revision_digest char(64) NOT NULL",
        "ON DELETE RESTRICT",
        "UNIQUE (fact_id, revision_ordinal)",
        "security_master_one_root_per_fact_uidx",
        "security_master_one_child_per_revision_uidx",
        "security_master_revisions_subject_pit_idx",
        "security_master_revisions_symbol_pit_idx",
        "security_master_revisions_action_pit_idx",
        "security_master_revisions_export_idx",
        "ON public.security_master_revisions(recorded_at, revision_id)",
        "canonical_domain_json_string",
        "sha256",
    ):
        assert expected in text
    assert "known_to" not in text


def test_append_authority_is_bounded_canonical_linear_and_idempotent() -> None:
    text = source()
    body = text.split("CREATE FUNCTION public.append_security_master_revision", 1)[1]

    for expected in (
        "RETURNS TABLE(revision_id uuid, revision_digest text, inserted boolean)",
        "SECURITY DEFINER",
        "VOLATILE",
        "PARALLEL UNSAFE",
        "SET search_path = pg_catalog",
        "octet_length(p_canonical_revision_text) > 1048576",
        "security-master-revision-v1",
        "revision known_at must equal maximum evidence known_at",
        "pg_advisory_xact_lock",
        "RETURN QUERY SELECT v_revision_id, v_revision_digest, false",
        "parent is not the current head",
        "revision ordinal is not contiguous",
        "database knowledge time is not strictly increasing",
        "security-master-record-clock",
        "v_recorded_at := pg_catalog.clock_timestamp()",
        "transaction_isolation",
        "read committed",
        "cannot append after RETRACT",
        "RETRACT must repeat parent interval and lookup keys",
        "P2S01",
        "P2S02",
        "P2S03",
        "P2S04",
    ):
        assert expected in body
    assert body.index("octet_length(p_canonical_revision_text)") < body.index(
        "p_canonical_revision_text::pg_catalog.jsonb"
    )
    assert body.index("RETURN QUERY SELECT v_revision_id, v_revision_digest, false") < body.index(
        "parent is not the current head"
    )
    assert "v_known_at <= v_parent.known_at" not in body
    assert "v_known_at > pg_catalog.transaction_timestamp()" not in body
    assert "v_known_at > v_recorded_at" in body
    assert "recorded_at timestamptz NOT NULL DEFAULT" not in text
    assert body.index("security-master-record-clock") < body.index(
        "v_recorded_at := pg_catalog.clock_timestamp()"
    )
    assert "revision_digest, recorded_at" in body
    assert "v_revision_digest, v_recorded_at" in body


def test_tables_are_append_only_and_runtime_acl_remains_closed() -> None:
    text = source()

    for expected in (
        "security_master_identities_append_only",
        "security_master_revisions_append_only",
        "BEFORE UPDATE OR DELETE",
        "BEFORE TRUNCATE",
        "REVOKE ALL PRIVILEGES ON TABLE public.security_master_identities FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON TABLE public.security_master_revisions FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON FUNCTION public.append_security_master_revision(text) FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON FUNCTION public.append_security_master_revision(text) FROM trading_job_worker",
        "REVOKE ALL PRIVILEGES ON FUNCTION public.append_security_master_revision(text) FROM trading_job_api",
    ):
        assert expected in text
    assert "GRANT " not in text
    assert "CREATE ROLE" not in text


def test_preflight_and_postflight_use_postgresql_authority_primitives() -> None:
    text = source()

    assert "pg_catalog.current_database()" in text
    assert "nspname = 'public'" in text
    assert "current_user <> 'trading_owner'" in text
    assert "session_user <> 'trading_owner'" in text
    assert "pg_catalog.current_user" not in text
    assert "pg_catalog.session_user" not in text
    assert "pg_catalog.aclexplode" in text
    assert "pg_catalog.has_function_privilege" not in text
    assert "set_config('search_path', v_prior_search_path" not in text
    assert text.count(
        "pg_catalog.set_config('search_path', 'public, pg_catalog', true)"
    ) == 1
    assert text.rfind(
        "pg_catalog.set_config('search_path', 'public, pg_catalog', true)"
    ) > text.rfind("$p2_security_master_postflight$;")


def test_retractions_do_not_dereference_their_required_null_payload() -> None:
    body = source().split(
        "IF v_subject_kind = 'ASSET' AND NOT EXISTS", 1
    )[0].rsplit("IF v_operation = 'ASSERT' THEN", 1)

    assert len(body) == 2


def test_plpgsql_uses_real_catalog_types_constructs_and_unambiguous_columns() -> None:
    text = source()

    for invalid in (
        "pg_catalog.bigint",
        "pg_catalog.greatest",
        "pg_catalog.coalesce",
        "WHERE revision_id = v_revision_id",
        "WHERE revision_digest = v_revision_digest",
    ):
        assert invalid not in text
    assert "v_revision_ordinal pg_catalog.int8" in text
    assert "existing_revision.revision_id = v_revision_id" in text


def test_database_timestamp_spelling_matches_python_canonical_utc_bytes() -> None:
    text = source()

    assert r"T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{6})?Z$" in text
    assert r"(\.[0-9]{1,6})?Z$" not in text
    assert text.count(r"\.000000Z$") >= 4


def test_append_revalidates_exact_evidence_and_payload_unions() -> None:
    text = source()

    for expected in (
        "canonical revision evidence locator is invalid",
        "canonical revision evidence locator path is invalid",
        "ISSUER payload is invalid",
        "ASSET payload is invalid",
        "SECURITY payload is invalid",
        "VENUE payload is invalid",
        "LISTING payload is invalid",
        "SYMBOL_MAPPING payload is invalid",
        "SPLIT payload is invalid",
        "CASH_DIVIDEND payload is invalid",
        "SYMBOL_CHANGE payload is invalid",
        "DELISTING payload is invalid",
        "corporate action relation is invalid",
    ):
        assert expected in text
    assert "adjusted_price" not in text


def test_postgres_regex_bounds_remain_executable_without_weakening_legal_name() -> None:
    text = source()

    assert "'^[ -~]{1,256}$'" not in text
    assert "v_payload ->> 'legal_name' !~ '^[ -~]+$'" in text
    assert "pg_catalog.length(v_payload ->> 'legal_name') > 256" in text


def test_append_types_duplicate_roots_and_locks_retraction_lookup_keys() -> None:
    text = source()
    body = text.split("CREATE FUNCTION public.append_security_master_revision", 1)[1]

    assert "fact already has a root revision" in body
    assert body.index("fact already has a root revision") < body.index(
        "INSERT INTO public.security_master_identities"
    )
    assert body.index("IF v_operation = 'RETRACT' THEN\n            SELECT") < body.index(
        "coalesce(v_lookup_provider, '')"
    )


def test_preflight_pins_canonical_helpers_and_postflight_closes_all_objects() -> None:
    text = source()

    for expected in (
        "canonical helper authority is invalid",
        "function_row.proisstrict",
        "function_row.prolang = (",
        "function_row.prorettype = 'pg_catalog.text'::pg_catalog.regtype",
        "6dfdd6d74df0ee7300bc543788d9f58f5161c80df586ae087cdc5514a8ddf1ed",
        "c1165db17b4f6aead5fdbdd5dfffe34dbbb02390bba189a54046358ffcf732d1",
        "P2 security-master table ACL is invalid",
        "P2 security-master mutation authority is invalid",
        "P2 security-master trigger authority is invalid",
    ):
        assert expected in text


def test_canonical_helper_pins_match_the_immutable_0008_sources() -> None:
    source_migration = Path("alembic/versions/0008_trading_domain_ledger.py").read_text(
        encoding="utf-8"
    )
    current = source()

    for name in ("canonical_domain_json", "canonical_domain_json_string"):
        match = re.search(
            rf"AS \${name}\$(.*?)\${name}\$;", source_migration, re.DOTALL
        )
        assert match is not None
        digest = hashlib.sha256(match.group(1).encode("utf-8")).hexdigest()
        assert digest in current


def test_locator_validation_matches_safe_domain_exceptions_and_string_types() -> None:
    text = source()

    for expected in (
        "pg_catalog.jsonb_typeof(item) <> 'string'",
        "pg_catalog.replace(v_locator_candidate, 'tokeniz', '#')",
        "pg_catalog.replace(v_locator_candidate, 'secretar', '#')",
        "pg_catalog.replace(v_locator_candidate, 'accounting', '#')",
        "pg_catalog.strpos(v_locator_candidate, 'credential') > 0",
        "pg_catalog.strpos(v_locator_candidate, 'apikey') > 0",
        "pg_catalog.strpos(v_locator_candidate, 'ordertype') > 0",
    ):
        assert expected in text
    locator_shape = text.split("IF v_total <> 3 OR v_valid <> 3", 1)[1].split(
        "END IF;", 1
    )[0]
    assert "jsonb_array_length" not in locator_shape
    assert "IF pg_catalog.jsonb_array_length(v_locator -> 'path') NOT BETWEEN 1 AND 16 THEN" in text


def test_postflight_binds_each_guard_to_its_exact_table() -> None:
    text = source()

    for expected in (
        "trigger_row.tgname = 'security_master_identities_append_only'",
        "trigger_row.tgname = 'security_master_identities_truncate_guard'",
        "trigger_row.tgname = 'security_master_revisions_append_only'",
        "trigger_row.tgname = 'security_master_revisions_truncate_guard'",
        "trigger_row.tgrelid =\n                            'public.security_master_identities'::pg_catalog.regclass",
        "trigger_row.tgrelid =\n                            'public.security_master_revisions'::pg_catalog.regclass",
    ):
        assert expected in text
