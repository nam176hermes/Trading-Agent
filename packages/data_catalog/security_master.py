"""Point-in-time, descriptor-bound Parquet security-master snapshots."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from packages.domain import require_utc
from packages.engine_contracts.serialization import (
    CanonicalUtcDateTime,
    Sha256Hex,
    canonical_json_bytes,
)
from packages.security_master.models import (
    PersistedSecurityMasterRevisionV1,
    SecurityMasterRevisionV1,
)
from packages.security_master.postgres_repository import PostgresSecurityMasterRepository
from packages.security_master.resolver import (
    SecurityMasterIntegrityError,
    SecurityMasterResolver,
)

from .parquet import (
    CatalogMaterializationError,
    CatalogWorkspaceV1,
    _BoundedFdReader,
    _duplicate_workspace_directory_fd,
    _link_fd_into_directory,
    _regular_fd_at,
    _sealed_snapshot_fd,
    _sha256_fd,
)


_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_PARQUET_BYTES = 8 * 1024 * 1024
_MAX_ROWS = 4096
_SELECTION_POLICY = "visible-revision-log-v1"
_WRITER_VERSION = "pyarrow-25-deterministic-v1"
_SCHEMA_VERSION = "security-master-pit-snapshot-manifest-v1"
_ROW_COLUMNS = (
    "revision_id",
    "fact_id",
    "subject_id",
    "subject_kind",
    "revision_ordinal",
    "operation",
    "effective_from",
    "effective_to",
    "known_at",
    "recorded_at",
    "supersedes_revision_id",
    "revision_digest",
    "canonical_revision_text",
)
_PARQUET_SCHEMA = pa.schema(
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


class _SnapshotModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class SecurityMasterSnapshotCursorV1(_SnapshotModel):
    recorded_at: CanonicalUtcDateTime
    revision_id: UUID


def _snapshot_digest(
    *,
    knowledge_cutoff: datetime,
    row_count: int,
    first_cursor: SecurityMasterSnapshotCursorV1 | None,
    last_cursor: SecurityMasterSnapshotCursorV1 | None,
    revision_log_sha256: str,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "first_cursor": first_cursor,
                "knowledge_cutoff": knowledge_cutoff.isoformat().replace("+00:00", "Z"),
                "last_cursor": last_cursor,
                "revision_log_sha256": revision_log_sha256,
                "row_count": row_count,
                "schema_version": _SCHEMA_VERSION,
                "selection_policy": _SELECTION_POLICY,
            }
        )
    ).hexdigest()


class SecurityMasterSnapshotManifestV1(_SnapshotModel):
    schema_version: Literal["security-master-pit-snapshot-manifest-v1"] = _SCHEMA_VERSION
    knowledge_cutoff: CanonicalUtcDateTime
    selection_policy: Literal[
        "visible-revision-log-v1"
    ] = _SELECTION_POLICY
    row_count: StrictInt = Field(ge=0, le=_MAX_ROWS)
    first_cursor: SecurityMasterSnapshotCursorV1 | None
    last_cursor: SecurityMasterSnapshotCursorV1 | None
    revision_log_sha256: Sha256Hex
    snapshot_digest: Sha256Hex
    parquet_sha256: Sha256Hex
    writer_version: Literal["pyarrow-25-deterministic-v1"] = _WRITER_VERSION

    @model_validator(mode="after")
    def _complete_binding(self) -> "SecurityMasterSnapshotManifestV1":
        if (self.first_cursor is None) != (self.last_cursor is None):
            raise ValueError("snapshot cursors must both be null or both be present")
        if (self.row_count == 0) != (self.first_cursor is None):
            raise ValueError("empty snapshot cursor binding is inconsistent")
        if self.first_cursor is not None and self.last_cursor is not None:
            first = (self.first_cursor.recorded_at, self.first_cursor.revision_id.bytes)
            last = (self.last_cursor.recorded_at, self.last_cursor.revision_id.bytes)
            if first > last or self.last_cursor.recorded_at > self.knowledge_cutoff:
                raise ValueError("snapshot cursors are outside canonical order or cutoff")
        expected = _snapshot_digest(
            knowledge_cutoff=self.knowledge_cutoff,
            row_count=self.row_count,
            first_cursor=self.first_cursor,
            last_cursor=self.last_cursor,
            revision_log_sha256=self.revision_log_sha256,
        )
        if self.snapshot_digest != expected:
            raise ValueError("snapshot digest does not bind the semantic manifest")
        return self


@dataclass(frozen=True, slots=True)
class MaterializedSecurityMasterSnapshotV1:
    workspace: CatalogWorkspaceV1
    manifest: SecurityMasterSnapshotManifestV1
    parquet_name: str
    manifest_name: str


def _utc(value: datetime, name: str) -> datetime:
    try:
        return require_utc(value).astimezone(UTC)
    except (TypeError, ValueError) as exc:
        raise CatalogMaterializationError(f"{name} must be an explicit UTC datetime") from exc


def _revision_log_bytes(
    revisions: tuple[PersistedSecurityMasterRevisionV1, ...],
) -> bytes:
    return b"[" + b",".join(canonical_json_bytes(item) for item in revisions) + b"]"


def _cursor(
    persisted: PersistedSecurityMasterRevisionV1,
) -> SecurityMasterSnapshotCursorV1:
    return SecurityMasterSnapshotCursorV1(
        recorded_at=persisted.recorded_at,
        revision_id=persisted.revision.revision_id,
    )


def _semantic_manifest_values(
    revisions: tuple[PersistedSecurityMasterRevisionV1, ...],
    knowledge_cutoff: datetime,
) -> dict[str, object]:
    revision_log_sha256 = hashlib.sha256(_revision_log_bytes(revisions)).hexdigest()
    first_cursor = _cursor(revisions[0]) if revisions else None
    last_cursor = _cursor(revisions[-1]) if revisions else None
    return {
        "knowledge_cutoff": knowledge_cutoff,
        "row_count": len(revisions),
        "first_cursor": first_cursor,
        "last_cursor": last_cursor,
        "revision_log_sha256": revision_log_sha256,
        "snapshot_digest": _snapshot_digest(
            knowledge_cutoff=knowledge_cutoff,
            row_count=len(revisions),
            first_cursor=first_cursor,
            last_cursor=last_cursor,
            revision_log_sha256=revision_log_sha256,
        ),
    }


def _artifact_names(snapshot_digest: str) -> tuple[str, str]:
    prefix = f"security-master-{snapshot_digest}"
    return f"{prefix}.parquet", f"{prefix}.manifest.json"


def _row(persisted: PersistedSecurityMasterRevisionV1) -> dict[str, object]:
    revision = persisted.revision
    return {
        "revision_id": str(revision.revision_id),
        "fact_id": str(revision.fact_id),
        "subject_id": str(revision.subject_id),
        "subject_kind": revision.subject_kind.value,
        "revision_ordinal": revision.revision_ordinal,
        "operation": revision.operation.value,
        "effective_from": revision.effective_from,
        "effective_to": revision.effective_to,
        "known_at": revision.known_at,
        "recorded_at": persisted.recorded_at,
        "supersedes_revision_id": (
            str(revision.supersedes_revision_id)
            if revision.supersedes_revision_id is not None
            else None
        ),
        "revision_digest": revision.digest,
        "canonical_revision_text": revision.canonical_revision_bytes.decode("utf-8"),
    }


def _canonical_parquet_bytes(
    revisions: tuple[PersistedSecurityMasterRevisionV1, ...],
) -> bytes:
    try:
        with io.BytesIO() as stream:
            pq.write_table(
                pa.Table.from_pylist(
                    [_row(item) for item in revisions], schema=_PARQUET_SCHEMA
                ),
                stream,
                compression="NONE",
                use_dictionary=False,
                write_statistics=False,
                version="2.6",
                data_page_version="1.0",
            )
            encoded = stream.getvalue()
    except Exception as exc:
        raise CatalogMaterializationError(
            "security-master revisions cannot encode canonical parquet"
        ) from exc
    if len(encoded) > _MAX_PARQUET_BYTES:
        raise CatalogMaterializationError("parquet exceeds its bounded size")
    return encoded


def _write_new_parquet(
    directory_fd: int,
    name: str,
    revisions: tuple[PersistedSecurityMasterRevisionV1, ...],
) -> None:
    encoded = _canonical_parquet_bytes(revisions)
    temporary = f".security-master-{uuid.uuid4().hex}.parquet"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise CatalogMaterializationError("parquet write was incomplete")
            remaining = remaining[written:]
        os.fsync(fd)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > _MAX_PARQUET_BYTES
        ):
            raise CatalogMaterializationError("temporary parquet artifact violates its bounded policy")
        _link_fd_into_directory(fd, directory_fd, name)
    finally:
        try:
            current = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
            held = os.fstat(fd)
            if current.st_dev == held.st_dev and current.st_ino == held.st_ino:
                os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(fd)


def _write_new_manifest(
    directory_fd: int, name: str, manifest: SecurityMasterSnapshotManifestV1
) -> None:
    encoded = canonical_json_bytes(manifest)
    if len(encoded) > _MAX_MANIFEST_BYTES:
        raise CatalogMaterializationError("manifest exceeds its bounded size")
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
    except FileExistsError as exc:
        raise CatalogMaterializationError("catalog artifact already exists") from exc
    try:
        if os.write(fd, encoded) != len(encoded):
            raise CatalogMaterializationError("manifest write was incomplete")
        os.fsync(fd)
    finally:
        os.close(fd)


def materialize_security_master_snapshot(
    repository: PostgresSecurityMasterRepository,
    knowledge_cutoff: datetime,
    workspace: CatalogWorkspaceV1,
) -> MaterializedSecurityMasterSnapshotV1:
    """Materialize the authoritative DB revision log visible at ``knowledge_cutoff``."""

    cutoff = _utc(knowledge_cutoff, "knowledge_cutoff")
    if not isinstance(workspace, CatalogWorkspaceV1):
        raise CatalogMaterializationError("workspace must be a catalog-created CatalogWorkspaceV1")
    if type(repository) is not PostgresSecurityMasterRepository:
        raise CatalogMaterializationError(
            "repository must be the exact PostgresSecurityMasterRepository authority"
        )
    selected = repository.export_visible_revisions(knowledge_cutoff=cutoff)
    semantic = _semantic_manifest_values(selected, cutoff)
    parquet_name, manifest_name = _artifact_names(str(semantic["snapshot_digest"]))
    directory_fd = _duplicate_workspace_directory_fd(workspace)
    try:
        _write_new_parquet(directory_fd, parquet_name, selected)
        parquet_fd = _regular_fd_at(
            directory_fd, parquet_name, maximum_bytes=_MAX_PARQUET_BYTES
        )
        try:
            sealed_fd = _sealed_snapshot_fd(parquet_fd, maximum_bytes=_MAX_PARQUET_BYTES)
            try:
                parquet_sha256 = _sha256_fd(
                    sealed_fd, maximum_bytes=_MAX_PARQUET_BYTES
                )
            finally:
                os.close(sealed_fd)
        finally:
            os.close(parquet_fd)
        manifest = SecurityMasterSnapshotManifestV1.model_validate(
            {**semantic, "parquet_sha256": parquet_sha256}
        )
        _write_new_manifest(directory_fd, manifest_name, manifest)
        artifact = MaterializedSecurityMasterSnapshotV1(
            workspace=workspace,
            manifest=manifest,
            parquet_name=parquet_name,
            manifest_name=manifest_name,
        )
        if verify_security_master_snapshot(artifact) != selected:
            raise CatalogMaterializationError("security-master snapshot does not round-trip")
        return artifact
    finally:
        os.close(directory_fd)


def _read_manifest(
    directory_fd: int, name: str
) -> SecurityMasterSnapshotManifestV1:
    fd = _regular_fd_at(directory_fd, name, maximum_bytes=_MAX_MANIFEST_BYTES)
    try:
        encoded = b""
        while block := os.read(fd, 64 * 1024):
            encoded += block
            if len(encoded) > _MAX_MANIFEST_BYTES:
                raise CatalogMaterializationError("manifest exceeds its bounded size")
    finally:
        os.close(fd)
    try:
        manifest = SecurityMasterSnapshotManifestV1.model_validate_json(encoded)
    except Exception as exc:
        raise CatalogMaterializationError("security-master manifest is invalid") from exc
    if encoded != canonical_json_bytes(manifest):
        raise CatalogMaterializationError("security-master manifest is not canonical JSON")
    return manifest


def _read_rows(fd: int) -> list[dict[str, object]]:
    try:
        with _BoundedFdReader(fd, maximum_bytes=_MAX_PARQUET_BYTES) as stream:
            reader = pq.ParquetFile(stream)
            metadata = reader.metadata
            if metadata.num_rows > _MAX_ROWS or metadata.num_row_groups > _MAX_ROWS:
                raise CatalogMaterializationError("parquet exceeds the security-master row bound")
            for group_index in range(metadata.num_row_groups):
                group = metadata.row_group(group_index)
                for column_index in range(group.num_columns):
                    column = group.column(column_index)
                    if (
                        column.compression != "UNCOMPRESSED"
                        or column.statistics is not None
                        or "PLAIN_DICTIONARY" in column.encodings
                        or "RLE_DICTIONARY" in column.encodings
                    ):
                        raise CatalogMaterializationError(
                            "parquet writer policy is not canonical"
                        )
            table = reader.read()
    except CatalogMaterializationError:
        raise
    except Exception as exc:
        raise CatalogMaterializationError("security-master parquet cannot be read") from exc
    if table.schema != _PARQUET_SCHEMA:
        raise CatalogMaterializationError("security-master parquet schema is not canonical")
    rows = table.to_pylist()
    if len(rows) > _MAX_ROWS or not all(
        isinstance(row, dict) and tuple(row) == _ROW_COLUMNS for row in rows
    ):
        raise CatalogMaterializationError("security-master parquet rows are not canonical")
    return rows


def _row_revision(row: dict[str, object]) -> PersistedSecurityMasterRevisionV1:
    canonical_text = row["canonical_revision_text"]
    if not isinstance(canonical_text, str):
        raise CatalogMaterializationError("canonical revision text must be UTF-8 text")
    try:
        revision = SecurityMasterRevisionV1.model_validate_json(canonical_text)
    except Exception as exc:
        raise CatalogMaterializationError("canonical revision text is invalid") from exc
    if revision.canonical_revision_bytes != canonical_text.encode("utf-8"):
        raise CatalogMaterializationError("canonical revision text is not canonical JSON")

    def timestamp(value: object, name: str) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise CatalogMaterializationError(f"{name} mirror is not a timestamp")
        return _utc(value, name)

    mirrors = {
        "revision_id": str(revision.revision_id),
        "fact_id": str(revision.fact_id),
        "subject_id": str(revision.subject_id),
        "subject_kind": revision.subject_kind.value,
        "revision_ordinal": revision.revision_ordinal,
        "operation": revision.operation.value,
        "effective_from": revision.effective_from,
        "effective_to": revision.effective_to,
        "known_at": revision.known_at,
        "supersedes_revision_id": (
            str(revision.supersedes_revision_id)
            if revision.supersedes_revision_id is not None
            else None
        ),
        "revision_digest": revision.digest,
    }
    observed = {
        **{name: row[name] for name in mirrors},
        "effective_from": timestamp(row["effective_from"], "effective_from"),
        "effective_to": timestamp(row["effective_to"], "effective_to"),
        "known_at": timestamp(row["known_at"], "known_at"),
    }
    if observed != mirrors:
        raise CatalogMaterializationError("parquet mirrors do not match canonical revision text")
    recorded_at = timestamp(row["recorded_at"], "recorded_at")
    if recorded_at is None:
        raise CatalogMaterializationError("recorded_at mirror is not a timestamp")
    try:
        return PersistedSecurityMasterRevisionV1(
            revision=revision,
            recorded_at=recorded_at,
        )
    except Exception as exc:
        raise CatalogMaterializationError(
            "recorded_at mirror does not form a canonical persisted revision"
        ) from exc


def verify_security_master_snapshot(
    artifact: MaterializedSecurityMasterSnapshotV1,
) -> tuple[PersistedSecurityMasterRevisionV1, ...]:
    """Verify exact direct children and reconstruct their visible revision log."""

    if not isinstance(artifact, MaterializedSecurityMasterSnapshotV1):
        raise CatalogMaterializationError(
            "artifact must be a MaterializedSecurityMasterSnapshotV1"
        )
    expected_names = _artifact_names(artifact.manifest.snapshot_digest)
    if (artifact.parquet_name, artifact.manifest_name) != expected_names:
        raise CatalogMaterializationError("artifact names do not match snapshot identity")
    directory_fd = _duplicate_workspace_directory_fd(artifact.workspace)
    try:
        manifest = _read_manifest(directory_fd, artifact.manifest_name)
        if manifest != artifact.manifest or _artifact_names(manifest.snapshot_digest) != expected_names:
            raise CatalogMaterializationError("manifest does not match snapshot identity")
        parquet_fd = _regular_fd_at(
            directory_fd, artifact.parquet_name, maximum_bytes=_MAX_PARQUET_BYTES
        )
        try:
            sealed_fd = _sealed_snapshot_fd(parquet_fd, maximum_bytes=_MAX_PARQUET_BYTES)
            try:
                parquet_sha256 = _sha256_fd(
                    sealed_fd, maximum_bytes=_MAX_PARQUET_BYTES
                )
                if parquet_sha256 != manifest.parquet_sha256:
                    raise CatalogMaterializationError(
                        "parquet digest does not match security-master manifest"
                    )
                rows = _read_rows(sealed_fd)
            finally:
                os.close(sealed_fd)
        finally:
            os.close(parquet_fd)
        revisions = tuple(_row_revision(row) for row in rows)
        if any(item.recorded_at > manifest.knowledge_cutoff for item in revisions):
            raise CatalogMaterializationError("parquet contains a revision beyond the cutoff")
        if revisions != tuple(
            sorted(
                revisions,
                key=lambda item: (item.recorded_at, item.revision.revision_id.bytes),
            )
        ):
            raise CatalogMaterializationError("parquet revision log is not canonically ordered")
        try:
            SecurityMasterResolver(revisions)
        except SecurityMasterIntegrityError as exc:
            raise CatalogMaterializationError(
                "parquet security-master revision log is not canonical"
            ) from exc
        if (
            hashlib.sha256(_canonical_parquet_bytes(revisions)).hexdigest()
            != parquet_sha256
        ):
            raise CatalogMaterializationError("parquet writer policy is not canonical")
        expected_manifest = SecurityMasterSnapshotManifestV1.model_validate(
            {
                **_semantic_manifest_values(revisions, manifest.knowledge_cutoff),
                "parquet_sha256": manifest.parquet_sha256,
            }
        )
        if expected_manifest != manifest:
            raise CatalogMaterializationError(
                "manifest does not bind reconstructed security-master revisions"
            )
        return revisions
    finally:
        os.close(directory_fd)


__all__ = [
    "MaterializedSecurityMasterSnapshotV1",
    "SecurityMasterSnapshotCursorV1",
    "SecurityMasterSnapshotManifestV1",
    "materialize_security_master_snapshot",
    "verify_security_master_snapshot",
]
