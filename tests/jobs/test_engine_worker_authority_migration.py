from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


MIGRATION = Path(
    "alembic/versions/0011_engine_backtest_worker_authority.py"
)
TRANSITION_AUTHORITY = Path(
    "alembic/versions/0006_job_transition_database_authority.py"
)
TRANSITION_CATALOG = Path(
    "ops/postgres/job-plane-authority/catalog-0006-v1.snapshot"
)
PG16_PROC_CATALOG = Path(
    "tests/jobs/fixtures/postgresql-16-pg-proc.catalog"
)


def test_engine_worker_authority_is_forward_chained_and_fail_closed() -> None:
    source = MIGRATION.read_text()

    for expected in (
        'revision = "0011_engine_backtest_worker_authority"',
        'down_revision = "0010_engine_event_ledger"',
        "CREATE TABLE public.engine_job_results",
        "PRIMARY KEY",
        "batch_sha256 char(64) NOT NULL UNIQUE",
        "CREATE FUNCTION job_plane.ingest_engine_job_result",
        "p_job_id text",
        "p_attempt_id text",
        "p_worker_id text",
        "p_lease_token text",
        "session_user <> 'trading_job_worker'",
        "current_job.state <> 'RUNNING'",
        "current_attempt.outcome <> 'RUNNING'",
        "current_attempt.attempt_number <> current_job.attempt_count",
        "current_job.lease_owner IS DISTINCT FROM p_worker_id",
        "current_job.lease_token IS DISTINCT FROM p_lease_token",
        "current_attempt.lease_expires_at <= statement_timestamp()",
        "accepted.job_id IS DISTINCT FROM p_job_id",
        "accepted.attempt_id IS DISTINCT FROM p_attempt_id",
        "public.ingest_engine_event_batch(p_batch_document)",
        "ON CONFLICT (job_id) DO NOTHING",
        "binding.batch_sha256 IS DISTINCT FROM accepted.batch_sha256",
        "USING ERRCODE = 'P2D01'",
        "RuntimeError",
    ):
        assert expected in source


def test_every_pg_proc_reference_exists_in_frozen_postgresql_16_catalog() -> None:
    migration = MIGRATION.read_text()
    catalog_source = PG16_PROC_CATALOG.read_text()
    assert hashlib.sha256(catalog_source.encode()).hexdigest() == (
        "7643e5ba362fd5aad9f26190f19657e002c04d4e886cca42598f56544d838a5a"
    )
    assert catalog_source.startswith("# PostgreSQL 16\n")
    declaration = re.search(
        r"create pg_proc 1255 bootstrap rowtype_oid 81\n \((.*?)\n \)",
        catalog_source,
        flags=re.DOTALL,
    )
    assert declaration is not None
    allowed_columns = frozenset(
        re.findall(r"^ ([a-z][a-z0-9_]*) = ", declaration.group(1), re.MULTILINE)
    )
    assert len(allowed_columns) == 30
    assert "protrftypes" in allowed_columns
    assert "protransform" not in allowed_columns

    referenced_columns = frozenset(
        re.findall(r"procedure_row\.([a-z][a-z0-9_]*)", migration)
    )
    assert referenced_columns
    assert referenced_columns <= allowed_columns
    assert referenced_columns - allowed_columns == frozenset()
    assert "procedure_row.protransform" not in migration
    assert migration.count("procedure_row.protrftypes IS NULL") == 3
    assert migration.count("procedure_row.protrftypes IS NOT NULL") == 1

    transition_authority = TRANSITION_AUTHORITY.read_text()
    assert (
        "current_setting('server_version_num')::integer / 10000 <> 16"
        in transition_authority
    )
    assert "0006 requires PostgreSQL 16" in transition_authority


