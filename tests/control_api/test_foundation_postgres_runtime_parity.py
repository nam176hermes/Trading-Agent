from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat

import psycopg
import pytest

from trading_control.db import DatabaseSettings
from tests.control_api._disposable_runtime import require_disposable_green
from tests.control_api._postgres_catalog import CatalogSnapshot, capture_catalog
from tests.control_api.test_alembic_schema import EXPECTED_TABLES, EXACT_HEAD
from tests.jobs._postgres import (
    disposable_restore_workflow,
    disposable_role_settings,
    upgrade_to_head,
)


OPERATION_ID = "foundation-postgres-restore-green-v1"
EVENT_ID = "10000000-0000-0000-0000-000000000001"
STREAM_ID = "10000000-0000-0000-0000-000000000010"
pytestmark = pytest.mark.runtime_postgres


def _canonical(document: object) -> str:
    return json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _seed_restore_authority(owner: DatabaseSettings) -> tuple[str, str]:
    event_text = _canonical(
        {
            "event_id": EVENT_ID,
            "event_type": "SignalProposal",
            "sequence": 1,
            "stream_id": STREAM_ID,
        }
    )
    digest = sha256(event_text.encode("utf-8")).hexdigest()
    snapshot_text = _canonical(
        {
            "issues": [],
            "reducer_version": "event-ledger-reducer-v1",
            "schema_version": "event-ledger-replay-v1",
            "state": {
                "applied_events": [{"digest": digest, "event_id": EVENT_ID}],
                "event_count": 1,
                "streams": [
                    {
                        "event_count": 1,
                        "last_digest": digest,
                        "last_sequence": 1,
                        "stream_id": STREAM_ID,
                    }
                ],
                "type_counts": [
                    {"count": 1, "event_type": "SignalProposal"}
                ],
            },
            "status": "COMPLETE",
        }
    )
    with psycopg.connect(owner.conninfo(), autocommit=True) as connection:
        assert connection.execute(
            "SELECT public.append_domain_event(%s,%s,1,'SignalProposal',%s,%s,%s)",
            (EVENT_ID, STREAM_ID, event_text, "domain.signal", '{"attempt":1}'),
        ).fetchone()[0] is True
        assert connection.execute(
            "INSERT INTO public.consumer_inbox (consumer,event_id) "
            "VALUES ('restore-proof',%s) ON CONFLICT DO NOTHING RETURNING 1",
            (EVENT_ID,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT public.save_domain_snapshot(%s)",
            (snapshot_text,),
        ).fetchone()[0] is True
    return event_text, snapshot_text


def _assert_restored_event_chain(
    owner: DatabaseSettings,
    event_text: str,
    snapshot_text: str,
) -> None:
    with psycopg.connect(owner.conninfo(), autocommit=True) as connection:
        assert connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone()[0] == EXACT_HEAD
        event_rows = connection.execute(
            """
            SELECT event_id::text,stream_id::text,sequence,canonical_event_text,
                   digest
            FROM public.domain_events ORDER BY stream_id,sequence,event_id
            """
        ).fetchall()
        expected_digest = sha256(event_text.encode("utf-8")).hexdigest()
        assert event_rows == [
            (EVENT_ID, STREAM_ID, 1, event_text, expected_digest)
        ]
        assert connection.execute(
            "SELECT canonical_state_json FROM public.aggregate_snapshots"
        ).fetchone()[0] == snapshot_text
        assert connection.execute(
            "SELECT count(*) FROM public.consumer_inbox "
            "WHERE consumer='restore-proof' AND event_id=%s",
            (EVENT_ID,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT public.append_domain_event(%s,%s,1,'SignalProposal',%s,%s,%s)",
            (EVENT_ID, STREAM_ID, event_text, "domain.signal", '{"attempt":1}'),
        ).fetchone()[0] is False
        assert connection.execute(
            "INSERT INTO public.consumer_inbox (consumer,event_id) "
            "VALUES ('restore-proof',%s) ON CONFLICT DO NOTHING RETURNING 1",
            (EVENT_ID,),
        ).fetchone() is None


def _assert_non_owner_ledger_denials(owner: DatabaseSettings) -> None:
    for role in (
        "trading_migrator",
        "trading_reader",
        "trading_job_api",
        "trading_job_worker",
        "trading_job_scheduler",
    ):
        settings = disposable_role_settings(owner, role)
        with psycopg.connect(settings.conninfo(), autocommit=True) as connection:
            connection.execute("SET default_transaction_read_only = off")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute("SELECT count(*) FROM public.domain_events")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    "SELECT public.save_domain_snapshot('{}')"
                )


def _write_sanitized_catalog_evidence(
    source: CatalogSnapshot,
    restored: CatalogSnapshot,
) -> Path:
    raw_root = os.environ.get("TRADING_TEST_POSTGRES_EVIDENCE_DIR", "")
    root = Path(raw_root)
    if (
        not root.is_absolute()
        or root.parent != Path("/tmp")
        or not root.name.startswith("foundation-postgres-evidence-")
    ):
        raise RuntimeError("sanitized PostgreSQL evidence root is not exact")
    try:
        metadata = root.lstat()
        resolved = root.resolve(strict=True)
    except OSError:
        raise RuntimeError("sanitized PostgreSQL evidence root is unavailable") from None
    if (
        resolved != root
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise RuntimeError("sanitized PostgreSQL evidence root is not private")
    evidence = root / "catalog-restore-semantic-evidence.json"
    differing_subgroups = sorted(
        f"{group}.{subgroup}"
        for group in sorted(
            set(source.semantic_subgroup_digests)
            | set(restored.semantic_subgroup_digests)
        )
        for subgroup in sorted(
            set(source.semantic_subgroup_digests.get(group, {}))
            | set(restored.semantic_subgroup_digests.get(group, {}))
        )
        if source.semantic_subgroup_digests.get(group, {}).get(subgroup)
        != restored.semantic_subgroup_digests.get(group, {}).get(subgroup)
    )
    row_differences: dict[str, dict[str, list[list[object]]]] = {}
    for group in sorted(set(source.semantic_rows) | set(restored.semantic_rows)):
        source_rows = {
            _canonical(list(row)): list(row)
            for row in source.semantic_rows.get(group, ())
        }
        restored_rows = {
            _canonical(list(row)): list(row)
            for row in restored.semantic_rows.get(group, ())
        }
        source_only = [source_rows[key] for key in sorted(source_rows.keys() - restored_rows)]
        restored_only = [
            restored_rows[key] for key in sorted(restored_rows.keys() - source_rows)
        ]
        if source_only or restored_only:
            if len(source_only) + len(restored_only) > 512:
                raise RuntimeError("sanitized PostgreSQL semantic row diff is too large")
            row_differences[group] = {
                "source_only": source_only,
                "restored_only": restored_only,
            }
    document = {
        "schema_version": "foundation-postgres-catalog-restore-evidence-v1",
        "alembic_head": EXACT_HEAD,
        "semantic_groups_equal": (
            source.semantic_digests == restored.semantic_digests
        ),
        "row_counts_equal": source.table_row_counts == restored.table_row_counts,
        "differing_semantic_subgroups": differing_subgroups,
        "semantic_row_differences": row_differences,
        "source": {
            "semantic_digests": source.semantic_digests,
            "semantic_row_counts": source.semantic_row_counts,
            "semantic_subgroup_digests": source.semantic_subgroup_digests,
            "semantic_subgroup_row_counts": source.semantic_subgroup_row_counts,
            "raw_acl_sha256_informational": source.raw_acl_sha256,
            "raw_acl_row_count": source.raw_acl_row_count,
            "table_row_counts": source.table_row_counts,
        },
        "restored": {
            "semantic_digests": restored.semantic_digests,
            "semantic_row_counts": restored.semantic_row_counts,
            "semantic_subgroup_digests": restored.semantic_subgroup_digests,
            "semantic_subgroup_row_counts": restored.semantic_subgroup_row_counts,
            "raw_acl_sha256_informational": restored.raw_acl_sha256,
            "raw_acl_row_count": restored.raw_acl_row_count,
            "table_row_counts": restored.table_row_counts,
        },
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    content = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(evidence, flags, 0o600)
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("short evidence write")
            offset += written
    except OSError:
        raise RuntimeError("sanitized PostgreSQL evidence cannot be created") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return evidence


def test_custom_dump_restore_preserves_0008_catalog_acl_and_event_authority() -> None:
    require_disposable_green()
    with disposable_restore_workflow(
        operation_id=OPERATION_ID,
        planned=True,
    ) as workflow:
        source = workflow.source
        upgrade_to_head(source)
        event_text, snapshot_text = _seed_restore_authority(source)
        source_catalog = capture_catalog(source)

        restored = workflow.restore()
        restored_catalog = capture_catalog(restored)
        evidence = _write_sanitized_catalog_evidence(
            source_catalog,
            restored_catalog,
        )

        assert source_catalog.semantic_digests == restored_catalog.semantic_digests
        assert (
            source_catalog.semantic_row_counts
            == restored_catalog.semantic_row_counts
        )
        assert source_catalog.table_row_counts == restored_catalog.table_row_counts
        assert set(restored_catalog.table_row_counts) == EXPECTED_TABLES
        # Raw ACL text digests and row counts are retained as informational
        # evidence. Effective ACL semantics above are the restore-portable
        # blocking comparison; representation-only normalization is allowed.
        assert len(source_catalog.raw_acl_sha256) == 64
        assert len(restored_catalog.raw_acl_sha256) == 64
        _assert_restored_event_chain(restored, event_text, snapshot_text)
        _assert_non_owner_ledger_denials(restored)
        assert evidence.is_file()
