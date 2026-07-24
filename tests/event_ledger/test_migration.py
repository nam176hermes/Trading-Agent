from __future__ import annotations

from pathlib import Path
import re


def test_ledger_migration_is_chained_and_has_append_only_schema_authority() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()
    required = (
        'revision = "0008_trading_domain_ledger"', 'down_revision = "0007_job_event_chain_authority"',
        "domain_events", "event_outbox", "consumer_inbox", "aggregate_snapshots", "JSONB",
        "UNIQUE (stream_id, sequence)", "CHECK (digest ~", "CREATE TRIGGER", "append_only",
        "REVOKE ALL PRIVILEGES ON TABLE", "REVOKE ALL PRIVILEGES ON TABLE public.domain_events FROM PUBLIC",
        "FOREIGN KEY (event_id) REFERENCES public.domain_events(event_id)", "PRIMARY KEY (state_hash)",
        "RuntimeError",
    )
    for expected in required:
        assert expected in source


def test_atomic_append_function_preserves_canonical_text_and_locks_streams() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()
    required = (
        "CREATE FUNCTION public.append_domain_event",
        "pg_advisory_xact_lock",
        "expected sequence",
        "conflicting duplicate event",
        "canonical_event_text",
        "canonical_state_json",
        "replay_schema_version",
        "reducer_version",
        "canonical_event_text::jsonb = canonical_event",
        "SET search_path = pg_catalog, public",
        "REVOKE ALL PRIVILEGES ON FUNCTION public.append_domain_event",
    )
    for expected in required:
        assert expected in source
    assert "jsonb::text" not in source
    assert "CREATE INDEX public." not in source


def test_database_computes_event_digest_and_preserves_outbox_payload_bytes() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()

    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public" in source
    assert "p_digest" not in source
    assert "v_digest char(64)" in source
    assert "encode(public.digest(convert_to(p_canonical_event_text, 'UTF8'), 'sha256'), 'hex')" in source
    assert "payload_text text NOT NULL" in source
    assert "CHECK (payload_text::jsonb = payload)" in source
    assert "request_digest = v_request_digest" in source
    assert "INSERT INTO public.event_outbox (event_id, topic, payload, payload_text)" in source
    assert "v_event := p_canonical_event_text::jsonb" in source
    assert "v_event ->> 'event_id' IS DISTINCT FROM p_event_id::text" in source
    assert "v_event ->> 'stream_id' IS DISTINCT FROM p_stream_id::text" in source
    assert "v_event ->> 'sequence' IS DISTINCT FROM p_sequence::text" in source
    assert "v_event ->> 'event_type' IS DISTINCT FROM p_event_type" in source
    assert "event envelope metadata does not match append arguments" in source


def test_append_retry_identity_is_independent_of_mutable_delivery_state() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()
    append_body = source.split(
        "CREATE FUNCTION public.append_domain_event(", 1
    )[1].split("$append_domain_event$;", 1)[0]
    retry_section = append_body.split("IF FOUND THEN", 1)[0]

    assert "CREATE TABLE public.event_append_idempotency" in source
    assert "request_digest char(64)" in source
    assert "event_append_idempotency_append_only" in source
    assert "FROM public.event_append_idempotency" in retry_section
    assert "request_digest = v_request_digest" in retry_section
    assert "JOIN public.event_outbox" not in retry_section
    assert "INSERT INTO public.event_append_idempotency" in append_body
    for argument in (
        "p_sequence::text",
        "p_event_type",
        "p_canonical_event_text",
        "p_topic",
        "p_payload_json",
    ):
        assert f"octet_length({argument})" in append_body
    assert "octet_length(uuid_send(p_event_id))" in append_body
    assert "octet_length(uuid_send(p_stream_id))" in append_body


