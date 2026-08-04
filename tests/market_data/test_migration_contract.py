from __future__ import annotations

from pathlib import Path


def test_market_data_migration_is_forward_only_and_additive() -> None:
    source = Path("alembic/versions/0009_canonical_market_data.py").read_text()

    for expected in (
        'revision = "0009_canonical_market_data"',
        'down_revision = "0008_trading_domain_ledger"',
        "CREATE TABLE public.market_data_snapshots",
        "CREATE TABLE public.market_data_candles",
        "FOREIGN KEY (snapshot_id) REFERENCES public.market_data_snapshots(snapshot_id)",
        "UNIQUE (snapshot_digest)",
        "PRIMARY KEY (snapshot_id, open_time)",
        "UNIQUE (snapshot_id, source_sequence)",
        "market_data_snapshot_identity",
        "CREATE FUNCTION public.save_market_data_snapshot",
        "pg_advisory_xact_lock",
        "canonical_domain_json_string",
        "public.digest(convert_to(p_canonical_snapshot_text, 'UTF8'), 'sha256')",
        "REVOKE ALL PRIVILEGES ON TABLE public.market_data_snapshots FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON TABLE public.market_data_candles FROM PUBLIC",
        "RuntimeError",
    ):
        assert expected in source


def test_migration_keeps_exact_wire_document_with_queryable_utc_and_decimal_columns() -> None:
    source = Path("alembic/versions/0009_canonical_market_data.py").read_text()

    for expected in (
        "canonical_snapshot JSONB NOT NULL",
        "canonical_snapshot_text text NOT NULL",
        "snapshot_digest char(64) NOT NULL",
        "symbol varchar(32) NOT NULL",
        "venue varchar(32) NOT NULL",
        "product_type varchar(32) NOT NULL",
        "timeframe varchar(3) NOT NULL",
        "range_start timestamptz NOT NULL",
        "range_end timestamptz NOT NULL",
        "known_at timestamptz NOT NULL",
        "observed_at timestamptz NOT NULL",
        "fetched_at timestamptz NOT NULL",
        "raw_evidence_sha256 char(64) NOT NULL",
        "provenance_schema_version varchar(64) NOT NULL",
        "created_at timestamptz NOT NULL DEFAULT transaction_timestamp()",
        "open numeric NOT NULL",
        "high numeric NOT NULL",
        "low numeric NOT NULL",
        "close numeric NOT NULL",
        "volume numeric NOT NULL",
        "source_sequence integer NOT NULL",
        "market_data_candles_snapshot_sequence_idx",
        "market_data_snapshots_lookup_idx",
        "market_data_snapshots_digest_idx",
    ):
        assert expected in source


def test_database_write_authority_validates_storage_invariants_and_is_not_runtime_proof() -> None:
    source = Path("alembic/versions/0009_canonical_market_data.py").read_text()
    body = source.split("CREATE FUNCTION public.save_market_data_snapshot", 1)[1]

    for expected in (
        "snapshot JSON is not canonically encoded",
        "snapshot keys are invalid",
        "snapshot metadata does not match canonical document",
        "snapshot candles are structurally invalid",
        "candle violates OHLCV invariants",
        "conflicting market snapshot identity",
        "RETURN false",
        "INSERT INTO public.market_data_snapshots",
        "INSERT INTO public.market_data_candles",
        "market_data_snapshots_append_only",
        "market_data_candles_append_only",
    ):
        assert expected in body
    assert "Source-contract proof does not prove PostgreSQL runtime behavior" in source
    assert "Runtime proof requires a separately approved disposable PostgreSQL fixture" in source


def test_database_write_authority_bounds_input_before_parsing_and_mirrors_domain_safety() -> None:
    source = Path("alembic/versions/0009_canonical_market_data.py").read_text()
    body = source.split("AS $save_market_data_snapshot$", 1)[1]

    assert "octet_length(p_canonical_snapshot_text) > 8388608" in body
    assert body.index("octet_length(p_canonical_snapshot_text)") < body.index(
        "p_canonical_snapshot_text::jsonb"
    )
    assert "regexp_replace(v_provider, '[^a-z0-9]', '', 'g')" in body
    for prohibited in ("account", "broker", "execution", "order", "token"):
        assert f"'{prohibited}'" in body
    assert "mod(extract(epoch FROM v_open_time), v_interval) <> 0" in body
    assert "mod(floor(extract(epoch FROM v_open_time))" not in body
    assert "snapshot timestamps must use canonical UTC encoding" in body
    assert "YYYY-MM-DD\"T\"HH24:MI:SS" in body
    assert "to_char(v_open_time AT TIME ZONE 'UTC', 'US')" in body
    assert "'|' || (v_snapshot ->> 'known_at') || '|'" in body
    assert "'|' || v_snapshot ->> 'known_at' || '|'" not in body


def test_candle_timestamp_canonicality_is_checked_before_snapshot_hashing() -> None:
    source = Path("alembic/versions/0009_canonical_market_data.py").read_text()
    body = source.split("AS $save_market_data_snapshot$", 1)[1]

    assert body.index("v_digest := encode(") > body.index(
        "IF (v_candle ->> 'open_time') IS DISTINCT FROM ("
    )


def test_database_write_authority_is_function_only_and_not_invoker_dml() -> None:
    source = Path("alembic/versions/0009_canonical_market_data.py").read_text()
    authority = source.split(
        "CREATE FUNCTION public.save_market_data_snapshot",
        maxsplit=1,
    )[1].split("CREATE FUNCTION public.market_data_snapshots_append_only", maxsplit=1)[0]

    assert "SECURITY DEFINER" in authority
    assert "SECURITY INVOKER" not in authority
    assert "VOLATILE" in authority
    assert "PARALLEL UNSAFE" in authority
    assert "SET search_path = pg_catalog" in authority
    assert "SET search_path = pg_catalog, public" not in authority
    assert (
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.save_market_data_snapshot(text) FROM PUBLIC"
    ) in source
    assert "GRANT INSERT ON TABLE public.market_data_snapshots" not in source
    assert "GRANT INSERT ON TABLE public.market_data_candles" not in source
