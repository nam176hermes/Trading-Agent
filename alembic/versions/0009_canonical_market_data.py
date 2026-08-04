"""Add immutable canonical market-data snapshot persistence.

Revision ID: 0009_canonical_market_data
Revises: 0008_trading_domain_ledger
"""
from __future__ import annotations

from alembic import op


revision = "0009_canonical_market_data"
down_revision = "0008_trading_domain_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        -- Source-contract proof does not prove PostgreSQL runtime behavior.
        -- Runtime proof requires a separately approved disposable PostgreSQL fixture.
        CREATE TABLE public.market_data_snapshots (
          snapshot_id uuid PRIMARY KEY DEFAULT public.gen_random_uuid(),
          canonical_snapshot JSONB NOT NULL,
          canonical_snapshot_text text NOT NULL,
          snapshot_digest char(64) NOT NULL,
          symbol varchar(32) NOT NULL CHECK (symbol ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'),
          venue varchar(32) NOT NULL CHECK (venue ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'),
          product_type varchar(32) NOT NULL CHECK (product_type IN ('crypto_spot', 'equity')),
          timeframe varchar(3) NOT NULL CHECK (timeframe IN ('1m', '5m', '15m', '1h', '4h', '1d')),
          range_start timestamptz NOT NULL,
          range_end timestamptz NOT NULL,
          known_at timestamptz NOT NULL,
          observed_at timestamptz NOT NULL,
          fetched_at timestamptz NOT NULL,
          provider varchar(64) NOT NULL CHECK (provider ~ '^[a-z0-9][a-z0-9.-]{0,63}$'),
          raw_evidence_sha256 char(64) NOT NULL,
          schema_version varchar(64) NOT NULL CHECK (schema_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
          provenance_schema_version varchar(64) NOT NULL CHECK (provenance_schema_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
          normalization_version varchar(64) NOT NULL CHECK (normalization_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
          created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CHECK (snapshot_digest ~ '^[0-9a-f]{64}$'),
          CHECK (raw_evidence_sha256 ~ '^[0-9a-f]{64}$'),
          CHECK (range_start < range_end),
          CHECK (observed_at <= fetched_at AND fetched_at <= known_at),
          CHECK (canonical_snapshot_text::jsonb = canonical_snapshot),
          CHECK (canonical_snapshot_text = public.canonical_domain_json_string(canonical_snapshot_text)),
          CHECK (snapshot_digest = encode(public.digest(convert_to(canonical_snapshot_text, 'UTF8'), 'sha256'), 'hex')),
          UNIQUE (snapshot_digest),
          CONSTRAINT market_data_snapshot_identity UNIQUE (
            symbol, venue, product_type, timeframe, range_start, range_end, known_at,
            observed_at, fetched_at, provider, raw_evidence_sha256, schema_version,
            provenance_schema_version, normalization_version
          )
        );
        CREATE INDEX market_data_snapshots_lookup_idx ON public.market_data_snapshots
          (symbol, venue, product_type, timeframe, range_end DESC, known_at DESC);
        CREATE INDEX market_data_snapshots_digest_idx ON public.market_data_snapshots (snapshot_digest);

        CREATE TABLE public.market_data_candles (
          snapshot_id uuid NOT NULL,
          source_sequence integer NOT NULL CHECK (source_sequence >= 0),
          open_time timestamptz NOT NULL,
          open numeric NOT NULL,
          high numeric NOT NULL,
          low numeric NOT NULL,
          close numeric NOT NULL,
          volume numeric NOT NULL,
          PRIMARY KEY (snapshot_id, open_time),
          UNIQUE (snapshot_id, source_sequence),
          FOREIGN KEY (snapshot_id) REFERENCES public.market_data_snapshots(snapshot_id) ON DELETE RESTRICT,
          CHECK (open > 0 AND high > 0 AND low > 0 AND close > 0),
          CHECK (volume >= 0),
          CHECK (high >= GREATEST(open, low, close)),
          CHECK (low <= LEAST(open, high, close))
        );
        CREATE INDEX market_data_candles_snapshot_sequence_idx ON public.market_data_candles
          (snapshot_id, source_sequence);

        CREATE FUNCTION public.save_market_data_snapshot(p_canonical_snapshot_text text)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $save_market_data_snapshot$
        DECLARE
          v_snapshot jsonb;
          v_instrument jsonb;
          v_provenance jsonb;
          v_candle jsonb;
          v_total bigint;
          v_valid bigint;
          v_ordinal bigint;
          v_snapshot_id uuid;
          v_digest char(64);
          v_exact_retry boolean;
          v_symbol text;
          v_venue text;
          v_product_type text;
          v_timeframe text;
          v_interval integer;
          v_range_start timestamptz;
          v_range_end timestamptz;
          v_previous_open timestamptz;
          v_known_at timestamptz;
          v_observed_at timestamptz;
          v_fetched_at timestamptz;
          v_provider text;
          v_provider_compact text;
          v_raw_evidence_sha256 text;
          v_schema_version text;
          v_provenance_schema_version text;
          v_normalization_version text;
          v_open_time timestamptz;
          v_open_text text;
          v_high_text text;
          v_low_text text;
          v_close_text text;
          v_volume_text text;
          v_open numeric;
          v_high numeric;
          v_low numeric;
          v_close numeric;
          v_volume numeric;
          v_decimal_pattern text := '^(?:[1-9][0-9]*|[1-9][0-9]*\.[0-9]*[1-9]|0\.0{0,127}(?:[1-9]|[1-9][0-9]{0,126}[1-9]))$';
          v_timestamp_pattern text := '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$';
        BEGIN
          IF p_canonical_snapshot_text IS NULL
             OR octet_length(p_canonical_snapshot_text) > 8388608 THEN
            RAISE EXCEPTION 'snapshot canonical JSON exceeds the supported bound' USING ERRCODE = '23514';
          END IF;
          v_snapshot := p_canonical_snapshot_text::jsonb;
          IF p_canonical_snapshot_text IS DISTINCT FROM public.canonical_domain_json_string(p_canonical_snapshot_text) THEN
            RAISE EXCEPTION 'snapshot JSON is not canonically encoded' USING ERRCODE = '23514';
          END IF;
          IF jsonb_typeof(v_snapshot) <> 'object' THEN
            RAISE EXCEPTION 'snapshot keys are invalid' USING ERRCODE = '23514';
          END IF;
          SELECT count(*), count(*) FILTER (WHERE key = ANY (ARRAY['candles','instrument','known_at','normalization_version','provenance','schema_version','timeframe']))
            INTO v_total, v_valid FROM jsonb_object_keys(v_snapshot) AS keys(key);
          IF v_total <> 7 OR v_valid <> 7 THEN
            RAISE EXCEPTION 'snapshot keys are invalid' USING ERRCODE = '23514';
          END IF;
          v_instrument := v_snapshot -> 'instrument';
          v_provenance := v_snapshot -> 'provenance';
          IF jsonb_typeof(v_instrument) <> 'object' OR jsonb_typeof(v_provenance) <> 'object'
             OR jsonb_typeof(v_snapshot -> 'candles') <> 'array'
             OR jsonb_typeof(v_snapshot -> 'timeframe') <> 'string'
             OR jsonb_typeof(v_snapshot -> 'known_at') <> 'string'
             OR jsonb_typeof(v_snapshot -> 'schema_version') <> 'string'
             OR jsonb_typeof(v_snapshot -> 'normalization_version') <> 'string'
             OR jsonb_typeof(v_instrument -> 'symbol') <> 'string'
             OR jsonb_typeof(v_instrument -> 'venue') <> 'string'
             OR jsonb_typeof(v_instrument -> 'product_type') <> 'string'
             OR jsonb_typeof(v_provenance -> 'provider') <> 'string'
             OR jsonb_typeof(v_provenance -> 'observed_at') <> 'string'
             OR jsonb_typeof(v_provenance -> 'fetched_at') <> 'string'
             OR jsonb_typeof(v_provenance -> 'raw_evidence_sha256') <> 'string'
             OR jsonb_typeof(v_provenance -> 'schema_version') <> 'string'
             OR jsonb_typeof(v_provenance -> 'normalization_version') <> 'string' THEN
            RAISE EXCEPTION 'snapshot metadata does not match canonical document' USING ERRCODE = '23514';
          END IF;
          SELECT count(*), count(*) FILTER (WHERE key = ANY (ARRAY['product_type','symbol','venue']))
            INTO v_total, v_valid FROM jsonb_object_keys(v_instrument) AS keys(key);
          IF v_total <> 3 OR v_valid <> 3 THEN RAISE EXCEPTION 'snapshot metadata does not match canonical document' USING ERRCODE = '23514'; END IF;
          SELECT count(*), count(*) FILTER (WHERE key = ANY (ARRAY['fetched_at','normalization_version','observed_at','provider','raw_evidence_sha256','schema_version']))
            INTO v_total, v_valid FROM jsonb_object_keys(v_provenance) AS keys(key);
          IF v_total <> 6 OR v_valid <> 6 THEN RAISE EXCEPTION 'snapshot metadata does not match canonical document' USING ERRCODE = '23514'; END IF;
          v_symbol := v_instrument ->> 'symbol'; v_venue := v_instrument ->> 'venue'; v_product_type := v_instrument ->> 'product_type';
          v_timeframe := v_snapshot ->> 'timeframe'; v_provider := v_provenance ->> 'provider';
          v_provider_compact := regexp_replace(v_provider, '[^a-z0-9]', '', 'g');
          v_raw_evidence_sha256 := v_provenance ->> 'raw_evidence_sha256'; v_schema_version := v_snapshot ->> 'schema_version'; v_provenance_schema_version := v_provenance ->> 'schema_version';
          v_normalization_version := v_snapshot ->> 'normalization_version';
          IF v_symbol !~ '^[A-Z0-9][A-Z0-9._-]{0,31}$' OR v_venue !~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'
             OR v_product_type NOT IN ('crypto_spot','equity') OR v_timeframe NOT IN ('1m','5m','15m','1h','4h','1d')
             OR v_provider !~ '^[a-z0-9][a-z0-9.-]{0,63}$' OR v_raw_evidence_sha256 !~ '^[0-9a-f]{64}$'
             OR EXISTS (
               SELECT 1
               FROM unnest(ARRAY[
                 'account','apikey','authorization','balance','broker','credential',
                 'execution','execute','order','password','position','routing',
                 'secret','token'
               ]) AS prohibited(term)
               WHERE position(term IN v_provider_compact) > 0
             )
             OR v_schema_version !~ '^[a-z0-9][a-z0-9._-]{0,63}$' OR v_provenance_schema_version !~ '^[a-z0-9][a-z0-9._-]{0,63}$' OR v_normalization_version !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
             OR v_normalization_version IS DISTINCT FROM v_provenance ->> 'normalization_version'
             THEN
            RAISE EXCEPTION 'snapshot metadata does not match canonical document' USING ERRCODE = '23514';
          END IF;
          IF (v_snapshot ->> 'known_at') !~ v_timestamp_pattern OR (v_provenance ->> 'observed_at') !~ v_timestamp_pattern OR (v_provenance ->> 'fetched_at') !~ v_timestamp_pattern THEN
            RAISE EXCEPTION 'snapshot metadata timestamps must be UTC' USING ERRCODE = '23514';
          END IF;
          v_known_at := (v_snapshot ->> 'known_at')::timestamptz; v_observed_at := (v_provenance ->> 'observed_at')::timestamptz; v_fetched_at := (v_provenance ->> 'fetched_at')::timestamptz;
          IF (v_snapshot ->> 'known_at') IS DISTINCT FROM (
               to_char(v_known_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
               || CASE WHEN extract(microseconds FROM v_known_at)::bigint % 1000000 = 0
                       THEN 'Z'
                       ELSE '.' || to_char(v_known_at AT TIME ZONE 'UTC', 'US') || 'Z'
                  END
             )
             OR (v_provenance ->> 'observed_at') IS DISTINCT FROM (
               to_char(v_observed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
               || CASE WHEN extract(microseconds FROM v_observed_at)::bigint % 1000000 = 0
                       THEN 'Z'
                       ELSE '.' || to_char(v_observed_at AT TIME ZONE 'UTC', 'US') || 'Z'
                  END
             )
             OR (v_provenance ->> 'fetched_at') IS DISTINCT FROM (
               to_char(v_fetched_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
               || CASE WHEN extract(microseconds FROM v_fetched_at)::bigint % 1000000 = 0
                       THEN 'Z'
                       ELSE '.' || to_char(v_fetched_at AT TIME ZONE 'UTC', 'US') || 'Z'
                  END
             ) THEN
            RAISE EXCEPTION 'snapshot timestamps must use canonical UTC encoding' USING ERRCODE = '23514';
          END IF;
          IF v_observed_at > v_fetched_at OR v_fetched_at > v_known_at THEN RAISE EXCEPTION 'snapshot metadata timestamps are out of order' USING ERRCODE = '23514'; END IF;
          v_interval := CASE v_timeframe WHEN '1m' THEN 60 WHEN '5m' THEN 300 WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END;
          IF jsonb_array_length(v_snapshot -> 'candles') NOT BETWEEN 1 AND 4096 THEN RAISE EXCEPTION 'snapshot candles are structurally invalid' USING ERRCODE = '23514'; END IF;
          FOR v_candle, v_ordinal IN SELECT item, ordinal FROM jsonb_array_elements(v_snapshot -> 'candles') WITH ORDINALITY AS entries(item, ordinal) LOOP
            IF jsonb_typeof(v_candle) <> 'object' OR v_candle -> 'instrument' IS DISTINCT FROM v_instrument OR v_candle ->> 'timeframe' IS DISTINCT FROM v_timeframe THEN RAISE EXCEPTION 'snapshot candles are structurally invalid' USING ERRCODE = '23514'; END IF;
            SELECT count(*), count(*) FILTER (WHERE key = ANY (ARRAY['close','high','instrument','low','open','open_time','timeframe','volume'])) INTO v_total, v_valid FROM jsonb_object_keys(v_candle) AS keys(key);
            IF v_total <> 8 OR v_valid <> 8 OR jsonb_typeof(v_candle -> 'open_time') <> 'string' OR jsonb_typeof(v_candle -> 'open') <> 'string' OR jsonb_typeof(v_candle -> 'high') <> 'string' OR jsonb_typeof(v_candle -> 'low') <> 'string' OR jsonb_typeof(v_candle -> 'close') <> 'string' OR jsonb_typeof(v_candle -> 'volume') <> 'string' OR (v_candle ->> 'open_time') !~ v_timestamp_pattern THEN RAISE EXCEPTION 'snapshot candles are structurally invalid' USING ERRCODE = '23514'; END IF;
            v_open_time := (v_candle ->> 'open_time')::timestamptz; v_open_text := v_candle ->> 'open'; v_high_text := v_candle ->> 'high'; v_low_text := v_candle ->> 'low'; v_close_text := v_candle ->> 'close'; v_volume_text := v_candle ->> 'volume';
            IF (v_candle ->> 'open_time') IS DISTINCT FROM (
                 to_char(v_open_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
                 || CASE WHEN extract(microseconds FROM v_open_time)::bigint % 1000000 = 0
                         THEN 'Z'
                         ELSE '.' || to_char(v_open_time AT TIME ZONE 'UTC', 'US') || 'Z'
                    END
               ) THEN
              RAISE EXCEPTION 'snapshot timestamps must use canonical UTC encoding' USING ERRCODE = '23514';
            END IF;
            IF v_open_text !~ v_decimal_pattern OR v_high_text !~ v_decimal_pattern OR v_low_text !~ v_decimal_pattern OR v_close_text !~ v_decimal_pattern OR (v_volume_text <> '0' AND v_volume_text !~ v_decimal_pattern) OR length(ltrim(replace(v_open_text,'.',''), '0')) > 128 OR length(ltrim(replace(v_high_text,'.',''), '0')) > 128 OR length(ltrim(replace(v_low_text,'.',''), '0')) > 128 OR length(ltrim(replace(v_close_text,'.',''), '0')) > 128 OR length(ltrim(replace(v_volume_text,'.',''), '0')) > 128 THEN RAISE EXCEPTION 'candle violates canonical decimal grammar' USING ERRCODE = '23514'; END IF;
            v_open := v_open_text::numeric; v_high := v_high_text::numeric; v_low := v_low_text::numeric; v_close := v_close_text::numeric; v_volume := v_volume_text::numeric;
            IF v_open <= 0 OR v_high < GREATEST(v_open,v_low,v_close) OR v_low > LEAST(v_open,v_high,v_close) OR v_volume < 0 OR mod(extract(epoch FROM v_open_time), v_interval) <> 0 OR v_open_time + make_interval(secs => v_interval) > v_observed_at OR (v_previous_open IS NOT NULL AND v_open_time <= v_previous_open) THEN RAISE EXCEPTION 'candle violates OHLCV invariants' USING ERRCODE = '23514'; END IF;
            IF v_ordinal = 1 THEN v_range_start := v_open_time; END IF; v_previous_open := v_open_time; v_range_end := v_open_time + make_interval(secs => v_interval);
          END LOOP;
          v_digest := encode(public.digest(convert_to(p_canonical_snapshot_text, 'UTF8'), 'sha256'), 'hex');
          PERFORM pg_advisory_xact_lock(hashtextextended(v_symbol || '|' || v_venue || '|' || v_product_type || '|' || v_timeframe || '|' || (v_snapshot ->> 'known_at') || '|' || v_provider || '|' || v_raw_evidence_sha256, 9));
          SELECT snapshot_id, canonical_snapshot_text = p_canonical_snapshot_text INTO v_snapshot_id, v_exact_retry FROM public.market_data_snapshots WHERE snapshot_digest = v_digest;
          IF FOUND THEN IF v_exact_retry THEN RETURN false; END IF; RAISE EXCEPTION 'conflicting market snapshot digest %', v_digest USING ERRCODE = '23505'; END IF;
          SELECT canonical_snapshot_text = p_canonical_snapshot_text INTO v_exact_retry FROM public.market_data_snapshots WHERE symbol = v_symbol AND venue = v_venue AND product_type = v_product_type AND timeframe = v_timeframe AND range_start = v_range_start AND range_end = v_range_end AND known_at = v_known_at AND observed_at = v_observed_at AND fetched_at = v_fetched_at AND provider = v_provider AND raw_evidence_sha256 = v_raw_evidence_sha256 AND schema_version = v_schema_version AND provenance_schema_version = v_provenance_schema_version AND normalization_version = v_normalization_version;
          IF FOUND THEN IF v_exact_retry THEN RETURN false; END IF; RAISE EXCEPTION 'conflicting market snapshot identity' USING ERRCODE = '23505'; END IF;
          INSERT INTO public.market_data_snapshots (canonical_snapshot, canonical_snapshot_text, snapshot_digest, symbol, venue, product_type, timeframe, range_start, range_end, known_at, observed_at, fetched_at, provider, raw_evidence_sha256, schema_version, provenance_schema_version, normalization_version) VALUES (v_snapshot, p_canonical_snapshot_text, v_digest, v_symbol, v_venue, v_product_type, v_timeframe, v_range_start, v_range_end, v_known_at, v_observed_at, v_fetched_at, v_provider, v_raw_evidence_sha256, v_schema_version, v_provenance_schema_version, v_normalization_version) RETURNING snapshot_id INTO v_snapshot_id;
          FOR v_candle, v_ordinal IN SELECT item, ordinal FROM jsonb_array_elements(v_snapshot -> 'candles') WITH ORDINALITY AS entries(item, ordinal) LOOP
            INSERT INTO public.market_data_candles (snapshot_id, source_sequence, open_time, open, high, low, close, volume) VALUES (v_snapshot_id, v_ordinal - 1, (v_candle ->> 'open_time')::timestamptz, (v_candle ->> 'open')::numeric, (v_candle ->> 'high')::numeric, (v_candle ->> 'low')::numeric, (v_candle ->> 'close')::numeric, (v_candle ->> 'volume')::numeric);
          END LOOP;
          RETURN true;
        END;
        $save_market_data_snapshot$;

        CREATE FUNCTION public.market_data_snapshots_append_only() RETURNS trigger LANGUAGE plpgsql AS $append_only$
        BEGIN RAISE EXCEPTION 'market_data_snapshots is immutable'; END; $append_only$;
        CREATE TRIGGER market_data_snapshots_append_only BEFORE UPDATE OR DELETE ON public.market_data_snapshots FOR EACH ROW EXECUTE FUNCTION public.market_data_snapshots_append_only();
        CREATE TRIGGER market_data_snapshots_reject_truncate BEFORE TRUNCATE ON public.market_data_snapshots FOR EACH STATEMENT EXECUTE FUNCTION public.market_data_snapshots_append_only();
        CREATE FUNCTION public.market_data_candles_append_only() RETURNS trigger LANGUAGE plpgsql AS $append_only$
        BEGIN RAISE EXCEPTION 'market_data_candles is immutable'; END; $append_only$;
        CREATE TRIGGER market_data_candles_append_only BEFORE UPDATE OR DELETE ON public.market_data_candles FOR EACH ROW EXECUTE FUNCTION public.market_data_candles_append_only();
        CREATE TRIGGER market_data_candles_reject_truncate BEFORE TRUNCATE ON public.market_data_candles FOR EACH STATEMENT EXECUTE FUNCTION public.market_data_candles_append_only();

        REVOKE ALL PRIVILEGES ON TABLE public.market_data_snapshots FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON TABLE public.market_data_candles FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.save_market_data_snapshot(text) FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.market_data_snapshots_append_only() FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.market_data_candles_append_only() FROM PUBLIC;
        -- Role grants are intentionally absent until reviewed Runtime Authority activation.
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0009 canonical market-data persistence is forward-only; use a reviewed forward repair"
    )