def test_paper_worker_capabilities_derive_only_from_pinned_reviewed_bodies() -> None:
    source = MIGRATION.read_text()

    for capability in (
        "worker_claim_paper",
        "worker_start_paper",
        "worker_control_paper_lease",
        "worker_finalize_paper",
        "worker_recover_expired_paper",
    ):
        assert capability in source
        assert f"GRANT EXECUTE ON FUNCTION job_plane.{capability}" in source
    for proof in (
        "pg_get_functiondef",
        "source_hashes text[]",
        "public.digest",
        "pg_get_function_result",
        "pg_get_userbyid(procedure_row.proowner) = 'trading_owner'",
        "procedure_row.prosecdef",
        "procedure_row.proconfig = ARRAY['search_path=pg_catalog']",
        "paper worker source ACL drifted",
        "occurrences <> 1",
        "paper worker source predicate drifted",
        "source_body, reviewed_predicate, paper_predicate",
        "paper worker promoted definition postflight failed",
        "paper worker target catalog postflight failed",
        "paper worker target ACL postflight failed",
        "paper worker support catalog postflight failed",
        "paper worker support ACL postflight failed",
        "job_plane.paper_worker_job_allowed(job_row.job_type, job_row.payload)",
        "0011 parent head changed during migration",
    ):
        assert proof in source
    assert "regexp_matches" not in source
    assert "regexp_replace" not in source
    for pinned_hash in (
        "0755231aaba81d1692581ff62b44f5babb03dcc0ed352687fc3a67b0ad2ee80a",
        "a7a402f871b4790f74dd774e5b8ce9417294dc78f6bca436062acddb933fa905",
        "47d09d4157c6992e8e21c3f6f08866e32176e8fda04a74bb2b4f49582ad5233d",
        "4a19cdf22297ee97dc7e31f354f42c020075e9a61e3cd411fb9dc8b160c88181",
        "90d7cda5361ccc9e2364821baad7a8a5c4ce56f5d3552b7ba835e980eb175b82",
    ):
        assert pinned_hash in source


def test_each_frozen_hash_matches_the_complete_reviewed_0006_function_body() -> None:
    migration = MIGRATION.read_text()
    source = TRANSITION_AUTHORITY.read_text()
    expected = {
        "worker_claim_snapshot": (
            "0755231aaba81d1692581ff62b44f5babb03dcc0ed352687fc3a67b0ad2ee80a"
        ),
        "worker_start_snapshot": (
            "a7a402f871b4790f74dd774e5b8ce9417294dc78f6bca436062acddb933fa905"
        ),
        "worker_control_snapshot_lease": (
            "47d09d4157c6992e8e21c3f6f08866e32176e8fda04a74bb2b4f49582ad5233d"
        ),
        "worker_finalize_snapshot": (
            "4a19cdf22297ee97dc7e31f354f42c020075e9a61e3cd411fb9dc8b160c88181"
        ),
        "worker_recover_expired_snapshot": (
            "90d7cda5361ccc9e2364821baad7a8a5c4ce56f5d3552b7ba835e980eb175b82"
        ),
    }

    for function_name, expected_sha256 in expected.items():
        bodies = re.findall(
            rf"CREATE FUNCTION job_plane\.{function_name}\(.*?"
            rf"AS \$function\$(.*?)\$function\$;",
            source,
            flags=re.DOTALL,
        )
        assert len(bodies) == 1
        assert hashlib.sha256(bodies[0].encode()).hexdigest() == expected_sha256
        assert bodies[0].count("job_row.job_type = 'SNAPSHOT'") == 1
        assert migration.count(expected_sha256) >= 1


def test_complete_catalog_definitions_are_frozen_before_and_after_promotion() -> None:
    migration = MIGRATION.read_text()
    reviewed_predicate = "job_row.job_type = 'SNAPSHOT'"
    paper_predicate = (
        "job_plane.paper_worker_job_allowed(job_row.job_type, job_row.payload)"
    )
    target_names = {
        "worker_claim_snapshot": "worker_claim_paper",
        "worker_start_snapshot": "worker_start_paper",
        "worker_control_snapshot_lease": "worker_control_paper_lease",
        "worker_finalize_snapshot": "worker_finalize_paper",
        "worker_recover_expired_snapshot": "worker_recover_expired_paper",
    }
    catalog = tuple(
        json.loads(line) for line in TRANSITION_CATALOG.read_text().splitlines()
    )
    reviewed = {
        row["name"]: row["definition"]
        for row in catalog
        if row.get("schema") == "job_plane" and row.get("name") in target_names
    }
    assert set(reviewed) == set(target_names)

    for source_name, target_name in target_names.items():
        source_definition = reviewed[source_name]
        source_digest = hashlib.sha256(source_definition.encode()).hexdigest()
        assert migration.count(source_digest) >= 1

        promoted_definition = source_definition.replace(
            source_name, target_name
        ).replace(reviewed_predicate, paper_predicate)
        target_digest = hashlib.sha256(promoted_definition.encode()).hexdigest()
        assert migration.count(target_digest) >= 1

        default_injection = source_definition.replace(
            ")\n RETURNS", " DEFAULT NULL)\n RETURNS", 1
        )
        argument_name_drift = source_definition.replace(
            "(p_", "(p_drift_", 1
        )
        assert hashlib.sha256(default_injection.encode()).hexdigest() != source_digest
        assert (
            hashlib.sha256(argument_name_drift.encode()).hexdigest()
            != source_digest
        )


