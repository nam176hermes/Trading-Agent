from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from packages.data_catalog import (
    CatalogMaterializationError,
    CatalogWorkspaceV1,
    MaterializedSecurityMasterSnapshotV1,
    materialize_security_master_snapshot,
    verify_security_master_snapshot,
)
from packages.data_catalog import parquet as catalog_parquet
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.domain import (
    EvidenceLocator,
    EvidenceLocatorKind,
    EvidenceReference,
    EvidenceSource,
)
from packages.security_master import (
    IssuerPayloadV1,
    PersistedSecurityMasterRevisionV1,
    PostgresSecurityMasterRepository,
    SecurityMasterEvidenceV1,
    SecurityMasterIdentityKind,
    SecurityMasterOperation,
    SecurityMasterPersistenceError,
    SecurityMasterRevisionV1,
)

from .test_postgres_repository import Connection, Pool


KNOWN = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
ISSUER_ID = UUID("10000000-0000-4000-8000-000000000001")
FACT_ID = UUID("80000000-0000-4000-8000-000000000001")


def evidence(index: int, known_at: datetime) -> SecurityMasterEvidenceV1:
    return SecurityMasterEvidenceV1(
        schema_version="security-master-evidence-v1",
        reference=EvidenceReference(
            evidence_id=UUID(f"a0000000-0000-4000-8000-{index:012d}"),
            source=EvidenceSource.FILING,
            locator=EvidenceLocator(
                kind=EvidenceLocatorKind.HTTPS,
                authority="example.invalid",
                path=("security-master", f"record-{index}"),
            ),
            observed_at=known_at - timedelta(minutes=2),
            schema_version="source-record-v1",
        ),
        fetched_at=known_at - timedelta(minutes=1),
        known_at=known_at,
        content_sha256=f"{index:x}" * 64,
        media_type="application/json",
        source_revision=f"r{index}",
        normalization_version="security-master-normalization-v1",
    )


def revision(
    index: int,
    *,
    known_at: datetime,
    operation: SecurityMasterOperation = SecurityMasterOperation.ASSERT,
) -> SecurityMasterRevisionV1:
    return SecurityMasterRevisionV1(
        schema_version="security-master-revision-v1",
        revision_id=UUID(f"90000000-0000-4000-8000-{index:012d}"),
        fact_id=FACT_ID,
        subject_id=ISSUER_ID,
        subject_kind=SecurityMasterIdentityKind.ISSUER,
        revision_ordinal=index,
        operation=operation,
        effective_from=KNOWN - timedelta(days=1),
        effective_to=None,
        known_at=known_at,
        supersedes_revision_id=(
            None
            if index == 1
            else UUID(f"90000000-0000-4000-8000-{index - 1:012d}")
        ),
        evidence=(evidence(index, known_at),),
        payload=(
            None
            if operation is SecurityMasterOperation.RETRACT
            else IssuerPayloadV1(
                issuer_id=ISSUER_ID,
                legal_name=f"Bitcoin Network {index}",
                jurisdiction="GLOBAL",
            )
        ),
    )


def persisted_revision(
    value: SecurityMasterRevisionV1,
    *,
    recorded_at: datetime | None = None,
) -> PersistedSecurityMasterRevisionV1:
    return PersistedSecurityMasterRevisionV1(
        revision=value,
        recorded_at=value.known_at if recorded_at is None else recorded_at,
    )


def repository_for(
    *values: PersistedSecurityMasterRevisionV1,
) -> PostgresSecurityMasterRepository:
    rows = []
    for value in values:
        document = value.revision
        rows.append(
            {
                "canonical_revision_text": document.canonical_revision_bytes.decode(
                    "utf-8"
                ),
                "revision_digest": document.digest,
                "revision_id": document.revision_id,
                "fact_id": document.fact_id,
                "subject_id": document.subject_id,
                "subject_kind": document.subject_kind.value,
                "revision_ordinal": document.revision_ordinal,
                "operation": document.operation.value,
                "effective_from": document.effective_from,
                "effective_to": document.effective_to,
                "known_at": document.known_at,
                "recorded_at": value.recorded_at,
                "supersedes_revision_id": document.supersedes_revision_id,
                "lookup_provider": None,
                "lookup_symbol": None,
                "related_security_id": None,
            }
        )
    return PostgresSecurityMasterRepository(
        Pool(Connection([{"transaction_isolation": "read committed"}, rows]))
    )