def test_outbox_uses_atomic_retention_delete_with_immutable_receipts() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()
    acknowledge_body = source.split(
        "CREATE FUNCTION public.acknowledge_domain_publication(", 1
    )[1].split("$acknowledge_domain_publication$;", 1)[0]

    assert "CREATE TABLE public.event_publications" in source
    assert "event_publications_append_only" in source
    assert "event_outbox_reject_update" in source
    assert "event_outbox_require_publication_receipt" in source
    assert "event_outbox delete requires durable publication receipt" in source
    assert "INSERT INTO public.event_publications" in acknowledge_body
    assert "DELETE FROM public.event_outbox" in acknowledge_body
    assert acknowledge_body.index("INSERT INTO public.event_publications") < acknowledge_body.index(
        "DELETE FROM public.event_outbox"
    )
    assert "SECURITY DEFINER" in acknowledge_body
    assert "SET search_path = pg_catalog, public" in acknowledge_body
    assert "REVOKE ALL PRIVILEGES ON FUNCTION public.acknowledge_domain_publication" in source


def test_inbox_claims_are_append_only_without_source_retention_escape() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()

    assert "claimed_at timestamptz NOT NULL" in source
    assert "consumer_inbox_append_only" in source
    assert "consumer_inbox is append-only" in source
    assert "BEFORE TRUNCATE ON public.consumer_inbox" in source
    assert "BEFORE TRUNCATE ON public.event_append_idempotency" in source
    assert "BEFORE TRUNCATE ON public.event_publications" in source
    assert "BEFORE TRUNCATE ON public.event_outbox" in source
    assert "BEFORE TRUNCATE ON public.domain_events" in source
    assert "BEFORE TRUNCATE ON public.aggregate_snapshots" in source
    assert "release_inbox" not in source
    assert "DELETE FROM public.consumer_inbox" not in source


def test_delivery_durability_adr_locks_lifecycle_retention_and_acl_matrix() -> None:
    adr = Path("docs/adr/0003-event-ledger-delivery-durability.md").read_text()

    required = (
        "retention/delete model",
        "event_append_idempotency",
        "event_publications",
        "same lifetime as `domain_events`",
        "Owner |",
        "Writer |",
        "Publisher |",
        "Consumer |",
        "Runtime Authority not activated",
    )
    for expected in required:
        assert expected in adr


def test_database_rejects_noncanonical_event_outbox_and_snapshot_bytes() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()

    assert "CREATE FUNCTION public.canonical_domain_json" in source
    assert "CREATE FUNCTION public.canonical_domain_json_string" in source
    assert "canonical_event_text = public.canonical_domain_json_string(canonical_event_text)" in source
    assert "payload_text = public.canonical_domain_json_string(payload_text)" in source
    assert "canonical_state_json = public.canonical_domain_json_string(canonical_state_json)" in source
    assert "p_canonical_event_text IS DISTINCT FROM public.canonical_domain_json_string(p_canonical_event_text)" in source
    assert "p_payload_json IS DISTINCT FROM public.canonical_domain_json_string(p_payload_json)" in source
    assert "p_canonical_state_json IS DISTINCT FROM public.canonical_domain_json_string(p_canonical_state_json)" in source
    assert "v_codepoint := ascii(v_character)" in source
    assert "unicode(" not in source
    assert "fractional JSON numbers are not canonical domain values" in source
    assert "REVOKE ALL PRIVILEGES ON FUNCTION public.canonical_domain_json(jsonb) FROM PUBLIC" in source
    assert "REVOKE ALL PRIVILEGES ON FUNCTION public.canonical_domain_json_string(text) FROM PUBLIC" in source


def test_canonical_json_quote_escape_and_plpgsql_bodies_are_parseable() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()

    assert "v_result := v_result || chr(92) || chr(34);" in source
    assert not re.search(r"^\s*END\s*$\n\s*\$[a-z_]+\$;", source, flags=re.MULTILINE)
    assert len(re.findall(r"^\s*END;\s*$\n\s*\$[a-z_]+\$;", source, flags=re.MULTILINE)) == 12


