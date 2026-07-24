"""Add the immutable canonical trading-domain event ledger.

Revision ID: 0008_trading_domain_ledger
Revises: 0007_job_event_chain_authority
"""
from __future__ import annotations

from alembic import op


revision = "0008_trading_domain_ledger"
down_revision = "0007_job_event_chain_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

        CREATE FUNCTION public.canonical_domain_json(p_value jsonb)
        RETURNS text
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $canonical_domain_json$
        DECLARE
          v_type text;
          v_text text;
          v_result text := '';
          v_index integer;
          v_character text;
          v_codepoint integer;
          v_high_surrogate integer;
          v_low_surrogate integer;
        BEGIN
          v_type := jsonb_typeof(p_value);
          IF v_type = 'null' THEN
            RETURN 'null';
          ELSIF v_type = 'boolean' THEN
            RETURN p_value #>> '{}';
          ELSIF v_type = 'number' THEN
            v_text := p_value #>> '{}';
            IF position('.' IN v_text) > 0
               OR position('e' IN v_text) > 0
               OR position('E' IN v_text) > 0 THEN
              RAISE EXCEPTION 'fractional JSON numbers are not canonical domain values'
                USING ERRCODE = '22023';
            END IF;
            RETURN v_text;
          ELSIF v_type = 'array' THEN
            SELECT '[' || COALESCE(
              string_agg(public.canonical_domain_json(element), ',' ORDER BY ordinal),
              ''
            ) || ']'
            INTO v_result
            FROM jsonb_array_elements(p_value) WITH ORDINALITY AS elements(element, ordinal);
            RETURN v_result;
          ELSIF v_type = 'object' THEN
            SELECT '{' || COALESCE(
              string_agg(
                public.canonical_domain_json(to_jsonb(key)) || ':' || public.canonical_domain_json(value),
                ',' ORDER BY key COLLATE "C"
              ),
              ''
            ) || '}'
            INTO v_result
            FROM jsonb_each(p_value) AS entries(key, value);
            RETURN v_result;
          ELSIF v_type <> 'string' THEN
            RAISE EXCEPTION 'unsupported canonical domain JSON value' USING ERRCODE = '22023';
          END IF;

          v_text := p_value #>> '{}';
          FOR v_index IN 1..char_length(v_text) LOOP
            v_character := substr(v_text, v_index, 1);
            v_codepoint := ascii(v_character);
            IF v_codepoint = 34 THEN
              v_result := v_result || chr(92) || chr(34);
            ELSIF v_codepoint = 92 THEN
              v_result := v_result || chr(92) || chr(92);
            ELSIF v_codepoint = 8 THEN
              v_result := v_result || chr(92) || 'b';
            ELSIF v_codepoint = 9 THEN
              v_result := v_result || chr(92) || 't';
            ELSIF v_codepoint = 10 THEN
              v_result := v_result || chr(92) || 'n';
            ELSIF v_codepoint = 12 THEN
              v_result := v_result || chr(92) || 'f';
            ELSIF v_codepoint = 13 THEN
              v_result := v_result || chr(92) || 'r';
            ELSIF v_codepoint < 32 OR v_codepoint > 126 THEN
              IF v_codepoint <= 65535 THEN
                v_result := v_result || chr(92) || 'u' || lpad(to_hex(v_codepoint), 4, '0');
              ELSE
                v_codepoint := v_codepoint - 65536;
                v_high_surrogate := 55296 + floor(v_codepoint::numeric / 1024)::integer;
                v_low_surrogate := 56320 + mod(v_codepoint, 1024);
                v_result := v_result || chr(92) || 'u' || lpad(to_hex(v_high_surrogate), 4, '0')
                  || chr(92) || 'u' || lpad(to_hex(v_low_surrogate), 4, '0');
              END IF;
            ELSE
              v_result := v_result || v_character;
            END IF;
          END LOOP;
          RETURN chr(34) || v_result || chr(34);
        END;
        $canonical_domain_json$;

        CREATE FUNCTION public.canonical_domain_json_string(p_value text)
        RETURNS text
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $canonical_domain_json_string$
        DECLARE
          v_index integer := 1;
          v_length integer := char_length(p_value);
          v_character text;
          v_token text;
          v_in_string boolean := false;
          v_escaped boolean := false;
          v_value jsonb;
        BEGIN
          -- jsonb loses exponent spelling, so inspect number tokens before parsing.
          WHILE v_index <= v_length LOOP
            v_character := substr(p_value, v_index, 1);
            IF v_in_string THEN
              IF v_escaped THEN
                v_escaped := false;
              ELSIF v_character = chr(92) THEN
                v_escaped := true;
              ELSIF v_character = chr(34) THEN
                v_in_string := false;
              END IF;
              v_index := v_index + 1;
            ELSIF v_character = chr(34) THEN
              v_in_string := true;
              v_index := v_index + 1;
            ELSIF v_character = '-' OR v_character BETWEEN '0' AND '9' THEN
              v_token := '';
              WHILE v_index <= v_length
                AND substr(p_value, v_index, 1) NOT IN (
                  ' ', chr(9), chr(10), chr(13), ',', '}', ']'
                ) LOOP
                v_token := v_token || substr(p_value, v_index, 1);
                v_index := v_index + 1;
              END LOOP;
              IF position('.' IN v_token) > 0
                 OR position('e' IN v_token) > 0
                 OR position('E' IN v_token) > 0 THEN
                RAISE EXCEPTION 'fractional JSON numbers are not canonical domain values'
                  USING ERRCODE = '22023';
              END IF;
            ELSE
              v_index := v_index + 1;
            END IF;
          END LOOP;
          v_value := p_value::jsonb;
          RETURN public.canonical_domain_json(v_value);
        END;
        $canonical_domain_json_string$;

        CREATE TABLE public.domain_events (
          event_id uuid PRIMARY KEY,
          stream_id uuid NOT NULL,
          sequence bigint NOT NULL CHECK (sequence > 0),
          event_type text NOT NULL CHECK (length(event_type) BETWEEN 1 AND 256),
          canonical_event JSONB NOT NULL,
          canonical_event_text text NOT NULL,
          digest char(64) NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
          CHECK (canonical_event_text::jsonb = canonical_event),
          CHECK (canonical_event_text = public.canonical_domain_json_string(canonical_event_text)),
          UNIQUE (stream_id, sequence),
          UNIQUE (event_id, digest)
        );
        CREATE INDEX domain_events_stream_sequence_idx
          ON public.domain_events (stream_id, sequence, event_id);

        CREATE TABLE public.event_append_idempotency (
          event_id uuid PRIMARY KEY,
          request_digest char(64) NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
          FOREIGN KEY (event_id) REFERENCES public.domain_events(event_id) ON DELETE RESTRICT
        );

        CREATE TABLE public.event_outbox (
          event_id uuid PRIMARY KEY,
          topic text NOT NULL CHECK (length(topic) BETWEEN 1 AND 256),
          payload JSONB NOT NULL,
          payload_text text NOT NULL,
          CHECK (payload_text::jsonb = payload),
          CHECK (payload_text = public.canonical_domain_json_string(payload_text)),
          FOREIGN KEY (event_id) REFERENCES public.domain_events(event_id) ON DELETE RESTRICT
        );
        CREATE INDEX event_outbox_topic_event_idx ON public.event_outbox (topic, event_id);

        CREATE TABLE public.event_publications (
          event_id uuid PRIMARY KEY,
          published_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          FOREIGN KEY (event_id) REFERENCES public.domain_events(event_id) ON DELETE RESTRICT
        );

        CREATE TABLE public.consumer_inbox (
          consumer text NOT NULL CHECK (length(consumer) BETWEEN 1 AND 256),
          event_id uuid NOT NULL,
          claimed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          PRIMARY KEY (consumer, event_id),
          FOREIGN KEY (event_id) REFERENCES public.domain_events(event_id) ON DELETE RESTRICT
        );

        -- Source-contract proof does not prove PostgreSQL runtime behavior.
        -- Runtime proof requires a separately approved disposable PostgreSQL fixture.
        CREATE TABLE public.aggregate_snapshots (
          state_hash char(64) NOT NULL CHECK (state_hash ~ '^[0-9a-f]{64}$'),
          state JSONB NOT NULL,
          status text NOT NULL CHECK (status IN ('COMPLETE', 'DEGRADED')),
          issues JSONB NOT NULL CHECK (jsonb_typeof(issues) = 'array'),
          canonical_state_json text NOT NULL,
          replay_schema_version text NOT NULL CHECK (length(replay_schema_version) BETWEEN 1 AND 256),
          reducer_version text NOT NULL CHECK (length(reducer_version) BETWEEN 1 AND 256),
          CHECK ((canonical_state_json::jsonb -> 'state') = state),
          CHECK ((canonical_state_json::jsonb ->> 'status') = status),
          CHECK ((canonical_state_json::jsonb -> 'issues') = issues),
          CHECK (canonical_state_json = public.canonical_domain_json_string(canonical_state_json)),
          CHECK ((canonical_state_json::jsonb ->> 'schema_version') = replay_schema_version),
          CHECK ((canonical_state_json::jsonb ->> 'reducer_version') = reducer_version),
          CHECK (replay_schema_version = 'event-ledger-replay-v1'),
          CHECK (reducer_version = 'event-ledger-reducer-v1'),
          CHECK (state_hash = encode(public.digest(convert_to(canonical_state_json, 'UTF8'), 'sha256'), 'hex')),
          PRIMARY KEY (state_hash)
        );

        CREATE FUNCTION public.append_domain_event(
          p_event_id uuid,
          p_stream_id uuid,
          p_sequence bigint,
          p_event_type text,
          p_canonical_event_text text,
          p_topic text,
          p_payload_json text
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $append_domain_event$
        DECLARE
          v_expected_sequence bigint;
          v_exact_retry boolean;
          v_digest char(64);
          v_request_digest char(64);
          v_event jsonb;
          v_payload jsonb;
        BEGIN
          -- Serialize retries for one identity before the per-stream sequence check.
          PERFORM pg_advisory_xact_lock(hashtextextended(p_event_id::text, 1));
          PERFORM pg_advisory_xact_lock(hashtextextended(p_stream_id::text, 0));
          v_event := p_canonical_event_text::jsonb;
          v_payload := p_payload_json::jsonb;
          IF p_canonical_event_text IS DISTINCT FROM public.canonical_domain_json_string(p_canonical_event_text) THEN
            RAISE EXCEPTION 'event JSON is not canonically encoded' USING ERRCODE = '23514';
          END IF;
          IF p_payload_json IS DISTINCT FROM public.canonical_domain_json_string(p_payload_json) THEN
            RAISE EXCEPTION 'outbox payload JSON is not canonically encoded' USING ERRCODE = '23514';
          END IF;
          IF jsonb_typeof(v_event) <> 'object'
             OR v_event ->> 'event_id' IS DISTINCT FROM p_event_id::text
             OR v_event ->> 'stream_id' IS DISTINCT FROM p_stream_id::text
             OR v_event ->> 'sequence' IS DISTINCT FROM p_sequence::text
             OR v_event ->> 'event_type' IS DISTINCT FROM p_event_type THEN
            RAISE EXCEPTION 'event envelope metadata does not match append arguments'
              USING ERRCODE = '23514';
          END IF;
          v_digest := encode(public.digest(convert_to(p_canonical_event_text, 'UTF8'), 'sha256'), 'hex');
          v_request_digest := encode(public.digest(
            int8send(octet_length(uuid_send(p_event_id))::bigint) || uuid_send(p_event_id) ||
            int8send(octet_length(uuid_send(p_stream_id))::bigint) || uuid_send(p_stream_id) ||
            int8send(octet_length(p_sequence::text)::bigint) || convert_to(p_sequence::text, 'UTF8') ||
            int8send(octet_length(p_event_type)::bigint) || convert_to(p_event_type, 'UTF8') ||
            int8send(octet_length(p_canonical_event_text)::bigint) || convert_to(p_canonical_event_text, 'UTF8') ||
            int8send(octet_length(p_topic)::bigint) || convert_to(p_topic, 'UTF8') ||
            int8send(octet_length(p_payload_json)::bigint) || convert_to(p_payload_json, 'UTF8'),
            'sha256'
          ), 'hex');

          SELECT request_digest = v_request_digest
          INTO v_exact_retry
          FROM public.event_append_idempotency
          WHERE event_id = p_event_id;
          IF FOUND THEN
            IF v_exact_retry THEN
              RETURN false;
            END IF;
            RAISE EXCEPTION 'conflicting duplicate event %', p_event_id USING ERRCODE = '23505';
          END IF;

          SELECT COALESCE(MAX(e.sequence), 0) + 1
          INTO v_expected_sequence
          FROM public.domain_events AS e
          WHERE e.stream_id = p_stream_id;
          IF p_sequence <> v_expected_sequence THEN
            RAISE EXCEPTION 'expected sequence % for stream %, got %',
              v_expected_sequence, p_stream_id, p_sequence USING ERRCODE = '23514';
          END IF;

          INSERT INTO public.domain_events (
            event_id, stream_id, sequence, event_type, canonical_event,
            canonical_event_text, digest
          ) VALUES (
            p_event_id, p_stream_id, p_sequence, p_event_type,
            v_event, p_canonical_event_text, v_digest
          );
          INSERT INTO public.event_append_idempotency (event_id, request_digest)
          VALUES (p_event_id, v_request_digest);
          INSERT INTO public.event_outbox (event_id, topic, payload, payload_text)
          VALUES (p_event_id, p_topic, v_payload, p_payload_json);
          RETURN true;
        END;
        $append_domain_event$;

        CREATE FUNCTION public.acknowledge_domain_publication(p_event_id uuid)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $acknowledge_domain_publication$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(p_event_id::text, 1));
          IF EXISTS (SELECT 1 FROM public.event_publications WHERE event_id = p_event_id) THEN
            RETURN false;
          END IF;
          IF NOT EXISTS (SELECT 1 FROM public.event_outbox WHERE event_id = p_event_id) THEN
            RAISE EXCEPTION 'event % has no pending outbox work', p_event_id USING ERRCODE = '23514';
          END IF;
          INSERT INTO public.event_publications (event_id) VALUES (p_event_id);
          DELETE FROM public.event_outbox WHERE event_id = p_event_id;
          RETURN true;
        END;
        $acknowledge_domain_publication$;

        CREATE FUNCTION public.save_domain_snapshot(p_canonical_state_json text)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $save_domain_snapshot$
        DECLARE
          v_snapshot jsonb;
          v_state jsonb;
          v_issues jsonb;
          v_status text;
          v_schema_version text;
          v_reducer_version text;
          v_state_hash char(64);
          v_event_count bigint;
          v_total bigint;
          v_valid bigint;
          v_invalid bigint;
          v_sum numeric;
          v_exact_retry boolean;
        BEGIN
          v_snapshot := p_canonical_state_json::jsonb;
          IF p_canonical_state_json IS DISTINCT FROM public.canonical_domain_json_string(p_canonical_state_json) THEN
            RAISE EXCEPTION 'snapshot JSON is not canonically encoded' USING ERRCODE = '23514';
          END IF;
          IF jsonb_typeof(v_snapshot) <> 'object' THEN
            RAISE EXCEPTION 'snapshot wrapper keys are invalid' USING ERRCODE = '23514';
          END IF;
          SELECT count(*), count(*) FILTER (
            WHERE key = ANY (ARRAY['issues','reducer_version','schema_version','state','status']::text[])
          ) INTO v_total, v_valid
          FROM jsonb_object_keys(v_snapshot) AS keys(key);
          IF v_total <> 5 OR v_valid <> 5 THEN
            RAISE EXCEPTION 'snapshot wrapper keys are invalid' USING ERRCODE = '23514';
          END IF;

          v_state := v_snapshot -> 'state';
          v_issues := v_snapshot -> 'issues';
          v_status := v_snapshot ->> 'status';
          v_schema_version := v_snapshot ->> 'schema_version';
          v_reducer_version := v_snapshot ->> 'reducer_version';
          IF jsonb_typeof(v_state) <> 'object'
             OR jsonb_typeof(v_issues) <> 'array'
             OR jsonb_typeof(v_snapshot -> 'status') <> 'string'
             OR jsonb_typeof(v_snapshot -> 'schema_version') <> 'string'
             OR jsonb_typeof(v_snapshot -> 'reducer_version') <> 'string' THEN
            RAISE EXCEPTION 'snapshot wrapper types are invalid' USING ERRCODE = '23514';
          END IF;
          IF v_schema_version <> 'event-ledger-replay-v1'
             OR v_reducer_version <> 'event-ledger-reducer-v1' THEN
            RAISE EXCEPTION 'snapshot uses unsupported replay or reducer version' USING ERRCODE = '23514';
          END IF;

          SELECT count(*), count(*) FILTER (
            WHERE key = ANY (ARRAY['applied_events','event_count','streams','type_counts']::text[])
          ) INTO v_total, v_valid
          FROM jsonb_object_keys(v_state) AS keys(key);
          IF v_total <> 4 OR v_valid <> 4 THEN
            RAISE EXCEPTION 'snapshot state keys are invalid' USING ERRCODE = '23514';
          END IF;
          IF jsonb_typeof(v_state -> 'event_count') <> 'number'
             OR (v_state ->> 'event_count') !~ '^(0|[1-9][0-9]*)$'
             OR (v_state ->> 'event_count')::numeric > 9223372036854775807 THEN
            RAISE EXCEPTION 'snapshot event_count is outside PostgreSQL bigint bounds' USING ERRCODE = '23514';
          END IF;
          v_event_count := (v_state ->> 'event_count')::bigint;
          IF jsonb_typeof(v_state -> 'applied_events') <> 'array'
             OR jsonb_typeof(v_state -> 'type_counts') <> 'array'
             OR jsonb_typeof(v_state -> 'streams') <> 'array' THEN
            RAISE EXCEPTION 'snapshot state arrays are invalid' USING ERRCODE = '23514';
          END IF;

          IF jsonb_array_length(v_state -> 'applied_events') <> v_event_count THEN
            RAISE EXCEPTION 'snapshot event_count does not match applied events' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM jsonb_array_elements(v_state -> 'applied_events') AS entries(item)
          WHERE jsonb_typeof(item) <> 'object';
          IF v_invalid <> 0 THEN
            RAISE EXCEPTION 'snapshot applied event fields are invalid' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM jsonb_array_elements(v_state -> 'applied_events') AS entries(item)
          WHERE (SELECT count(*) FROM jsonb_object_keys(item)) <> 2
             OR (SELECT count(*) FROM jsonb_object_keys(item) AS keys(key)
                 WHERE key = ANY (ARRAY['digest','event_id']::text[])) <> 2
             OR jsonb_typeof(item -> 'event_id') <> 'string'
             OR jsonb_typeof(item -> 'digest') <> 'string'
             OR (item ->> 'digest') !~ '^[0-9a-f]{64}$';
          IF v_invalid <> 0 THEN
            RAISE EXCEPTION 'snapshot applied event fields are invalid' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM (
            SELECT item, ordinal,
              row_number() OVER (ORDER BY (item ->> 'event_id')::uuid) AS canonical_ordinal
            FROM jsonb_array_elements(v_state -> 'applied_events')
              WITH ORDINALITY AS entries(item, ordinal)
          ) AS ordered
          WHERE ordinal <> canonical_ordinal;
          SELECT count(*), count(DISTINCT item ->> 'event_id')
          INTO v_total, v_valid
          FROM jsonb_array_elements(v_state -> 'applied_events') AS entries(item);
          IF v_invalid <> 0 OR v_total <> v_valid THEN
            RAISE EXCEPTION 'snapshot applied events are not unique and canonically ordered' USING ERRCODE = '23514';
          END IF;

          SELECT count(*) INTO v_invalid
          FROM jsonb_array_elements(v_state -> 'type_counts') AS entries(item)
          WHERE jsonb_typeof(item) <> 'object';
          IF v_invalid <> 0 THEN
            RAISE EXCEPTION 'snapshot type count fields are invalid' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM jsonb_array_elements(v_state -> 'type_counts') AS entries(item)
          WHERE (SELECT count(*) FROM jsonb_object_keys(item)) <> 2
             OR (SELECT count(*) FROM jsonb_object_keys(item) AS keys(key)
                 WHERE key = ANY (ARRAY['count','event_type']::text[])) <> 2
             OR jsonb_typeof(item -> 'event_type') <> 'string'
             OR jsonb_typeof(item -> 'count') <> 'number'
             OR (item ->> 'count') !~ '^(0|[1-9][0-9]*)$'
             OR (item ->> 'count')::numeric > 9223372036854775807
             OR item ->> 'event_type' NOT IN (
               'SignalProposal','TargetPortfolio','RiskDecision',
               'OrderIntent','OrderEvent','FillEvent'
             );
          IF v_invalid <> 0 THEN
            RAISE EXCEPTION 'snapshot type counts are not unique, registered, and canonically ordered' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM (
            SELECT item, ordinal,
              row_number() OVER (ORDER BY (item ->> 'event_type') COLLATE "C") AS canonical_ordinal
            FROM jsonb_array_elements(v_state -> 'type_counts')
              WITH ORDINALITY AS entries(item, ordinal)
          ) AS ordered
          WHERE ordinal <> canonical_ordinal;
          SELECT count(*), count(DISTINCT item ->> 'event_type'),
                 COALESCE(sum((item ->> 'count')::numeric), 0)
          INTO v_total, v_valid, v_sum
          FROM jsonb_array_elements(v_state -> 'type_counts') AS entries(item);
          IF v_invalid <> 0 OR v_total <> v_valid THEN
            RAISE EXCEPTION 'snapshot type counts are not unique, registered, and canonically ordered' USING ERRCODE = '23514';
          END IF;
          IF v_sum <> v_event_count THEN
            RAISE EXCEPTION 'snapshot type counts do not match event_count' USING ERRCODE = '23514';
          END IF;

          SELECT count(*) INTO v_invalid
          FROM jsonb_array_elements(v_state -> 'streams') AS entries(item)
          WHERE jsonb_typeof(item) <> 'object';
          IF v_invalid <> 0 THEN
            RAISE EXCEPTION 'snapshot stream fields are invalid' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM jsonb_array_elements(v_state -> 'streams') AS entries(item)
          WHERE (SELECT count(*) FROM jsonb_object_keys(item)) <> 4
             OR (SELECT count(*) FROM jsonb_object_keys(item) AS keys(key)
                 WHERE key = ANY (ARRAY['event_count','last_digest','last_sequence','stream_id']::text[])) <> 4
             OR jsonb_typeof(item -> 'stream_id') <> 'string'
             OR jsonb_typeof(item -> 'last_digest') <> 'string'
             OR (item ->> 'last_digest') !~ '^[0-9a-f]{64}$'
             OR jsonb_typeof(item -> 'event_count') <> 'number'
             OR jsonb_typeof(item -> 'last_sequence') <> 'number'
             OR (item ->> 'event_count') !~ '^[1-9][0-9]*$'
             OR (item ->> 'last_sequence') !~ '^[1-9][0-9]*$'
             OR (item ->> 'event_count')::numeric > 9223372036854775807
             OR (item ->> 'last_sequence')::numeric > 9223372036854775807
             OR (item ->> 'last_sequence')::numeric <> (item ->> 'event_count')::numeric;
          IF v_invalid <> 0 THEN
            RAISE EXCEPTION 'snapshot stream projection is structurally inconsistent' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM (
            SELECT item, ordinal,
              row_number() OVER (ORDER BY (item ->> 'stream_id')::uuid) AS canonical_ordinal
            FROM jsonb_array_elements(v_state -> 'streams')
              WITH ORDINALITY AS entries(item, ordinal)
          ) AS ordered
          WHERE ordinal <> canonical_ordinal;
          SELECT count(*), count(DISTINCT item ->> 'stream_id'),
                 COALESCE(sum((item ->> 'event_count')::numeric), 0)
          INTO v_total, v_valid, v_sum
          FROM jsonb_array_elements(v_state -> 'streams') AS entries(item);
          IF v_invalid <> 0 OR v_total <> v_valid THEN
            RAISE EXCEPTION 'snapshot streams are not unique and canonically ordered' USING ERRCODE = '23514';
          END IF;
          IF v_sum <> v_event_count THEN
            RAISE EXCEPTION 'snapshot stream counts do not match event_count' USING ERRCODE = '23514';
          END IF;

          SELECT count(*) INTO v_invalid
          FROM jsonb_array_elements(v_issues) AS entries(item)
          WHERE jsonb_typeof(item) <> 'object';
          IF v_invalid <> 0 THEN
            RAISE EXCEPTION 'snapshot issue fields are invalid' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM jsonb_array_elements(v_issues) AS entries(item)
          WHERE (SELECT count(*) FROM jsonb_object_keys(item)) <> 6
             OR (SELECT count(*) FROM jsonb_object_keys(item) AS keys(key)
                 WHERE key = ANY (ARRAY['code','digest','event_id','expected_sequence','sequence','stream_id']::text[])) <> 6
             OR jsonb_typeof(item -> 'code') <> 'string'
             OR item ->> 'code' NOT IN ('SEQUENCE_GAP','SEQUENCE_REGRESSION')
             OR jsonb_typeof(item -> 'stream_id') <> 'string'
             OR jsonb_typeof(item -> 'event_id') <> 'string'
             OR jsonb_typeof(item -> 'digest') <> 'string'
             OR (item ->> 'digest') !~ '^[0-9a-f]{64}$'
             OR jsonb_typeof(item -> 'sequence') <> 'number'
             OR jsonb_typeof(item -> 'expected_sequence') <> 'number'
             OR (item ->> 'sequence') !~ '^[1-9][0-9]*$'
             OR (item ->> 'expected_sequence') !~ '^[1-9][0-9]*$'
             OR (item ->> 'sequence')::numeric > 9223372036854775807
             OR (item ->> 'expected_sequence')::numeric > 9223372036854775807;
          IF v_invalid <> 0 THEN
            RAISE EXCEPTION 'snapshot replay issue is structurally inconsistent' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM (
            SELECT item, ordinal,
              row_number() OVER (
                ORDER BY (item ->> 'stream_id')::uuid,
                         (item ->> 'sequence')::bigint,
                         (item ->> 'event_id')::uuid
              ) AS canonical_ordinal
            FROM jsonb_array_elements(v_issues)
              WITH ORDINALITY AS entries(item, ordinal)
          ) AS ordered
          WHERE ordinal <> canonical_ordinal;
          SELECT count(*), count(DISTINCT item ->> 'event_id')
          INTO v_total, v_valid
          FROM jsonb_array_elements(v_issues) AS entries(item);
          IF v_invalid <> 0 OR v_total <> v_valid THEN
            RAISE EXCEPTION 'snapshot issues are not unique and canonically ordered' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM jsonb_array_elements(v_issues) AS issue(item)
          JOIN jsonb_array_elements(v_state -> 'applied_events') AS applied(item)
            ON issue.item ->> 'event_id' = applied.item ->> 'event_id';
          IF v_invalid <> 0 THEN
            RAISE EXCEPTION 'snapshot issue is also marked applied' USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_invalid
          FROM jsonb_array_elements(v_issues) AS entries(item)
          WHERE (item ->> 'code' = 'SEQUENCE_GAP'
                 AND (item ->> 'sequence')::bigint <= (item ->> 'expected_sequence')::bigint)
             OR (item ->> 'code' = 'SEQUENCE_REGRESSION'
                 AND (item ->> 'sequence')::bigint >= (item ->> 'expected_sequence')::bigint);
          IF v_invalid <> 0 THEN
            RAISE EXCEPTION 'snapshot replay issue is structurally inconsistent' USING ERRCODE = '23514';
          END IF;
          IF v_status NOT IN ('COMPLETE','DEGRADED')
             OR (v_status = 'DEGRADED') <> (jsonb_array_length(v_issues) > 0) THEN
            RAISE EXCEPTION 'snapshot status does not match replay issues' USING ERRCODE = '23514';
          END IF;

          v_state_hash := encode(public.digest(convert_to(p_canonical_state_json, 'UTF8'), 'sha256'), 'hex');
          PERFORM pg_advisory_xact_lock(hashtextextended(v_state_hash::text, 2));
          SELECT s.canonical_state_json = p_canonical_state_json
          INTO v_exact_retry
          FROM public.aggregate_snapshots AS s
          WHERE s.state_hash = v_state_hash;
          IF FOUND THEN
            IF v_exact_retry THEN
              RETURN false;
            END IF;
            RAISE EXCEPTION 'conflicting duplicate snapshot for hash %', v_state_hash
              USING ERRCODE = '23505';
          END IF;

          INSERT INTO public.aggregate_snapshots (
            state_hash, state, status, issues, canonical_state_json,
            replay_schema_version, reducer_version
          ) VALUES (
            v_state_hash, v_state, v_status, v_issues, p_canonical_state_json,
            v_schema_version, v_reducer_version
          );
          RETURN true;
        END;
        $save_domain_snapshot$;

        CREATE FUNCTION public.domain_events_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $append_only$
        BEGIN
          RAISE EXCEPTION 'domain_events is append-only';
        END;
        $append_only$;
        CREATE TRIGGER domain_events_append_only
          BEFORE UPDATE OR DELETE ON public.domain_events
          FOR EACH ROW EXECUTE FUNCTION public.domain_events_append_only();
        CREATE TRIGGER domain_events_reject_truncate
          BEFORE TRUNCATE ON public.domain_events
          FOR EACH STATEMENT EXECUTE FUNCTION public.domain_events_append_only();

        CREATE FUNCTION public.aggregate_snapshots_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $append_only$
        BEGIN
          RAISE EXCEPTION 'aggregate_snapshots is immutable';
        END;
        $append_only$;
        CREATE TRIGGER aggregate_snapshots_append_only
          BEFORE UPDATE OR DELETE ON public.aggregate_snapshots
          FOR EACH ROW EXECUTE FUNCTION public.aggregate_snapshots_append_only();
        CREATE TRIGGER aggregate_snapshots_reject_truncate
          BEFORE TRUNCATE ON public.aggregate_snapshots
          FOR EACH STATEMENT EXECUTE FUNCTION public.aggregate_snapshots_append_only();

        CREATE FUNCTION public.event_append_idempotency_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $event_append_idempotency_append_only$
        BEGIN
          RAISE EXCEPTION 'event_append_idempotency is append-only';
        END;
        $event_append_idempotency_append_only$;
        CREATE TRIGGER event_append_idempotency_append_only
          BEFORE UPDATE OR DELETE ON public.event_append_idempotency
          FOR EACH ROW EXECUTE FUNCTION public.event_append_idempotency_append_only();
        CREATE TRIGGER event_append_idempotency_reject_truncate
          BEFORE TRUNCATE ON public.event_append_idempotency
          FOR EACH STATEMENT EXECUTE FUNCTION public.event_append_idempotency_append_only();

        CREATE FUNCTION public.event_publications_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $event_publications_append_only$
        BEGIN
          RAISE EXCEPTION 'event_publications is append-only';
        END;
        $event_publications_append_only$;
        CREATE TRIGGER event_publications_append_only
          BEFORE UPDATE OR DELETE ON public.event_publications
          FOR EACH ROW EXECUTE FUNCTION public.event_publications_append_only();
        CREATE TRIGGER event_publications_reject_truncate
          BEFORE TRUNCATE ON public.event_publications
          FOR EACH STATEMENT EXECUTE FUNCTION public.event_publications_append_only();

        CREATE FUNCTION public.consumer_inbox_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $consumer_inbox_append_only$
        BEGIN
          RAISE EXCEPTION 'consumer_inbox is append-only';
        END;
        $consumer_inbox_append_only$;
        CREATE TRIGGER consumer_inbox_append_only
          BEFORE UPDATE OR DELETE ON public.consumer_inbox
          FOR EACH ROW EXECUTE FUNCTION public.consumer_inbox_append_only();
        CREATE TRIGGER consumer_inbox_reject_truncate
          BEFORE TRUNCATE ON public.consumer_inbox
          FOR EACH STATEMENT EXECUTE FUNCTION public.consumer_inbox_append_only();

        CREATE FUNCTION public.event_outbox_reject_update()
        RETURNS trigger LANGUAGE plpgsql AS $event_outbox_reject_update$
        BEGIN
          RAISE EXCEPTION 'event_outbox is immutable pending work';
        END;
        $event_outbox_reject_update$;
        CREATE TRIGGER event_outbox_reject_update
          BEFORE UPDATE ON public.event_outbox
          FOR EACH ROW EXECUTE FUNCTION public.event_outbox_reject_update();
        CREATE TRIGGER event_outbox_reject_truncate
          BEFORE TRUNCATE ON public.event_outbox
          FOR EACH STATEMENT EXECUTE FUNCTION public.event_outbox_reject_update();

        CREATE FUNCTION public.event_outbox_require_publication_receipt()
        RETURNS trigger LANGUAGE plpgsql AS $event_outbox_require_publication_receipt$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM public.event_publications WHERE event_id = OLD.event_id
          ) THEN
            RAISE EXCEPTION 'event_outbox delete requires durable publication receipt';
          END IF;
          RETURN OLD;
        END;
        $event_outbox_require_publication_receipt$;
        CREATE TRIGGER event_outbox_require_publication_receipt
          BEFORE DELETE ON public.event_outbox
          FOR EACH ROW EXECUTE FUNCTION public.event_outbox_require_publication_receipt();

        REVOKE ALL PRIVILEGES ON TABLE public.domain_events FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON TABLE public.event_append_idempotency FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON TABLE public.event_outbox FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON TABLE public.event_publications FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON TABLE public.consumer_inbox FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON TABLE public.aggregate_snapshots FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.domain_events_append_only() FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.aggregate_snapshots_append_only() FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.event_append_idempotency_append_only() FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.event_publications_append_only() FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.consumer_inbox_append_only() FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.event_outbox_reject_update() FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.event_outbox_require_publication_receipt() FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.canonical_domain_json(jsonb) FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.canonical_domain_json_string(text) FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.append_domain_event(uuid, uuid, bigint, text, text, text, text) FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.acknowledge_domain_publication(uuid) FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.save_domain_snapshot(text) FROM PUBLIC;

        -- Role grants are intentionally absent until reviewed Runtime Authority activation.
        -- That activation must grant EXECUTE on the canonical helpers and write wrappers,
        -- then grant the SECURITY INVOKER table privileges required by those wrappers.
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0008 trading-domain ledger is forward-only; use a reviewed forward repair"
    )