def artifact_path(workspace: CatalogWorkspaceV1, name: str) -> Path:
    return catalog_parquet._workspace_state(workspace).path / name


def rewrite_parquet_column(
    artifact: MaterializedSecurityMasterSnapshotV1,
    column: str,
    values: list[object],
) -> MaterializedSecurityMasterSnapshotV1:
    parquet_path = artifact_path(artifact.workspace, artifact.parquet_name)
    table = pq.read_table(parquet_path)
    rewritten = table.set_column(
        table.schema.get_field_index(column),
        table.schema.field(column),
        pa.array(values, type=table.schema.field(column).type),
    )
    pq.write_table(
        rewritten,
        parquet_path,
        compression="NONE",
        use_dictionary=False,
        write_statistics=False,
        version="2.6",
        data_page_version="1.0",
    )
    manifest = artifact.manifest.model_copy(
        update={"parquet_sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest()}
    )
    artifact_path(artifact.workspace, artifact.manifest_name).write_bytes(
        canonical_json_bytes(manifest)
    )
    return replace(artifact, manifest=manifest)


def test_snapshot_preserves_visible_revision_log_in_canonical_order(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    first = persisted_revision(revision(1, known_at=KNOWN))
    corrected = persisted_revision(
        revision(2, known_at=KNOWN + timedelta(minutes=1))
    )
    retracted = persisted_revision(
        revision(
            3,
            known_at=KNOWN + timedelta(minutes=2),
            operation=SecurityMasterOperation.RETRACT,
        )
    )
    cutoff = KNOWN + timedelta(minutes=2)
    workspace = CatalogWorkspaceV1.create(tmp_path)

    artifact = materialize_security_master_snapshot(
        repository_for(first, corrected, retracted), cutoff, workspace
    )

    expected = (first, corrected, retracted)
    assert verify_security_master_snapshot(artifact) == expected
    assert artifact.manifest.row_count == 3
    assert artifact.manifest.knowledge_cutoff == cutoff
    assert artifact.manifest.schema_version == "security-master-pit-snapshot-manifest-v1"
    assert artifact.manifest.selection_policy == "visible-revision-log-v1"
    assert artifact.manifest.writer_version == "pyarrow-25-deterministic-v1"
    assert artifact.manifest.first_cursor.recorded_at == first.recorded_at
    assert artifact.manifest.first_cursor.revision_id == first.revision.revision_id
    assert artifact.manifest.last_cursor.recorded_at == retracted.recorded_at
    assert artifact.manifest.last_cursor.revision_id == retracted.revision.revision_id
    assert artifact.manifest.revision_log_sha256 == hashlib.sha256(
        b"[" + b",".join(canonical_json_bytes(item) for item in expected) + b"]"
    ).hexdigest()
    assert pq.read_schema(artifact_path(workspace, artifact.parquet_name)) == pa.schema(
        [
            pa.field("revision_id", pa.string(), nullable=False),
            pa.field("fact_id", pa.string(), nullable=False),
            pa.field("subject_id", pa.string(), nullable=False),
            pa.field("subject_kind", pa.string(), nullable=False),
            pa.field("revision_ordinal", pa.int64(), nullable=False),
            pa.field("operation", pa.string(), nullable=False),
            pa.field("effective_from", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("effective_to", pa.timestamp("us", tz="UTC"), nullable=True),
            pa.field("known_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("recorded_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("supersedes_revision_id", pa.string(), nullable=True),
            pa.field("revision_digest", pa.string(), nullable=False),
            pa.field("canonical_revision_text", pa.string(), nullable=False),
        ]
    )


def test_snapshot_rejects_revision_recorded_after_the_knowledge_cutoff(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    revision_at_t0 = revision(1, known_at=KNOWN)
    recorded_at_t2 = KNOWN + timedelta(minutes=2)
    persisted = PersistedSecurityMasterRevisionV1(
        revision=revision_at_t0,
        recorded_at=recorded_at_t2,
    )
    cutoff_at_t1 = KNOWN + timedelta(minutes=1)

    assert persisted.revision.known_at < cutoff_at_t1 < persisted.recorded_at
    with pytest.raises(SecurityMasterPersistenceError, match="cutoff"):
        materialize_security_master_snapshot(
            repository_for(persisted),
            cutoff_at_t1,
            CatalogWorkspaceV1.create(tmp_path),
        )


def test_snapshot_is_deterministic_and_supports_an_empty_cutoff(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    source = (
        persisted_revision(revision(1, known_at=KNOWN)),
        persisted_revision(revision(2, known_at=KNOWN + timedelta(minutes=1))),
    )
    first = materialize_security_master_snapshot(
        repository_for(*source),
        KNOWN + timedelta(minutes=1),
        CatalogWorkspaceV1.create(tmp_path),
    )
    second = materialize_security_master_snapshot(
        repository_for(*source),
        KNOWN + timedelta(minutes=1),
        CatalogWorkspaceV1.create(tmp_path),
    )

    assert first.manifest == second.manifest
    assert artifact_path(first.workspace, first.parquet_name).read_bytes() == artifact_path(
        second.workspace, second.parquet_name
    ).read_bytes()

    empty = materialize_security_master_snapshot(
        repository_for(),
        KNOWN - timedelta(days=1),
        CatalogWorkspaceV1.create(tmp_path),
    )
    assert verify_security_master_snapshot(empty) == ()
    assert empty.manifest.row_count == 0
    assert empty.manifest.first_cursor is None
    assert empty.manifest.last_cursor is None


def test_materializer_rejects_non_authoritative_revision_input(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    first = persisted_revision(revision(1, known_at=KNOWN))
    catalog_workspace = CatalogWorkspaceV1.create(tmp_path)

    with pytest.raises(CatalogMaterializationError, match="exact Postgres"):
        materialize_security_master_snapshot(
            (first,),  # type: ignore[arg-type]
            KNOWN,
            catalog_workspace,
        )


@pytest.mark.parametrize(
    ("column", "replacement", "message"),
    (
        ("revision_digest", "b" * 64, "mirrors"),
        ("canonical_revision_text", None, "canonical revision text is not canonical JSON"),
    ),
)
def test_verifier_rejects_forged_canonical_text_or_mirrors(
    tmp_path: Path, column: str, replacement: str | None, message: str
) -> None:
    tmp_path.chmod(0o700)
    artifact = materialize_security_master_snapshot(
        repository_for(persisted_revision(revision(1, known_at=KNOWN))),
        KNOWN,
        CatalogWorkspaceV1.create(tmp_path),
    )
    if replacement is None:
        replacement = (
            pq.read_table(artifact_path(artifact.workspace, artifact.parquet_name))
            .column(column)[0]
            .as_py()
            + "\n"
        )
    forged = rewrite_parquet_column(artifact, column, [replacement])

    with pytest.raises(CatalogMaterializationError, match=message):
        verify_security_master_snapshot(forged)


def test_verifier_rejects_reordered_rows_even_with_a_rebound_parquet_digest(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    revisions = tuple(
        persisted_revision(
            revision(index, known_at=KNOWN + timedelta(minutes=index - 1))
        )
        for index in range(1, 4)
    )
    artifact = materialize_security_master_snapshot(
        repository_for(*revisions),
        KNOWN + timedelta(minutes=2),
        CatalogWorkspaceV1.create(tmp_path),
    )
    parquet_path = artifact_path(artifact.workspace, artifact.parquet_name)
    table = pq.read_table(parquet_path).take(pa.array([2, 1, 0]))
    pq.write_table(
        table,
        parquet_path,
        compression="NONE",
        use_dictionary=False,
        write_statistics=False,
        version="2.6",
        data_page_version="1.0",
    )
    manifest = artifact.manifest.model_copy(
        update={"parquet_sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest()}
    )
    artifact_path(artifact.workspace, artifact.manifest_name).write_bytes(
        canonical_json_bytes(manifest)
    )

    with pytest.raises(CatalogMaterializationError, match="canonically ordered"):
        verify_security_master_snapshot(replace(artifact, manifest=manifest))


def test_verifier_rejects_alternate_parquet_writer_shape_with_a_rebound_digest(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    revisions = tuple(
        persisted_revision(
            revision(index, known_at=KNOWN + timedelta(minutes=index - 1))
        )
        for index in range(1, 4)
    )
    artifact = materialize_security_master_snapshot(
        repository_for(*revisions),
        KNOWN + timedelta(minutes=2),
        CatalogWorkspaceV1.create(tmp_path),
    )
    parquet_path = artifact_path(artifact.workspace, artifact.parquet_name)
    pq.write_table(
        pq.read_table(parquet_path),
        parquet_path,
        compression="NONE",
        use_dictionary=False,
        write_statistics=False,
        version="2.6",
        data_page_version="1.0",
        row_group_size=1,
    )
    manifest = artifact.manifest.model_copy(
        update={"parquet_sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest()}
    )
    artifact_path(artifact.workspace, artifact.manifest_name).write_bytes(
        canonical_json_bytes(manifest)
    )

    with pytest.raises(CatalogMaterializationError, match="writer policy is not canonical"):
        verify_security_master_snapshot(replace(artifact, manifest=manifest))


def test_verifier_rejects_tamper_symlink_and_size_boundaries(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    first = persisted_revision(revision(1, known_at=KNOWN))

    tampered = materialize_security_master_snapshot(
        repository_for(first), KNOWN, CatalogWorkspaceV1.create(tmp_path)
    )
    path = artifact_path(tampered.workspace, tampered.parquet_name)
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(CatalogMaterializationError, match="parquet digest"):
        verify_security_master_snapshot(tampered)

    linked = materialize_security_master_snapshot(
        repository_for(first), KNOWN, CatalogWorkspaceV1.create(tmp_path)
    )
    linked_path = artifact_path(linked.workspace, linked.parquet_name)
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(linked_path.read_bytes())
    linked_path.unlink()
    linked_path.symlink_to(outside)
    with pytest.raises(CatalogMaterializationError, match="regular non-symlink"):
        verify_security_master_snapshot(linked)

    oversized_parquet = materialize_security_master_snapshot(
        repository_for(first), KNOWN, CatalogWorkspaceV1.create(tmp_path)
    )
    artifact_path(
        oversized_parquet.workspace, oversized_parquet.parquet_name
    ).write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(CatalogMaterializationError, match="bounded regular-file policy"):
        verify_security_master_snapshot(oversized_parquet)

    oversized_manifest = materialize_security_master_snapshot(
        repository_for(first), KNOWN, CatalogWorkspaceV1.create(tmp_path)
    )
    artifact_path(
        oversized_manifest.workspace, oversized_manifest.manifest_name
    ).write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(CatalogMaterializationError, match="bounded regular-file policy"):
        verify_security_master_snapshot(oversized_manifest)


def test_verifier_rejects_unbound_artifact_names_before_open(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    artifact = materialize_security_master_snapshot(
        repository_for(persisted_revision(revision(1, known_at=KNOWN))),
        KNOWN,
        CatalogWorkspaceV1.create(tmp_path),
    )

    with pytest.raises(CatalogMaterializationError, match="artifact names"):
        verify_security_master_snapshot(replace(artifact, manifest_name="../outside"))