def test_snapshot_wrapper_and_write_function_are_database_verified() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()

    assert "CHECK ((canonical_state_json::jsonb -> 'state') = state)" in source
    assert "CHECK ((canonical_state_json::jsonb ->> 'schema_version') = replay_schema_version)" in source
    assert "CHECK ((canonical_state_json::jsonb ->> 'reducer_version') = reducer_version)" in source
    assert "CHECK (state_hash = encode(public.digest(convert_to(canonical_state_json, 'UTF8'), 'sha256'), 'hex'))" in source
    assert "CHECK (replay_schema_version = 'event-ledger-replay-v1')" in source
    assert "CHECK (reducer_version = 'event-ledger-reducer-v1')" in source
    assert "CREATE FUNCTION public.save_domain_snapshot(" in source
    assert "p_canonical_state_json text" in source
    assert "encode(public.digest(convert_to(p_canonical_state_json, 'UTF8'), 'sha256'), 'hex')" in source
    assert "s.canonical_state_json = p_canonical_state_json" in source
    assert "conflicting duplicate snapshot" in source
    assert "SECURITY INVOKER" in source
    assert "REVOKE ALL PRIVILEGES ON FUNCTION public.save_domain_snapshot" in source
    assert "CREATE TABLE public.aggregate_snapshots" in source
    assert "PRIMARY KEY (state_hash)" in source
    assert "p_stream_id" not in _snapshot_function(source)
    assert "p_last_sequence" not in _snapshot_function(source)
    assert "FROM public.aggregate_snapshots AS s" in source
    assert "WHERE s.state_hash = v_state_hash" in source


def _snapshot_function(source: str) -> str:
    return source.split("CREATE FUNCTION public.save_domain_snapshot(", 1)[1].split(
        "$save_domain_snapshot$;", 1
    )[0]


def test_snapshot_sql_source_contract_mirrors_python_invariants() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()
    body = _snapshot_function(source)
    validation = body.split("INSERT INTO public.aggregate_snapshots", 1)[0]

    required = (
        "jsonb_object_keys(v_snapshot)",
        "snapshot wrapper keys are invalid",
        "jsonb_object_keys(v_state)",
        "snapshot state keys are invalid",
        "jsonb_array_length(v_state -> 'applied_events')",
        "snapshot event_count does not match applied events",
        "snapshot applied events are not unique and canonically ordered",
        "snapshot type counts do not match event_count",
        "snapshot type counts are not unique, registered, and canonically ordered",
        "SignalProposal", "TargetPortfolio", "RiskDecision",
        "OrderIntent", "OrderEvent", "FillEvent",
        "snapshot stream counts do not match event_count",
        "snapshot streams are not unique and canonically ordered",
        "snapshot stream projection is structurally inconsistent",
        "snapshot issues are not unique and canonically ordered",
        "snapshot issue is also marked applied",
        "snapshot replay issue is structurally inconsistent",
        "snapshot status does not match replay issues",
        "9223372036854775807",
        "COLLATE \"C\"",
    )
    for expected in required:
        assert expected in validation

    assert "stream_snapshots" not in body
    assert "p_stream_id" not in body
    assert "p_last_sequence" not in body
    assert "floor(v_codepoint::numeric / 1024)::integer" in source
    assert "mod(v_codepoint, 1024)" in source


def test_snapshot_source_proof_is_explicitly_not_runtime_database_proof() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()

    assert "Source-contract proof does not prove PostgreSQL runtime behavior" in source
    assert "Runtime proof requires a separately approved disposable PostgreSQL fixture" in source
    test_source = Path(__file__).read_text()
    assert ("import " + "psycopg") not in test_source
    assert ("disposable" + "_database") not in test_source


def test_snapshot_runtime_postgres_proof_is_separate_and_opt_in() -> None:
    root = Path(__file__).parents[2]
    runtime_test = (
        root / "tests/event_ledger/test_snapshot_postgres_runtime.py"
    ).read_text()
    makefile = (root / "Makefile").read_text()

    assert "pytestmark = pytest.mark.runtime_postgres" in runtime_test
    assert 'EXACT_0008_HEAD = "0008_trading_domain_ledger"' in runtime_test
    assert (
        "disposable" + "_database(operation_id=OPERATION_ID, planned=True)"
    ) in runtime_test
    assert "test-event-ledger-runtime-postgres:" in makefile
    assert "tests/event_ledger/test_snapshot_postgres_runtime.py" in makefile
    assert "test_runtime_postgres_retry_survives_publish_retention_and_inbox_claim_is_permanent" in runtime_test


def test_migration_records_non_operational_privilege_intent() -> None:
    source = Path("alembic/versions/0008_trading_domain_ledger.py").read_text()

    assert "grants are intentionally absent until reviewed Runtime Authority activation" in source
    assert "grant EXECUTE on the canonical helpers and write wrappers" in source
    assert "grant the SECURITY INVOKER table privileges" in source
    assert "GRANT " not in source