def test_dynamic_promotion_rejects_hidden_callable_arity_and_argument_drift() -> None:
    source = MIGRATION.read_text()

    repeated_catalog_guards = {
        "procedure_row.pronargdefaults = 0": 3,
        "procedure_row.proargdefaults IS NULL": 3,
        "procedure_row.provariadic = 0": 3,
        "array_to_string(procedure_row.proargnames, ',')": 4,
        "array_to_string(procedure_row.proargmodes, ',')": 4,
    }
    for guard, minimum_count in repeated_catalog_guards.items():
        assert source.count(guard) >= minimum_count
    for guard in (
        "expected_argument_counts[function_index]",
        "expected_argument_names[function_index]",
        "expected_argument_modes[function_index]",
    ):
        assert source.count(guard) >= 2

    declarations_block = re.search(
        r"expected_argument_declarations text\[\] := ARRAY\[(.*?)\n          \];",
        source,
        flags=re.DOTALL,
    )
    names_block = re.search(
        r"expected_argument_names text\[\] := ARRAY\[(.*?)\n          \];",
        source,
        flags=re.DOTALL,
    )
    assert declarations_block is not None
    assert names_block is not None
    declarations = re.findall(r"'([^']+)'", declarations_block.group(1))
    argument_names = re.findall(r"'([^']+)'", names_block.group(1))
    assert len(declarations) == len(argument_names) == 5
    transition_source = TRANSITION_AUTHORITY.read_text()
    source_names = (
        "worker_claim_snapshot",
        "worker_start_snapshot",
        "worker_control_snapshot_lease",
        "worker_finalize_snapshot",
        "worker_recover_expired_snapshot",
    )

    for function_name, declaration, names in zip(
        source_names, declarations, argument_names, strict=True
    ):
        source_declaration = re.search(
            rf"CREATE FUNCTION job_plane\.{function_name}\((.*?)\)\s*RETURNS",
            transition_source,
            flags=re.DOTALL,
        )
        assert source_declaration is not None
        normalized_source_declaration = " ".join(
            source_declaration.group(1).split()
        ).replace(" ,", ",")
        assert normalized_source_declaration == declaration
        inputs = tuple(part.strip() for part in declaration.split(","))
        input_names = tuple(part.split()[0] for part in inputs)
        catalog_names = tuple(names.split(","))
        assert catalog_names[: len(inputs)] == input_names

        default_injection = declaration + " DEFAULT NULL"
        name_drift = names.replace(input_names[-1], input_names[-1] + "_drift")
        assert default_injection not in declarations
        assert name_drift not in argument_names

        reviewed_catalog = {
            "pronargs": len(inputs),
            "pronargdefaults": 0,
            "proargdefaults": None,
            "provariadic": 0,
            "argument_names": names,
        }
        mutations = (
            {**reviewed_catalog, "pronargdefaults": 1},
            {**reviewed_catalog, "proargdefaults": "default-node"},
            {**reviewed_catalog, "provariadic": 25},
            {**reviewed_catalog, "argument_names": name_drift},
        )
        for candidate in mutations:
            assert candidate != reviewed_catalog
            assert not (
                candidate["pronargs"] == len(inputs)
                and candidate["pronargdefaults"] == 0
                and candidate["proargdefaults"] is None
                and candidate["provariadic"] == 0
                and candidate["argument_names"] == names
            )


def test_targets_use_literal_headers_and_complete_definition_postflight() -> None:
    source = MIGRATION.read_text()

    assert "promoted_definition := format(" in source
    assert "expected_argument_declarations[function_index]" in source
    assert "EXECUTE promoted_definition" in source
    assert "EXECUTE expected_target_definition" not in source
    for proof in (
        "target_definition IS DISTINCT FROM",
        "expected_target_definition",
        "convert_to(target_definition, 'UTF8')",
        "procedure_row.pronargdefaults IS DISTINCT FROM 0",
        "procedure_row.proargdefaults IS NOT NULL",
        "procedure_row.provariadic IS DISTINCT FROM 0",
    ):
        assert proof in source


def test_promoted_and_support_body_hashes_cover_every_generated_authority() -> None:
    source = MIGRATION.read_text()
    transition_source = TRANSITION_AUTHORITY.read_text()
    reviewed_predicate = "job_row.job_type = 'SNAPSHOT'"
    paper_predicate = (
        "job_plane.paper_worker_job_allowed(job_row.job_type, job_row.payload)"
    )
    promoted_names = {
        "worker_claim_snapshot": "worker_claim_paper",
        "worker_start_snapshot": "worker_start_paper",
        "worker_control_snapshot_lease": "worker_control_paper_lease",
        "worker_finalize_snapshot": "worker_finalize_paper",
        "worker_recover_expired_snapshot": "worker_recover_expired_paper",
    }
    for source_name, target_name in promoted_names.items():
        body = re.search(
            rf"CREATE FUNCTION job_plane\.{source_name}\(.*?"
            rf"AS \$function\$(.*?)\$function\$;",
            transition_source,
            flags=re.DOTALL,
        )
        assert body is not None
        promoted = body.group(1).replace(reviewed_predicate, paper_predicate)
        digest = hashlib.sha256(promoted.encode()).hexdigest()
        assert target_name in source
        assert source.count(digest) >= 1

    for function_name, tag in (
        ("paper_worker_job_allowed", "paper_worker_job_allowed"),
        ("ingest_engine_job_result", "ingest_engine_job_result"),
    ):
        body = re.search(
            rf"CREATE FUNCTION job_plane\.{function_name}\(.*?"
            rf"AS \${tag}\$(.*?)\${tag}\$;",
            source,
            flags=re.DOTALL,
        )
        assert body is not None
        digest = hashlib.sha256(body.group(1).encode()).hexdigest()
        assert source.count(digest) >= 1


def test_paper_worker_payload_gate_is_exact_and_legacy_fail_closed() -> None:
    source = MIGRATION.read_text()

    for proof in (
        "CREATE FUNCTION job_plane.paper_worker_job_allowed",
        "WHEN p_job_type = 'SNAPSHOT' THEN true",
        "WHEN p_job_type IS NULL",
        "OR p_job_type <> 'BACKTEST'",
        "p_payload IS DISTINCT FROM jsonb_build_object(",
        "p_payload -> 'engine_backtest' IS DISTINCT FROM",
        "'engine_configuration'",
        "'instrument_catalog'",
        "'strategy_configuration'",
        "'market_data'",
        "'start_time'",
        "'end_time'",
        "artifact.value IS NOT DISTINCT FROM jsonb_build_object(",
        "jsonb_typeof(artifact.value -> 'artifact_id') = 'string'",
        "jsonb_typeof(artifact.value -> 'sha256') = 'string'",
        "jsonb_typeof(artifact.value -> 'media_type') = 'string'",
        "'application/jsonl'",
        "to_char(to_date(",
        ")::timestamp without time zone",
        "job_row.job_type = 'BACKTEST'",
    ):
        assert proof in source


def test_runtime_role_gets_only_required_job_result_authority() -> None:
    source = MIGRATION.read_text()

    assert (
        "GRANT SELECT ON TABLE public.engine_event_batch_receipts\n"
        "          TO trading_job_worker"
    ) in source
    assert "GRANT SELECT ON TABLE public.engine_job_results" in source
    assert (
        "GRANT EXECUTE ON FUNCTION job_plane.ingest_engine_job_result(\n"
        "          text, text, text, text, text"
        in source
    )
    assert "GRANT SELECT ON TABLE public.engine_events" not in source
    assert "GRANT SELECT ON TABLE public.engine_run_projections" not in source
